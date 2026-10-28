#!/usr/bin/env python3
"""Profile FootPrints tickets and summarize payment-related requests.

Install: pip install pandas openpyxl matplotlib
Run: python footprints_payment_eda.py
The input workbook stays on the local machine. No record counts are hard-coded.
"""

from pathlib import Path
import re
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

INPUT_FILE = Path(r"C:\Users\qw597f\footprint_data_eda\Invoice FP Tickets Jan thru Oct.xlsx")
SHEET_NAME = "report-1"
OUTPUT_DIR = Path("footprints_payment_eda_output")
EXPECTED_ELIGIBLE_ROWS = 924  # Manual checkpoint; warning only, never a filter.

TICKET = "Ticket Number"
TYPE = "Request Type"
SUBTYPE = "Request Sub-Type"
TITLE = "Subject (Title)"
DETAIL = "Request Detail"
STATUS = "Status"
SUBMITTED = "Date Submitted"
EDITED = "Last Edit Date"

BLANKS = {"", "nan", "none", "null", "n/a", "na", "-", "--"}
# Match whole payment terms across category labels and ticket text.
CATEGORY_PAYMENT = re.compile(
    r"\b(?:payment|payments|paid|unpaid|invoice|invoices|invoicing|"
    r"remittance|remit|po|pr|purchase\s+order|p[ -]?card|wire\s+transfer)\b", re.I)
TEXT_PAYMENT = re.compile(
    r"\b(?:payment|payments|paid|unpaid|invoice|invoices|invoicing|"
    r"remittance|remit|purchase\s+order|po|pr|p\.?\s*o\.?\s*(?:#|no\.?|number|:)?\s*\d{5,}|"
    r"p[ -]?card|wire\s+transfer|past\s+due|overdue)\b", re.I)


def clean_text(series):
    value = series.astype("string").str.strip()
    return value.mask(value.str.lower().isin(BLANKS))


def pct(count, total):
    return round(100 * count / total, 1) if total else 0.0


def save_table(writer, name, frame):
    frame.to_excel(writer, sheet_name=name, index=False)


def load_and_scope():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_FILE}")
    raw = pd.read_excel(INPUT_FILE, sheet_name=SHEET_NAME, engine="openpyxl")
    raw.columns = [str(name).strip() for name in raw.columns]
    needed = [TICKET, TYPE, SUBTYPE, TITLE, DETAIL]
    missing = [name for name in needed if name not in raw.columns]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")

    ticket = clean_text(raw[TICKET]).str.replace(r"\.0$", "", regex=True)
    six_digits = ticket.str.fullmatch(r"\d{6}", na=False)
    valid_type = clean_text(raw[TYPE]).notna()
    valid_subtype = clean_text(raw[SUBTYPE]).notna()
    stage1 = raw.loc[six_digits].copy()
    stage2 = raw.loc[six_digits & valid_type].copy()
    eligible = raw.loc[six_digits & valid_type & valid_subtype].copy()
    eligible[TICKET] = ticket.loc[eligible.index]

    flow = pd.DataFrame([
        ("Input rows", len(raw), 0),
        ("Six-digit Ticket Number", len(stage1), len(raw) - len(stage1)),
        ("Request Type populated", len(stage2), len(stage1) - len(stage2)),
        ("Request Sub-Type populated", len(eligible), len(stage2) - len(eligible)),
    ], columns=["Step", "Rows retained", "Rows removed at step"])
    if len(eligible) != EXPECTED_ELIGIBLE_ROWS:
        print(f"CHECK: {len(eligible):,} eligible rows; manual checkpoint is "
              f"{EXPECTED_ELIGIBLE_ROWS:,}. Confirm the same workbook and sheet were used.")
    return raw, eligible, flow


def profile_columns(frame):
    rows = []
    for name in frame.columns:
        value = clean_text(frame[name])
        filled = value.dropna()
        rows.append({
            "Column": name,
            "Populated": len(filled),
            "Completeness %": pct(len(filled), len(frame)),
            "Blank": len(frame) - len(filled),
            "Distinct nonblank values": filled.nunique(),
        })
