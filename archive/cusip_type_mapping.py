#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Jul  9 21:25:02 2026
@author: graceha
"""
# ARCHIVED — NOT RUNNABLE AS-IS: past line 40 this references `sentiment_path`,
# `class_df`, and `tick_map_df`, none of which are defined in this file (they
# only exist in the untitled3.py / manager_mapping.py sessions this was
# pasted from). It will raise NameError if executed past the cusip_type_map
# build. Kept as a historical record of the exploration; the working version
# of this idea is archive/untitled3.py, and the ticker join it was building
# toward is now done inline by pipeline/aggregate_13f.py.
import pandas as pd
from pathlib import Path
from collections import Counter

folder = Path("/Users/graceha/Desktop/Ownership-13F-data/2025q4/holdings_by_manager_2025q4")
files = list(folder.glob("*.csv"))
counts = Counter()  # (cusip, titleOfClass) -> count
skipped = []
for i, f in enumerate(files):
    try:
        df = pd.read_csv(f, dtype=str, usecols=lambda c: c.strip().lower() in ("cusip", "titleofclass"))
        df.columns = df.columns.str.strip().str.lower()
        for cusip, title in zip(df["cusip"], df["titleofclass"]):
            counts[(cusip, title)] += 1
    except Exception as e:
        skipped.append((f.name, str(e)))
    if i % 500 == 0:
        print(f"{i}/{len(files)} processed")
print(f"done. {len(skipped)} files skipped.")

cusip_types = (
    pd.DataFrame([(c, t, n) for (c, t), n in counts.items()], columns=["cusip", "titleOfClass", "count"])
    .sort_values(["cusip", "count"], ascending=[True, False])
)
cusip_type_map = cusip_types.drop_duplicates("cusip").set_index("cusip")["titleOfClass"]

# --- write output ---
out_path = Path("/Users/graceha/Desktop/cusip_type_map.csv")
cusip_type_map_df = cusip_type_map.reset_index()
cusip_type_map_df.columns = ["cusip", "titleOfClass"]
cusip_type_map_df.to_csv(out_path, index=False)
print(f"saved {len(cusip_type_map_df)} rows to {out_path}")
print(out_path.exists())

df = pd.read_csv(sentiment_path, dtype={"cusip": str})
print("Columns in file:", df.columns.tolist())

# 2. Point this to your pickle file
PICKLE_PATH = Path("/Users/graceha/Downloads/cusip_ticker_mapping/cusip_ticker_map_20260331.pickle")
if not PICKLE_PATH.exists():
    PICKLE_PATH = Path("/Users/graceha/Desktop/Ownership-13F/cusip_ticker_map_20260331.pickle")
    
sentiment_path = Path("/Users/graceha/Desktop/Ownership-13F-data/stock_sentiment_2025q4_to_2026q1.csv")

raw_tick_map = pd.read_pickle(PICKLE_PATH)
map_col = ['cusip9', 'ticker']

cusip_ticker_map = raw_tick_map[map_col]

df['cusip8'] = df['cusip'].apply(lambda x: x[:-1])
df_temp = df.merge(raw_tick_map, how='left', left_on = 'cusip8', right_on = 'cusip8')

df_temp = df_temp.merge(class_df, how='left', on='cusip')
df_cutick_type = df_temp.merge(tick_map_df, how='left', on='cusip')
df_cutick_type.loc[df_cutick_type['titleOfClass'].isin(['COM', 'SH', 'SHS', 'ORD', 'ORD SHS', 'CL A', 'Class A', 'CL B', 'Class B', 'COM NEW', 'UNIT', 'ADS', 'ADR'])]

