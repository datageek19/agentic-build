#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FootPrints payment-ticket analysis.

Requires: pandas, numpy, openpyxl, matplotlib (and Pillow for embedded charts).
Edit EXCEL_FILE and SHEET_NAME, then run: python footprints_eda_filtered.py
"""

import os
import re
import sys
import math
import random
from datetime import datetime
from statistics import NormalDist

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# =============================================================================
# 1. CONFIGURATION  -- the only block the data owner should need to edit
# =============================================================================

EXCEL_FILE = r"C:\Users\qw597f\footprint_data_eda\Invoice FP Tickets Jan thru Oct.xlsx"
SHEET_NAME = "report-1"

OUTPUT_DIR = "eda_output"          # workbook, summary, charts and 2 CSVs land here

# All summary tables are consolidated into a single workbook and/or a single
# Markdown file. The two row-level working files (the per-ticket audit log and
# the manual review sheet) are always written as separate CSVs, because they
# are meant to be filtered and filled in.
TABLE_OUTPUT = "both"              # "xlsx" | "markdown" | "both"
WORKBOOK_NAME = "footprints_eda_tables.xlsx"
MARKDOWN_NAME = "footprints_eda_tables.md"
EMBED_CHARTS_IN_WORKBOOK = True    # adds a Charts sheet; needs Pillow
WORKBOOK_FONT = "Arial"

WINDOW_MONTHS = 3                  # length of the reporting window
WINDOW_ANCHOR = "data_max"         # "data_max" | "today" | "YYYY-MM-DD"

DEDUPE_BY_TICKET = True            # keep one row per Ticket Number
DEDUPE_KEEP = "last_edited"        # "last_edited" | "first"

# uses this list only to flag values it has not seen before.
KNOWN_STATUS_VALUES = [
    "Closed", "Resolved", "Open", "Pending Supplier Response", "In Progress",
]
CLOSED_STATUS_VALUES = ["Closed", "Resolved"]

# Domains treated as non-supplier (verified values, carried over unchanged).
INTERNAL_DOMAINS = ["boeing.com", "ariba.com", "internal", "nan", "unknown.com"]

URGENT_TERMS = [
    r"urgent(?:ly)?", r"escalat\w*", r"block(?:ed|ing)?", r"critical",
    r"delay(?:ed|s|ing)?", r"overdue", r"past\s+due", r"work\s+stoppage",
    r"line\s+down", r"shut\s*down",
]

RANDOM_SEED = 7

# --- sampling and confidence -------------------------------------------------
# The payment flag is applied to every ticket, so the count itself has no
# sampling error. What is uncertain is whether the rule is RIGHT. That is
# measured by reviewing two samples by hand: flagged tickets (precision) and
# not-flagged tickets (missed cases). These settings size those samples.
CONFIDENCE_LEVEL = 0.95            # 0.90 | 0.95 | 0.99
TARGET_MARGIN_OF_ERROR = 0.05      # half-width, e.g. 0.05 = +/- 5 percentage points
REVIEW_SAMPLE_MODE = "auto"        # "auto" sizes from the two settings above
EXTRACTION_REVIEW_SAMPLE = 50      # used only when REVIEW_SAMPLE_MODE = "fixed"

# If the reviewer saves the filled-in sheets next to the script with a
# _REVIEWED suffix, the next run reads them back and reports precision, missed
# rate and an adjusted payment-ticket estimate with intervals.
READ_BACK_REVIEW = True
REVIEW_FILES = {
    "precision": "extraction_sample_for_manual_review_REVIEWED.csv",
    "recall":    "non_payment_sample_for_manual_review_REVIEWED.csv",
}
REVIEW_COL_PRECISION = "Reviewer: is the payment classification correct? (Y/N)"
REVIEW_COL_RECALL = "Reviewer: is this actually payment related? (Y/N)"
SHORT_TEXT_CHARS = 120           
MIN_USABLE_TEXT_CHARS = 20         # below this, a description is not actionable


# =============================================================================
# 2. VERIFIED SCHEMA  -- names and values confirmed by the data owner
# =============================================================================

COL_TICKET    = "Ticket Number"
COL_STATUS    = "Status"
COL_COMMODITY = "Commodity Group"
COL_TYPE      = "Request Type"
COL_SUBTYPE   = "Request Sub-Type"
COL_SUBJECT   = "Subject (Title)"
COL_DETAIL    = "Request Detail"
COL_EMAIL     = "Email Address"
COL_SUBMITTED = "Date Submitted"
COL_EDITED    = "Last Edit Date"

REQUIRED_COLS = [COL_TICKET, COL_TYPE, COL_SUBTYPE, COL_SUBJECT, COL_STATUS, COL_SUBMITTED]
EXPECTED_COLS = [
    COL_TICKET, COL_STATUS, COL_COMMODITY, COL_TYPE, COL_SUBTYPE,
    COL_SUBJECT, COL_DETAIL, COL_EMAIL, COL_SUBMITTED, COL_EDITED,
]
TEXT_COLS = [
    COL_TICKET, COL_STATUS, COL_COMMODITY, COL_TYPE, COL_SUBTYPE,
    COL_SUBJECT, COL_DETAIL, COL_EMAIL,
]
DATE_COLS = [COL_SUBMITTED, COL_EDITED]

BLANK_TOKENS = {"", "nan", "none", "null", "n/a", "na", "-", "--"}


# =============================================================================
# 3. PATTERNS
# =============================================================================

_PO_CORE = r"(?:700\d{7}|701\d{7}|702\d{6,7}|703\d{6,7})"
RE_PO_CUED = re.compile(r"\bP\.?\s*O\.?\s*(?:#|no\.?|number)?\s*[:#\-]?\s*(" + _PO_CORE + r")\b", re.I)
RE_PO_BARE = re.compile(r"\b(" + _PO_CORE + r")\b")

RE_INV_STRICT = re.compile(r"\b(INV|IR|IV|PMT|PAY|CHS|FLY)\s*[#:\-]?\s*(\d{3,}[A-Z0-9\-_]*)\b", re.I)
RE_VENDOR_DOC = re.compile(r"\bV\d{3,}\b", re.I)
RE_INV_LEGACY = re.compile(r"\b(INV|IR|PAY|PMT|CHS|IV|FLY|V\d{3,})\b\s*[#\s:]*\d*[-_\w]*", re.I)

RE_PAY_STRUCTURED = re.compile(r"\b(?:invoice|payment|pay|po|pr|p-?card|card|remittance)\b", re.I)
RE_PAY_TEXT = re.compile(
    r"\b(?:invoices?|invoiced|payments?|paid|unpaid|remittance|remit|"
    r"purchase\s+order|p\.?\s?o\.?|pr|p-?card|wire\s+transfer|wired?|"
    r"past\s+due|overdue)\b", re.I)

INTENT_RULES = [
    ("Reject Request",        r"\breject(?:ed|ion|ing|s)?\b"),
    ("Cancellation Request",  r"\b(?:cancel(?:l?ed|l?ation|l?ing|s)?|void(?:ed)?|delete[ds]?)\b"),
    ("Expedite Request",      r"\b(?:expedite[ds]?|expediting|urgent(?:ly)?|rush)\b"),
    ("Payment Status Query",  r"\b(?:status|unpaid|not\s+(?:yet\s+)?paid|past\s+due|overdue|"
                              r"still\s+waiting|follow[\s-]?up)\b"),
]
INTENT_DEFAULT = "Unclassified / General Support"

RE_URGENT = re.compile(r"\b(?:" + "|".join(URGENT_TERMS) + r")\b", re.I)
RE_DOMAIN = re.compile(r"@([\w.\-]+)")

STOP_WORDS = {
    # grammatical
    "the", "and", "to", "of", "for", "in", "a", "is", "it", "you", "this",
    "that", "with", "on", "from", "we", "have", "has", "had", "be", "are",
    "was", "were", "as", "at", "i", "our", "us", "your", "can", "could",
    "or", "an", "but", "not", "any", "all", "if", "will", "would", "been",
    "they", "their", "there", "which", "when", "what", "who", "how", "by",
    "do", "does", "did", "so", "its", "his", "her", "them", "then",
    # Common email boilerplate
    "please", "hi", "hello", "dear", "thanks", "thank", "regards", "best",
    "team", "am", "pm", "help", "need", "kindly", "com", "www", "http",
    "https", "email", "mail", "sent", "see", "below", "attached", "attach",
    "ticket", "request", "support", "boeing", "ariba", "fyi", "etc",
}


# =============================================================================
# 4. HELPERS
# =============================================================================

def safe_div(numerator, denominator):
    """Percentage helper that returns NaN rather than raising on an empty set."""
    try:
        if denominator in (0, None) or pd.isna(denominator):
            return float("nan")
        return 100.0 * float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        return float("nan")


def fmt_pct(value, digits=1):
    return "n/a" if pd.isna(value) else f"{value:.{digits}f}%"


def fmt_int(value):
    return "n/a" if value is None or (isinstance(value, float) and pd.isna(value)) else f"{int(value):,}"


def fmt_num(value, digits=1):
    return "n/a" if value is None or (isinstance(value, float) and pd.isna(value)) else f"{value:,.{digits}f}"


def norm_text(series):
    """Trim whitespace and collapse blank-ish tokens to NA, without deleting rows."""
    s = series.astype("string").str.strip()
    return s.mask(s.str.lower().isin(BLANK_TOKENS))


def is_blank(series):
    return norm_text(series).isna()


def paths(*parts):
    return os.path.join(OUTPUT_DIR, *parts)


# --- proportion statistics ---------------------------------------------------

def z_for(confidence=None):
    """Two-sided normal critical value for the configured confidence level."""
    conf = CONFIDENCE_LEVEL if confidence is None else confidence
    return NormalDist().inv_cdf(0.5 + conf / 2.0)


def wilson_ci(successes, trials, confidence=None):
    """Wilson score interval - behaves correctly at small n and near 0% or 100%,
    where the textbook normal interval does not."""
    if not trials:
        return (float("nan"), float("nan"))
    z = z_for(confidence)
    p = successes / trials
    denom = 1.0 + z * z / trials
    centre = (p + z * z / (2.0 * trials)) / denom
    half = (z / denom) * math.sqrt(p * (1.0 - p) / trials + z * z / (4.0 * trials * trials))
    return (max(0.0, centre - half), min(1.0, centre + half))


def required_sample_size(population, confidence=None, margin=None, p=0.5):
    """Sample size for a proportion at the given confidence and margin of error,
    with the finite population correction applied."""
    conf = CONFIDENCE_LEVEL if confidence is None else confidence
    moe = TARGET_MARGIN_OF_ERROR if margin is None else margin
    if not population or population <= 0 or moe <= 0:
        return 0
    z = z_for(conf)
    n0 = (z * z * p * (1.0 - p)) / (moe * moe)
    n = n0 / (1.0 + (n0 - 1.0) / population)
    return int(min(population, math.ceil(n)))


def margin_at(n, population, confidence=None, p=0.5):
    """Worst-case (p=0.5) margin of error actually achieved at sample size n."""
    if not n or n <= 0:
        return float("nan")
    z = z_for(confidence)
    base = z * math.sqrt(p * (1.0 - p) / n)
    if population and population >= n > 1:
        base *= math.sqrt((population - n) / (population - 1.0))
    return base


# Ordered registry of every summary table produced during the run.
TABLES = {}
TABLE_NOTES = {}

SHEET_NAMES = {
    "01_column_profile":                 ("01 Column Profile",   "Every column: populated count and %, distinct values, quality notes"),
    "02_data_quality_checks":            ("02 Data Quality",     "Named integrity checks with rows affected and the action taken"),
    "03_row_count_waterfall":            ("03 Row Waterfall",    "Raw rows to final rows, one line per exclusion rule"),
    "04_metric_definitions_and_values":  ("04 Metrics",          "Every metric with its value, calculation, denominator and validation status"),
    "05_monthly_volume":                 ("05 Monthly Volume",   "Tickets per month, payment related vs other"),
    "06_status_mix":                     ("06 Status Mix",       "Ticket status breakdown for the reporting window"),
    "07_request_type_mix":               ("07 Request Type",     "Top 10 Request Type values"),
    "08_request_subtype_mix":            ("08 Request Sub-Type", "Top 10 Request Sub-Type values"),
    "09_commodity_group_mix":            ("09 Commodity Group",  "Top 10 Commodity Group values"),
    "10_payment_evidence":               ("10 Payment Evidence", "How each payment ticket was identified: category, free text, or both"),
    "11_identifier_availability":        ("11 Identifiers",      "Whether a PO or invoice reference was found in the ticket text"),
    "12_action_intent":                  ("12 Action Intent",    "What the requester is asking for"),
    "13_handling_tiers":                 ("13 Handling Tiers",   "How payment tickets would route under an agentic workflow"),
    "14_duration_bands":                 ("14 Duration Bands",   "Closed payment tickets grouped by elapsed days (proxy measure)"),
    "15_top_requester_domains":          ("15 Requester Domains","Top external email domains submitting payment tickets"),
    "16_repeat_po_numbers":              ("16 Repeat POs",       "PO numbers appearing in more than one ticket"),
    "17_repeat_invoice_refs":            ("17 Repeat Invoices",  "Invoice references appearing in more than one ticket"),
    "18_frequent_terms":                 ("18 Frequent Terms",   "Most frequent words in Request Detail, boilerplate removed"),
    "19_non_payment_composition":        ("19 Non-Payment Mix",  "What the tickets excluded by the payment filter actually are"),
    "20_sampling_plan":                  ("20 Sampling Plan",    "Sample sizes needed to put an error bar on the payment classification"),
    "21_sample_size_options":            ("21 Sample Size Options", "Required sample size at other confidence levels and margins of error"),
    "22_estimates_and_intervals":        ("22 Estimates+Intervals", "Which figures are a census, which are estimates, and their intervals"),
    "23_review_results":                 ("23 Review Results",   "Precision, missed rate and adjusted payment count from the completed review sheets"),
}

CHART_CAPTIONS = [
    ("chart_01_monthly_volume.png",           "Is this workload growing, flat or seasonal?"),
    ("chart_02_status_mix.png",               "How much of the workload is still in flight?"),
    ("chart_03_request_type.png",             "Which request categories actually dominate?"),
    ("chart_04_identifier_availability.png",  "Can an agent look a payment up without asking the requester?"),
    ("chart_05_handling_tiers.png",           "What share of payment tickets could be handled end to end?"),
    ("chart_06_duration_bands.png",           "How long do closed tickets take before the last update?"),
]


def write_table(frame, name, index=False):
    """Register a summary table for the consolidated workbook / Markdown file."""
    out = frame.reset_index() if index else frame.copy()
    TABLES[name] = out
    TABLE_NOTES[name] = SHEET_NAMES.get(name, (name, ""))[1]
    return name


def write_csv(frame, filename):
    """Row-level working files stay as standalone CSVs."""
    out = paths(f"{filename}.csv")
    frame.to_csv(out, index=False, encoding="utf-8-sig")
    return out


def count_pct_table(series, label, denominator, top_n=None):
    """Counts alongside percentages - the format stakeholders asked for."""
    counts = norm_text(series).fillna("(blank)").value_counts()
    if top_n is not None and len(counts) > top_n:
        other = counts.iloc[top_n:].sum()
        counts = counts.iloc[:top_n]
        counts["All other values"] = other
    frame = counts.rename("Tickets").to_frame()
    frame.index.name = label
    frame["% of " + str(denominator["label"])] = [
        round(safe_div(v, denominator["value"]), 1) for v in frame["Tickets"]
    ]
    return frame.reset_index()


def barh(frame, label_col, value_col, pct_col, title, xlabel, filename):
    """One plain horizontal bar chart with count and percent on each bar."""
    data = frame.iloc[::-1]
    height = max(2.4, 0.46 * len(data) + 1.5)
    fig, ax = plt.subplots(figsize=(9.5, height))
    bars = ax.barh(data[label_col].astype(str), data[value_col], color="#4878a6")
    span = max(data[value_col].max(), 1)
    for bar, count, pct in zip(bars, data[value_col], data[pct_col]):
        txt = f"{int(count):,}" + ("" if pd.isna(pct) else f"  ({pct:.1f}%)")
        ax.text(bar.get_width() + span * 0.015, bar.get_y() + bar.get_height() / 2,
                txt, va="center", fontsize=9)
    ax.set_xlim(0, span * 1.22)
    ax.set_xlabel(xlabel)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    fig.text(0.015, 0.955, title, fontsize=12, weight="bold", ha="left", va="center")
    fig.savefig(paths("charts", filename), dpi=200)
    plt.close(fig)


# =============================================================================
# 5. LOAD
# =============================================================================

def load_raw():
    if not os.path.exists(EXCEL_FILE):
        raise FileNotFoundError(
            f"Input file not found: {EXCEL_FILE}\n"
            f"Edit EXCEL_FILE at the top of this script."
        )
    print(f"[1/8] Reading {os.path.basename(EXCEL_FILE)} / sheet '{SHEET_NAME}' ...")
    df = pd.read_excel(EXCEL_FILE, sheet_name=SHEET_NAME, engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise KeyError(
            "Required column(s) absent after trimming header whitespace: "
            + ", ".join(missing)
            + "\nColumns found: " + ", ".join(df.columns)
        )
    for col in [c for c in EXPECTED_COLS if c not in df.columns]:
        print(f"      NOTE: expected column '{col}' is not in this export; "
              f"metrics that depend on it will be reported as unavailable.")

    # Keep six-digit ticket IDs, populated Request Type, and payment terms in
    # Request Sub-Type or Subject. Other analysis rules remain unchanged.
    source_rows = len(df)
    ticket = norm_text(df[COL_TICKET]).str.replace(r"\.0$", "", regex=True)
    valid_ticket = ticket.str.fullmatch(r"\d{6}", na=False)
    valid_type = norm_text(df[COL_TYPE]).notna()
    subtype = norm_text(df[COL_SUBTYPE]).fillna("")
    subject = norm_text(df[COL_SUBJECT]).fillna("")
    payment_match = (subtype.str.contains(RE_PAY_STRUCTURED, na=False)
                     | subtype.str.contains(RE_PAY_TEXT, na=False)
                     | subject.str.contains(RE_PAY_TEXT, na=False))
    eligible = df.loc[valid_ticket & valid_type].copy()
    eligible[COL_TICKET] = ticket.loc[eligible.index]
    with_subtype = eligible.loc[norm_text(eligible[COL_SUBTYPE]).notna()]
    df = eligible.loc[payment_match.loc[eligible.index]].copy()
    df.attrs["source_rows"] = source_rows
    df.attrs["prepayment_rows"] = len(with_subtype)
    print(f"      Input: {source_rows:,} rows; six-digit ticket and populated Request Type: "
          f"{len(eligible):,}; also populated Request Sub-Type: {len(with_subtype):,}; "
          f"payment term in Request Sub-Type or Subject: {len(df):,}.")
    print("      Request Sub-Type blanks may be excluded later by the existing cleaning rule.")
    print("      Payment share below is conditional on this filter; it is not the share of all tickets.")
    return df


# =============================================================================
# 6. COLUMN PROFILE  (deliverable: every column, populated count and percent,
#    distinct values, quality checks)
# =============================================================================

def profile_columns(df_raw):
    print("[2/8] Profiling every column ...")
    total = len(df_raw)
    rows = []

    for col in df_raw.columns:
        raw = df_raw[col]
        non_null = int(raw.notna().sum())

        if col in DATE_COLS:
            parsed = pd.to_datetime(raw, errors="coerce")
            populated = int(parsed.notna().sum())
            distinct = int(parsed.dropna().nunique())
            unparseable = int((raw.notna() & parsed.isna()).sum())
            lo = parsed.min()
            hi = parsed.max()
            example = "" if pd.isna(lo) else f"{lo:%Y-%m-%d} to {hi:%Y-%m-%d}"
            top_value, top_share = "", float("nan")
            note = f"{unparseable:,} value(s) present but not parseable as a date" if unparseable else ""
        elif pd.api.types.is_numeric_dtype(raw):
            populated = non_null
            distinct = int(raw.dropna().nunique())
            example = f"min {raw.min():,.2f} / max {raw.max():,.2f}" if populated else ""
            top_value, top_share = "", float("nan")
            note = ""
        else:
            clean = norm_text(raw)
            populated = int(clean.notna().sum())
            distinct = int(clean.dropna().nunique())
            blank_tokens = int((raw.notna() & clean.isna()).sum())
            vc = clean.dropna().value_counts()
            top_value = str(vc.index[0])[:60] if len(vc) else ""
            top_share = safe_div(vc.iloc[0], populated) if len(vc) else float("nan")
            sample = [str(v)[:40] for v in clean.dropna().unique()[:3]]
            example = " | ".join(sample)
            notes = []
            if blank_tokens:
                notes.append(f"{blank_tokens:,} placeholder blank(s) ('nan'/'none'/'')")
            trimmed = raw.astype("string")
            ws = int((trimmed.notna() & (trimmed != trimmed.str.strip())).sum())
            if ws:
                notes.append(f"{ws:,} value(s) with leading/trailing spaces")
            if populated and distinct and col not in (COL_TICKET, COL_SUBJECT, COL_DETAIL, COL_EMAIL):
                case_variants = clean.dropna().str.lower().nunique()
                if case_variants < distinct:
                    notes.append(f"{distinct - case_variants} value(s) differ only by letter case")
            note = "; ".join(notes)

        rows.append({
            "Column": col,
            "Type": "date" if col in DATE_COLS else ("number" if pd.api.types.is_numeric_dtype(raw) else "text"),
            "Populated (rows)": populated,
            "Populated %": round(safe_div(populated, total), 1),
            "Blank / missing (rows)": total - populated,
            "Distinct values": distinct,
            "Most common value": top_value,
            "Most common % of populated": None if pd.isna(top_share) else round(top_share, 1),
            "Range / examples": example,
            "Quality note": note,
            "In verified schema": "yes" if col in EXPECTED_COLS else "NO - unexpected column",
        })

    profile = pd.DataFrame(rows)
    write_table(profile, "01_column_profile")
    return profile


# =============================================================================
# 7. DATA-QUALITY CHECKS
# =============================================================================

def quality_checks(df_raw):
    print("[3/8] Running data-quality checks ...")
    total = len(df_raw)
    checks = []

    def add(check, rows_affected, matters, action):
        checks.append({
            "Check": check,
            "Rows affected": rows_affected,
            "% of raw rows": round(safe_div(rows_affected, total), 1),
            "Why it matters": matters,
            "Action taken by this script": action,
        })

    ticket = norm_text(df_raw[COL_TICKET])
    add("Ticket Number blank or placeholder", int(ticket.isna().sum()),
        "Row cannot be tied to a ticket; it cannot be counted or deduplicated.",
        "Row excluded, counted in the waterfall.")

    dup_rows = int(ticket.dropna().duplicated(keep=False).sum())
    dup_ids = int((ticket.dropna().value_counts() > 1).sum())
    add(f"Duplicate Ticket Number ({dup_ids:,} ticket id(s) appear more than once)", dup_rows,
        "FootPrints exports often carry one row per update. If so, every volume "
        "count can be inflated.",
        "Deduplicated." if DEDUPE_BY_TICKET else "Kept (DEDUPE_BY_TICKET is off).")

    for col, label in [(COL_TYPE, "Request Type"), (COL_SUBTYPE, "Request Sub-Type")]:
        if col in df_raw.columns:
            add(f"{label} blank or placeholder", int(is_blank(df_raw[col]).sum()),
                "Missing category information may limit reporting.",
                "Request Type excluded; blank Request Sub-Type retained if Subject matches."
                if col == COL_SUBTYPE else "Row excluded by the input filter.")

    if COL_STATUS in df_raw.columns:
        status = norm_text(df_raw[COL_STATUS])
        add("Status blank or placeholder", int(status.isna().sum()),
            "Open vs closed cannot be determined.", "Row kept; reported as '(blank)'.")
        unexpected = sorted(set(status.dropna().unique()) - set(KNOWN_STATUS_VALUES))
        n_unexpected = int(status.isin(unexpected).sum()) if unexpected else 0
        add("Status value outside the five known values"
            + (f" ({', '.join(map(str, unexpected[:6]))}{' ...' if len(unexpected) > 6 else ''})" if unexpected else ""),
            n_unexpected,
            "Unexpected statuses are reported and retained.",
            "Row kept and reported. No status filter is applied.")

    submitted = pd.to_datetime(df_raw[COL_SUBMITTED], errors="coerce")
    add("Date Submitted missing or unparseable", int(submitted.isna().sum()),
        "Ticket cannot be placed in a reporting period.",
        "Row excluded, counted in the waterfall.")

    if COL_EDITED in df_raw.columns:
        edited = pd.to_datetime(df_raw[COL_EDITED], errors="coerce")
        add("Last Edit Date missing or unparseable", int(edited.isna().sum()),
            "Duration cannot be computed for this ticket.",
            "Excluded from duration metrics only; still counted in volume.")
        add("Last Edit Date earlier than Date Submitted",
            int((edited.notna() & submitted.notna() & (edited < submitted)).sum()),
            "Negative elapsed time indicates a timestamp or export problem.",
            "Excluded from duration metrics and reported here.")

    if COL_DETAIL in df_raw.columns:
        detail = norm_text(df_raw[COL_DETAIL])
        add("Request Detail blank", int(detail.isna().sum()),
            "No free text for an agent to read.",
            "Flagged and routed to manual review.")
        add(f"Request Detail shorter than {MIN_USABLE_TEXT_CHARS} characters",
            int((detail.fillna("").str.len() < MIN_USABLE_TEXT_CHARS).sum()),
            "Too little content to classify or investigate.",
            "Flagged and routed to manual review.")

    if COL_EMAIL in df_raw.columns:
        email = norm_text(df_raw[COL_EMAIL])
        add("Email Address blank", int(email.isna().sum()),
            "Requester cannot be attributed to an internal team or a supplier.",
            "Reported as 'unknown'.")
        add("Email Address without an @domain", int((email.notna() & ~email.str.contains("@", na=False)).sum()),
            "Domain parsing fails; supplier attribution is unreliable.", "Reported as 'unknown'.")

    frame = pd.DataFrame(checks)
    write_table(frame, "02_data_quality_checks")
    return frame


# =============================================================================
# 8. CLEANING LADDER  (auditable row-count waterfall)
# =============================================================================

def _payment_looking(frame):
    """Cheap payment flag applied to RAW rows, so the waterfall can report how
    many of the rows removed at each step looked payment related."""
    def col(name):
        return frame[name].astype("string").fillna("") if name in frame.columns else pd.Series("", index=frame.index, dtype="string")
    cat = (col(COL_TYPE) + " " + col(COL_SUBTYPE)).astype(str)
    txt = (col(COL_SUBJECT) + " " + col(COL_DETAIL)).astype(str)
    return cat.str.contains(RE_PAY_STRUCTURED, na=False) | txt.str.contains(RE_PAY_TEXT, na=False)


def build_clean(df_raw):
    print("[4/8] Applying the exclusion ladder ...")
    steps = []
    df = df_raw.copy()
    start = len(df)
    pay_look = _payment_looking(df_raw)
    pay_start = int(pay_look.sum())

    def record(step, before_idx, after_idx, rule):
        removed = before_idx.difference(after_idx)
        steps.append({
            "Step": step,
            "Rows in": len(before_idx),
            "Rows removed": len(removed),
            "Of the removed, look payment related": int(pay_look.reindex(removed).fillna(False).sum())
                if len(removed) else 0,
            "Rows out": len(after_idx),
            "% of raw retained": round(safe_div(len(after_idx), start), 1),
            "Rule": rule,
        })

    for col in [c for c in TEXT_COLS if c in df.columns]:
        df[col] = norm_text(df[col])
    for col in [c for c in DATE_COLS if c in df.columns]:
        df[col] = pd.to_datetime(df[col], errors="coerce")

    record("0. Raw rows read from the sheet", df.index, df.index, "No filter.")

    before = df.index
    df = df[df[COL_TICKET].notna()]
    record("1. Drop rows with no Ticket Number", before, df.index,
           "Ticket Number is blank or a placeholder.")

    if DEDUPE_BY_TICKET:
        before = df.index
        if DEDUPE_KEEP == "last_edited" and COL_EDITED in df.columns:
            df = (df.sort_values([COL_TICKET, COL_EDITED], na_position="first")
                    .drop_duplicates(subset=[COL_TICKET], keep="last"))
        else:
            df = df.drop_duplicates(subset=[COL_TICKET], keep="first")
        record("2. Deduplicate to one row per ticket", before, df.index,
               f"Keep the {'most recently edited' if DEDUPE_KEEP == 'last_edited' else 'first'} "
               f"row per Ticket Number. Removed rows are repeat updates of a ticket that is "
               f"still counted, NOT lost tickets.")
    else:
        record("2. Deduplicate to one row per ticket", df.index, df.index,
               "SKIPPED - DEDUPE_BY_TICKET is False.")

    before = df.index
    df = df[df[COL_TYPE].notna()]
    record("3. Drop rows with no Request Type", before, df.index,
           "Request Type must be populated; a blank Sub-Type may still have a matching Subject.")

    before = df.index
    df = df[df[COL_SUBMITTED].notna()]
    record("4. Drop rows with no usable Date Submitted", before, df.index,
           "Ticket cannot be placed in a reporting period.")

    record("5. Status filter", df.index, df.index,
           "NOT APPLIED. Unexpected statuses are reported in the quality checks.")

    df = df.sort_values(COL_SUBMITTED).reset_index(drop=True)
    df["Submission Month"] = df[COL_SUBMITTED].dt.to_period("M").astype(str)
    return df, steps, pay_start


def extend_waterfall(steps, n_clean, n_window, n_payment, start_rows, meta):
    """Append reporting-window and payment-classification steps."""
    steps.append({
        "Step": "6. Outside the reporting window",
        "Rows in": n_clean,
        "Rows removed": n_clean - n_window,
        "Of the removed, look payment related": "",
        "Rows out": n_window,
        "% of raw retained": round(safe_div(n_window, start_rows), 1),
        "Rule": f"Date Submitted outside {meta['label']}. These are real tickets from "
                f"other periods, not data problems. Set WINDOW_MONTHS to widen.",
    })
    steps.append({
        "Step": "7. Not classified as payment related",
        "Rows in": n_window,
        "Rows removed": n_window - n_payment,
        "Of the removed, look payment related": "",
        "Rows out": n_payment,
        "% of raw retained": round(safe_div(n_payment, start_rows), 1),
        "Rule": "No payment term in Request Type / Sub-Type and none in Subject / "
                "Request Detail. See sheet 19 for what these tickets are, and the "
                "non-payment review CSV to test whether any were missed.",
    })
    frame = pd.DataFrame(steps)
    write_table(frame, "03_row_count_waterfall")
    return frame


def apply_window(df_clean):
    if WINDOW_ANCHOR == "data_max":
        anchor = df_clean[COL_SUBMITTED].max()
    elif WINDOW_ANCHOR == "today":
        anchor = pd.Timestamp(datetime.now().date())
    else:
        anchor = pd.Timestamp(WINDOW_ANCHOR)

    start = anchor - pd.DateOffset(months=WINDOW_MONTHS)
    window = df_clean[(df_clean[COL_SUBMITTED] >= start) & (df_clean[COL_SUBMITTED] <= anchor)].copy()
    meta = {
        "anchor": anchor, "start": start,
        "label": f"{start:%d %b %Y} to {anchor:%d %b %Y}",
        "full_label": (f"{df_clean[COL_SUBMITTED].min():%d %b %Y} to "
                       f"{df_clean[COL_SUBMITTED].max():%d %b %Y}") if len(df_clean) else "n/a",
    }
    print(f"      Reporting window: {meta['label']} "
          f"({len(window):,} of {len(df_clean):,} clean tickets)")
    return window, meta


# =============================================================================
# 9. CLASSIFICATION AND EXTRACTION
# =============================================================================

def classify_and_extract(df):
    print("[5/8] Classifying payment tickets and extracting identifiers ...")
    out = df.copy()
    subject = out[COL_SUBJECT].fillna("") if COL_SUBJECT in out.columns else pd.Series("", index=out.index)
    detail = out[COL_DETAIL].fillna("") if COL_DETAIL in out.columns else pd.Series("", index=out.index)
    combined = (subject.astype(str) + " " + detail.astype(str)).str.strip()

    out["Has Description"] = detail.astype(str).str.len() >= MIN_USABLE_TEXT_CHARS
    out["Description Length"] = detail.astype(str).str.len()

    # --- payment evidence, kept separate on purpose -------------------------
    cat = (out[COL_TYPE].fillna("") + " " + out[COL_SUBTYPE].fillna("")).astype(str)
    out["Payment Evidence - Category"] = cat.str.contains(RE_PAY_STRUCTURED, na=False)
    out["Payment Evidence - Free text"] = combined.str.contains(RE_PAY_TEXT, na=False)
    out["Is Payment Ticket"] = out["Payment Evidence - Category"] | out["Payment Evidence - Free text"]
    out["Payment Evidence"] = np.select(
        [out["Payment Evidence - Category"] & out["Payment Evidence - Free text"],
         out["Payment Evidence - Category"] & ~out["Payment Evidence - Free text"],
         ~out["Payment Evidence - Category"] & out["Payment Evidence - Free text"]],
        ["Category and text agree (strongest)",
         "Category only",
         "Free text only (weakest - verify)"],
        default="Not payment related")

    # --- identifiers ---------------------------------------------------------
    def first_group(pattern, text, group=1):
        m = pattern.search(text)
        return m.group(group) if m else None

    po_cued = combined.map(lambda t: first_group(RE_PO_CUED, t))
    po_bare = combined.map(lambda t: first_group(RE_PO_BARE, t))
    out["PO Number"] = po_cued.where(po_cued.notna(), po_bare)
    out["PO Evidence"] = np.where(po_cued.notna(), "Labelled 'PO' in the text",
                          np.where(po_bare.notna(), "Bare number matching a PO pattern (weaker)", "None"))

    def inv_strict(t):
        m = RE_INV_STRICT.search(t)
        if m:
            return (m.group(1) + m.group(2)).upper()
        m = RE_VENDOR_DOC.search(t)
        return m.group(0).upper() if m else None

    out["Invoice Reference"] = combined.map(inv_strict)
    out["Invoice Reference (broad pattern)"] = combined.map(
        lambda t: (lambda m: m.group(0).upper() if m else None)(RE_INV_LEGACY.search(t)))

    out["Has PO"] = out["PO Number"].notna()
    out["Has Invoice"] = out["Invoice Reference"].notna()
    out["Identifier Availability"] = np.select(
        [out["Has PO"] & out["Has Invoice"], out["Has PO"] & ~out["Has Invoice"],
         ~out["Has PO"] & out["Has Invoice"]],
        ["Both PO and invoice reference", "PO only", "Invoice reference only"],
        default="Neither - agent must ask the requester")

    # --- intent --------------------------------------------------------------
    intent = pd.Series(INTENT_DEFAULT, index=out.index, dtype=object)
    unassigned = pd.Series(True, index=out.index)
    for label, pattern in INTENT_RULES:
        hit = unassigned & combined.str.contains(pattern, case=False, regex=True, na=False)
        intent[hit] = label
        unassigned &= ~hit
    out["Action Intent"] = intent

    # --- escalation language ---------------------------------------------------
    out["Escalation Language"] = combined.str.contains(RE_URGENT, na=False)

    # --- requester domain -------------------------------------------------------
    if COL_EMAIL in out.columns:
        out["Requester Domain"] = out[COL_EMAIL].fillna("").astype(str).str.lower().map(
            lambda e: (lambda m: m.group(1) if m else "unknown")(RE_DOMAIN.search(e)))
    else:
        out["Requester Domain"] = "unknown"
    out["Requester Origin"] = np.where(
        out["Requester Domain"].isin(INTERNAL_DOMAINS + ["unknown"]),
        "Internal or unknown", "External supplier domain")

    # --- duration (named for what it actually measures) --------------------------
    if COL_EDITED in out.columns:
        delta = (out[COL_EDITED] - out[COL_SUBMITTED]).dt.days
        out["Days Submitted To Last Edit"] = delta.where(delta >= 0)
    else:
        out["Days Submitted To Last Edit"] = np.nan
    out["Is Closed"] = out[COL_STATUS].isin(CLOSED_STATUS_VALUES)

    # --- handling tier -----------------------------------------------------------
    def tier(row):
        if row["Escalation Language"] or not row["Has Description"]:
            return "C - Manual review first"
        if (row["Has PO"] or row["Has Invoice"]) and row["Action Intent"] != INTENT_DEFAULT:
            return "A - Agent-ready candidate"
        return "B - Agent-assisted, needs a lookup"

    out["Handling Tier"] = out.apply(tier, axis=1)
    return out


# =============================================================================
# 10. METRICS
# =============================================================================

def build_metrics(window, payment):
    print("[6/8] Computing business metrics ...")
    n_win = len(window)
    n_pay = len(payment)
    den_win = {"label": "tickets in window", "value": n_win}
    den_pay = {"label": "payment tickets", "value": n_pay}
    rows = []

    def add(mid, name, value, calc, den_label, den_value, scope, validation="Computed from the local file"):
        rows.append({
            "ID": mid, "Metric": name, "Value": value, "Calculation": calc,
            "Denominator": den_label, "Denominator value": den_value,
            "Scope": scope, "Validation": validation,
        })

    add("V1", "Tickets in reporting window", n_win,
        "Count of rows surviving the exclusion ladder with Date Submitted inside the window.",
        "Clean tickets, all periods", None, "Window")
    add("V2", "Average tickets per month", round(window.groupby("Submission Month").size().mean(), 1) if n_win else np.nan,
        "Window ticket count divided by the number of calendar months present.",
        "Months present in window", int(window["Submission Month"].nunique()) if n_win else 0, "Window")
    add("V3", "Payment-related tickets", n_pay,
        "Request Type/Sub-Type matches a payment term, OR Subject/Request Detail matches a payment term (word-boundary match).",
        "tickets in window", n_win, "Window")
    add("V4", "Payment share of selected tickets", round(safe_div(n_pay, n_win), 1),
        "Payment tickets / tickets in window x 100.", "tickets in window", n_win, "Window")
    add("V5", "Payment tickets evidenced by category, not only text",
        int(payment["Payment Evidence - Category"].sum()) if n_pay else 0,
        "Payment tickets whose Request Type or Sub-Type matches a payment term.",
        den_pay["label"], n_pay, "Payment tickets",
        "Computed. The free-text-only group needs a manual sample check - Requires data validation")

    if n_pay:
        open_mask = ~payment["Is Closed"]
        add("S1", "Payment tickets still open", int(open_mask.sum()),
            "Payment tickets whose Status is not Closed or Resolved.", den_pay["label"], n_pay, "Payment tickets")
        add("S2", "Open share of payment tickets", round(safe_div(open_mask.sum(), n_pay), 1),
            "Open payment tickets / payment tickets x 100.", den_pay["label"], n_pay, "Payment tickets")

        closed = payment.loc[payment["Is Closed"], "Days Submitted To Last Edit"].dropna()
        add("R1", "Closed payment tickets with a usable duration", len(closed),
            "Closed/Resolved payment tickets where Last Edit Date >= Date Submitted and both parse.",
            "closed payment tickets", int(payment["Is Closed"].sum()), "Closed payment tickets")
        add("R2", "Median days from submission to last edit (closed)",
            round(closed.median(), 1) if len(closed) else np.nan,
            "Median of (Last Edit Date - Date Submitted) in days, closed tickets only.",
            "closed payment tickets with a usable duration", len(closed), "Closed payment tickets",
            "Proxy only. Last Edit Date is the last touch, not a resolution event - Requires data validation")
        add("R3", "90th percentile days to last edit (closed)",
            round(closed.quantile(0.9), 1) if len(closed) else np.nan,
            "90th percentile of the same measure.",
            "closed payment tickets with a usable duration", len(closed), "Closed payment tickets",
            "Proxy only - Requires data validation")
        add("R4", "Median age of open payment tickets (days)",
            round(payment.loc[open_mask, "Days Submitted To Last Edit"].dropna().median(), 1)
            if open_mask.any() else np.nan,
            "Median of (Last Edit Date - Date Submitted) for tickets not Closed/Resolved.",
            "open payment tickets with a usable duration",
            int(payment.loc[open_mask, "Days Submitted To Last Edit"].notna().sum()), "Open payment tickets",
            "Measures time to last touch, not time outstanding - Requires data validation")

        add("E1", "Payment tickets with a PO number", int(payment["Has PO"].sum()),
            "Subject or Request Detail contains a number matching a verified PO pattern.",
            den_pay["label"], n_pay, "Payment tickets",
            "Yield only, not accuracy. Review the manual sample CSV - Requires data validation")
        add("E2", "PO numbers explicitly labelled 'PO' in the text",
            int((payment["PO Evidence"] == "Labelled 'PO' in the text").sum()),
            "Subset of E1 where the number is preceded by a PO cue.",
            "payment tickets with a PO number", int(payment["Has PO"].sum()), "Payment tickets")
        add("E3", "Payment tickets with an invoice reference", int(payment["Has Invoice"].sum()),
            "Prefix (INV/IR/IV/PMT/PAY/CHS/FLY) followed by at least 3 digits, or a V-number.",
            den_pay["label"], n_pay, "Payment tickets",
            "Yield only, not accuracy - Requires data validation")
        add("E4", "Invoice references counted by the broad pattern",
            int(payment["Invoice Reference (broad pattern)"].notna().sum()),
            "Broad identifier pattern; may match ordinary words without a document number.",
            den_pay["label"], n_pay, "Payment tickets",
            "For comparison only; not a validated identifier count.")
        add("E5", "Payment tickets with neither identifier",
            int((~payment["Has PO"] & ~payment["Has Invoice"]).sum()),
            "No PO number and no invoice reference found in Subject or Request Detail.",
            den_pay["label"], n_pay, "Payment tickets")

        add("O1", "Payment tickets from an external supplier domain",
            int((payment["Requester Origin"] == "External supplier domain").sum()),
            "Email domain is not in the internal-domain list and is not blank.",
            den_pay["label"], n_pay, "Payment tickets",
            "Email domain is not a verified supplier identity - Requires data validation")
        add("O2", "Distinct requester domains", int(payment["Requester Domain"].nunique()),
            "Distinct @domain values among payment tickets.", den_pay["label"], n_pay, "Payment tickets")

        for tier_name in ["A - Agent-ready candidate", "B - Agent-assisted, needs a lookup", "C - Manual review first"]:
            cnt = int((payment["Handling Tier"] == tier_name).sum())
            add("T" + tier_name[0], f"Tier {tier_name}", cnt,
                "See the tier rules printed in the dashboard.", den_pay["label"], n_pay, "Payment tickets",
                "Rule-based estimate from text only - Requires data validation")

    metrics = pd.DataFrame(rows)
    write_table(metrics, "04_metric_definitions_and_values")
    return metrics, den_win, den_pay


# =============================================================================
# 11. DASHBOARD TABLES AND CHARTS
# =============================================================================

def build_dashboard(window, payment, non_payment, den_win, den_pay, meta):
    print("[7/8] Building dashboard tables and charts ...")
    tables = {}

    monthly = (window.assign(Group=np.where(window["Is Payment Ticket"], "Payment related", "Other"))
                     .pivot_table(index="Submission Month", columns="Group",
                                  values=COL_TICKET, aggfunc="count", fill_value=0))
    for col in ["Payment related", "Other"]:
        if col not in monthly.columns:
            monthly[col] = 0
    monthly = monthly[["Payment related", "Other"]]
    monthly["Total"] = monthly.sum(axis=1)
    monthly["Payment %"] = [round(safe_div(p, t), 1) for p, t in zip(monthly["Payment related"], monthly["Total"])]
    tables["05_monthly_volume"] = monthly.reset_index()

    if len(monthly):
        fig, ax = plt.subplots(figsize=(9.5, 4.6))
        idx = range(len(monthly))
        ax.bar(idx, monthly["Payment related"], label="Payment related", color="#4878a6")
        ax.bar(idx, monthly["Other"], bottom=monthly["Payment related"], label="Other", color="#c6cdd4")
        for i, total in enumerate(monthly["Total"]):
            ax.text(i, total, f"{int(total):,}", ha="center", va="bottom", fontsize=9)
        ax.set_xticks(list(idx))
        ax.set_xticklabels(monthly.index, rotation=0)
        ax.set_ylabel("Tickets submitted")
        ax.legend(frameon=False, loc="upper left")
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_ylim(0, max(monthly["Total"].max() * 1.18, 1))
        fig.tight_layout(rect=(0, 0, 1, 0.90))
        fig.text(0.015, 0.955, "Monthly ticket volume, payment related vs other",
                 fontsize=12, weight="bold", ha="left", va="center")
        fig.savefig(paths("charts", "chart_01_monthly_volume.png"), dpi=200)
        plt.close(fig)

    status = count_pct_table(window[COL_STATUS], "Status", den_win)
    tables["06_status_mix"] = status
    barh(status, "Status", "Tickets", status.columns[-1],
         f"Ticket status mix, {meta['label']}", "Tickets", "chart_02_status_mix.png")

    tables["07_request_type_mix"] = count_pct_table(window[COL_TYPE], "Request Type", den_win, top_n=10)
    barh(tables["07_request_type_mix"], "Request Type", "Tickets",
         tables["07_request_type_mix"].columns[-1],
         "Tickets by Request Type (top 10)", "Tickets", "chart_03_request_type.png")
    tables["08_request_subtype_mix"] = count_pct_table(window[COL_SUBTYPE], "Request Sub-Type", den_win, top_n=10)
    if COL_COMMODITY in window.columns:
        tables["09_commodity_group_mix"] = count_pct_table(window[COL_COMMODITY], "Commodity Group", den_win, top_n=10)

    if len(payment):
        tables["10_payment_evidence"] = count_pct_table(payment["Payment Evidence"], "Payment evidence", den_pay)

        ident = count_pct_table(payment["Identifier Availability"], "Identifier found in the ticket text", den_pay)
        tables["11_identifier_availability"] = ident
        barh(ident, "Identifier found in the ticket text", "Tickets", ident.columns[-1],
             "Can an agent look the payment up without asking the requester?",
             "Payment tickets", "chart_04_identifier_availability.png")

        tables["12_action_intent"] = count_pct_table(payment["Action Intent"], "Action Intent", den_pay)

        tier = count_pct_table(payment["Handling Tier"], "Handling Tier", den_pay).sort_values("Handling Tier")
        tables["13_handling_tiers"] = tier
        barh(tier, "Handling Tier", "Tickets", tier.columns[-1],
             "How payment tickets would route under an agentic workflow",
             "Payment tickets", "chart_05_handling_tiers.png")

        closed = payment.loc[payment["Is Closed"], "Days Submitted To Last Edit"].dropna()
        bands = ["0-1 days", "2-5 days", "6-10 days", "11-20 days", "21+ days"]
        cut = pd.cut(closed, bins=[-0.1, 1, 5, 10, 20, np.inf], labels=bands) if len(closed) else pd.Series(dtype="object")
        band_counts = (cut.value_counts().reindex(bands, fill_value=0) if len(closed)
                       else pd.Series([0] * 5, index=bands))
        band_frame = band_counts.rename("Tickets").to_frame()
        band_frame.index.name = "Days from submission to last edit"
        band_frame["% of closed payment tickets with a usable duration"] = [
            round(safe_div(v, len(closed)), 1) for v in band_frame["Tickets"]]
        band_frame = band_frame.reset_index()
        tables["14_duration_bands"] = band_frame
        barh(band_frame, "Days from submission to last edit", "Tickets", band_frame.columns[-1],
             f"Closed payment tickets by elapsed days (n={len(closed):,}; proxy measure)",
             "Closed payment tickets", "chart_06_duration_bands.png")

        tables["15_top_requester_domains"] = count_pct_table(
            payment.loc[payment["Requester Origin"] == "External supplier domain", "Requester Domain"],
            "External requester domain",
            {"label": "external payment tickets",
             "value": int((payment["Requester Origin"] == "External supplier domain").sum())},
            top_n=10)

        po_counts = payment.loc[payment["Has PO"], "PO Number"].value_counts().head(10)
        tables["16_repeat_po_numbers"] = po_counts.rename("Tickets").rename_axis("PO Number").reset_index()
        inv_counts = payment.loc[payment["Has Invoice"], "Invoice Reference"].value_counts().head(10)
        tables["17_repeat_invoice_refs"] = inv_counts.rename("Tickets").rename_axis("Invoice Reference").reset_index()

        words = []
        for txt in payment[COL_DETAIL].fillna("").astype(str):
            words += [w for w in re.findall(r"\b[a-zA-Z]{3,}\b", txt.lower()) if w not in STOP_WORDS]
        tables["18_frequent_terms"] = pd.DataFrame(
            pd.Series(words).value_counts().head(15).rename("Occurrences")).rename_axis("Term").reset_index()

        audit_cols = [c for c in [COL_TICKET, COL_SUBMITTED, COL_STATUS, COL_TYPE, COL_SUBTYPE,
                                  COL_SUBJECT, "Payment Evidence", "PO Number", "PO Evidence",
                                  "Invoice Reference", "Action Intent", "Escalation Language",
                                  "Handling Tier", "Days Submitted To Last Edit"] if c in payment.columns]
        tables["csv_ticket_audit_log"] = payment[audit_cols]

        n_draw = (required_sample_size(len(payment)) if REVIEW_SAMPLE_MODE == "auto"
                  else EXTRACTION_REVIEW_SAMPLE)
        n_draw = min(n_draw, len(payment))
        tables["csv_extraction_sample"] = (
            payment[audit_cols].sample(n=n_draw, random_state=RANDOM_SEED)
            .assign(**{REVIEW_COL_PRECISION: "",
                       "Reviewer: is the PO/invoice correct? (Y/N)": "",
                       "Reviewer: is the intent correct? (Y/N)": "",
                       "Reviewer note": ""}))

    # --- the excluded population: what the payment filter left behind ---------
    if len(non_payment):
        comp = count_pct_table(non_payment[COL_TYPE], "Request Type",
                               {"label": "non-payment tickets", "value": len(non_payment)}, top_n=15)
        tables["19_non_payment_composition"] = comp

        np_cols = [c for c in [COL_TICKET, COL_SUBMITTED, COL_STATUS, COL_TYPE, COL_SUBTYPE,
                               COL_SUBJECT] if c in non_payment.columns]
        n_np = (required_sample_size(len(non_payment)) if REVIEW_SAMPLE_MODE == "auto"
                else EXTRACTION_REVIEW_SAMPLE)
        n_np = min(n_np, len(non_payment))
        tables["csv_non_payment_sample"] = (
            non_payment[np_cols].sample(n=n_np, random_state=RANDOM_SEED)
            .assign(**{REVIEW_COL_RECALL: "", "Reviewer note": ""}))

    for name, frame in tables.items():
        if name == "csv_ticket_audit_log":
            write_csv(frame, "ticket_audit_log")
        elif name == "csv_extraction_sample":
            write_csv(frame, "extraction_sample_for_manual_review")
        elif name == "csv_non_payment_sample":
            write_csv(frame, "non_payment_sample_for_manual_review")
        else:
            write_table(frame, name)
    return tables


# =============================================================================
# 11b. SAMPLING, INTERVALS AND REVIEW READ-BACK
# =============================================================================

def build_sampling(window, payment, non_payment):
    """Size the manual review samples and state the resulting error bars."""
    tables = {}
    n_pay, n_non = len(payment), len(non_payment)

    plan_rows = []
    for label, population, measures in [
        ("Flagged as payment related", n_pay,
         "Precision: of the tickets the rule flagged, what share really are payment related"),
        ("Not flagged as payment related", n_non,
         "Missed cases: of the tickets the rule skipped, what share really are payment related"),
    ]:
        if population == 0:
            continue
        required = required_sample_size(population)
        drawn = min(population, required if REVIEW_SAMPLE_MODE == "auto" else EXTRACTION_REVIEW_SAMPLE)
        plan_rows.append({
            "Population": label,
            "Population size": population,
            "Confidence level": f"{CONFIDENCE_LEVEL:.0%}",
            "Target margin of error": f"+/- {TARGET_MARGIN_OF_ERROR:.0%}",
            "Required sample size": required,
            "Sample drawn by this run": drawn,
            "Margin of error at that sample (worst case)":
                round(100 * margin_at(drawn, population), 1),
            "What it measures": measures,
        })
    plan = pd.DataFrame(plan_rows)
    tables["20_sampling_plan"] = plan

    option_rows = []
    for label, population in [("Flagged as payment related", n_pay),
                              ("Not flagged as payment related", n_non)]:
        if population == 0:
            continue
        for conf in (0.90, 0.95, 0.99):
            for moe in (0.10, 0.075, 0.05):
                n = required_sample_size(population, conf, moe)
                option_rows.append({
                    "Population": label,
                    "Population size": population,
                    "Confidence level": f"{conf:.0%}",
                    "Margin of error": f"+/- {moe:.1%}",
                    "Rows a reviewer must check": n,
                })
    tables["21_sample_size_options"] = pd.DataFrame(option_rows)
    return tables, {r["Population"]: r["Sample drawn by this run"] for r in plan_rows}


def read_back_review(n_pay, n_non):
    """If the reviewer saved the filled-in sheets, turn them into precision,
    missed rate and an adjusted payment count with intervals."""
    def tally(filename, column):
        path = os.path.join(os.path.dirname(os.path.abspath(EXCEL_FILE)) if False else ".", filename)
        for candidate in (filename, paths(filename), path):
            if os.path.exists(candidate):
                frame = pd.read_csv(candidate)
                if column not in frame.columns:
                    print(f"      NOTE: {candidate} has no column '{column}'; skipped.")
                    return None
                answers = frame[column].astype(str).str.strip().str.upper().str[:1]
                yes = int((answers == "Y").sum())
                no = int((answers == "N").sum())
                return (yes, yes + no, candidate)
        return None

    if not READ_BACK_REVIEW:
        return None
    prec = tally(REVIEW_FILES["precision"], REVIEW_COL_PRECISION)
    rec = tally(REVIEW_FILES["recall"], REVIEW_COL_RECALL) if n_non else None
    if not prec and not rec:
        return None

    rows = []
    p_hat = q_hat = None
    p_lo = p_hi = q_lo = q_hi = None
    if prec and prec[1]:
        yes, n, src = prec
        p_hat, (p_lo, p_hi) = yes / n, wilson_ci(yes, n)
        rows.append({
            "Measure": "Precision of the payment rule",
            "Reviewed": n, "Confirmed": yes,
            "Estimate %": round(100 * p_hat, 1),
            "Lower %": round(100 * p_lo, 1), "Upper %": round(100 * p_hi, 1),
            "Interval": f"Wilson score, {CONFIDENCE_LEVEL:.0%} confidence",
            "Source file": os.path.basename(src),
        })
    if rec and rec[1]:
        yes, n, src = rec
        q_hat, (q_lo, q_hi) = yes / n, wilson_ci(yes, n)
        rows.append({
            "Measure": "Payment tickets missed by the rule",
            "Reviewed": n, "Confirmed": yes,
            "Estimate %": round(100 * q_hat, 1),
            "Lower %": round(100 * q_lo, 1), "Upper %": round(100 * q_hi, 1),
            "Interval": f"Wilson score, {CONFIDENCE_LEVEL:.0%} confidence",
            "Source file": os.path.basename(src),
        })
    if p_hat is not None and q_hat is not None:
        rows.append({
            "Measure": "Adjusted payment tickets in the window",
            "Reviewed": "", "Confirmed": "",
            "Estimate %": round(n_pay * p_hat + n_non * q_hat, 0),
            "Lower %": round(n_pay * p_lo + n_non * q_lo, 0),
            "Upper %": round(n_pay * p_hi + n_non * q_hi, 0),
            "Interval": "Counts, not percentages. Bounds combine both intervals "
                        "at their extremes, so this is a conservative range.",
            "Source file": "both review files",
        })
    return pd.DataFrame(rows) if rows else None


def build_estimates(window, payment, non_payment, review):
    """Separate the figures that are a census from the ones that are estimates."""
    n_win, n_pay, n_non = len(window), len(payment), len(non_payment)
    census_note = ("Census of every ticket in the window - there is no sampling error. "
                   "The uncertainty is whether the rule classified correctly, which is "
                   "measured by the review samples on sheet 20.")
    rows = [
        {"Figure": "Tickets in the reporting window", "Value": n_win,
         "Basis": "Census", "Lower": "", "Upper": "", "Note": census_note},
        {"Figure": "Flagged as payment related", "Value": n_pay,
         "Basis": "Census, rule-based", "Lower": "", "Upper": "", "Note": census_note},
        {"Figure": "Payment share of selected window (%)", "Value": round(safe_div(n_pay, n_win), 1),
         "Basis": "Census, rule-based", "Lower": "", "Upper": "", "Note": census_note},
    ]
    if review is not None and len(review):
        adj = review[review["Measure"] == "Adjusted payment tickets in the window"]
        prc = review[review["Measure"] == "Precision of the payment rule"]
        if len(prc):
            r = prc.iloc[0]
            rows.append({"Figure": "Rule precision (%)", "Value": r["Estimate %"],
                         "Basis": f"Sample of {r['Reviewed']}, Wilson {CONFIDENCE_LEVEL:.0%}",
                         "Lower": r["Lower %"], "Upper": r["Upper %"],
                         "Note": "Share of flagged tickets a reviewer confirmed."})
        if len(adj):
            r = adj.iloc[0]
            rows.append({"Figure": "Adjusted payment tickets (estimate)", "Value": r["Estimate %"],
                         "Basis": "Derived from both review samples",
                         "Lower": r["Lower %"], "Upper": r["Upper %"],
                         "Note": "Flagged x precision, plus not-flagged x missed rate."})
    else:
        rows.append({
            "Figure": "Rule precision (%)", "Value": "", "Basis": "Not yet measured",
            "Lower": "", "Upper": "",
            "Note": "Review the selected-ticket sample to estimate precision; "
                    "this does not measure misses outside the input filter."})
        rows.append({
            "Figure": "Adjusted payment tickets (estimate)", "Value": "", "Basis": "Not yet measured",
            "Lower": "", "Upper": "",
            "Note": "An estimate for the full source requires reviewing excluded tickets."})
    return pd.DataFrame(rows)


# =============================================================================
# 12. STAKEHOLDER SUMMARY  (populated from the run above)
# =============================================================================

def write_summary(profile, quality, waterfall, metrics, tables, window, payment, meta,
                  df_clean, non_payment, sampling_tables, review):
    print("[8/8] Writing the stakeholder summary ...")

    def m(mid, default="n/a"):
        row = metrics.loc[metrics["ID"] == mid]
        return default if row.empty or pd.isna(row["Value"].iloc[0]) else row["Value"].iloc[0]

    n_win, n_pay = len(window), len(payment)
    dup = quality.loc[quality["Check"].str.startswith("Duplicate Ticket Number"), "Rows affected"]
    dup_rows = int(dup.iloc[0]) if len(dup) else 0
    odd_status = quality.loc[quality["Check"].str.startswith("Status value outside"), "Rows affected"]
    odd_status_rows = int(odd_status.iloc[0]) if len(odd_status) else 0
    raw_rows = int(waterfall["Rows in"].iloc[0])
    clean_rows = len(df_clean)
    n_non = len(non_payment)

    lines = []
    A = lines.append
    A("# Payment ticket analysis - stakeholder summary")
    A("")
    A(f"Source file: `{os.path.basename(EXCEL_FILE)}`, sheet `{SHEET_NAME}`")
    A("Scope: six-digit Ticket Number, populated Request Type, and payment terms "
      "in Request Sub-Type or Subject (Title). Results below refer to this subset.")
    A(f"Source rows before scope filter: {meta['source_rows']:,}; rows with populated Type "
      f"and Sub-Type before payment filter: {meta['prepayment_rows']:,}.")
    A("Because payment terms define the input scope, the payment-classification share "
      "below does not estimate the payment share of the full export. The non-payment "
      "review cannot detect payment tickets excluded at input.")
    A(f"Generated: {datetime.now():%d %b %Y %H:%M}")
    A(f"Reporting window: **{meta['label']}** ({WINDOW_MONTHS} months, anchored on `{WINDOW_ANCHOR}`)")
    A(f"Full extract covers: {meta['full_label']}")
    A("")
    A("## 1. What the data contains")
    A("")
    A(f"- **{raw_rows:,}** rows were read from the sheet; **{clean_rows:,}** "
      f"({fmt_pct(safe_div(clean_rows, raw_rows))}) are usable tickets after exclusions.")
    A(f"- **{dup_rows:,}** rows shared a Ticket Number with another row. "
      + ("These were deduplicated to one row per ticket, so the counts below are lower than "
         "a row-level count." if DEDUPE_BY_TICKET and dup_rows
         else "Deduplication is currently switched off." if dup_rows
         else "No duplicate ticket numbers were found."))
    A(f"- **{odd_status_rows:,}** rows carry a Status outside the five expected values. "
      "These rows are retained.")
    A(f"- **{n_win:,}** tickets fall inside the reporting window "
      f"({fmt_pct(safe_div(n_win, clean_rows))} of all usable tickets).")
    A("")
    A("### How the raw row count becomes the payment ticket count")
    A("")
    A("| Step | Rows in | Removed | Of those, look payment related | Rows out |")
    A("|---|---|---|---|---|")
    for _, row in waterfall.iterrows():
        looks = row["Of the removed, look payment related"]
        looks = "" if looks == "" or pd.isna(looks) else f"{int(looks):,}"
        A(f"| {row['Step']} | {int(row['Rows in']):,} | {int(row['Rows removed']):,} "
          f"| {looks} | {int(row['Rows out']):,} |")
    A("")
    A("Read this table before reacting to the payment ticket count. Most of the "
      "reduction is usually steps 2 and 6, and neither is a data loss: step 2 collapses "
      "repeat updates of the same ticket, and step 6 simply excludes tickets from "
      "outside the reporting window. The column to watch is **of those, look payment "
      "related** on step 3 - if it is large, the Request Sub-Type completeness rule is "
      "discarding genuine payment tickets and should be relaxed.")
    A("")
    A(f"Full column-by-column detail is in the workbook `{WORKBOOK_NAME}`, sheet "
      "**01 Column Profile**; the exclusion trail is on sheet **03 Row Waterfall**.")
    A("")
    A("## 2. Headline volumes")
    A("")
    A("| Measure | Value | Denominator |")
    A("|---|---|---|")
    A(f"| Tickets in window | {fmt_int(n_win)} | all usable tickets ({clean_rows:,}) |")
    A(f"| Average per month | {fmt_num(m('V2'))} | months in window |")
    A(f"| Payment-related tickets | {fmt_int(n_pay)} | tickets in window ({n_win:,}) |")
    A(f"| Payment classification within selected tickets | {fmt_pct(safe_div(n_pay, n_win))} | selected tickets in window |")
    A(f"| Payment tickets still open | {fmt_int(m('S1', None))} | payment tickets ({n_pay:,}) |")
    A("")
    cat_only = int(payment["Payment Evidence - Category"].sum()) if n_pay else 0
    A(f"Of the {n_pay:,} payment tickets, {cat_only:,} ({fmt_pct(safe_div(cat_only, n_pay))}) are "
      "identified by Request Type or Sub-Type. The remainder were matched on free text alone and "
      "should be sampled before the figure is quoted externally. **Requires data validation.**")
    A("")
    A("## 3. Could an agent act on these tickets?")
    A("")
    if n_pay:
        both = int((payment["Has PO"] & payment["Has Invoice"]).sum())
        neither = int((~payment["Has PO"] & ~payment["Has Invoice"]).sum())
        A(f"- A PO number was found in **{fmt_int(m('E1', None))}** payment tickets "
          f"({fmt_pct(safe_div(m('E1', 0), n_pay))}); an invoice reference in "
          f"**{fmt_int(m('E3', None))}** ({fmt_pct(safe_div(m('E3', 0), n_pay))}).")
        A(f"- **{both:,}** tickets contain both; **{neither:,}** "
          f"({fmt_pct(safe_div(neither, n_pay))}) contain neither, so an agent would have to go "
          "back to the requester before it could look anything up.")
        A("")
        A("Tier breakdown (sheet **13 Handling Tiers**):")
        A("")
        A("| Tier | Tickets | % of payment tickets |")
        A("|---|---|---|")
        for _, row in tables.get("13_handling_tiers", pd.DataFrame()).iterrows():
            A(f"| {row.iloc[0]} | {int(row['Tickets']):,} | {row.iloc[2]}% |")
        A("")
        A("These tiers are inferred from ticket text only. They indicate where to look first, "
          "not what an agent will achieve. **Requires data validation.**")
    else:
        A("No payment tickets were identified in the window. Check the window settings and the "
          "payment term lists at the top of the script.")
    A("")
    A("## 4. How long tickets take")
    A("")
    A(f"Median elapsed days from submission to last edit, closed payment tickets only: "
      f"**{fmt_num(m('R2'))}** days (n={fmt_int(m('R1', None))}). 90th percentile: {fmt_num(m('R3'))} days.")
    A("")
    A("`Last Edit Date` records the last change to the ticket, not a resolution event. For an open "
      "ticket it is simply the last time somebody touched it. Treat this as a rough elapsed-time "
      "proxy until a true closure timestamp is available. **Requires data validation.**")
    A("")
    A("## 5. How confident can you be in the payment count?")
    A("")
    A(f"The payment flag is applied to all {n_win:,} selected tickets in the window. "
      "A review sample checks whether flagged tickets are genuinely payment related. "
      "Tickets excluded at input are outside this sample:")
    A("")
    A("| Population | Size | Rows to review | Confidence | Margin of error |")
    A("|---|---|---|---|---|")
    for _, row in sampling_tables.get("20_sampling_plan", pd.DataFrame()).iterrows():
        A(f"| {row['Population']} | {int(row['Population size']):,} | "
          f"{int(row['Sample drawn by this run']):,} | {row['Confidence level']} | "
          f"{'n/a' if pd.isna(row['Margin of error at that sample (worst case)']) else '+/- ' + str(row['Margin of error at that sample (worst case)']) + '%'} |")
    A("")
    if review is not None and len(review):
        for _, row in review.iterrows():
            if row["Measure"] == "Adjusted payment tickets in the window":
                A(f"- **{row['Measure']}: {int(row['Estimate %']):,}** "
                  f"(range {int(row['Lower %']):,} to {int(row['Upper %']):,}).")
            else:
                A(f"- **{row['Measure']}: {row['Estimate %']}%** "
                  f"({CONFIDENCE_LEVEL:.0%} interval {row['Lower %']}% to {row['Upper %']}%, "
                  f"from {int(row['Reviewed']):,} rows reviewed).")
        A("")
        A("Intervals are Wilson score intervals, which stay valid at small samples and "
          "near 0% or 100% where the textbook normal interval does not.")
    else:
        A("Review the flagged-ticket sample in "
          f"`{REVIEW_FILES['precision']}` to estimate the rule's precision. "
          "To measure missed payment tickets, sample records excluded by the input filter "
          "separately; this report does not estimate that rate.")
        A("")
        A("Sheet 21 lists the review effort needed at other confidence levels - relaxing "
          "to 90% confidence and +/- 10% cuts the reading substantially.")
    A("")
    if n_non:
        A(f"Sheet **19 Non-Payment Mix** describes {n_non:,} selected tickets that "
          "did not match the downstream classifier.")
    A("")
    A("## 6. What this analysis cannot tell you")
    A("")
    A("- **Ownership and workload by team.** The verified schema has no assignee, queue or "
      "resolver-group column, so tickets cannot be attributed to a team.")
    A("- **Financial exposure.** There is no amount, currency, due date or payment-terms column. "
      "No value can be attached to the backlog.")
    A("- **System linkage.** `Ticket Number` is a FootPrints key. Nothing in this export joins to "
      "SAP S/4HANA or Ariba document numbers, supplier IDs or PO line items. Extracted PO and "
      "invoice strings are text found in a message, not validated keys.")
    A("- **Extraction accuracy.** The yields above count matches, not correct matches. "
      "`extraction_sample_for_manual_review.csv` is a blank review sheet for that check.")
    A("- **Whether a ticket was actually resolved correctly.** No outcome or resolution-code field exists.")
    A("")
    A("## 7. Suggested next step")
    A("")
    A("Review the payment-ticket sample to check keyword accuracy. To estimate "
      "the payment share of the full extract, also review tickets excluded by the "
      "initial filter; those tickets are outside this report.")
    A("")

    path = paths("stakeholder_summary.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path, "\n".join(lines)


# =============================================================================
# 12b. CONSOLIDATED EXPORT  (one workbook and/or one Markdown file)
# =============================================================================

def _is_pct_column(name):
    return "%" in str(name)


def export_workbook(meta, clean_rows):
    """One .xlsx: a Contents sheet, one sheet per summary table, and the charts."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    header_font = Font(name=WORKBOOK_FONT, bold=True, color="FFFFFF", size=10)
    header_fill = PatternFill("solid", fgColor="31506E")
    body_font = Font(name=WORKBOOK_FONT, size=10)
    title_font = Font(name=WORKBOOK_FONT, bold=True, size=13)
    thin = Side(style="thin", color="D9D9D9")
    border = Border(bottom=thin)

    # ---- Contents ----------------------------------------------------------
    ws = wb.active
    ws.title = "Contents"
    ws["A1"] = "Payment ticket analysis - summary tables"
    ws["A1"].font = title_font
    info = [
        ("Source file", os.path.basename(EXCEL_FILE)),
        ("Sheet", SHEET_NAME),
        ("Generated", f"{datetime.now():%d %b %Y %H:%M}"),
        ("Reporting window", meta["label"]),
        ("Full extract covers", meta["full_label"]),
        ("Usable tickets after exclusions", f"{clean_rows:,}"),
    ]
    row = 3
    for label, value in info:
        ws.cell(row=row, column=1, value=label).font = Font(name=WORKBOOK_FONT, bold=True, size=10)
        ws.cell(row=row, column=2, value=value).font = body_font
        row += 1

    row += 1
    for col, head in enumerate(["Sheet", "What it shows", "Rows"], start=1):
        cell = ws.cell(row=row, column=col, value=head)
        cell.font = header_font
        cell.fill = header_fill
    header_row = row
    row += 1
    for key, frame in TABLES.items():
        sheet_name = SHEET_NAMES.get(key, (key[:31], ""))[0]
        ws.cell(row=row, column=1, value=sheet_name).font = body_font
        ws.cell(row=row, column=2, value=TABLE_NOTES.get(key, "")).font = body_font
        ws.cell(row=row, column=3, value=len(frame)).font = body_font
        for col in range(1, 4):
            ws.cell(row=row, column=col).border = border
        row += 1

    row += 1
    note = ("Two row-level files are written separately as CSV, because they are meant to be "
            "filtered and filled in: ticket_audit_log.csv and "
            "extraction_sample_for_manual_review.csv.")
    ws.cell(row=row, column=1, value=note).font = Font(name=WORKBOOK_FONT, italic=True, size=9)
    row += 1
    ws.cell(row=row, column=1,
            value='Figures marked "Requires data validation" on sheet 04 Metrics need a human '
                  "check before they are quoted externally.").font = Font(
        name=WORKBOOK_FONT, italic=True, size=9)

    for col, width in [(1, 26), (2, 78), (3, 8)]:
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)

    # ---- one sheet per table ----------------------------------------------
    for key, frame in TABLES.items():
        sheet_name = SHEET_NAMES.get(key, (key[:31], ""))[0][:31]
        sheet = wb.create_sheet(sheet_name)
        for col, head in enumerate(frame.columns, start=1):
            cell = sheet.cell(row=1, column=col, value=str(head))
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for r, (_, record) in enumerate(frame.iterrows(), start=2):
            for c, head in enumerate(frame.columns, start=1):
                value = record[head]
                if isinstance(value, (np.integer,)):
                    value = int(value)
                elif isinstance(value, (np.floating,)):
                    value = None if pd.isna(value) else float(value)
                elif isinstance(value, (pd.Timestamp, datetime)):
                    value = value.to_pydatetime() if isinstance(value, pd.Timestamp) else value
                elif isinstance(value, (np.bool_, bool)):
                    value = bool(value)
                elif value is not None and not isinstance(value, (int, float, str)):
                    value = str(value)
                if isinstance(value, float) and pd.isna(value):
                    value = None
                cell = sheet.cell(row=r, column=c, value=value)
                cell.font = body_font
                cell.border = border
                if isinstance(value, (int, float)):
                    cell.number_format = "0.0" if _is_pct_column(head) else "#,##0"
                if isinstance(value, datetime):
                    cell.number_format = "yyyy-mm-dd"

        for c, head in enumerate(frame.columns, start=1):
            longest = max([len(str(head))] + [len(str(v)) for v in frame[head].head(300)])
            sheet.column_dimensions[get_column_letter(c)].width = min(max(longest + 2, 11), 62)
        sheet.row_dimensions[1].height = 30
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = (f"A1:{get_column_letter(max(len(frame.columns), 1))}"
                                 f"{max(len(frame) + 1, 2)}")

    # ---- charts ------------------------------------------------------------
    if EMBED_CHARTS_IN_WORKBOOK:
        try:
            from openpyxl.drawing.image import Image as XLImage
            sheet = wb.create_sheet("Charts")
            sheet["A1"] = "Dashboard charts"
            sheet["A1"].font = title_font
            sheet.column_dimensions["A"].width = 110
            anchor_row = 3
            for filename, caption in CHART_CAPTIONS:
                path = paths("charts", filename)
                if not os.path.exists(path):
                    continue
                cell = sheet.cell(row=anchor_row, column=1, value=caption)
                cell.font = Font(name=WORKBOOK_FONT, bold=True, size=11)
                img = XLImage(path)
                scale = min(1.0, 760 / float(img.width))
                img.width = int(img.width * scale)
                img.height = int(img.height * scale)
                sheet.add_image(img, f"A{anchor_row + 1}")
                anchor_row += int(img.height / 19) + 4
        except Exception as exc:
            print(f"      NOTE: charts not embedded ({type(exc).__name__}: {exc}). "
                  f"The PNG files are still in {paths('charts')}.")

    out = paths(WORKBOOK_NAME)
    wb.save(out)
    return out


