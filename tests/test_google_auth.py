"""Tests for the Google OAuth flow and token refresh.

Context: Unit tests with respx for Google's token endpoint and a real loopback server. |
Input: Fixtures. | Output: Assertions.
"""

import base64
import hashlib
import logging
import threading
from datetime import UTC, datetime
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
import respx
from pydantic import SecretStr

from radarauditor.collectors import google_auth
from radarauditor.config import Settings
from radarauditor.errors import AuthorizationError, ConfigurationError, TransientCollectionError

pytestmark = pytest.mark.usefixtures("fast_retries")

REFRESH_TOKEN = "1//refresh-token-secret"
ACCESS_TOKEN = "ya29.access-token-secret"
STORED = {
    "refresh_token": REFRESH_TOKEN,
    "granted_scopes": list(google_auth.SCOPES),
    "obtained_at": "2026-10-16T08:00:00+00:00",
}


@pytest.fixture
def oauth_settings(collector_settings: Settings) -> Settings:
    """Settings with an OAuth desktop client."""
    return collector_settings.model_copy(
        update={
            "google_oauth_client_id": "client-id.apps.googleusercontent.com",
            "google_oauth_client_secret": SecretStr("client-secret"),
        }
    )


def _form(request: httpx.Request) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


def test_pkce_challenge_is_the_s256_of_the_verifier() -> None:
    pair = google_auth.new_pkce_pair()
    digest = hashlib.sha256(pair.verifier.encode()).digest()
    assert pair.challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert 43 <= len(pair.verifier) <= 128
    assert google_auth.new_pkce_pair().verifier != pair.verifier


def test_consent_url_asks_for_offline_access_to_all_scopes() -> None:
    url = google_auth.consent_url("client", "http://localhost:5000", "state-1", "challenge")
    query = parse_qs(urlsplit(url).query)
    assert url.startswith(google_auth.AUTH_URL)
    assert query["scope"] == [" ".join(google_auth.SCOPES)]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["redirect_uri"] == ["http://localhost:5000"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == ["state-1"]


def test_client_credentials_require_both_values(
    collector_settings: Settings, oauth_settings: Settings
) -> None:
    with pytest.raises(ConfigurationError, match="GOOGLE_OAUTH_CLIENT_ID"):
        google_auth.client_credentials(collector_settings)
    client_id, secret = google_auth.client_credentials(oauth_settings)
    assert client_id.startswith("client-id")
    assert secret.get_secret_value() == "client-secret"


@pytest.mark.parametrize(
    "text",
    [
        "http://localhost:8765/?state=s1&code=4/abc&scope=x",
        "/?code=4/abc&state=s1",
        "  4/abc  ",
        "code=4/abc",
    ],
)
def test_parse_redirect_accepts_address_path_or_code(text: str) -> None:
    assert google_auth.parse_redirect(text, "s1") == "4/abc"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "no authorisation code"),
        ("http://localhost:1/?error=access_denied&state=s1", "access_denied"),
        ("http://localhost:1/?code=4/abc&state=other", "another authorisation"),
        ("http://localhost:1/?state=s1", "contains no authorisation code"),
    ],
)
def test_parse_redirect_rejects_errors(text: str, message: str) -> None:
    with pytest.raises(AuthorizationError, match=message):
        google_auth.parse_redirect(text, "s1")


def test_loopback_receiver_returns_the_redirect_and_ignores_other_requests() -> None:
    with google_auth.LoopbackReceiver(timeout=5) as receiver:
        uri = receiver.redirect_uri.replace("localhost", "127.0.0.1")
        answers: list[httpx.Response] = []

        def browser() -> None:
            answers.append(httpx.get(f"{uri}/favicon.ico", trust_env=False))
            answers.append(httpx.get(f"{uri}/?code=4/abc&state=s1", trust_env=False))

        thread = threading.Thread(target=browser)
        thread.start()
        received = receiver.wait_for_redirect()
        thread.join()

    assert received == "/?code=4/abc&state=s1"
    assert [answer.status_code for answer in answers] == [404, 200]
    assert answers[1].content == google_auth.RECEIVED_PAGE
    assert receiver.redirect_uri.startswith("http://localhost:")


