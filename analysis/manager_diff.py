"""
Compare one manager's 13F portfolio between two quarters.

Reads the per-manager holdings files from each quarter's folder
(BASE_DIR/<quarter>/holdings_by_manager_<quarter>/), merges on CUSIP,
and reports value / share / weight changes with an action label:
NEW, EXITED, ADDED, TRIMMED, UNCHANGED (action is keyed to SHARE count,
since value moves with price even when the manager did nothing).

USAGE
-----
    python3 manager_diff.py                                # Berkshire, 2025q4 -> 2026q1
    python3 manager_diff.py --cik 1067983 --from 2025q4 --to 2026q1
    python3 manager_diff.py --cik 2045724 --from 2025q4 --to 2026q1

Output: <BASE_DIR>/manager_diff_<cik>_<from>_to_<to>.csv
"""

import argparse
import glob
import sys

import pandas as pd

BASE_DIR = "/Users/graceha/Desktop/Ownership-13F"

def load_ticker_map():
    """cusip (leading zeros stripped) -> ticker, or {} if no map file found.

    The SEC data has no tickers, so we join against the market summary CSV
    built earlier. Searched anywhere up to 3 levels deep under BASE_DIR, so
    it keeps working when folders get reorganized. Newest quarter wins."""
    candidates = []
    for depth in ["", "/*", "/*/*", "/*/*/*"]:
        candidates += glob.glob(
            f"{BASE_DIR}{depth}/institutional_market_summary_*.csv")
    if not candidates:
        print("NOTE: no institutional_market_summary_*.csv found anywhere "
              "under Ownership-13F — ticker column will be blank.")
        return {}
    path = sorted(candidates)[-1]  # newest quarter if several
    print(f"(ticker map: {path})")
    m = pd.read_csv(path, usecols=["cusip", "ticker"], dtype={"cusip": str})
    m["cusip"] = m["cusip"].str.strip().str.upper().str.lstrip("0")
    return dict(zip(m["cusip"], m["ticker"].fillna("")))


def load_quarter(quarter, cik):
    pattern = f"{BASE_DIR}/{quarter}/holdings_by_manager_{quarter}/{cik}_*.csv"
    matches = glob.glob(pattern)
    if not matches:
        sys.exit(f"ERROR: no file found for CIK {cik} in quarter {quarter}.\n"
                 f"Looked for: {pattern}\n"
                 f"Make sure that quarter has been downloaded and split "
                 f"(download_13f_code.py --quarters {quarter}).")
    df = pd.read_csv(matches[0], dtype={"cusip": str})
    # STOCK ONLY — two exclusions:
    #  1. put/call option rows (option "shares" are controlled, not owned,
    #     and puts are bearish — they distort share counts, e.g. NVDA)
    #  2. PRN rows: bonds/convertible notes, where the "shares" field is
    #     principal in DOLLARS, not a share count (e.g. MARA notes)
    n_before = len(df)
    if "putCall" in df.columns:
        df = df[df["putCall"].fillna("").astype(str).str.strip() == ""]
    if "sshPrnamtType" in df.columns:
        df = df[df["sshPrnamtType"].fillna("SH").astype(str)
                .str.strip().str.upper() == "SH"]
    # Belt-and-suspenders: some filers mislabel bonds as SH — catch them by
    # the class text (e.g. "NOTE 3.500% 6/0", "SR NOTES", "DEBENTURE").
    if "titleOfClass" in df.columns:
        df = df[~df["titleOfClass"].fillna("").astype(str).str.upper()
                .str.contains(r"\bNOTES?\b|\bBONDS?\b|DEBENTURE|\d+\.\d+%",
                              regex=True)]
    n_dropped = n_before - len(df)
    if n_dropped:
        print(f"  ({quarter}: excluded {n_dropped} option/bond rows — "
              f"diff covers common stock only)")
    if "titleOfClass" not in df.columns:
        df["titleOfClass"] = ""
    g = df.groupby("cusip").agg(name=("nameOfIssuer", "first"),
                                share_class=("titleOfClass", "first"),
                                shares=("shares", "sum"),
                                value=("value", "sum"))
    g["weight"] = (g["value"] / g["value"].sum() * 100).round(2)
    return g


def action(row):
    if row.value_old == 0:
        return "NEW"
    if row.value_new == 0:
        return "EXITED"
    if row.shares_chg > 0:
        return "ADDED"
    if row.shares_chg < 0:
        return "TRIMMED"
    return "UNCHANGED"


