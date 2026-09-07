#!/usr/bin/env bash
# One command to get results for one or more quarters end-to-end:
# download -> per-manager split -> classify -> combined CSV -> sentiment.
#
# Usage:
#   ./pipeline/run_pipeline.sh 2026q1
#   ./pipeline/run_pipeline.sh 2025q4 2026q1
#
# Requires: pip3 install requests pandas
# Output lands under BASE_DIR from pipeline/download_13f.py and
# pipeline/aggregate_13f.py (currently ~/Desktop/Ownership-13F/<quarter>/),
# NOT under this repo's data/ folder — see README.md.
set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <quarter> [<quarter> ...]   e.g. $0 2026q1" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Step 1/2: downloading + splitting per-manager holdings ==="
python3 "$SCRIPT_DIR/download_13f.py" --quarters "$@"

for q in "$@"; do
    echo ""
    echo "=== Step 2/2: aggregating $q (classify + combined + sentiment) ==="
    python3 "$SCRIPT_DIR/aggregate_13f.py" --quarter "$q" --combined
done

echo ""
echo "Done. Results are under ~/Desktop/Ownership-13F/<quarter>/ for each: $*"
