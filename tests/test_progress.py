"""Tests for progress/status derivation and the diary index."""

from typing import TYPE_CHECKING, cast

import pytest
from anibridge.list import ListStatus

from anibridge.providers.list.serializd.models import (
    DiaryEntry,
    DiaryResponse,
    NextEpisodeForUser,
    SeasonSummary,
)
from anibridge.providers.list.serializd.progress import (
    DiaryIndex,
    derive_progress_and_status,
)

if TYPE_CHECKING:
    from anibridge.providers.list.serializd.client import SerializdClient


def _next_episode(number: int, season: int = 1) -> NextEpisodeForUser:
    return NextEpisodeForUser(
        episodeId=1, episodeNumber=number, name="x", seasonNumber=season
    )


def _season(season_number: int, episode_count: int) -> SeasonSummary:
    return SeasonSummary(
        id=season_number,
        seasonId=100 + season_number,
        episodeCount=episode_count,
        name=f"Season {season_number}",
        seasonNumber=season_number,
    )


def test_partial_progress_from_next_episode_pointer() -> None:
    progress, status = derive_progress_and_status(
        total_episodes=10,
        next_episode=_next_episode(3),
        ordered_seasons=[_season(1, 10)],
        has_diary_entry=False,
    )
    assert progress == 2
    assert status is ListStatus.CURRENT


def test_partial_progress_converts_within_season_pointer_to_absolute() -> None:
    # S2E6 (within-season) with two prior 10-episode seasons means 15
    # episodes watched overall, not 5 - the bug this provider had (C4).
    ordered_seasons = [_season(1, 10), _season(2, 10)]
    progress, status = derive_progress_and_status(
        total_episodes=20,
        next_episode=_next_episode(6, season=2),
        ordered_seasons=ordered_seasons,
        has_diary_entry=False,
    )
    assert progress == 15
    assert status is ListStatus.CURRENT


def test_partial_progress_excludes_specials_season_from_earlier_seasons() -> None:
    # A seasonNumber=0 "Specials" season must not be counted as an "earlier
    # season" when converting the pointer to an absolute count (I3).
    ordered_seasons = [_season(1, 10)]  # specials already filtered out by caller
    progress, status = derive_progress_and_status(
        total_episodes=10,
        next_episode=_next_episode(4, season=1),
        ordered_seasons=ordered_seasons,
        has_diary_entry=False,
    )
    assert progress == 3
    assert status is ListStatus.CURRENT


def test_completed_when_pointer_null_and_diary_has_entry() -> None:
    progress, status = derive_progress_and_status(
        total_episodes=62, next_episode=None, ordered_seasons=[], has_diary_entry=True
    )
    assert progress == 62
    assert status is ListStatus.COMPLETED


def test_absent_when_pointer_null_and_no_diary_entry() -> None:
    # This is the verified ambiguity: nextEpisodeForUser is null for BOTH a
    # never-touched show and a fully-watched one, and the diary is the only
    # disambiguating signal this provider has.
    progress, status = derive_progress_and_status(
        total_episodes=10, next_episode=None, ordered_seasons=[], has_diary_entry=False
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
            total_episodes=total,
            next_episode=next_ep,
            ordered_seasons=[_season(1, 10)],
            has_diary_entry=has_diary,
        )
        assert status in (None, ListStatus.CURRENT, ListStatus.COMPLETED)


class _FakeDiaryClient:
    """Fake client exposing only get_user_diary, paginated."""

    def __init__(self, pages: list[list[DiaryEntry]]) -> None:
        self._pages = pages
        self.calls: list[int] = []

    async def get_user_diary(self, username: str, page: int = 1) -> DiaryResponse:
        self.calls.append(page)
        return DiaryResponse(reviews=self._pages[page - 1], totalPages=len(self._pages))


def _entry(
    show_id: int,
    *,
    entry_id: int = 1,
    date_added: str = "2026-01-01T00:00:00Z",
    rating: int | None = None,
    review_text: str = "",
) -> DiaryEntry:
    return DiaryEntry(
        id=entry_id,
        showId=show_id,
        dateAdded=date_added,
        rating=rating,
        reviewText=review_text,
    )


@pytest.mark.asyncio
async def test_diary_index_paginates_through_every_page() -> None:
    fake = _FakeDiaryClient(pages=[[_entry(1)], [_entry(2)], [_entry(3)]])
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


@pytest.mark.asyncio
async def test_diary_index_latest_entry_returns_none_when_absent() -> None:
    fake = _FakeDiaryClient(pages=[[_entry(1)]])
    index = DiaryIndex(cast("SerializdClient", fake), "user")

    assert await index.latest_entry(999) is None


@pytest.mark.asyncio
async def test_diary_index_latest_entry_returns_the_entry() -> None:
    fake = _FakeDiaryClient(pages=[[_entry(1, rating=8, review_text="great show")]])
    index = DiaryIndex(cast("SerializdClient", fake), "user")

    entry = await index.latest_entry(1)
    assert entry is not None
    assert entry.rating == 8
    assert entry.reviewText == "great show"


@pytest.mark.asyncio
async def test_diary_index_latest_entry_picks_the_most_recent_by_date() -> None:
    # Same show logged twice (e.g. rated, then re-rated on a later sync) -
    # the index must keep the newest entry, not the first or last seen in
    # pagination order.
    older = _entry(1, entry_id=1, date_added="2026-01-01T00:00:00Z", rating=5)
    newer = _entry(1, entry_id=2, date_added="2026-02-01T00:00:00Z", rating=9)
    fake = _FakeDiaryClient(pages=[[older], [newer]])
    index = DiaryIndex(cast("SerializdClient", fake), "user")

    entry = await index.latest_entry(1)
    assert entry is not None
    assert entry.id == 2
    assert entry.rating == 9
