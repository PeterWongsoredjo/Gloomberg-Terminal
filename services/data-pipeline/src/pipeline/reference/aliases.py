"""
Turns an IDX company name into the name forms a headline might actually use.

Headlines say "Bank Mega Syariah", never "PT Bank Mega Syariah Tbk", so the
registry has to carry the stripped forms too. The hard part is not generating
name forms, it is refusing the ones that would match ordinary Indonesian.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

_NOISE_WORDS = frozenset({"PT", "TBK", "PERSERO", "PERSEROAN", "TERBUKA"})

GENERIC_SEED = frozenset(
    {
        "ABADI", "AGRO", "ASIA", "ASURANSI", "BANK", "BUANA", "BUMI", "CITRA",
        "ENERGI", "ENERGY", "GLOBAL", "GROUP", "INDO", "INDONESIA", "INDUSTRI",
        "INTERNASIONAL", "INTERNATIONAL", "INTI", "INVESTAMA", "JAYA", "KARYA",
        "LESTARI", "MAKMUR", "MANDIRI", "MEGA", "MITRA", "MULTI", "NUSANTARA",
        "PERKASA", "PERSADA", "PRATAMA", "PRIMA", "PUTRA", "RAYA", "SARANA",
        "SEJAHTERA", "SENTOSA", "SUKSES", "SUMBER", "UTAMA",
    }
)

CATEGORY_LEADS = frozenset(
    {
        "ASURANSI", "BANK", "DANA", "HOTEL", "INDUSTRI", "KOPERASI", "LEMBAGA",
        "PABRIK", "PEMBANGUNAN", "PERDAGANGAN", "PERUSAHAAN", "RUMAH", "SEMEN",
        "TAMBANG", "YAYASAN",
    }
)

BLOCKED_PHRASES = frozenset(
    {
        "BANK INDONESIA",
        "BURSA EFEK INDONESIA",
        "REPUBLIK INDONESIA",
        "BANK SENTRAL",
    }
)

_CONNECTORS = frozenset({"OF", "DAN", "DE", "THE", "AND", "EL", "AL"})

CORPUS_GENERIC_THRESHOLD = 8

_MIN_ALIAS_LEN = 5
_MIN_SOLO_WORD_LEN = 5
_MIN_SHORT_ALIAS_WORD_LEN = 3

_PUNCT = re.compile(r"[^A-Z0-9\s]+")
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Uppercases and strips punctuation so names and headlines compare cleanly."""
    return _SPACES.sub(" ", _PUNCT.sub(" ", text.upper())).strip()


def content_words(company_name: str) -> list[str]:
    """The name with legal scaffolding removed."""
    return [w for w in normalize(company_name).split(" ") if w and w not in _NOISE_WORDS]


def generic_words(company_names: Iterable[str]) -> frozenset[str]:
    """The seed list plus every word too many issuers share to be distinctive."""
    counts: Counter[str] = Counter()
    for name in company_names:
        counts.update(set(content_words(name)))
    shared = {w for w, n in counts.items() if n >= CORPUS_GENERIC_THRESHOLD}
    return GENERIC_SEED | shared


def _is_usable(alias: str, generic: frozenset[str]) -> bool:
    """Rejects name forms too short or too generic to identify one issuer."""
    words = alias.split(" ")
    if len(alias) < _MIN_ALIAS_LEN or alias in BLOCKED_PHRASES:
        return False
    if words[0] in _CONNECTORS or all(w in generic for w in words):
        return False
    if len(words) == 1:
        return len(alias) >= _MIN_SOLO_WORD_LEN and alias not in generic
    if len(words) == 2:
        return all(len(w) >= _MIN_SHORT_ALIAS_WORD_LEN for w in words)
    return True


def aliases_for(company_name: str, generic: frozenset[str] = GENERIC_SEED) -> list[str]:
    """The name forms worth searching a headline for, longest first."""
    words = content_words(company_name)
    if not words:
        return []

    candidates = [" ".join(words)]
    if len(words) >= 3 and words[0] in generic:
        candidates.append(" ".join(words[1:]))
    if len(words) >= 3 and words[0] not in CATEGORY_LEADS:
        candidates.append(" ".join(words[:2]))

    seen: dict[str, None] = {}
    for candidate in candidates:
        if _is_usable(candidate, generic):
            seen.setdefault(candidate, None)
    return sorted(seen, key=len, reverse=True)
