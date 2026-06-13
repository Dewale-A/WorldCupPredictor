"""
Live results overlay.

The upstream dataset (martj42 on GitHub) lags real matches by a day or more.
This module lets us enter real scores the moment we see them, so the model
reflects the tournament as it actually unfolds rather than waiting on the feed.

Entered results are stored in data/live_results.json as a simple list of
{date, home, away, home_score, away_score}. The tournament builder merges these
over the dataset fixtures: when a stored result matches a scheduled fixture (by
the unordered team pair), that fixture's score is filled in and treated exactly
like an officially played match.

Why a JSON overlay rather than editing the CSV: it keeps user-entered data
separate from the source feed, so a later auto-refresh of the CSV never wipes
the manual entries, and we can clear or audit them independently.
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

LIVE_FILE = Path(__file__).parent / "data" / "live_results.json"

# A lock guards concurrent writes from overlapping API requests.
_LOCK = Lock()


def load_live_results() -> list[dict]:
    """Return the list of manually entered results, or empty if none yet."""
    if not LIVE_FILE.exists():
        return []
    try:
        return json.loads(LIVE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt or unreadable overlay should never crash the app; treat as empty.
        return []


def save_live_results(entries: list[dict]) -> None:
    """Persist the full list of entries atomically under the data directory."""
    with _LOCK:
        LIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
        LIVE_FILE.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _pair_key(a: str, b: str) -> frozenset:
    """Unordered team-pair key so home/away order never causes a mismatch."""
    return frozenset((a, b))


def add_result(home: str, away: str, home_score: int, away_score: int) -> list[dict]:
    """Add or replace one live result, keyed by the team pair. Returns the new list."""
    entries = load_live_results()
    key = _pair_key(home, away)
    # Drop any existing entry for the same pairing so re-entering a score updates it.
    entries = [e for e in entries if _pair_key(e["home"], e["away"]) != key]
    entries.append({
        "home": home,
        "away": away,
        "home_score": int(home_score),
        "away_score": int(away_score),
    })
    save_live_results(entries)
    return entries


def clear_results() -> None:
    """Remove all manual entries (reset to the dataset's own results)."""
    save_live_results([])


def overrides_by_pair() -> dict:
    """Return a lookup of pair-key -> (home, away, hs, as) for fast fixture merging."""
    out = {}
    for e in load_live_results():
        out[_pair_key(e["home"], e["away"])] = (
            e["home"], e["away"], int(e["home_score"]), int(e["away_score"]),
        )
    return out
