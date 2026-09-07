"""
SEC Form 13F — DOWNLOAD script (consolidated)
=============================================
 
This is the consolidation of the former:
    build_13f_manager_universe.py   (download ZIPs + manager universe CSVs)
    build_holdings_by_manager.py    (per-manager holdings folder)
 
ONE run per quarter now does everything network/extraction related.
FOLDER LAYOUT: each quarter gets its OWN folder under BASE_DIR —
    Ownership-13F/<quarter>/13f_data/raw/           (ZIP + extracted tables)
    Ownership-13F/<quarter>/holdings_by_manager_<quarter>/
(the cross-quarter universe CSVs stay at BASE_DIR root).

  1. Resolves & downloads SEC's official bulk 13F structured-data ZIP for
     each requested quarter (cached — never re-downloaded if present).
  2. Extracts it under <quarter>/13f_data/raw/<quarter>/.
  3. Builds the manager universe CSVs:
         manager_quarterly_aum.csv   — cik, manager_name, quarter,
                                       filing_date, period_of_report, aum,
                                       num_holdings (all quarters processed)
         manager_master.csv          — one row per unique cik (most recent name)
  4. Streams INFOTABLE.tsv and writes ONE CSV PER MANAGER into
         holdings_by_manager_<quarter>/
             <CIK>_<sanitized manager name>.csv
             _index.csv              — cik, manager_name, filename,
                                       num_holdings_written
  5. Optionally (--other-managers-for-cik) extracts the OTHERMANAGER /
     OTHERMANAGER2 subsidiary/joint-filer list found WITHIN one filer's own
     13F-HR filing (e.g. 1067983 for Berkshire Hathaway).
 
The former build_all_manager_holdings.py (single giant combined CSV) is no
longer needed here — aggregate_13f.py can rebuild that file cheaply from the
per-manager folder with its --combined flag, without re-scanning INFOTABLE.
 
WHY THIS RUNS LOCALLY
---------------------
The analysis sandbox that produced this script cannot reach sec.gov and
cannot hold the ~400MB INFOTABLE.tsv. Run this on your own machine.
 
SCOPE NOTE (per-manager holdings)
---------------------------------
For each quarter, the holdings split includes only original 13F-HR filings
(no amendments) whose PERIODOFREPORT matches that quarter's period end
(e.g. 31-MAR-2026 for 2026q1) — the "current quarter" manager list, not the
handful of backfill/late filings covering older periods that arrive in the
same window. Pass --all-periods to include every period found instead.
The universe CSVs (manager_quarterly_aum / manager_master) intentionally
keep ALL 13F-HR filings in each ZIP, as before.
 
SEC FAIR-ACCESS RULES (already handled)
---------------------------------------
SEC requires a descriptive User-Agent (name + email) — set in USER_AGENT
below — and asks automated tools to stay well under ~10 requests/second;
this script makes only a handful of requests with polite delays.
 
REQUIREMENTS
------------
    pip3 install requests pandas
 
USAGE
-----
    python3 download_13f.py --quarters 2026q1
    python3 download_13f.py --quarters 2025q4 2026q1
    python3 download_13f.py --start 2024q1 --end 2026q1
    python3 download_13f.py                  # all available quarters (slow!)
    python3 download_13f.py --quarters 2026q1 --other-managers-for-cik 1067983
    python3 download_13f.py --quarters 2026q1 --skip-holdings   # universe CSVs only
"""
 
import argparse
import re
import sys
import time
import zipfile
from pathlib import Path
 
import pandas as pd
import requests
 
# ----------------------------------------------------------------------------
# CONFIG — edit before running
# ----------------------------------------------------------------------------
 
# SEC requires a descriptive User-Agent: "Name email" — requests without a
# real contact identifier can be blocked.
USER_AGENT = "Grace Ha grace.rs.ha@gmail.com"
 
# Absolute paths on purpose: earlier cwd-relative versions silently wrote
# outputs to different folders depending on where the script was run from.
BASE_DIR = Path("/Users/graceha/Desktop/Ownership-13F")


def quarter_dir(quarter_key):
    """Everything for one quarter lives in its own folder: BASE_DIR/<quarter>/
    (e.g. .../Ownership-13F/2025q4/). Created on first use."""
    return BASE_DIR / quarter_key


