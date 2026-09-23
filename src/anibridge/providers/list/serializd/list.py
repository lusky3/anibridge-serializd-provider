"""Serializd list provider for AniBridge."""

from collections.abc import Sequence
from datetime import datetime
from typing import Self, cast

import msgspec
from anibridge.list import (
    ListEntry,
    ListMedia,
    ListMediaType,
    ListProvider,
    ListStatus,
    ListTarget,
    ListUser,
)
from anibridge.utils.types import MappingDescriptor, ProviderLogger

from anibridge.providers.list.serializd.client import SerializdClient
from anibridge.providers.list.serializd.config import SerializdListProviderConfig
from anibridge.providers.list.serializd.models import ShowDetail
from anibridge.providers.list.serializd.progress import (
    DiaryIndex,
    derive_progress_and_status,
)

__all__ = ["SerializdListEntry", "SerializdListMedia", "SerializdListProvider"]


def _total_episodes(show: ShowDetail) -> int | None:
    """Sum episode counts across all seasons, or None if unknown."""
    total = sum(season.episodeCount for season in show.seasons)
    return total or None


class SerializdListMedia(ListMedia["SerializdListProvider"]):
    """AniBridge media wrapper for a Serializd (TMDB) show."""

    def __init__(self, provider: SerializdListProvider, show: ShowDetail) -> None:
        """Wrap a catalog show for AniBridge."""
        self._provider = provider
        self._show = show
        self._key = str(show.id)
        self._title = show.name

    @property
    def external_url(self) -> str | None:
        """Public Serializd URL for this show."""
        return f"https://www.serializd.com/show/{self._key}"

    @property
    def media_type(self) -> ListMediaType:
        """Serializd only tracks TV shows through the verified API."""
        return ListMediaType.TV

    @property
    def total_units(self) -> int | None:
        """Total episode count across all seasons."""
        return _total_episodes(self._show)


class SerializdListEntry(ListEntry["SerializdListProvider"]):
    """AniBridge list entry backed by Serializd watch/review state."""

    def __init__(
        self,
        provider: SerializdListProvider,
        show: ShowDetail,
        *,
        progress: int | None,
        status: ListStatus | None,
        rating: int | None = None,
        review_text: str | None = None,
    ) -> None:
        """Wrap a show plus its derived progress/status for AniBridge."""
        self._provider = provider
        self._show = show
        self._media = SerializdListMedia(provider, show)
        self._key = str(show.id)
        self._title = show.name

        self._progress = progress
        self._status = status
        self._rating = rating
        self._review_text = review_text
        self._changed_fields: set[str] = set()

    def __copy__(self) -> Self:
        """Return an isolated entry for planning list updates."""
        return type(self)(
            self._provider,
            self._show,
            progress=self._progress,
            status=self._status,
            rating=self._rating,
            review_text=self._review_text,
        )

    @property
    def progress(self) -> int | None:
        """Episodes watched, inferred from the next-unwatched-episode pointer."""
        return self._progress

    @progress.setter
    def progress(self, value: int | None) -> None:
        if value is not None and value < 0:
            raise ValueError("Progress cannot be negative.")
        self._progress = value
        self._changed_fields.add("progress")

    @property
    def repeats(self) -> int | None:
        """Not reachable through the verified Serializd API; always 0."""
        return 0

    @repeats.setter
    def repeats(self, value: int | None) -> None:
        # No per-show rewatch counter exists on Serializd's own account
        # state - accepted as a documented no-op.
        pass

    @property
    def review(self) -> str | None:
        """Review text, written via the reviews/add endpoint."""
        return self._review_text

    @review.setter
    def review(self, value: str | None) -> None:
        self._review_text = value
        self._changed_fields.add("review")

    @property
    def status(self) -> ListStatus | None:
        """Read-only derived status: None, CURRENT, or COMPLETED only."""
        return self._status

    @status.setter
    def status(self, value: ListStatus | None) -> None:
        # Serializd has no pause/drop concept for the account's own watch
        # state (only a friends/social view) - documented no-op, not a
        # silent lie about what was actually written.
        self._provider.log.debug(
            "Ignoring status write for Serializd entry %s (progress-only sync)",
            self._key,
        )

    @property
    def user_rating(self) -> int | None:
        """AniBridge 0-100 scale, converted from Serializd's 0-10 scale."""
        if self._rating is None:
            return None
        return self._rating * 10

    @user_rating.setter
    def user_rating(self, value: int | None) -> None:
        if value is None:
            self._rating = None
            self._changed_fields.add("user_rating")
            return
        if value < 0 or value > 100:
            raise ValueError("Ratings must be between 0 and 100.")
        self._rating = round(value / 10)
        self._changed_fields.add("user_rating")

    @property
    def started_at(self) -> datetime | None:
        """Not reachable through the verified Serializd API."""
        return None

    @started_at.setter
    def started_at(self, value: datetime | None) -> None:
        # Accepted as a documented no-op rather than raising for a routine
        # sync field with no corresponding Serializd write path.
        pass

    @property
    def finished_at(self) -> datetime | None:
        """Not reachable through the verified Serializd API."""
        return None

    @finished_at.setter
    def finished_at(self, value: datetime | None) -> None:
        # Accepted as a documented no-op; see started_at.
        pass

    def media(self) -> SerializdListMedia:
        """Return the media item this entry belongs to."""
        return self._media


