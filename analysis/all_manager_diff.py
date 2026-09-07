"""
Quarter-over-quarter diff for EVERY manager at once.

Loops over the union of managers in two quarters' holdings folders
(BASE_DIR/<quarter>/holdings_by_manager_<quarter>/), diffs each manager's
portfolio at the CUSIP level (same logic as manager_diff.py), and writes:

  1. manager_diff_summary_<from>_to_<to>.csv   — ONE ROW PER MANAGER:
         cik, manager_name, filer_status (BOTH / NEW_FILER / STOPPED_FILING),
         positions_<from>, positions_<to>, n_new, n_exited, n_added,
         n_trimmed, n_unchanged, value_<from>, value_<to>, value_chg
  2. manager_diff_detail_<from>_to_<to>.csv    — ONE ROW PER POSITION CHANGE
         (all managers stacked; skip with --no-detail if you only want the
         summary — the detail file is large, roughly the size of the two
         quarters' holdings combined)

Action labels are keyed to SHARE count (value moves with price even when
the manager did nothing): NEW, EXITED, ADDED, TRIMMED, UNCHANGED.

USAGE
-----
    python3 all_manager_diff.py                          # 2025q4 -> 2026q1, everything
    python3 all_manager_diff.py --limit 100              # test batch: first 100 managers
    python3 all_manager_diff.py --no-detail              # summary only (much smaller output)
    python3 all_manager_diff.py --from 2025q4 --to 2026q1
"""

import argparse
import csv
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path("/Users/graceha/Desktop/Ownership-13F")


def load_index(quarter):
    path = BASE_DIR / quarter / f"holdings_by_manager_{quarter}" / "_index.csv"
    if not path.exists():
        sys.exit(f"ERROR: {path} not found — run "
                 f"download_13f_code.py --quarters {quarter} first.")
    idx = pd.read_csv(path)
    idx = idx[idx["num_holdings_written"] > 0]
    return {int(r.cik): (str(r.manager_name), r.filename)
            for r in idx.itertuples(index=False)}


def load_manager(quarter, filename):
    path = BASE_DIR / quarter / f"holdings_by_manager_{quarter}" / filename
    if not path.exists():
        return None
    df = pd.read_csv(path, dtype={"cusip": str})
    # STOCK ONLY: drop put/call option rows (option "shares" are controlled,
    # not owned; puts are bearish) AND bond/note rows (sshPrnamtType "PRN",
    # where "shares" is principal in dollars, not a share count).
    if "putCall" in df.columns:
        df = df[df["putCall"].fillna("").astype(str).str.strip() == ""]
    if "sshPrnamtType" in df.columns:
        df = df[df["sshPrnamtType"].fillna("SH").astype(str)
                .str.strip().str.upper() == "SH"]
    if "titleOfClass" not in df.columns:
        df["titleOfClass"] = ""
    # Catch bonds mislabeled as SH by their class text.
    df = df[~df["titleOfClass"].fillna("").astype(str).str.upper()
            .str.contains(r"\bNOTES?\b|\bBONDS?\b|DEBENTURE|\d+\.\d+%",
                          regex=True)]

    df["cusip"] = df["cusip"].str.strip().str.upper()
    
    g = df.groupby("cusip").agg(name=("nameOfIssuer", "first"),
                                share_class=("titleOfClass", "first"),
                                shares=("shares", "sum"),
                                value=("value", "sum"))
    return g


def action(shares_old, shares_new, value_old, value_new):
    if value_old == 0:
        return "NEW"
    if value_new == 0:
        return "EXITED"
    if shares_new > shares_old:
        return "ADDED"
    if shares_new < shares_old:
        return "TRIMMED"
    return "UNCHANGED"