def test_loopback_receiver_times_out() -> None:
    with (
        google_auth.LoopbackReceiver(timeout=0.05) as receiver,
        pytest.raises(AuthorizationError, match="waiting time"),
    ):
        receiver.wait_for_redirect()


def test_loopback_receiver_reports_a_busy_port() -> None:
    with google_auth.LoopbackReceiver() as first:
        port = int(first.redirect_uri.rsplit(":", 1)[1])
        with pytest.raises(AuthorizationError, match="cannot listen"):
            google_auth.LoopbackReceiver(port)


@respx.mock
async def test_exchange_code_returns_the_grant(oauth_settings: Settings) -> None:
    route = respx.post(google_auth.TOKEN_URL).respond(
        200,
        json={
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
            "scope": " ".join(google_auth.SCOPES[:2]),
            "expires_in": 3599,
        },
    )

    grant = await google_auth.exchange_code(
        "4/abc", "http://localhost:5000", "verifier", oauth_settings
    )

    assert grant.refresh_token.get_secret_value() == REFRESH_TOKEN
    assert grant.missing_scopes() == [google_auth.SCOPES[2]]
    form = _form(route.calls[0].request)
    assert form == {
        "code": "4/abc",
        "client_id": "client-id.apps.googleusercontent.com",
        "client_secret": "client-secret",
        "redirect_uri": "http://localhost:5000",
        "grant_type": "authorization_code",
        "code_verifier": "verifier",
    }


@respx.mock
async def test_exchange_code_without_refresh_token_fails(oauth_settings: Settings) -> None:
    respx.post(google_auth.TOKEN_URL).respond(200, json={"access_token": ACCESS_TOKEN})
    with pytest.raises(AuthorizationError, match="no refresh token"):
        await google_auth.exchange_code("4/abc", "http://localhost:1", "v", oauth_settings)


@respx.mock
async def test_exchange_code_rejected_or_malformed(oauth_settings: Settings) -> None:
    respx.post(google_auth.TOKEN_URL).respond(400, json={"error": "invalid_grant"})
    with pytest.raises(AuthorizationError, match="invalid_grant"):
        await google_auth.exchange_code("4/abc", "http://localhost:1", "v", oauth_settings)
    respx.post(google_auth.TOKEN_URL).respond(200, text="kein json")
    with pytest.raises(AuthorizationError, match="malformed"):
        await google_auth.exchange_code("4/abc", "http://localhost:1", "v", oauth_settings)


def test_save_and_load_grant(fake_repositories: MagicMock) -> None:
    grant = google_auth.GoogleGrant(
        refresh_token=SecretStr(REFRESH_TOKEN),
        granted_scopes=list(google_auth.SCOPES),
        obtained_at=datetime(2026, 10, 16, 8, tzinfo=UTC),
    )
    google_auth.save_grant(fake_repositories, grant)
    fake_repositories.set_setting.assert_called_once_with("google_oauth", STORED)

    fake_repositories.settings_value.return_value = STORED
    assert google_auth.load_grant(fake_repositories) == grant
    fake_repositories.settings_value.assert_called_with("google_oauth")


def test_load_grant_missing_or_malformed(fake_repositories: MagicMock) -> None:
    assert google_auth.load_grant(fake_repositories) is None
    fake_repositories.settings_value.return_value = {"refresh_token": 5}
    with pytest.raises(AuthorizationError, match="malformed"):
        google_auth.load_grant(fake_repositories)


@respx.mock
async def test_access_token_refreshes_without_logging_tokens(
    fake_repositories: MagicMock, oauth_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    fake_repositories.settings_value.return_value = STORED
    route = respx.post(google_auth.TOKEN_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"access_token": ACCESS_TOKEN, "expires_in": 3599}),
        ]
    )
    caplog.set_level(logging.DEBUG)

    token = await google_auth.access_token(fake_repositories, oauth_settings)

    assert token == ACCESS_TOKEN
    assert route.call_count == 2
    form = _form(route.calls[1].request)
    assert form["grant_type"] == "refresh_token"
    assert form["refresh_token"] == REFRESH_TOKEN
    assert REFRESH_TOKEN not in caplog.text
    assert ACCESS_TOKEN not in caplog.text


