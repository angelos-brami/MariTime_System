from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import yaml
from eastmed_schema.enums import AccountTier

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "alert_rules.yaml"


@dataclass(frozen=True)
class TierThrottle:
    max_alerts_day: int
    min_severity: int


@dataclass(frozen=True)
class AlertRules:
    version: str
    throttles: dict[AccountTier, TierThrottle]
    severity_4_bypasses_daily_cap: bool

    def for_tier(self, tier: AccountTier) -> TierThrottle:
        return self.throttles[tier]


@lru_cache(maxsize=4)
def load_alert_rules(path: str | None = None) -> AlertRules:
    config_path = Path(path) if path else _CONFIG_PATH
    payload = cast(dict[str, Any], yaml.safe_load(config_path.read_text(encoding="utf-8")))
    raw_throttles = cast(dict[str, dict[str, int]], payload.get("throttles", {}))
    throttles: dict[AccountTier, TierThrottle] = {}
    for tier in AccountTier:
        raw = raw_throttles.get(tier.value)
        if raw is None:
            raise ValueError(f"Alert rules are missing account tier {tier.value}")
        maximum = int(raw["max_alerts_day"])
        minimum = int(raw["min_severity"])
        if maximum < 1 or not 1 <= minimum <= 4:
            raise ValueError(f"Invalid alert throttle for account tier {tier.value}")
        throttles[tier] = TierThrottle(
            max_alerts_day=maximum,
            min_severity=minimum,
        )
    return AlertRules(
        version=str(payload.get("version", "alert-rules-v1")),
        throttles=throttles,
        severity_4_bypasses_daily_cap=bool(payload.get("severity_4_bypasses_daily_cap", True)),
    )
