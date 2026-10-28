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
    return pd.DataFrame(rows)


def classify(eligible):
    out = eligible.copy()
    for name in [TYPE, SUBTYPE, TITLE, DETAIL]:
        out[name] = clean_text(out[name])
    category = (out[TYPE].fillna("") + " " + out[SUBTYPE].fillna(""))
    free_text = (out[TITLE].fillna("") + " " + out[DETAIL].fillna(""))
    out["Category payment match"] = category.str.contains(CATEGORY_PAYMENT, na=False)
    out["Text payment match"] = free_text.str.contains(TEXT_PAYMENT, na=False)
    out["Payment-related"] = out["Category payment match"] | out["Text payment match"]
    out["Payment evidence"] = "No keyword match"
    out.loc[out["Category payment match"], "Payment evidence"] = "Category"
    out.loc[out["Text payment match"], "Payment evidence"] = "Title or detail"
    out.loc[out["Category payment match"] & out["Text payment match"], "Payment evidence"] = "Category and text"
    if SUBMITTED in out:
        out[SUBMITTED] = pd.to_datetime(out[SUBMITTED], errors="coerce")
        out["Submitted month"] = out[SUBMITTED].dt.strftime("%Y-%m").fillna("Date missing")
    if EDITED in out:
        out[EDITED] = pd.to_datetime(out[EDITED], errors="coerce")
    return out


def counts(frame, column, base, top=None):
    values = clean_text(frame[column]).fillna("(blank)")
    result = values.value_counts(dropna=False).rename_axis(column).reset_index(name="Tickets")
    result["% of selected tickets"] = result["Tickets"].map(lambda n: pct(n, base))
    return result.head(top) if top else result