def raw_dir(quarter_key):
    """Raw SEC ZIP + extracted tables for one quarter:
    BASE_DIR/<quarter>/13f_data/raw/"""
    return quarter_dir(quarter_key) / "13f_data" / "raw"
 
REQUEST_DELAY_SECONDS = 0.5
CHUNK_SIZE = 200_000  # INFOTABLE.tsv rows per chunk while streaming
 
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})
 
 
# ----------------------------------------------------------------------------
# STEP 1 — build & verify quarterly ZIP URLs (no page-scraping)
# ----------------------------------------------------------------------------
# SEC has used TWO filename conventions:
#   OLD (~2013q2 - 2023q4):  {year}q{quarter}_form13f.zip
#   NEW (2024+):             {DDmonYYYY}-{DDmonYYYY}_form13f.zip
# The new style names the file after the 3-month FILING window (filings lag
# ~45 days after quarter end): holdings as of 3/31/2026 ("2026q1") are filed
# Mar-May 2026 -> "01mar2026-31may2026_form13f.zip". We HEAD-check the old
# pattern first, then fall back to the new one.
 
OLD_ZIP_URL_TEMPLATE = (
    "https://www.sec.gov/files/structureddata/data/"
    "form-13f-data-sets/{quarter_key}_form13f.zip"
)
NEW_ZIP_URL_TEMPLATE = (
    "https://www.sec.gov/files/structureddata/data/"
    "form-13f-data-sets/{window}_form13f.zip"
)
 
EARLIEST_QUARTER = "2013q2"  # first quarter SEC published in this bulk format
 
_MONTH_ABBR = ["jan", "feb", "mar", "apr", "may", "jun",
               "jul", "aug", "sep", "oct", "nov", "dec"]
_MONTH_ABBR_UPPER = [m.upper() for m in _MONTH_ABBR]
_MONTH_LAST_DAY = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
 
 
def _is_leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
 
 
def parse_quarter_key(quarter_key):
    """'2026q1' -> (2026, 1), with validation."""
    m = re.fullmatch(r"(\d{4})q([1-4])", quarter_key)
    if not m:
        raise ValueError(
            f"Bad quarter key {quarter_key!r} — expected e.g. '2026q1' "
            f"(lowercase 'q')."
        )
    return int(m.group(1)), int(m.group(2))
 
 
def period_of_report_for_quarter(quarter_key):
    """Reporting-period end date as SEC writes it, e.g. '31-MAR-2026'."""
    year, qtr = parse_quarter_key(quarter_key)
    end_month = {1: 2, 2: 5, 3: 8, 4: 11}[qtr]  # 0-based: mar, jun, sep, dec
    return f"{_MONTH_LAST_DAY[end_month]:02d}-{_MONTH_ABBR_UPPER[end_month]}-{year}"
 
 
def filing_window_for_quarter(quarter_key):
    """
    (start, end) of the ~3-month window during which 13Fs covering this
    reporting quarter are actually filed, as (day, month_idx, year) tuples.
    """
    year, qtr = parse_quarter_key(quarter_key)
    window_start_month = {1: 2, 2: 5, 3: 8, 4: 11}[qtr]  # mar, jun, sep, dec
    end_year = year if qtr != 4 else year + 1
    end_month = (window_start_month + 2) % 12
    if qtr == 4:
        end_month = 1  # feb of the following year
 
    end_day = _MONTH_LAST_DAY[end_month]
    if end_month == 1 and _is_leap(end_year):
        end_day = 29
    return (1, window_start_month, year), (end_day, end_month, end_year)
 
 
def new_style_filename_key(quarter_key):
    """Build the '01mar2026-31may2026' filename component for a quarter."""
    (sd, sm, sy), (ed, em, ey) = filing_window_for_quarter(quarter_key)
    return f"{sd:02d}{_MONTH_ABBR[sm]}{sy}-{ed:02d}{_MONTH_ABBR[em]}{ey}"
 
 
def candidate_urls_for_quarter(quarter_key):
    return [
        OLD_ZIP_URL_TEMPLATE.format(quarter_key=quarter_key),
        NEW_ZIP_URL_TEMPLATE.format(window=new_style_filename_key(quarter_key)),
    ]
 
 
def resolve_quarter_url(quarter_key):
    """HEAD-check candidates (old style then new); return first that exists."""
    for url in candidate_urls_for_quarter(quarter_key):
        try:
            resp = SESSION.head(url, timeout=20, allow_redirects=True)
            if resp.status_code == 200:
                return url
        except requests.RequestException:
            continue
    return None
 
 
