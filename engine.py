"""
WorldCup 2026 Predictor — "The Oracle"
=======================================
Core prediction engine.

Pipeline:
  1. Build recency- and importance-weighted Elo ratings from ~150 years of
     international results (martj42 dataset).
  2. Convert Elo difference into a Poisson goal model to get full scoreline
     probabilities for any matchup.
  3. Monte Carlo simulate the actual 2026 World Cup bracket (48-team format)
     thousands of times to produce each team's odds to advance / win the cup.

Why these choices:
  - Elo is the gold standard for international football strength: it self-
    corrects, rewards beating strong teams, and handles the long tail of
    minnow vs giant matchups gracefully.
  - Poisson is the classic goal-count model; pairing it with Elo gives us
    realistic scorelines rather than just win/draw/loss labels.
  - Monte Carlo is the only honest way to turn per-match odds into a
    tournament-wide "chance to win it all" number, because the bracket path
    matters (who you might meet in the round of 16, etc.).
"""

import csv
import math
import random
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
RESULTS_CSV = DATA_DIR / "results.csv"

# --- Elo tuning constants -------------------------------------------------
# K-factor controls how fast ratings move. We scale it by match importance
# below, so this is the baseline for a normal match.
BASE_K = 32.0
# Home advantage expressed in Elo points. ~65 is the well-established value
# for international football; it materially shifts win probability.
HOME_ADVANTAGE = 65.0
# Starting rating for a team we have never seen before.
INITIAL_ELO = 1500.0

# Tournament importance multipliers. A World Cup result should teach the model
# far more than a meaningless friendly. These multiply the K-factor.
TOURNAMENT_WEIGHT = {
    "FIFA World Cup": 3.0,
    "FIFA World Cup qualification": 2.0,
    "UEFA Euro": 2.5,
    "UEFA Euro qualification": 1.75,
    "Copa América": 2.5,
    "African Cup of Nations": 2.0,
    "AFC Asian Cup": 2.0,
    "Gold Cup": 1.75,
    "UEFA Nations League": 1.75,
    "Confederations Cup": 2.0,
    "Friendly": 1.0,
}
DEFAULT_WEIGHT = 1.25  # anything not listed: a bit above a friendly


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _importance(tournament: str) -> float:
    return TOURNAMENT_WEIGHT.get(tournament, DEFAULT_WEIGHT)


def _margin_multiplier(goal_diff: int) -> float:
    """Blowouts should move ratings more, but with diminishing returns.

    This is the standard World Football Elo margin-of-victory adjustment: a
    7-0 win is more informative than a 1-0 win, but not 7x more.
    """
    if goal_diff <= 1:
        return 1.0
    if goal_diff == 2:
        return 1.5
    return (11.0 + goal_diff) / 8.0


class EloModel:
    """Holds team ratings and the logic to update them match by match."""

    def __init__(self):
        # current rating per team
        self.ratings: dict[str, float] = defaultdict(lambda: INITIAL_ELO)
        # number of matches seen per team (confidence proxy)
        self.matches_played: dict[str, int] = defaultdict(int)
        self.last_played: dict[str, date] = {}

    def expected_score(self, rating_a: float, rating_b: float) -> float:
        """Classic Elo expectation: probability A beats B (draw counts as 0.5)."""
        return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))

    def update(self, home, away, hs, as_, tournament, neutral, match_date):
        """Process one historical match and nudge both teams' ratings."""
        ra = self.ratings[home]
        rb = self.ratings[away]

        # Apply home advantage only when the match is not on neutral ground.
        ha = 0.0 if neutral else HOME_ADVANTAGE
        exp_home = self.expected_score(ra + ha, rb)

        # Actual result in Elo terms: win=1, draw=0.5, loss=0.
        if hs > as_:
            actual_home = 1.0
        elif hs < as_:
            actual_home = 0.0
        else:
            actual_home = 0.5

        # K scales with tournament importance and margin of victory.
        k = BASE_K * _importance(tournament) * _margin_multiplier(abs(hs - as_))

        delta = k * (actual_home - exp_home)
        self.ratings[home] = ra + delta
        self.ratings[away] = rb - delta

        for t in (home, away):
            self.matches_played[t] += 1
            self.last_played[t] = match_date

    def train(self, rows):
        """Feed all historical (played) matches in chronological order."""
        for r in rows:
            if r["home_score"] == "NA" or r["away_score"] == "NA":
                continue
            try:
                hs = int(r["home_score"])
                as_ = int(r["away_score"])
            except ValueError:
                continue
            neutral = r["neutral"].strip().upper() == "TRUE"
            self.update(
                r["home_team"], r["away_team"], hs, as_,
                r["tournament"], neutral, _parse_date(r["date"]),
            )


