"""Industry groups a customer belongs to, with labels and the mapping of older stored values.

Context: config/branchen.yaml is the single source; the forecast, the assistant questions, the tax
export and the app read their industries from it. Customers saved before the groups existed carry
values such as "Handwerk" or "medizin"; resolve maps them onto a group. An audit started with only
the website address finds its group from the Google profile categories (by_google_types) or, failing
that, from the words of the business name and the category text (by_words).
Input: Path of branchen.yaml.
Output: Validated IndustryCatalogue.
"""

import re
from collections.abc import Iterable, Sequence
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from radarauditor.errors import ConfigurationError

DEFAULT_INDUSTRIES_PATH = Path("config/branchen.yaml")
INDUSTRIES_FILE = "branchen.yaml"
ID_PATTERN = r"^[a-z][a-z_]*$"
SHORT_WORD = 5


def lookup_key(value: str) -> str:
    """Form under which ids, labels and legacy values are compared."""
    return value.strip().lower()


class IndustryGroup(BaseModel):
    """One group, for example haustechnik with electricians, plumbers and heating engineers."""

    id: str = Field(pattern=ID_PATTERN)
    label: str = Field(min_length=1)
    examples: list[str]
    services: list[str] = Field(min_length=1)
    question_term: str = Field(min_length=1)
    google_types: list[str] = Field(default_factory=list)

    @property
    def words(self) -> list[str]:
        """Label parts, examples and services: words a business name of this group may contain.

        The label "Fitness, Yoga, Sport" gives "Fitness", "Yoga" and "Sport".
        """
        label_parts = re.split(r",|\bund\b", self.label)
        items = (item.strip() for item in [*label_parts, *self.examples, *self.services])
        return list(dict.fromkeys(item for item in items if item))


class IndustryCatalogue(BaseModel):
    """Content of config/branchen.yaml."""

    groups: list[IndustryGroup] = Field(min_length=1)
    legacy: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> "IndustryCatalogue":
        ids = [group.id for group in self.groups]
        if len(set(ids)) != len(ids):
            raise ValueError("industry ids must be unique")
        unnormalised = sorted(key for key in self.legacy if key != lookup_key(key))
        if unnormalised:
            raise ValueError(f"legacy values must be lower case: {', '.join(unnormalised)}")
        unknown = sorted(target for target in self.legacy.values() if target not in ids)
        if unknown:
            raise ValueError(f"legacy values point to unknown groups: {', '.join(unknown)}")
        owners: dict[str, list[str]] = {}
        for group in self.groups:
            for google_type in group.google_types:
                owners.setdefault(google_type, []).append(group.id)
        shared = sorted(google_type for google_type, groups in owners.items() if len(groups) > 1)
        if shared:
            raise ValueError(f"Google types in more than one group: {', '.join(shared)}")
        return self

    @property
    def ids(self) -> frozenset[str]:
        """Ids of every group."""
        return frozenset(group.id for group in self.groups)

    def resolve(self, value: str) -> IndustryGroup | None:
        """Group of a stored industry: its id, its label or an older value, ignoring case.

        Args:
            value: Industry as stored at the customer.

        Returns:
            The matching group, None for values outside every group.
        """
        key = lookup_key(value)
        key = self.legacy.get(key, key)
        for group in self.groups:
            if key in (group.id, lookup_key(group.label)):
                return group
        return None

    def by_google_types(self, types: Sequence[str]) -> IndustryGroup | None:
        """Group of the first Google profile category that belongs to a group.

        Args:
            types: Categories of the Places API (New) in their order, the primary one first.

        Returns:
            The group, None when no category belongs to any group.
        """
        for google_type in types:
            for group in self.groups:
                if google_type in group.google_types:
                    return group
        return None

    def by_words(self, texts: Iterable[str]) -> IndustryGroup | None:
        """Group whose label, example or service starts a word in the texts.

        "Dachdeckerei Hollenbeck" contains "Dachdecker" (bauhandwerk); words shorter than
        SHORT_WORD letters count only as whole words, so "Barbara" is no "Bar". When several match,
        the longest matching word decides ("Barbier" beats "Bar"); a tie between groups gives no
        group, so a name that fits two trades is not guessed.

        Args:
            texts: Business name and Google category text.

        Returns:
            The single best group, or None.
        """
        given = [text for text in texts if text.strip()]
        best: dict[str, int] = {}
        for group in self.groups:
            for word in (item.strip() for item in group.words):
                if word and any(_word_pattern(word).search(text) for text in given):
                    best[group.id] = max(best.get(group.id, 0), len(word))
        if not best:
            return None
        longest = max(best.values())
        winners = [group for group in self.groups if best.get(group.id) == longest]
        return winners[0] if len(winners) == 1 else None

    def label(self, value: str) -> str:
        """German label of a stored industry; values outside every group are shown as stored."""
        group = self.resolve(value)
        return group.label if group else value.strip()

    def question_term(self, value: str) -> str:
        """Noun for the assistant questions; values outside every group are used as stored."""
        group = self.resolve(value)
        return group.question_term if group else value.strip()


def _word_pattern(word: str) -> re.Pattern[str]:
    """A word at the start of a word; short words must also end there."""
    end = r"(?!\w)" if len(word) < SHORT_WORD else ""
    return re.compile(rf"(?<!\w){re.escape(word)}{end}", re.IGNORECASE)


def load_industries(path: Path) -> IndustryCatalogue:
    """Read and validate the industry groups.

    Args:
        path: Location of branchen.yaml.

    Returns:
        The validated groups and legacy mapping.

    Raises:
        ConfigurationError: If the file cannot be read or is malformed.
    """
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"industry groups {path} cannot be read: {exc}") from exc
    try:
        return IndustryCatalogue.model_validate(content)
    except ValidationError as exc:
        raise ConfigurationError(f"industry groups {path} are malformed: {exc}") from exc
