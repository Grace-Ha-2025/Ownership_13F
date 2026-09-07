"""
SEC Form 13F — AGGREGATION script (consolidated)
================================================
 
This is the consolidation of the former:
    classify_managers.py            (rule-based manager-type classification)
    build_all_manager_holdings.py   (single combined all-holdings CSV)
 
It runs entirely OFFLINE against the holdings_by_manager_<quarter>/ folder
produced by download_13f.py — no network, no INFOTABLE.tsv re-scan.
 
WHAT IT DOES
------------
  1. Classifies every manager in holdings_by_manager_<quarter>/_index.csv
     into a type (Hedge Fund, Mutual Fund, Bank/Trust, Insurance Company,
     Pension/Endowment, Sovereign Wealth Fund, Unknown) and writes
         manager_types_<quarter>.csv — cik, manager_name, manager_type,
                                       matched_rule, num_holdings
  2. Optionally (--combined) rebuilds the single giant all-holdings CSV
     by concatenating the per-manager files (re-adding cik/manager_name
     columns), writing
         all_manager_holdings_<quarter>.csv
     This replaces the old build_all_manager_holdings.py without needing
     to re-stream the ~400MB INFOTABLE.tsv. Expect a ~300-400MB output.
     The combined file now also carries a portfolio_weight_pct column —
     each holding's weight (%) within its manager's reported portfolio.
  3. (with --combined) Also writes summed_weights_<quarter>.csv — one row
     per security with that weight summed across ALL managers:
         cusip, issuer, num_managers, sum_weight_pct, avg_weight_pct,
         max_weight_pct, total_value
     sum_weight_pct is a conviction measure: a 5% position at a small fund
     adds far more than a giant manager's 0.01% sliver.
 
METHODOLOGY / HONESTY NOTE (classification)
-------------------------------------------
This is a rule-based pass, NOT per-manager verified research:
  - Banks/Trusts, Insurance, Pensions/Endowments: keyword detection — these
    categories tend to say what they are in their legal name.
  - Hedge Funds, Mutual Funds, SWFs: curated WHITELISTS of well-known names
    only. "XYZ Capital Management LLC" is not distinguishable as hedge fund
    vs. ordinary RIA from the name alone, so no guessing.
A large "Unknown" bucket is expected and intentional. It does NOT mean
"RIA" — it could be a non-famous hedge fund, a mutual fund complex, a
broker-dealer (no whitelist here at all), or anything else. matched_rule
shows exactly which keyword/whitelist entry fired, for manual review.
 
REQUIREMENTS
------------
    pip3 install pandas
 
USAGE
-----
    !/opt/anaconda3/bin/python3 aggregation_13f_code.py --quarter [yr&q] --combined
"""
 
import argparse
import re
import sys
from pathlib import Path
 
import pandas as pd
 
# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
# Same BASE_DIR as download_13f.py — absolute on purpose so outputs always
# land in the same place no matter where you run this from.
BASE_DIR = Path("/Users/graceha/Desktop/Ownership-13F")
 
 
# ----------------------------------------------------------------------------
# PART 1 — classification rules
# ----------------------------------------------------------------------------
 
# Sovereign Wealth Funds — curated whitelist (SWFs rarely say "sovereign
# wealth fund" in their legal name).
SOVEREIGN_WEALTH_WHITELIST = [
    "NORGES BANK", "GOVERNMENT PENSION FUND GLOBAL", "GIC PRIVATE",
    "GIC ASSET MANAGEMENT", "TEMASEK", "KUWAIT INVESTMENT AUTHORITY",
    "QATAR INVESTMENT AUTHORITY", "ABU DHABI INVESTMENT AUTHORITY",
    # NOTE: bare "ADIA" removed — substring of unrelated names like
    # "Arcadia Wealth Management".
    "MUBADALA", "CHINA INVESTMENT CORP", "KOREA INVESTMENT CORP",
    "KHAZANAH", "PUBLIC INVESTMENT FUND", "ALASKA PERMANENT FUND",
    "NEW ZEALAND SUPERANNUATION", "FUTURE FUND BOARD",
    "IRELAND STRATEGIC INVESTMENT FUND", "INVESTMENT CORP OF DUBAI",
]
 
