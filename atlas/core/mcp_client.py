"""MCP Client — stdio transport with robust watchdog and recovery.

Architecture note:
    Ensures that MCP subprocesses are monitored and automatically respawned
    if they crash. Provides a thread-safe (via asyncio) way to call tools.
"""

import asyncio
from typing import Any, Optional

from loguru import logger

from atlas.shared.config import PolarisSettings
from atlas.shared.serialisation import decode_json, encode_json


class MCPProcessError(Exception):
    """Raised when the MCP process is unhealthy and fails to recover."""

    pass


class MCPClient:
    """MCP client using stdio transport with automatic watchdog."""

    def __init__(self, command: str, args: list[str], settings: PolarisSettings) -> None:
        self._command = command
        self._args = args
        self._settings = settings
        self._process: Optional[asyncio.subprocess.Process] = None
        self._is_healthy = False
        self._watchdog_task: Optional[asyncio.Task[None]] = None
        self._respawn_delay = 1.0
        self._max_respawn_delay = 15.0
        self._rpc_id = 0
        self._health_event = asyncio.Event()

    async def start(self) -> None:
        """Start the MCP client and background watchdog."""
        await self._respawn_process()
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def _respawn_process(self) -> None:
        """Spawn the MCP subprocess with exponential backoff."""
        delay = 1.0
        while True:
            try:
                self._process = await asyncio.create_subprocess_exec(
                    self._command,
                    *self._args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                self._is_healthy = True
                self._health_event.set()
                logger.info(
                    "MCP process started | command={} | args={}",
                    self._command,
                    self._args,
                )
                return
            except Exception as e:
                logger.error(
                    "Failed to respawn MCP process | command={} | error={}",
                    self._command,
                    str(e),
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._max_respawn_delay)

    async def _watchdog_loop(self) -> None:
        """Monitor the subprocess and trigger respawn on failure."""
        try:
            while True:
                if self._process:
                    exit_code = await self._process.wait()
                    logger.error(
                        "MCP process died | command={} | exit_code={}",
                        self._command,
                        exit_code,
                    )
                    self._is_healthy = False
                    self._health_event.clear()
                    await self._respawn_process()
                else:
                    await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            logger.info("Watchdog loop cancelled | command={}", self._command)
            raise

    async def call_tool(
        self, method: str, params: dict[str, Any], timeout: float = 3.0
    ) -> Any:
        """Execute a tool via MCP JSON-RPC with health check."""
        if not self._is_healthy:
            try:
                await asyncio.wait_for(self._health_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                raise MCPProcessError(
                    f"MCP process failed to recover within {timeout}s"
                )

        self._rpc_id += 1
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": self._rpc_id,
        }
        return await self._send_and_receive(payload)

    async def _send_and_receive(self, payload: dict[str, Any]) -> Any:
        """Internal helper to handle stdio I/O for JSON-RPC."""
        if not self._process or not self._process.stdin or not self._process.stdout:
            raise MCPProcessError("MCP process streams are unavailable")

        try:
            line = encode_json(payload) + b"\n"
            self._process.stdin.write(line)
            await self._process.stdin.drain()

            response_line = await self._process.stdout.readline()
            if not response_line:
                raise MCPProcessError("MCP process closed stdout unexpectedly")

            response = decode_json(response_line)  # type: ignore[reportArgumentType]
            if "error" in response:
                logger.error(
                    "MCP tool error | method={} | error={}",
                    payload["method"],
                    response["error"],
                )
                return None
            return response.get("result")
        except Exception as e:
            self._is_healthy = False
            self._health_event.clear()
            logger.error("MCP communication failure | error={}", str(e))
            raise MCPProcessError(f"MCP communication failure: {e}")

    async def stop(self) -> None:
        """Gracefully shutdown the MCP client and subprocess."""
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                if not self._watchdog_task.cancelled():
                    raise

        proc = self._process
        self._process = None

        if proc is None:
            return

        await self._shutdown_child_process(proc)

    @staticmethod
    async def _stdin_eof_shutdown(proc: asyncio.subprocess.Process) -> None:
        """Close stdin so line-oriented children exit; no-op if no stdin."""
        stdin = proc.stdin
        if stdin is None:
            return
        try:
            stdin.close()
            await stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def _shutdown_child_process(self, proc: asyncio.subprocess.Process) -> None:
        """terminate → wait; on timeout try kill. Tolerate sandbox EACCES on signals."""
        try:
            proc.terminate()
        except PermissionError:
            logger.warning(
                "MCP terminate not permitted (sandbox); trying stdin EOF | command={}",
                self._command,
            )
            await self._stdin_eof_shutdown(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "MCP subprocess alive after stdin EOF | command={}",
                    self._command,
                )
            return

        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
            return
        except asyncio.TimeoutError:
            logger.warning(
                "MCP process refused to terminate, killing | command={}",
                self._command,
            )

        try:
            proc.kill()
            await proc.wait()
        except PermissionError:
            logger.warning(
                "MCP kill not permitted (sandbox); trying stdin EOF | command={}",
                self._command,
            )
            await self._stdin_eof_shutdown(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "MCP subprocess still running after stdin EOF | command={}",
                    self._command,
                )
