"""
Backtest: does the model actually work?

This is the credibility layer. For each past World Cup we:
  1. Train the Elo model ONLY on matches played before that tournament started
     (no peeking at the future).
  2. Read the model's pre-tournament rating for every team that competed.
  3. Check where the eventual champion and finalists ranked among the field.

A model with real predictive power should rank the actual winner near the top
of the field most of the time. We report that so the app can honestly say
"here is how this same model would have rated past champions."

We keep this lightweight: we rank by pre-tournament Elo among the actual
participants rather than running a full Monte Carlo for each historical event,
because Elo rank is the core signal and this keeps the backtest fast and
transparent.
"""

from __future__ import annotations

from datetime import date

from engine import EloModel, load_results, _parse_date

# Known results to validate against: champion + runner-up per edition, and the
# set of participants is derived from the data itself.
PAST_FINALS = {
    2014: {"winner": "Germany", "runner_up": "Argentina"},
    2018: {"winner": "France", "runner_up": "Croatia"},
    2022: {"winner": "Argentina", "runner_up": "France"},
}

# Approximate tournament start dates so we cut training data cleanly before each.
WC_START = {
    2014: date(2014, 6, 12),
    2018: date(2018, 6, 14),
    2022: date(2022, 11, 20),
}


def _participants(rows, year, start):
    """Find the teams that actually played in that World Cup (the real field)."""
    teams = set()
    for r in rows:
        if r["tournament"] != "FIFA World Cup":
            continue
        d = _parse_date(r["date"])
        # Group the matches that fall within ~45 days of the start date.
        if start <= d <= date(start.year, start.month, 28) or (
            start <= d and (d - start).days <= 45
        ):
            teams.add(r["home_team"])
            teams.add(r["away_team"])
    return teams


def backtest_edition(rows, year):
    """Return the model's pre-tournament ranking facts for one World Cup."""
    start = WC_START[year]

    # Train ONLY on matches strictly before the tournament began.
    pre_rows = [r for r in rows if _parse_date(r["date"]) < start]
    model = EloModel()
    model.train(pre_rows)

    field = _participants(rows, year, start)
    # Rank participants by their pre-tournament Elo.
    ranked = sorted(
        ((t, model.ratings[t]) for t in field if t in model.ratings),
        key=lambda kv: kv[1], reverse=True,
    )
    order = [t for t, _ in ranked]

    winner = PAST_FINALS[year]["winner"]
    runner = PAST_FINALS[year]["runner_up"]

    def rank_of(team):
        return order.index(team) + 1 if team in order else None

    return {
        "year": year,
        "field_size": len(order),
        "winner": winner,
        "winner_rank": rank_of(winner),
        "runner_up": runner,
        "runner_up_rank": rank_of(runner),
        "model_favorite": order[0] if order else None,
        "top5": order[:5],
    }


def run_backtest():
    """Backtest all known editions and summarize accuracy."""
    rows = load_results()
    results = [backtest_edition(rows, y) for y in sorted(PAST_FINALS)]

    # Headline stat: how often the actual winner was in the model's top 5.
    top5_hits = sum(
        1 for r in results if r["winner_rank"] and r["winner_rank"] <= 5
    )
    summary = {
        "editions": results,
        "winner_top5_rate": top5_hits / len(results) if results else 0.0,
    }
    return summary


if __name__ == "__main__":
    out = run_backtest()
    print("=== Backtest: how the same model rated past World Cups ===\n")
    for r in out["editions"]:
        print(f"{r['year']} World Cup  (field of {r['field_size']})")
        print(f"  Actual winner:  {r['winner']:<14} -> model ranked #{r['winner_rank']}")
        print(f"  Actual final:   {r['runner_up']:<14} -> model ranked #{r['runner_up_rank']}")
        print(f"  Model favorite: {r['model_favorite']}")
        print(f"  Model top 5:    {', '.join(r['top5'])}")
        print()
    print(f"Winner ranked in model's top 5: "
          f"{out['winner_top5_rate']*100:.0f}% of editions")
