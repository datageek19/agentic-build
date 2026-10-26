#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FootPrints payment-ticket analysis - corrected pipeline (v3)
===========================================================

Replaces eda_v1.py and eda_v2.py with one reconcilable run.

Why the two earlier versions disagreed, and what changed here:
  * v1 analysed the whole export; v2 analysed a 3-month trailing window.
    -> v3 reports BOTH, and prints the exact window bounds it used.
  * v1 silently dropped every ticket whose Status was outside a hard-coded
    list of five values; v2 kept all statuses.
    -> v3 keeps all statuses by default and REPORTS any status value that
       is not in the known list, instead of deleting the rows.
  * Neither version deduplicated by Ticket Number.
    -> v3 counts duplicates and deduplicates (configurable).
  * Both versions used substring matching ('po' matches "support",
    'pr' matches "approval"), which inflates the payment population.
    -> v3 uses word-boundary matching and separates structured evidence
       (Request Type / Sub-Type) from free-text evidence.
  * Both versions reported a single row-count, with no audit trail.
    -> v3 emits a row-count waterfall so every number can be traced.

Every figure this script prints comes from the local file. Nothing is
hard-coded. Items that cannot be settled by code alone are labelled
"Requires data validation".

Requires: pandas, numpy, openpyxl, matplotlib
    pip install pandas numpy openpyxl matplotlib
Run:
    python footprints_eda_v3.py
