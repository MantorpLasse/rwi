from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Airport, Runway, Signal, Source


KEYWORDS = ("EMAS", "RSA", "runway safety area", "arresting system")


def add_source_and_flag_keywords(
    session: Session,
    *,
    airport: Airport,
    source: Source,
    runway: Runway | None = None,
) -> Signal | None:
    """Add a Source and, if its text mentions EMAS/RSA keywords, a low-confidence Signal.

    Rule 2 from PLAN_FORENKLING.md: when a document (Master Plan/ALP/AIP grant) is
    added, a keyword hit is a candidate worth reading, not a confirmed signal - it
    starts at confidence=low and waits for you to read it and raise that manually.
    """

    session.add(source)
    session.flush()

    haystack = " ".join(
        filter(None, (source.title, source.summary, source.document_reference))
    ).lower()
    matched = [keyword for keyword in KEYWORDS if keyword.lower() in haystack]
    if not matched:
        return None

    signal = Signal(
        airport=airport,
        runway=runway,
        source=source,
        title=f"Possible EMAS/RSA signal: {source.title}",
        category="unknown",
        confidence="low",
        notes=(
            f"Auto-flagged from source text (matched: {', '.join(matched)}). "
            "Read the source and raise confidence to medium/high once verified."
        ),
    )
    session.add(signal)
    session.flush()
    return signal
