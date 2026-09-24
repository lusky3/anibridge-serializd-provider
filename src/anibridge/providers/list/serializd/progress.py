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


def _is_show_level(entry: DiaryEntry) -> bool:
    """Return True for a show-wide entry (not scoped to one season/episode)."""
    return entry.seasonId is None and entry.episodeNumber is None


class DiaryIndex:
    """Per-session index of diary (review-log) entries per show.

    Exists because the diary endpoint's `show_id`/`showId` query params are
    verified to be a no-op (always return the same unfiltered page), so the
    only way to check "has this show been logged" - and to read back its
    rating/review - is to paginate the whole diary once and index it
    client-side.

    Two views are kept: the latest entry of ANY scope per show (used only to
    detect that a show has been touched at all, regardless of whether that
    touch was show/season/episode-scoped), and the latest SHOW-LEVEL entry
    per show (used to read back rating/review text). Keeping them separate
    matters because a season- or episode-level log entry (typically
    `rating=None`) could otherwise be picked as "latest by date" and shadow
    an earlier, still-current show-level rating.
    """

    def __init__(self, client: SerializdClient, username: str) -> None:
        """Construct the index for a given client/username, unbuilt until needed."""
        self._client = client
        self._username = username
        self._entries: dict[int, DiaryEntry] | None = None
        self._show_level_entries: dict[int, DiaryEntry] | None = None

    async def latest_entry(self, show_id: int) -> DiaryEntry | None:
        """Return the most recent diary entry of any scope for a show."""
        if self._entries is None:
            await self._build()
        assert self._entries is not None
        return self._entries.get(show_id)

    async def show_level_entry(self, show_id: int) -> DiaryEntry | None:
        """Return the most recent show-level (not season/episode-scoped) entry."""
        if self._show_level_entries is None:
            await self._build()
        assert self._show_level_entries is not None
        return self._show_level_entries.get(show_id)

    async def contains(self, show_id: int) -> bool:
        """Return True if the show has any diary (review-log) entry."""
        return await self.latest_entry(show_id) is not None

    async def _build(self) -> None:
        """Paginate the full diary, keeping the latest entry per show per view."""
        entries: dict[int, DiaryEntry] = {}
        show_level_entries: dict[int, DiaryEntry] = {}
        page = 1
        while True:
            response = await self._client.get_user_diary(self._username, page=page)
            for entry in response.reviews:
                existing = entries.get(entry.showId)
                if existing is None or _parse_date_added(
                    entry.dateAdded
                ) > _parse_date_added(existing.dateAdded):
                    entries[entry.showId] = entry

                if _is_show_level(entry):
                    existing_show_level = show_level_entries.get(entry.showId)
                    if existing_show_level is None or _parse_date_added(
                        entry.dateAdded
                    ) > _parse_date_added(existing_show_level.dateAdded):
                        show_level_entries[entry.showId] = entry
            if page >= response.totalPages:
                break
            page += 1
        self._entries = entries
        self._show_level_entries = show_level_entries

    def invalidate(self) -> None:
        """Drop the cached index so the next lookup call rebuilds it."""
        self._entries = None
        self._show_level_entries = None
