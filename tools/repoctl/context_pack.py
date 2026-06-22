from __future__ import annotations

from .context_model import ContextCandidate


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def pack_candidates(candidates: list[ContextCandidate], *, budget_tokens: int) -> tuple[list[ContextCandidate], dict[str, int]]:
    packed: list[ContextCandidate] = []
    used = 0
    for candidate in candidates:
        cost = estimate_tokens(candidate.text)
        if used + cost > budget_tokens:
            continue
        packed.append(candidate)
        used += cost
    return packed, {"requested_tokens": budget_tokens, "estimated_tokens": used, "candidate_count": len(candidates), "packed_count": len(packed)}
