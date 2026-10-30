"""Authorise the operator's Google account once and hand out access tokens on demand.

Context: Audits with customer access read Business Profile, Search Console and Google Analytics
of customers who added the operator's Google account. The installed-app OAuth 2.0 flow with PKCE
runs once through `radarauditor google auth`; the refresh token is kept in the settings table
under key google_oauth. Tokens and authorisation codes are never logged or printed.
Input: OAuth client id and secret from settings, the authorisation code from the browser.
Output: Stored GoogleGrant, short-lived access tokens.
"""

import asyncio
import base64
import hashlib
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import TracebackType
from typing import Self
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from radarauditor.collectors.transport import send
from radarauditor.config import Settings
from radarauditor.db.repositories import Repositories
from radarauditor.errors import (
    AuthorizationError,
    CollectionError,
    ConfigurationError,
    TransientCollectionError,
)

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = (
    "https://www.googleapis.com/auth/business.manage",
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
)
SETTINGS_KEY = "google_oauth"
REQUEST_TIMEOUT_SECONDS = 20
LOOPBACK_HOST = "127.0.0.1"
REDIRECT_HOST = "localhost"
MANUAL_PORT = 8765
CONSENT_TIMEOUT_SECONDS = 300.0
STATE_BYTES = 24
VERIFIER_BYTES = 48
RECEIVED_PAGE = b"RadarAuditor received the Google authorisation. You can close this tab."


class GoogleGrant(BaseModel):
    """The stored result of the consent flow."""

    refresh_token: SecretStr
    granted_scopes: list[str]
    obtained_at: datetime

    def stored_value(self) -> dict[str, object]:
        """JSON value for the settings table, with the refresh token in clear text."""
        return {
            "refresh_token": self.refresh_token.get_secret_value(),
            "granted_scopes": self.granted_scopes,
            "obtained_at": self.obtained_at.isoformat(),
        }

    def missing_scopes(self) -> list[str]:
        """Scopes RadarAuditor asked for that the operator did not grant."""
        return [scope for scope in SCOPES if scope not in self.granted_scopes]


@dataclass(frozen=True)
class PkcePair:
    """Code verifier and its S256 challenge for one consent flow."""

    verifier: str
    challenge: str


class _TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    scope: str = ""


def new_pkce_pair() -> PkcePair:
    """Create a random code verifier and its S256 challenge.

    Returns:
        Verifier (43-128 URL-safe characters) and challenge.
    """
    verifier = secrets.token_urlsafe(VERIFIER_BYTES)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return PkcePair(verifier=verifier, challenge=challenge)


def client_credentials(settings: Settings) -> tuple[str, SecretStr]:
    """Return the OAuth client id and secret.

    Args:
        settings: Runtime settings.

    Returns:
        Client id and client secret.

    Raises:
        ConfigurationError: If either value is missing.
    """
    if not settings.google_oauth_client_id or settings.google_oauth_client_secret is None:
        raise ConfigurationError(
            "GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET must be set for Google access"
        )
    return settings.google_oauth_client_id, settings.google_oauth_client_secret


def consent_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    """Build the Google consent address for all three read scopes with offline access.

    Args:
        client_id: OAuth desktop client id.
        redirect_uri: Loopback address Google sends the browser back to.
        state: Random value echoed back by Google.
        challenge: PKCE S256 challenge.

    Returns:
        Address to open in the browser.
    """
    parameters = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(parameters)}"


