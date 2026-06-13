"""Precompute all model outputs and cache to data/predictions.json.

Run this whenever new match results arrive (or on a daily cron during the
tournament) to refresh every prediction the web app serves:
  - Championship odds with confidence intervals
  - Round-by-round progression odds per team
  - Group-finish odds per team
  - Likely first knockout opponents
  - A representative bracket
  - The historical backtest (recomputed so the credibility stats stay fresh)

Usage:
  python precompute.py [n_sims]   (default 30000)
"""

import json
from pathlib import Path

from simulate import run_full_simulation
from backtest import run_backtest
from tournament import build_groups
from engine import EloModel, load_results

OUT = Path(__file__).parent / "data" / "predictions.json"


def main(n=30000):
    # Heavy instrumented simulation: produces odds, progression, brackets, etc.
    sim = run_full_simulation(n)

    # Ratings table for the methodology / strength view.
    model = EloModel()
    model.train(load_results())
    ratings = sorted(
        ({"team": t, "elo": round(r, 1)} for t, r in model.ratings.items()),
        key=lambda x: x["elo"], reverse=True,
    )

    # Group rosters for the groups view.
    groups = build_groups()
    group_payload = [
        {"name": f"Group {i}", "teams": g.teams}
        for i, g in enumerate(groups, 1)
    ]

    # Backtest for the credibility section.
    backtest = run_backtest()

    payload = {
        "n_sims": n,
        "championship_odds": sim["championship_odds"],
        "progression": sim["progression"],
        "group_odds": sim["group_odds"],
        "likely_opponents": sim["likely_opponents"],
        "sample_bracket": sim["sample_bracket"],
        "modal_champion": sim["modal_champion"],
        "groups": group_payload,
        "ratings": ratings,
        "wc_teams": sim["teams"],
        "backtest": backtest,
    }
    OUT.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {OUT}")
    print(f"  {len(sim['championship_odds'])} teams, {n:,} sims")
    print(f"  backtest winner-top5 rate: {backtest['winner_top5_rate']*100:.0f}%")


if __name__ == "__main__":
    import sys
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30000)
