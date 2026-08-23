"""Deterministic, lossless `EvidenceBag` <-> JSON serialization (EB1,
docs/architecture/rwi-eb1-evidencebag-persistence-foundation-report.md,
Slice 1 of docs/architecture/rwi-full-evidencebag-persistence-design.md).

Closes the exact information-loss bug UAC5's own adversarial review
proved empirically: `app.services.discovery_evidence_persistence._join_or_none()`
(`", ".join(sorted(values))`) collapses structurally different
`frozenset[str]` values to the identical string whenever any member
itself contains the delimiter (e.g. `{"KABC, KXYZ"}` and
`{"KABC", "KXYZ"}` both join to `"KABC, KXYZ"`). This module never joins
strings with a delimiter - every set-valued field is serialized as its
own JSON array, so no delimiter-collision is possible for any input,
including values that themselves contain commas, quotes, or newlines.

Deliberately NOT `repr()` and NOT `pickle`: neither is a stable,
cross-version, human-inspectable, or safely-deserializable format for
data a database is expected to hold indefinitely - `repr()` has no
compatibility contract across Python versions for dataclasses' own
field ordering/formatting, and `pickle` can execute arbitrary code on
deserialization, an unacceptable property for governance-relevant
persisted evidence. JSON is deterministic, versionable, Unicode-safe by
construction (this module's own tests attack Swedish/Portuguese/
Japanese/emoji content directly), and inspectable via SQLite's own
JSON1 functions without deserializing in Python.

DETERMINISM: `EvidenceBag`'s eleven identity-relevant fields are all
`frozenset[str]` - by their own type, unordered, so serializing each as
a `sorted()` JSON array is a pure canonicalization choice, never a
change to the field's own semantics (a `frozenset` never had a defined
order for anything to preserve). `json.dumps(..., sort_keys=True)`
additionally makes the top-level key order deterministic regardless of
how the payload dict was constructed. Two equivalent `EvidenceBag`
values (same field contents, any construction order) always serialize to
byte-identical JSON.

STRICT, NEVER FORWARD-COMPATIBLE BY SILENT DROP: deserialization refuses
on any missing OR extra top-level field, and on any field of the wrong
JSON type - this module prefers a loud failure over a best-effort partial
reconstruction, exactly the "prefer strict replay correctness" instruction
this module's own governing mission gave. A future schema evolution must
bump `EVIDENCE_BAG_SCHEMA_VERSION` and add an explicit, reviewed migration
path in THIS module - never silently accept an old or unrecognized shape.
"""
from __future__ import annotations

import hashlib
import json

from app.services.evidence_attachment_guard import EvidenceBag

__all__ = [
    "EVIDENCE_BAG_SCHEMA_VERSION",
    "EvidenceBagSerializationError",
    "serialize_evidence_bag",
    "deserialize_evidence_bag",
    "hash_serialized_evidence_bag",
]

# Bump only alongside a reviewed change to this module's own serialization
# shape - see the module docstring's "STRICT, NEVER FORWARD-COMPATIBLE"
# section. Never bump merely because EvidenceBag itself gained a field
# without this module being updated to serialize it.
EVIDENCE_BAG_SCHEMA_VERSION = 1

# Every EvidenceBag field is enumerated explicitly here, verified fresh
# against the actual dataclass definition (app/services/evidence_attachment_guard.py) -
# not copied from any prior design document. If EvidenceBag ever gains or
# loses a field, this module's own tests (test_evidence_bag_persistence.py)
# will fail loudly rather than silently under- or over-serializing.
_SET_FIELDS = (
    "identifiers",
    "names",
    "runway_ends",
    "runway_pairs",
    "issuers",
    "locations",
    "contradicting_names",
    "contradicting_issuers",
    "contradicting_locations",
    "alternate_airport_runway_ends",
    "alternate_airport_runway_pairs",
)
_SCALAR_FIELDS = ("document_title", "project_number", "contract_number", "url")
_ALL_PAYLOAD_KEYS = frozenset({"schema_version", *_SET_FIELDS, *_SCALAR_FIELDS})


