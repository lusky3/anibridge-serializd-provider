"""Progress/status derivation and diary-based disambiguation."""

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from anibridge.list import ListStatus

from anibridge.providers.list.serializd.models import (
    DiaryEntry,
    NextEpisodeForUser,
    SeasonSummary,
)

if TYPE_CHECKING:
    from anibridge.providers.list.serializd.client import SerializdClient

__all__ = ["DiaryIndex", "derive_progress_and_status"]


def _parse_date_added(value: str) -> datetime:
    """Parse a diary entry's `dateAdded` ISO8601 timestamp for comparison."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _absolute_progress(
    ordered_seasons: Sequence[SeasonSummary], next_episode: NextEpisodeForUser
) -> int:
    """Convert a within-season next-episode pointer into an absolute count.

    `nextEpisodeForUser.episodeNumber` resets to 1 each season (live-
    verified), so the absolute progress is every earlier ordered
    (non-special) season's full episode count, plus however many episodes
    of the pointer's own season have already been watched.
    """
    watched_in_earlier_seasons = sum(
        season.episodeCount
        for season in ordered_seasons
        if season.seasonNumber < next_episode.seasonNumber
    )
    return watched_in_earlier_seasons + (next_episode.episodeNumber - 1)


def derive_progress_and_status(
    *,
    total_episodes: int | None,
    next_episode: NextEpisodeForUser | None,
    ordered_seasons: Sequence[SeasonSummary],
    has_diary_entry: bool,
) -> tuple[int | None, ListStatus | None]:
    """Derive (progress, status) from the verified Serializd signals.

    `next_episode` is `None` both when a show is fully watched and when it
    has never been touched - live-verified, not an assumption. `has_diary_entry`
    disambiguates the two by checking whether the show has any diary
    (review-log) entry. A show completed purely through raw `episode_log`
    calls with zero diary trace is indistinguishable from untouched and is
    reported as absent (`(None, None)`) - a documented, accepted limitation.

    When `next_episode` is present, its within-season pointer is converted
    into an absolute, all-seasons progress count via `ordered_seasons` (the
    show's non-special seasons, sorted by season number), so progress is on
    the same scale the write side uses.
    """
    if next_episode is not None:
        return _absolute_progress(ordered_seasons, next_episode), ListStatus.CURRENT

    if has_diary_entry:
        return total_episodes, ListStatus.COMPLETED

    return None, None


class DiaryIndex:
    """Per-session index of the latest diary (review-log) entry per show.

    Exists because the diary endpoint's `show_id`/`showId` query params are
    verified to be a no-op (always return the same unfiltered page), so the
    only way to check "has this show been logged" - and to read back its
    rating/review - is to paginate the whole diary once and index it
    client-side.
    """

    def __init__(self, client: SerializdClient, username: str) -> None:
        """Construct the index for a given client/username, unbuilt until needed."""
        self._client = client
        self._username = username
        self._entries: dict[int, DiaryEntry] | None = None

    async def latest_entry(self, show_id: int) -> DiaryEntry | None:
        """Return the most recent diary entry for a show, or None if absent."""
        if self._entries is None:
            await self._build()
        assert self._entries is not None
        return self._entries.get(show_id)

    async def contains(self, show_id: int) -> bool:
        """Return True if the show has any diary (review-log) entry."""
        return await self.latest_entry(show_id) is not None

    async def _build(self) -> None:
        """Paginate the full diary, keeping only the latest entry per show."""
        entries: dict[int, DiaryEntry] = {}
        page = 1
        while True:
            response = await self._client.get_user_diary(self._username, page=page)
            for entry in response.reviews:
                existing = entries.get(entry.showId)
                if existing is None or _parse_date_added(
                    entry.dateAdded
                ) > _parse_date_added(existing.dateAdded):
                    entries[entry.showId] = entry
            if page >= response.totalPages:
                break
            page += 1
        self._entries = entries

    def invalidate(self) -> None:
        """Drop the cached index so the next lookup call rebuilds it."""
        self._entries = None
