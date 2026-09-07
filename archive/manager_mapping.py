#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ARCHIVED — despite the filename, this is not about mapping managers: it's
# the all-managers buy/sell loop (hardcoded 2025q4/2026q1 paths, no CLI
# args). Superseded by analysis/manager_buysell.py, which does the same
# thing parameterized by --curr/--prior. Kept for reference only.
from pathlib import Path
import warnings
import pandas as pd

warnings.simplefilter(action="ignore", category=FutureWarning)

curr_path = Path("/Users/graceha/Desktop/Ownership-13F-data/2026q1/all_manager_holdings_2026q1.csv")
prior_path = Path("/Users/graceha/Desktop/Ownership-13F-data/2025q4/all_manager_holdings_2025q4.csv")

PICKLE_PATH = Path("/Users/graceha/Downloads/cusip_ticker_mapping/cusip_ticker_map_20260331.pickle")
if not PICKLE_PATH.exists():
    PICKLE_PATH = Path("/Users/graceha/Desktop/Ownership-13F/cusip_ticker_map_20260331.pickle")

out_folder = Path("/Users/graceha/Desktop/manager_buysell")
out_folder.mkdir(parents=True, exist_ok=True)

# 1. LOAD THE TICKER MAP ONCE
raw_tick_map = pd.read_pickle(PICKLE_PATH)
raw_tick_map.columns = raw_tick_map.columns.str.strip().str.lower()
dedup_key = "cusip9" if "cusip9" in raw_tick_map.columns else ("cusip8" if "cusip8" in raw_tick_map.columns else raw_tick_map.columns[0])
raw_tick_map[dedup_key] = raw_tick_map[dedup_key].astype(str).str.slice(0, 8).str.strip().str.upper()
tick_lookup = raw_tick_map.drop_duplicates(dedup_key, keep="last").set_index(dedup_key)["ticker"]

# 2. LOAD & AGGREGATE CURRENT QUARTER (ALL MANAGERS)
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
    (df_curr_raw["cusip"].str.len() <= 10)
]
df_curr_all = df_curr_clean.groupby(["cik", "cusip"], as_index=False).agg(
    value=("value", "sum"),
    shares=("shares", "sum"),
    manager_name=("manager_name", "first"),
)

# 3. LOAD & AGGREGATE PRIOR QUARTER (ALL MANAGERS)
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
    (df_prior_raw["cusip"].str.len() <= 10)
]
df_prior_all = df_prior_clean.groupby(["cik", "cusip"], as_index=False).agg(
    value=("value", "sum"),
    shares=("shares", "sum"),
    manager_name=("manager_name", "first"),
)

# 4. LOOP THROUGH ALL MANAGERS
all_ciks = df_curr_all["cik"].unique()
total_managers = len(all_ciks)
print(f"Processing {total_managers} managers...")

for i, target_cik in enumerate(all_ciks, start=1):
    print(f"Processing manager {i}/{total_managers} (CIK: {target_cik})", end="\r")

    df_curr = df_curr_all[df_curr_all["cik"] == target_cik]
    df_prior = df_prior_all[df_prior_all["cik"] == target_cik]

    # NOTE: pandas merge doesn't have how="union" -- the union/full-outer
    # join is called "outer". Passing an invalid how value can fail deep
    # inside pandas' internals with a confusing error (e.g. UnboundLocalError:
    # 'lidx') instead of a clean "invalid how" message.
    diff = df_curr.merge(df_prior, how="outer", on=["cik", "cusip"], suffixes=("_curr", "_prior"))
    diff["manager_name"] = diff["manager_name_curr"].fillna(diff["manager_name_prior"])

    diff[["value_curr", "value_prior", "shares_curr", "shares_prior"]] = diff[
        ["value_curr", "value_prior", "shares_curr", "shares_prior"]
    ].astype(float).fillna(0)

    # ---- classify the position change based on SHARES (unambiguous signal) ----
    diff["classification"] = "unchanged"
    diff.loc[(diff["shares_prior"] == 0) & (diff["shares_curr"] > 0), "classification"] = "new position"
    diff.loc[(diff["shares_curr"] == 0) & (diff["shares_prior"] > 0), "classification"] = "exited"
    diff.loc[
        (diff["shares_curr"] > diff["shares_prior"]) & (diff["shares_prior"] > 0),
        "classification",
    ] = "increased"
    diff.loc[
        (diff["shares_curr"] < diff["shares_prior"]) & (diff["shares_curr"] > 0),
        "classification",
    ] = "trimmed"

    shares_diff = diff["shares_curr"] - diff["shares_prior"]
    diff["shares_bought"] = shares_diff.clip(lower=0)
    diff["shares_sold"] = (-shares_diff).clip(lower=0)

    price_curr = (diff["value_curr"] / diff["shares_curr"]).replace([float("inf"), -float("inf")], pd.NA)
    price_prior = (diff["value_prior"] / diff["shares_prior"]).replace([float("inf"), -float("inf")], pd.NA)

    price_for_buys = price_curr.fillna(price_prior).fillna(0)
    price_for_sells = price_prior.fillna(price_curr).fillna(0)

    diff["value_bought"] = (diff["shares_bought"] * price_for_buys).fillna(0.0).round(2)
    diff["value_sold"] = (diff["shares_sold"] * price_for_sells).fillna(0.0).round(2)
    
    diff["prior_val"] = diff["value_prior"]
    diff["curr_val"] = diff["value_curr"]
    diff["diff_val"] = diff["curr_val"] - diff["prior_val"]

    diff["prior_quantity"] = diff["shares_prior"]
    diff["curr_quantity"] = diff["shares_curr"]
    diff["diff_quantity"] = diff["curr_quantity"] - diff["prior_quantity"]
    
    diff = diff.rename(columns={"cik": "CIK"})

    diff["shares_bought"] = diff["shares_bought"].fillna(0).astype(int)
    diff["shares_sold"] = diff["shares_sold"].fillna(0).astype(int)
    diff["ticker"] = diff["cusip"].str.slice(0, 8).str.upper().map(tick_lookup)
    
    diff["type"] = "unchanged"
    diff.loc[(diff["prior_quantity"] > 0) & (diff["curr_quantity"] == 0), "type"] = "exited"
    diff.loc[(diff["prior_quantity"] == 0) & (diff["curr_quantity"] > 0), "type"] = "new"
    diff.loc[(diff["prior_quantity"] > 0) & (diff["curr_quantity"] > 0) & (diff["curr_quantity"] < diff["prior_quantity"]), "type"] = "trimmed"
    diff.loc[(diff["prior_quantity"] > 0) & (diff["curr_quantity"] > 0) & (diff["curr_quantity"] > diff["prior_quantity"]), "type"] = "increased"
    diff.loc[(diff["prior_quantity"] > 0) & (diff["curr_quantity"] > 0) & (diff["curr_quantity"] == diff["prior_quantity"]), "type"] = "unchanged"
    
    diff["portfolio_weight"] = (diff["curr_val"] / diff["curr_val"].sum()).round(2)

    diff = diff.rename(columns={"cik": "CIK", "manager_name": "name"})
    output_cols = [
        "CIK", "name", "cusip", "ticker", 
        "value_bought", "value_sold", "shares_bought", "shares_sold",
        "prior_val", "curr_val", "diff_val", 
        "prior_quantity", "curr_quantity", "diff_quantity", "type", "portfolio_weight"
    ]
    diff = diff[output_cols]

    out_file = out_folder / f"buysell_{target_cik}.csv"
    diff.to_csv(out_file, index=False)

print("\nAll managers processed.")