def all_quarter_keys_since(start_key, end_key=None):
    """Every quarter key from start_key to end_key inclusive (default: today)."""
    if end_key is None:
        today = pd.Timestamp.today()
        end_key = f"{today.year}q{(today.month - 1) // 3 + 1}"
 
    year, qtr = parse_quarter_key(start_key)
    end_year, end_qtr = parse_quarter_key(end_key)
 
    keys = []
    while (year, qtr) <= (end_year, end_qtr):
        keys.append(f"{year}q{qtr}")
        qtr += 1
        if qtr > 4:
            qtr, year = 1, year + 1
    return keys
 
 
def discover_quarterly_zip_urls(start_key=EARLIEST_QUARTER, end_key=None):
    """quarter_key -> url for every published quarter in range (HEAD-verified)."""
    candidates = all_quarter_keys_since(start_key, end_key)
    quarters = {}
    print(f"  checking {len(candidates)} candidate quarters for availability...")
    for key in candidates:
        url = resolve_quarter_url(key)
        if url is not None:
            quarters[key] = url
        time.sleep(0.1)  # light HEAD-request pacing, well under SEC's limit
 
    if not quarters:
        raise RuntimeError(
            "No quarterly ZIPs found in the requested range. Check "
            "EARLIEST_QUARTER / --start/--end, or SEC may have changed their "
            "URL pattern — inspect OLD_/NEW_ZIP_URL_TEMPLATE."
        )
    return quarters
 
 
# ----------------------------------------------------------------------------
# STEP 2 — download + cache + extract each quarterly ZIP
# ----------------------------------------------------------------------------
 
def download_quarter_zip(quarter_key, url):
    """Download one quarter's ZIP, skipping if already cached on disk."""
    rd = raw_dir(quarter_key)
    rd.mkdir(parents=True, exist_ok=True)
    zip_path = rd / f"{quarter_key}.zip"
 
    if zip_path.exists():
        print(f"  [{quarter_key}] already downloaded, skipping ({zip_path})")
        return zip_path
 
    print(f"  [{quarter_key}] downloading from {url} ...")
    resp = SESSION.get(url, timeout=120)
    resp.raise_for_status()
    zip_path.write_bytes(resp.content)
    time.sleep(REQUEST_DELAY_SECONDS)
    print(f"  [{quarter_key}] saved {len(resp.content) / 1e6:.1f} MB -> {zip_path}")
    return zip_path
 
 
def extract_quarter_zip(quarter_key, zip_path):
    """Unzip into a per-quarter subfolder, skipping if already extracted."""
    extract_dir = raw_dir(quarter_key) / quarter_key
    if extract_dir.exists() and any(extract_dir.iterdir()):
        return extract_dir
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)
    return extract_dir
 
 
# ----------------------------------------------------------------------------
# STEP 3 — shared table loading + manager universe (AUM) summary
# ----------------------------------------------------------------------------
# Tables inside each quarterly ZIP:
#   SUBMISSION.tsv    — one row per filing (ACCESSION_NUMBER, CIK, dates, type)
#   COVERPAGE.tsv     — one row per filing (FILINGMANAGER_NAME, ...)
#   SUMMARYPAGE.tsv   — one row per filing (TABLEVALUETOTAL = certified AUM)
#   INFOTABLE.tsv     — one row per HOLDING (the big one, ~4M rows/quarter)
#   OTHERMANAGER(2)   — subsidiary / joint-filer manager lists
#
# "AUM" = SUMMARYPAGE.TABLEVALUETOTAL, the filer's own certified grand total —
# more authoritative (and much faster) than re-summing INFOTABLE.
 
def read_tsv(extract_dir, name, **kwargs):
    """Read one of the quarter's TSVs (searched recursively — SEC sometimes
    nests them one directory deeper)."""
    matches = list(extract_dir.rglob(name))
    if not matches:
        raise FileNotFoundError(f"{name} not found under {extract_dir}")
    return pd.read_csv(matches[0], sep="\t", encoding="latin-1",
                       low_memory=False, **kwargs)
 
 
