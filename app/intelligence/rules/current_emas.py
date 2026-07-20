from __future__ import annotations

from collections.abc import Sequence

from app.intelligence.rules.base import RuleResult
from app.models import Fact


class CurrentEmasRule:
    key = "CURRENT_EMAS"
    name = "Current EMAS"
    description = "Establish current EMAS presence from a governed product Fact."
    finding_type_key = "CURRENT_EMAS"
    required_fact_type_key = "airport.emas.product"

    def evaluate(self, facts: Sequence[Fact]) -> tuple[RuleResult, ...]:
        by_subject: dict[tuple[str, str], list[Fact]] = {}
        for fact in facts:
            if fact.fact_type_key != self.required_fact_type_key:
                continue
            subject = (fact.subject_type, fact.subject_identifier)
            by_subject.setdefault(subject, []).append(fact)

        results: list[RuleResult] = []
        for subject_type, subject_identifier in sorted(by_subject):
            support = min(
                by_subject[(subject_type, subject_identifier)],
                key=lambda fact: fact.id,
            )
            results.append(
                RuleResult(
                    finding_type_key=self.finding_type_key,
                    fact_ids=(support.id,),
                    title=(
                        f"Current EMAS established for {subject_type} "
                        f"{subject_identifier}"
                    ),
                    summary=(
                        "A current accepted airport.emas.product Fact establishes "
                        f"an EMAS installation for {subject_type} {subject_identifier}."
                    ),
                    rule_key=self.key,
                )
            )
        return tuple(results)