def main():
    p = argparse.ArgumentParser(description="Diff ALL managers between two quarters.")
    p.add_argument("--from", dest="q_from", default="2025q4")
    p.add_argument("--to", dest="q_to", default="2026q1")
    p.add_argument("--limit", type=int, default=None,
                   help="Only process the first N managers (test batches).")
    p.add_argument("--no-detail", action="store_true",
                   help="Skip the big per-position detail file; summary only.")
    args = p.parse_args()

    idx_old = load_index(args.q_from)
    idx_new = load_index(args.q_to)
    all_ciks = sorted(set(idx_old) | set(idx_new))
    if args.limit:
        all_ciks = all_ciks[:args.limit]
    total = len(all_ciks)
    print(f"Diffing {total:,} managers: {args.q_from} -> {args.q_to} ...")

    summary_path = BASE_DIR / f"manager_diff_summary_{args.q_from}_to_{args.q_to}.csv"
    detail_path = BASE_DIR / f"manager_diff_detail_{args.q_from}_to_{args.q_to}.csv"

    summary_f = open(summary_path, "w", newline="")
    summary_w = csv.writer(summary_f)
    summary_w.writerow(["cik", "manager_name", "filer_status",
                        f"positions_{args.q_from}", f"positions_{args.q_to}",
                        "n_new", "n_exited", "n_added", "n_trimmed",
                        "n_unchanged", f"value_{args.q_from}",
                        f"value_{args.q_to}", "value_chg"])

    detail_w = None
    if not args.no_detail:
        detail_f = open(detail_path, "w", newline="")
        detail_w = csv.writer(detail_f)
        detail_w.writerow(["cik", "manager_name", "cusip", "name",
                           "share_class", "action",
                           f"value_{args.q_from}", f"value_{args.q_to}",
                           "value_chg", f"shares_{args.q_from}",
                           f"shares_{args.q_to}", "shares_chg"])

    # Per-STOCK sentiment accumulator: for each cusip, how many managers
    # bought/sold/held between the two quarters.
    stock_stats = {}

    for i, cik in enumerate(all_ciks, 1):
        name_old, file_old = idx_old.get(cik, (None, None))
        name_new, file_new = idx_new.get(cik, (None, None))
        manager_name = name_new or name_old
        if file_old and file_new:
            filer_status = "BOTH"
        elif file_new:
            filer_status = "NEW_FILER"
        else:
            filer_status = "STOPPED_FILING"

        old = load_manager(args.q_from, file_old) if file_old else None
        new = load_manager(args.q_to, file_new) if file_new else None
        if old is None and new is None:
            continue

        empty = pd.DataFrame(columns=["name", "share_class", "shares", "value"])
        old = old if old is not None else empty
        new = new if new is not None else empty
        cmp = old.join(new, lsuffix="_old", rsuffix="_new", how="outer")
        for c in ["shares_old", "shares_new", "value_old", "value_new"]:
            cmp[c] = pd.to_numeric(cmp[c], errors="coerce").fillna(0)
        cmp["name"] = cmp["name_new"].fillna(cmp["name_old"])
        cmp["share_class"] = cmp["share_class_new"].fillna(cmp["share_class_old"])

        counts = {"NEW": 0, "EXITED": 0, "ADDED": 0, "TRIMMED": 0, "UNCHANGED": 0}
        for row in cmp.itertuples():
            act = action(row.shares_old, row.shares_new,
                         row.value_old, row.value_new)
            counts[act] += 1
            ss = stock_stats.setdefault(row.Index, {
                "name": row.name, "NEW": 0, "ADDED": 0, "TRIMMED": 0,
                "EXITED": 0, "UNCHANGED": 0,
                "shares_bought": 0, "shares_sold": 0,
                "value_bought": 0, "value_sold": 0})
            ss[act] += 1
            s_chg = row.shares_new - row.shares_old
            v_chg = row.value_new - row.value_old
            if s_chg > 0:
                ss["shares_bought"] += s_chg
            elif s_chg < 0:
                ss["shares_sold"] += -s_chg
            if v_chg > 0:
                ss["value_bought"] += v_chg
            elif v_chg < 0:
                ss["value_sold"] += -v_chg
            if detail_w is not None and act != "UNCHANGED":
                detail_w.writerow([cik, manager_name, row.Index, row.name,
                                   row.share_class, act,
                                   int(row.value_old), int(row.value_new),
                                   int(row.value_new - row.value_old),
                                   int(row.shares_old), int(row.shares_new),
                                   int(row.shares_new - row.shares_old)])

        v_old = int(cmp["value_old"].sum())
        v_new = int(cmp["value_new"].sum())
        summary_w.writerow([cik, manager_name, filer_status,
                            int((cmp["value_old"] > 0).sum()),
                            int((cmp["value_new"] > 0).sum()),
                            counts["NEW"], counts["EXITED"], counts["ADDED"],
                            counts["TRIMMED"], counts["UNCHANGED"],
                            v_old, v_new, v_new - v_old])

        if i % 100 == 0 or i == total:
            print(f"\rProcessed {i:,}/{total:,} managers ({i/total*100:.1f}%)",
                  end="", flush=True)

    print()
    summary_f.close()
    print(f"Wrote {summary_path}")

    # ---- per-stock sentiment: % of involved managers who were net buyers ----
    # ticker map (searched anywhere under BASE_DIR, newest quarter wins)
    import glob
    tick = {}
    cands = []
    for d in ["", "/*", "/*/*", "/*/*/*"]:
        cands += glob.glob(f"{BASE_DIR}{d}/institutional_market_summary_*.csv")
    if cands:
        m = pd.read_csv(sorted(cands)[-1], usecols=["cusip", "ticker"], dtype=str)
        m["cusip"] = m["cusip"].str.strip().str.upper()
        m = m[m["cusip"].str.len() == 9]          # make sure it's a full, well-formed CUSIP first
        m = m[m["cusip"].str[6] != "9"]           # check the real 7th character BEFORE stripping
        m["cusip"] = m["cusip"].str.lstrip("0")   # now strip zeros, just for the join key
        tick = dict(zip(m["cusip"], m["tickerok"].fillna("")))

    sentiment_path = (BASE_DIR /
                      f"stock_sentiment_{args.q_from}_to_{args.q_to}.csv")
    with open(sentiment_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "pct_bought", "pct_sold", "net_share_chg",
                    "name", "cusip", "shares_bought", "shares_sold",
                    "value_bought", "value_sold", "net_value_chg",
                    "n_managers", "buyers", "sellers", "unchanged"])
        rows = []
        for cusip, s in stock_stats.items():
            buyers = s["NEW"] + s["ADDED"]
            sellers = s["TRIMMED"] + s["EXITED"]
            total = buyers + sellers + s["UNCHANGED"]
            rows.append([tick.get(str(cusip).strip().upper().lstrip("0"), ""),
                         round(buyers / total * 100, 1) if total else 0,
                         round(sellers / total * 100, 1) if total else 0,
                         int(s["shares_bought"] - s["shares_sold"]),
                         s["name"], cusip,
                         int(s["shares_bought"]), int(s["shares_sold"]),
                         int(s["value_bought"]), int(s["value_sold"]),
                         int(s["value_bought"] - s["value_sold"]),
                         total, buyers, sellers, s["UNCHANGED"]])
        rows.sort(key=lambda r: r[11], reverse=True)
        w.writerows(rows)
    print(f"Wrote {sentiment_path} ({len(rows):,} stocks) — per-stock: "
          f"how many managers bought vs sold, and % net buyers")
    if detail_w is not None:
        detail_f.close()
        print(f"Wrote {detail_path}")
        print("(detail file lists every changed position for every manager; "
              "UNCHANGED positions are omitted to keep it manageable)")


if __name__ == "__main__":
    main()
