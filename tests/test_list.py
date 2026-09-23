"""Tests for the Serializd ListProvider/ListEntry/ListMedia triad."""

from logging import getLogger
from typing import cast

import pytest
from anibridge.list import ListMediaType, ListStatus
from anibridge.utils.types import ProviderLogger

from anibridge.providers.list.serializd.client import SerializdAPIError, SerializdClient
from anibridge.providers.list.serializd.list import SerializdListProvider
from anibridge.providers.list.serializd.models import (
    DiaryEntry,
    DiaryResponse,
    NextEpisodeForUser,
    ReviewAddResponse,
    SeasonSummary,
    ShowDetail,
)


def _show(show_id: int, *, episode_count: int = 10) -> ShowDetail:
    return ShowDetail(
        id=show_id,
        name=f"Show {show_id}",
        seasons=[
            SeasonSummary(
                id=100 + show_id,
                seasonId=100 + show_id,
                episodeCount=episode_count,
                name="Season 1",
                seasonNumber=1,
            )
        ],
        numEpisodes=episode_count,
    )


def _multi_season_show(show_id: int) -> ShowDetail:
    """A show with a specials season plus two regular seasons (10 + 8 eps)."""
    return ShowDetail(
        id=show_id,
        name=f"Show {show_id}",
        seasons=[
            SeasonSummary(
                id=9000,
                seasonId=9000,
                episodeCount=2,
                name="Specials",
                seasonNumber=0,
            ),
            SeasonSummary(
                id=1001,
                seasonId=1001,
                episodeCount=10,
                name="Season 1",
                seasonNumber=1,
            ),
            SeasonSummary(
                id=1002,
                seasonId=1002,
                episodeCount=8,
                name="Season 2",
                seasonNumber=2,
            ),
        ],
        numEpisodes=20,
    )


class _FakeSerializdClient:
    """Lightweight Serializd client stub used by list.py tests."""

    def __init__(self) -> None:
        self.username = "chaoszero112"
        self.shows: dict[int, ShowDetail] = {}
        self.missing_shows: set[int] = set()
        self.progress: dict[int, NextEpisodeForUser | None] = {}
        self.diary_entries: list[DiaryEntry] = []
        self.log_show_calls: list[int] = []
        self.unlog_show_calls: list[int] = []
        self.log_episode_calls: list[tuple[int, int, list[int]]] = []
        self.unlog_episode_calls: list[tuple[int, int, list[int]]] = []
        self.review_calls: list[dict] = []

    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def get_show(self, show_id: int) -> ShowDetail:
        if show_id in self.missing_shows:
            raise SerializdAPIError(
                f"GET /api/show/{show_id} returned 404: not found", status_code=404
            )
        return self.shows[show_id]

    async def get_show_progress(self, show_id: int) -> NextEpisodeForUser | None:
        return self.progress.get(show_id)

    async def get_user_diary(self, username: str, page: int = 1) -> DiaryResponse:
        return DiaryResponse(reviews=self.diary_entries, totalPages=1)

    async def log_show(self, show_id: int) -> None:
        self.log_show_calls.append(show_id)

    async def unlog_show(self, show_id: int) -> None:
        self.unlog_show_calls.append(show_id)

    async def log_episodes(
        self, show_id: int, season_id: int, episode_numbers: list[int]
    ):
        self.log_episode_calls.append((show_id, season_id, episode_numbers))

    async def unlog_episodes(
        self, show_id: int, season_id: int, episode_numbers: list[int]
    ) -> None:
        self.unlog_episode_calls.append((show_id, season_id, episode_numbers))

    async def add_review(self, show_id: int, **kwargs) -> ReviewAddResponse:
        self.review_calls.append({"show_id": show_id, **kwargs})
        # Mirror Serializd's verified live behavior: season_id=null plus
        # is_log=true marks every episode watched and creates a diary
        # entry, so subsequent reads see the completion trace (C3) and the
        # rating (I1) instead of "forgetting" the write.
        if kwargs.get("add_to_diary"):
            entry = DiaryEntry(
                id=len(self.diary_entries) + 1,
                showId=show_id,
                dateAdded=f"2026-01-{len(self.diary_entries) + 1:02d}T00:00:00Z",
                rating=kwargs.get("rating"),
                reviewText=kwargs.get("review_text", ""),
            )
            self.diary_entries.append(entry)
            if kwargs.get("season_id") is None:
                self.progress[show_id] = None
        return ReviewAddResponse(
            id=len(self.diary_entries),
            showId=show_id,
            dateAdded="2026-01-01T00:00:00Z",
            rating=kwargs.get("rating"),
        )

    async def search_shows(self, query: str):
        return []


