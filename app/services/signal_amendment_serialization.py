"""Canonical TEXT serialization for Signal amendment field values (RWI HQ
"Signal Amendment Audit Trail — Append-Only Governance" mission,
implementing docs/architecture/rwi-signal-amendment-audit-trail-design.md
§8).

    a Python value already validated as one of ALLOWED_AMENDMENT_FIELDS'
    known types (int, str, date, datetime, Decimal - or None)
        -> serialize_amendment_value()
        -> str | None (a real Python None -> a real SQL NULL; never a
           sentinel string)
        -> stored verbatim in SignalAmendmentFieldChange.old_value/new_value
        -> deserialize_amendment_value(text, expected_type)
        -> the original typed Python value back, exactly
        -> STOP

WHY TEXT, KEYED BY FIELD NAME, NOT A STORED TYPE TAG: the relevant type
universe is small, closed, and fully known in advance - entirely determined
by `app.services.signal_amendment.ALLOWED_AMENDMENT_FIELDS`, itself a
small, version-controlled Python constant. `field_name` alone already
identifies `Signal.__table__.columns[field_name].type` deterministically
(see `expected_python_type_for_field()` below, which derives this fresh
from the live ORM model - never a second, hardcoded, driftable mapping) -
so no per-row type tag is ever needed. This is a deliberate rejection of
both a wide, mostly-NULL typed-value-column schema (design doc §8 Option 2)
and a JSON blob (design doc §8 Option 3, for which this codebase has no
existing precedent anywhere).

ROUND-TRIP GUARANTEES (design doc §8's own explicit requirements):

  - `None` <-> a genuine SQL NULL, never a sentinel string like `"NULL"` or
    `""` - NULL and empty string remain natively, cleanly distinguishable.
  - `Decimal` <-> `str(Decimal(...))`, NEVER via `float()` at any point in
    either direction - `float` would silently reintroduce binary
    floating-point error into a currency figure.
  - `date`/`datetime` <-> `.isoformat()` / `.fromisoformat()` - predictable,
    unambiguous, and (for `datetime`) preserves whatever timezone-awareness
    the caller's own value already had; this module never injects or
    assumes a timezone that was not already present on the input.
  - `int` <-> `str(int(...))`.
  - `str` <-> stored as-is.

FAILS CLOSED, NEVER SILENTLY COERCES (mission's own explicit instruction):
an unsupported Python value type (including `bool`, which this module
deliberately refuses even though `bool` is a Python subclass of `int` - a
caller-supplied `True`/`False` for an integer field like `target_year`
would otherwise be silently, confusingly coerced to `"1"`/`"0"`), an
unsupported/unknown field name, or an unparseable historical string all
raise `SignalAmendmentSerializationError` rather than guessing.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import sqlalchemy as sa

from app.models.signal import Signal

__all__ = [
    "SignalAmendmentSerializationError",
    "expected_python_type_for_field",
    "serialize_amendment_value",
    "deserialize_amendment_value",
]


class SignalAmendmentSerializationError(ValueError):
    """Raised for every refusal this module makes - a plain ValueError
    subclass, matching app.services.signal_amendment.SignalAmendmentError's
    own convention."""


# Ordered so a `DateTime` column is never mis-tested against `Date` first -
# SQLAlchemy's `Date`/`DateTime` are independent sibling classes (neither is
# a subclass of the other), so order does not actually matter for
# correctness here, but the DateTime/Decimal/Integer/Date/String order
# below mirrors this module's own serialize_amendment_value() dispatch
# order for readability.
_SQLA_TYPE_TO_PYTHON_TYPE: "tuple[tuple[type, type], ...]" = (
    (sa.DateTime, datetime),
    (sa.Date, date),
    (sa.Numeric, Decimal),
    (sa.Integer, int),
    (sa.String, str),
)


def expected_python_type_for_field(field_name: str) -> type:
    """Derives the expected Python type for one Signal column FRESH from
    the live ORM model every call - never a second, hardcoded mapping that
    could silently drift from `app/models/signal.py`. Raises
    `SignalAmendmentSerializationError` for a field this module does not
    know how to type (including any field not on `Signal` at all) - fails
    closed rather than guessing."""
    try:
        column = Signal.__table__.columns[field_name]
    except KeyError as exc:
        raise SignalAmendmentSerializationError(f"unsupported field for amendment serialization: {field_name!r}") from exc
    for sqla_type, python_type in _SQLA_TYPE_TO_PYTHON_TYPE:
        if isinstance(column.type, sqla_type):
            return python_type
    raise SignalAmendmentSerializationError(
        f"field {field_name!r} has an unsupported column type for amendment serialization: {column.type!r}"
    )


def serialize_amendment_value(value: Any) -> Optional[str]:
    """`None` -> `None` (a real SQL NULL once persisted). Every other
    supported type -> its canonical TEXT form. Raises
    `SignalAmendmentSerializationError` for anything else - never silently
    stringifies an unsupported type via a bare `str(value)` fallback."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise SignalAmendmentSerializationError(
            f"bool is not a supported amendment value type (got {value!r}) - no ALLOWED_AMENDMENT_FIELDS "
            "entry is boolean today, and silently coercing True/False to an int-shaped '1'/'0' would be "
            "an unannounced type coercion"
        )
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    raise SignalAmendmentSerializationError(f"unsupported value type for amendment serialization: {type(value)!r}")


def deserialize_amendment_value(text: Optional[str], expected_type: type) -> Any:
    """The exact inverse of `serialize_amendment_value()`, keyed by an
    explicitly-supplied `expected_type` (see
    `expected_python_type_for_field()`) rather than re-guessing the type
    from the text's own shape - a stored `"2028"` must deserialize to the
    `int` `2028` for `target_year` but would be nonsensical to
    auto-detect as a `Decimal` or a `date`. Raises
    `SignalAmendmentSerializationError` for an unparseable value or an
    unsupported `expected_type` - never silently returns `None` or a
    best-guess value for a historical row that fails to parse."""
    if text is None:
        return None
    if expected_type is int:
        try:
            return int(text)
        except ValueError as exc:
            raise SignalAmendmentSerializationError(f"unparseable int amendment value: {text!r}") from exc
    if expected_type is Decimal:
        try:
            return Decimal(text)
        except InvalidOperation as exc:
            raise SignalAmendmentSerializationError(f"unparseable Decimal amendment value: {text!r}") from exc
    if expected_type is date:
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise SignalAmendmentSerializationError(f"unparseable date amendment value: {text!r}") from exc
    if expected_type is datetime:
        try:
            return datetime.fromisoformat(text)
        except ValueError as exc:
            raise SignalAmendmentSerializationError(f"unparseable datetime amendment value: {text!r}") from exc
    if expected_type is str:
        return text
    raise SignalAmendmentSerializationError(f"unsupported expected_type for amendment deserialization: {expected_type!r}")
