"""
provider rate limits and breaker config, seeded from published free-tier ceilings.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimits:
    rpm: int    # requests per minute
    tpm: int    # tokens per minute
    rpd: int    # requests per day


@dataclass(frozen=True)
class BreakerConfig:
    failure_threshold: int      # consecutive failures that open the breaker
    cooldown_seconds: float

# conservative free-tier seeds, a deploy overrides these with current published limits
GROQ_LIMITS = RateLimits(rpm=30, tpm=6000, rpd=1000)
GEMINI_LIMITS = RateLimits(rpm=15, tpm=250000, rpd=1000)
