from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.models import Fact


@dataclass(frozen=True, slots=True)
class RuleResult:
    finding_type_key: str
    fact_ids: tuple[int, ...]
    title: str
    summary: str
    rule_key: str


class IntelligenceRule(Protocol):
    key: str
    name: str
    description: str
    finding_type_key: str

    def evaluate(self, facts: Sequence[Fact]) -> Sequence[RuleResult]: ...