class SerializdListProvider(ListProvider):
    """List provider backed by the (partially undocumented) Serializd API."""

    NAMESPACE = "serializd"
    MAPPING_PROVIDERS = frozenset({"tmdb_show"})

    def __init__(self, *, logger: ProviderLogger, config: dict | None = None) -> None:
        """Create the Serializd list provider with required credentials."""
        super().__init__(logger=logger, config=config)
        self.parsed_config = msgspec.convert(
            config or {}, type=SerializdListProviderConfig
        )
        self._client = SerializdClient(
            logger=self.log,
            email=self.parsed_config.email,
            password=self.parsed_config.password,
        )
        self._diary_index: DiaryIndex | None = None
        self._user: ListUser | None = None

    async def initialize(self) -> None:
        """Authenticate against Serializd and prepare the diary index."""
        self.log.debug("Initializing Serializd provider client")
        await self._client.initialize()
        if self._client.username is None:
            raise RuntimeError("Serializd provider initialized without a resolved user")
        self._user = ListUser(key=self._client.username, title=self._client.username)
        self._diary_index = DiaryIndex(self._client, self._client.username)
        self.log.debug(
            "Serializd provider initialized for user %s", self._client.username
        )

    async def close(self) -> None:
        """Close the underlying Serializd client session."""
        await self._client.close()
        self.log.debug("Closed Serializd provider client")

    async def clear_cache(self) -> None:
        """Invalidate the cached diary index."""
        if self._diary_index is not None:
            self._diary_index.invalidate()

    def user(self) -> ListUser | None:
        """Return cached Serializd user info if initialized."""
        return self._user

    async def resolve_mapping_descriptors(
        self, descriptors: Sequence[MappingDescriptor]
    ) -> Sequence[ListTarget]:
        """Resolve TMDB-show mapping descriptors into Serializd media keys."""
        return [
            ListTarget(descriptor=(provider, entry_id, scope), media_key=entry_id)
            for provider, entry_id, scope in descriptors
            if provider in self.MAPPING_PROVIDERS and entry_id
        ]

    async def get_entry(self, key: str) -> SerializdListEntry | None:
        """Fetch a single entry, deriving progress/status per the spec.

        The diary index is only consulted when the next-unwatched-episode
        pointer is null (the ambiguous case) - the common partial-progress
        path never pays the diary-pagination cost.
        """
        show_id = int(key)
        show = await self._client.get_show(show_id)
        next_episode = await self._client.get_show_progress(show_id)

        has_diary_entry = False
        if next_episode is None:
            assert self._diary_index is not None
            has_diary_entry = await self._diary_index.contains(show_id)

        progress, status = derive_progress_and_status(
            total_episodes=_total_episodes(show),
            next_episode=next_episode,
            has_diary_entry=has_diary_entry,
        )
        if status is None:
            return None

        return SerializdListEntry(self, show, progress=progress, status=status)

    async def update_entry(
        self, key: str, entry: ListEntry
    ) -> SerializdListEntry | None:
        """Write changed fields back to Serializd."""
        serializd_entry = cast(SerializdListEntry, entry)
        changed_fields = serializd_entry._changed_fields.copy()
        if not changed_fields:
            self.log.debug("Skipping unchanged Serializd entry for show id %s", key)
            return serializd_entry

        show_id = int(key)
        show = serializd_entry._show

        if "progress" in changed_fields and serializd_entry.progress is not None:
            total = _total_episodes(show) or 0
            target_progress = min(serializd_entry.progress, total)
            if total > 0 and target_progress >= total:
                await self._client.log_show(show_id)
            else:
                remaining = target_progress
                for season in show.seasons:
                    if remaining <= 0:
                        break
                    take = min(remaining, season.episodeCount)
                    if take > 0:
                        await self._client.log_episodes(
                            show_id, season.seasonId, list(range(1, take + 1))
                        )
                    remaining -= take

        if "user_rating" in changed_fields or "review" in changed_fields:
            await self._client.add_review(
                show_id,
                rating=serializd_entry._rating,
                review_text=serializd_entry._review_text or "",
                add_to_diary=False,
            )

        if self._diary_index is not None:
            self._diary_index.invalidate()
        serializd_entry._changed_fields.clear()
        self.log.debug("Updated Serializd entry for show id %s", key)
        return serializd_entry

    async def delete_entry(self, key: str) -> None:
        """Unmark the show watched on Serializd."""
        await self._client.unlog_show(int(key))
        if self._diary_index is not None:
            self._diary_index.invalidate()
        self.log.debug("Deleted Serializd entry for show id %s", key)

    async def search(self, query: str) -> Sequence[SerializdListEntry]:
        """Search Serializd's catalog, returning entries with no progress."""
        results = await self._client.search_shows(query)
        entries: list[SerializdListEntry] = []
        for result in results:
            show = ShowDetail(
                id=result.id,
                name=result.name,
                seasons=result.seasons,
                numSeasons=len(result.seasons),
                numEpisodes=sum(s.episodeCount for s in result.seasons),
                bannerImage=result.bannerImage,
            )
            entries.append(SerializdListEntry(self, show, progress=None, status=None))
        self.log.debug(
            "Serializd search query=%r yielded %s entries", query, len(entries)
        )
        return entries
