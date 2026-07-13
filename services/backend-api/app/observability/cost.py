"""
calculates expected costs and monitors API quota consumption against daily limits
tracks both notional usd prices and free tier ceilings to prevent getting rate limited
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.observability.clock import WIB_OFFSET

_COST_MODEL_PATH = Path(__file__).resolve().parent / "config" / "cost_model.yaml"


@dataclass(frozen=True)
class ProviderPricing:
    """notional pricing and free tier daily ceilings for a provider"""

    model: str
    prompt_per_1k: float
    completion_per_1k: float
    rpd_ceiling: int
    tpd_ceiling: int


@dataclass(frozen=True)
class CostModel:
    """pricing table with methods to compute token cost and quota percentages"""

    providers: dict[str, ProviderPricing]
    currency: str
    effective_from: date

    def notional_cost(self, provider: str, prompt_tokens: int, completion_tokens: int) -> float:
        """returns predicted cost of a generation using current pricing configuration"""
        p = self.providers.get(provider)
        if p is None:
            return 0.0
        return (prompt_tokens / 1000) * p.prompt_per_1k + (completion_tokens / 1000) * p.completion_per_1k

    def quota_pct(self, provider: str, requests: int, tokens: int) -> float:
        """returns request or token usage fraction whichever is higher"""
        p = self.providers.get(provider)
        if p is None:
            return 0.0
        by_requests = requests / p.rpd_ceiling if p.rpd_ceiling else 0.0
        by_tokens = tokens / p.tpd_ceiling if p.tpd_ceiling else 0.0
        return max(by_requests, by_tokens)


def load_cost_model(path: Path = _COST_MODEL_PATH) -> CostModel:
    doc: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    providers = {
        name: ProviderPricing(
            model=spec["model"],
            prompt_per_1k=float(spec["list_price"]["prompt_per_1k"]),
            completion_per_1k=float(spec["list_price"]["completion_per_1k"]),
            rpd_ceiling=int(spec["free_tier_ceilings"]["rpd"]),
            tpd_ceiling=int(spec["free_tier_ceilings"]["tpd"]),
        )
        for name, spec in doc["providers"].items()
    }
    return CostModel(providers=providers, currency=doc["currency"], effective_from=date.fromisoformat(str(doc["effective_from"])))


@lru_cache(maxsize=1)
def get_cost_model() -> CostModel:
    return load_cost_model()


def wib_today() -> date:
    return (datetime.now(UTC) + WIB_OFFSET).date()


class QuotaTracker:
    """tracks daily API limits per provider to bypass them before hitting rate limits"""

    def __init__(self, cost_model: CostModel, *, threshold: float, today: Any = wib_today) -> None:
        self._cost_model = cost_model
        self._threshold = threshold
        self._today = today
        self._day: date = today()
        self._requests: dict[str, int] = {}
        self._tokens: dict[str, int] = {}

    def _roll(self) -> None:
        """clears counters if calendar day changed"""
        current = self._today()
        if current != self._day:
            self._day = current
            self._requests.clear()
            self._tokens.clear()

    def seed(self, provider: str, *, requests: int, tokens: int) -> None:
        """initializes daily counters from ledger baseline"""
        self._roll()
        self._requests[provider] = requests
        self._tokens[provider] = tokens

    def record(self, provider: str, *, requests: int, tokens: int) -> None:
        """increments usage counters by request and token counts"""
        self._roll()
        self._requests[provider] = self._requests.get(provider, 0) + requests
        self._tokens[provider] = self._tokens.get(provider, 0) + tokens

    def consumption(self, provider: str) -> tuple[int, int]:
        """returns requests and tokens consumed today"""
        self._roll()
        return self._requests.get(provider, 0), self._tokens.get(provider, 0)

    def quota_pct(self, provider: str) -> float:
        """returns current quota usage percentage"""
        requests, tokens = self.consumption(provider)
        return self._cost_model.quota_pct(provider, requests, tokens)

    def exhausted(self, provider: str) -> bool:
        """returns true if quota usage has exceeded threshold"""
        return self.quota_pct(provider) >= self._threshold

