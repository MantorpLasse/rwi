"""Canonical runway/runway-end designation normalization.

Deliberately source-agnostic (docs/domain/canonical-runway-runway-end-design.md
S5/S11): no FAA/NASR-specific concept lives here. Reuses the exact
leading-zero rule scripts/import_faa_runway_ends.py already proved safe in
production (it deduplicated MHT/HYA), rather than reinventing it.
"""
from __future__ import annotations

import re

_LEADING_ZERO = re.compile(r"^0(\d)")
_HEADING_PREFIX = re.compile(r"^(\d+)")


class AmbiguousRunwayDesignationError(ValueError):
    """Raised when a designation cannot be normalized without guessing."""


def normalize_end(designation: str) -> str:
    """Normalize one runway-end token: "06" and "6" -> "6"; "04R" -> "4R"."""
    token = designation.strip().upper()
    if not token:
        raise AmbiguousRunwayDesignationError("runway end designation is empty")
    token = _LEADING_ZERO.sub(r"\1", token)
    if not _HEADING_PREFIX.match(token):
        raise AmbiguousRunwayDesignationError(f"runway end designation has no numeric heading: {designation!r}")
    return token


def _heading_sort_key(token: str) -> int:
    match = _HEADING_PREFIX.match(token)
    if not match:
        raise AmbiguousRunwayDesignationError(f"runway end designation has no numeric heading: {token!r}")
    return int(match.group(1))


def normalize_pair(pair_designation: str) -> str:
    """Normalize a runway-pair designation to a canonical, order-independent
    form: both ends normalized, lower heading number first.

    "22L/04R" and "04R/22L" both normalize to "4R/22L" (not "04R/22L" -
    normalize_end already strips the leading zero on each end; a two-digit
    heading like "22" has no leading zero to strip). Fails closed - never
    guesses - on anything that isn't exactly two ends.
    """
    parts = [part.strip() for part in pair_designation.split("/")]
    if len(parts) != 2 or not all(parts):
        raise AmbiguousRunwayDesignationError(
            f"expected a two-ended runway pair designation, got {pair_designation!r}"
        )
    normalized = [normalize_end(part) for part in parts]
    if normalized[0] == normalized[1]:
        raise AmbiguousRunwayDesignationError(f"runway pair has two identical ends: {pair_designation!r}")
    normalized.sort(key=_heading_sort_key)
    return "/".join(normalized)


def pair_ends(normalized_pair_designation: str) -> tuple[str, str]:
    """Split an already-normalized pair designation into its two end designations."""
    parts = normalized_pair_designation.split("/")
    if len(parts) != 2:
        raise AmbiguousRunwayDesignationError(
            f"expected an already-normalized two-ended pair, got {normalized_pair_designation!r}"
        )
    return parts[0], parts[1]
