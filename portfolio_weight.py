#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Finds which tickers are held at >5% portfolio weight by the most managers,
for 2025q4. Self-contained (includes ticker map setup, boundary, and the
name-formatting helper). Managers with fewer than 10 total positions are
excluded (filters out shell/single-holding filers, e.g. 100% weight rows).
"""
from pathlib import Path
import pandas as pd

# ==========================================
# SETUP
# ==========================================
boundary_pct = 5  # 5%, on the 0-100 scale (this file's portfolio_weight_pct is 0-100, not 0-1)
MIN_POSITIONS = 10  # exclude managers holding fewer than this many distinct cusips

PICKLE_PATH = Path("/Users/graceha/Downloads/cusip_ticker_mapping/cusip_ticker_map_20260331.pickle")
if not PICKLE_PATH.exists():
    PICKLE_PATH = Path("/Users/graceha/Desktop/Ownership-13F/cusip_ticker_map_20260331.pickle")

raw_tick_map = pd.read_pickle(PICKLE_PATH)
raw_tick_map.columns = raw_tick_map.columns.str.strip().str.lower()
dedup_key = "cusip9" if "cusip9" in raw_tick_map.columns else ("cusip8" if "cusip8" in raw_tick_map.columns else raw_tick_map.columns[0])
raw_tick_map[dedup_key] = raw_tick_map[dedup_key].astype(str).str.slice(0, 8).str.strip().str.upper()
tick_lookup = raw_tick_map.drop_duplicates(dedup_key, keep="last").set_index(dedup_key)["ticker"]


def format_names(names, max_show=5):
    names = sorted(set(names))
    if len(names) <= max_show:
        return ", ".join(names)
    return ", ".join(names[:max_show]) + f", +{len(names) - max_show} more"


# ==========================================
# 1. LOAD 2025Q4 RAW HOLDINGS
# ==========================================
df_2025q4 = pd.read_csv("/Users/graceha/Desktop/2025q4/all_manager_holdings_2025q4.csv", dtype=str)
df_2025q4.columns = df_2025q4.columns.str.strip().str.lower()

df_2025q4["portfolio_weight_pct"] = pd.to_numeric(df_2025q4["portfolio_weight_pct"], errors="coerce")
df_2025q4["cusip"] = df_2025q4["cusip"].astype(str).str.strip()
df_2025q4["cik"] = df_2025q4["cik"].astype(str).str.strip()

# ==========================================
# 2. SUM WEIGHT PER (CIK, CUSIP) IN CASE OF MULTIPLE LINE ITEMS
# ==========================================
agg_2025q4 = df_2025q4.groupby(["cik", "cusip"], as_index=False).agg(
    portfolio_weight=("portfolio_weight_pct", "sum"),
    name=("manager_name", "first"),
)

# ==========================================
# 3. ATTACH TICKER
# ==========================================
agg_2025q4["ticker"] = agg_2025q4["cusip"].str.slice(0, 8).str.upper().map(tick_lookup)
agg_2025q4 = agg_2025q4.rename(columns={"cik": "CIK"})

# ==========================================
# 4. EXCLUDE MANAGERS WITH FEWER THAN MIN_POSITIONS DISTINCT CUSIPS
# ==========================================
positions_per_manager = agg_2025q4.groupby("CIK")["cusip"].nunique()
managers_with_enough_positions = positions_per_manager[positions_per_manager >= MIN_POSITIONS].index
n_excluded = agg_2025q4["CIK"].nunique() - len(managers_with_enough_positions)
print(f"Excluding {n_excluded} managers with fewer than {MIN_POSITIONS} positions")

agg_2025q4_filtered = agg_2025q4[agg_2025q4["CIK"].isin(managers_with_enough_positions)]

# ==========================================
# 5. FILTER TO >5% POSITIONS
# ==========================================
df_weights_filtered_2025q4 = agg_2025q4_filtered[agg_2025q4_filtered["portfolio_weight"] > boundary_pct]

# ==========================================
# 6. RANK BY # OF MANAGERS
# ==========================================
ranked_2025q4 = (
    df_weights_filtered_2025q4.groupby("ticker")
    .agg(
        n_managers=("CIK", "nunique"),
        avg_weight_pct=("portfolio_weight", "mean"),
        name=("name", format_names),
    )
    .sort_values("n_managers", ascending=False)
    .reset_index()
)

total_managers_considered = len(managers_with_enough_positions)
ranked_2025q4["pct_of_managers"] = (ranked_2025q4["n_managers"] / total_managers_considered * 100)

ranked_2025q4 = ranked_2025q4.round(2)
print(f"Total managers considered (after {MIN_POSITIONS}-position filter): {total_managers_considered}")
print(ranked_2025q4.head(20))

out_path = Path("/Users/graceha/Desktop/2025q4/ticker_by_manager_count_2025q4.csv")
ranked_2025q4.to_csv(out_path, index=False)
print(f"\nsaved to {out_path}")

# ==========================================
# 7. % OF MANAGERS WHO BOUGHT SOMETHING (from the buysell files, not this script's data)
# ==========================================
# NOTE: "bought" only exists as a concept in the buysell_{CIK}.csv files
# (built by manager_buysell_final.py, which diffs 2025q4 vs 2026q1). This
# section reads those files fresh since df_all didn't exist anywhere above.
buysell_folder = Path("/Users/graceha/Desktop/2026q1/manager_buysell")
buysell_files = sorted(buysell_folder.glob("buysell_*.csv"))

frames = []
for f in buysell_files:
    try:
        frames.append(pd.read_csv(f))
    except Exception as e:
        print(f"Skipped {f.name}: {e}")

df_all = pd.concat(frames, ignore_index=True)

pct_bought = (
    df_all.loc[df_all["classification"].isin(["new position", "increased"]), "CIK"].nunique()
    / df_all["CIK"].nunique() * 100
)
print(f"\n{pct_bought:.1f}% of managers bought something (new position or increased)")