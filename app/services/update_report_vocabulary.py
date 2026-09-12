"""Update & Report V1 — material-change and report-priority vocabulary
(RWI HQ "Update & Report V1 — First Coherent Intelligence Loop" mission).

This is a small, internal-only triage vocabulary for the Update & Report
loop. It is deliberately NOT mixed with, and never derived from:

  - Signal.probability_score / Signal.confidence (evidence strength for the
    underlying claim, not report urgency or triage shape)
  - Signal.published (publication governance, a separate concern)
  - FH-D4 disposition (app.services.signal_disposition_resolution /
    app.services.fh_d4_disposition_resolution) - exact-set, non-transitive,
    untouched by this module

STOP: no new persisted table, no new ReviewerAction vocabulary value, no
change to existing_signal_reconciliation's own three-outcome vocabulary
(app.services.existing_signal_reconciliation.ExistingSignalReconciliationOutcome)
- this module classifies the REPORT-FACING shape of a candidate, one layer
above that pure reconciliation decision, never a replacement for it.
"""
from __future__ import annotations

from enum import Enum

__all__ = [
    "MaterialChangeClassification",
    "ReportPriority",
]


class MaterialChangeClassification(str, Enum):
    """Update & Report triage outcome for one candidate piece of evidence,
    already reconciled (or not) against existing Signals via
    app.services.existing_signal_reconciliation. Never persisted."""

    NEW_SIGNAL_CANDIDATE = "NEW_SIGNAL_CANDIDATE"
    FIELD_CHANGE_CANDIDATE = "FIELD_CHANGE_CANDIDATE"
    CORROBORATION_ONLY = "CORROBORATION_ONLY"
    DUPLICATE = "DUPLICATE"
    NEEDS_MORE_EVIDENCE = "NEEDS_MORE_EVIDENCE"
    CONTRADICTION_OR_CORRECTION = "CONTRADICTION_OR_CORRECTION"
    NO_MATERIAL_CHANGE = "NO_MATERIAL_CHANGE"


class ReportPriority(str, Enum):
    """Report urgency only - never a substitute for, and never derived
    from, Signal.probability_score/confidence (see module docstring)."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"
