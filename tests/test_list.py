"""Tests for the Serializd ListProvider/ListEntry/ListMedia triad."""

from logging import getLogger
from typing import cast

import pytest
from anibridge.list import ListMediaType, ListStatus
from anibridge.utils.types import ProviderLogger

from anibridge.providers.list.serializd.client import SerializdClient
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


class _FakeSerializdClient:
    """Lightweight Serializd client stub used by list.py tests."""

    def __init__(self) -> None:
        self.username = "chaoszero112"
        self.shows: dict[int, ShowDetail] = {}
        self.progress: dict[int, NextEpisodeForUser | None] = {}
        self.diary_entries: list[DiaryEntry] = []
        self.log_show_calls: list[int] = []
        self.unlog_show_calls: list[int] = []
        self.log_episode_calls: list[tuple[int, int, list[int]]] = []
        self.review_calls: list[dict] = []

    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def get_show(self, show_id: int) -> ShowDetail:
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

    async def add_review(self, show_id: int, **kwargs) -> ReviewAddResponse:
        self.review_calls.append({"show_id": show_id, **kwargs})
        return ReviewAddResponse(
            id=1,
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
async def test_get_entry_returns_none_for_untouched_show(
    provider: SerializdListProvider, fake_client: _FakeSerializdClient
) -> None:
    fake_client.shows[1396] = _show(1396)
    fake_client.progress[1396] = None
    fake_client.diary_entries = []

    entry = await provider.get_entry("1396")
    assert entry is None


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
async def test_update_entry_full_progress_calls_log_show(
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

    assert fake_client.log_show_calls == [1396]


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
