#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Jul 14 20:28:49 2026

@author: graceha
"""

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Computes per-manager buy/sell activity (value_bought, value_sold,
shares_bought, shares_sold, classification, ticker, portfolio_weight_pct)
between any two quarters. Writes one CSV per manager.

USAGE
-----
    python3 manager_buysell.py --curr 2025q3 --prior 2025q2
    python3 manager_buysell.py --curr 2026q1 --prior 2025q4

Assumes each quarter's combined holdings file already exists at:
    BASE_DIR/<quarter>/all_manager_holdings_<quarter>.csv
(built by aggregation_13f_code.py --quarter <quarter> --combined)
"""
import argparse
import sys
import warnings
from pathlib import Path

import pandas as pd

warnings.simplefilter(action="ignore", category=FutureWarning)

BASE_DIR = Path("/Users/graceha/Desktop/Ownership-13F")

PICKLE_PATH = Path("/Users/graceha/Downloads/cusip_ticker_mapping/cusip_ticker_map_20260331.pickle")
if not PICKLE_PATH.exists():
    PICKLE_PATH = Path("/Users/graceha/Desktop/Ownership-13F/cusip_ticker_map_20260331.pickle")


def load_holdings(quarter):
    path = BASE_DIR / quarter / f"all_manager_holdings_{quarter}.csv"
    if not path.exists():
        sys.exit(
            f"ERROR: {path} not found.\n"
            f"Run: python3 aggregation_13f_code.py --quarter {quarter} --combined"
        )

    df_raw = pd.read_csv(path, dtype=str)
    df_raw.columns = df_raw.columns.str.strip().str.lower()
    df_raw["value"] = pd.to_numeric(df_raw["value"], errors="coerce")
    df_raw["shares"] = pd.to_numeric(df_raw["shares"], errors="coerce")
    df_raw["cusip"] = df_raw["cusip"].astype(str).str.strip()
    df_raw["cik"] = df_raw["cik"].astype(str).str.strip()
    df_raw["portfolio_weight_pct"] = pd.to_numeric(df_raw["portfolio_weight_pct"], errors="coerce")

    df_clean = df_raw[
        df_raw["cusip"].notna() &
        (df_raw["cusip"] != "") &
        (df_raw["cusip"].str.lower() != "nan") &
        (~df_raw["cusip"].str.contains(r"E\+", case=False, na=False)) &
        (df_raw["cusip"].str.len() <= 10)
    ]
    return df_clean.groupby(["cik", "cusip"], as_index=False).agg(
        value=("value", "sum"),
        shares=("shares", "sum"),
        manager_name=("manager_name", "first"),
        portfolio_weight_pct=("portfolio_weight_pct", "sum"),
    )


def main():
    parser = argparse.ArgumentParser(
        description="Compute per-manager buy/sell activity between two quarters."
    )
    parser.add_argument("--curr", required=True, help="Current quarter, e.g. 2025q3")
    parser.add_argument("--prior", required=True, help="Prior quarter to diff against, e.g. 2025q2")
    args = parser.parse_args()

    out_folder = BASE_DIR / f"manager_buysell_{args.curr}"
    out_folder.mkdir(parents=True, exist_ok=True)

    # ---- ticker map (loaded once) ----
    raw_tick_map = pd.read_pickle(PICKLE_PATH)
    raw_tick_map.columns = raw_tick_map.columns.str.strip().str.lower()
    dedup_key = "cusip9" if "cusip9" in raw_tick_map.columns else (
        "cusip8" if "cusip8" in raw_tick_map.columns else raw_tick_map.columns[0]
    )
    raw_tick_map[dedup_key] = raw_tick_map[dedup_key].astype(str).str.slice(0, 8).str.strip().str.upper()
    tick_lookup = raw_tick_map.drop_duplicates(dedup_key, keep="last").set_index(dedup_key)["ticker"]

    print(f"Loading {args.curr} (current) and {args.prior} (prior)...")
    df_curr_all = load_holdings(args.curr)
    df_prior_all = load_holdings(args.prior)

    all_ciks = df_curr_all["cik"].unique()
    total_managers = len(all_ciks)
    print(f"Processing {total_managers} managers...")

    for i, target_cik in enumerate(all_ciks, start=1):
        print(f"Processing manager {i}/{total_managers} (CIK: {target_cik})", end="\r")

        df_curr = df_curr_all[df_curr_all["cik"] == target_cik]
        df_prior = df_prior_all[df_prior_all["cik"] == target_cik]

        diff = df_curr.merge(df_prior, how="outer", on=["cik", "cusip"], suffixes=("_curr", "_prior"))
        diff["manager_name"] = diff["manager_name_curr"].fillna(diff["manager_name_prior"])

        diff[["value_curr", "value_prior", "shares_curr", "shares_prior"]] = diff[
            ["value_curr", "value_prior", "shares_curr", "shares_prior"]
        ].astype(float).fillna(0)

        # ---- classify based on SHARES (unambiguous signal, not value) ----
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

        diff["shares_bought"] = diff["shares_bought"].fillna(0).astype(int)
        diff["shares_sold"] = diff["shares_sold"].fillna(0).astype(int)

        if diff.empty:
            continue

        diff["ticker"] = diff["cusip"].str.slice(0, 8).str.upper().map(tick_lookup)
        diff["portfolio_weight_pct"] = diff["portfolio_weight_pct"].fillna(0)

        diff = diff.rename(columns={"cik": "CIK", "manager_name": "name"})
        output_cols = ["CIK", "name", "cusip", "ticker", "classification",
                       "value_bought", "value_sold", "shares_bought", "shares_sold",
                       "portfolio_weight_pct"]
        diff = diff[output_cols]

        out_file = out_folder / f"buysell_{target_cik}.csv"
        diff.to_csv(out_file, index=False)

    print(f"\nAll managers processed. Output in {out_folder}")


if __name__ == "__main__":
    main()