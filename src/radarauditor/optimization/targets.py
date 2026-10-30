"""Where a prepared change goes: the target text of optimization_changes.

Context: Claude Code writes targets into aenderungen.json, the worker validates them, the app shows
them and the apply step dispatches on them. The grammar, colon separated:
- wordpress:<page|post>:<id>:<title|content|excerpt|meta_title|meta_description|schema>
- business_profile:<name|description|phone|website>
- business_profile:review:<review id>:reply
- manual:<short label>  (no automatic way; the operator does it by hand)
Input: Target text.
Output: Target object or ValueError.
"""

import re
from dataclasses import dataclass
from enum import StrEnum


class System(StrEnum):
    """The system a change is made in."""

    WORDPRESS = "wordpress"
    BUSINESS_PROFILE = "business_profile"
    MANUAL = "manual"


class WordPressType(StrEnum):
    """WordPress content types the apply step can write."""

    PAGE = "page"
    POST = "post"


class WordPressField(StrEnum):
    """Fields of a page or post; meta_* go to Yoast or Rank Math, schema is JSON-LD."""

    TITLE = "title"
    CONTENT = "content"
    EXCERPT = "excerpt"
    META_TITLE = "meta_title"
    META_DESCRIPTION = "meta_description"
    SCHEMA = "schema"


class ProfileField(StrEnum):
    """Business Profile fields the apply step can write, plus review replies."""

    NAME = "name"
    DESCRIPTION = "description"
    PHONE = "phone"
    WEBSITE = "website"
    REVIEW_REPLY = "review_reply"


WORDPRESS_PATTERN = re.compile(
    r"^wordpress:(?P<type>page|post):(?P<id>[1-9][0-9]{0,11}):(?P<field>[a-z_]+)$"
)
PROFILE_PATTERN = re.compile(r"^business_profile:(?P<field>name|description|phone|website)$")
REVIEW_PATTERN = re.compile(r"^business_profile:review:(?P<id>[A-Za-z0-9_-]{1,200}):reply$")
MANUAL_PATTERN = re.compile(r"^manual:(?P<label>\S.{0,198})$")


@dataclass(frozen=True)
class Target:
    """A parsed target; fields that do not apply to the system are None."""

    system: System
    wordpress_type: WordPressType | None = None
    wordpress_id: int | None = None
    wordpress_field: WordPressField | None = None
    profile_field: ProfileField | None = None
    review_id: str | None = None
    label: str | None = None


def parse_target(text: str) -> Target:
    """Parse a target text.

    Args:
        text: Target as written in aenderungen.json or optimization_changes.target.

    Returns:
        The parsed target.

    Raises:
        ValueError: If the text matches no form of the grammar in the module docstring.
    """
    if match := WORDPRESS_PATTERN.match(text):
        try:
            field = WordPressField(match["field"])
        except ValueError as exc:
            raise ValueError(f"unknown WordPress field in target {text!r}") from exc
        return Target(
            system=System.WORDPRESS,
            wordpress_type=WordPressType(match["type"]),
            wordpress_id=int(match["id"]),
            wordpress_field=field,
        )
    if match := PROFILE_PATTERN.match(text):
        return Target(system=System.BUSINESS_PROFILE, profile_field=ProfileField(match["field"]))
    if match := REVIEW_PATTERN.match(text):
        return Target(
            system=System.BUSINESS_PROFILE,
            profile_field=ProfileField.REVIEW_REPLY,
            review_id=match["id"],
        )
    if match := MANUAL_PATTERN.match(text):
        return Target(system=System.MANUAL, label=match["label"].strip())
    raise ValueError(f"target {text!r} matches no known form")