def main():
    p = argparse.ArgumentParser(description="Diff one manager between two quarters.")
    p.add_argument("--cik", type=int, default=1067983,
                   help="Manager CIK (default 1067983 = Berkshire Hathaway)")
    p.add_argument("--from", dest="q_from", default="2025q4",
                   help="Earlier quarter (default 2025q4)")
    p.add_argument("--to", dest="q_to", default="2026q1",
                   help="Later quarter (default 2026q1)")
    p.add_argument("--random", action="store_true",
                   help="Ignore --cik and pick a random manager that filed "
                        "in BOTH quarters.")
    p.add_argument("--sort", choices=["value", "shares"], default="value",
                   help="Rank moves by dollar change (default) or by "
                        "share-count change.")
    p.add_argument("--top", type=int, default=25,
                   help="How many rows to print (default 25; CSV always "
                        "contains everything).")
    args = p.parse_args()

    if args.random:
        import random

        def ciks_in(quarter):
            path = f"{BASE_DIR}/{quarter}/holdings_by_manager_{quarter}/_index.csv"
            idx = pd.read_csv(path)
            idx = idx[idx["num_holdings_written"] > 0]
            return dict(zip(idx["cik"].astype(int), idx["manager_name"]))

        old_ciks = ciks_in(args.q_from)
        new_ciks = ciks_in(args.q_to)
        both = sorted(set(old_ciks) & set(new_ciks))
        args.cik = random.choice(both)
        print(f"Randomly picked: {new_ciks[args.cik]} (CIK {args.cik}) "
              f"out of {len(both):,} managers present in both quarters")

    old = load_quarter(args.q_from, args.cik)
    new = load_quarter(args.q_to, args.cik)

    cmp = old.join(new, lsuffix="_old", rsuffix="_new", how="outer")
    cmp["name"] = cmp["name_new"].fillna(cmp["name_old"])
    cmp["share_class"] = cmp["share_class_new"].fillna(cmp["share_class_old"])
    # Distinguish multi-class listings (e.g. Alphabet Class A vs Class C):
    # append the class whenever the same issuer name appears more than once.
    dup_names = cmp["name"].duplicated(keep=False)
    cmp.loc[dup_names, "name"] = (cmp.loc[dup_names, "name"] + " ["
                                  + cmp.loc[dup_names, "share_class"].fillna("?")
                                  + "]")
    for c in ["value_old", "value_new", "shares_old", "shares_new",
              "weight_old", "weight_new"]:
        cmp[c] = cmp[c].fillna(0)

    cmp["value_chg"] = cmp["value_new"] - cmp["value_old"]
    denom = cmp["value_old"].replace(0, float("nan"))
    cmp["value_chg_pct"] = (cmp["value_chg"] / denom * 100).round(1)
    cmp["shares_chg"] = cmp["shares_new"] - cmp["shares_old"]
    cmp["action"] = cmp.apply(action, axis=1)

    tickers = load_ticker_map()
    cmp["ticker"] = [tickers.get(str(c).strip().upper().lstrip("0"), "")
                     for c in cmp.index]

    # Identify the MANAGER on every row (useful when combining diff files).
    # NOTE: this is the manager's CIK, not the company's — companies are
    # identified by CUSIP (13F data carries no issuer CIKs).
    cmp["manager_cik"] = args.cik
    manager_file = glob.glob(
        f"{BASE_DIR}/{args.q_to}/holdings_by_manager_{args.q_to}/{args.cik}_*.csv"
    ) or glob.glob(
        f"{BASE_DIR}/{args.q_from}/holdings_by_manager_{args.q_from}/{args.cik}_*.csv"
    )
    mgr_name = ""
    if manager_file:
        import os
        mgr_name = (os.path.basename(manager_file[0])
                    .split("_", 1)[1].rsplit(".csv", 1)[0])
    cmp["manager_name"] = mgr_name

    out = cmp[["manager_cik", "manager_name", "ticker", "name", "share_class",
               "action", "value_old", "value_new", "value_chg",
               "value_chg_pct", "shares_old", "shares_new", "shares_chg",
               "weight_old", "weight_new"]]
    out = out.rename(columns={
        "value_old": f"value_{args.q_from}", "value_new": f"value_{args.q_to}",
        "shares_old": f"shares_{args.q_from}", "shares_new": f"shares_{args.q_to}",
        "weight_old": f"weight_{args.q_from}", "weight_new": f"weight_{args.q_to}",
    })
    sort_col = "value_chg" if args.sort == "value" else "shares_chg"
    out = out.sort_values(sort_col, key=abs, ascending=False)

    print(f"\n=== CIK {args.cik}: {args.q_from} -> {args.q_to} "
          f"(sorted by |{sort_col}|) ===\n")
    print(out.head(args.top).to_string())
    print("\nAction counts:")
    print(out["action"].value_counts().to_string())

    out_path = f"{BASE_DIR}/manager_diff_{args.cik}_{args.q_from}_to_{args.q_to}.csv"
    out.to_csv(out_path)
    print(f"\nSaved full table -> {out_path}")


if __name__ == "__main__":
    main()
