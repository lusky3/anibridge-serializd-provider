"""Tests for Serializd response models."""

import msgspec

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
    ValidateTokenResponse,
)


def test_login_response() -> None:
    data = {"username": "chaoszero112", "token": "eyJ0eXAiOiJKV1QiLCJh..."}
    parsed = msgspec.convert(data, type=LoginResponse)
    assert parsed.username == "chaoszero112"
    assert parsed.token.startswith("eyJ")


def test_validate_token_response() -> None:
    data = {"isValid": True, "username": "chaoszero112"}
    parsed = msgspec.convert(data, type=ValidateTokenResponse)
    assert parsed.isValid is True


def test_show_search_response_ignores_unmodeled_fields() -> None:
    data = {
        "results": [
            {
                "id": 1396,
                "name": "Breaking Bad",
                "bannerImage": "/anFx9aTOOYqgS3v7x3R84Kz67ly.jpg",
                "seasons": [
                    {
                        "id": 3572,
                        "seasonId": 3572,
                        "airDate": "2008-01-20",
                        "episodeCount": 7,
                        "name": "Season 1",
                        "overview": "...",
                        "posterPath": "/x.jpg",
                        "seasonNumber": 1,
                        "extraUnmodeledField": "should be ignored",
                    }
                ],
            }
        ]
    }
    parsed = msgspec.convert(data, type=ShowSearchResponse)
    assert parsed.results[0].id == 1396
    assert parsed.results[0].seasons[0].seasonId == 3572


def test_show_detail() -> None:
    data = {
        "id": 2316,
        "name": "The Office",
        "tagline": "A comedy for anyone whose boss is an idiot.",
        "summary": "...",
        "status": "Ended",
        "bannerImage": "/x.jpg",
        "premiereDate": "2005-03-24",
        "lastAirDate": "2013-05-16",
        "seasons": [
            {
                "id": 7240,
                "seasonId": 7240,
                "airDate": "2005-03-24",
                "episodeCount": 6,
                "name": "Season 1",
                "overview": "...",
                "posterPath": "/x.jpg",
                "seasonNumber": 1,
            }
        ],
        "numSeasons": 9,
        "numEpisodes": 201,
    }
    parsed = msgspec.convert(data, type=ShowDetail)
    assert parsed.id == 2316
    assert parsed.numEpisodes == 201
    assert parsed.seasons[0].episodeCount == 6


def test_season_detail_with_episodes() -> None:
    data = {
        "seasonId": 7240,
        "id": 7240,
        "seasonNumber": 1,
        "name": "Season 1",
        "overview": "...",
        "airDate": "2005-03-24",
        "posterPath": "/x.jpg",
        "episodes": [
            {
                "episodeId": 170135,
                "airDate": "2005-03-24",
                "episodeNumber": 1,
                "name": "Pilot",
                "overview": "...",
                "stillPath": "/x.jpg",
                "seasonNumber": 1,
                "runtime": 24,
            }
        ],
    }
    parsed = msgspec.convert(data, type=SeasonDetail)
    assert parsed.episodes[0].episodeNumber == 1


def test_episode_log_add_response() -> None:
    data = {
        "message": "Successfully added episode",
        "nextEpisode": None,
        "shouldMarkSeasonAsWatched": False,
    }
    parsed = msgspec.convert(data, type=EpisodeLogAddResponse)
    assert parsed.shouldMarkSeasonAsWatched is False


def test_review_add_response() -> None:
    data = {
        "dateAdded": "2026-09-23T16:07:21Z",
        "rating": 7,
        "like": False,
        "id": 89766253,
        "reviewText": "",
        "seasonId": None,
        "showId": 1396,
        "author": "chaoszero112",
    }
    parsed = msgspec.convert(data, type=ReviewAddResponse)
    assert parsed.rating == 7
    assert parsed.seasonId is None


def test_diary_response_multi_entry() -> None:
    data = {
        "reviews": [
            {
                "id": 89766253,
                "dateAdded": "2026-09-23T16:07:21Z",
                "backdate": "2026-09-23T16:06:57Z",
                "rating": 7,
                "reviewText": "",
                "seasonId": None,
                "showId": 1396,
                "isLog": True,
                "isRewatch": False,
                "episodeNumber": None,
            }
        ],
        "totalPages": 1,
        "totalReviews": None,
    }
    parsed = msgspec.convert(data, type=DiaryResponse)
    assert parsed.reviews[0].showId == 1396
    assert parsed.totalPages == 1


def test_profile_stats_response() -> None:
    data = {
        "details": {
            "username": "chaoszero112",
            "showWatchedCount": 1,
            "showWatchlistCount": 0,
            "reviewCount": 1,
            "totalEpisodesWatched": 62,
        }
    }
    parsed = msgspec.convert(data, type=ProfileStatsResponse)
    assert parsed.details.totalEpisodesWatched == 62


def test_show_progress_response_with_next_episode() -> None:
    data = {
        "nextEpisodeForUser": {
            "episodeId": 170134,
            "airDate": "2005-04-05",
            "episodeNumber": 3,
            "name": "Health Care",
            "seasonNumber": 1,
        }
    }
    parsed = msgspec.convert(data, type=ShowProgressResponse)
    assert parsed.nextEpisodeForUser is not None
    assert parsed.nextEpisodeForUser.episodeNumber == 3


def test_show_progress_response_null_next_episode() -> None:
    data = {"nextEpisodeForUser": None}
    parsed = msgspec.convert(data, type=ShowProgressResponse)
    assert parsed.nextEpisodeForUser is None