def load_results():
    with open(RESULTS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# --- Poisson goal model ---------------------------------------------------
# We map the Elo win expectation onto an expected-goals figure, then draw
# actual goals from a Poisson distribution. AVG_GOALS is the long-run mean
# total goals in an international match; we split it between the teams in
# proportion to their relative strength.
AVG_GOALS = 2.6


def expected_goals(elo_a, elo_b, home_adv=0.0):
    """Return (xg_a, xg_b) expected goals for each side given Elo ratings."""
    diff = (elo_a + home_adv) - elo_b
    # Logistic share of the total goal supply, tuned so a 0 diff => 50/50.
    share_a = 1.0 / (1.0 + 10 ** (-diff / 400.0))
    xg_a = AVG_GOALS * share_a
    xg_b = AVG_GOALS * (1.0 - share_a)
    # Floor so even huge underdogs have a puncher's chance.
    return max(xg_a, 0.15), max(xg_b, 0.15)


def _poisson(lmbda: float) -> int:
    """Knuth's algorithm: draw a Poisson random goal count."""
    L = math.exp(-lmbda)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= random.random()
        if p <= L:
            return k - 1


def simulate_match(elo_a, elo_b, home_adv=0.0, allow_draw=True):
    """Simulate one match. Returns (goals_a, goals_b, winner) where winner is
    'A', 'B', or 'draw'. In knockouts (allow_draw=False) we resolve ties via
    an Elo-weighted coin flip standing in for extra time + penalties."""
    xg_a, xg_b = expected_goals(elo_a, elo_b, home_adv)
    ga, gb = _poisson(xg_a), _poisson(xg_b)
    if ga > gb:
        return ga, gb, "A"
    if gb > ga:
        return ga, gb, "B"
    if allow_draw:
        return ga, gb, "draw"
    # Knockout tie-break: stronger team more likely to survive penalties.
    p_a = 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400.0))
    return ga, gb, ("A" if random.random() < p_a else "B")


def outcome_probabilities(elo_a, elo_b, home_adv=0.0, n=20000):
    """Monte Carlo a single matchup to get win/draw/loss + modal scoreline."""
    wins_a = draws = wins_b = 0
    scorelines = defaultdict(int)
    for _ in range(n):
        ga, gb, w = simulate_match(elo_a, elo_b, home_adv, allow_draw=True)
        scorelines[(ga, gb)] += 1
        if w == "A":
            wins_a += 1
        elif w == "B":
            wins_b += 1
        else:
            draws += 1
    modal = max(scorelines.items(), key=lambda kv: kv[1])[0]
    return {
        "win_a": wins_a / n,
        "draw": draws / n,
        "win_b": wins_b / n,
        "likely_score": modal,
    }


if __name__ == "__main__":
    rows = load_results()
    model = EloModel()
    model.train(rows)

    # Sanity check: print the current top 20 teams by Elo.
    ranked = sorted(model.ratings.items(), key=lambda kv: kv[1], reverse=True)
    print("=== Top 20 international sides by weighted Elo (2026) ===")
    for i, (team, rating) in enumerate(ranked[:20], 1):
        print(f"{i:2d}. {team:<24} {rating:7.1f}  ({model.matches_played[team]} matches)")