def _md_table(frame, max_cell=150):
    """Render a DataFrame as a Markdown pipe table without extra dependencies."""
    if frame.empty:
        return "_No rows._\n"

    def cell(value, column=None):
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        if isinstance(value, float):
            text = f"{value:,.1f}" if (_is_pct_column(column) or value % 1) else f"{value:,.0f}"
        elif isinstance(value, (int, np.integer)):
            text = f"{int(value):,}"
        else:
            text = str(value)
        text = text.replace("|", "\\|").replace("\n", " ").strip()
        return text if len(text) <= max_cell else text[: max_cell - 1] + "\u2026"

    heads = [cell(h) for h in frame.columns]
    lines = ["| " + " | ".join(heads) + " |",
             "|" + "|".join(["---"] * len(heads)) + "|"]
    for _, record in frame.iterrows():
        lines.append("| " + " | ".join(cell(record[h], h) for h in frame.columns) + " |")
    return "\n".join(lines) + "\n"


def export_markdown(meta, clean_rows):
    """One .md containing every summary table, in the same order as the workbook."""
    lines = ["# Payment ticket analysis - summary tables", ""]
    lines += [f"Source file: `{os.path.basename(EXCEL_FILE)}`, sheet `{SHEET_NAME}`  ",
              f"Generated: {datetime.now():%d %b %Y %H:%M}  ",
              f"Reporting window: **{meta['label']}**  ",
              f"Full extract covers: {meta['full_label']}  ",
              f"Usable tickets after exclusions: **{clean_rows:,}**", ""]
    lines += ["Two row-level files are written separately as CSV, because they are meant to be "
              "filtered and filled in: `ticket_audit_log.csv` and "
              "`extraction_sample_for_manual_review.csv`.", "", "## Contents", ""]
    for key in TABLES:
        title = SHEET_NAMES.get(key, (key, ""))[0]
        anchor = title.lower().replace(" ", "-").replace(".", "")
        lines.append(f"- [{title}](#{anchor}) - {TABLE_NOTES.get(key, '')}")
    lines.append("")
    for key, frame in TABLES.items():
        title = SHEET_NAMES.get(key, (key, ""))[0]
        lines += [f"## {title}", "", f"_{TABLE_NOTES.get(key, '')}_", "",
                  _md_table(frame), ""]
    lines += ["## Charts", ""]
    for filename, caption in CHART_CAPTIONS:
        if os.path.exists(paths("charts", filename)):
            lines += [f"**{caption}**", "", f"![{caption}](charts/{filename})", ""]

    out = paths(MARKDOWN_NAME)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out


