from __future__ import annotations

from collections.abc import Iterable

from app.intelligence.rules.base import IntelligenceRule


class IntelligenceRuleRegistry:
    def __init__(self, rules: Iterable[IntelligenceRule] = ()) -> None:
        self._rules: dict[str, IntelligenceRule] = {}
        for rule in rules:
            self.register(rule)

    def register(self, rule: IntelligenceRule) -> None:
        if rule.key in self._rules:
            raise ValueError(f"Duplicate Intelligence rule key: {rule.key}")
        self._rules[rule.key] = rule

    def get_by_key(self, key: str) -> IntelligenceRule | None:
        return self._rules.get(key)

    def list(self) -> list[IntelligenceRule]:
        return [self._rules[key] for key in sorted(self._rules)]
