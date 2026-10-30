"""Text patterns and normalisers for German business data (name, address, phone, URLs).

Context: Shared by the website, directory, search and assistant collectors so that a business is
recognised the same way everywhere.
Input: Raw strings.
Output: Regular expressions, normalised strings, validated URLs.
"""

import re
from urllib.parse import urlsplit

from pydantic import HttpUrl, TypeAdapter, ValidationError

CITY = (
    r"(?:Bad\s)?[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)*"
    r"(?:\s(?:am|an der|in der|im|ob der|bei)\s[A-ZÄÖÜ][a-zäöüß]+)?"
    r"(?:\s\([A-ZÄÖÜ][a-zäöüß]+\))?"
)
POSTAL_CITY_PATTERN = re.compile(rf"(?<![\d.])(\d{{5}})\s+({CITY})")
STREET_PATTERN = re.compile(
    r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß.\- ]*?[A-Za-zäöüß.]\s?\d{1,4}\s?[a-zA-Z]?"
    r"(?:\s?[-–/]\s?\d{1,4}\s?[a-zA-Z]?)?)\s*$"
)
PHONE_PATTERN = re.compile(
    r"(?<![\d+])(?:(?:\+|00)49[\s\-/]*(?:\(0\)[\s\-/]*)?|\(?0)[1-9][\d\s\-/()]{4,20}\d"
)
PHONE_LABEL_PATTERN = re.compile(r"\b(?:tel|telefon|fon|phone|mobil)\b", re.IGNORECASE)
FAX_LABEL_PATTERN = re.compile(r"\bfax\b", re.IGNORECASE)
URL_PATTERN = re.compile(r"https?://[^\s<>()\[\]\"'`]+")
HOST_PATTERN = re.compile(r"[^\s.]+(\.[^\s.]+)+")
MIN_PHONE_DIGITS = 7
MAX_PHONE_DIGITS = 15

LEGAL_FORM_PATTERN = re.compile(
    r"(?<![\w])(?:gmbh\s*&\s*co\.?\s*kg|ggmbh|gmbh|mbh|e\.\s?k(?:fm)?\.?|"
    r"ug\s*\(haftungsbeschränkt\)|ug|ohg|kg|gbr|ag|inh\.)(?![\w])",
)
STREET_SUFFIX_PATTERN = re.compile(r"(straße|strasse|str\.)(?=\s|\d|$)")
PUNCTUATION_PATTERN = re.compile(r"[,;|·•]")

_HTTP_URL_ADAPTER: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)


def collapse_whitespace(text: str) -> str:
    """Replace every whitespace run with one space and strip the ends."""
    return " ".join(text.split())


def normalise_name(name: str) -> str:
    """Lowercase a business name, drop legal forms and collapse whitespace.

    Args:
        name: Business name as written anywhere.

    Returns:
        Comparable form, for example "dachdeckerei müller" for "Dachdeckerei Müller GmbH".
    """
    lowered = re.sub(r"[*_`#\"„“”]", "", name.lower())
    without_forms = LEGAL_FORM_PATTERN.sub(" ", lowered)
    return collapse_whitespace(without_forms).strip(" ,.-&")


def normalise_address(address: str) -> str:
    """Lowercase an address line, unify street suffixes and drop separators.

    Args:
        address: Street, postal code and city in any common spelling.

    Returns:
        Comparable form, for example "hafenstr 12 48155 münster".
    """
    lowered = STREET_SUFFIX_PATTERN.sub("str ", address.lower())
    without_separators = PUNCTUATION_PATTERN.sub(" ", lowered)
    return collapse_whitespace(without_separators)


def normalise_phone(phone: str) -> str:
    """Reduce a phone number to digits with a national leading 0.

    Args:
        phone: Number as written, for example "+49 (0) 251 / 123 45".

    Returns:
        Digits only, for example "025112345".
    """
    stripped = phone.strip().replace("(0)", "")
    digits = re.sub(r"\D", "", stripped)
    if stripped.startswith("+49"):
        return "0" + digits[2:]
    if digits.startswith("0049"):
        return "0" + digits[4:]
    return digits


def find_phone(text: str) -> str | None:
    """Return the first plausible German phone number, preferring labelled lines.

    Args:
        text: Multi-line text, for example a footer.

    Returns:
        The number as written, or None.
    """
    labelled: list[str] = []
    unlabelled: list[str] = []
    for line in text.splitlines():
        if FAX_LABEL_PATTERN.search(line) and not PHONE_LABEL_PATTERN.search(line):
            continue
        for match in PHONE_PATTERN.finditer(line):
            candidate = match.group(0).strip()
            if MIN_PHONE_DIGITS <= len(re.sub(r"\D", "", candidate)) <= MAX_PHONE_DIGITS:
                target = labelled if PHONE_LABEL_PATTERN.search(line) else unlabelled
                target.append(candidate)
    candidates = labelled or unlabelled
    return candidates[0] if candidates else None


def find_address(lines: list[str]) -> dict[str, str]:
    """Find the first postal code with city and the street written before it.

    The street is taken from the same line before the postal code or, failing that, from the
    previous line.

    Args:
        lines: Text lines, for example of a footer or a directory listing.

    Returns:
        Keys postal_code, city and, when found, street; empty when no postal code is present.
    """
    for index, line in enumerate(lines):
        match = POSTAL_CITY_PATTERN.search(line)
        if match is None:
            continue
        address = {"postal_code": match.group(1), "city": match.group(2)}
        street = _street_before(line[: match.start()]) or (
            _street_before(lines[index - 1]) if index > 0 else None
        )
        if street:
            address["street"] = street
        return address
    return {}


def _street_before(text: str) -> str | None:
    segment = re.split(r"[,|·•–]", text.strip(" ,|·•–"))[-1].strip()
    match = STREET_PATTERN.search(segment)
    return collapse_whitespace(match.group(1)) if match else None


def find_urls(text: str) -> list[str]:
    """Return the distinct http(s) URLs in a text, in order, without trailing punctuation."""
    found: list[str] = []
    for match in URL_PATTERN.finditer(text):
        url = match.group(0).rstrip(".,;:!?*")
        if url not in found:
            found.append(url)
    return found


def parse_http_url(value: str) -> HttpUrl | None:
    """Validate a string as an absolute http(s) URL; None if it is not one."""
    try:
        return _HTTP_URL_ADAPTER.validate_python(value)
    except ValidationError:
        return None


def host_of(url: str | HttpUrl | None) -> str | None:
    """Host name without a leading www., or None; a bare domain counts as its own host.

    Search collectors record organic results as domains such as "example.de", fixtures and paid
    providers may record full URLs; both forms resolve to the same host.

    Args:
        url: Full URL, protocol-relative URL or bare domain.

    Returns:
        The lowercase host without "www.", or None when the value names no host with a dot.
    """
    if url is None:
        return None
    text = str(url).strip()
    if text and "://" not in text and not text.startswith("//"):
        text = f"//{text}"
    host = urlsplit(text).hostname
    if not host or not HOST_PATTERN.fullmatch(host):
        return None
    return host.removeprefix("www.")


def site_key(host: str) -> str:
    """Return the host in lowercase without a leading "www." for same-site comparison."""
    return host.lower().removeprefix("www.")