def load_filing_tables(extract_dir):
    """SUBMISSION (13F-HR only) merged with COVERPAGE and SUMMARYPAGE —
    one row per original 13F-HR filing."""
    submission = read_tsv(extract_dir, "SUBMISSION.tsv")
    coverpage = read_tsv(extract_dir, "COVERPAGE.tsv")
    summarypage = read_tsv(extract_dir, "SUMMARYPAGE.tsv")
 
    # Original 13F-HR only (skip amendments, NT notices) — one clean row
    # per manager rather than duplicates.
    submission = submission[submission["SUBMISSIONTYPE"] == "13F-HR"]
 
    merged = (
        submission[["ACCESSION_NUMBER", "CIK", "FILING_DATE", "PERIODOFREPORT"]]
        .merge(coverpage[["ACCESSION_NUMBER", "FILINGMANAGER_NAME"]],
               on="ACCESSION_NUMBER", how="left")
        .merge(summarypage[["ACCESSION_NUMBER", "TABLEVALUETOTAL", "TABLEENTRYTOTAL"]],
               on="ACCESSION_NUMBER", how="left")
    )
    return merged
 
 
def build_quarter_summary(quarter_key, filings):
    """Universe rows for this quarter: cik, manager_name, quarter,
    filing_date, period_of_report, aum, num_holdings."""
    out = filings.rename(columns={
        "CIK": "cik",
        "FILING_DATE": "filing_date",
        "PERIODOFREPORT": "period_of_report",
        "FILINGMANAGER_NAME": "manager_name",
        "TABLEVALUETOTAL": "aum",
        "TABLEENTRYTOTAL": "num_holdings",
    }).copy()
    out["quarter"] = quarter_key
    return out[["cik", "manager_name", "quarter", "filing_date",
                "period_of_report", "aum", "num_holdings"]]
 
 
# ----------------------------------------------------------------------------
# STEP 4 — per-manager holdings split (streams INFOTABLE.tsv)
# ----------------------------------------------------------------------------
 
_SANITIZE_RE = re.compile(r"[^A-Za-z0-9 _.-]+")
 
INFOTABLE_USECOLS = ["ACCESSION_NUMBER", "NAMEOFISSUER", "TITLEOFCLASS",
                     "CUSIP", "VALUE", "SSHPRNAMT", "SSHPRNAMTTYPE",
                     "PUTCALL",
                     "INVESTMENTDISCRETION", "OTHERMANAGER",
                     "VOTING_AUTH_SOLE", "VOTING_AUTH_SHARED",
                     "VOTING_AUTH_NONE"]
INFOTABLE_RENAMES = {
    "ACCESSION_NUMBER": "accession_number",
    "NAMEOFISSUER": "nameOfIssuer",
    "TITLEOFCLASS": "titleOfClass",
    "CUSIP": "cusip",
    "VALUE": "value",
    "SSHPRNAMT": "shares",
    "SSHPRNAMTTYPE": "sshPrnamtType",
    "PUTCALL": "putCall",  # blank = actual stock; "Put"/"Call" = option
    "INVESTMENTDISCRETION": "investmentDiscretion",
    "OTHERMANAGER": "otherManager",
    "VOTING_AUTH_SOLE": "votingAuthoritySole",
    "VOTING_AUTH_SHARED": "votingAuthorityShared",
    "VOTING_AUTH_NONE": "votingAuthorityNone",
}
HOLDINGS_OUT_COLS = ["accession_number", "nameOfIssuer", "titleOfClass",
                     "cusip", "value", "shares", "sshPrnamtType", "putCall",
                     "investmentDiscretion", "otherManager",
                     "votingAuthoritySole", "votingAuthorityShared",
                     "votingAuthorityNone"]
 
 
def sanitize_filename(cik, manager_name):
    """Safe, readable filename: '<cik>_<cleaned manager name>.csv'."""
    name = str(manager_name) if pd.notna(manager_name) else "UNKNOWN"
    name = _SANITIZE_RE.sub("_", name).strip()
    name = re.sub(r"_+", "_", name).strip("_ ")
    name = name[:60] or "UNKNOWN"
    return f"{cik}_{name}.csv"
 
 