# Mutual Fund complexes — curated whitelist of well-known traditional
# open-end fund families.
MUTUAL_FUND_WHITELIST = [
    "VANGUARD GROUP", "FMR LLC", "FIDELITY MANAGEMENT", "FIDELITY INVESTMENTS",
    "T ROWE PRICE", "PRICE T ROWE", "AMERICAN FUNDS",
    "FRANKLIN RESOURCES", "FRANKLIN TEMPLETON", "DODGE & COX",
    "AMERICAN CENTURY INVESTMENTS", "JANUS HENDERSON", "INVESCO", "MFS",
    "PUTNAM INVESTMENTS", "COLUMBIA MANAGEMENT", "THREADNEEDLE",
    "FEDERATED HERMES", "NUVEEN", "PACIFIC INVESTMENT MANAGEMENT", "PIMCO",
    "DIMENSIONAL FUND ADVISORS", "NEUBERGER BERMAN", "EATON VANCE",
    "VOYA INVESTMENT", "PRINCIPAL FUNDS", "JOHN HANCOCK INVESTMENT",
    "LORD ABBETT", "HARBOR CAPITAL ADVISORS", "WELLINGTON MANAGEMENT",
    # NOTE: "CAPITAL GROUP" / "CAPITAL RESEARCH" removed — generic fragments
    # ("Opus Capital Group, LLC") produced more false positives than real
    # American Funds matches.
]
 
# Hedge Funds — curated whitelist of widely-recognized managers.
HEDGE_FUND_WHITELIST = [
    "RENAISSANCE TECHNOLOGIES", "CITADEL ADVISORS", "MILLENNIUM MANAGEMENT",
    "BRIDGEWATER ASSOCIATES", "TWO SIGMA", "D E SHAW", "D. E. SHAW",
    "AQR CAPITAL", "POINT72", "ELLIOTT INVESTMENT", "ELLIOTT MANAGEMENT",
    "VIKING GLOBAL", "TIGER GLOBAL", "LONE PINE CAPITAL", "COATUE",
    "BAUPOST GROUP", "APPALOOSA MANAGEMENT", "THIRD POINT",
    "PERSHING SQUARE", "SOROS FUND MANAGEMENT", "PAULSON & CO",
    "YORK CAPITAL", "MARSHALL WACE", "MAN GROUP", "WINTON GROUP",
    "BLUECREST CAPITAL", "BALYASNY ASSET MANAGEMENT", "EXODUSPOINT",
    "VERITION FUND MANAGEMENT", "SCHONFELD STRATEGIC", "SQUAREPOINT",
    "DAVIDSON KEMPNER", "KING STREET CAPITAL", "FARALLON CAPITAL",
    "ANGELO GORDON", "CERBERUS CAPITAL", "FORTRESS INVESTMENT",
    "SILVER POINT CAPITAL", "MOORE CAPITAL", "CAXTON ASSOCIATES",
    "GARDA CAPITAL", "GRAHAM GLOBAL", "PDT PARTNERS", "SURVEYOR CAPITAL",
]
 
# Banks/Trusts — keyword patterns (reliable naming convention).
BANK_TRUST_KEYWORDS = [
    "BANK", "TRUST CO", "TRUST COMPANY", "TRUST N A", "TRUST NA",
    "NATIONAL ASSOCIATION", "BANCORP", "BANCSHARES", "SAVINGS BANK",
    "BANQUE",
    # NOTE: bare "N A" removed — false positives after normalization.
]
 
