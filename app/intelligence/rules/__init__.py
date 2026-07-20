from app.intelligence.rules.base import IntelligenceRule, RuleResult
from app.intelligence.rules.current_emas import CurrentEmasRule
from app.intelligence.rules.registry import IntelligenceRuleRegistry


def default_rule_registry() -> IntelligenceRuleRegistry:
    return IntelligenceRuleRegistry([CurrentEmasRule()])


__all__ = [
    "CurrentEmasRule",
    "IntelligenceRule",
    "IntelligenceRuleRegistry",
    "RuleResult",
    "default_rule_registry",
]
