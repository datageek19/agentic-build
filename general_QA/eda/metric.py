"""FootPrints snapshot metrics. No network calls or automatic business decisions."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

TEXT_FIELDS = [
    "Ticket Number", "Status", "Commodity Group", "Request Type",
    "Request Sub-Type", "Subject (Title)", "Request Detail", "Email Address",
]
ACTIVE = {"Open", "In Progress", "Pending Supplier Response"}
COMPLETE = {"Closed", "Resolved"}
STATUS_MAP = {x.casefold(): x for x in ACTIVE | COMPLETE}
NULL_WORDS = {"", "nan", "none", "null", "nat", "n/a", "na"}
PAYMENT_RE = re.compile(
    r"\b(?:invoices?|payments?|paid|unpaid|remittance|purchase\s+orders?|"
    r"purchase\s+requisitions?|po|pr|p[- ]card|wire|credit\s+card|corporate\s+card)\b", re.I
)
URGENCY_RE = re.compile(
    r"\b(?:urgent|urgently|escalat\w*|blocked|blocking|critical|overdue|past\s+due|"
    r"delayed|delay|late)\b", re.I
)
PO_EXPLICIT = re.compile(
    r"\b(?:po|purchase\s+order)(?:\s*(?:number|no\.?))?\s*[#:\-]?\s*(\d{6,14})\b", re.I
)
PO_UNLABELLED = re.compile(r"\b(70[01]\d{7}|70[23]\d{6,7})\b")
INVOICE_EXPLICIT = re.compile(
    r"\binvoice(?:\s*(?:number|no\.?))?\s*[#:]?\s*([A-Z0-9][A-Z0-9/_-]{2,39})\b", re.I
)
INVOICE_PREFIXED = re.compile(r"\b((?:INV|IR|PAY|PMT|CHS|IV|FLY)[-_]?\d[A-Z0-9/_-]{1,38})\b", re.I)


def clean_text(s: pd.Series) -> pd.Series:
    out = s.astype("string").str.strip()
    return out.mask(out.str.casefold().isin(NULL_WORDS))


def dates(s: pd.Series) -> pd.Series:
    # Excel numeric serials and timestamp strings are handled separately.
    # Unparseable dates remain missing; ambiguous text uses month/day ordering.
    def one(value):
        if pd.isna(value):
            return pd.NaT
        if isinstance(value, (int, float, np.integer, np.floating)):
            if 1 <= value <= 100000:
                return pd.to_datetime(value, unit="D", origin="1899-12-30", errors="coerce")
            return pd.NaT
        value = pd.to_datetime(value, errors="coerce")
        if pd.isna(value):
            return pd.NaT
        # Preserve source wall time; exports should use one business timezone.
        return value.tz_localize(None) if value.tzinfo else value
    return pd.to_datetime(s.map(one), errors="coerce")


def reference_candidates(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    pos = set(PO_EXPLICIT.findall(text)) | set(PO_UNLABELLED.findall(text))
    invoices = set(INVOICE_PREFIXED.findall(text))
    for candidate in INVOICE_EXPLICIT.findall(text):
        if any(c.isdigit() for c in candidate) and candidate not in pos:
            invoices.add(candidate)
    return tuple(sorted(pos)), tuple(sorted(x.upper() for x in invoices))


def tentative_theme(text: str) -> str:
    rules = [
        (r"\b(?:cancel\w*|void|delete)\b", "Cancellation / void request"),
        (r"\b(?:reject\w*)\b", "Rejection mentioned"),
        (r"\b(?:expedite|urgent|urgently)\b", "Expedite request"),
        (r"\b(?:status|unpaid|past\s+due|overdue|late|payment\s+update)\b", "Payment / invoice status"),
    ]
    for pattern, label in rules:
        if re.search(pattern, text, re.I):
            return label
    return "Other / needs classification"


def prepare(raw: pd.DataFrame, completion_column: str | None = None):
    """Return latest unique ticket rows, quality counts, rejected rows, duplicate audit.

    Repeated IDs are collapsed to the latest valid Last Edit Date, then submission
    date, then source row. This is a snapshot selection rule, not a history model.
    """
    d = raw.copy()
    d.columns = pd.Index([str(x).strip() for x in d.columns])
    if d.columns.duplicated().any():
        raise ValueError("Duplicate column names after trimming. Rename them in the source export.")
    if "Ticket Number" not in d:
        raise ValueError("The export must contain a 'Ticket Number' column.")
    source_cols = list(d.columns)
    missing_columns = [x for x in TEXT_FIELDS + ["Date Submitted", "Last Edit Date"] if x not in d]
    d["source_excel_row"] = np.arange(2, len(d) + 2)
    for col in TEXT_FIELDS:
        if col not in d:
            d[col] = pd.NA
        d[col] = clean_text(d[col])
    # Numeric spreadsheet IDs such as 123.0 are normalized without stripping
    # leading zeros from string IDs. Ticket IDs should be exported as text.
    d["Ticket Number"] = d["Ticket Number"].str.replace(r"^(\d+)\.0$", r"\1", regex=True)
    for col in ["Date Submitted", "Last Edit Date"]:
        d[col] = dates(d[col]) if col in d else pd.NaT
    if completion_column is not None:
        if completion_column not in d:
            raise ValueError(f"Completion column not found: {completion_column}")
        d["confirmed_completion_date"] = dates(raw.rename(columns=lambda x: str(x).strip())[completion_column])
    else:
        d["confirmed_completion_date"] = pd.NaT
    rejected = d.loc[d["Ticket Number"].isna()].copy()
    identified = d.loc[d["Ticket Number"].notna()].copy()
    repeated = identified["Ticket Number"].duplicated(keep=False)
    duplicate_audit = identified.loc[repeated].copy()
    conflicts = identified.groupby("Ticket Number")["Date Submitted"].nunique().gt(1)
    d = identified.sort_values(
        ["Last Edit Date", "Date Submitted", "source_excel_row"], na_position="first", kind="stable"
    ).drop_duplicates("Ticket Number", keep="last").copy()
    if not duplicate_audit.empty:
        duplicate_audit["selected_for_snapshot"] = duplicate_audit["source_excel_row"].isin(d["source_excel_row"])
    d["submission_date_conflict"] = d["Ticket Number"].map(conflicts).fillna(False)
    d["Status"] = d["Status"].fillna("Unknown / missing").map(
        lambda x: STATUS_MAP.get(x.casefold(), x)
    )
    for col in ["Commodity Group", "Request Type", "Request Sub-Type"]:
        d[col] = d[col].fillna("Unknown / missing")
    d["status_group"] = np.select(
        [d["Status"].isin(ACTIVE), d["Status"].isin(COMPLETE)],
        ["Active", "Closed / resolved"], default="Other / unknown"
    )
    d["is_active"] = d["status_group"].eq("Active")
    d["is_complete"] = d["status_group"].eq("Closed / resolved")
    d["waiting_supplier"] = d["Status"].eq("Pending Supplier Response")
    d["invalid_update_order"] = d["Last Edit Date"].lt(d["Date Submitted"])
    text = (d["Subject (Title)"].fillna("") + " " + d["Request Detail"].fillna(""))
    scope_text = text + " " + d["Request Type"] + " " + d["Request Sub-Type"]
    d["payment_keyword_match"] = scope_text.str.contains(PAYMENT_RE, na=False)
    d["urgency_wording"] = text.str.contains(URGENCY_RE, na=False)
    d["tentative_theme"] = text.map(tentative_theme)
    refs = text.map(reference_candidates)
    d["po_candidates"] = refs.map(lambda x: x[0])
    d["invoice_candidates"] = refs.map(lambda x: x[1])
    d["has_po_candidate"] = d["po_candidates"].map(bool).astype(bool)
    d["has_invoice_candidate"] = d["invoice_candidates"].map(bool).astype(bool)
    d["has_reference_candidate"] = d["has_po_candidate"] | d["has_invoice_candidate"]
    d["reference_coverage"] = np.select(
        [d["has_po_candidate"] & d["has_invoice_candidate"], d["has_po_candidate"], d["has_invoice_candidate"]],
        ["PO and invoice candidate", "PO candidate only", "Invoice candidate only"], default="No reference detected"
    )
    d["requester_domain"] = d["Email Address"].str.lower().str.extract(
        r"@([a-z0-9.-]+\.[a-z]{2,})", expand=False
    ).fillna("Unknown / missing")
    # A review queue: rules do not establish feasibility, safety, or savings.
    d["status_lookup_review_candidate"] = (
        d["payment_keyword_match"] & d["tentative_theme"].eq("Payment / invoice status")
        & d["has_reference_candidate"] & ~d["urgency_wording"]
    )
    update_days = (d["Last Edit Date"] - d["Date Submitted"]).dt.total_seconds() / 86400
    d["closed_last_update_proxy_days"] = update_days.where(d["is_complete"] & update_days.ge(0))
    elapsed = (d["confirmed_completion_date"] - d["Date Submitted"]).dt.total_seconds() / 86400
    d["invalid_completion_order"] = elapsed.lt(0)
    d["completion_days"] = elapsed.where(d["is_complete"] & elapsed.ge(0))
    d["age_days"] = np.nan
    quality = {
        "source_rows": len(raw), "rows_missing_ticket_id": len(rejected),
        "repeated_id_rows_removed": len(identified) - len(d), "unique_tickets": len(d),
        "tickets_with_repeated_ids": int(identified.loc[repeated, "Ticket Number"].nunique()),
        "missing_submission_date": int(d["Date Submitted"].isna().sum()),
        "missing_last_edit_date": int(d["Last Edit Date"].isna().sum()),
        "invalid_update_order": int(d["invalid_update_order"].sum()),
        "conflicting_submission_dates": int(d["submission_date_conflict"].sum()),
        "other_unknown_status": int(d["status_group"].eq("Other / unknown").sum()),
        "missing_request_type": int(d["Request Type"].eq("Unknown / missing").sum()),
        "missing_request_subtype": int(d["Request Sub-Type"].eq("Unknown / missing").sum()),
        "invalid_completion_order": int(d["invalid_completion_order"].sum()),
        "missing_source_columns": ", ".join(missing_columns),
    }
    return d.reset_index(drop=True), quality, rejected, duplicate_audit


def apply_snapshot(d: pd.DataFrame, snapshot_date=None) -> pd.DataFrame:
    out = d.copy()
    out["age_days"] = np.nan
    out.attrs["snapshot_date"] = None
    if snapshot_date is None:
        return out
    snapshot = pd.Timestamp(snapshot_date).normalize()
    observed = pd.concat([out["Date Submitted"], out["Last Edit Date"], out["confirmed_completion_date"]]).dropna()
    if not observed.empty and observed.max().normalize() > snapshot:
        raise ValueError("The as-of date precedes dates in this snapshot. Current statuses cannot reconstruct an earlier backlog.")
    out["age_days"] = (snapshot - out["Date Submitted"].dt.normalize()).dt.days.where(out["is_active"])
    out.attrs["snapshot_date"] = str(snapshot.date())
    return out


def select_cohort(d, start, end, payment_only=False, request_types=None, commodities=None):
    submitted = d["Date Submitted"].dt.normalize()
    mask = submitted.between(pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize())
    if payment_only:
        mask &= d["payment_keyword_match"]
    if request_types is not None:
        mask &= d["Request Type"].isin(request_types)
    if commodities is not None:
        mask &= d["Commodity Group"].isin(commodities)
    return d.loc[mask].copy()


def summary(d: pd.DataFrame, age_threshold=14):
    n, active, closed = len(d), int(d["is_active"].sum()), int(d["is_complete"].sum())
    completion = d["completion_days"].dropna()
    known_age = d.loc[d["is_active"], "age_days"].dropna()
    return {
        "tickets_received": n, "active_in_cohort": active,
        "waiting_for_supplier": int(d["waiting_supplier"].sum()),
        "closed_resolved": closed,
        "closed_resolved_share_pct": round(100 * closed / n, 1) if n else None,
        "other_unknown_status": int(d["status_group"].eq("Other / unknown").sum()),
        "active_older_than_threshold": int(known_age.gt(age_threshold).sum()) if out_age_available(d, known_age) else None,
        "active_age_sample_n": len(known_age),
        "median_completion_days": round(float(completion.median()), 1) if len(completion) else None,
        "p90_completion_days": round(float(completion.quantile(.9)), 1) if len(completion) else None,
        "completion_sample_n": len(completion),
        "po_reference_detected_pct": round(100 * d["has_po_candidate"].mean(), 1) if n else None,
        "invoice_reference_detected_pct": round(100 * d["has_invoice_candidate"].mean(), 1) if n else None,
        "neither_reference_detected": int((~d["has_reference_candidate"]).sum()),
        "urgency_wording_tickets": int(d["urgency_wording"].sum()),
        "status_lookup_review_candidates": int(d["status_lookup_review_candidate"].sum()),
    }


def out_age_available(d, known_age):
    return d.attrs.get("snapshot_date") is not None or not known_age.empty


def drivers(d: pd.DataFrame, group: str):
    out = d.groupby(group, dropna=False).agg(
        Tickets=("Ticket Number", "size"), Active=("is_active", "sum"),
        Waiting_for_supplier=("waiting_supplier", "sum"),
        Closed_resolved=("is_complete", "sum"), Median_active_age_days=("age_days", "median")
    ).reset_index()
    out["Share_pct"] = (100 * out["Tickets"] / len(d)).round(1) if len(d) else 0.0
    return out.sort_values("Tickets", ascending=False).reset_index(drop=True)


def weekly_demand(d: pd.DataFrame, start, end):
    start, end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    first = start - pd.Timedelta(days=start.weekday())
    last = end - pd.Timedelta(days=end.weekday())
    weeks = pd.date_range(first, last, freq="W-MON")
    created = d["Date Submitted"].dt.normalize()
    bucket = created - pd.to_timedelta(created.dt.weekday, unit="D")
    counts = bucket.value_counts().reindex(weeks, fill_value=0)
    return pd.DataFrame({"Week starting": weeks, "Tickets": counts.values,
                         "Coverage": ["Partial week" if w < start or w + pd.Timedelta(days=6) > end else "Full selected week" for w in weeks]})


def reference_repeats(d: pd.DataFrame, field="po_candidates"):
    rows = d[["Ticket Number", field]].explode(field).dropna(subset=[field])
    out = rows.groupby(field)["Ticket Number"].nunique().sort_values(ascending=False).reset_index(name="Distinct tickets")
    return out.rename(columns={field: "Reference candidate"})


def export_frame(d: pd.DataFrame) -> pd.DataFrame:
    out = d.copy()
    for col in ["po_candidates", "invoice_candidates"]:
        if col in out:
            out[col] = out[col].map(lambda x: "; ".join(x))
    # Neutralize spreadsheet formulas in any user-supplied text exported to CSV.
    for col in out.select_dtypes(include=["object", "string"]).columns:
        out[col] = out[col].map(lambda v: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("excel_file", type=Path)
    parser.add_argument("--sheet", default="report-1")
    parser.add_argument("--as-of", help="Confirmed export date YYYY-MM-DD; omit to leave aging unavailable.")
    parser.add_argument("--completion-column", help="Only use a business-validated actual closure/resolution timestamp.")
    parser.add_argument("--payment-only", action="store_true")
    parser.add_argument("--age-threshold", type=int, default=14)
    parser.add_argument("--out", type=Path, default=Path("footprint_output"))
    args = parser.parse_args()
    if args.age_threshold < 0:
        parser.error("--age-threshold must be nonnegative")
    raw = pd.read_excel(args.excel_file, sheet_name=args.sheet, dtype={"Ticket Number": "string"}, engine="openpyxl")
    d, quality, rejected, duplicates = prepare(raw, args.completion_column)
    d = apply_snapshot(d, args.as_of)
    dated = d.dropna(subset=["Date Submitted"])
    if dated.empty:
        raise ValueError("No valid submission dates. Inspect source date formats.")
    start, end = dated["Date Submitted"].min(), dated["Date Submitted"].max()
    view = select_cohort(d, start, end, args.payment_only)
    args.out.mkdir(parents=True, exist_ok=True)
    outputs = {
        "all_unique_tickets": d, "selected_cohort_tickets": view,
        "rows_missing_ticket_id": rejected, "repeated_ticket_rows": duplicates,
        "request_type_drivers": drivers(view, "Request Type"),
        "request_subtype_drivers": drivers(view, "Request Sub-Type"),
        "commodity_drivers": drivers(view, "Commodity Group"),
        "weekly_demand": weekly_demand(view, start, end),
        "repeated_po_candidates": reference_repeats(view),
        "repeated_invoice_candidates": reference_repeats(view, "invoice_candidates"),
        "review_queue": view.loc[view["is_active"] | view["status_lookup_review_candidate"]],
        "data_quality": pd.DataFrame(list(quality.items()), columns=["Check", "Value"]),
    }
    for name, frame in outputs.items():
        export_frame(frame).to_csv(args.out / f"{name}.csv", index=False)
    report = {"submission_window_start": str(start), "submission_window_end": str(end),
              "as_of_date": args.as_of, "payment_keyword_scope_only": args.payment_only,
              "age_review_threshold_days": args.age_threshold,
              "completion_column": args.completion_column, "metrics": summary(view, args.age_threshold),
              "quality": quality}
    (args.out / "business_summary.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