# Insurance Companies — keyword patterns (reliable convention).
INSURANCE_KEYWORDS = [
    "INSURANCE", "ASSURANCE", "REINSURANCE", "ANNUITY", "MUTUAL LIFE",
    "LIFE INS",
]
 
# Pensions/Endowments — keyword patterns (reliable convention).
PENSION_ENDOWMENT_KEYWORDS = [
    "RETIREMENT SYSTEM", "RETIREMENT FUND", "RETIREMENT BOARD",
    "RETIREMENT ADMINISTRATION", "PENSION", "ENDOWMENT", "UNIVERSITY",
    "TEACHERS RETIREMENT", "EMPLOYEES RETIREMENT", "PUBLIC EMPLOYEES",
    "SUPERANNUATION", "PROVIDENT FUND", "COLLEGE ", "FOUNDATION",
]
 
 
def normalize(name):
    """Uppercase, strip punctuation, collapse whitespace — so 'TRUST  CO'
    still matches 'TRUST CO'."""
    cleaned = re.sub(r"[^A-Z0-9 ]", " ", str(name).upper())
    return re.sub(r"\s+", " ", cleaned).strip()
 
 
_PATTERN_CACHE = {}
 
 
def _compiled(phrase):
    """Word-boundary regex for a phrase — 'ADIA' won't match inside
    'ARCADIA', 'MAN GROUP' won't match inside 'FAIRMAN GROUP'."""
    if phrase not in _PATTERN_CACHE:
        _PATTERN_CACHE[phrase] = re.compile(r"\b" + re.escape(phrase) + r"\b")
    return _PATTERN_CACHE[phrase]
 
 
def _match_any(norm_name, phrases):
    for p in phrases:
        if _compiled(p).search(norm_name):
            return p
    return None
 
 
_RIA_BRANDING_GUARD = _compiled("WEALTH MANAGEMENT")
 
 
def classify(manager_name):
    """Return (manager_type, matched_rule) for one manager name."""
    norm = normalize(manager_name)
 
    # Guard: ordinary RIAs branding themselves "Foundation/Endowment Wealth
    # Management" are not actual foundations/endowments — skip the
    # pension/endowment keyword pass for names containing WEALTH MANAGEMENT.
    skip_pension_endowment = bool(_RIA_BRANDING_GUARD.search(norm))
 
    # Priority: high-precision whitelists first, then keyword categories.
    hit = _match_any(norm, SOVEREIGN_WEALTH_WHITELIST)
    if hit:
        return "Sovereign Wealth Fund", hit
 
    hit = _match_any(norm, MUTUAL_FUND_WHITELIST)
    if hit:
        return "Mutual Fund", hit
 
    hit = _match_any(norm, HEDGE_FUND_WHITELIST)
    if hit:
        return "Hedge Fund", hit
 
    hit = _match_any(norm, INSURANCE_KEYWORDS)
    if hit:
        return "Insurance Company", hit
 
    if not skip_pension_endowment:
        hit = _match_any(norm, PENSION_ENDOWMENT_KEYWORDS)
        if hit:
            return "Pension/Endowment", hit
 
    hit = _match_any(norm, BANK_TRUST_KEYWORDS)
    if hit:
        return "Bank/Trust", hit
 
    # Genuinely unclassified — NOT an implied "RIA".
    return "Unknown", ""
 
 
def classify_managers(index_df, quarter_key):
    """Classify every manager in the index; write manager_types_<quarter>.csv."""
    out = index_df.copy()
    results = out["manager_name"].map(classify)
    out["manager_type"] = results.map(lambda t: t[0])
    out["matched_rule"] = results.map(lambda t: t[1])
    out = out.rename(columns={"num_holdings_written": "num_holdings"})
    out = out[["cik", "manager_name", "manager_type", "matched_rule",
               "num_holdings"]]
 
    out_path = BASE_DIR / quarter_key / f"manager_types_{quarter_key}.csv"
    out.to_csv(out_path, index=False)
    print(f"Wrote {out_path} ({len(out)} rows)")
 
    print("\nCounts by type:")
    counts = out["manager_type"].value_counts()
    for t in ["Hedge Fund", "Mutual Fund", "Bank/Trust", "Insurance Company",
              "Pension/Endowment", "Sovereign Wealth Fund", "Unknown"]:
        c = int(counts.get(t, 0))
        print(f"  {t:22s} {c:6,d}  ({100 * c / len(out):5.1f}%)")
    return out
 
 