@pytest.fixture()
def fake_client() -> _FakeSerializdClient:
    return _FakeSerializdClient()


@pytest.fixture()
async def provider(fake_client: _FakeSerializdClient) -> SerializdListProvider:
    p = SerializdListProvider(
        config={"email": "u@example.com", "password": "pw"},
        logger=cast(ProviderLogger, getLogger("tests.serializd.list")),
    )
    p._client = cast(SerializdClient, fake_client)
    await p.initialize()
    return p


@pytest.mark.asyncio
async def test_get_entry_returns_entry_with_none_status_for_untouched_show(
    provider: SerializdListProvider, fake_client: _FakeSerializdClient
) -> None:
    # The show exists on Serializd but was never watched/logged - this must
    # NOT return None, since None is reserved for "does not exist" (C2).
    fake_client.shows[1396] = _show(1396)
    fake_client.progress[1396] = None
    fake_client.diary_entries = []

    entry = await provider.get_entry("1396")
    assert entry is not None
    assert entry.status is None
    assert entry.progress is None
    assert entry.user_rating is None


@pytest.mark.asyncio
async def test_get_entry_returns_none_for_nonexistent_show(
    provider: SerializdListProvider, fake_client: _FakeSerializdClient
) -> None:
    fake_client.missing_shows.add(999999)

    entry = await provider.get_entry("999999")
    assert entry is None


@pytest.mark.asyncio
async def test_get_entry_propagates_non_404_errors(
    provider: SerializdListProvider, fake_client: _FakeSerializdClient
) -> None:
    async def _raise(show_id: int) -> ShowDetail:
        raise SerializdAPIError("boom", status_code=500)

    fake_client.get_show = _raise  # ty: ignore[invalid-assignment]

    with pytest.raises(SerializdAPIError):
        await provider.get_entry("1396")


@pytest.mark.asyncio
async def test_get_entry_returns_current_for_partial_progress(
    provider: SerializdListProvider, fake_client: _FakeSerializdClient
) -> None:
    fake_client.shows[2316] = _show(2316, episode_count=10)
    fake_client.progress[2316] = NextEpisodeForUser(
        episodeId=1, episodeNumber=3, name="x", seasonNumber=1
    )

    entry = await provider.get_entry("2316")
    assert entry is not None
    assert entry.status is ListStatus.CURRENT
    assert entry.progress == 2
    assert entry.media().media_type is ListMediaType.TV
    assert entry.media().total_units == 10


@pytest.mark.asyncio
async def test_get_entry_returns_completed_when_diary_has_entry(
    provider: SerializdListProvider, fake_client: _FakeSerializdClient
) -> None:
    fake_client.shows[1396] = _show(1396, episode_count=62)
    fake_client.progress[1396] = None
    fake_client.diary_entries = [
        DiaryEntry(id=1, showId=1396, dateAdded="2026-01-01T00:00:00Z")
    ]

    entry = await provider.get_entry("1396")
    assert entry is not None
    assert entry.status is ListStatus.COMPLETED
    assert entry.progress == 62


@pytest.mark.asyncio
async def test_get_entry_populates_rating_from_latest_diary_entry(
    provider: SerializdListProvider, fake_client: _FakeSerializdClient
) -> None:
    fake_client.shows[1396] = _show(1396, episode_count=10)
    fake_client.progress[1396] = None
    fake_client.diary_entries = [
        DiaryEntry(
            id=1,
            showId=1396,
            dateAdded="2026-01-01T00:00:00Z",
            rating=7,
            reviewText="pretty good",
        )
    ]

    entry = await provider.get_entry("1396")
    assert entry is not None
    assert entry.user_rating == 70
    assert entry.review == "pretty good"


@pytest.mark.asyncio
async def test_get_entry_excludes_specials_from_total_units_and_progress(
    provider: SerializdListProvider, fake_client: _FakeSerializdClient
) -> None:
    fake_client.shows[555] = _multi_season_show(555)
    # S2E4 (within-season) => season 1 fully watched (10) + 3 of season 2.
    fake_client.progress[555] = NextEpisodeForUser(
        episodeId=1, episodeNumber=4, name="x", seasonNumber=2
    )

    entry = await provider.get_entry("555")
    assert entry is not None
    assert entry.media().total_units == 18  # 10 + 8, specials excluded
    assert entry.progress == 13  # 10 (season 1) + 3 (season 2)


