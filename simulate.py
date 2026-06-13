"""
Enhanced Monte Carlo simulation for the 2026 World Cup predictor.

This module extends the basic champion-only simulation with the richer data the
product UI needs:
  - Round-by-round survival odds per team (group, R32, R16, QF, SF, Final, Win).
  - Group-finish odds per team (1st / 2nd / 3rd-and-advance / eliminated).
  - Most likely knockout opponents per team.
  - Confidence intervals on championship odds (so we show a range, not a point).
  - A modal/representative bracket for the visual bracket view.

It reuses the validated Elo + Poisson primitives from engine.py and the group
inference from tournament.py. We deliberately keep one "instrumented" simulate
function that records the full path of a single tournament run, then aggregate
across many runs.

Why a separate file: app.py and precompute.py both need this, and keeping the
heavy aggregation here avoids bloating the original tournament.py (which the web
layer already imports for the lightweight what-if path).
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Dict, List, Tuple

from engine import EloModel, load_results, simulate_match
from tournament import build_groups, _apply_form

# Knockout round labels in order. The 48-team format starts at a 32-team
# knockout, so the first knockout round is the Round of 32.
ROUND_NAMES = ["R32", "R16", "QF", "SF", "Final", "Win"]


def _group_standings(group, elo):
    """Play one group and return ordered [(team, pts, gd, gf), ...]."""
    return group.play(elo, None)


def simulate_once(elo, groups, record):
    """Simulate one full tournament, recording every team's progress into `record`.

    `record` is a dict of counters we mutate in place so we can aggregate over
    many runs without allocating per-call. Returns the champion plus the bracket
    pairings for this single run (used to capture a representative bracket).
    """
    # Fresh form draw each tournament so outcomes spread realistically.
    elo = _apply_form(elo)

    group_winners, group_runners, thirds = [], [], []
    # Track each team's group finish position this run.
    for g in groups:
        standings = _group_standings(g, elo)
        group_winners.append(standings[0][0])
        group_runners.append(standings[1][0])
        thirds.append(standings[2])
        # Record group-finish positions (1st, 2nd, 3rd, 4th) for odds.
        for pos, (team, *_rest) in enumerate(standings, start=1):
            record["group_finish"][team][pos] += 1

    # Best 8 third-placed teams advance alongside the 24 top-two finishers.
    best_thirds = sorted(
        thirds, key=lambda x: (x[1], x[2], x[3], random.random()), reverse=True
    )[:8]
    third_teams = [t[0] for t in best_thirds]
    advanced_third = set(third_teams)

    # Mark who reached the knockout stage (survived the group).
    knockout = group_winners + group_runners + third_teams
    knockout = list(dict.fromkeys(knockout))
    for team in knockout:
        record["reached"][team]["R32"] += 1
        # A team "advances" from the group if it made the knockouts.
        record["advanced"][team] += 1
    # Record which third-placed teams advanced vs went home (for group odds).
    for team, *_ in thirds:
        if team in advanced_third:
            record["third_advanced"][team] += 1

    # Seed the bracket by strength so strong teams are spread out.
    knockout.sort(key=lambda t: elo[t], reverse=True)
    n = len(knockout)
    pairs = [(knockout[i], knockout[n - 1 - i]) for i in range(n // 2)]

    # Capture first-round opponents for the "likely opponents" feature.
    for a, b in pairs:
        record["first_opp"][a][b] += 1
        record["first_opp"][b][a] += 1

    # Play knockout rounds, recording how far each team gets.
    current_pairs = pairs
    round_idx = 0
    bracket_rounds = []  # list of rounds; each round is list of (a, b, winner)
    survivors = []
    # Resolve first round.
    this_round = []
    for a, b in current_pairs:
        _, _, w = simulate_match(elo[a], elo[b], home_adv=0.0, allow_draw=False)
        winner = a if w == "A" else b
        this_round.append((a, b, winner))
        survivors.append(winner)
    bracket_rounds.append(this_round)
    # Winners reached the next round (R16).
    for w in survivors:
        record["reached"][w][ROUND_NAMES[round_idx + 1]] += 1

    # Continue through remaining rounds.
    while len(survivors) > 1:
        round_idx += 1
        nxt = []
        this_round = []
        for i in range(0, len(survivors), 2):
            a, b = survivors[i], survivors[i + 1]
            _, _, w = simulate_match(elo[a], elo[b], home_adv=0.0, allow_draw=False)
            winner = a if w == "A" else b
            this_round.append((a, b, winner))
            nxt.append(winner)
        bracket_rounds.append(this_round)
        survivors = nxt
        label = ROUND_NAMES[round_idx + 1] if round_idx + 1 < len(ROUND_NAMES) else "Win"
        for w in survivors:
            record["reached"][w][label] += 1

    champion = survivors[0]
    record["champion"][champion] += 1
    return champion, bracket_rounds


def _new_record():
    """Allocate the nested counters used during aggregation."""
    return {
        "champion": defaultdict(int),
        "advanced": defaultdict(int),          # reached knockouts at all
        "third_advanced": defaultdict(int),    # advanced specifically as a 3rd
        "reached": defaultdict(lambda: defaultdict(int)),     # team -> round -> count
        "group_finish": defaultdict(lambda: defaultdict(int)),# team -> position -> count
        "first_opp": defaultdict(lambda: defaultdict(int)),   # team -> opponent -> count
    }


def _wilson_interval(p, n, z=1.96):
    """Wilson score interval for a proportion.

    We use Wilson rather than the naive normal approximation because it behaves
    well for small probabilities (many teams have low title odds), never going
    below 0 or above 1. This gives an honest +/- range on each team's odds.
    """
    if n == 0:
        return (0.0, 0.0)
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def run_full_simulation(n_sims=30000):
    """Run the instrumented simulation n times and aggregate all product data.

    Returns a dict ready to serialize to predictions.json.
    """
    rows = load_results()
    model = EloModel()
    model.train(rows)
    elo = dict(model.ratings)
    groups = build_groups()

    record = _new_record()
    # Keep a representative bracket: the one whose champion is the modal champion.
    sample_bracket = None
    bracket_by_champ = {}

    for _ in range(n_sims):
        champ, bracket = simulate_once(elo, groups, record)
        # Store one bracket per champion so we can later pick the modal one.
        if champ not in bracket_by_champ:
            bracket_by_champ[champ] = bracket

    teams = sorted({t for g in groups for t in g.teams})

    # --- Championship odds with confidence intervals ---
    champ_odds = []
    for t in teams:
        wins = record["champion"][t]
        p = wins / n_sims
        lo, hi = _wilson_interval(p, n_sims)
        champ_odds.append({
            "team": t,
            "prob": p,
            "ci_low": lo,
            "ci_high": hi,
        })
    champ_odds.sort(key=lambda r: r["prob"], reverse=True)

    # The modal champion's bracket becomes the showcase bracket.
    modal_champ = champ_odds[0]["team"]
    sample_bracket = bracket_by_champ.get(modal_champ)

    # --- Round-by-round survival odds per team ---
    progression = {}
    for t in teams:
        reached = record["reached"][t]
        progression[t] = {
            "group": 1.0,  # everyone starts in the group stage
            "R32": reached["R32"] / n_sims,
            "R16": reached["R16"] / n_sims,
            "QF": reached["QF"] / n_sims,
            "SF": reached["SF"] / n_sims,
            "Final": reached["Final"] / n_sims,
            "Win": reached["Win"] / n_sims,
        }

    # --- Group-finish odds per team ---
    group_odds = {}
    for t in teams:
        gf = record["group_finish"][t]
        total = sum(gf.values()) or 1
        group_odds[t] = {
            "first": gf[1] / total,
            "second": gf[2] / total,
            "third": gf[3] / total,
            "fourth": gf[4] / total,
            "advance": record["advanced"][t] / n_sims,
        }

    # --- Most likely first knockout opponents per team ---
    likely_opponents = {}
    for t in teams:
        opps = record["first_opp"][t]
        ranked = sorted(opps.items(), key=lambda kv: kv[1], reverse=True)[:3]
        total = sum(opps.values()) or 1
        likely_opponents[t] = [
            {"team": o, "prob": c / total} for o, c in ranked
        ]

    # --- Serialize the representative bracket ---
    bracket_payload = []
    if sample_bracket:
        for rnd in sample_bracket:
            bracket_payload.append([
                {"a": a, "b": b, "winner": w} for (a, b, w) in rnd
            ])

    return {
        "teams": teams,
        "championship_odds": champ_odds,
        "progression": progression,
        "group_odds": group_odds,
        "likely_opponents": likely_opponents,
        "sample_bracket": bracket_payload,
        "modal_champion": modal_champ,
    }


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    print(f"Running full instrumented simulation: {n:,} runs...\n")
    out = run_full_simulation(n)
    print("=== Title odds (top 10) with 95% confidence range ===")
    for row in out["championship_odds"][:10]:
        print(f"  {row['team']:<20} {row['prob']*100:5.1f}%  "
              f"[{row['ci_low']*100:4.1f}-{row['ci_high']*100:4.1f}]")
    print(f"\n=== Deepest run example: {out['modal_champion']} progression ===")
    prog = out["progression"][out["modal_champion"]]
    for rnd in ["R32", "R16", "QF", "SF", "Final", "Win"]:
        print(f"  {rnd:<6} {prog[rnd]*100:5.1f}%")
    print(f"\n=== Sample bracket rounds captured: {len(out['sample_bracket'])} ===")
