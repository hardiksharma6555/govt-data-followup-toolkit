"""
Shared helpers used by every stage: name normalization for fuzzy matching
across the master list and the criteria file, and mobile number cleanup.
"""
import re

DEFAULT_STRIP_TOKENS = ["GSSS", "GSS"]  # school-type tokens common to BOTH files' naming


def normalize_name(name: str, strip_tokens=None) -> str:
    """Uppercase, strip punctuation, and drop tokens that are redundant on
    BOTH sides of the match (e.g. every entry in a GSSS-only master list has
    a "GSSS" you can safely drop). Never strip a token that only appears on
    one side (e.g. "GHS") - that would silently merge two different schools
    that happen to share a village name. See README "Matching caveats"."""
    if not name:
        return ""
    strip_tokens = DEFAULT_STRIP_TOKENS if strip_tokens is None else strip_tokens
    n = name.strip().upper()
    for tok in strip_tokens:
        n = re.sub(rf"\b{re.escape(tok.upper())}\b", "", n)
    n = re.sub(r"[^A-Z0-9]+", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def clean_mobile(v, country_code="91") -> str:
    """Normalize a mobile number to <country_code><10 digits>, e.g. 91XXXXXXXXXX.
    Handles Excel's float-ification of numeric cells (7807963323.0)."""
    if v is None:
        return ""
    if isinstance(v, float):
        v = int(v)  # avoid "7807963323.0" -> stray trailing 0 after stripping the dot
    s = re.sub(r"\D", "", str(v))
    if len(s) == 10:
        return country_code + s
    if len(s) == 11 and s.startswith("0"):
        return country_code + s[1:]
    if len(s) == 10 + len(country_code) and s.startswith(country_code):
        return s
    return s  # leave as-is; caller flags anything not the expected length