@pytest.mark.asyncio
async def test_resolve_mapping_descriptors_only_accepts_tmdb_show(
    provider: SerializdListProvider,
) -> None:
    targets = await provider.resolve_mapping_descriptors(
        [
            ("tmdb_show", "1396", None),
            ("tmdb_movie", "999", None),
            ("mal", "1", None),
        ]
    )
    assert [t.media_key for t in targets] == ["1396"]


@pytest.mark.asyncio
async def test_update_entry_full_progress_marks_watched_via_diary_logged_review(
    provider: SerializdListProvider, fake_client: _FakeSerializdClient
) -> None:
    fake_client.shows[1396] = _show(1396, episode_count=10)
    fake_client.progress[1396] = NextEpisodeForUser(
        episodeId=1, episodeNumber=1, name="x", seasonNumber=1
    )
    entry = await provider.get_entry("1396")
    assert entry is not None

    entry.progress = 10
    await provider.update_entry("1396", entry)

    # No log_show call - full completion now routes through add_review with
    # season_id=None + is_log=true, which both marks everything watched AND
    # leaves a diary trace (C3), instead of the diary-blind log_show call.
    assert fake_client.log_show_calls == []
    assert fake_client.review_calls == [
        {
            "show_id": 1396,
            "season_id": None,
            "rating": None,
            "review_text": "",
            "add_to_diary": True,
        }
    ]


@pytest.mark.asyncio
async def test_update_entry_partial_progress_calls_log_episodes(
    provider: SerializdListProvider, fake_client: _FakeSerializdClient
) -> None:
    fake_client.shows[1396] = _show(1396, episode_count=10)
    fake_client.progress[1396] = NextEpisodeForUser(
        episodeId=1, episodeNumber=1, name="x", seasonNumber=1
    )
    entry = await provider.get_entry("1396")
    assert entry is not None

    entry.progress = 4
    await provider.update_entry("1396", entry)

    assert fake_client.log_episode_calls == [(1396, 1496, [1, 2, 3, 4])]


@pytest.mark.asyncio
async def test_update_entry_partial_progress_uses_ordered_seasons_across_boundary(
    provider: SerializdListProvider, fake_client: _FakeSerializdClient
) -> None:
    fake_client.shows[555] = _multi_season_show(555)
    fake_client.progress[555] = NextEpisodeForUser(
        episodeId=1, episodeNumber=1, name="x", seasonNumber=1
    )
    entry = await provider.get_entry("555")
    assert entry is not None

    entry.progress = 13  # season 1 full (10) + 3 of season 2
    await provider.update_entry("555", entry)

    assert fake_client.log_episode_calls == [
        (555, 1001, list(range(1, 11))),
        (555, 1002, [1, 2, 3]),
    ]


@pytest.mark.asyncio
async def test_update_entry_skips_network_calls_when_nothing_changed(
    provider: SerializdListProvider, fake_client: _FakeSerializdClient
) -> None:
    fake_client.shows[1396] = _show(1396)
    fake_client.progress[1396] = NextEpisodeForUser(
        episodeId=1, episodeNumber=1, name="x", seasonNumber=1
    )
    entry = await provider.get_entry("1396")
    assert entry is not None

    await provider.update_entry("1396", entry)

    assert fake_client.log_show_calls == []
    assert fake_client.log_episode_calls == []
    assert fake_client.review_calls == []


@pytest.mark.asyncio
async def test_update_entry_lowering_progress_unlogs_episodes(
    provider: SerializdListProvider, fake_client: _FakeSerializdClient
) -> None:
    fake_client.shows[1396] = _show(1396, episode_count=10)
    fake_client.progress[1396] = NextEpisodeForUser(
        episodeId=1, episodeNumber=9, name="x", seasonNumber=1
    )
    entry = await provider.get_entry("1396")
    assert entry is not None
    assert entry.progress == 8

    entry.progress = 3
    await provider.update_entry("1396", entry)

    assert fake_client.log_episode_calls == []
    assert fake_client.unlog_episode_calls == [(1396, 1496, [4, 5, 6, 7, 8])]


@pytest.mark.asyncio
async def test_status_setter_is_a_documented_noop(
    provider: SerializdListProvider, fake_client: _FakeSerializdClient
) -> None:
    fake_client.shows[1396] = _show(1396)
    fake_client.progress[1396] = NextEpisodeForUser(
        episodeId=1, episodeNumber=1, name="x", seasonNumber=1
    )
    entry = await provider.get_entry("1396")
    assert entry is not None

    entry.status = ListStatus.PAUSED
    assert entry.status is ListStatus.CURRENT  # unchanged


