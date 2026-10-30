"""Exception hierarchy for the package.

Context: Every module raises these instead of bare built-ins so callers can react by type.
Input: Nothing.
Output: Exception classes.
"""

from enum import StrEnum
from uuid import UUID


class RadarError(Exception):
    """Base class for all package errors."""


class ConfigurationError(RadarError):
    """Settings are missing or malformed."""


class CollectionError(RadarError):
    """A data source could not be read."""


class TransientCollectionError(CollectionError):
    """A data source failed temporarily; the call may be retried."""


class SearchBlockedError(TransientCollectionError):
    """A search engine answered with a captcha, an "unusual traffic" page or HTTP 429."""

    def __init__(self, message: str, url: str = "") -> None:
        """Keep the address of the bot check next to the message."""
        super().__init__(message)
        self.url = url


class LoginWallError(TransientCollectionError):
    """A consumer website asks to sign in before it answers."""


class ProfileNotFoundError(CollectionError):
    """Google lists no business profile matching the name and address that were searched."""


class IdentificationError(CollectionError):
    """An audit started with only the website cannot go on: the business has no findable city.

    The message is German and shown in the app as it is; the job is not retried.
    """


class SourceUnavailableError(CollectionError):
    """A source may not be used now because of the app settings."""

    def __init__(self, source: str, message: str) -> None:
        """Keep the source name next to the message."""
        super().__init__(f"{source}: {message}")
        self.source = source


class SourceDisabledError(SourceUnavailableError):
    """The source is switched off in the app settings."""


class DailyCapReachedError(SourceUnavailableError):
    """The source has used up its daily cap."""


class MonthlyCostCapReachedError(SourceUnavailableError):
    """The source has spent its monthly cost cap."""


class AuthorizationError(CollectionError):
    """The operator's Google authorisation is missing, revoked or lacks a scope."""


class ScoringError(RadarError):
    """The check catalogue or collected data is inconsistent."""


class RenderError(RadarError):
    """A report or document could not be rendered."""


class StorageError(RadarError):
    """Supabase rejected a read or write; reason is the database's own message when it sent one."""

    def __init__(self, message: str, reason: str | None = None) -> None:
        """Keep the database's message, for example a German refusal of a CRM function."""
        super().__init__(message)
        self.reason = reason


class UnreadableMailError(StorageError):
    """A row crm.claim_mail_triage handed out does not have the agreed shape."""

    def __init__(self, message: str, mail_id: UUID | None) -> None:
        """Keep the id of the row, when it has one, so the row can be failed."""
        super().__init__(message)
        self.mail_id = mail_id


class HandoffError(RadarError):
    """Job hand-off files for Claude Code are missing or malformed."""


class ClaudeCodeFailure(StrEnum):
    """Why a headless Claude Code run gave no usable answer."""

    NOT_INSTALLED = "not_installed"
    NOT_LOGGED_IN = "not_logged_in"
    USAGE_LIMIT = "usage_limit"
    TIMEOUT = "timeout"
    FAILED = "failed"


class ClaudeCodeError(RadarError):
    """The headless Claude Code run is unavailable, not logged in, timed out or failed."""

    def __init__(self, message: str, failure: ClaudeCodeFailure = ClaudeCodeFailure.FAILED) -> None:
        """Keep the kind of failure next to the message."""
        super().__init__(message)
        self.failure = failure


class AnalysisUnavailableError(RadarError):
    """Claude Code gave no valid analysis; the message is the German reason shown in the app."""


class MailTriageError(RadarError):
    """Claude Code's answer about a mail fits neither result shape; the message is German."""


class AssistantError(RadarError):
    """A Cockpit assistant request cannot be done; its German message is shown in the Cockpit."""


class IntegrationError(RadarError):
    """A customer system (WordPress, Business Profile) rejected a change or is not reachable."""


class OptimizationError(RadarError):
    """An optimisation job lacks its order, quote or audit, or the drafts are invalid."""