# ----------------------------------------------------------------------------
# PART 2 — optional combined all-holdings CSV
# ----------------------------------------------------------------------------
 
def build_combined_csv(index_df, holdings_dir, quarter_key):
    """
    Concatenate every per-manager CSV into one combined file
    (all_manager_holdings_<quarter>.csv), re-adding cik and manager_name
    columns. Streams file-by-file — memory stays small, but the output is
    large (~300-400MB for a full quarter).
    """
    out_path = BASE_DIR / quarter_key / f"all_manager_holdings_{quarter_key}.csv"
    if out_path.exists():
        out_path.unlink()  # start fresh rather than appending to a stale file
 
    with_holdings = index_df[index_df["num_holdings_written"] > 0]
    print(f"\nBuilding combined CSV from {len(with_holdings):,} per-manager "
          f"files (large output — expect a few minutes) ...")
 
    first_write = True
    total_rows = 0
    missing_files = 0
    cusip_weights = {}  # summed portfolio weight per security, across managers
 
    for i, row in enumerate(with_holdings.itertuples(index=False), start=1):
        f_path = holdings_dir / row.filename
        if not f_path.exists():
            missing_files += 1
            continue
        df = pd.read_csv(f_path, low_memory=False, dtype={"cusip": str})
        df.insert(0, "cik", row.cik)
        df.insert(1, "manager_name", row.manager_name)

        # Normalize CUSIPs: trimmed uppercase strings. All-digit CUSIPs that
        # lost leading zeros to earlier numeric parsing (e.g. Apple's
        # 037833100 -> 37833100) are zero-padded back to 9 characters.
        cus = df["cusip"].fillna("").astype(str).str.strip().str.upper()
        cus = cus.where(~cus.str.fullmatch(r"\d{1,9}"), cus.str.zfill(9))
        df["cusip"] = cus

        # Separate actual STOCK rows from PUT/CALL option rows. Option rows
        # report shares CONTROLLED by the option (not owned), and puts are
        # bearish — so options are excluded from all weight math. They stay
        # in the combined file (flagged by putCall) with weight 0.
        if "putCall" in df.columns:
            is_option = df["putCall"].fillna("").astype(str).str.strip() != ""
        else:
            df["putCall"] = ""
            is_option = pd.Series(False, index=df.index)
        # Also exclude bond/note rows (sshPrnamtType "PRN": the shares field
        # is principal in DOLLARS, not shares) — same treatment as options:
        # kept in the combined file, weight 0, excluded from all sums.
        if "sshPrnamtType" in df.columns:
            is_option = is_option | (
                df["sshPrnamtType"].fillna("SH").astype(str)
                .str.strip().str.upper() != "SH")
        # Catch bonds mislabeled as SH by their class text
        # (e.g. "NOTE 3.500% 6/0", "SR NOTES", "DEBENTURE").
        if "titleOfClass" in df.columns:
            is_option = is_option | (
                df["titleOfClass"].fillna("").astype(str).str.upper()
                .str.contains(r"\bNOTES?\b|\bBONDS?\b|DEBENTURE|\d+\.\d+%",
                              regex=True))

        # GOAL 1: each holding's weight in THIS manager's reported STOCK
        # portfolio. A weight of 2.5 = "2.5% of this manager's stock value".
        total_value = df.loc[~is_option, "value"].fillna(0).sum()
        df["portfolio_weight_pct"] = 0.0
        if total_value > 0:
            df.loc[~is_option, "portfolio_weight_pct"] = (
                df.loc[~is_option, "value"].fillna(0) / total_value * 100.0
            )

        # GOAL 2: accumulate this manager's weights into per-security sums.
        # Duplicate rows for the same CUSIP within one manager (e.g. split by
        # voting authority) are combined before being counted once.
        # STOCK ROWS ONLY — options are excluded here too.
        stock = df[~is_option]
        for cusip, grp in stock.groupby(stock["cusip"]):
            if not cusip or cusip == "NAN":
                continue
            w = float(grp["portfolio_weight_pct"].sum())
            rec = cusip_weights.setdefault(cusip, {
                "issuer": str(grp["nameOfIssuer"].iloc[0]),
                "sum_weight_pct": 0.0, "num_managers": 0,
                "max_weight_pct": 0.0, "total_value": 0.0,
                "total_shares": 0.0,
            })
            rec["sum_weight_pct"] += w
            rec["num_managers"] += 1
            rec["max_weight_pct"] = max(rec["max_weight_pct"], w)
            rec["total_value"] += float(grp["value"].fillna(0).sum())
            rec["total_shares"] += float(grp["shares"].fillna(0).sum())

        df.to_csv(out_path, mode="a", header=first_write, index=False)
        first_write = False
        total_rows += len(df)
        if i % 500 == 0 or i == len(with_holdings):
            print(f"  {i:,}/{len(with_holdings):,} managers, "
                  f"{total_rows:,} rows written...", end="\r")
 
    print()
    print(f"Wrote {out_path} ({total_rows:,} holding rows)")

    # GOAL 2 output: one row per security, weights summed across all managers.
    summed = pd.DataFrame.from_dict(cusip_weights, orient="index")
    summed.index.name = "cusip"
    summed = summed.reset_index()
    summed["avg_weight_pct"] = summed["sum_weight_pct"] / summed["num_managers"]
    # Market-share weight: this security's total value as a % of ALL
    # institutional 13F dollars (value / grand total). Sums to 100% down
    # the file. Complements sum_weight_pct (conviction measure).
    summed["value_weight_pct"] = (
        summed["total_value"] / summed["total_value"].sum() * 100.0
    )
    
    summed["value_weight_pct"] = (
        summed["total_value"] / summed["total_value"].sum() * 100.0
    )

    # Ticker column, joined from your cusip/ticker mapping CSV.
    TICKER_MAP_PATH = BASE_DIR.parent / "Ownership_13F" / "cusip_ticker_type_mapping.csv"
    tick = {}
    if TICKER_MAP_PATH.exists():
        tick_df = pd.read_csv(TICKER_MAP_PATH, dtype={"cusip": str})
        tick = {
            str(k).strip().upper(): str(v)
            for k, v in zip(tick_df["cusip"], tick_df["ticker_y"])
        }
    else:
        print(f"  WARNING: ticker map not found at {TICKER_MAP_PATH} — "
              f"ticker column will be blank.")
    summed["ticker"] = [
        tick.get(str(c).strip().upper(), "") for c in summed["cusip"]
    ]
    summed = summed[["ticker", "cusip", "issuer", "num_managers",
                     "total_shares", "total_value", "sum_weight_pct",
                     "avg_weight_pct", "max_weight_pct", "value_weight_pct"]]
    summed_path = BASE_DIR / quarter_key / f"summed_weights_{quarter_key}.csv"
    summed.to_csv(summed_path, index=False)
    print(f"Wrote {summed_path} ({len(summed):,} securities)")
    if missing_files:
        print(f"  WARNING: {missing_files} file(s) listed in _index.csv were "
              f"missing on disk and skipped — consider re-running "
              f"download_13f.py for this quarter.")
 
 