def write_holdings_by_manager(quarter_key, extract_dir, filings, target_period,
                              drop_options=False):
    """
    Stream INFOTABLE.tsv once and write one CSV per manager (CIK) into
    holdings_by_manager_<quarter>/, plus an _index.csv.
 
    `filings` is the merged 13F-HR table from load_filing_tables();
    `target_period` restricts to filings whose PERIODOFREPORT matches
    (None = include every period found).
    """
    infotable_matches = list(extract_dir.rglob("INFOTABLE.tsv"))
    if not infotable_matches:
        raise FileNotFoundError(
            f"INFOTABLE.tsv not found under {extract_dir} — make sure the "
            f"full quarterly ZIP was extracted, not just the small tables."
        )
    infotable_path = infotable_matches[0]
 
    scoped = filings
    if target_period is not None:
        scoped = filings[filings["PERIODOFREPORT"] == target_period]
 
    lookup = (
        scoped[["ACCESSION_NUMBER", "CIK", "FILINGMANAGER_NAME"]]
        .rename(columns={"CIK": "cik", "FILINGMANAGER_NAME": "manager_name"})
        .drop_duplicates(subset="ACCESSION_NUMBER")
        .set_index("ACCESSION_NUMBER")
    )
    target_accessions = set(lookup.index)
    period_label = target_period if target_period is not None else "any period"
    print(f"  [{quarter_key}] holdings split: {len(target_accessions):,} managers "
          f"(13F-HR only, period = {period_label})")
 
    out_dir = quarter_dir(quarter_key) / f"holdings_by_manager_{quarter_key}"
    out_dir.mkdir(parents=True, exist_ok=True)
 
    # cik -> filename, computed once
    cik_to_filename = {
        row["cik"]: sanitize_filename(row["cik"], row["manager_name"])
        for _, row in lookup.iterrows()
    }
 
    # Start fresh: remove stale per-manager files from any previous run so
    # append-mode writes below can't double-count.
    for cik, filename in cik_to_filename.items():
        stale = out_dir / filename
        if stale.exists():
            stale.unlink()
 
    files_with_header = set()
    holdings_written_per_cik = {}
    total_scanned = 0
    total_written = 0
 
    print(f"  [{quarter_key}] streaming INFOTABLE.tsv in chunks of "
          f"{CHUNK_SIZE:,} rows (large file — expect a few minutes) ...")
 
    for chunk in pd.read_csv(infotable_path, sep="\t", encoding="latin-1",
                             low_memory=False, chunksize=CHUNK_SIZE,
                             usecols=INFOTABLE_USECOLS):
        total_scanned += len(chunk)
        matched = chunk[chunk["ACCESSION_NUMBER"].isin(target_accessions)].copy()
 
        if len(matched):
            matched["cik"] = matched["ACCESSION_NUMBER"].map(lookup["cik"])
            matched = matched.rename(columns=INFOTABLE_RENAMES)

            # --drop-options: exclude put/call rows entirely, so per-manager
            # files contain ONLY actual stock. (Default keeps them, labeled
            # in the putCall column; analysis scripts ignore them either way.)
            if drop_options:
                matched = matched[matched["putCall"].fillna("")
                                  .astype(str).str.strip() == ""]
 
            for cik, group in matched.groupby("cik"):
                filename = cik_to_filename.get(cik)
                if filename is None:
                    continue  # shouldn't happen — cik came from target set
                out_path = out_dir / filename
                write_header = cik not in files_with_header
                group[HOLDINGS_OUT_COLS].to_csv(out_path, mode="a",
                                                header=write_header, index=False)
                files_with_header.add(cik)
                holdings_written_per_cik[cik] = (
                    holdings_written_per_cik.get(cik, 0) + len(group)
                )
                total_written += len(group)
 
        print(f"    scanned {total_scanned:,} rows, written {total_written:,} "
              f"holdings across {len(files_with_header):,} manager files...",
              end="\r")
 
    print()
    print(f"  [{quarter_key}] wrote {total_written:,} holding rows across "
          f"{len(files_with_header):,} manager files -> {out_dir}")
 
    # _index.csv: cik, manager_name, filename, num_holdings_written
    index_df = (
        lookup.reset_index(drop=True)
        .drop_duplicates(subset="cik")
        .assign(
            filename=lambda d: d["cik"].map(cik_to_filename),
            num_holdings_written=lambda d: d["cik"]
                .map(holdings_written_per_cik).fillna(0).astype(int),
        )[["cik", "manager_name", "filename", "num_holdings_written"]]
    )
    index_path = out_dir / "_index.csv"
    index_df.to_csv(index_path, index=False)
    print(f"  [{quarter_key}] wrote index -> {index_path}")
 
    zero = (index_df["num_holdings_written"] == 0).sum()
    if zero:
        print(f"  [{quarter_key}] NOTE: {zero} manager(s) had zero matching "
              f"holdings rows (no file created) — see _index.csv.")
 
 
