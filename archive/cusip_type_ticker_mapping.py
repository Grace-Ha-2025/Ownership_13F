#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ARCHIVED — despite the filename, this is not a cusip/ticker mapping script:
# it's a single-manager (hardcoded CIK 9015) buy/sell diff between 2025q4 and
# 2026q1. It's the prototype that analysis/manager_buysell.py generalized to
# every manager via --curr/--prior args. Kept for reference only.
from pathlib import Path
import pandas as pd

TARGET_CIK = "9015"

curr_path = Path("/Users/graceha/Desktop/Ownership-13F-data/2026q1/all_manager_holdings_2026q1.csv")
prior_path = Path("/Users/graceha/Desktop/Ownership-13F-data/2025q4/all_manager_holdings_2025q4.csv")

PICKLE_PATH = Path("/Users/graceha/Downloads/cusip_ticker_mapping/cusip_ticker_map_20260331.pickle")
if not PICKLE_PATH.exists():
    PICKLE_PATH = Path("/Users/graceha/Desktop/Ownership-13F/cusip_ticker_map_20260331.pickle")

out_folder = Path("/Users/graceha/Desktop/manager_buysell")
out_folder.mkdir(parents=True, exist_ok=True)

# 1. LOAD & AGGREGATE CURRENT QUARTER
df_curr_raw = pd.read_csv(curr_path, dtype=str)
df_curr_raw.columns = df_curr_raw.columns.str.strip().str.lower()
df_curr_raw["value"] = pd.to_numeric(df_curr_raw["value"], errors="coerce")
df_curr_raw["shares"] = pd.to_numeric(df_curr_raw["shares"], errors="coerce")
df_curr_raw["cusip"] = df_curr_raw["cusip"].astype(str).str.strip()
df_curr_raw["cik"] = df_curr_raw["cik"].astype(str).str.strip()

df_curr_clean = df_curr_raw[
    df_curr_raw["cusip"].notna() & 
    (df_curr_raw["cusip"] != "") & 
    (df_curr_raw["cusip"].str.lower() != "nan") &
    (~df_curr_raw["cusip"].str.contains("E\\+", case=False, na=False)) &
    (df_curr_raw["cik"] == TARGET_CIK)
]
df_curr = df_curr_clean.groupby(["cik", "cusip"], as_index=False).agg(
    value=("value", "sum"),
    shares=("shares", "sum"),
    manager_name=("manager_name", "first"),
)

# 2. LOAD & AGGREGATE PRIOR QUARTER
df_prior_raw = pd.read_csv(prior_path, dtype=str)
df_prior_raw.columns = df_prior_raw.columns.str.strip().str.lower()
df_prior_raw["value"] = pd.to_numeric(df_prior_raw["value"], errors="coerce")
df_prior_raw["shares"] = pd.to_numeric(df_prior_raw["shares"], errors="coerce")
df_prior_raw["cusip"] = df_prior_raw["cusip"].astype(str).str.strip()
df_prior_raw["cik"] = df_prior_raw["cik"].astype(str).str.strip()

df_prior_clean = df_prior_raw[
    df_prior_raw["cusip"].notna() & 
    (df_prior_raw["cusip"] != "") & 
    (df_prior_raw["cusip"].str.lower() != "nan") &
    (~df_prior_raw["cusip"].str.contains("E\\+", case=False, na=False)) &
    (df_prior_raw["cik"] == TARGET_CIK)
]
df_prior = df_prior_clean.groupby(["cik", "cusip"], as_index=False).agg(
    value=("value", "sum"),
    shares=("shares", "sum"),
    manager_name=("manager_name", "first"),
)

# 3. LOAD THE TICKER MAP (Sliced to 8-char keys)
raw_tick_map = pd.read_pickle(PICKLE_PATH)
raw_tick_map.columns = raw_tick_map.columns.str.strip().str.lower()
dedup_key = "cusip9" if "cusip9" in raw_tick_map.columns else ("cusip8" if "cusip8" in raw_tick_map.columns else raw_tick_map.columns[0])
raw_tick_map[dedup_key] = raw_tick_map[dedup_key].astype(str).str.slice(0, 8).str.strip().str.upper()
tick_lookup = raw_tick_map.drop_duplicates(dedup_key, keep="last").set_index(dedup_key)["ticker"]

# 4. DIFF CURRENT VS PRIOR
diff = df_curr.merge(df_prior, how="outer", on=["cik", "cusip"], suffixes=("_curr", "_prior"))
diff["manager_name"] = diff["manager_name_curr"].fillna(diff["manager_name_prior"])

diff[["value_curr", "value_prior", "shares_curr", "shares_prior"]] = diff[
    ["value_curr", "value_prior", "shares_curr", "shares_prior"]
].astype(float).fillna(0)

shares_diff = diff["shares_curr"] - diff["shares_prior"]
diff["shares_bought"] = shares_diff.clip(lower=0)
diff["shares_sold"] = (-shares_diff).clip(lower=0)

price_curr = (diff["value_curr"] / diff["shares_curr"]).replace([float("inf"), -float("inf")], pd.NA)
price_prior = (diff["value_prior"] / diff["shares_prior"]).replace([float("inf"), -float("inf")], pd.NA)

price_for_buys = price_curr.fillna(price_prior).fillna(0)
price_for_sells = price_prior.fillna(price_curr).fillna(0)

diff["value_bought"] = (diff["shares_bought"] * price_for_buys).fillna(0.0).round(2)
diff["value_sold"] = (diff["shares_sold"] * price_for_sells).fillna(0.0).round(2)

diff["shares_bought"] = diff["shares_bought"].fillna(0).astype(int)
diff["shares_sold"] = diff["shares_sold"].fillna(0).astype(int)

diff = diff[(diff["shares_bought"] > 0) | (diff["shares_sold"] > 0)]

# Map ticker using 8-character truncation
diff["ticker"] = diff["cusip"].str.slice(0, 8).str.upper().map(tick_lookup)

diff = diff.rename(columns={"cik": "CIK", "manager_name": "name"})
output_cols = ["CIK", "name", "cusip", "ticker", "value_bought", "value_sold", "shares_bought", "shares_sold"]
diff = diff[output_cols]

# 5. SAVE SINGLE MANAGER FILE
out_file = out_folder / f"buysell_{TARGET_CIK}.csv"
diff.to_csv(out_file, index=False)
print(f"Done. File written to {out_file}")