# ----------------------------------------------------------------------------
# PART 3 — per-stock quarterly diff across ALL managers
# ----------------------------------------------------------------------------

def build_quarterly_diff(q_old, q_new, target):
    """For one stock (9-char CUSIP, or a ticker if the ticker map is
    available): across ALL managers, how many increased their share count
    between the two quarters. COMMON STOCK only (options/bonds excluded).
    Reads all_manager_holdings_<quarter>.csv — run --combined for both
    quarters first. NOTE: loads two large CSVs; expect a minute or two."""
    target = str(target).strip().upper()
    if re.fullmatch(r"[0-9A-Z]{9}", target):
        norm_targets = {target.lstrip("0")}
    else:
        import glob
        cands = []
        for depth in ["", "/*", "/*/*", "/*/*/*"]:
            cands += glob.glob(
                f"{BASE_DIR}{depth}/institutional_market_summary_*.csv")
        if not cands:
            sys.exit("ERROR: ticker lookup needs an "
                     "institutional_market_summary_*.csv map — pass a "
                     "9-character CUSIP instead.")
        m = pd.read_csv(sorted(cands)[-1], usecols=["cusip", "ticker"],
                        dtype=str)
        hits = m.loc[m["ticker"].fillna("").str.strip().str.upper() == target,
                     "cusip"]
        norm_targets = {c.strip().upper().lstrip("0") for c in hits}
        if not norm_targets:
            sys.exit(f"ERROR: ticker {target!r} not found in the ticker map "
                     f"— pass the 9-character CUSIP instead.")

    def load_stock(q):
        path = BASE_DIR / q / f"all_manager_holdings_{q}.csv"
        if not path.exists():
            sys.exit(f"ERROR: {path} not found.\nRun: aggregation_13f_code.py "
                     f"--quarter {q} --combined first.")
        print(f"  scanning {path.name} ...")
        df = pd.read_csv(path, low_memory=False, dtype={"cusip": str})
        cus = (df["cusip"].fillna("").astype(str).str.strip().str.upper()
               .str.lstrip("0"))
        df = df[cus.isin(norm_targets)]
        # common stock only — same rules as everywhere else in the pipeline
        if "putCall" in df.columns:
            df = df[df["putCall"].fillna("").astype(str).str.strip() == ""]
        if "sshPrnamtType" in df.columns:
            df = df[df["sshPrnamtType"].fillna("SH").astype(str)
                    .str.strip().str.upper() == "SH"]
        # one row per manager (sums subsidiary/voting-authority splits)
        return df.groupby("cik").agg(manager_name=("manager_name", "first"),
                                     shares=("shares", "sum"),
                                     value=("value", "sum"))

    old, new = load_stock(q_old), load_stock(q_new)
    merged = old.join(new, lsuffix="_old", rsuffix="_new", how="outer")
    merged["manager_name"] = (merged["manager_name_new"]
                              .fillna(merged["manager_name_old"]))
    merged = merged.drop(columns=["manager_name_old", "manager_name_new"])
    for c in ["shares_old", "value_old", "shares_new", "value_new"]:
        merged[c] = merged[c].fillna(0)
    merged["share_change"] = merged["shares_new"] - merged["shares_old"]
    merged["value_change"] = merged["value_new"] - merged["value_old"]
    # stamp the STOCK's identity on every row (rows are managers)
    merged.insert(0, "ticker", target if not re.fullmatch(r"[0-9A-Z]{9}",
                                                          target) else "")
    merged.insert(1, "cusip", ";".join(sorted(norm_targets)))

    total = len(merged)
    pos = int((merged["share_change"] > 0).sum())
    neg = int((merged["share_change"] < 0).sum())
    flat = total - pos - neg
    print(f"\n--- {target}: {q_old} -> {q_new} (common stock only) ---")
    print(f"Managers involved:            {total:,}")
    print(f"POSITIVE (bought/increased):  {pos:,}  "
          f"({pos / total * 100:.1f}%)" if total else "no managers found")
    print(f"NEGATIVE (sold/decreased):    {neg:,}  ({neg / total * 100:.1f}%)")
    print(f"UNCHANGED:                    {flat:,}  ({flat / total * 100:.1f}%)")
    print(f"Total value: ${merged['value_old'].sum():,.0f} -> "
          f"${merged['value_new'].sum():,.0f}  "
          f"(chg ${merged['value_change'].sum():,.0f})")

    out_path = BASE_DIR / f"stock_diff_{target}_{q_old}_to_{q_new}.csv"
    out = merged.reset_index().rename(columns={"cik": "manager_cik"})
    out = out[["ticker", "cusip", "manager_cik", "manager_name",
               "shares_old", "shares_new", "share_change",
               "value_old", "value_new", "value_change"]]
    out.sort_values("share_change", key=abs,
                    ascending=False).to_csv(out_path, index=False)
    print(f"Wrote per-manager detail -> {out_path}")