"""

import os
import re
import sys
import random
from datetime import datetime

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

OUTPUT_DIR = "eda_output"          # tables/ charts/ summary land here

WINDOW_MONTHS = 3                  # length of the reporting window
WINDOW_ANCHOR = "data_max"         # "data_max" | "today" | "YYYY-MM-DD"

DEDUPE_BY_TICKET = True            # keep one row per Ticket Number
DEDUPE_KEEP = "last_edited"        # "last_edited" | "first"

# v1 filtered rows down to these statuses. v3 does NOT filter on status; it
# uses this list only to flag values it has not seen before.
KNOWN_STATUS_VALUES = [
    "Closed", "Resolved", "Open", "Pending Supplier Response", "In Progress",
]
CLOSED_STATUS_VALUES = ["Closed", "Resolved"]

# Domains treated as non-supplier (verified values, carried over unchanged).
INTERNAL_DOMAINS = ["boeing.com", "ariba.com", "internal", "nan", "unknown.com"]

# Escalation language. 'late' and 'stop' from the earlier versions were
# removed: they match "related", "translate", "stopped by", "workshop".
URGENT_TERMS = [
    r"urgent(ly)?", r"escalat\w*", r"block(ed|ing)?", r"critical",
    r"delay(ed|s|ing)?", r"overdue", r"past\s+due", r"work\s+stoppage",
    r"line\s+down", r"shut\s*down",
]

RANDOM_SEED = 7
EXTRACTION_REVIEW_SAMPLE = 50      # rows exported for manual precision check
SHORT_TEXT_CHARS = 120             # threshold reused from v1/v2
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

REQUIRED_COLS = [COL_TICKET, COL_TYPE, COL_SUBTYPE, COL_STATUS, COL_SUBMITTED]
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

# PO number shapes carried over verbatim from the verified v1/v2 patterns.
_PO_CORE = r"(?:700\d{7}|701\d{7}|702\d{6,7}|703\d{6,7})"
RE_PO_CUED = re.compile(r"\bP\.?\s*O\.?\s*(?:#|no\.?|number)?\s*[:#\-]?\s*(" + _PO_CORE + r")\b", re.I)
RE_PO_BARE = re.compile(r"\b(" + _PO_CORE + r")\b")

# Invoice / payment-document prefixes carried over from v1/v2, but a digit
# payload is now required. The old pattern matched the bare English word
# "pay" and the bare token "IR", so its yield was not measuring invoice IDs.
RE_INV_STRICT = re.compile(r"\b(INV|IR|IV|PMT|PAY|CHS|FLY)\s*[#:\-]?\s*(\d{3,}[A-Z0-9\-_]*)\b", re.I)
RE_VENDOR_DOC = re.compile(r"\bV\d{3,}\b", re.I)
# Kept only so the report can show how much of the old yield was noise.
RE_INV_LEGACY = re.compile(r"\b(INV|IR|PAY|PMT|CHS|IV|FLY|V\d{3,})\b\s*[#\s:]*\d*[-_\w]*", re.I)

# Payment evidence. Word boundaries replace the v1/v2 substring match.
RE_PAY_STRUCTURED = re.compile(r"\b(invoice|payment|pay|po|pr|p-?card|card|remittance)\b", re.I)
RE_PAY_TEXT = re.compile(
    r"\b(invoices?|invoiced|payments?|paid|unpaid|remittance|remit|"
    r"purchase\s+order|p\.?\s?o\.?|pr|p-?card|wire\s+transfer|wired?|"
    r"past\s+due|overdue)\b", re.I)

INTENT_RULES = [
    ("Reject Request",        r"\breject(ed|ion|ing|s)?\b"),
    ("Cancellation Request",  r"\b(cancel(l?ed|l?ation|l?ing|s)?|void(ed)?|delete[ds]?)\b"),
    ("Expedite Request",      r"\b(expedite[ds]?|expediting|urgent(ly)?|rush)\b"),
    ("Payment Status Query",  r"\b(status|unpaid|not\s+(yet\s+)?paid|past\s+due|overdue|"
                              r"still\s+waiting|follow[\s-]?up)\b"),
]
INTENT_DEFAULT = "Unclassified / General Support"

RE_URGENT = re.compile(r"\b(" + "|".join(URGENT_TERMS) + r")\b", re.I)
RE_DOMAIN = re.compile(r"@([\w.\-]+)")

STOP_WORDS = {
    # grammatical
    "the", "and", "to", "of", "for", "in", "a", "is", "it", "you", "this",
    "that", "with", "on", "from", "we", "have", "has", "had", "be", "are",
    "was", "were", "as", "at", "i", "our", "us", "your", "can", "could",
    "or", "an", "but", "not", "any", "all", "if", "will", "would", "been",
    "they", "their", "there", "which", "when", "what", "who", "how", "by",
    "do", "does", "did", "so", "its", "his", "her", "them", "then",
    # email boilerplate and client-specific noise (from v1; v2 omitted these,
    # which is why v2's keyword list was dominated by greetings)
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


def write_table(frame, name, index=False):
    out = paths("tables", f"{name}.csv")
    frame.to_csv(out, index=index, encoding="utf-8-sig")
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

    print(f"      {len(df):,} raw rows, {len(df.columns)} columns.")
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
        "count in v1 and v2 is inflated.",
        "Deduplicated." if DEDUPE_BY_TICKET else "Kept (DEDUPE_BY_TICKET is off).")

    for col, label in [(COL_TYPE, "Request Type"), (COL_SUBTYPE, "Request Sub-Type")]:
        if col in df_raw.columns:
            add(f"{label} blank or placeholder", int(is_blank(df_raw[col]).sum()),
                "Ticket cannot be categorised; v1 and v2 both dropped these rows.",
                "Row excluded, counted in the waterfall.")

    if COL_STATUS in df_raw.columns:
        status = norm_text(df_raw[COL_STATUS])
        add("Status blank or placeholder", int(status.isna().sum()),
            "Open vs closed cannot be determined.", "Row kept; reported as '(blank)'.")
        unexpected = sorted(set(status.dropna().unique()) - set(KNOWN_STATUS_VALUES))
        n_unexpected = int(status.isin(unexpected).sum()) if unexpected else 0
        add("Status value outside the five known values"
            + (f" ({', '.join(map(str, unexpected[:6]))}{' ...' if len(unexpected) > 6 else ''})" if unexpected else ""),
            n_unexpected,
            "v1 deleted these rows; v2 kept them. This is a direct cause of the "
            "two versions disagreeing.",
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
            "Negative elapsed time indicates a timestamp or export problem. "
            "v1 and v2 hid this by clipping to zero.",
            "Excluded from duration metrics and reported here.")

    if COL_DETAIL in df_raw.columns:
        detail = norm_text(df_raw[COL_DETAIL])
        add("Request Detail blank", int(detail.isna().sum()),
            "No free text for an agent to read. v1 and v2 scored these as "
            "short text and classed them as automation candidates.",
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

def build_clean(df_raw):
    print("[4/8] Applying the exclusion ladder ...")
    steps = []
    df = df_raw.copy()
    start = len(df)

    def record(step, before, after, rule):
        steps.append({
            "Step": step,
            "Rows in": before,
            "Rows removed": before - after,
            "Rows out": after,
            "% of raw retained": round(safe_div(after, start), 1),
            "Rule": rule,
        })

    for col in [c for c in TEXT_COLS if c in df.columns]:
        df[col] = norm_text(df[col])
    for col in [c for c in DATE_COLS if c in df.columns]:
        df[col] = pd.to_datetime(df[col], errors="coerce")

    record("0. Raw rows read from the sheet", start, start, "No filter.")

    before = len(df)
    df = df[df[COL_TICKET].notna()]
    record("1. Drop rows with no Ticket Number", before, len(df),
           "Ticket Number is blank or a placeholder.")

    if DEDUPE_BY_TICKET:
        before = len(df)
        if DEDUPE_KEEP == "last_edited" and COL_EDITED in df.columns:
            df = (df.sort_values([COL_TICKET, COL_EDITED], na_position="first")
                    .drop_duplicates(subset=[COL_TICKET], keep="last"))
        else:
            df = df.drop_duplicates(subset=[COL_TICKET], keep="first")
        record("2. Deduplicate to one row per ticket", before, len(df),
               f"Keep the {'most recently edited' if DEDUPE_KEEP == 'last_edited' else 'first'} row per Ticket Number.")
    else:
        record("2. Deduplicate to one row per ticket", len(df), len(df),
               "SKIPPED - DEDUPE_BY_TICKET is False.")

    before = len(df)
    df = df[df[COL_TYPE].notna() & df[COL_SUBTYPE].notna()]
    record("3. Drop rows with no Request Type or Sub-Type", before, len(df),
           "Same rule as v1 and v2: ticket cannot be categorised.")

    before = len(df)
    df = df[df[COL_SUBMITTED].notna()]
    record("4. Drop rows with no usable Date Submitted", before, len(df),
           "Ticket cannot be placed in a reporting period.")

    record("5. Status filter", len(df), len(df),
           "NOT APPLIED. v1 removed every status outside five hard-coded "
           "values here; see check 02 for how many rows that was.")

    df = df.sort_values(COL_SUBMITTED).reset_index(drop=True)
    df["Submission Month"] = df[COL_SUBMITTED].dt.to_period("M").astype(str)

    waterfall = pd.DataFrame(steps)
    write_table(waterfall, "03_row_count_waterfall")
    return df, waterfall


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
    out["Invoice Reference (v1/v2 pattern)"] = combined.map(
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
    add("V4", "Payment share of all tickets", round(safe_div(n_pay, n_win), 1),
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
            "Yield only, not accuracy. Review tables/20 sample - Requires data validation")
        add("E2", "PO numbers explicitly labelled 'PO' in the text",
            int((payment["PO Evidence"] == "Labelled 'PO' in the text").sum()),
            "Subset of E1 where the number is preceded by a PO cue.",
            "payment tickets with a PO number", int(payment["Has PO"].sum()), "Payment tickets")
        add("E3", "Payment tickets with an invoice reference", int(payment["Has Invoice"].sum()),
            "Prefix (INV/IR/IV/PMT/PAY/CHS/FLY) followed by at least 3 digits, or a V-number.",
            den_pay["label"], n_pay, "Payment tickets",
            "Yield only, not accuracy - Requires data validation")
        add("E4", "Invoice references counted by the v1/v2 pattern",
            int(payment["Invoice Reference (v1/v2 pattern)"].notna().sum()),
            "The old pattern, retained for comparison. It matched the bare English word 'pay' and the bare token 'IR'.",
            den_pay["label"], n_pay, "Payment tickets",
            "Shown only to quantify the overcount in the earlier versions.")
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

def build_dashboard(window, payment, den_win, den_pay, meta):
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
        tables["19_ticket_audit_log"] = payment[audit_cols]
        sample_n = min(EXTRACTION_REVIEW_SAMPLE, len(payment))
        tables["20_extraction_sample_for_manual_review"] = (
            payment[audit_cols].sample(n=sample_n, random_state=RANDOM_SEED)
            .assign(**{"Reviewer: is the payment classification correct? (Y/N)": "",
                       "Reviewer: is the PO/invoice correct? (Y/N)": "",
                       "Reviewer: is the intent correct? (Y/N)": ""}))

    for name, frame in tables.items():
        write_table(frame, name)
    return tables


# =============================================================================
# 12. STAKEHOLDER SUMMARY  (populated from the run above)
# =============================================================================

def write_summary(profile, quality, waterfall, metrics, tables, window, payment, meta, df_clean):
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
    clean_rows = int(waterfall["Rows out"].iloc[-1])

    lines = []
    A = lines.append
    A("# Payment ticket analysis - stakeholder summary")
    A("")
    A(f"Source file: `{os.path.basename(EXCEL_FILE)}`, sheet `{SHEET_NAME}`")
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
         "the earlier scripts produced." if DEDUPE_BY_TICKET and dup_rows
         else "Deduplication is currently switched off." if dup_rows
         else "No duplicate ticket numbers were found."))
    A(f"- **{odd_status_rows:,}** rows carry a Status outside the five expected values. "
      "These are retained here; the earlier version 1 script deleted them.")
    A(f"- **{n_win:,}** tickets fall inside the reporting window "
      f"({fmt_pct(safe_div(n_win, clean_rows))} of all usable tickets).")
    A("")
    A("Full column-by-column detail is in `tables/01_column_profile.csv`; "
      "the exclusion trail is in `tables/03_row_count_waterfall.csv`.")
    A("")
    A("## 2. Headline volumes")
    A("")
    A("| Measure | Value | Denominator |")
    A("|---|---|---|")
    A(f"| Tickets in window | {fmt_int(n_win)} | all usable tickets ({clean_rows:,}) |")
    A(f"| Average per month | {fmt_num(m('V2'))} | months in window |")
    A(f"| Payment-related tickets | {fmt_int(n_pay)} | tickets in window ({n_win:,}) |")
    A(f"| Payment share | {fmt_pct(safe_div(n_pay, n_win))} | tickets in window |")
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
        A(f"- For comparison, the invoice pattern used by the earlier scripts matched "
          f"**{fmt_int(m('E4', None))}** tickets. The two counts differ because the old pattern "
          "matched the ordinary English word \"pay\" and the bare token \"IR\", while also missing "
          "run-together references such as INV889900. Neither count is an accuracy measure.")
        A("")
        A("Tier breakdown (rules in `tables/13_handling_tiers.csv`):")
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
    A("## 5. What this analysis cannot tell you")
    A("")
    A("- **Ownership and workload by team.** The verified schema has no assignee, queue or "
      "resolver-group column, so tickets cannot be attributed to a team.")
    A("- **Financial exposure.** There is no amount, currency, due date or payment-terms column. "
      "No value can be attached to the backlog.")
    A("- **System linkage.** `Ticket Number` is a FootPrints key. Nothing in this export joins to "
      "SAP S/4HANA or Ariba document numbers, supplier IDs or PO line items. Extracted PO and "
      "invoice strings are text found in a message, not validated keys.")
    A("- **Extraction accuracy.** The yields above count matches, not correct matches. "
      "`tables/20_extraction_sample_for_manual_review.csv` is a blank review sheet for that check.")
    A("- **Whether a ticket was actually resolved correctly.** No outcome or resolution-code field exists.")
    A("")
    A("## 6. Suggested next step")
    A("")
    A("Have one subject-matter expert complete the 50-row review sheet. That single pass converts "
      "the classification and extraction yields above from \"matched\" to \"matched correctly\", "
      "and is the shortest path to a defensible automation scope.")
    A("")

    path = paths("stakeholder_summary.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path, "\n".join(lines)


# =============================================================================
# 13. MAIN
# =============================================================================

def main():
    random.seed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)
    for sub in ("tables", "charts"):
        os.makedirs(paths(sub), exist_ok=True)

    df_raw = load_raw()
    profile = profile_columns(df_raw)
    quality = quality_checks(df_raw)
    df_clean, waterfall = build_clean(df_raw)
    if df_clean.empty:
        print("\nNo usable rows survived the exclusion ladder. "
              "Review tables/03_row_count_waterfall.csv.")
        return

    window, meta = apply_window(df_clean)
    if window.empty:
        print("\nThe reporting window contains no tickets. Check WINDOW_MONTHS / WINDOW_ANCHOR.")
        return

    window = classify_and_extract(window)
    payment = window[window["Is Payment Ticket"]].copy()
    metrics, den_win, den_pay = build_metrics(window, payment)
    tables = build_dashboard(window, payment, den_win, den_pay, meta)
    summary_path, summary_text = write_summary(
        profile, quality, waterfall, metrics, tables, window, payment, meta, df_clean)

    print("\n" + "=" * 78)
    print(summary_text)
    print("=" * 78)
    print(f"\nTables : {paths('tables')}")
    print(f"Charts : {paths('charts')}")
    print(f"Summary: {summary_path}\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # surface a readable message rather than a bare traceback
        print(f"\nRUN FAILED: {type(exc).__name__}: {exc}\n", file=sys.stderr)
        raise
