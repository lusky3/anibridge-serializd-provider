"""Response models for the Serializd API."""

import msgspec

__all__ = [
    "DiaryEntry",
    "DiaryResponse",
    "EpisodeDetail",
    "EpisodeLogAddResponse",
    "LoginResponse",
    "NextEpisodeForUser",
    "ProfileStatsDetails",
    "ProfileStatsResponse",
    "ReviewAddResponse",
    "SeasonDetail",
    "SeasonSummary",
    "ShowDetail",
    "ShowProgressResponse",
    "ShowSearchResponse",
    "ShowSearchResult",
    "ValidateTokenResponse",
]


class LoginResponse(msgspec.Struct):
    """Response body from `POST /api/login`."""

    username: str
    token: str


class ValidateTokenResponse(msgspec.Struct):
    """Response body from `POST /api/validateauthtoken`."""

    isValid: bool
    username: str


class SeasonSummary(msgspec.Struct):
    """A season as it appears nested in show search/detail responses."""

    id: int
    seasonId: int
    episodeCount: int
    name: str
    seasonNumber: int
    airDate: str | None = None
    overview: str = ""
    posterPath: str | None = None


class ShowSearchResult(msgspec.Struct):
    """A single result from `GET /api/quick_log/search_shows`."""

    id: int
    name: str
    seasons: list[SeasonSummary]
    bannerImage: str | None = None


class ShowSearchResponse(msgspec.Struct):
    """Response body from `GET /api/quick_log/search_shows`."""

    results: list[ShowSearchResult]


class ShowDetail(msgspec.Struct):
    """Response body from `GET /api/show/{id}`."""

    id: int
    name: str
    seasons: list[SeasonSummary]
    tagline: str = ""
    summary: str = ""
    status: str = ""
    bannerImage: str | None = None
    premiereDate: str | None = None
    lastAirDate: str | None = None
    numSeasons: int = 0
    numEpisodes: int = 0


class EpisodeDetail(msgspec.Struct):
    """A single episode as returned by `GET /api/show/{id}/season/{n}`."""

    episodeId: int
    episodeNumber: int
    name: str
    seasonNumber: int
    airDate: str | None = None
    overview: str = ""
    stillPath: str | None = None
    runtime: int | None = None


class SeasonDetail(msgspec.Struct):
    """Response body from `GET /api/show/{id}/season/{n}`."""

    seasonId: int
    id: int
    seasonNumber: int
    name: str
    episodes: list[EpisodeDetail]
    overview: str = ""
    airDate: str | None = None
    posterPath: str | None = None


class EpisodeLogAddResponse(msgspec.Struct):
    """Response body from `POST /api/episode_log/add`."""

    message: str
    nextEpisode: int | None = None
    shouldMarkSeasonAsWatched: bool = False


class ReviewAddResponse(msgspec.Struct):
    """Response body from `POST /api/show/reviews/add`."""

    id: int
    showId: int
    dateAdded: str
    rating: int | None = None
    seasonId: int | None = None
    reviewText: str = ""
    like: bool = False


class DiaryEntry(msgspec.Struct):
    """A single entry from `GET /api/user/{username}/diary`."""

    id: int
    showId: int
    dateAdded: str
    backdate: str | None = None
    rating: int | None = None
    reviewText: str = ""
    seasonId: int | None = None
    isLog: bool = False
    isRewatch: bool = False
    episodeNumber: int | None = None


class DiaryResponse(msgspec.Struct):
    """Response body from `GET /api/user/{username}/diary`."""

    reviews: list[DiaryEntry]
    totalPages: int = 1
    totalReviews: int | None = None


class ProfileStatsDetails(msgspec.Struct):
    """The `details` object in the profile stats response."""

    username: str
    showWatchedCount: int = 0
    showWatchlistCount: int = 0
    reviewCount: int = 0
    totalEpisodesWatched: int | None = None


class ProfileStatsResponse(msgspec.Struct):
    """Response body from `GET /mobile/page/profile_v2_part_1/{username}`."""

    details: ProfileStatsDetails


class NextEpisodeForUser(msgspec.Struct):
    """The `nextEpisodeForUser` object in the show progress response."""

    episodeId: int
    episodeNumber: int
    name: str
    seasonNumber: int
    airDate: str | None = None


class ShowProgressResponse(msgspec.Struct):
    """Response body from `GET /mobile/page/show_v2_part_2/{show_id}`."""

    nextEpisodeForUser: NextEpisodeForUser | None = None