class EvidenceBagSerializationError(ValueError):
    """Raised by `deserialize_evidence_bag()` for any malformed, wrong-
    shape, wrong-type, or unsupported-schema-version payload. Never
    raised by `serialize_evidence_bag()`, which only ever receives an
    already-valid, already-type-checked `EvidenceBag` instance (the
    dataclass's own `__post_init__`/type system is the input guard for
    that direction)."""


def serialize_evidence_bag(evidence_bag: EvidenceBag) -> str:
    """Pure, deterministic, no I/O. Every one of `EvidenceBag`'s eleven
    identity-relevant `frozenset[str]` fields becomes a `sorted()` JSON
    array (canonicalization only - `frozenset` has no order of its own to
    preserve); the four audit-only scalar fields are carried through
    verbatim. `json.dumps(..., sort_keys=True, ensure_ascii=False)`:
    `sort_keys` for deterministic top-level key order, `ensure_ascii=False`
    so Unicode content is stored as readable UTF-8 text, not `\\uXXXX`
    escapes, directly satisfying this module's own human-auditability
    goal."""
    payload = {"schema_version": EVIDENCE_BAG_SCHEMA_VERSION}
    for name in _SET_FIELDS:
        payload[name] = sorted(getattr(evidence_bag, name))
    for name in _SCALAR_FIELDS:
        payload[name] = getattr(evidence_bag, name)
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def deserialize_evidence_bag(payload: str) -> EvidenceBag:
    """Fails loud (`EvidenceBagSerializationError`) on: invalid JSON, a
    non-object top level, a missing `schema_version`, an unsupported
    `schema_version`, any missing or unrecognized top-level field, or any
    field holding the wrong JSON type. Never attempts a best-effort
    partial reconstruction."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise EvidenceBagSerializationError(f"invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise EvidenceBagSerializationError(
            f"expected a JSON object at the top level, got {type(data).__name__}"
        )

    if "schema_version" not in data:
        raise EvidenceBagSerializationError("missing required 'schema_version' field")
    schema_version = data["schema_version"]
    # Strict type check, not merely `!=`: Python's `bool` is a subclass of
    # `int` (`True == 1` and `isinstance(True, int)` are both True), and
    # `1.0 == 1` is also True - a plain `!=` comparison would silently
    # accept `schema_version: true` or `schema_version: 1.0` as if they
    # were the real integer `1`. `type(schema_version) is not int` rejects
    # both (bool's actual runtime type is `bool`, never `int`, even though
    # it subclasses it; float's runtime type is `float`), matching this
    # module's own "fail loud, never silently coerce" contract exactly.
    if type(schema_version) is not int or schema_version != EVIDENCE_BAG_SCHEMA_VERSION:
        raise EvidenceBagSerializationError(
            f"unsupported schema_version {schema_version!r} - this module only supports the "
            f"literal integer {EVIDENCE_BAG_SCHEMA_VERSION!r}. A future schema evolution requires "
            "its own reviewed change to this module, never a silent best-effort read."
        )

    actual_keys = frozenset(data.keys())
    missing = _ALL_PAYLOAD_KEYS - actual_keys
    if missing:
        raise EvidenceBagSerializationError(f"missing required field(s): {sorted(missing)}")
    extra = actual_keys - _ALL_PAYLOAD_KEYS
    if extra:
        raise EvidenceBagSerializationError(
            f"unexpected/unknown field(s): {sorted(extra)} - this module never silently "
            "drops unrecognized fields"
        )

    kwargs: dict = {}
    for name in _SET_FIELDS:
        value = data[name]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise EvidenceBagSerializationError(
                f"field {name!r} must be a JSON array of strings, got {value!r}"
            )
        kwargs[name] = frozenset(value)
    for name in _SCALAR_FIELDS:
        value = data[name]
        if value is not None and not isinstance(value, str):
            raise EvidenceBagSerializationError(
                f"field {name!r} must be a string or null, got {value!r}"
            )
        kwargs[name] = value

    return EvidenceBag(**kwargs)


def hash_serialized_evidence_bag(serialized: str) -> str:
    """SHA-256 hex digest of the EXACT persisted serialized string -
    never a second, independently-normalized representation of the
    EvidenceBag object. Deterministic: identical serialized text always
    produces the identical hash, and (since `serialize_evidence_bag()`
    itself is deterministic) a serialize -> deserialize -> serialize
    round trip always reproduces the identical hash."""
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