# ----------------------------------------------------------------------------
# STEP 5 — optional: OTHERMANAGER extraction for one CIK
# ----------------------------------------------------------------------------
# CAVEAT (confirmed against Berkshire's Q1 2026 filing): the CIK field is
# BLANK for almost all subsidiary entries — they're identified by Form 13F
# File Number (e.g. "028-718" for National Indemnity Co), not CIK.
 
def build_other_managers_for_cik(quarter_key, extract_dir, filings, target_cik):
    """Every OTHERMANAGER/OTHERMANAGER2 row tied to target_cik's own 13F-HR
    filing(s) this quarter."""
    target_accessions = filings.loc[
        filings["CIK"] == target_cik, "ACCESSION_NUMBER"
    ].unique()
 
    out_cols = ["quarter", "accession_number", "cik", "form_13f_file_number",
                "crd_number", "sec_file_number", "name", "source_table"]
    if len(target_accessions) == 0:
        print(f"  [{quarter_key}] CIK {target_cik} has no 13F-HR filing this "
              f"quarter — skipping other-managers extraction")
        return pd.DataFrame(columns=out_cols)
 
    om1 = read_tsv(extract_dir, "OTHERMANAGER.tsv").rename(
        columns={"OTHERMANAGER_SK": "SEQUENCENUMBER"})
    om2 = read_tsv(extract_dir, "OTHERMANAGER2.tsv")
    om1["SOURCE_TABLE"] = "OTHERMANAGER"    # Instruction 5 "other included managers"
    om2["SOURCE_TABLE"] = "OTHERMANAGER2"   # per-holding joint-filer code list
 
    cols = ["ACCESSION_NUMBER", "SEQUENCENUMBER", "CIK", "FORM13FFILENUMBER",
            "CRDNUMBER", "SECFILENUMBER", "NAME", "SOURCE_TABLE"]
    combined = pd.concat([om1[cols], om2[cols]], ignore_index=True)
 
    matched = combined[combined["ACCESSION_NUMBER"].isin(target_accessions)].copy()
    matched["quarter"] = quarter_key
    matched = matched.rename(columns={
        "ACCESSION_NUMBER": "accession_number",
        "CIK": "cik",
        "FORM13FFILENUMBER": "form_13f_file_number",
        "CRDNUMBER": "crd_number",
        "SECFILENUMBER": "sec_file_number",
        "NAME": "name",
        "SOURCE_TABLE": "source_table",
    })
    return matched[out_cols]
 
 
# ----------------------------------------------------------------------------
# STEP 6 — orchestration
# ----------------------------------------------------------------------------
 
