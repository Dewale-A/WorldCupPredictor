# World Cup 2026 Oracle Web App

This project adds a FastAPI web layer and a polished single-page frontend on top of the existing prediction engine.

## Run locally

1. Create and activate a virtual environment.
```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies.
```bash
pip install -r requirements.txt
```

3. Refresh cached tournament predictions.
```bash
python precompute.py
```

4. Start the API and frontend server.
```bash
uvicorn app:app --port 8090
```

5. Open:
- `http://localhost:8090/`
- `http://localhost:8090/api/predictions`

## Refresh workflow when new results arrive

1. Update the underlying match data in `data/results.csv`.
2. Recompute cached outputs:
```bash
python precompute.py
```
3. Restart the app server:
```bash
uvicorn app:app --port 8090
```

The API endpoint `/api/predictions` serves the latest `data/predictions.json` contents, so refreshing that file keeps the UI in sync.

## Live Results (keep the model current)

The upstream dataset lags real matches by a day or more. Two ways to stay current:

1. **Enter results manually (instant).** In the app, use the "Live Results" section: pick the two teams, enter the final score, click "Save result." The whole model rebuilds and every odd updates. Entered results persist in `data/live_results.json` and survive dataset refreshes. Clear them anytime with "Clear all."

2. **Automatic daily refresh (backup).** `refresh.sh` pulls the latest upstream data and regenerates predictions. A cron job runs it daily at 9 AM Mountain. Manually entered live results are always preserved (they are a separate overlay merged on top of the feed).

Run a refresh manually anytime:
```
./refresh.sh
```

## Owner-only editing (admin key)

When deployed publicly, set an `ADMIN_KEY` environment variable to lock the
"Save result" and "Clear all" actions to the owner. Everyone with the link keeps
full read access (odds, deep-dives, head-to-head, what-if), but only someone with
the key can change the shared live results.

- Set `ADMIN_KEY=your-secret` in the deployment environment.
- In the app, click "Unlock", enter the key once. It is remembered in your browser.
- If `ADMIN_KEY` is unset (local dev), editing is open with no key required.
