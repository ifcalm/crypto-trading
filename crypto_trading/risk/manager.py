from crypto_trading.core.errors import RiskRuleViolation
from crypto_trading.core.types import Portfolio, Signal
from crypto_trading.risk.rules import RiskRule


class RiskManager:
    def __init__(self, rules: list[RiskRule] | None = None):
        self.rules = rules or []

    def add_rule(self, rule: RiskRule) -> None:
        self.rules.append(rule)

    def check_signal(self, signal: Signal, portfolio: Portfolio) -> Signal | None:
        """Apply all rules in order. Returns adjusted Signal or None if rejected."""
        current = signal
        for rule in self.rules:
            try:
                current = rule.check(current, portfolio)
            except RiskRuleViolation:
                return None
        return current
