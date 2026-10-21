"""Is this seller's listing the same product the user asked about?

Two layers, both deterministic:
  * token similarity: how many of the reference title's words the listing contains
    (listing titles are long, so we measure coverage of the *reference*);
  * hard attribute checks: model numbers ("RTX 5070", "iPhone 16"), capacities
    ("128GB") and screen sizes ("15.6") must not conflict, and variant words
    ("Pro", "Max", "Plus") or accessory words ("case", "cover") must not appear
    in the listing unless the user asked for them.

score >= ACCEPT -> "match"; REVIEW <= score < ACCEPT -> "possible" (the caller may
ask the LLM); below REVIEW -> "rejected".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ACCEPT = 0.75
REVIEW = 0.5

_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "the",
        "for",
        "with",
        "of",
        "in",
        "on",
        "at",
        "to",
        "by",
        "from",
        "new",
        "buy",
        "online",
        "best",
        "price",
        "prices",
        "india",
        "shop",
        "store",
        "official",
        "free",
        "shipping",
        "delivery",
        "offer",
        "offers",
        "deal",
        "deals",
        "sale",
        "only",
        "edition",
        "version",
        "model",
        "variant",
        "colour",
        "color",
        "laptop",
        "smartphone",
        "mobile",
        "phone",
    ]
)
# words that make a *different* product when they appear in the listing but not the reference
_VARIANTS = frozenset(["pro", "max", "plus", "ultra", "mini", "lite", "fe"])
_ACCESSORIES = frozenset(
    [
        "case",
        "cover",
        "covers",
        "pouch",
        "skin",
        "sleeve",
        "protector",
        "tempered",
        "guard",
        "charger",
        "adapter",
        "cable",
        "stand",
        "holder",
        "mount",
        "strap",
        "refurbished",
        "renewed",
        "used",
        "compatible",
        "replacement",
    ]
)
_UNIT_JOIN = re.compile(r"(\d+(?:\.\d+)?)\s+(gb|tb|mb|mah|hz|w|inch|in|cm|mm|kg|g|ml|l)\b")
_ALNUM_SPLIT = re.compile(r"\b([a-z]{2,})(\d{3,}[a-z]{0,2})\b")
_CAPACITY = re.compile(r"^(\d+(?:\.\d+)?)(gb|tb|mb)$")
_SIZE_TOKEN = re.compile(r"^(\d{1,2}\.\d)(?:inch|in|cm)?$|^(\d{2})(?:inch|in)$")
_NUMBERISH = re.compile(r"^\d+[a-z]{0,2}$")
_ROMAN = {"ii": "2", "iii": "3", "iv": "4"}


def normalise(text: str) -> list[str]:
    """Lower-case word tokens; '128 GB' -> '128gb', 'RTX5070' -> 'rtx 5070', '15.6"' -> '15.6'."""
    t = (text or "").lower()
    t = t.replace("”", '"').replace("″", '"').replace("’", "'")
    t = re.sub(r'(\d+(?:\.\d+)?)\s*(?:"|-inch\b|inches\b)', r"\1inch", t)
    t = re.sub(r"[^a-z0-9.]+", " ", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)  # keep decimal points only
    t = _UNIT_JOIN.sub(r"\1\2", t)
    t = _ALNUM_SPLIT.sub(r"\1 \2", t)
    t = re.sub(r"\b(\d+(?:\.\d+)?)tb\b", lambda m: f"{float(m.group(1)) * 1024:g}gb", t)  # 1tb == 1024gb
    return [_ROMAN.get(w, w) for w in t.split()]


def _content(tokens: list[str]) -> list[str]:
    return [w for w in tokens if w not in _STOP]


def _capacities(tokens: list[str]) -> set[str]:
    out = set()
    for w in tokens:
        m = _CAPACITY.match(w)
        if m:
            value, unit = float(m.group(1)), m.group(2)
            out.add(f"{value * 1024 if unit == 'tb' else value:g}gb" if unit != "mb" else f"{value:g}mb")
    return out


def _sizes(tokens: list[str]) -> set[str]:
    out = set()
    for w in tokens:
        m = _SIZE_TOKEN.match(w)
        if m:
            out.add(m.group(1) or m.group(2))
    return out


def _model_pairs(tokens: list[str]) -> dict[str, set[str]]:
    """{'rtx': {'5070'}, 'iphone': {'16'}} : an alphabetic word followed by a number-ish token."""
    pairs: dict[str, set[str]] = {}
    for prev, word in zip(tokens, tokens[1:], strict=False):
        if prev.isalpha() and prev not in _STOP and _NUMBERISH.match(word) and not _CAPACITY.match(word):
            pairs.setdefault(prev, set()).add(word)
    return pairs


@dataclass(frozen=True)
class Match:
    score: float
    verdict: str  # match | possible | rejected
    reason: str
    hard: bool = False  # rejected by a hard rule (conflicting model/capacity/size, variant, accessory)


def _verdict(score: float) -> str:
    if score >= ACCEPT:
        return "match"
    if score >= REVIEW:
        return "possible"
    return "rejected"


def match_product(reference: str, candidate: str) -> Match:
    ref_all = normalise(reference)
    cand_all = normalise(candidate)
    ref, cand = _content(ref_all), _content(cand_all)
    if not ref or not cand:
        return Match(0.0, "rejected", "no title to compare")
    cand_set = set(cand)
    found = [w for w in ref if w in cand_set]
    coverage = len(found) / len(ref)
    # a short listing title fully contained in a long reference title is also a good sign
    ref_unique = set(ref)
    cand_cov = len([w for w in set(cand) if w in ref_unique]) / len(set(cand))
    score = max(coverage, 0.9 * cand_cov if len(set(cand)) >= 3 and cand_cov >= 0.75 else 0.0)
    reasons: list[str] = []
    missing = [w for w in ref if w not in cand_set]
    if missing:
        reasons.append("missing " + ", ".join(missing[:4]))
        if any(ch.isdigit() for w in missing for ch in w):  # a model number or capacity we could not confirm
            score = min(score, 0.7)

    ref_set = set(ref_all)
    # hard conflicts: same family word, different number (RTX 5070 vs RTX 5080, iPhone 16 vs 15)
    conflicts: list[str] = []
    rp, cp = _model_pairs(ref_all), _model_pairs(cand_all)
    for family, numbers in rp.items():
        theirs = cp.get(family)
        if theirs and not (numbers & theirs):
            conflicts.append(f"{family} {'/'.join(sorted(numbers))} vs {'/'.join(sorted(theirs))}")
    rc, cc = _capacities(ref_all), _capacities(cand_all)
    if rc and cc and not rc <= cc:
        conflicts.append(f"{'/'.join(sorted(rc))} vs {'/'.join(sorted(cc))}")
    rs, cs = _sizes(ref_all), _sizes(cand_all)
    if rs and cs and not (rs & cs):
        conflicts.append(f'{"/".join(sorted(rs))}" vs {"/".join(sorted(cs))}"')
    if conflicts:
        score = min(score, 0.3)
        reasons.insert(0, "conflict: " + "; ".join(conflicts))

    extra_variants = sorted({w for w in cand_all if w in _VARIANTS} - ref_set)
    if extra_variants:
        score = min(score, 0.4)
        reasons.insert(0, "different variant: " + ", ".join(extra_variants))
    accessories = sorted({w for w in cand_all if w in _ACCESSORIES} - ref_set)
    if accessories:
        score = min(score, 0.2)
        reasons.insert(0, "accessory or used item: " + ", ".join(accessories))

    score = round(max(0.0, min(1.0, score)), 2)
    if not reasons:
        reasons.append("all key words match")
    hard = bool(conflicts or extra_variants or accessories)
    return Match(score, _verdict(score), "; ".join(reasons)[:200], hard)
