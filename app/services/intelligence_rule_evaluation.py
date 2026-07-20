"""Evaluate deterministic Intelligence rules against current accepted Facts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.intelligence.rules import (
    IntelligenceRule,
    IntelligenceRuleRegistry,
    RuleResult,
    default_rule_registry,
)
from app.models import Fact, Intelligence
from app.repositories import FactRepository
from app.services.intelligence_derivation import (
    IntelligenceDerivationError,
    IntelligenceDerivationService,
)


@dataclass(frozen=True, slots=True)
class RuleEvaluationError:
    code: str
    message: str
    rule_key: str | None = None


@dataclass(frozen=True, slots=True)
class RuleEvaluationReport:
    rules_evaluated: tuple[str, ...]
    results_produced: tuple[RuleResult, ...]
    intelligence_created: tuple[Intelligence, ...]
    validation_errors: tuple[RuleEvaluationError, ...]


class IntelligenceRuleEvaluationService:
    def __init__(
        self,
        session: Session,
        registry: IntelligenceRuleRegistry | None = None,
    ) -> None:
        self.session = session
        self.registry = registry or default_rule_registry()

    def evaluate(
        self,
        rule_keys: list[str] | None = None,
        *,
        persist: bool = False,
    ) -> RuleEvaluationReport:
        facts = FactRepository(self.session).list_current()
        rules, errors = self._resolve_rules(rule_keys)
        current_fact_ids = {fact.id for fact in facts}
        results: list[RuleResult] = []
        seen: set[RuleResult] = set()

        for rule in rules:
            for candidate in rule.evaluate(facts):
                error = self._validate_result(candidate, rule, current_fact_ids)
                if error is not None:
                    errors.append(error)
                    continue
                if candidate in seen:
                    continue
                seen.add(candidate)
                results.append(candidate)

        created: list[Intelligence] = []
        if persist:
            derivation_service = IntelligenceDerivationService(self.session)
            for result in results:
                try:
                    intelligence = derivation_service.derive(
                        result.finding_type_key,
                        result.fact_ids,
                        title=result.title,
                        summary=result.summary,
                    )
                except IntelligenceDerivationError as exc:
                    errors.append(
                        RuleEvaluationError(
                            code=exc.code,
                            message=exc.message,
                            rule_key=result.rule_key,
                        )
                    )
                else:
                    created.append(intelligence)

        return RuleEvaluationReport(
            rules_evaluated=tuple(rule.key for rule in rules),
            results_produced=tuple(results),
            intelligence_created=tuple(created),
            validation_errors=tuple(errors),
        )

    def _resolve_rules(
        self, rule_keys: list[str] | None
    ) -> tuple[list[IntelligenceRule], list[RuleEvaluationError]]:
        if rule_keys is None:
            return self.registry.list(), []

        rules: list[IntelligenceRule] = []
        errors: list[RuleEvaluationError] = []
        for key in sorted(set(rule_keys)):
            rule = self.registry.get_by_key(key)
            if rule is None:
                errors.append(
                    RuleEvaluationError(
                        code="unknown_rule",
                        message=f"Intelligence rule {key!r} is not registered.",
                        rule_key=key,
                    )
                )
            else:
                rules.append(rule)
        return rules, errors

    @staticmethod
    def _validate_result(
        result: object,
        rule: IntelligenceRule,
        current_fact_ids: set[int],
    ) -> RuleEvaluationError | None:
        def error(code: str, message: str) -> RuleEvaluationError:
            return RuleEvaluationError(code=code, message=message, rule_key=rule.key)

        if not isinstance(result, RuleResult):
            return error("invalid_result_type", "Rule output must be a RuleResult.")
        if result.rule_key != rule.key:
            return error("rule_key_mismatch", "RuleResult rule key does not match its rule.")
        if result.finding_type_key != rule.finding_type_key:
            return error(
                "finding_type_mismatch",
                "RuleResult FindingType does not match its rule.",
            )
        if not isinstance(result.fact_ids, tuple) or not result.fact_ids:
            return error("fact_ids_required", "RuleResult requires ordered Fact IDs.")
        if any(not isinstance(fact_id, int) for fact_id in result.fact_ids):
            return error("invalid_fact_ids", "RuleResult Fact IDs must be integers.")
        if len(set(result.fact_ids)) != len(result.fact_ids):
            return error("duplicate_fact_ids", "RuleResult Fact IDs must be unique.")
        if tuple(sorted(result.fact_ids)) != result.fact_ids:
            return error("unordered_fact_ids", "RuleResult Fact IDs must be ordered.")
        if any(fact_id not in current_fact_ids for fact_id in result.fact_ids):
            return error(
                "non_current_fact",
                "RuleResult may reference only current accepted Facts.",
            )
        for field, value in (
            ("finding_type_key", result.finding_type_key),
            ("title", result.title),
            ("summary", result.summary),
        ):
            if not isinstance(value, str) or not value.strip():
                return error(
                    f"{field}_required",
                    f"RuleResult {field} must contain non-whitespace text.",
                )
        return None
