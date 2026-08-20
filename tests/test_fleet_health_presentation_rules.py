import ast
from pathlib import Path

import pytest

from app.services import fleet_health_presentation_rules as fhp
from app.services.fleet_health_rules import HealthClassification
from app.services.fleet_health_presentation_rules import (
    FleetPresentationSnapshot,
    PublishedSignalFact,
    RenderedSignalPageFact,
    evaluate_fh_h2,
    evaluate_presentation_findings,
)

MODULE_PATH = Path(fhp.__file__)


def _all_identifiers(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name)
    return names


# ---------------------------------------------------------------------------
# Purity / registry
# ---------------------------------------------------------------------------


class TestPurity:
    def test_no_forbidden_imports(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.append(node.module)
        forbidden = (
            "sqlalchemy", "app.database", "app.models", "app.static_export",
            "os", "pathlib", "random", "uuid", "jinja2",
        )
        for name in found:
            for bad in forbidden:
                assert not name.startswith(bad)

    def test_no_clock_random_or_scoring_identifiers(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        identifiers = {n.lower() for n in _all_identifiers(tree)}
        for banned in ("now", "utcnow", "today", "random", "uuid4", "score", "rank", "weight"):
            assert banned not in identifiers

    def test_h1_has_no_evaluator_function(self):
        # H1 is NOT_CURRENTLY_DETECTABLE per the reviewed design - there
        # must be no fabricated per-row detector for it anywhere.
        assert not hasattr(fhp, "evaluate_fh_h1")
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        identifiers = _all_identifiers(tree)
        assert "evaluate_fh_h1" not in identifiers

    def test_exactly_one_implemented_rule(self):
        assert fhp.PRESENTATION_RULE_IDS == ("FH-H2",)


class TestFactContract:
    def test_published_signal_fact_has_only_id(self):
        assert set(PublishedSignalFact.__dataclass_fields__) == {"signal_id"}

    def test_rendered_signal_page_fact_has_only_id(self):
        assert set(RenderedSignalPageFact.__dataclass_fields__) == {"signal_id"}

    def test_no_title_or_content_field_anywhere(self):
        all_fields: set[str] = set()
        for cls in (PublishedSignalFact, RenderedSignalPageFact):
            all_fields |= set(cls.__dataclass_fields__)
        for banned in ("title", "airport_name", "name", "html", "content", "text"):
            assert banned not in all_fields


# ---------------------------------------------------------------------------
# FH-H2
# ---------------------------------------------------------------------------


class TestFhH2:
    def test_matching_sets_no_finding(self):
        snapshot = FleetPresentationSnapshot(
            published_signals=(PublishedSignalFact(1), PublishedSignalFact(2)),
            rendered_signal_pages=(RenderedSignalPageFact(1), RenderedSignalPageFact(2)),
        )
        assert evaluate_fh_h2(snapshot) == ()

    def test_both_empty_no_finding(self):
        assert evaluate_fh_h2(FleetPresentationSnapshot()) == ()

    def test_published_but_not_rendered_fires_error(self):
        snapshot = FleetPresentationSnapshot(
            published_signals=(PublishedSignalFact(1), PublishedSignalFact(2)),
            rendered_signal_pages=(RenderedSignalPageFact(1),),
        )
        findings = evaluate_fh_h2(snapshot)
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "FH-H2"
        assert f.classification == HealthClassification.DETERMINISTIC_ERROR
        assert f.structured_evidence["missing_signal_ids"] == (2,)
        assert f.structured_evidence["extra_signal_ids"] == ()
        assert f.entity_ids == (2,)

    def test_rendered_but_not_published_fires_error(self):
        snapshot = FleetPresentationSnapshot(
            published_signals=(PublishedSignalFact(1),),
            rendered_signal_pages=(RenderedSignalPageFact(1), RenderedSignalPageFact(2)),
        )
        findings = evaluate_fh_h2(snapshot)
        assert len(findings) == 1
        f = findings[0]
        assert f.structured_evidence["extra_signal_ids"] == (2,)
        assert f.structured_evidence["missing_signal_ids"] == ()

    def test_same_count_different_ids_is_not_silently_missed(self):
        # Review-checkpoint regression test matching the mission's exact
        # attack: published={1,2}, rendered={1,3} - same COUNT (2 == 2) but
        # a genuinely different SET. A pure count-comparison implementation
        # would wrongly report zero findings here; FH-H2 is set-based
        # specifically to catch this class of stale/wrong-content export.
        snapshot = FleetPresentationSnapshot(
            published_signals=(PublishedSignalFact(1), PublishedSignalFact(2)),
            rendered_signal_pages=(RenderedSignalPageFact(1), RenderedSignalPageFact(3)),
        )
        findings = evaluate_fh_h2(snapshot)
        assert len(findings) == 1
        assert findings[0].structured_evidence["published_count"] == 2
        assert findings[0].structured_evidence["rendered_count"] == 2
        assert findings[0].structured_evidence["missing_signal_ids"] == (2,)
        assert findings[0].structured_evidence["extra_signal_ids"] == (3,)

    def test_both_missing_and_extra_in_one_finding(self):
        snapshot = FleetPresentationSnapshot(
            published_signals=(PublishedSignalFact(1), PublishedSignalFact(2)),
            rendered_signal_pages=(RenderedSignalPageFact(2), RenderedSignalPageFact(3)),
        )
        findings = evaluate_fh_h2(snapshot)
        assert len(findings) == 1
        f = findings[0]
        assert f.structured_evidence["missing_signal_ids"] == (1,)
        assert f.structured_evidence["extra_signal_ids"] == (3,)
        assert f.entity_ids == (1, 3)

    def test_counts_in_evidence_are_accurate(self):
        snapshot = FleetPresentationSnapshot(
            published_signals=tuple(PublishedSignalFact(i) for i in range(1, 6)),
            rendered_signal_pages=tuple(RenderedSignalPageFact(i) for i in range(1, 5)),
        )
        findings = evaluate_fh_h2(snapshot)
        assert findings[0].structured_evidence["published_count"] == 5
        assert findings[0].structured_evidence["rendered_count"] == 4

    def test_duplicate_input_row_does_not_manufacture_false_mismatch(self):
        snapshot = FleetPresentationSnapshot(
            published_signals=(PublishedSignalFact(1), PublishedSignalFact(1)),
            rendered_signal_pages=(RenderedSignalPageFact(1),),
        )
        assert evaluate_fh_h2(snapshot) == ()

    def test_duplicate_rendered_row_does_not_manufacture_false_mismatch(self):
        snapshot = FleetPresentationSnapshot(
            published_signals=(PublishedSignalFact(1),),
            rendered_signal_pages=(RenderedSignalPageFact(1), RenderedSignalPageFact(1)),
        )
        assert evaluate_fh_h2(snapshot) == ()

    def test_no_score_confidence_or_ranking_field_on_finding(self):
        snapshot = FleetPresentationSnapshot(
            published_signals=(PublishedSignalFact(1),),
            rendered_signal_pages=(),
        )
        findings = evaluate_fh_h2(snapshot)
        for banned in ("score", "confidence", "rank", "auto_repair", "fix"):
            assert banned not in findings[0].structured_evidence


class TestDeterminism:
    def test_repeated_evaluation_equal(self):
        snapshot = FleetPresentationSnapshot(
            published_signals=(PublishedSignalFact(1), PublishedSignalFact(2)),
            rendered_signal_pages=(RenderedSignalPageFact(1),),
        )
        assert evaluate_fh_h2(snapshot) == evaluate_fh_h2(snapshot)

    def test_reversed_input_order_produces_identical_finding(self):
        forward = FleetPresentationSnapshot(
            published_signals=(PublishedSignalFact(1), PublishedSignalFact(2), PublishedSignalFact(3)),
            rendered_signal_pages=(RenderedSignalPageFact(1),),
        )
        reversed_ = FleetPresentationSnapshot(
            published_signals=tuple(reversed(forward.published_signals)),
            rendered_signal_pages=forward.rendered_signal_pages,
        )
        assert evaluate_fh_h2(forward) == evaluate_fh_h2(reversed_)

    def test_evaluate_presentation_findings_matches_direct_call(self):
        snapshot = FleetPresentationSnapshot(
            published_signals=(PublishedSignalFact(1),),
            rendered_signal_pages=(),
        )
        assert evaluate_presentation_findings(snapshot) == evaluate_fh_h2(snapshot)


class TestDataAnomalyVsPresentationAnomalyBoundary:
    def test_facts_cannot_represent_page_content_at_all(self):
        # Structural proof: neither fact type has anywhere to put a name,
        # title, or rendered text, so FH-H2 cannot be extended by accident
        # into a content-comparison rule - it can only ever compare
        # identity/count. A bad-but-faithfully-rendered Airport.name (the
        # Signal #20 case) is therefore structurally unreachable by this
        # module - it has no field that could ever represent it.
        all_fields: set[str] = set()
        for cls in (PublishedSignalFact, RenderedSignalPageFact):
            all_fields |= set(cls.__dataclass_fields__)
        assert all_fields == {"signal_id"}
