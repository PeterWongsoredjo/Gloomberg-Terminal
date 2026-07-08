"""AG-06 provider rate limits and breaker config, seeded from published free-tier ceilings.

These are effective config, not code constants: free-tier RPM/TPM/RPD move often, so a deploy
reseeds them. The values here are conservative defaults for the current free tiers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimits:
    """Requests and tokens per minute and per day for one provider model."""

    rpm: int
    tpm: int
    rpd: int


@dataclass(frozen=True)
class BreakerConfig:
    """When a provider is considered dead and how it probes back to life."""

    failure_threshold: int
    window_seconds: float
    cooldown_seconds: float


# conservative free-tier seeds; a deploy overrides these with current published limits
GROQ_LIMITS = RateLimits(rpm=30, tpm=6000, rpd=1000)
GEMINI_LIMITS = RateLimits(rpm=15, tpm=250000, rpd=1000)
