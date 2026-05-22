"""GitHub provider models.

Defines the frozen Pydantic schemas for developer momentum metrics.
"""

from typing import Literal
from pydantic import BaseModel


class CommitMetric(BaseModel, frozen=True):
    """Metrics for a single commit."""

    sha: str
    author_login: str | None = None
    message_length: int
    date: str


class ReleaseEvent(BaseModel, frozen=True):
    """A GitHub release event."""

    tag_name: str
    published_at: str
    name: str | None = None


class RepoActivitySnapshot(BaseModel, frozen=True):
    """Snapshot of developer momentum for a repository."""

    owner: str
    repo: str
    recent_commits: list[CommitMetric]
    recent_releases: list[ReleaseEvent]
    status: Literal["active", "degraded"] = "active"
