"""GitHub API connector for developer momentum.

Provides a REST adapter to extract commit velocity and release tags
from the GitHub API with strict rate limiting and safe degradation.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import msgspec
import redis.asyncio as redis_async
from loguru import logger

from atlas.providers.base import BaseProvider, ProviderHealth
from atlas.providers.github.models import CommitMetric, ReleaseEvent, RepoActivitySnapshot


class GitHubProvider(BaseProvider):
    """GitHub REST API adapter for ATLAS developer activity."""

    def __init__(
        self,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
        http_client: httpx.AsyncClient,
        github_token: str | None = None
    ) -> None:
        super().__init__("github", redis_client, max_concurrent=10)
        self._http = http_client
        self._token = github_token
        self._limit_key = "github:rate_limit:window"
        self._cache_prefix = "github:cache:"
        self._headers = {"Accept": "application/vnd.github.v3+json"}
        if self._token:
            self._headers["Authorization"] = f"Bearer {self._token}"

    async def _check_rate_limit(self) -> bool:
        """Strict sliding-window rate limiter for GitHub API."""
        now = time.time()
        window_start = now - 3600
        async with self._redis.pipeline() as pipe:
            pipe.zremrangebyscore(self._limit_key, 0, window_start)
            pipe.zcard(self._limit_key)
            pipe.zadd(self._limit_key, {f"{now}": now})
            pipe.expire(self._limit_key, 3600)
            results = await pipe.execute()
        return results[1] < 5000

    async def _fetch_url(self, url: str) -> bytes | None:
        """Fetch URL with safe fallback degradation."""
        if not await self._check_rate_limit():
            logger.warning("GitHub rate limit exceeded in sliding window")
            self.mark_degraded("Rate limit exceeded")
            return None
            
        try:
            resp = await self._http.get(url, headers=self._headers, timeout=10.0)
            if resp.status_code in (403, 500):
                logger.warning("GitHub API degraded | status={} | url={}", resp.status_code, url)
                self.mark_degraded(f"API HTTP {resp.status_code}")
                return None
            resp.raise_for_status()
            self.mark_healthy()
            return resp.content
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("GitHub fetch failed | err={}", str(exc))
            self.mark_degraded(str(exc))
            return None

    def _parse_commits(self, content: bytes) -> list[CommitMetric]:
        """Parse raw commit JSON without crashing async loop."""
        try:
            data = msgspec.json.decode(content)
            if not isinstance(data, list):
                return []
            
            metrics = []
            for item in data[:10]:
                sha = item.get("sha", "")
                commit_obj = item.get("commit", {})
                msg = commit_obj.get("message", "")
                author = item.get("author") or {}
                date = commit_obj.get("author", {}).get("date", "")
                metrics.append(
                    CommitMetric(
                        sha=sha, author_login=author.get("login"),
                        message_length=len(msg), date=date
                    )
                )
            return metrics
        except Exception as exc:
            logger.warning("Failed to parse GitHub commits | err={}", str(exc))
            return []

    def _parse_releases(self, content: bytes) -> list[ReleaseEvent]:
        """Parse raw release JSON without crashing async loop."""
        try:
            data = msgspec.json.decode(content)
            if not isinstance(data, list):
                return []
            
            events = []
            for item in data[:5]:
                events.append(
                    ReleaseEvent(
                        tag_name=item.get("tag_name", ""),
                        published_at=item.get("published_at", ""),
                        name=item.get("name")
                    )
                )
            return events
        except Exception as exc:
            logger.warning("Failed to parse GitHub releases | err={}", str(exc))
            return []

    async def get_repository_velocity(self, owner: str, repo: str) -> RepoActivitySnapshot:
        """Poll GitHub API for developer momentum metrics and cache results."""
        cache_key = f"{self._cache_prefix}{owner}:{repo}"
        cached = await self._redis.get(cache_key)
        if cached:
            try:
                data = msgspec.json.decode(cached)
                return RepoActivitySnapshot(**data)
            except Exception:
                pass
                
        async with self._semaphore:
            c_url = f"https://api.github.com/repos/{owner}/{repo}/commits"
            r_url = f"https://api.github.com/repos/{owner}/{repo}/releases"
            
            c_content = await self._fetch_url(c_url)
            r_content = await self._fetch_url(r_url)
            
            if c_content is None or r_content is None:
                return RepoActivitySnapshot(
                    owner=owner, repo=repo, recent_commits=[], recent_releases=[], status="degraded"
                )
                
            commits = self._parse_commits(c_content)
            releases = self._parse_releases(r_content)
            snapshot = RepoActivitySnapshot(
                owner=owner, repo=repo, recent_commits=commits, recent_releases=releases
            )
            
            cache_data = {
                "owner": owner, "repo": repo,
                "recent_commits": [{"sha": c.sha, "author_login": c.author_login, "message_length": c.message_length, "date": c.date} for c in commits],
                "recent_releases": [{"tag_name": r.tag_name, "published_at": r.published_at, "name": r.name} for r in releases],
                "status": "active"
            }
            await self._redis.setex(cache_key, 3600, msgspec.json.encode(cache_data))
            return snapshot

    async def get_health_status(self) -> ProviderHealth:
        """Return the current health snapshot of the provider."""
        return ProviderHealth(
            name=self.provider_name,
            status=self.status,
            last_update=time.monotonic(),
            error=self._last_error
        )

    async def close(self) -> None:
        """Release any internal resources held by the provider."""
        pass
