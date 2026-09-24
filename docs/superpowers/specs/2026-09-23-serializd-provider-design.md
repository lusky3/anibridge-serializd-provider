# anibridge-serializd-provider — Design Spec

Status: approved for implementation planning
Date: 2026-09-23

## 1. Goal

Build a new AniBridge list-provider plugin that syncs a user's watch progress
and ratings with [Serializd](https://www.serializd.com), a TV/movie tracking
site (TMDB-backed, "Letterboxd for TV"). The provider follows the pattern
established by `anibridge-anidb-provider`: an independent repo under
`github.com/lusky3` (the `anibridge` GitHub org does not include `lusky3` as
a member), published to PyPI via OIDC trusted publishing.

## 2. Why `anibridge-list-base`, not `anibridge-provider-base`

Two generations of provider contracts exist in the `anibridge` org:

- `anibridge-provider-base` — a newer, richer contract (`Provider`,
  `Capabilities`, `Role.SOURCE`/`TARGET`, mixins). **Not used by the core
  app** — `anibridge`'s `pyproject.toml` does not even depend on it, and its
  provider registry only loads `ListProvider` subclasses.
- `anibridge-list-base` — the `ListProvider` ABC. What MAL, AniList, Trakt,
  and Simkl actually subclass, and what the core app can load today.

**Decision: this provider targets `anibridge-list-base`'s `ListProvider`,
`ListEntry`, `ListMedia`, and `ListStatus`.** Targeting the newer contract
would produce a plugin the installed core app cannot load.

The real, current shape of these classes (verified against
`anibridge-list-base/src/anibridge/list/base.py`):

- `ListProvider(ABC)`: class vars `NAMESPACE: str`, `MAPPING_PROVIDERS:
  frozenset[str]`; abstract methods `delete_entry`, `resolve_mapping_descriptors`,
  `get_entry`, `update_entry`, `user`; optional overrides `initialize`,
  `backup_list`, `clear_cache`, `close`, `restore_list`, `search`,
  `get_entries_batch`, `update_entries_batch`.
- `ListEntry(ListEntity, ABC)`: abstract properties (all with setters)
  `progress: int | None`, `repeats: int | None`, `review: str | None`,
  `status: ListStatus | None`, `user_rating: int | None` (0–100 scale),
  `started_at: datetime | None`, `finished_at: datetime | None`, plus
  `media() -> ListMedia`.
- `ListMedia(ListEntity, ABC)`: abstract `media_type: ListMediaType`,
  `total_units: int | None`; optional `external_url`, `labels`,
  `poster_image`.
- `ListStatus(Enum)`: `COMPLETED`, `CURRENT`, `DROPPED`, `PAUSED`,
  `PLANNING`, `REPEATING`.

## 3. Serializd API — verified surface

`serializd-py` (the existing unofficial Python client) covers only login,
catalog reads, and episode/season watched-toggle writes. Live testing against
a real test account (browser network inspection + direct authenticated
`curl` calls, credentials never stored beyond the local `.env` used for
testing) confirmed a materially larger surface. All endpoints below were
exercised directly and returned real data; response shapes shown are
representative, not exhaustive.

**Auth:** `POST https://serializddesktop.onrender.com/api/login` with JSON
`{"email", "password"}` → `{"username", "token"}` (JWT). Every subsequent
request requires `Authorization: Bearer <token>` **and** the headers
`Origin: https://www.serializd.com`, `Referer: https://www.serializd.com`,
`X-Requested-With: serializd_vercel` — omitting the latter three silently
returns 401 even with a valid token. `POST /api/validateauthtoken` checks
token validity.

**IDs are TMDB-native throughout** — show IDs, season IDs (Serializd's own
`seasonId`, not TMDB's season number), and episode numbers all come directly
from TMDB. `MAPPING_PROVIDERS = frozenset({"tmdb_show"})`, and mapping
resolution is a pure local transform (no API round-trip needed, unlike
Simkl's `resolve_media_id` call).

**Catalog reads** (no per-user data, work unauthenticated):
- `GET /api/show/{tmdb_show_id}` — show details, season list (id, seasonId,
  episodeCount, seasonNumber).
- `GET /api/show/{tmdb_show_id}/season/{season_number}` — episode list
  (episodeId, episodeNumber, name, airDate, runtime). No watched flags here
  even when authenticated.

**Search** (undocumented in serializd-py):
- `GET /api/quick_log/search_shows?search_query={q}` — returns TMDB-id-keyed
  show results with season summaries.

**Watch-state writes** (documented in serializd-py, live-verified):
- `POST /api/watched_v2` / `POST /api/watched/remove_v2` —
  `{show_id, season_ids: [...]}`, season-level watched toggle.
- `POST /api/episode_log/add` / `POST /api/episode_log/remove` —
  `{show_id, season_id, episode_numbers: [...]}`, episode-level toggle.
  Response includes `nextEpisode` and `shouldMarkSeasonAsWatched` hints.
- These update the account's watched-episode count but do **not** appear in
  the diary/review log (see below) — they are a separate write path from
  reviews.

**Ratings/reviews write** (undocumented, live-verified — request body
captured directly from the browser, not inferred from the response echo):
- `POST /api/show/reviews/add` — request body is **snake_case** (the
  response echoes back camelCase field names — a real inconsistency between
  the two, not a typo): `{show_id, season_id (nullable), rating (0–10),
  review_text, contains_spoiler, backdate (ISO datetime), is_log,
  is_rewatch, episode_number (nullable), tags: [], allows_comments, like}`.
  Also creates a diary entry when `is_log: true`, and for `season_id: null`
  ("All Seasons") marks every episode of the show watched (equivalent to
  `log_show`).

**Per-user reads** (undocumented, live-verified — this is what makes
partial bidirectional sync possible):
- `GET /mobile/page/show_v2_part_2/{tmdb_show_id}` — includes
  `nextEpisodeForUser`: the next episode (with `episodeId`, `episodeNumber`,
  `episodeLog`) the user hasn't watched, or `null` once the show is fully
  watched. This is a **position pointer, not an enumerated watched-set** —
  see limitation below.
- `GET /api/user/{username}/diary?page=N&include_target=ALL` — paginated log
  of review-style actions (show/season-level logs with `rating`, `dateAdded`,
  `backdate`, `isLog`, `isRewatch`, `tags`). Does **not** include raw
  episode-level `episode_log` actions.
- `GET /mobile/page/profile_v2_part_1/{username}` — aggregate counts:
  `showWatchedCount`, `showWatchlistCount`, `reviewCount`,
  `totalEpisodesWatched`.
- `GET /mobile/page/show_v2_part_3/{tmdb_show_id}` — `watchedBy`/
  `watchlistedBy`/`currentlyWatchedBy`/`droppedBy`/`pausedBy` — but these are
  **friends/social lists**, not the authenticated user's own status, and are
  out of scope.

**Known limitation (document in README and code comments):** there is no
endpoint that enumerates *exactly which* episodes of a show are watched —
only the "next unwatched" pointer. Progress reconstruction therefore assumes
linear, in-order watching. A user who watches out of order will get an
approximate `progress` value. This is the same class of simplifying
assumption other AniBridge providers already make (e.g. AniDB's lossy status
round-trip) and will be documented, not silently hidden.

**Verified gap — `nextEpisodeForUser: null` is ambiguous.** Live-tested
against both a fully-watched show and a never-touched show: both return
`nextEpisodeForUser: null`. There is no field that distinguishes "completed"
from "never started." Also verified: the diary endpoint's `show_id`/`showId`
query parameters are silently ignored — it always returns the same
unfiltered page regardless, so there is no server-side per-show diary
filter. See §4 for the disambiguation strategy this forces and its residual
edge case.

## 4. Package design

Layout mirrors the MAL/Simkl providers:

```
src/anibridge/providers/list/serializd/
    __init__.py
    client.py     # async httpx client, one real-network seam
    config.py     # msgspec.Struct config (email/password or saved token)
    list.py       # ListProvider, ListEntry, ListMedia implementations
    models.py     # response/request msgspec structs
```

Toolchain (**corrected against live inspection of `anibridge-mal-provider`,
`anibridge-anilist-provider`, `anibridge-simkl-provider`, and
`anibridge-trakt-provider`'s actual `pyproject.toml`/source — every sibling
provider agrees, unanimously, on the choices below**): `uv` with the
`uv_build` backend (`[tool.uv.build-backend] module-name = "anibridge",
namespace = true` — required for this package to install correctly
alongside sibling providers under the shared `anibridge` namespace package),
Python 3.14, `ruff` (line-length 88, `select = ["B", "D", "DOC", "E", "F",
"I", "RUF", "SIM", "UP", "W"]`, Google docstring convention, `D` ignored
under `tests/**`), `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`,
`pythonpath = ["src"]`, `addopts = "--cov=src"`) + `pytest-cov`, `ty` for
type checking, `msgspec` for config/response structs. Dependencies:
`aiohttp>=3.13.3`, `anibridge-list-base>=0.2.0`, `anibridge-utils>=0.2.0`
(shared `ProviderLogger` protocol, `MappingDescriptor` type alias, and
`Limiter` rate-limiter helper), `msgspec>=0.21.1`.

**Client layer** (`client.py`): own async **`aiohttp.ClientSession`-based**
client (corrected from an earlier `httpx` assumption — every sibling
provider, including the one other project that also targets
`anibridge-list-base`'s newer sibling contract, uses `aiohttp`, never
`httpx`; matching this matters for consistency and for reusing
`anibridge-utils`' aiohttp-oriented `Limiter`), **not** a dependency on
`serializd-py` (whose client is sync `httpx.Client` and covers well under
half of what's needed). A single `_make_request` method is the one
real-network seam (same pattern as `MalClient._make_request` and the AniDB
provider's `_send_raw`), making the client fully unit-testable via a stub
`aiohttp.ClientSession`/response fixture. Public methods: `login`,
`validate_token`, `search_shows`, `get_show`, `get_season`,
`log_show`/`unlog_show`, `log_seasons`/`unlog_seasons`,
`log_episodes`/`unlog_episodes`, `add_review`, `get_show_progress`,
`get_user_diary`, `get_profile_stats`.

**`ListEntry` write pattern** (matching the verified `MalListEntry`
convention exactly, so `update_entry` only sends what actually changed): a
`_changed_fields: set[str]` populated by each property setter;
`ListProvider.update_entry` reads this set, sends only the corresponding
API calls, and clears it afterward. `ListMedia`/`ListEntry` subclasses set
`self._provider`/`self._key`/`self._title` directly in `__init__` (the
`ListEntity` base is a slotted dataclass; subclasses assign to those slots
rather than calling `super().__init__()`).

**Data mapping:**
- `ListMedia.total_units` = episode count summed from catalog season list.
- `ListEntry.progress`: if `nextEpisodeForUser` is present, `progress =
  episodeNumber - 1` (unambiguous — partial progress). If `nextEpisodeForUser`
  is `null`, disambiguate "completed" vs. "never touched" using a
  **per-session diary index**: since the diary endpoint's `show_id` filter
  is a no-op (verified — see §3), the provider paginates
  `/api/user/{username}/diary` once per session (lazily, on first need) and
  builds a local `set[show_id]` of shows with any diary entry, invalidated
  by `clear_cache()`. If the show's id is in that set, `progress` = total
  episode count (`COMPLETED`); otherwise the entry is treated as absent
  (`get_entry` returns `None`).
- **Residual documented edge case**: a show finished entirely through raw
  `episode_log` calls with zero diary/review entries ever created for it
  (e.g. a user who only ever uses per-episode "mark watched" checkboxes and
  never opens the review/log dialog) is indistinguishable from "never
  started" — the diary index has no record of it either way, and
  `nextEpisodeForUser` is `null` in both cases. This provider will report
  such a show as absent (not synced), a knowingly lossy limitation
  documented in the README rather than silently guessed at.
- `ListEntry.status` is a **read-only derived** property (per explicit scope
  decision — progress-only sync, not an independent status channel): `None`
  if absent per the above, `ListStatus.CURRENT` if partially watched,
  `ListStatus.COMPLETED` if fully watched per the diary-index check. The
  setter no-ops with a debug log — Serializd has no pause/drop concept for
  the account's own watch state, only the friends-list social view.
- `ListEntry.user_rating` maps Serializd's 0–10 scale to AniBridge's 0–100
  (`serializd_rating * 10`) via `add_review`.
- `ListEntry.started_at`/`finished_at` derive from diary `dateAdded`/
  `backdate` where an entry exists, else `None`.
- `ListEntry.repeats` derives from diary `isRewatch` flags where countable,
  else `0`.
- `ListEntry.review` maps to Serializd's `reviewText`.

**Error handling:** invalid credentials raise a clear `LoginError`-style
exception at `initialize()`. A 401 mid-session (expired token) triggers one
re-login attempt before surfacing the error, guarded so concurrent requests
don't each trigger their own re-login race (same `asyncio.Lock` pattern as
the AniDB provider's `_ensure_authenticated`).

## 5. Testing

pytest + pytest-asyncio, `conftest.py` fixture mocking the client's single
`_request` seam (canned JSON responses keyed by method+path, matching the
AniDB provider's `mock_udp_responses`-style fixture). Explicit test coverage
for the documented lossy behaviors: status derivation (None/CURRENT/
COMPLETED only — never PAUSED/DROPPED/PLANNING/REPEATING), progress
reconstruction from `nextEpisodeForUser`, the diary-index disambiguation for
`null`-pointer shows (both the completed-via-diary case and the
indistinguishable-from-absent edge case), and rating scale conversion.

## 6. CI / release workflow

`ci.yml`: lint (ruff check + format) and test jobs on PR + push to main,
following the Simkl-provider pattern (single workflow file, not a separate
`publish.yml`). A `publish` job runs on `v*` tag push, gated on lint+tests
passing, using OIDC trusted publishing (no stored PyPI token), GitHub
Environment `publish`.

**New: `release-gate` job**, required before `publish` runs, triggered on
the same `v*` tag push. Verifies two things before allowing a publish:

1. **Version match**: `pyproject.toml`'s `[project].version` equals the
   pushed tag with its leading `v` stripped (e.g. tag `v0.2.0` requires
   `version = "0.2.0"`). Fails the workflow otherwise — this proves the
   version was actually bumped for this release, not left stale from a
   previous tag.
2. **Changelog entry**: `CHANGELOG.md` (Keep a Changelog format) contains a
   `## [X.Y.Z]` heading matching that same version. Fails otherwise.

Both checks run as a small script step (no new action dependency) before the
`publish` job's `needs:` list, so a tag push with a stale version or missing
changelog entry never reaches `uv publish`. `CHANGELOG.md` is created as
part of this project with an `## [Unreleased]` section from day one.

## 7. Out of scope for v1

- Reverse-engineering the "friends" social endpoints (`show_v2_part_3`'s
  `*By` lists) — not the authenticated user's own state.
- Any attempt to recover exact per-episode watched booleans beyond the
  "next unwatched" pointer.
- Writing `PAUSED`/`DROPPED`/`PLANNING`/`REPEATING` status back to
  Serializd — no corresponding API exists for the account's own state.