def charts(tickets, payment):
    chart_dir = OUTPUT_DIR / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(7, 4))
    labels = ["Payment-related", "Other eligible tickets"]
    values = [len(payment), len(tickets) - len(payment)]
    ax.bar(labels, values, color=["#176B8A", "#9CA8B3"])
    for i, value in enumerate(values):
        ax.text(i, value, f"{value:,} ({pct(value, len(tickets)):.1f}%)", ha="center", va="bottom")
    ax.set(title="Tickets in the eligible sample", ylabel="Tickets")
    ax.set_ylim(0, max(values + [1]) * 1.15)
    fig.tight_layout()
    fig.savefig(chart_dir / "01_payment_share.png", dpi=160)
    plt.close(fig)

    if SUBMITTED in tickets and tickets[SUBMITTED].notna().any():
        trend = (tickets.loc[tickets[SUBMITTED].notna()]
                 .groupby("Submitted month")["Payment-related"].agg(["sum", "count"]).sort_index())
        if not trend.empty:
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.bar(trend.index, trend["sum"], label="Payment-related", color="#176B8A")
            ax.bar(trend.index, trend["count"] - trend["sum"], bottom=trend["sum"],
                   label="Other eligible tickets", color="#9CA8B3")
            ax.set(title="Tickets by submission month", ylabel="Tickets", xlabel="Month")
            ax.legend()
            fig.tight_layout()
            fig.savefig(chart_dir / "02_monthly_tickets.png", dpi=160)
            plt.close(fig)

    if len(payment) and STATUS in payment:
        top = counts(payment, STATUS, len(payment), top=8).iloc[::-1]
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.barh(top[STATUS], top["Tickets"], color="#176B8A")
        ax.set(title="Payment-related tickets by status", xlabel="Tickets")
        fig.tight_layout()
        fig.savefig(chart_dir / "03_payment_status.png", dpi=160)
        plt.close(fig)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    raw, eligible, flow = load_and_scope()
    if eligible.empty:
        raise ValueError("No rows passed the Ticket Number and Request Type/Sub-Type checks.")
    tickets = classify(eligible)
    payment = tickets.loc[tickets["Payment-related"]].copy()
    duplicate_rows = int(tickets[TICKET].duplicated(keep=False).sum())
    unique_tickets = tickets[TICKET].nunique()

    metrics = pd.DataFrame([
        ("Input rows", len(raw), "All rows in the source sheet"),
        ("Eligible records", len(tickets), "Six-digit ID and populated Type and Sub-Type"),
        ("Unique eligible ticket numbers", unique_tickets, "Duplicates are retained in record counts"),
        ("Records with repeated ticket numbers", duplicate_rows, "Check whether export contains multiple updates"),
        ("Payment-related records", len(payment), "Keyword match in Type, Sub-Type, Title, or Detail"),
        ("Payment-related share of eligible records (%)", pct(len(payment), len(tickets)),
         "Denominator: eligible records; keyword matches require review"),
        ("Payment-related records with blank title", int(clean_text(payment[TITLE]).isna().sum()),
         "Denominator: payment-related records"),
        ("Payment-related records with blank detail", int(clean_text(payment[DETAIL]).isna().sum()),
         "Denominator: payment-related records"),
    ], columns=["Measure", "Value", "Definition"])

    profile = profile_columns(eligible)
    evidence = counts(payment, "Payment evidence", len(payment)) if len(payment) else pd.DataFrame()
    with pd.ExcelWriter(OUTPUT_DIR / "footprints_payment_summary.xlsx", engine="openpyxl") as writer:
        save_table(writer, "Overview", metrics)
        save_table(writer, "Filtering", flow)
        save_table(writer, "Column completeness", profile)
        if len(payment):
            save_table(writer, "Payment evidence", evidence)
            for field, sheet in [(TYPE, "Payment request type"),
                                 (SUBTYPE, "Payment subtype"),
                                 (STATUS, "Payment status")]:
                if field in payment:
                    save_table(writer, sheet, counts(payment, field, len(payment), top=15))
        if STATUS in tickets:
            save_table(writer, "All eligible status", counts(tickets, STATUS, len(tickets)))
        if SUBMITTED in tickets:
            monthly = (tickets.groupby("Submitted month", dropna=False)["Payment-related"]
                       .agg(Payment_related="sum", Eligible_records="count").reset_index())
            monthly["Payment share %"] = monthly.apply(
                lambda row: pct(row["Payment_related"], row["Eligible_records"]), axis=1)
            save_table(writer, "Monthly trend", monthly)

    charts(tickets, payment)
    review_cols = [TICKET, TYPE, SUBTYPE, TITLE, DETAIL, "Payment evidence"]
    review = tickets.loc[:, review_cols].copy()
    review["Rule flag"] = tickets["Payment-related"].map({True: "Payment-related", False: "Other"})
    review["Reviewer: payment-related? (Y-N)"] = ""
    review.to_csv(OUTPUT_DIR / "classification_review.csv", index=False)

    summary = [
        "# FootPrints ticket overview", "",
        f"- Source rows: **{len(raw):,}**; eligible records: **{len(tickets):,}**.",
        f"- Payment-related keyword matches: **{len(payment):,}** "
        f"({pct(len(payment), len(tickets)):.1f}% of eligible records).",
        f"- Unique eligible ticket numbers: **{unique_tickets:,}**. "
        f"Records with a repeated Ticket Number: **{duplicate_rows:,}**.",
        "", f"The {EXPECTED_ELIGIBLE_ROWS:,}-record checkpoint is a validation aid; all figures above are calculated "
        "from the selected workbook. The payment flag is a keyword screen, not a confirmed "
        "business classification. Review flagged and unflagged records before using the share "
        "to size the payment workflow.", "",
        "Date Submitted to Last Edit is not a resolution-time measure. This analysis does not "
        "infer automation success or SAP integration readiness from ticket text.",
    ]
    (OUTPUT_DIR / "business_summary.md").write_text("\n".join(summary), encoding="utf-8")
    print(f"Source: {len(raw):,} rows; eligible: {len(tickets):,}; "
          f"payment-related: {len(payment):,} ({pct(len(payment), len(tickets)):.1f}%).")
    print(f"Results: {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
