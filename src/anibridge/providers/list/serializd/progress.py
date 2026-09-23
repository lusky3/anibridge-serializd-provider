"""Progress/status derivation and diary-based disambiguation."""

from typing import TYPE_CHECKING

from anibridge.list import ListStatus

from anibridge.providers.list.serializd.models import NextEpisodeForUser

if TYPE_CHECKING:
    from anibridge.providers.list.serializd.client import SerializdClient

__all__ = ["DiaryIndex", "derive_progress_and_status"]


def derive_progress_and_status(
    *,
    total_episodes: int | None,
    next_episode: NextEpisodeForUser | None,
    has_diary_entry: bool,
) -> tuple[int | None, ListStatus | None]:
    """Derive (progress, status) from the verified Serializd signals.

    `next_episode` is `None` both when a show is fully watched and when it
    has never been touched - live-verified, not an assumption. `has_diary_entry`
    disambiguates the two by checking whether the show has any diary
    (review-log) entry. A show completed purely through raw `episode_log`
    calls with zero diary trace is indistinguishable from untouched and is
    reported as absent (`(None, None)`) - a documented, accepted limitation.
    """
    if next_episode is not None:
        return next_episode.episodeNumber - 1, ListStatus.CURRENT

    if has_diary_entry:
        return total_episodes, ListStatus.COMPLETED

    return None, None


class DiaryIndex:
    """Per-session index of show ids with at least one diary entry.

    Exists because the diary endpoint's `show_id`/`showId` query params are
    verified to be a no-op (always return the same unfiltered page), so the
    only way to check "has this show been logged" is to paginate the whole
    diary once and check client-side.
    """

    def __init__(self, client: "SerializdClient", username: str) -> None:
        """Construct the index for a given client/username, unbuilt until needed."""
        self._client = client
        self._username = username
        self._show_ids: set[int] | None = None

    async def contains(self, show_id: int) -> bool:
        """Return True if the show has any diary (review-log) entry."""
        if self._show_ids is None:
            await self._build()
        assert self._show_ids is not None
        return show_id in self._show_ids

    async def _build(self) -> None:
        """Paginate the full diary and cache the set of show ids it covers."""
        show_ids: set[int] = set()
        page = 1
        while True:
            response = await self._client.get_user_diary(self._username, page=page)
            show_ids.update(entry.showId for entry in response.reviews)
            if page >= response.totalPages:
                break
            page += 1
        self._show_ids = show_ids

    def invalidate(self) -> None:
        """Drop the cached index so the next `contains` call rebuilds it."""
        self._show_ids = None
