"""
Name matching for misspelled tables / columns.

Ranking uses two signals:
  1. edit distance with adjacent swaps (Damerau / OSA): "ABL" -> "ALB" costs 1 (one swap),
     while "ABL" -> "ALP" costs 2. Plain similarity ratios treat these two as equal.
  2. SequenceMatcher similarity as a tie-breaker and as a fallback for longer names.

The best candidate is accepted only if it is *clearly* better than the runner-up;
otherwise the match is reported as ambiguous so the repair layer can abstain instead of guessing
(e.g. "A1" is one edit away from both "A10" and "A11").
"""

from difflib import SequenceMatcher
from typing import Iterable, List, Optional, Tuple, TypeVar

T = TypeVar("T")


def normalize(name: str) -> str:
    return name.lower().replace("_", "").replace(" ", "")


def osa_distance(a: str, b: str) -> int:
    """Optimal string alignment distance (insert / delete / substitute / swap adjacent)."""
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


class Match:
    __slots__ = ("item", "name", "distance", "similarity")

    def __init__(self, item, name: str, distance: int, similarity: float):
        self.item, self.name, self.distance, self.similarity = item, name, distance, similarity

    def __repr__(self):
        return f"Match({self.name!r}, d={self.distance}, s={self.similarity:.2f})"


def rank(query: str, items: Iterable[Tuple[T, str]]) -> List[Match]:
    """Rank (item, name) pairs by closeness to query: smaller distance first, then higher similarity."""
    q = normalize(query)
    out = []
    for item, name in items:
        n = normalize(name)
        out.append(Match(item, name, osa_distance(q, n), SequenceMatcher(None, q, n).ratio()))
    out.sort(key=lambda m: (m.distance, -m.similarity))
    return out


def max_edits(query: str) -> int:
    return max(2, len(normalize(query)) // 4)


def acceptable(m: Match, query: str) -> bool:
    """A plausible typo (at most ~2 edits, scaled for long names) or a clearly similar name."""
    return m.distance <= max_edits(query) or m.similarity >= 0.6


def pick(query: str, items: Iterable[Tuple[T, str]], sim_margin: float = 0.05) -> Tuple[Optional[Match], bool, List[Match]]:
    """
    Returns (best_match_or_None, is_ambiguous, ranked_list).
    is_ambiguous=True when the runner-up is just as close (same distance and similarity within margin).
    """
    all_ranked = rank(query, items)
    ranked = [m for m in all_ranked if acceptable(m, query)]
    if not ranked:
        return None, False, []
    best = ranked[0]
    second = all_ranked[1] if len(all_ranked) > 1 else None
    if second is not None:
        if best.distance <= max_edits(query):
            # typo-like match: ambiguous only if the runner-up is an equally short edit away
            if second.distance == best.distance and best.similarity - second.similarity < sim_margin:
                return None, True, ranked
        else:
            # looser, similarity-based match: demand a clear similarity gap to the runner-up
            by_sim = sorted(all_ranked, key=lambda m: -m.similarity)
            if by_sim[0] is not best or best.similarity - by_sim[1].similarity < 0.1:
                return None, True, ranked
    return best, False, ranked
