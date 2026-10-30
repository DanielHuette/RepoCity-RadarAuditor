"""Load and validate config/pruefkatalog.yaml.

Context: The catalogue is the single source of checks, weights, thresholds and customer wording.
Input: YAML file.
Output: Catalogue model.
"""

import logging
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from radarauditor.errors import ScoringError

logger = logging.getLogger(__name__)

TOTAL_WEIGHT = 100
CHECK_ID_PATTERN = re.compile(r"^[a-n]\d+$")
KNOWN_SOURCES = frozenset(
    {
        "crawler",
        "pagespeed",
        "places",
        "serp",
        "directories",
        "assistants",
        "paid",
        "access",
        "manual",
    }
)


class CheckDefinition(BaseModel):
    """One check as defined in the catalogue.

    measured_by names the data behind the check where the source alone does not say it.
    """

    id: str
    area: str
    name: str
    why: str
    source: str
    requires_paid: bool
    requires_access: bool
    thresholds: dict[str, float | int | str]
    customer_question: str
    customer_impact: str
    measured_by: str | None = None


class AreaDefinition(BaseModel):
    """One catalogue area (a to n) with its weight."""

    id: str
    name: str
    weight: int
    checks: list[CheckDefinition]


class Catalogue(BaseModel):
    """All areas; weights must sum to 100."""

    areas: list[AreaDefinition]


def load_catalogue(path: Path) -> Catalogue:
    """Parse the YAML and verify weights, unique ids and known sources.

    Args:
        path: Location of the catalogue YAML file.

    Returns:
        The validated catalogue.

    Raises:
        ScoringError: If the file cannot be read or the catalogue is inconsistent.
    """
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ScoringError(f"Cannot read check catalogue {path}: {error}") from error
    except yaml.YAMLError as error:
        raise ScoringError(f"Check catalogue {path} is not valid YAML: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get("areas"), list):
        raise ScoringError(f"Check catalogue {path} has no list of areas")
    try:
        catalogue = Catalogue.model_validate({"areas": _with_area_ids(document["areas"])})
    except ValidationError as error:
        raise ScoringError(f"Check catalogue {path} is malformed: {error}") from error
    validate_catalogue(catalogue)
    logger.debug("Loaded check catalogue %s with %d areas", path, len(catalogue.areas))
    return catalogue


def validate_catalogue(catalogue: Catalogue) -> None:
    """Verify weights, check ids, sources and the paid/access flags.

    Args:
        catalogue: Parsed catalogue.

    Raises:
        ScoringError: On the first inconsistency found.
    """
    weights = sum(area.weight for area in catalogue.areas)
    if weights != TOTAL_WEIGHT:
        raise ScoringError(f"Area weights sum to {weights}, expected {TOTAL_WEIGHT}")
    area_ids = [area.id for area in catalogue.areas]
    if len(set(area_ids)) != len(area_ids):
        raise ScoringError("Area ids are not unique")
    seen: set[str] = set()
    for area in catalogue.areas:
        if area.weight < 0:
            raise ScoringError(f"Area {area.id} has a negative weight")
        for check in area.checks:
            _validate_check(check, area.id, seen)
            seen.add(check.id)


def find_check(catalogue: Catalogue, check_id: str) -> CheckDefinition:
    """Return the definition of one check.

    Args:
        catalogue: Validated catalogue.
        check_id: Id such as "a2".

    Returns:
        The matching check definition.

    Raises:
        ScoringError: If no check has this id.
    """
    for area in catalogue.areas:
        for check in area.checks:
            if check.id == check_id:
                return check
    raise ScoringError(f"Check {check_id} is not in the catalogue")


def checks_for_context(
    catalogue: Catalogue, paid_enabled: bool, with_access: bool
) -> list[CheckDefinition]:
    """Return the checks that count in this audit context, in catalogue order.

    Args:
        catalogue: Validated catalogue.
        paid_enabled: Whether the paid ranking provider is switched on.
        with_access: Whether the audit has access to the customer's own accounts.

    Returns:
        Checks whose paid and access requirements are satisfied.
    """
    return [
        check
        for area in catalogue.areas
        for check in area.checks
        if (paid_enabled or not check.requires_paid) and (with_access or not check.requires_access)
    ]


def _with_area_ids(areas: list[object]) -> list[object]:
    """Copy the area id into every check, since the YAML nests checks without it."""
    enriched: list[object] = []
    for area in areas:
        if not isinstance(area, dict) or not isinstance(area.get("checks"), list):
            raise ScoringError("Every area needs an id and a list of checks")
        checks = [
            {**check, "area": area.get("id")} if isinstance(check, dict) else check
            for check in area["checks"]
        ]
        enriched.append({**area, "checks": checks})
    return enriched


def _validate_check(check: CheckDefinition, area_id: str, seen: set[str]) -> None:
    """Raise ScoringError if one check breaks an id, source or flag rule."""
    if not CHECK_ID_PATTERN.fullmatch(check.id):
        raise ScoringError(f"Check id {check.id!r} does not match {CHECK_ID_PATTERN.pattern}")
    if check.id in seen:
        raise ScoringError(f"Check id {check.id} is defined twice")
    if not check.id.startswith(area_id):
        raise ScoringError(f"Check {check.id} is filed under area {area_id}")
    if check.source not in KNOWN_SOURCES:
        raise ScoringError(f"Check {check.id} has unknown source {check.source!r}")
    if (check.source == "paid") != check.requires_paid:
        raise ScoringError(f"Check {check.id}: source paid and requires_paid disagree")
    if (check.source == "access") != check.requires_access:
        raise ScoringError(f"Check {check.id}: source access and requires_access disagree")