def main():
    parser = argparse.ArgumentParser(
        description="Download SEC 13F bulk data: manager universe CSVs + "
                    "per-manager holdings folder, per quarter.")
    parser.add_argument("--quarters", nargs="+",
                        help="Specific quarters, e.g. 2025q4 2026q1")
    parser.add_argument("--start", help="Start quarter, e.g. 2024q1 (inclusive)")
    parser.add_argument("--end", help="End quarter, e.g. 2026q1 (inclusive)")
    parser.add_argument("--skip-holdings", action="store_true",
                        help="Only build the universe CSVs; skip the (slow) "
                             "per-manager holdings split.")
    parser.add_argument("--drop-options", action="store_true",
                        help="Exclude put/call option rows from the "
                             "per-manager files entirely — every CSV "
                             "contains actual stock only. Default keeps "
                             "them, labeled in the putCall column.")
    parser.add_argument("--all-periods", action="store_true",
                        help="Include filings for EVERY reporting period in "
                             "the holdings split, not just the quarter's own "
                             "period end (default filters to e.g. 31-MAR-2026 "
                             "for 2026q1).")
    parser.add_argument(
        "--other-managers-for-cik",
        help="Also extract the OTHERMANAGER/OTHERMANAGER2 subsidiary/"
             "joint-filer list found WITHIN this CIK's own 13F-HR filing "
             "(e.g. 1067983 for Berkshire Hathaway). Writes "
             "other_managers_<CIK>.csv. Most rows will have a blank cik — "
             "use form_13f_file_number as the identifier.")
    args = parser.parse_args()
 
    if USER_AGENT.startswith("Your Name"):
        sys.exit("ERROR: Edit USER_AGENT at the top of this script with your "
                 "real name/email — SEC blocks the placeholder.")
 
    # ---- select quarters -----------------------------------------------
    if args.quarters:
        print(f"Verifying {len(args.quarters)} requested quarter(s) exist on SEC...")
        selected, missing = {}, {}
        for q in args.quarters:
            parse_quarter_key(q)  # validate format early, clear error message
            url = resolve_quarter_url(q)
            if url is not None:
                selected[q] = url
            else:
                missing[q] = candidate_urls_for_quarter(q)
        if missing:
            tried = "\n".join(f"  {q}: {urls}" for q, urls in missing.items())
            sys.exit(
                f"ERROR: these requested quarters don't exist on SEC. Tried:\n"
                f"{tried}\nDouble-check the format (lowercase 'q', e.g. 2026q1) "
                f"and that SEC has published that quarter yet — there's usually "
                f"a ~1.5 month lag after quarter-end.")
    else:
        print("Discovering available quarterly 13F data sets from SEC...")
        selected = discover_quarterly_zip_urls(
            start_key=args.start or EARLIEST_QUARTER, end_key=args.end)
        print(f"  found {len(selected)} quarters: {sorted(selected)}")
 
    if not selected:
        sys.exit("ERROR: no quarters selected — nothing to do.")
    print(f"Processing {len(selected)} quarter(s): {sorted(selected)}")
 
    target_cik = None
    if args.other_managers_for_cik:
        try:
            target_cik = int(args.other_managers_for_cik)
        except ValueError:
            sys.exit(f"ERROR: --other-managers-for-cik must be numeric, "
                     f"got {args.other_managers_for_cik!r}")
 
    # ---- per-quarter processing ------------------------------------------
    all_rows = []
    other_manager_rows = []
    for quarter_key, url in sorted(selected.items()):
        zip_path = download_quarter_zip(quarter_key, url)
        extract_dir = extract_quarter_zip(quarter_key, zip_path)
 
        filings = load_filing_tables(extract_dir)  # loaded ONCE per quarter
        quarter_df = build_quarter_summary(quarter_key, filings)
        print(f"  [{quarter_key}] {len(quarter_df)} 13F-HR filings parsed")
        all_rows.append(quarter_df)
 
        if not args.skip_holdings:
            target_period = (None if args.all_periods
                             else period_of_report_for_quarter(quarter_key))
            write_holdings_by_manager(quarter_key, extract_dir, filings,
                                      target_period,
                                      drop_options=args.drop_options)
 
        if target_cik is not None:
            om_df = build_other_managers_for_cik(quarter_key, extract_dir,
                                                 filings, target_cik)
            print(f"  [{quarter_key}] {len(om_df)} other-manager row(s) "
                  f"for CIK {target_cik}")
            other_manager_rows.append(om_df)
 
    # ---- universe CSVs ----------------------------------------------------
    full = pd.concat(all_rows, ignore_index=True)
 
    aum_path = BASE_DIR / "manager_quarterly_aum.csv"
    full.to_csv(aum_path, index=False)
    print(f"Wrote {aum_path} ({len(full)} rows)")
 
    latest = (
        full.sort_values("filing_date")
        .groupby("cik", as_index=False)
        .last()[["cik", "manager_name"]]
    )
    master_path = BASE_DIR / "manager_master.csv"
    latest.to_csv(master_path, index=False)
    print(f"Wrote {master_path} ({len(latest)} unique managers)")
 
    if target_cik is not None:
        other_full = pd.concat(other_manager_rows, ignore_index=True)
        out_path = BASE_DIR / f"other_managers_{target_cik}.csv"
        other_full.to_csv(out_path, index=False)
        print(f"Wrote {out_path} ({len(other_full)} rows)")
        blank = other_full["cik"].isna().sum() + (other_full["cik"] == "").sum()
        if len(other_full) > 0 and blank == len(other_full):
            print("  NOTE: every row has a blank 'cik' — expected. SEC records "
                  "these subsidiaries by Form 13F File Number, not CIK; use "
                  "'form_13f_file_number' as the identifier.")
 
 
if __name__ == "__main__":
    main()