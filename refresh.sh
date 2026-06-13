#!/usr/bin/env bash
#
# Daily refresh for the World Cup 2026 Oracle.
#
# Pulls the latest results from the upstream martj42 dataset and regenerates
# all cached predictions. Manually entered live results are preserved, because
# they live in data/live_results.json (a separate overlay) and are merged on top
# of the refreshed dataset by build_groups(). So this never wipes hand-entered
# scores; it just catches up everything the feed has published since.
#
# Intended to run from cron once a day during the tournament.

set -euo pipefail

# Resolve the project directory regardless of where cron invokes this from.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

DATA_DIR="$PROJECT_DIR/data"
BASE="https://raw.githubusercontent.com/martj42/international_results/master"

echo "[refresh] $(date) pulling latest results..."

# Download to temp files first so a failed fetch never corrupts good data.
for f in results.csv goalscorers.csv shootouts.csv; do
  if curl -fsS "$BASE/$f" -o "$DATA_DIR/$f.tmp"; then
    mv "$DATA_DIR/$f.tmp" "$DATA_DIR/$f"
    echo "[refresh] updated $f"
  else
    echo "[refresh] WARN could not fetch $f, keeping existing copy"
    rm -f "$DATA_DIR/$f.tmp"
  fi
done

# Regenerate predictions using the project venv so deps resolve correctly.
echo "[refresh] recomputing predictions..."
"$PROJECT_DIR/.venv/bin/python" precompute.py 30000

echo "[refresh] done $(date)"
