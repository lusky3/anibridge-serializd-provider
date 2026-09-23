"""Async client for the (partially undocumented) Serializd API."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import aiohttp
import msgspec
from anibridge.utils.types import ProviderLogger

from anibridge.providers.list.serializd.models import (
    DiaryResponse,
    EpisodeLogAddResponse,
    LoginResponse,
    NextEpisodeForUser,
    ProfileStatsResponse,
    ReviewAddResponse,
    SeasonDetail,
    ShowDetail,
    ShowProgressResponse,
    ShowSearchResponse,
    ShowSearchResult,
    ValidateTokenResponse,
)

__all__ = ["SerializdAPIError", "SerializdClient"]

BASE_URL = "https://serializddesktop.onrender.com"
FRONT_PAGE_URL = "https://www.serializd.com"
APP_ID = "serializd_vercel"


class SerializdAPIError(Exception):
    """Raised when the Serializd API returns an error response."""


def _default_headers(token: str | None) -> dict[str, str]:
    """Return the headers every Serializd request must carry.

    Verified live: omitting Origin/Referer/X-Requested-With silently 401s
    even with a valid bearer token.
    """
    headers = {
        "Origin": FRONT_PAGE_URL,
        "Referer": FRONT_PAGE_URL,
        "X-Requested-With": APP_ID,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class SerializdClient:
    """Async client for the Serializd API."""

    def __init__(self, *, logger: ProviderLogger, email: str, password: str) -> None:
        """Construct the client with account credentials."""
        self.log = logger
        self._email = email
        self._password = password
        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None
        self.username: str | None = None
        self._auth_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session, recreated whenever the token changes."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                base_url=BASE_URL, headers=_default_headers(self._token)
            )
        return self._session

    async def close(self) -> None:
        """Close the underlying HTTP session if it is open."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def initialize(self) -> None:
        """Authenticate against the Serializd API."""
        await self._ensure_authenticated()

    async def _ensure_authenticated(self) -> None:
        """Log in if there is no cached token, guarded against concurrent re-login."""
        if self._token is not None:
            return
        async with self._auth_lock:
            if self._token is not None:
                return
            data = await self._make_request(
                "POST",
                "/api/login",
                json={"email": self._email, "password": self._password},
                authed=False,
            )
            parsed = msgspec.convert(data, type=LoginResponse)
            self._token = parsed.token
            self.username = parsed.username
            if self._session and not self._session.closed:
                await self._session.close()
            self._session = None

    async def _make_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        authed: bool = True,
        _retry_on_401: bool = True,
    ) -> dict[str, Any]:
        """Single real-network seam. Every client method routes through here."""
        if authed and self._token is None:
            await self._ensure_authenticated()

        session = await self._get_session()
        async with session.request(
            method, path, params=params, json=json
        ) as response:
            if response.status == 401 and authed and _retry_on_401:
                self.log.debug("Serializd session expired, re-authenticating")
                self._token = None
                await self._ensure_authenticated()
                return await self._make_request(
                    method,
                    path,
                    params=params,
                    json=json,
                    authed=authed,
                    _retry_on_401=False,
                )

            if response.status >= 400:
                text = await response.text()
                raise SerializdAPIError(
                    f"{method} {path} returned {response.status}: {text}"
                )

            if response.status == 204:
                return {}
            return await response.json()

    async def validate_token(self, token: str) -> bool:
        """Check whether a saved access token is still valid."""
        data = await self._make_request(
            "POST", "/api/validateauthtoken", json={"token": token}, authed=False
        )
        return msgspec.convert(data, type=ValidateTokenResponse).isValid

    async def search_shows(self, query: str) -> list[ShowSearchResult]:
        """Search Serializd's TMDB-backed show catalog."""
        data = await self._make_request(
            "GET", "/api/quick_log/search_shows", params={"search_query": query}
        )
        return msgspec.convert(data, type=ShowSearchResponse).results

    async def get_show(self, show_id: int) -> ShowDetail:
        """Fetch catalog details for a TMDB show id."""
        data = await self._make_request("GET", f"/api/show/{show_id}")
        return msgspec.convert(data, type=ShowDetail)

    async def get_season(self, show_id: int, season_number: int) -> SeasonDetail:
        """Fetch episode-level catalog details for one season."""
        data = await self._make_request(
            "GET", f"/api/show/{show_id}/season/{season_number}"
        )
        return msgspec.convert(data, type=SeasonDetail)

    async def log_seasons(self, show_id: int, season_ids: list[int]) -> None:
        """Mark the given seasons watched."""
        await self._make_request(
            "POST",
            "/api/watched_v2",
            json={"show_id": show_id, "season_ids": season_ids},
        )

    async def unlog_seasons(self, show_id: int, season_ids: list[int]) -> None:
        """Unmark the given seasons watched."""
        await self._make_request(
            "POST",
            "/api/watched/remove_v2",
            json={"show_id": show_id, "season_ids": season_ids},
        )

    async def log_show(self, show_id: int) -> None:
        """Mark every season of the show watched."""
        show = await self.get_show(show_id)
        await self.log_seasons(show_id, [s.seasonId for s in show.seasons])

    async def unlog_show(self, show_id: int) -> None:
        """Unmark every season of the show watched."""
        show = await self.get_show(show_id)
        await self.unlog_seasons(show_id, [s.seasonId for s in show.seasons])

    async def log_episodes(
        self, show_id: int, season_id: int, episode_numbers: list[int]
    ) -> EpisodeLogAddResponse:
        """Mark the given episode numbers of one season watched."""
        data = await self._make_request(
            "POST",
            "/api/episode_log/add",
            json={
                "show_id": show_id,
                "season_id": season_id,
                "episode_numbers": episode_numbers,
            },
        )
        return msgspec.convert(data, type=EpisodeLogAddResponse)

    async def unlog_episodes(
        self, show_id: int, season_id: int, episode_numbers: list[int]
    ) -> None:
        """Unmark the given episode numbers of one season watched."""
        await self._make_request(
            "POST",
            "/api/episode_log/remove",
            json={
                "show_id": show_id,
                "season_id": season_id,
                "episode_numbers": episode_numbers,
            },
        )

    async def add_review(
        self,
        show_id: int,
        *,
        season_id: int | None = None,
        rating: int | None = None,
        review_text: str = "",
        like: bool = False,
        add_to_diary: bool = True,
        episode_number: int | None = None,
    ) -> ReviewAddResponse:
        """Write a rating/review, optionally logging it to the diary.

        Verified live: the request body is snake_case, unlike the camelCase
        response it returns.
        """
        data = await self._make_request(
            "POST",
            "/api/show/reviews/add",
            json={
                "show_id": show_id,
                "season_id": season_id,
                "review_text": review_text,
                "rating": rating,
                "contains_spoiler": False,
                "backdate": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "is_log": add_to_diary,
                "is_rewatch": False,
                "episode_number": episode_number,
                "tags": [],
                "allows_comments": True,
                "like": like,
            },
        )
        return msgspec.convert(data, type=ReviewAddResponse)

    async def get_show_progress(self, show_id: int) -> NextEpisodeForUser | None:
        """Return the next unwatched episode, or None if fully watched/untouched."""
        data = await self._make_request(
            "GET", f"/mobile/page/show_v2_part_2/{show_id}"
        )
        return msgspec.convert(data, type=ShowProgressResponse).nextEpisodeForUser

    async def get_user_diary(self, username: str, page: int = 1) -> DiaryResponse:
        """Fetch one page of a user's diary (review-log history)."""
        data = await self._make_request(
            "GET",
            f"/api/user/{username}/diary",
            params={"page": page, "include_target": "ALL"},
        )
        return msgspec.convert(data, type=DiaryResponse)

    async def get_profile_stats(self, username: str) -> ProfileStatsResponse:
        """Fetch aggregate watched/watchlist/review counts for a user."""
        data = await self._make_request(
            "GET", f"/mobile/page/profile_v2_part_1/{username}"
        )
        return msgspec.convert(data, type=ProfileStatsResponse)
