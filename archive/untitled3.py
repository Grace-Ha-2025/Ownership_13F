#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Jul  9 21:25:02 2026
@author: graceha
"""
# ARCHIVED: never renamed from Spyder's default filename. Exploratory script
# that (1) rebuilds a cusip->titleOfClass map from the per-manager holdings
# files, (2) merges it against a cusip/ticker pickle, and (3) counts how many
# rows land in each titleOfClass (e.g. ADR) to sanity-check ticker coverage.
# This is the fuller, actually-runnable version of the same idea started in
# archive/cusip_type_mapping.py. Kept for reference only — the ticker join it
# explores is now done inline by pipeline/aggregate_13f.py.
from collections import Counter
from pathlib import Path
import pandas as pd

# ==========================================
# 1. PATHS & CONFIGURATION
# ==========================================
folder = Path("/Users/graceha/Desktop/Ownership-13F-data/2025q4/holdings_by_manager_2025q4")
sentiment_path = Path("/Users/graceha/Desktop/Ownership-13F-data/stock_sentiment_2025q4_to_2026q1.csv")

PICKLE_PATH = Path("/Users/graceha/Downloads/cusip_ticker_mapping/cusip_ticker_map_20260331.pickle")
if not PICKLE_PATH.exists():
  PICKLE_PATH = Path("/Users/graceha/Desktop/Ownership-13F/cusip_ticker_map_20260331.pickle")

out_path = Path("/Users/graceha/Desktop/cusip_type_map.csv")

# ==========================================
# 2. PROCESS 13F FILES TO BUILD CUSIP-TITLE MAP
# ==========================================
files = list(folder.glob("*.csv"))
counts = Counter()  # (cusip9, titleOfClass) -> count
skipped = []

for i, f in enumerate(files):
  try:
    df_chunk = pd.read_csv(
        f,
        dtype=str,
        usecols=lambda c: c.strip().lower() in ("cusip", "titleofclass"),
    )
    df_chunk.columns = df_chunk.columns.str.strip().str.lower()

    # Safe cusip9 extraction from Script 1
    for cusip, title in zip(df_chunk["cusip"], df_chunk["titleofclass"]):
      cusip9 = (
          str(cusip).strip()[:-1]
          if pd.notna(cusip) and len(str(cusip).strip()) > 1
          else cusip
      )
      counts[(cusip9, title)] += 1

  except Exception as e:
    skipped.append((f.name, str(e)))

  if i % 500 == 0:
    print(f"{i}/{len(files)} processed")

print(f"done. {len(skipped)} files skipped.")

# Build Dataframe & Map
cusip_types = pd.DataFrame(
    [(c, t, n) for (c, t), n in counts.items()],
    columns=["cusip9", "titleOfClass", "count"], 
).sort_values(["cusip9", "count"], ascending=[True, False])

cusip_type_map = (
    cusip_types.drop_duplicates("cusip9").set_index("cusip9")["titleOfClass"]
)

# Write output map
cusip_type_map_df = cusip_type_map.reset_index()
cusip_type_map_df.columns = ["cusip9", "titleOfClass"]
cusip_type_map_df.to_csv(out_path, index=False)
print(f"saved {len(cusip_type_map_df)} rows to {out_path} (Exists: {out_path.exists()})")

# ==========================================
# 3. MERGE WITH TICKER MAP & SENTIMENT DATA
# ==========================================
# Load ticker map pickle (First pass as DataFrame)

raw_tick_map = pd.read_pickle(PICKLE_PATH)
if hasattr(raw_tick_map, "columns") and "cusip9" in raw_tick_map.columns:
  raw_tick_map["cusip8"] = raw_tick_map["cusip9"].astype(str).str.slice(0, 8)

# Load sentiment data
df_sentiment = pd.read_csv(sentiment_path, dtype={"cusip9": str})

# Ensure cusip9/cusip8 exist on df_sentiment if needed for the first merge
df_sentiment["cusip8"] = df_sentiment["cusip"].astype(str).apply(lambda x: x[:8])
df_temp = df_sentiment.merge(raw_tick_map, how = 'left', on='cusip8')

# Handle the dictionary conversion safely as a DataFrame
raw_tick_map_dict = pd.read_pickle(PICKLE_PATH)
tick_map = {
    str(k).strip().upper().zfill(9): str(v)
    for k, v in raw_tick_map_dict.items()
}
tick_map_df = pd.DataFrame(
    list(tick_map.items()), columns=["cusip8", "ticker"]
)

# Merge using the converted DataFrame instead of a plain dict
df_cusip_ticker_mapping = df_temp.merge(tick_map_df[['cusip8']], how="left", on="cusip8")

cusip_type_map_df['cusip8'] = cusip_type_map_df["cusip9"].astype(str).apply(lambda x: x[:8])

df_cutick_type_map = df_cusip_ticker_mapping[['ticker_y', 'ticker_x', 'cusip8']].merge(
    cusip_type_map_df, 
    how='left', 
    on='cusip8'
)

# ========================================================
# 4. QUANTIFYING THE NUMBER OF EACH STOCK (WITH FILTERING)
# ========================================================

print(df_cutick_type_map[df_cutick_type_map['ticker_y'].isnull()])
typecnt = df_cutick_type_map[df_cutick_type_map['ticker_y'].isnull()].groupby('titleOfClass')['ticker_y'].apply(lambda x:  len(x))
print(typecnt.index)
print(typecnt.index=='ADR')
print(typecnt[typecnt.index=='ADR'])

df_cutick_type_map['ticker_merged'] = df_cutick_type_map['ticker_y'].fillna(df_cutick_type_map['ticker_x'])
print(df_cutick_type_map[df_cutick_type_map['ticker_y'].isnull()]['ticker_merged'].unique())
print(len(df_cutick_type_map[~df_cutick_type_map['ticker_merged'].isnull()]['ticker_merged'].unique()))

