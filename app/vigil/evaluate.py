"""Pure condition evaluation and fire-on-transition logic."""

from __future__ import annotations

import difflib
from dataclasses import dataclass


def condition_true(
    kind: str,
    *,
    value: float | None = None,
    threshold: float | None = None,
    keyword: str | None = None,
    text: str = "",
    prev_hash: str | None = None,
    new_hash: str | None = None,
) -> bool:
    if kind == "price_below":
        return value is not None and threshold is not None and value < threshold
    if kind == "price_above":
        return value is not None and threshold is not None and value > threshold
    if kind == "text_appears":
        return bool(keyword) and keyword.lower() in (text or "").lower()
    if kind == "text_disappears":
        return bool(keyword) and keyword.lower() not in (text or "").lower()
    if kind == "page_changed":
        return prev_hash is not None and new_hash is not None and prev_hash != new_hash
    raise ValueError(f"unknown kind: {kind}")


@dataclass(frozen=True)
class Evaluation:
    condition: bool  # is the condition true right now?
    fire: bool  # should we notify?
    status: str  # baseline | unchanged | changed | triggered
    condition_met: bool  # value to store for the next check


def evaluate(
    kind: str,
    *,
    prev_condition_met: bool,
    prev_hash: str | None,
    new_hash: str,
    value: float | None = None,
    threshold: float | None = None,
    keyword: str | None = None,
    text: str = "",
) -> Evaluation:
    """Decide status and whether to notify.

    Fires only on the transition not-met -> met, and re-arms when the condition
    becomes false again. page_changed fires on every change after the baseline.
    """
    cond = condition_true(
        kind, value=value, threshold=threshold, keyword=keyword, text=text, prev_hash=prev_hash, new_hash=new_hash
    )
    if kind == "page_changed":
        if prev_hash is None:
            return Evaluation(False, False, "baseline", False)
        if not cond:
            return Evaluation(False, False, "unchanged", False)
        return Evaluation(True, True, "triggered", True)

    fire = cond and not prev_condition_met
    if fire:
        status = "triggered"
    elif prev_hash is None:
        status = "baseline"
    elif prev_hash == new_hash:
        status = "unchanged"
    else:
        status = "changed"
    return Evaluation(cond, fire, status, cond)


def diff_summary(old_text: str, new_text: str, max_lines: int = 3) -> str:
    """Deterministic change summary: '+N/-M lines' plus the first added lines."""
    old = (old_text or "").splitlines()
    new = (new_text or "").splitlines()
    added: list[str] = []
    removed: list[str] = []
    for line in difflib.unified_diff(old, new, lineterm="", n=0):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith("+"):
            added.append(line[1:].strip())
        elif line.startswith("-"):
            removed.append(line[1:].strip())
    head = f"+{len(added)}/-{len(removed)} lines"
    shown = [a[:120] for a in added if a][:max_lines]
    if not shown and removed:
        return head + "; removed: " + " | ".join(r[:120] for r in removed[:max_lines])
    if not shown:
        return head
    return head + "; new: " + " | ".join(shown)


def unified_diff_text(old_text: str, new_text: str, limit: int = 4000) -> str:
    lines = difflib.unified_diff((old_text or "").splitlines(), (new_text or "").splitlines(), lineterm="", n=1)
    out = "\n".join(lines)
    return out[:limit]