def parse_redirect(text: str, state: str) -> str:
    """Extract the authorisation code from a redirect address, its path, or a bare code.

    Args:
        text: Full redirect address, the path with query string, or the code alone.
        state: The state sent with the consent address; checked when the text carries one.

    Returns:
        The authorisation code.

    Raises:
        AuthorizationError: If Google reported an error, the state differs or no code is found.
    """
    value = text.strip()
    if not value:
        raise AuthorizationError("no authorisation code was entered")
    if "?" not in value and "=" not in value:
        return value
    query = parse_qs(urlsplit(value).query if "?" in value else value)
    if "error" in query:
        raise AuthorizationError(f"Google refused the authorisation: {query['error'][0]}")
    returned_state = query.get("state", [None])[0]
    if returned_state is not None and returned_state != state:
        raise AuthorizationError("the redirect belongs to another authorisation attempt")
    codes = query.get("code")
    if not codes or not codes[0]:
        raise AuthorizationError("the redirect address contains no authorisation code")
    return codes[0]


class _RedirectServer(HTTPServer):
    received: str | None = None


class _RedirectHandler(BaseHTTPRequestHandler):
    server: _RedirectServer

    def do_GET(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        if "code" not in query and "error" not in query:
            self.send_response(404)
            self.end_headers()
            return
        self.server.received = self.path
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(RECEIVED_PAGE)))
        self.end_headers()
        self.wfile.write(RECEIVED_PAGE)

    def log_message(self, format: str, *args: object) -> None:
        """Stay silent: the default access log would print the authorisation code."""


class LoopbackReceiver:
    """One-shot local HTTP server that receives Google's redirect after consent."""

    def __init__(self, port: int = 0, timeout: float = CONSENT_TIMEOUT_SECONDS) -> None:
        """Bind the server; port 0 lets the system choose a free port.

        Args:
            port: Local port to listen on.
            timeout: Seconds to wait for the redirect.

        Raises:
            AuthorizationError: If the port cannot be bound.
        """
        try:
            self._server = _RedirectServer((LOOPBACK_HOST, port), _RedirectHandler)
        except OSError as error:
            raise AuthorizationError(f"cannot listen on local port {port}: {error}") from error
        self._timeout = timeout

    @property
    def redirect_uri(self) -> str:
        """Loopback address to register as redirect target."""
        return f"http://{REDIRECT_HOST}:{self._server.server_address[1]}"

    def wait_for_redirect(self) -> str:
        """Serve requests until the redirect with a code or an error arrives.

        Returns:
            Path and query string of the redirect.

        Raises:
            AuthorizationError: If no redirect arrives within the timeout.
        """
        deadline = time.monotonic() + self._timeout
        while self._server.received is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AuthorizationError("no answer from Google within the waiting time")
            self._server.timeout = remaining
            self._server.handle_request()
        return self._server.received

    def __enter__(self) -> Self:
        """Return the bound receiver."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the listening socket."""
        self._server.server_close()


async def _token_request(form: dict[str, str]) -> _TokenResponse:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        try:
            response = await send(client, "POST", TOKEN_URL, source="Google OAuth", data=form)
        except TransientCollectionError:
            raise
        except CollectionError as error:
            raise AuthorizationError(str(error)) from error
    try:
        return _TokenResponse.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise AuthorizationError("Google OAuth answer is malformed") from error


async def exchange_code(
    code: str, redirect_uri: str, verifier: str, settings: Settings
) -> GoogleGrant:
    """Trade the authorisation code for a refresh token.

    Args:
        code: Authorisation code from the redirect.
        redirect_uri: The redirect address used in the consent address.
        verifier: PKCE code verifier of this flow.
        settings: Settings with the OAuth client.

    Returns:
        The grant with refresh token and granted scopes.

    Raises:
        ConfigurationError: If the OAuth client is not configured.
        AuthorizationError: If Google rejects the code or returns no refresh token.
        TransientCollectionError: If Google is unreachable after retries.
    """
    client_id, client_secret = client_credentials(settings)
    answer = await _token_request(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret.get_secret_value(),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        }
    )
    if answer.refresh_token is None:
        raise AuthorizationError(
            "Google returned no refresh token; remove RadarAuditor under "
            "myaccount.google.com/permissions and authorise again"
        )
    return GoogleGrant(
        refresh_token=SecretStr(answer.refresh_token),
        granted_scopes=answer.scope.split(),
        obtained_at=datetime.now(UTC),
    )


