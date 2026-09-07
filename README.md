OWNERSHIP 13F
=============

    pip3 install requests pandas
    ./pipeline/run_pipeline.sh 2026q1

One command that downloads a quarter's SEC 13F data, splits it per manager,
classifies managers by type, and writes the portfolio-weight/sentiment output.

Builds and analyzes SEC Form 13F institutional-ownership data: downloads
SEC's quarterly bulk filings, splits them per manager, classifies managers
by type, and computes portfolio weights / sentiment / quarter-over-quarter
diffs.


QUICKSTART -- ONE COMMAND TO GET RESULTS
-----------------------------------------

Pass multiple quarters to process more than one:
    ./pipeline/run_pipeline.sh 2025q4 2026q1

Output location: results are written under
~/Desktop/Ownership-13F/<quarter>/ (a fixed BASE_DIR inside
pipeline/download_13f.py and pipeline/aggregate_13f.py) -- not into this
repo's data/ folder. data/ here holds an earlier run's outputs, kept as
a snapshot. If you want new runs to land in this repo instead, update
BASE_DIR in both pipeline scripts.


FOLDER LAYOUT
-------------

pipeline/   -- the two production scripts run above.

analysis/   -- standalone tools for follow-up analysis (run individually,
               each takes its own quarter/CIK arguments -- see each
               script's docstring):
    manager_diff.py               -- diff one manager's holdings between two quarters.
    all_manager_diff.py           -- same diff, but for every manager at once.
    manager_buysell.py            -- per-manager buy/sell $ activity between two quarters.
    portfolio_weight_2025q4.py    -- tickers held at >5% weight by the most managers.

archive/    -- superseded prototypes, kept for reference only (each has a
               header comment explaining why it's archived and what
               replaced it).

data/       -- a snapshot of outputs/inputs from an earlier run (mappings,
               quarter diffs, raw per-manager holdings). Gitignored --
               regenerate via the pipeline rather than relying on this
               being current.

reports/    -- curated .numbers files for human review.


REQUIREMENTS
------------

    pip3 install requests pandas
