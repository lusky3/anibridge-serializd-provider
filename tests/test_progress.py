"""Tests for progress/status derivation and the diary index."""

from typing import cast

import pytest
from anibridge.list import ListStatus

from anibridge.providers.list.serializd.models import (
    DiaryEntry,
    DiaryResponse,
    NextEpisodeForUser,
)
from anibridge.providers.list.serializd.progress import (
    DiaryIndex,
    derive_progress_and_status,
)


def _next_episode(number: int) -> NextEpisodeForUser:
    return NextEpisodeForUser(
        episodeId=1, episodeNumber=number, name="x", seasonNumber=1
    )


def test_partial_progress_from_next_episode_pointer() -> None:
    progress, status = derive_progress_and_status(
        total_episodes=10, next_episode=_next_episode(3), has_diary_entry=False
    )
    assert progress == 2
    assert status is ListStatus.CURRENT


def test_completed_when_pointer_null_and_diary_has_entry() -> None:
    progress, status = derive_progress_and_status(
        total_episodes=62, next_episode=None, has_diary_entry=True
    )
    assert progress == 62
    assert status is ListStatus.COMPLETED


def test_absent_when_pointer_null_and_no_diary_entry() -> None:
    # This is the verified ambiguity: nextEpisodeForUser is null for BOTH a
    # never-touched show and a fully-watched one, and the diary is the only
    # disambiguating signal this provider has.
    progress, status = derive_progress_and_status(
        total_episodes=10, next_episode=None, has_diary_entry=False
    )
    assert progress is None
    assert status is None


def test_status_is_never_paused_dropped_planning_or_repeating() -> None:
    # Only None/CURRENT/COMPLETED are ever produced - the progress-only
    # scope decision.
    for total, next_ep, has_diary in [
        (10, _next_episode(1), False),
        (10, None, True),
        (10, None, False),
    ]:
        _, status = derive_progress_and_status(
            total_episodes=total, next_episode=next_ep, has_diary_entry=has_diary
        )
        assert status in (None, ListStatus.CURRENT, ListStatus.COMPLETED)


class _FakeDiaryClient:
    """Fake client exposing only get_user_diary, paginated."""

    def __init__(self, pages: list[list[DiaryEntry]]) -> None:
        self._pages = pages
        self.calls: list[int] = []

    async def get_user_diary(self, username: str, page: int = 1) -> DiaryResponse:
        self.calls.append(page)
        return DiaryResponse(
            reviews=self._pages[page - 1], totalPages=len(self._pages)
        )


def _entry(show_id: int) -> DiaryEntry:
    return DiaryEntry(id=1, showId=show_id, dateAdded="2026-01-01T00:00:00Z")


@pytest.mark.asyncio
async def test_diary_index_paginates_through_every_page() -> None:
    fake = _FakeDiaryClient(
        pages=[[_entry(1)], [_entry(2)], [_entry(3)]]
    )
    index = DiaryIndex(cast("SerializdClient", fake), "user")

    assert await index.contains(3) is True
    assert fake.calls == [1, 2, 3]


@pytest.mark.asyncio
async def test_diary_index_returns_false_for_untouched_show() -> None:
    fake = _FakeDiaryClient(pages=[[_entry(1)]])
    index = DiaryIndex(cast("SerializdClient", fake), "user")

    assert await index.contains(999) is False


@pytest.mark.asyncio
async def test_diary_index_builds_only_once() -> None:
    fake = _FakeDiaryClient(pages=[[_entry(1)]])
    index = DiaryIndex(cast("SerializdClient", fake), "user")

    await index.contains(1)
    await index.contains(1)
    assert fake.calls == [1]


@pytest.mark.asyncio
async def test_diary_index_invalidate_forces_rebuild() -> None:
    fake = _FakeDiaryClient(pages=[[_entry(1)]])
    index = DiaryIndex(cast("SerializdClient", fake), "user")

    await index.contains(1)
    index.invalidate()
    await index.contains(1)
    assert fake.calls == [1, 1]
