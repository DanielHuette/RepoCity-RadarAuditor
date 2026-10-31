"""Read the signals, thresholds and contact ways of the customer search.

Context: config/kundensuche.yaml is the single source; the assessment reads it once per search.
Input: Path of kundensuche.yaml.
Output: Validated ProspectCriteria.
"""

from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from radarauditor.errors import ConfigurationError

CRITERIA_FILE = "kundensuche.yaml"


class SignalId(StrEnum):
    """Signs of a neglected profile the Places API shows, in the order the sentence names them."""

    NO_WEBSITE = "no_website"
    NO_OPENING_HOURS = "no_opening_hours"
    NO_PHONE = "no_phone"
    NO_PHOTOS = "no_photos"
    FEW_PHOTOS = "few_photos"
    NO_REVIEWS = "no_reviews"
    FEW_REVIEWS = "few_reviews"


ContactStatus = Literal["allowed", "uncertain", "forbidden"]


class Signal(BaseModel):
    """Points of one signal and its part of the reason sentence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    points: int = Field(ge=0)
    text: str = Field(min_length=1)


class Thresholds(BaseModel):
    """Where few photos and few reviews begin."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    few_photos_below: int = Field(ge=1)
    few_reviews_share: float = Field(gt=0, lt=1)
    few_reviews_min_median: int = Field(ge=1)


class ContactWay(BaseModel):
    """One way to approach a business and what the law says about it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    channel: Literal["letter", "visit", "phone", "email"]
    label: str = Field(min_length=1)
    status: ContactStatus
    note: str = Field(min_length=1)
    needs: Literal["address", "phone"] | None = None


class ProspectCriteria(BaseModel):
    """Content of config/kundensuche.yaml."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_pages: int = Field(ge=1, le=3)
    thresholds: Thresholds
    signals: dict[SignalId, Signal]
    no_signal_text: str = Field(min_length=1)
    contact_ways: list[ContactWay] = Field(min_length=1)

    @model_validator(mode="after")
    def _every_signal_defined(self) -> "ProspectCriteria":
        missing = set(SignalId) - set(self.signals)
        if missing:
            raise ValueError(f"signals without definition: {', '.join(sorted(missing))}")
        return self


def load_criteria(path: Path) -> ProspectCriteria:
    """Read and validate the criteria of the customer search.

    Args:
        path: Location of kundensuche.yaml.

    Returns:
        The validated criteria.

    Raises:
        ConfigurationError: If the file is missing, no YAML or has another shape.
    """
    try:
        return ProspectCriteria.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigurationError(f"customer search criteria {path} cannot be read: {exc}") from exc