# ----------------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Aggregate a quarter's per-manager 13F holdings: "
                    "classify manager types, optionally rebuild the "
                    "combined all-holdings CSV.")
    parser.add_argument("--quarter",
                        help="Quarter to aggregate, e.g. 2026q1 — must match "
                             "an existing holdings_by_manager_<quarter>/ "
                             "folder produced by download_13f.py.")
    parser.add_argument("--diff-from", dest="diff_from",
                        help="Per-stock diff mode: older quarter, e.g. 2025q4.")
    parser.add_argument("--diff-to", dest="diff_to",
                        help="Per-stock diff mode: newer quarter, e.g. 2026q1.")
    parser.add_argument("--target-stock", dest="target_stock", default="AAPL",
                        help="Ticker or 9-char CUSIP for the per-stock diff "
                             "(default AAPL).")
    parser.add_argument("--combined", action="store_true",
                        help="Also write all_manager_holdings_<quarter>.csv "
                             "(~300-400MB) by concatenating the per-manager "
                             "files. Off by default.")
    args = parser.parse_args()

    # Per-stock diff mode: --diff-from/--diff-to (and optional --target-stock)
    if args.diff_from and args.diff_to:
        build_quarterly_diff(args.diff_from, args.diff_to, args.target_stock)
        return
    if not args.quarter:
        parser.error("--quarter is required (or use --diff-from/--diff-to)")

    # Per-quarter layout: everything for one quarter lives in
    # BASE_DIR/<quarter>/ (e.g. Ownership-13F/2025q4/).
    holdings_dir = BASE_DIR / args.quarter / f"holdings_by_manager_{args.quarter}"
    index_path = holdings_dir / "_index.csv"
    if not index_path.exists():
        sys.exit(
            f"ERROR: {index_path} not found.\n"
            f"Run download_13f_code.py --quarters {args.quarter} first to "
            f"build the per-manager holdings folder.")
 
    index_df = pd.read_csv(index_path)
    print(f"Loaded {len(index_df):,} managers from {index_path}")
 
    classify_managers(index_df, args.quarter)
 
    if args.combined:
        build_combined_csv(index_df, holdings_dir, args.quarter)
    else:
        print("\n(--combined not set: skipping the all_manager_holdings CSV. "
              "Re-run with --combined if you need the single large file.)")
 
    
    # Run this cell immediately after your aggregation finishes!
    summed_path = BASE_DIR / args.quarter / f"summed_weights_{args.quarter}.csv"
    df = pd.read_csv(summed_path)

    # Calculate your institutional sentiment/conviction score
    df["institutional_sentiment_score"] = df["sum_weight_pct"] * df["num_managers"]
    df = df.sort_values("institutional_sentiment_score", ascending=False)

    # Export the final sentiment file
    sentiment_path = BASE_DIR / args.quarter / f"stock_sentiment_{args.quarter}.csv"
    df.to_csv(sentiment_path, index=False)
    print(f"Wrote stock sentiment file -> {sentiment_path}")
 
if __name__ == "__main__":
    main()
 