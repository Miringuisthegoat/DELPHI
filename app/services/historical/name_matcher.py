"""
Phase 12: resolves a historical `source_name` (e.g. "Son Heung-min") to a
current-season `Player.id`, where possible.

Match strategy, in preference order (see module docstring in
`player_stats_historical.py` for why this is necessary at all - FPL's
`element` id is not stable across seasons):

1. **Exact match on `web_name`** - cheap, and covers most regulars since
   vaastav's `name` column is usually close to FPL's own display name.
2. **Exact match on normalised full name** (`first_name second_name`,
   lowercased, diacritics stripped) - catches cases where `web_name`
   differs (e.g. shortened) but the full name lines up.
3. **Fuzzy match** (via `rapidfuzz`) above `_FUZZY_THRESHOLD` - catches
   spelling variants, hyphenation differences, etc. Anything below the
   threshold is left unmatched rather than guessed.

Every match records `match_method` and `match_confidence` on the
resulting row (see `service.py`), so a low-confidence or fuzzy match is
always visible/auditable later, never silently trusted at the same level
as an exact match.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

from app.models.player import Player

logger = logging.getLogger(__name__)

_FUZZY_THRESHOLD = 88.0
"""Minimum rapidfuzz token-sort-ratio (0-100) to accept a fuzzy match.
Deliberately conservative - a false match corrupts a player's career-
prior features, which is worse than leaving a row unmatched."""


@dataclass
class MatchResult:
    player_id: int | None
    confidence: float
    method: str
    """One of 'exact_web_name', 'exact_full_name', 'fuzzy', 'unmatched'."""


def _normalise(name: str) -> str:
    """Lowercase, strip diacritics/accents, collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


class PlayerNameMatcher:
    """Matches historical player names against the current `players` table.

    Built once per ingestion run from the full current `Player` list
    (cheap - a few hundred rows), so matching many historical rows
    doesn't mean a database query per row.
    """

    def __init__(self, current_players: list[Player]) -> None:
        self._by_web_name: dict[str, int] = {}
        self._by_full_name: dict[str, int] = {}
        self._full_names: list[tuple[str, int]] = []

        for player in current_players:
            web_key = _normalise(player.web_name)
            full_key = _normalise(player.full_name)
            # First-write-wins on collisions (rare - e.g. two "Fernandes"s);
            # collisions are safer left ambiguous than overwritten silently.
            self._by_web_name.setdefault(web_key, player.id)
            self._by_full_name.setdefault(full_key, player.id)
            self._full_names.append((full_key, player.id))

    def match(self, source_name: str) -> MatchResult:
        """Resolve one historical `source_name` to a current player, if possible."""
        normalised = _normalise(source_name)

        if normalised in self._by_web_name:
            return MatchResult(
                player_id=self._by_web_name[normalised],
                confidence=1.0,
                method="exact_web_name",
            )

        if normalised in self._by_full_name:
            return MatchResult(
                player_id=self._by_full_name[normalised],
                confidence=1.0,
                method="exact_full_name",
            )

        best_score = 0.0
        best_player_id: int | None = None
        for full_key, player_id in self._full_names:
            score = fuzz.token_sort_ratio(normalised, full_key)
            if score > best_score:
                best_score = score
                best_player_id = player_id

        if best_score >= _FUZZY_THRESHOLD:
            return MatchResult(
                player_id=best_player_id,
                confidence=round(best_score / 100.0, 3),
                method="fuzzy",
            )

        return MatchResult(player_id=None, confidence=0.0, method="unmatched")