def save_grant(repositories: Repositories, grant: GoogleGrant) -> None:
    """Store the grant in the settings table, replacing an earlier one.

    Raises:
        StorageError: If Supabase rejects the write.
    """
    repositories.set_setting(SETTINGS_KEY, grant.stored_value())
    logger.info("Stored Google authorisation with %d scopes", len(grant.granted_scopes))


def load_grant(repositories: Repositories) -> GoogleGrant | None:
    """Read the stored grant.

    Returns:
        The grant, or None if the consent flow never ran.

    Raises:
        AuthorizationError: If the stored value is malformed.
        StorageError: If Supabase cannot be read.
    """
    value = repositories.settings_value(SETTINGS_KEY)
    if value is None:
        return None
    try:
        return GoogleGrant.model_validate(value)
    except ValidationError as error:
        raise AuthorizationError(
            "stored Google authorisation is malformed; run radarauditor google auth again"
        ) from error


async def access_token(repositories: Repositories, settings: Settings) -> str:
    """Refresh a short-lived access token from the stored refresh token.

    Args:
        repositories: Database access for the stored grant.
        settings: Settings with the OAuth client.

    Returns:
        Bearer token valid for about an hour.

    Raises:
        AuthorizationError: If no grant is stored, the client is not configured or Google
            rejects the refresh token.
        TransientCollectionError: If Google is unreachable after retries.
        StorageError: If the settings table cannot be read.
    """
    grant = load_grant(repositories)
    if grant is None:
        raise AuthorizationError("no Google authorisation stored; run radarauditor google auth")
    try:
        client_id, client_secret = client_credentials(settings)
    except ConfigurationError as error:
        raise AuthorizationError(str(error)) from error
    answer = await _token_request(
        {
            "client_id": client_id,
            "client_secret": client_secret.get_secret_value(),
            "refresh_token": grant.refresh_token.get_secret_value(),
            "grant_type": "refresh_token",
        }
    )
    return answer.access_token


def authorise(
    settings: Settings,
    repositories: Repositories,
    *,
    manual: bool,
    port: int,
    show: Callable[[str], None],
    read_line: Callable[[], str],
) -> GoogleGrant:
    """Run the consent flow and store the resulting grant.

    Args:
        settings: Settings with the OAuth client.
        repositories: Database access to store the grant.
        manual: Read the redirect address from read_line instead of a local server.
        port: Local port; 0 picks a free one for the server and MANUAL_PORT for manual mode.
        show: Writes one line for the operator.
        read_line: Reads one line typed by the operator (manual mode only).

    Returns:
        The stored grant.

    Raises:
        ConfigurationError: If the OAuth client is not configured.
        AuthorizationError: If consent fails, times out or the code is rejected.
        TransientCollectionError: If Google is unreachable after retries.
        StorageError: If the grant cannot be stored.
    """
    client_id, _ = client_credentials(settings)
    pair = new_pkce_pair()
    state = secrets.token_urlsafe(STATE_BYTES)
    if manual:
        redirect_uri = f"http://{REDIRECT_HOST}:{port or MANUAL_PORT}"
        show(consent_url(client_id, redirect_uri, state, pair.challenge))
        show("Open the address above, approve, then paste the address the browser ends on:")
        code = parse_redirect(read_line(), state)
    else:
        with LoopbackReceiver(port) as receiver:
            redirect_uri = receiver.redirect_uri
            show(consent_url(client_id, redirect_uri, state, pair.challenge))
            show(f"Open the address above and approve; waiting for {redirect_uri} ...")
            code = parse_redirect(receiver.wait_for_redirect(), state)
    grant = asyncio.run(exchange_code(code, redirect_uri, pair.verifier, settings))
    save_grant(repositories, grant)
    return grant