def export_tables(meta, clean_rows):
    written = []
    if TABLE_OUTPUT in ("xlsx", "both"):
        written.append(export_workbook(meta, clean_rows))
    if TABLE_OUTPUT in ("markdown", "both"):
        written.append(export_markdown(meta, clean_rows))
    if not written:
        print(f"      NOTE: TABLE_OUTPUT is '{TABLE_OUTPUT}'; no consolidated table file written.")
    return written


# =============================================================================
# 13. MAIN
# =============================================================================

def main():
    random.seed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)
    os.makedirs(paths("charts"), exist_ok=True)

    df_raw = load_raw()
    profile = profile_columns(df_raw)
    quality = quality_checks(df_raw)
    df_clean, steps, _ = build_clean(df_raw)
    clean_rows = len(df_clean)
    if df_clean.empty:
        print("\nNo usable rows survived the exclusion ladder. "
              "No tables were written; check the sheet name and the required columns.")
        return

    window, meta = apply_window(df_clean)
    meta["source_rows"] = df_raw.attrs.get("source_rows", len(df_raw))
    meta["prepayment_rows"] = df_raw.attrs.get("prepayment_rows", len(df_raw))
    if window.empty:
        print("\nThe reporting window contains no tickets. Check WINDOW_MONTHS / WINDOW_ANCHOR.")
        return

    window = classify_and_extract(window)
    payment = window[window["Is Payment Ticket"]].copy()
    non_payment = window[~window["Is Payment Ticket"]].copy()

    waterfall = extend_waterfall(steps, len(df_clean), len(window), len(payment),
                                 len(df_raw), meta)

    metrics, den_win, den_pay = build_metrics(window, payment)
    tables = build_dashboard(window, payment, non_payment, den_win, den_pay, meta)

    sampling_tables, _ = build_sampling(window, payment, non_payment)
    review = read_back_review(len(payment), len(non_payment))
    sampling_tables["22_estimates_and_intervals"] = build_estimates(
        window, payment, non_payment, review)
    if review is not None:
        sampling_tables["23_review_results"] = review
        print(f"      Review sheets found and read back: "
              f"{len(review)} measure(s) with {CONFIDENCE_LEVEL:.0%} intervals.")
    for name, frame in sampling_tables.items():
        write_table(frame, name)

    summary_path, summary_text = write_summary(
        profile, quality, waterfall, metrics, tables, window, payment, meta, df_clean,
        non_payment, sampling_tables, review)
    exported = export_tables(meta, clean_rows)

    print("\n" + "=" * 78)
    print(summary_text)
    print("=" * 78)
    for path in exported:
        print(f"\nTables  : {path}")
    print(f"Charts  : {paths('charts')}")
    print(f"Summary : {summary_path}")
    print(f"CSVs    : {paths('ticket_audit_log.csv')}")
    print(f"          {paths('extraction_sample_for_manual_review.csv')}")
    if len(non_payment):
        print(f"          {paths('non_payment_sample_for_manual_review.csv')}")
    print()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # surface a readable message rather than a bare traceback
        print(f"\nRUN FAILED: {type(exc).__name__}: {exc}\n", file=sys.stderr)
        raise
