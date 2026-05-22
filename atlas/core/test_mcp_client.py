import asyncio
import sys

import pytest

from atlas.core.mcp_client import MCPClient, MCPProcessError
from atlas.shared.config import PolarisSettings


async def _crash_mcp_subprocess(client: MCPClient) -> None:
    """End the MCP child — prefer SIGKILL, fall back to stdin EOF when disallowed."""

    proc = client._process
    if proc is None:
        return

    try:
        proc.kill()
    except PermissionError:
        stdin = proc.stdin
        if stdin is None:
            raise
        stdin.close()
        await stdin.wait_closed()


@pytest.fixture
def settings() -> PolarisSettings:
    return PolarisSettings()


@pytest.mark.asyncio
async def test_mcp_client_lifecycle(settings: PolarisSettings) -> None:
    print("\nStarting test_mcp_client_lifecycle")
    cmd = sys.executable
    args = ["-c", "import sys, json; [print(json.dumps({'jsonrpc': '2.0', 'result': json.loads(l)['params'], 'id': json.loads(l)['id']}), flush=True) for l in sys.stdin]"]
    
    client = MCPClient(cmd, args, settings)
    await client.start()
    print("Client started")
    
    res = await client.call_tool("echo", {"foo": "bar"})
    print(f"First call result: {res}")
    assert res == {"foo": "bar"}
    
    old_pid = client._process.pid if client._process else None
    print(f"Killing process {old_pid}")
    await _crash_mcp_subprocess(client)
    
    print("Waiting for respawn...")
    for _ in range(10):
        await asyncio.sleep(0.5)
        if client._is_healthy and client._process and client._process.pid != old_pid:
            break
    
    print(f"New process healthy: {client._is_healthy}, new pid: {client._process.pid if client._process else 'None'}")
    assert client._is_healthy is True
    assert client._process is not None
    assert client._process.pid != old_pid
    
    res = await client.call_tool("echo", {"baz": "qux"})
    print(f"Second call result: {res}")
    assert res == {"baz": "qux"}
    
    await client.stop()
    print("Test finished")


@pytest.mark.asyncio
async def test_call_tool_waits_for_recovery(settings: PolarisSettings) -> None:
    print("\nStarting test_call_tool_waits_for_recovery")
    cmd = sys.executable
    args = ["-c", "import sys, json; [print(json.dumps({'jsonrpc': '2.0', 'result': json.loads(l)['params'], 'id': json.loads(l)['id']}), flush=True) for l in sys.stdin]"]
    
    client = MCPClient(cmd, args, settings)
    await client.start()
    
    client._is_healthy = False
    client._health_event.clear()
    
    print("Calling tool while unhealthy...")
    call_task = asyncio.create_task(client.call_tool("echo", {"wait": True}))
    
    await asyncio.sleep(0.5)
    print("Restoring health manually...")
    client._is_healthy = True
    client._health_event.set()
    
    res = await call_task
    print(f"Call result: {res}")
    assert res == {"wait": True}
    
    await client.stop()


@pytest.mark.asyncio
async def test_call_tool_raises_on_timeout(settings: PolarisSettings) -> None:
    print("\nStarting test_call_tool_raises_on_timeout")
    cmd = "false"
    client = MCPClient(cmd, [], settings)
    
    with pytest.raises(MCPProcessError, match="failed to recover"):
        await client.call_tool("echo", {}, timeout=0.1)


@pytest.mark.asyncio
async def test_graceful_shutdown(settings: PolarisSettings) -> None:
    print("\nStarting test_graceful_shutdown")
    cmd = sys.executable
    # Block on stdin so EOF (used when SIGTERM is disallowed) ends the child.
    args = ["-c", "import sys; sys.stdin.read()"]

    client = MCPClient(cmd, args, settings)
    await client.start()
    
    process = client._process
    assert process is not None
    assert process.returncode is None
    
    print("Stopping client...")
    await client.stop()
    print("Client stopped")
    
    assert process.returncode is not None
    assert client._watchdog_task is not None and client._watchdog_task.done()
