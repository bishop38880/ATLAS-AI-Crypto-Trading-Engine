"""GitHub API Provider.

Provides developer momentum metrics via the GitHub REST API.
"""

from .connector import GitHubProvider
from .models import CommitMetric, ReleaseEvent, RepoActivitySnapshot

__all__ = [
    "GitHubProvider",
    "CommitMetric",
    "ReleaseEvent",
    "RepoActivitySnapshot",
]
