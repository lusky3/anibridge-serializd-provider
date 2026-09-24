"""Tests for the Serializd API client."""

import asyncio
from logging import getLogger
from typing import Any, cast

import pytest
from anibridge.utils.types import ProviderLogger

from anibridge.providers.list.serializd.client import (
    SerializdAPIError,
    SerializdClient,
)


class _StubResponse:
    """Minimal aiohttp-like response context manager."""

    def __init__(
        self, *, status: int, payload: dict[str, Any] | None = None, text: str = ""
    ) -> None:
        self.status = status
        self._payload = payload or {}
        self._text = text

    async def __aenter__(self) -> _StubResponse:
        # Force a genuine cooperative task-switch point here. Without this,
        # none of the stub coroutines in this module ever truly suspend, so
        # asyncio.gather's tasks never actually interleave - the first task
        # runs to full completion before the second one starts at all, and
        # the concurrency test below would pass even with a broken auth
        # lock. This is the standard idiom for forcing a real yield.
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False

    async def json(self) -> dict[str, Any]:
        return self._payload

    async def text(self) -> str:
        return self._text


class _StubSession:
    """Session wrapper that serves predefined request responses in order."""

    def __init__(self, responses: list[_StubResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def request(self, method: str, url: str, **kwargs: Any) -> _StubResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._responses.pop(0)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture()
def client() -> SerializdClient:
    return SerializdClient(
        logger=cast(ProviderLogger, getLogger("tests.serializd.client")),
        email="user@example.com",
        password="hunter2",
    )


def _install_stub_session(
    client: SerializdClient, responses: list[_StubResponse]
) -> _StubSession:
    stub = _StubSession(responses)

    async def _get_session() -> _StubSession:
        return stub

    client._get_session = _get_session  # ty: ignore[invalid-assignment]
    return stub


@pytest.mark.asyncio
async def test_login_success_sets_token_and_username(client: SerializdClient) -> None:
    _install_stub_session(
        client,
        [
            _StubResponse(
                status=200, payload={"username": "chaoszero112", "token": "tok"}
            )
        ],
    )
    await client.initialize()
    assert client.username == "chaoszero112"
    assert client._token == "tok"


@pytest.mark.asyncio
async def test_login_failure_raises_serializd_api_error(
    client: SerializdClient,
) -> None:
    _install_stub_session(
        client,
        [_StubResponse(status=401, text='{"message": "Unauthorized"}')],
    )
    with pytest.raises(SerializdAPIError):
        await client.initialize()


@pytest.mark.asyncio
async def test_expired_token_triggers_one_relogin_and_retry(
    client: SerializdClient,
) -> None:
    client._token = "stale-token"
    client.username = "chaoszero112"
    stub = _install_stub_session(
        client,
        [
            _StubResponse(status=401, text="expired"),
            _StubResponse(
                status=200, payload={"username": "chaoszero112", "token": "fresh-token"}
            ),
            _StubResponse(
                status=200,
                payload={"id": 1396, "name": "Breaking Bad", "seasons": []},
            ),
        ],
    )
    show = await client.get_show(1396)
    assert show.id == 1396
    assert client._token == "fresh-token"
    assert len(stub.calls) == 3


class _DynamicStubSession:
    """Session that responds based on current auth state, not a fixed queue.

    Used only for the concurrency test below: a fixed response queue would
    assume a specific interleaving order between the two gathered
    coroutines, which asyncio does not guarantee (trivial stub coroutines
    with no real suspension point may run one fully to completion before
    the other starts at all, or may interleave - both are valid). Reacting
    to state instead of a queue makes the test correct regardless of the
    actual interleaving, and lets it assert the real invariant: at most one
    `/api/login` call happens no matter how the two callers interleave.
    """

    def __init__(self) -> None:
        self.login_count = 0
        self._authenticated = False
        self.closed = False

    def request(self, method: str, url: str, **kwargs: Any) -> _StubResponse:
        if url == "/api/login":
            self.login_count += 1
            self._authenticated = True
            return _StubResponse(
                status=200,
                payload={"username": "chaoszero112", "token": "fresh-token"},
            )
        if not self._authenticated:
            return _StubResponse(status=401, text="expired")
        show_id = int(url.rsplit("/", 1)[-1])
        return _StubResponse(
            status=200,
            payload={"id": show_id, "name": f"Show {show_id}", "seasons": []},
        )

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_concurrent_requests_during_expiry_relogin_only_once(
    client: SerializdClient,
) -> None:
    client._token = "stale-token"
    client.username = "chaoszero112"
    stub = _DynamicStubSession()

    async def _get_session() -> _DynamicStubSession:
        return stub

    client._get_session = _get_session  # ty: ignore[invalid-assignment]

    results = await asyncio.gather(client.get_show(1), client.get_show(2))
    assert {r.id for r in results} == {1, 2}
    assert client._token == "fresh-token"
    assert stub.login_count == 1


@pytest.mark.asyncio
async def test_late_401_does_not_clobber_a_token_already_refreshed(
    client: SerializdClient,
) -> None:
    """A 401 for a request sent with an already-superseded token must not
    wipe out a token another request already refreshed, and must not
    trigger a second login.
    """
    client._token = "stale-token"
    client.username = "chaoszero112"
    stub = _install_stub_session(client, [])
    # Simulate another request having already refreshed the token by the
    # time this request's 401 is handled: call the retry path directly with
    # a stale_token that no longer matches client._token. No HTTP call
    # should happen at all - the mismatch alone is enough to skip re-login.
    client._token = "fresh-token"
    await client._refresh_after_401(stale_token="stale-token")

    assert client._token == "fresh-token"
    assert len(stub.calls) == 0


def test_default_headers_always_present() -> None:
    from anibridge.providers.list.serializd.client import _default_headers

    headers = _default_headers(None)
    assert headers["Origin"] == "https://www.serializd.com"
    assert headers["Referer"] == "https://www.serializd.com"
    assert headers["X-Requested-With"] == "serializd_vercel"
    assert "Authorization" not in headers

    headers_with_token = _default_headers("tok")
    assert headers_with_token["Authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_error_response_raises_with_status_and_body(
    client: SerializdClient,
) -> None:
    client._token = "tok"
    client.username = "u"
    _install_stub_session(
        client, [_StubResponse(status=500, text="Internal Server Error")]
    )
    with pytest.raises(SerializdAPIError, match="500"):
        await client.get_show(1)
