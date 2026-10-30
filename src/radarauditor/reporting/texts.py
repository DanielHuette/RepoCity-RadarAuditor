"""Schema of texte.json, the report prose Claude Code writes for every analysed audit.

Context: Claude Code writes texte.json next to findings.json; `jobs push` uploads it to bucket raw
at `<audit_id>/texte.json`; the report reads it back. skills/radar-auswerter/SKILL.md describes the
same schema in German for Claude.
Input: JSON text.
Output: ReportTexts.
"""

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from radarauditor.errors import RenderError


class ReviewReply(BaseModel):
    """A suggested owner reply to one unanswered Google review."""

    model_config = ConfigDict(frozen=True)

    review_excerpt: str = Field(min_length=1)
    reply: str = Field(min_length=1)


class ReportTexts(BaseModel):
    """Report prose in German, addressing the customer with "Sie".

    Attributes:
        introduction: Opening paragraph on the cover page.
        summary: Three sentences under "So sichtbar sind Sie heute".
        forecast_note: How to read the forecast range; must call it an estimate.
        subscription_reason: Why ongoing care pays off for this customer.
        offer_text: The offer paragraph at the end of the sales report.
        talk_text: About five sentences the operator says to the customer word for word; shown
            only in the app, at the top of the Auswertung. None in texte.json files written before
            the field existed; `jobs push` rejects a new evaluation without it.
        before_after: Monthly runs only: what changed since the previous audit.
        post_suggestions: Ready-to-post Google profile posts.
        review_replies: Suggested replies to unanswered reviews.
    """

    model_config = ConfigDict(frozen=True)

    introduction: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    forecast_note: str = Field(min_length=1)
    subscription_reason: str = Field(min_length=1)
    offer_text: str = Field(min_length=1)
    talk_text: str | None = Field(default=None, min_length=1)
    before_after: str | None = None
    post_suggestions: list[str] = Field(default_factory=list)
    review_replies: list[ReviewReply] = Field(default_factory=list)


def parse_texts(content: str) -> ReportTexts:
    """Validate the content of texte.json.

    Args:
        content: JSON text.

    Returns:
        The parsed texts.

    Raises:
        RenderError: If the JSON is malformed or a required field is missing or empty.
    """
    try:
        return ReportTexts.model_validate_json(content)
    except ValidationError as exc:
        error = exc.errors()[0]
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        raise RenderError(f"texte.json invalid at {location}: {error['msg']}") from exc
