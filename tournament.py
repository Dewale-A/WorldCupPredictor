"""
2026 World Cup tournament structure + Monte Carlo simulation.

Format (the new 48-team format):
  - 12 groups of 4 teams, single round-robin (3 matches each).
  - Top 2 of every group advance (24 teams) PLUS the 8 best 3rd-placed teams.
  - That makes a 32-team straight knockout (Round of 32 -> ... -> Final).

We infer the 12 groups directly from the fixture list: the four teams that
all play each other in the group phase form a group. This keeps us honest to
whatever the dataset actually says rather than hard-coding a bracket that
might drift.
"""

import csv
import random
from collections import defaultdict
from datetime import date
from pathlib import Path

from engine import (
    EloModel, load_results, _parse_date, simulate_match,
)

DATA_DIR = Path(__file__).parent / "data"
RESULTS_CSV = DATA_DIR / "results.csv"

TOURNAMENT_TAG = "FIFA World Cup"
TOURNAMENT_START = date(2026, 6, 11)
GROUP_STAGE_END = date(2026, 6, 27)  # last group match date in the data


def load_tournament_matches():
    """Return the list of actual-tournament matches (group stage)."""
    matches = []
    with open(RESULTS_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = _parse_date(r["date"])
            if r["tournament"] == TOURNAMENT_TAG and TOURNAMENT_START <= d <= GROUP_STAGE_END:
                matches.append(r)
    return matches


def infer_groups(matches):
    """Infer the 12 groups of 4 from who-plays-whom in the group stage.

    Build an adjacency map of opponents; each team's group is itself plus the
    teams it faces. With a clean round-robin that yields exactly 4 teams.
    """
    opponents = defaultdict(set)
    for m in matches:
        h, a = m["home_team"], m["away_team"]
        opponents[h].add(a)
        opponents[a].add(h)

    groups = []
    seen = set()
    for team in opponents:
        if team in seen:
            continue
        members = {team} | opponents[team]
        # A clean group is a set of 4 mutually-connected teams.
        if len(members) == 4:
            groups.append(sorted(members))
            seen |= members
    return groups


class Group:
    """A single group: tracks fixtures and computes the final table."""

    def __init__(self, teams, fixtures):
        self.teams = teams
        self.fixtures = fixtures  # list of (home, away, hs|None, as|None)

    def play(self, elo, rng_results):
        """Simulate (or use real results for) all fixtures, return standings.

        rng_results: dict storing scenario outcomes (unused placeholder kept
        for symmetry). Returns ordered list of (team, points, gd, gf).
        """
        pts = dict.fromkeys(self.teams, 0)
        gf = dict.fromkeys(self.teams, 0)
        ga = dict.fromkeys(self.teams, 0)

        for home, away, hs, as_ in self.fixtures:
            if hs is not None and as_ is not None:
                # Real, already-played result: honor it exactly.
                gh, gaway = hs, as_
            else:
                gh, gaway, _ = simulate_match(
                    elo[home], elo[away], home_adv=0.0, allow_draw=True
                )
            gf[home] += gh
            ga[home] += gaway
            gf[away] += gaway
            ga[away] += gh
            if gh > gaway:
                pts[home] += 3
            elif gaway > gh:
                pts[away] += 3
            else:
                pts[home] += 1
                pts[away] += 1

        # Rank: points, then goal difference, then goals for, then a coin flip
        # (real WC uses more tie-breakers but this is a fair approximation).
        table = sorted(
            self.teams,
            key=lambda t: (pts[t], gf[t] - ga[t], gf[t], random.random()),
            reverse=True,
        )
        return [(t, pts[t], gf[t] - ga[t], gf[t]) for t in table]


def build_groups():
    matches = load_tournament_matches()
    group_lists = infer_groups(matches)

    # Manually entered live results override the dataset, so the model reflects
    # real matches before the upstream feed catches up. Keyed by unordered pair.
    try:
        from live_results import overrides_by_pair
        live_overrides = overrides_by_pair()
    except Exception:
        # If the overlay module or file is unavailable, fall back to dataset only.
        live_overrides = {}

    # Index fixtures by frozenset of the group's teams.
    fixtures_by_group = defaultdict(list)
    group_key = {}
    for members in group_lists:
        for t in members:
            group_key[t] = tuple(members)

    for m in matches:
        h, a = m["home_team"], m["away_team"]
        key = group_key.get(h)
        if key and group_key.get(a) == key:
            hs = None if m["home_score"] == "NA" else int(m["home_score"])
            as_ = None if m["away_score"] == "NA" else int(m["away_score"])
            # Apply a live override for this pairing if one exists. We re-map the
            # entered score onto this fixture's home/away order so it stays correct
            # regardless of which side the user typed as home.
            override = live_overrides.get(frozenset((h, a)))
            if override is not None:
                o_home, o_away, o_hs, o_as = override
                if o_home == h and o_away == a:
                    hs, as_ = o_hs, o_as
                else:
                    hs, as_ = o_as, o_hs
            fixtures_by_group[key].append((h, a, hs, as_))

    return [Group(list(members), fixtures_by_group[tuple(members)]) for members in group_lists]


# Per-simulation form noise (Elo points std-dev). Teams over/underperform
# their baseline rating on any given tournament run; this models "form" and
# stops the favorite from being over-confident across many sims.
FORM_SIGMA = 45.0


def _apply_form(elo):
    """Return a per-sim copy of ratings perturbed by random form."""
    return {t: r + random.gauss(0.0, FORM_SIGMA) for t, r in elo.items()}


def simulate_tournament(elo, groups):
    """Run one full tournament. Return the champion team name."""
    # Each tournament run uses a fresh "form" draw so results spread realistically.
    elo = _apply_form(elo)
    group_winners = []   # 1st place of each group
    group_runners = []   # 2nd place
    third_place = []     # (team, points, gd, gf) for best-third ranking

    for g in groups:
        standings = g.play(elo, None)
        group_winners.append(standings[0][0])
        group_runners.append(standings[1][0])
        third_place.append(standings[2])

    # Pick the 8 best third-placed teams.
    best_thirds = sorted(
        third_place, key=lambda x: (x[1], x[2], x[3], random.random()), reverse=True
    )[:8]
    third_teams = [t[0] for t in best_thirds]

    # Assemble the 32-team knockout pool. Real seeding/bracket pairing is
    # complex; we use a strength-aware shuffle so stronger qualifiers are
    # spread out rather than randomly colliding round one.
    knockout = group_winners + group_runners + third_teams
    knockout = list(dict.fromkeys(knockout))  # dedupe defensively

    # Pad/trim to a power of two (should already be 32).
    knockout.sort(key=lambda t: elo[t], reverse=True)
    # Standard seeding bracket: 1 vs 32, 2 vs 31, ... to spread strong teams.
    n = len(knockout)
    bracket = []
    for i in range(n // 2):
        bracket.append((knockout[i], knockout[n - 1 - i]))

    # Run knockout rounds until a single champion remains.
    round_teams = []
    for a, b in bracket:
        round_teams.append((a, b))

    current = []
    for a, b in bracket:
        _, _, w = simulate_match(elo[a], elo[b], home_adv=0.0, allow_draw=False)
        current.append(a if w == "A" else b)

    while len(current) > 1:
        nxt = []
        for i in range(0, len(current), 2):
            a, b = current[i], current[i + 1]
            _, _, w = simulate_match(elo[a], elo[b], home_adv=0.0, allow_draw=False)
            nxt.append(a if w == "A" else b)
        current = nxt

    return current[0]


def run_monte_carlo(n_sims=20000):
    """Train Elo, then simulate the tournament n times. Return win odds."""
    rows = load_results()
    model = EloModel()
    model.train(rows)
    elo = dict(model.ratings)

    groups = build_groups()

    titles = defaultdict(int)
    for _ in range(n_sims):
        champ = simulate_tournament(elo, groups)
        titles[champ] += 1

    odds = sorted(
        ((t, c / n_sims) for t, c in titles.items()),
        key=lambda kv: kv[1], reverse=True,
    )
    return odds, groups, elo


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    print(f"Simulating the 2026 World Cup {n:,} times...\n")
    odds, groups, elo = run_monte_carlo(n)
    print(f"=== {len(groups)} groups detected ===")
    for i, g in enumerate(groups, 1):
        print(f"  Group {i}: {', '.join(g.teams)}")
    print("\n=== CHAMPIONSHIP ODDS (top 20) ===")
    for i, (team, p) in enumerate(odds[:20], 1):
        bar = "#" * int(p * 100)
        print(f"{i:2d}. {team:<22} {p*100:5.1f}%  {bar}")
