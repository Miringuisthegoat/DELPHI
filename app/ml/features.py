"""
Phase 5: feature engineering for the DELPHI prediction engine.

`PlayerFeatureBuilder` turns everything the database knows about a player
*as of* a given moment into a flat, numeric `FeatureVector` that both the
heuristic predictor and the Random Forest model consume identically. This
is the one place that "what does DELPHI look at" is defined, so the two
predictors in `app.ml.model` never drift from each other and a future
model swap (e.g. XGBoost) only needs this vector, nothing bespoke.

Strict no-lookahead rule
------------------------
Every rolling statistic (`points_avg_3`, `form`, `rotation_risk`, ...) is
computed only from `PlayerGameweekStats` rows strictly *before* the
gameweek being predicted (`gameweek < target_gameweek`). Fixture-specific
fields (`fixture_difficulty`, `is_home`, opponent strengths) describe the
*target* gameweek's fixture(s), since those are known in advance. Mixing
the two up would leak the outcome being predicted into the input and make
any offline accuracy metric meaningless.

Cold start
----------
Early in a season (or in preseason, before any gameweek has been played)
a player may have zero prior `PlayerGameweekStats` rows. Every rolling
field then defaults to 0.0 rather than raising - `FeatureVector.has_history`
is `False` in that case, which is exactly the signal `DelphiPredictionEngine`
uses to prefer the heuristic predictor over an undertrained Random Forest.

Phase 13: defensive contribution
---------------------------------
2025-26 introduced a new points source - defenders/midfielders/forwards
who cross a per-gameweek CBIT (clearances+blocks+interceptions+tackles)
threshold earn 2 bonus points. Four rolling features
(`cbi_avg_5`, `tackles_avg_5`, `recoveries_avg_5`,
`defensive_contribution_avg_5`) capture a player's recent tendency to hit
that threshold. Seasons/rows before this rule existed simply have these
at 0 - correct, not missing data (see `PlayerGameweekStats`'s Phase 13
docstring). Adding these to `FEATURE_NAMES` changes the model's expected
input shape, so any previously-saved Random Forest artifact will fail its
`stored_features != FEATURE_NAMES` check on load (see `model.py`) and
must be retrained - this is intentional, not a bug, since an old model
has no idea this signal exists.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from statistics import pstdev

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import InjuryStatus, Position
from app.models.fixture import Fixture
from app.models.player import Player
from app.models.player_stats import PlayerGameweekStats
from app.models.team import Team

# Feature names, in the exact order fed to the model matrix. Kept as a
# module-level constant (rather than re-derived from the dataclass every
# call) so `ModelTrainingService` and `DelphiPredictionEngine` are
# guaranteed to build X in the same column order.
FEATURE_NAMES: tuple[str, ...] = (
    "price_millions",
    "ownership_percent",
    "price_trend",
    "is_gkp",
    "is_def",
    "is_mid",
    "is_fwd",
    "minutes_avg_3",
    "minutes_avg_5",
    "minutes_avg_season",
    "points_avg_3",
    "points_avg_5",
    "points_avg_season",
    "form_weighted",
    "goals_avg_5",
    "assists_avg_5",
    "clean_sheets_avg_5",
    "goals_conceded_avg_5",
    "bonus_avg_5",
    "bps_avg_5",
    "ict_index_avg_5",
    "influence_avg_5",
    "creativity_avg_5",
    "threat_avg_5",
    "cbi_avg_5",
    "tackles_avg_5",
    "recoveries_avg_5",
    "defensive_contribution_avg_5",
    "rotation_risk",
    "expected_minutes_probability",
    "fixture_difficulty",
    "is_home",
    "num_fixtures_this_gw",
    "team_strength_attack",
    "team_strength_defence",
    "opponent_strength_attack",
    "opponent_strength_defence",
)


@dataclass
class FeatureVector:
    """One player's feature row for one target gameweek.

    `has_history` and `gameweeks_of_history` aren't fed to the model but
    are used by the engine to decide whether a trained model can be
    trusted for this player, or whether to fall back to the heuristic.
    """

    player_id: int
    target_gameweek: int
    position: Position

    price_millions: float = 0.0
    ownership_percent: float = 0.0
    price_trend: float = 0.0

    is_gkp: float = 0.0
    is_def: float = 0.0
    is_mid: float = 0.0
    is_fwd: float = 0.0

    minutes_avg_3: float = 0.0
    minutes_avg_5: float = 0.0
    minutes_avg_season: float = 0.0

    points_avg_3: float = 0.0
    points_avg_5: float = 0.0
    points_avg_season: float = 0.0
    form_weighted: float = 0.0

    goals_avg_5: float = 0.0
    assists_avg_5: float = 0.0
    clean_sheets_avg_5: float = 0.0
    goals_conceded_avg_5: float = 0.0
    bonus_avg_5: float = 0.0
    bps_avg_5: float = 0.0
    ict_index_avg_5: float = 0.0
    influence_avg_5: float = 0.0
    creativity_avg_5: float = 0.0
    threat_avg_5: float = 0.0

    # --- Phase 13: defensive contribution (2025-26+ scoring rules) -------
    cbi_avg_5: float = 0.0
    """Recent average clearances+blocks+interceptions per gameweek."""
    tackles_avg_5: float = 0.0
    recoveries_avg_5: float = 0.0
    defensive_contribution_avg_5: float = 0.0
    """Recent average of FPL's own defensive-contribution bonus-points
    indicator - i.e. how often this player has been crossing the CBIT
    threshold lately, not the raw CBI count itself."""

    rotation_risk: float = 0.0
    expected_minutes_probability: float = 1.0

    fixture_difficulty: float = 3.0
    is_home: float = 0.0
    num_fixtures_this_gw: float = 1.0
    team_strength_attack: float = 1100.0
    team_strength_defence: float = 1100.0
    opponent_strength_attack: float = 1100.0
    opponent_strength_defence: float = 1100.0

    gameweeks_of_history: int = 0

    @property
    def has_history(self) -> bool:
        """Whether this player has any recorded `PlayerGameweekStats`."""
        return self.gameweeks_of_history > 0

    def to_row(self) -> list[float]:
        """Return the numeric feature values in `FEATURE_NAMES` order."""
        data = asdict(self)
        return [float(data[name]) for name in FEATURE_NAMES]

    def as_dict(self) -> dict[str, float]:
        """All non-identity fields, useful for building a training DataFrame."""
        data = asdict(self)
        return {name: float(data[name]) for name in FEATURE_NAMES}


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _weighted_form(points: list[float]) -> float:
    """Exponentially decayed recent form, most recent gameweek weighted highest.

    `points` must already be ordered oldest -> newest. Mirrors the intent
    of FPL's own "form" figure (a recency-weighted scoring rate) but is
    computed from our own stored history so it's available even when the
    live `Player.form` field hasn't been refreshed for the day.
    """
    if not points:
        return 0.0
    recent = points[-5:]
    decay = 0.65
    weights = [decay**i for i in range(len(recent) - 1, -1, -1)]
    weighted_sum = sum(w * p for w, p in zip(weights, recent))
    return weighted_sum / sum(weights)


_EXPECTED_MINUTES_BY_STATUS: dict[InjuryStatus, float] = {
    InjuryStatus.AVAILABLE: 1.0,
    InjuryStatus.DOUBTFUL: 0.5,
    InjuryStatus.INJURED: 0.0,
    InjuryStatus.SUSPENDED: 0.0,
    InjuryStatus.UNAVAILABLE: 0.0,
}


def _expected_minutes_probability(player: Player) -> float:
    """Best estimate of the probability this player plays meaningful minutes."""
    if player.chance_of_playing_next_round is not None:
        return max(0.0, min(1.0, player.chance_of_playing_next_round / 100.0))
    return _EXPECTED_MINUTES_BY_STATUS.get(player.status, 1.0)


class PlayerFeatureBuilder:
    """Builds `FeatureVector`s from the database for one or many players.

    Stateless aside from the `Session` passed per call, matching the
    convention set by `DataIngestionService` in Phase 4.
    """

    def build(
        self, db: Session, player: Player, target_gameweek: int
    ) -> FeatureVector:
        """Build the feature vector for `player` ahead of `target_gameweek`.

        Args:
            db: Active SQLAlchemy session.
            player: The player to build features for.
            target_gameweek: The gameweek being predicted. Only stats from
                strictly earlier gameweeks are used for rolling features.
        """
        history = (
            db.execute(
                select(PlayerGameweekStats)
                .where(
                    PlayerGameweekStats.player_id == player.id,
                    PlayerGameweekStats.gameweek < target_gameweek,
                )
                .order_by(PlayerGameweekStats.gameweek.asc())
            )
            .scalars()
            .all()
        )

        vector = FeatureVector(
            player_id=player.id,
            target_gameweek=target_gameweek,
            position=player.position,
            price_millions=player.price_millions,
            ownership_percent=player.ownership_percent,
            price_trend=player.price_trend,
            is_gkp=float(player.position == Position.GKP),
            is_def=float(player.position == Position.DEF),
            is_mid=float(player.position == Position.MID),
            is_fwd=float(player.position == Position.FWD),
            expected_minutes_probability=_expected_minutes_probability(player),
            gameweeks_of_history=len(history),
        )

        if history:
            last_3 = history[-3:]
            last_5 = history[-5:]

            minutes_all = [float(h.minutes) for h in history]
            points_all = [float(h.total_points) for h in history]

            vector.minutes_avg_3 = _avg([float(h.minutes) for h in last_3])
            vector.minutes_avg_5 = _avg([float(h.minutes) for h in last_5])
            vector.minutes_avg_season = _avg(minutes_all)

            vector.points_avg_3 = _avg([float(h.total_points) for h in last_3])
            vector.points_avg_5 = _avg([float(h.total_points) for h in last_5])
            vector.points_avg_season = _avg(points_all)
            vector.form_weighted = _weighted_form(points_all)

            vector.goals_avg_5 = _avg([float(h.goals_scored) for h in last_5])
            vector.assists_avg_5 = _avg([float(h.assists) for h in last_5])
            vector.clean_sheets_avg_5 = _avg([float(h.clean_sheets) for h in last_5])
            vector.goals_conceded_avg_5 = _avg(
                [float(h.goals_conceded) for h in last_5]
            )
            vector.bonus_avg_5 = _avg([float(h.bonus) for h in last_5])
            vector.bps_avg_5 = _avg([float(h.bps) for h in last_5])
            vector.ict_index_avg_5 = _avg([float(h.ict_index) for h in last_5])
            vector.influence_avg_5 = _avg([float(h.influence) for h in last_5])
            vector.creativity_avg_5 = _avg([float(h.creativity) for h in last_5])
            vector.threat_avg_5 = _avg([float(h.threat) for h in last_5])

            # Phase 13: defensive contribution rolling averages. Rows from
            # before the rule existed just contribute 0s, which correctly
            # dilutes the average toward "no defensive-contribution
            # history" rather than needing special-casing here.
            vector.cbi_avg_5 = _avg(
                [float(h.clearances_blocks_interceptions) for h in last_5]
            )
            vector.tackles_avg_5 = _avg([float(h.tackles) for h in last_5])
            vector.recoveries_avg_5 = _avg([float(h.recoveries) for h in last_5])
            vector.defensive_contribution_avg_5 = _avg(
                [float(h.defensive_contribution) for h in last_5]
            )

            vector.rotation_risk = (
                pstdev(minutes_all[-5:]) if len(minutes_all[-5:]) > 1 else 0.0
            )

        fixtures = self._fixtures_for_gameweek(db, player.team_id, target_gameweek)
        self._apply_fixture_context(db, vector, player, fixtures)

        return vector

    def build_many(
        self, db: Session, players: list[Player], target_gameweek: int
    ) -> list[FeatureVector]:
        """Convenience batch wrapper around `build`."""
        return [self.build(db, player, target_gameweek) for player in players]

    @staticmethod
    def _fixtures_for_gameweek(
        db: Session, team_id: int, gameweek: int
    ) -> list[Fixture]:
        return (
            db.execute(
                select(Fixture).where(
                    Fixture.gameweek == gameweek,
                    (Fixture.home_team_id == team_id)
                    | (Fixture.away_team_id == team_id),
                )
            )
            .scalars()
            .all()
        )

    @staticmethod
    def _apply_fixture_context(
        db: Session,
        vector: FeatureVector,
        player: Player,
        fixtures: list[Fixture],
    ) -> None:
        """Populate fixture/opponent/team-strength fields on `vector`.

        A blank gameweek (no fixtures) leaves `num_fixtures_this_gw` at 0
        and keeps a neutral difficulty so the model doesn't extrapolate
        wildly; a double gameweek (`len(fixtures) == 2`) averages the
        per-fixture difficulty and is flagged via `num_fixtures_this_gw`
        so both predictors can scale expected points up accordingly.
        """
        team = db.get(Team, player.team_id)
        vector.team_strength_attack = float(_team_attack(team))
        vector.team_strength_defence = float(_team_defence(team))

        if not fixtures:
            vector.num_fixtures_this_gw = 0.0
            return

        vector.num_fixtures_this_gw = float(len(fixtures))

        difficulties: list[float] = []
        home_flags: list[float] = []
        opp_attacks: list[float] = []
        opp_defences: list[float] = []

        for fixture in fixtures:
            is_home = fixture.home_team_id == player.team_id
            opponent_id = fixture.away_team_id if is_home else fixture.home_team_id
            opponent = db.get(Team, opponent_id)

            difficulties.append(float(fixture.difficulty_for(player.team_id)))
            home_flags.append(1.0 if is_home else 0.0)
            opp_attacks.append(float(_team_attack(opponent)))
            opp_defences.append(float(_team_defence(opponent)))

        vector.fixture_difficulty = _avg(difficulties)
        vector.is_home = _avg(home_flags)
        vector.opponent_strength_attack = _avg(opp_attacks)
        vector.opponent_strength_defence = _avg(opp_defences)


def _team_attack(team: Team | None) -> int:
    """Team attack strength with a neutral preseason fallback.

    `Team.strength_attack` is 0 until FPL populates its strength ratings
    (typically once a handful of gameweeks have been played), so a neutral
    league-average-ish constant (1100, matching FPL's own ~1000-1350
    scale) avoids the model reading "0 strength" as "this team never
    scores" during preseason/very early season.
    """
    if team is None or not team.strength_attack:
        return 1100
    return team.strength_attack


def _team_defence(team: Team | None) -> int:
    if team is None or not team.strength_defence:
        return 1100
    return team.strength_defence
