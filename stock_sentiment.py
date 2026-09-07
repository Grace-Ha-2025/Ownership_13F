#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Jul  9 16:07:11 2026

@author: graceha
"""

import pandas as pd
from pathlib import Path

# Point this to your actual CSV file
sentiment_path = Path("/Users/graceha/Desktop/Ownership-13F-data/stock_sentiment_2025q4_to_2026q1.csv")

# 1. Load the sentiment/diff CSV file
df = pd.read_csv(sentiment_path, dtype={"cusip": str})
print("Columns in file:", df.columns.tolist())

# 2. Point this to your pickle file
PICKLE_PATH = Path("/Users/graceha/Downloads/cusip_ticker_mapping/cusip_ticker_map_20260331.pickle")
if not PICKLE_PATH.exists():
    PICKLE_PATH = Path("/Users/graceha/Desktop/Ownership-13F/cusip_ticker_map_20260331.pickle")

raw_tick_map = pd.read_pickle(PICKLE_PATH)
map_col = ['cusip9', 'ticker']
cusip_ticker_map = raw_tick_map[map_col]

df['cusip8'] = df['cusip'].apply(lambda x: x[:-1])
df_temp = df.merge(raw_tick_map, how='left', left_on = 'cusip8', right_on = 'cusip8')

'''
tick_map = {str(k).strip().upper().zfill(9): str(v) for k, v in raw_tick_map.items()}

# 3. Create a clean padded CUSIP lookup column
df["cusip_padded"] = df["cusip"].fillna("").astype(str).str.strip().str.upper().str.zfill(9)

# 4. Map the ticker column using zero-padded CUSIP matching
df["ticker"] = df["cusip_padded"].map(tick_map).fillna(df.get("ticker", ""))

# 5. Drop the helper column and ensure 'ticker' is positioned first
df = df.drop(columns=["cusip_padded"])
cols = ["ticker"] + [c for c in df.columns if c != "ticker"]
df = df[cols]

# 6. Save back out to the exact same CSV path
df.to_csv(sentiment_path, index=False)
print(f"Successfully merged/patched tickers into -> {sentiment_path}")
'''