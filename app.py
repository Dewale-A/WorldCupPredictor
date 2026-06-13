"""FastAPI web layer for the World Cup 2026 predictor.

This file intentionally keeps engine and tournament logic untouched.
It only orchestrates cached model state and HTTP responses.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Dict, List, Tuple

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine import EloModel, load_results, outcome_probabilities
from tournament import build_groups, simulate_tournament
from live_results import (
    load_live_results, add_result, clear_results,
)

BASE_DIR = Path(__file__).parent
DATA_FILE = BASE_DIR / "data" / "predictions.json"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="World Cup 2026 Oracle API")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Module-level cache avoids retraining Elo per request, which keeps API latency low.
MODEL: EloModel | None = None
ELO_RATINGS: Dict[str, float] = {}
GROUPS = []
WC_TEAMS: List[str] = []


class ForcedResult(BaseModel):
    # Explicit field names keep payloads simple for frontend fetch usage.
    home: str
    away: str
    home_score: int = Field(ge=0, le=20)
    away_score: int = Field(ge=0, le=20)


class LiveResult(BaseModel):
    # One real, observed match score the user enters before the feed updates.
    home: str
    away: str
    home_score: int = Field(ge=0, le=30)
    away_score: int = Field(ge=0, le=30)


class WhatIfPayload(BaseModel):
    # A list supports forcing multiple matches in one scenario.
    forced_results: List[ForcedResult] = Field(default_factory=list)


def _init_state() -> None:
    """Train and cache ratings once so expensive setup is reused."""
    global MODEL, ELO_RATINGS, GROUPS, WC_TEAMS
    if MODEL is not None:
        return

    model = EloModel()
    model.train(load_results())
    groups = build_groups()

    MODEL = model
    ELO_RATINGS = dict(model.ratings)
    GROUPS = groups
    WC_TEAMS = sorted({team for group in groups for team in group.teams})


def _load_predictions() -> dict:
    """Read cached precompute output every request so manual refresh is visible immediately."""
    if not DATA_FILE.exists():
        raise HTTPException(
            status_code=404,
            detail="predictions.json was not found. Run python precompute.py first.",
        )
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def _apply_forced_results(forced_results: List[ForcedResult]):
    """Return a copy of groups with requested score overrides injected into fixtures."""
    forced_by_key: Dict[Tuple[str, str], Tuple[str, str, int, int]] = {}
    for item in forced_results:
        key = tuple(sorted((item.home, item.away)))
        forced_by_key[key] = (item.home, item.away, item.home_score, item.away_score)

    scenario_groups = []
    for group in GROUPS:
        group_copy = copy.deepcopy(group)
        new_fixtures = []
        for home, away, hs, as_ in group_copy.fixtures:
            key = tuple(sorted((home, away)))
            if key in forced_by_key:
                forced_home, forced_away, forced_hs, forced_as = forced_by_key[key]
                if forced_home == home and forced_away == away:
                    new_fixtures.append((home, away, forced_hs, forced_as))
                else:
                    # Reverse the score when the caller provides the opposite team order.
                    new_fixtures.append((home, away, forced_as, forced_hs))
            else:
                new_fixtures.append((home, away, hs, as_))
        group_copy.fixtures = new_fixtures
        scenario_groups.append(group_copy)

    return scenario_groups


@app.on_event("startup")
def startup_event() -> None:
    # Warm startup front-loads training cost so first user interaction is fast.
    _init_state()


@app.get("/")
def root() -> FileResponse:
    # Serving the built SPA from root keeps deployment and local dev simple.
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/predictions")
def api_predictions() -> dict:
    payload = _load_predictions()
    # Return the full enriched payload so the SPA can render every section
    # (odds with confidence ranges, progression, group odds, bracket, backtest).
    return {
        "n_sims": payload.get("n_sims"),
        "championship_odds": payload.get("championship_odds", []),
        "progression": payload.get("progression", {}),
        "group_odds": payload.get("group_odds", {}),
        "likely_opponents": payload.get("likely_opponents", {}),
        "sample_bracket": payload.get("sample_bracket", []),
        "modal_champion": payload.get("modal_champion"),
        "groups": payload.get("groups", []),
        "ratings": payload.get("ratings", []),
        "wc_teams": payload.get("wc_teams", []),
        "backtest": payload.get("backtest", {}),
    }


@app.get("/api/team/{team_name}")
def api_team(team_name: str) -> dict:
    """Return the full deep-dive profile for one team (the Path to Glory panel)."""
    payload = _load_predictions()
    progression = payload.get("progression", {})
    if team_name not in progression:
        raise HTTPException(status_code=404, detail="Team not found.")

    # Find the team's group for context.
    team_group = None
    for g in payload.get("groups", []):
        if team_name in g["teams"]:
            team_group = g["name"]
            break

    # Find the team's championship odds entry (with confidence range).
    odds_entry = next(
        (o for o in payload.get("championship_odds", []) if o["team"] == team_name),
        None,
    )

    return {
        "team": team_name,
        "group": team_group,
        "odds": odds_entry,
        "progression": progression[team_name],
        "group_odds": payload.get("group_odds", {}).get(team_name),
        "likely_opponents": payload.get("likely_opponents", {}).get(team_name, []),
    }


@app.get("/api/h2h")
def api_h2h(
    team_a: str = Query(..., min_length=1),
    team_b: str = Query(..., min_length=1),
) -> dict:
    _init_state()
    if team_a == team_b:
        raise HTTPException(status_code=400, detail="Pick two different teams.")
    if team_a not in ELO_RATINGS or team_b not in ELO_RATINGS:
        raise HTTPException(status_code=404, detail="One or both teams were not found.")

    result = outcome_probabilities(
        ELO_RATINGS[team_a],
        ELO_RATINGS[team_b],
        home_adv=0.0,
        n=10000,  # Smaller sample gives quick UI feedback while staying stable.
    )
    likely_a, likely_b = result["likely_score"]
    return {
        "team_a": team_a,
        "team_b": team_b,
        "win_a": result["win_a"],
        "draw": result["draw"],
        "win_b": result["win_b"],
        "likely_score": f"{likely_a}-{likely_b}",
        "elo_a": round(ELO_RATINGS[team_a], 1),
        "elo_b": round(ELO_RATINGS[team_b], 1),
    }


@app.post("/api/whatif")
def api_whatif(payload: WhatIfPayload) -> dict:
    _init_state()
    scenario_groups = _apply_forced_results(payload.forced_results)

    titles: Dict[str, int] = {}
    n_sims = 5000
    for _ in range(n_sims):
        champion = simulate_tournament(ELO_RATINGS, scenario_groups)
        titles[champion] = titles.get(champion, 0) + 1

    updated = sorted(
        [{"team": team, "prob": wins / n_sims} for team, wins in titles.items()],
        key=lambda row: row["prob"],
        reverse=True,
    )

    return {
        "n_sims": n_sims,
        "forced_results": [item.model_dump() for item in payload.forced_results],
        "championship_odds": updated[:20],
    }


def _rebuild_after_live_change() -> None:
    """Re-run the full precompute so every cached prediction reflects new live scores.

    Live results change the real standings, so we regenerate predictions.json (odds,
    progression, bracket, group odds) and refresh the in-memory groups. We use a
    modest sim count so the endpoint stays responsive when called from the UI.
    """
    global GROUPS
    from precompute import main as precompute_main
    # 12,000 sims balances accuracy against a quick turnaround after each entry.
    precompute_main(12000)
    # Rebuild the cached groups so what-if and h2h use the updated fixtures too.
    GROUPS = build_groups()


@app.get("/api/live-results")
def api_live_results_list() -> dict:
    """Return all manually entered live results currently applied."""
    return {"live_results": load_live_results()}


@app.post("/api/live-results")
def api_live_results_add(result: LiveResult) -> dict:
    """Add or update one observed match score, then refresh all predictions."""
    _init_state()
    if result.home == result.away:
        raise HTTPException(status_code=400, detail="A match needs two different teams.")
    if result.home not in ELO_RATINGS or result.away not in ELO_RATINGS:
        raise HTTPException(status_code=404, detail="One or both teams were not found.")

    entries = add_result(result.home, result.away, result.home_score, result.away_score)
    _rebuild_after_live_change()
    return {
        "status": "applied",
        "entry": result.model_dump(),
        "live_results": entries,
    }


@app.delete("/api/live-results")
def api_live_results_clear() -> dict:
    """Clear all manual entries and revert to the dataset's own results."""
    clear_results()
    _rebuild_after_live_change()
    return {"status": "cleared", "live_results": []}