@pytest.mark.parametrize(
    ("anibridge_rating", "serializd_rating"),
    [(0, 0), (100, 10), (50, 5), (73, 7)],
)
def test_user_rating_round_trips_through_scale_conversion(
    anibridge_rating: int, serializd_rating: int
) -> None:
    from anibridge.providers.list.serializd.list import SerializdListEntry

    show = _show(1)
    entry = SerializdListEntry(
        cast("SerializdListProvider", object()), show, progress=None, status=None
    )
    entry.user_rating = anibridge_rating
    assert entry._rating == serializd_rating
    assert entry.user_rating == serializd_rating * 10


def test_user_rating_rejects_out_of_range_values() -> None:
    from anibridge.providers.list.serializd.list import SerializdListEntry

    show = _show(1)
    entry = SerializdListEntry(
        cast("SerializdListProvider", object()), show, progress=None, status=None
    )
    with pytest.raises(ValueError, match="between 0 and 100"):
        entry.user_rating = 101
    with pytest.raises(ValueError, match="between 0 and 100"):
        entry.user_rating = -1


@pytest.mark.asyncio
async def test_delete_entry_calls_unlog_show(
    provider: SerializdListProvider, fake_client: _FakeSerializdClient
) -> None:
    await provider.delete_entry("1396")
    assert fake_client.unlog_show_calls == [1396]


@pytest.mark.asyncio
async def test_multi_season_write_then_read_round_trip(
    provider: SerializdListProvider, fake_client: _FakeSerializdClient
) -> None:
    """C3 + C4 + I4, exercised together on a multi-season show.

    A show with a specials season plus two regular seasons (10 + 8 = 18
    non-special episodes). Walks: partial progress read (crossing a season
    boundary) -> write full completion -> read back (must stay COMPLETED
    with the rating intact, not revert to absent) -> write a lowered
    progress (crossing the season boundary backwards) -> read back again.
    """
    fake_client.shows[555] = _multi_season_show(555)

    # 1. Partial progress, pointer mid-way through season 2 (within-season
    #    numbering) - progress must be reported as an absolute count across
    #    both regular seasons, not the raw within-season pointer (C4).
    fake_client.progress[555] = NextEpisodeForUser(
        episodeId=1, episodeNumber=4, name="x", seasonNumber=2
    )
    entry = await provider.get_entry("555")
    assert entry is not None
    assert entry.media().total_units == 18
    assert entry.progress == 13  # 10 (season 1) + 3 (season 2)
    assert entry.status is ListStatus.CURRENT
    assert entry.user_rating is None

    # 2. Write full completion plus a rating.
    entry.progress = 18
    entry.user_rating = 90
    await provider.update_entry("555", entry)

    # Full completion must route through add_review(season_id=None,
    # add_to_diary=True) - never the diary-blind log_show - and must not
    # also fire a second, separate rating-only add_review call.
    assert fake_client.log_show_calls == []
    assert fake_client.review_calls == [
        {
            "show_id": 555,
            "season_id": None,
            "rating": 9,
            "review_text": "",
            "add_to_diary": True,
        }
    ]

    # 3. Read back: must see COMPLETED with the rating intact, not "forget"
    #    the write and report absent again (C3), and the rating must round-
    #    trip (I1).
    completed_entry = await provider.get_entry("555")
    assert completed_entry is not None
    assert completed_entry.status is ListStatus.COMPLETED
    assert completed_entry.progress == 18
    assert completed_entry.user_rating == 90

    # 4. Lower progress back down, crossing the season boundary. This must
    #    unlog the shrinking range (I4), using the same per-season
    #    accounting as the forward-logging path (C4), rather than silently
    #    doing nothing.
    completed_entry.progress = 5
    await provider.update_entry("555", completed_entry)

    assert fake_client.unlog_episode_calls == [
        (555, 1001, [6, 7, 8, 9, 10]),
        (555, 1002, [1, 2, 3, 4, 5, 6, 7, 8]),
    ]

    # Simulate the server-side effect of those unlogs (the fake client only
    # auto-derives progress for the add_review "mark everything watched"
    # path, not per-episode unlog/log calls) so we can assert the read side
    # too: watched down to season 1 episode 5, so next-unwatched is S1E6.
    fake_client.progress[555] = NextEpisodeForUser(
        episodeId=1, episodeNumber=6, name="x", seasonNumber=1
    )

    lowered_entry = await provider.get_entry("555")
    assert lowered_entry is not None
    assert lowered_entry.status is ListStatus.CURRENT
    assert lowered_entry.progress == 5
    # The rating persists even though progress moved - it lives on the
    # diary entry independently of the watch pointer.
    assert lowered_entry.user_rating == 90