async def test_access_token_needs_grant_and_client(
    fake_repositories: MagicMock, collector_settings: Settings, oauth_settings: Settings
) -> None:
    with pytest.raises(AuthorizationError, match="no Google authorisation stored"):
        await google_auth.access_token(fake_repositories, oauth_settings)
    fake_repositories.settings_value.return_value = STORED
    with pytest.raises(AuthorizationError, match="GOOGLE_OAUTH_CLIENT_ID"):
        await google_auth.access_token(fake_repositories, collector_settings)


@respx.mock
async def test_access_token_revoked_or_unreachable(
    fake_repositories: MagicMock, oauth_settings: Settings
) -> None:
    fake_repositories.settings_value.return_value = STORED
    respx.post(google_auth.TOKEN_URL).respond(400, json={"error": "invalid_grant"})
    with pytest.raises(AuthorizationError, match="invalid_grant"):
        await google_auth.access_token(fake_repositories, oauth_settings)
    respx.post(google_auth.TOKEN_URL).respond(500)
    with pytest.raises(TransientCollectionError):
        await google_auth.access_token(fake_repositories, oauth_settings)


def _token_answer() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
            "scope": " ".join(google_auth.SCOPES),
        },
    )


@respx.mock
def test_authorise_manually_stores_the_grant(
    fake_repositories: MagicMock, oauth_settings: Settings
) -> None:
    route = respx.post(google_auth.TOKEN_URL).mock(return_value=_token_answer())
    shown: list[str] = []

    def paste() -> str:
        state = parse_qs(urlsplit(shown[0]).query)["state"][0]
        return f"http://localhost:8765/?{urlencode({'state': state, 'code': '4/xyz'})}"

    grant = google_auth.authorise(
        oauth_settings,
        fake_repositories,
        manual=True,
        port=0,
        show=shown.append,
        read_line=paste,
    )

    assert grant.missing_scopes() == []
    assert parse_qs(urlsplit(shown[0]).query)["redirect_uri"] == ["http://localhost:8765"]
    assert _form(route.calls[0].request)["code"] == "4/xyz"
    stored = fake_repositories.set_setting.call_args.args
    assert stored[0] == "google_oauth"
    assert stored[1]["refresh_token"] == REFRESH_TOKEN
    assert all(REFRESH_TOKEN not in line for line in shown)


@respx.mock(assert_all_mocked=False)
def test_authorise_with_loopback_server(
    respx_mock: respx.MockRouter, fake_repositories: MagicMock, oauth_settings: Settings
) -> None:
    respx_mock.route(host="127.0.0.1").pass_through()
    route = respx_mock.post(google_auth.TOKEN_URL).mock(return_value=_token_answer())
    threads: list[threading.Thread] = []

    def show(line: str) -> None:
        if not line.startswith(google_auth.AUTH_URL):
            return
        query = parse_qs(urlsplit(line).query)
        redirect = query["redirect_uri"][0].replace("localhost", "127.0.0.1")
        parameters = urlencode({"code": "4/loop", "state": query["state"][0]})
        thread = threading.Thread(
            target=lambda: httpx.get(f"{redirect}/?{parameters}", trust_env=False)
        )
        thread.start()
        threads.append(thread)

    def no_input() -> str:
        raise AssertionError("loopback mode reads no input")

    google_auth.authorise(
        oauth_settings, fake_repositories, manual=False, port=0, show=show, read_line=no_input
    )
    for thread in threads:
        thread.join()

    form = _form(route.calls[0].request)
    assert form["code"] == "4/loop"
    assert form["redirect_uri"].startswith("http://localhost:")
    fake_repositories.set_setting.assert_called_once()


def test_authorise_needs_the_client(
    fake_repositories: MagicMock, collector_settings: Settings
) -> None:
    with pytest.raises(ConfigurationError):
        google_auth.authorise(
            collector_settings,
            fake_repositories,
            manual=True,
            port=0,
            show=lambda line: None,
            read_line=lambda: "",
        )
