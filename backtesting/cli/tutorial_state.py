"""Persist tutorial progress to the user's home directory."""

from __future__ import annotations

from pathlib import Path

import msgspec

_PROGRESS_PATH = Path.home() / ".polaris_tutorial_progress.json"


class TutorialProgress(msgspec.Struct, frozen=True):
    """Saved tutorial chapter progress."""

    last_chapter: int = 0
    completed_chapters: list[int] = msgspec.field(default_factory=list)


def load_progress() -> TutorialProgress:
    """Load tutorial progress from disk."""
    if not _PROGRESS_PATH.exists():
        return TutorialProgress()
    raw_bytes = _PROGRESS_PATH.read_bytes()
    return msgspec.json.decode(raw_bytes, type=TutorialProgress)


def save_progress(progress: TutorialProgress) -> None:
    """Persist tutorial progress."""
    _PROGRESS_PATH.write_bytes(msgspec.json.encode(progress))


def mark_chapter_complete(chapter: int) -> TutorialProgress:
    """Record a completed chapter and return updated progress."""
    current = load_progress()
    completed = sorted(set(current.completed_chapters + [chapter]))
    updated = TutorialProgress(last_chapter=chapter, completed_chapters=completed)
    save_progress(updated)
    return updated
