"""
FootPrints invoice-ticket dashboard (v3)

Reads the FootPrints ticket export, audits data quality, scopes to a trailing
window, and writes two deliverables:

    data/ticket_dashboard.html  -> the version to share with business reviewers
    data/ticket_dashboard.txt   -> plain-text version for email / terminal

Usage:
    python dashboard_v3.py
    python dashboard_v3.py --file "C:/path/to/export.xlsx" --sheet report-1 --months 3
"""

import argparse
import html
import os
import re
from collections import Counter
from datetime import datetime

import pandas as pd

# --- CONFIGURATION -----------------------------------------------------------

EXCEL_FILE = "C:/Users/qw597f/footprint_data_eda/Invoice FP Tickets Jan thru Oct.xlsx"
SHEET_NAME = "report-1"
OUTPUT_DIR = "data"
WINDOW_MONTHS = 3

# Minutes of analyst handling time assumed per ticket. Used only for the
# automation savings estimate, which is explicitly labelled as an assumption.
MINUTES_PER_TICKET = 12

COL = {
    "ticket": "Ticket Number",
    "type": "Request Type",
    "subtype": "Request Sub-Type",
    "status": "Status",
    "submitted": "Date Submitted",
    "edited": "Last Edit Date",
    "title": "Subject (Title)",
    "detail": "Request Detail",
    "email": "Email Address",
}

INVALID_NULLS = {"nan", "none", "null", "nat", ""}
INTERNAL_DOMAINS = {"boeing.com", "ariba.com", "nan", "unknown.com", ""}

# Word-bounded so that "po" does not match "support"/"report"/"component" and
# "pr" does not match "problem". The loose version of this pattern silently
# inflates the payment-ticket count.
PAYMENT_TERMS = [
    r"invoice", r"payment", r"\bpaid\b", r"remittance", r"\bpo\b", r"\bpo#",
    r"\bpr\b", r"p-?card", r"\bwire\b", r"\bap\b", r"credit memo", r"short\s?pay",
]
PAYMENT_TERMS_RE = re.compile("|".join(PAYMENT_TERMS), re.IGNORECASE)
PAYMENT_TYPE_RE = re.compile(r"invoice|payment|\bpo\b|\bpr\b|card", re.IGNORECASE)

PO_RE = re.compile(r"\b(70[0-3]\d{6,7})\b")
INVOICE_RE = re.compile(
    r"\b(?:INV|IR|PAY|PMT|CHS|IV|FLY)[#\s:\-_]*([A-Z0-9][A-Z0-9\-_]{3,})\b",
    re.IGNORECASE,
)

URGENT_RE = re.compile(
    r"urgent|escalat|critical|overdue|past\s?due|on\s?hold|stop\s?ship|credit\s?hold",
    re.IGNORECASE,
)
ESCALATED_RE = re.compile(
    r"escalat|credit\s?hold|stop\s?ship|critical|legal|lien|suspend", re.IGNORECASE
)
CLOSED_RE = re.compile(r"clos|resolv|complet|cancel|done", re.IGNORECASE)

STOP_WORDS = {
    "the", "and", "to", "of", "for", "in", "a", "is", "it", "you", "this", "that",
    "with", "on", "please", "from", "we", "have", "be", "are", "as", "at", "i",
    "our", "hi", "team", "am", "your", "can", "or", "an", "any", "has", "was",
    "will", "not", "but", "all", "us", "if", "they", "would", "thanks", "thank",
    "regards", "hello", "there", "been", "get", "let", "know", "see", "attached",
}


# --- HELPERS -----------------------------------------------------------------

def pct(part, whole, digits=1):
    """Percentage that never raises on an empty denominator."""
    if not whole:
        return 0.0
    return round(part / whole * 100, digits)


def clean_series(s):
    return s.astype(str).str.strip()


def is_blank(s):
    return s.isna() | s.str.lower().isin(INVALID_NULLS)


def require_columns(df):
    missing = [name for name in COL.values() if name not in df.columns]
    if missing:
        raise KeyError(
            "These expected columns are not in the sheet: "
            + ", ".join(missing)
            + ". Found instead: "
            + ", ".join(map(str, df.columns))
        )


# --- 1. LOAD AND AUDIT -------------------------------------------------------

def load_and_audit(path, sheet):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Ticket export not found at '{path}'.")

    df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
    df.columns = df.columns.str.strip()
    require_columns(df)

    total_rows = len(df)

    for key in ("ticket", "type", "subtype", "status"):
        df[COL[key]] = clean_series(df[COL[key]])

    missing = {
        "Ticket number": is_blank(df[COL["ticket"]]),
        "Request type": is_blank(df[COL["type"]]),
        "Request sub-type": is_blank(df[COL["subtype"]]),
    }
    incomplete = missing["Ticket number"] | missing["Request type"] | missing["Request sub-type"]

    df[COL["submitted"]] = pd.to_datetime(df[COL["submitted"]], errors="coerce")
    df[COL["edited"]] = pd.to_datetime(df[COL["edited"]], errors="coerce")
    bad_date = df[COL["submitted"]].isna()

    duplicate_ids = (
        df.loc[~incomplete, COL["ticket"]].duplicated(keep=False).sum()
    )

    clean = df[~incomplete & ~bad_date].copy()

    quality = {
        "total_rows": total_rows,
        "dropped_incomplete": int(incomplete.sum()),
        "dropped_bad_date": int((bad_date & ~incomplete).sum()),
        "missing_breakdown": {k: int(v.sum()) for k, v in missing.items()},
        "duplicate_ids": int(duplicate_ids),
        "usable_rows": len(clean),
    }
    quality["usable_pct"] = pct(quality["usable_rows"], total_rows)
    return clean, quality


# --- 2. SCOPE TO WINDOW ------------------------------------------------------

def scope_window(df, months):
    end = df[COL["submitted"]].max()
    start = end - pd.DateOffset(months=months)
    window = df[df[COL["submitted"]] >= start].copy()

    window["is_closed"] = window[COL["status"]].str.contains(CLOSED_RE, na=False)
    age = (window[COL["edited"]] - window[COL["submitted"]]).dt.days
    window["days_to_last_touch"] = age.clip(lower=0)
    window.loc[~window["is_closed"], "days_to_last_touch"] = pd.NA
    window["submitted_month"] = window[COL["submitted"]].dt.to_period("M")
    return window, start, end


# --- 3. CLASSIFY AND ENRICH --------------------------------------------------

def split_payment(df):
    detail = df[COL["detail"]].astype(str)
    title = df[COL["title"]].astype(str)
    by_type = df[COL["type"]].str.contains(PAYMENT_TYPE_RE, na=False)
    by_text = (title + " " + detail).str.contains(PAYMENT_TERMS_RE, na=False)
    mask = by_type | by_text
    return df[mask].copy(), int(by_type.sum()), int((~by_type & by_text).sum())


def enrich(row):
    title = str(row.get(COL["title"], ""))
    detail = str(row.get(COL["detail"], ""))
    blob = f"{title} {detail}"
    low = blob.lower()

    po_match = PO_RE.search(blob)
    inv_match = INVOICE_RE.search(blob)

    if "reject" in low:
        intent = "Rejected document"
    elif re.search(r"cancel|void|delete|withdraw", low):
        intent = "Cancel or void"
    elif re.search(r"\bstatus\b|unpaid|not paid|past due|when will|follow ?up|late", low):
        intent = "Payment status chase"
    elif re.search(r"expedite|urgent|rush|asap", low):
        intent = "Expedite request"
    elif re.search(r"duplicate|short ?pay|wrong amount|discrepan|mismatch", low):
        intent = "Amount or match dispute"
    elif re.search(r"remit|bank|ach|address change|update vendor", low):
        intent = "Vendor or remittance change"
    else:
        intent = "Other support request"

    domain = ""
    email = str(row.get(COL["email"], "")).lower()
    at = re.search(r"@([\w.\-]+)", email)
    if at:
        domain = at.group(1).rstrip(".")
        if "." not in domain:
            domain = ""

    return pd.Series(
        {
            "po": po_match.group(1) if po_match else None,
            "invoice": inv_match.group(1).upper() if inv_match else None,
            "intent": intent,
            "domain": domain,
            "urgent": bool(URGENT_RE.search(blob)),
            "escalated": bool(ESCALATED_RE.search(blob)),
            "detail_chars": len(detail),
        }
    )


def automation_tier(row):
    """How a ticket would be handled if an agent sat in front of the queue."""
    has_id = bool(row["po"]) or bool(row["invoice"])
    if row["escalated"]:
        return "Analyst handles end to end"
    if has_id and row["intent"] == "Payment status chase":
        return "Answerable from a system lookup"
    if has_id:
        return "Agent drafts, analyst approves"
    return "Analyst handles end to end"


# --- 4. METRICS --------------------------------------------------------------

def build_metrics(path, sheet, months):
    clean, quality = load_and_audit(path, sheet)
    window, start, end = scope_window(clean, months)
    pay, by_type_n, by_text_n = split_payment(window)

    m = {
        "generated": datetime.now().strftime("%d %b %Y %H:%M"),
        "source_file": os.path.basename(path),
        "window_start": start,
        "window_end": end,
        "months": months,
        "quality": quality,
        "window_total": len(window),
        "payment_total": len(pay),
        "other_total": len(window) - len(pay),
        "matched_by_type": by_type_n,
        "matched_by_text_only": by_text_n,
    }
    m["payment_pct"] = pct(m["payment_total"], m["window_total"])
    m["other_pct"] = pct(m["other_total"], m["window_total"])

    monthly = (
        window.groupby("submitted_month").size().rename("all").to_frame()
    )
    if len(pay):
        monthly["payment"] = pay.groupby("submitted_month").size()
    else:
        monthly["payment"] = 0
    monthly = monthly.fillna(0).astype(int)
    m["monthly"] = [
        (str(idx), int(r["all"]), int(r["payment"])) for idx, r in monthly.iterrows()
    ]

    if not len(pay):
        m["empty"] = True
        return m
    m["empty"] = False

    pay = pay.join(pay.apply(enrich, axis=1))
    pay["tier"] = pay.apply(automation_tier, axis=1)

    m["po_found"] = int(pay["po"].notna().sum())
    m["invoice_found"] = int(pay["invoice"].notna().sum())
    m["any_id_found"] = int((pay["po"].notna() | pay["invoice"].notna()).sum())
    m["po_pct"] = pct(m["po_found"], len(pay))
    m["invoice_pct"] = pct(m["invoice_found"], len(pay))
    m["any_id_pct"] = pct(m["any_id_found"], len(pay))

    m["top_pos"] = pay["po"].dropna().value_counts().head(5).items()
    m["top_pos"] = [(k, int(v)) for k, v in m["top_pos"]]
    m["repeat_po_tickets"] = int(
        pay["po"].dropna().value_counts().pipe(lambda s: s[s > 1].sum())
    )
    m["top_invoices"] = [
        (k, int(v)) for k, v in pay["invoice"].dropna().value_counts().head(5).items()
    ]

    external = pay[~pay["domain"].isin(INTERNAL_DOMAINS)]
    m["external_total"] = len(external)
    m["external_pct"] = pct(len(external), len(pay))
    m["top_suppliers"] = [
        (k, int(v)) for k, v in external["domain"].value_counts().head(5).items()
    ]

    m["intents"] = [
        (k, int(v), pct(int(v), len(pay)))
        for k, v in pay["intent"].value_counts().items()
    ]

    words = []
    for txt in pay[COL["detail"]].astype(str):
        words.extend(
            w for w in re.findall(r"\b[a-zA-Z]{3,}\b", txt.lower()) if w not in STOP_WORDS
        )
    m["keywords"] = Counter(words).most_common(8)

    closed = pay[pay["is_closed"]]
    m["closed_total"] = len(closed)
    m["open_total"] = len(pay) - len(closed)
    m["open_pct"] = pct(m["open_total"], len(pay))
    if len(closed):
        d = closed["days_to_last_touch"].astype(float)
        m["median_days"] = round(float(d.median()), 1)
        m["mean_days"] = round(float(d.mean()), 1)
        m["p90_days"] = round(float(d.quantile(0.9)), 1)
        buckets = pd.cut(
            d,
            bins=[-0.1, 1, 3, 7, 14, float("inf")],
            labels=["Same or next day", "2-3 days", "4-7 days", "8-14 days", "Over 14 days"],
        )
        m["age_buckets"] = [
            (str(k), int(v), pct(int(v), len(closed)))
            for k, v in buckets.value_counts().reindex(buckets.cat.categories).items()
        ]
    else:
        m["median_days"] = m["mean_days"] = m["p90_days"] = None
        m["age_buckets"] = []

    m["urgent_total"] = int(pay["urgent"].sum())
    m["urgent_pct"] = pct(m["urgent_total"], len(pay))

    tier_order = [
        "Answerable from a system lookup",
        "Agent drafts, analyst approves",
        "Analyst handles end to end",
    ]
    counts = pay["tier"].value_counts()
    m["tiers"] = [
        (t, int(counts.get(t, 0)), pct(int(counts.get(t, 0)), len(pay))) for t in tier_order
    ]
    auto_n = int(counts.get(tier_order[0], 0))
    m["auto_candidates"] = auto_n
    m["auto_pct"] = pct(auto_n, len(pay))
    m["hours_saved_window"] = round(auto_n * MINUTES_PER_TICKET / 60, 1)
    m["hours_saved_year"] = round(m["hours_saved_window"] * (12 / months), 1)
    m["minutes_per_ticket"] = MINUTES_PER_TICKET

    m["df_pay"] = pay
    return m


# --- 5. TEXT REPORT ----------------------------------------------------------

def render_text(m):
    q = m["quality"]
    L = []
    add = L.append
    bar = "=" * 76

    add(bar)
    add("FOOTPRINTS INVOICE TICKETS — BUSINESS REVIEW PACK")
    add(f"Window: {m['window_start']:%d %b %Y} to {m['window_end']:%d %b %Y}"
        f"  |  Source: {m['source_file']}  |  Built: {m['generated']}")
    add(bar)

    add("\n1. CAN WE TRUST THE DATA?")
    add(f"   Rows in export                    {q['total_rows']:>8,}")
    add(f"   Usable for analysis               {q['usable_rows']:>8,}  ({q['usable_pct']}%)")
    add(f"   Dropped, key field blank          {q['dropped_incomplete']:>8,}")
    for k, v in q["missing_breakdown"].items():
        add(f"      {k:<30} {v:>8,}")
    add(f"   Dropped, unreadable submit date   {q['dropped_bad_date']:>8,}")
    add(f"   Rows sharing a ticket number      {q['duplicate_ids']:>8,}")

    add(f"\n2. WHAT CAME IN OVER {m['months']} MONTHS")
    add(f"   Tickets in window                 {m['window_total']:>8,}")
    add(f"   Invoice or payment related        {m['payment_total']:>8,}  ({m['payment_pct']}%)")
    add(f"   Everything else                   {m['other_total']:>8,}  ({m['other_pct']}%)")
    add("   Monthly intake (all / payment):")
    for label, all_n, pay_n in m["monthly"]:
        add(f"      {label}   {all_n:>6,} / {pay_n:>6,}")

    if m["empty"]:
        add("\nNo payment tickets matched in this window; remaining sections skipped.")
        return "\n".join(L)

    add("\n3. DO TICKETS CARRY A REFERENCE WE CAN LOOK UP?")
    add(f"   PO number found in text           {m['po_found']:>8,}  ({m['po_pct']}%)")
    add(f"   Invoice number found in text      {m['invoice_found']:>8,}  ({m['invoice_pct']}%)")
    add(f"   At least one reference            {m['any_id_found']:>8,}  ({m['any_id_pct']}%)")
    add(f"   Tickets about a repeat PO         {m['repeat_po_tickets']:>8,}")
    if m["top_pos"]:
        add("   POs raised most often:")
        for po, n in m["top_pos"]:
            add(f"      PO {po}   {n} tickets")
    if m["top_suppliers"]:
        add(f"   From outside the company          {m['external_total']:>8,}  ({m['external_pct']}%)")
        for dom, n in m["top_suppliers"]:
            add(f"      {dom:<30} {n:>8,}")

    add("\n4. WHY PEOPLE ARE WRITING IN")
    for intent, n, share in m["intents"]:
        add(f"   {intent:<34} {n:>6,}  ({share}%)")
    add("   Words most common in request text: "
        + ", ".join(f"{w} ({n})" for w, n in m["keywords"][:6]))

    add("\n5. HOW LONG THEY TAKE")
    add(f"   Closed in window                  {m['closed_total']:>8,}")
    add(f"   Still open                        {m['open_total']:>8,}  ({m['open_pct']}%)")
    if m["median_days"] is not None:
        add(f"   Typical time to close (median)    {m['median_days']:>8} days")
        add(f"   Slowest 10% take at least         {m['p90_days']:>8} days")
        for label, n, share in m["age_buckets"]:
            add(f"      {label:<20} {n:>6,}  ({share}%)")
    add(f"   Flagged urgent or escalated       {m['urgent_total']:>8,}  ({m['urgent_pct']}%)")

    add("\n6. WHERE AUTOMATION WOULD LAND")
    for tier, n, share in m["tiers"]:
        add(f"   {tier:<34} {n:>6,}  ({share}%)")
    add(f"   Assuming {m['minutes_per_ticket']} minutes of handling per ticket, the first group is")
    add(f"   about {m['hours_saved_window']} analyst hours over {m['months']} months "
        f"({m['hours_saved_year']} per year).")

    add("\n7. FOR THE BUSINESS TO CONFIRM")
    for qn in review_questions(m):
        add(f"   - {qn}")
    add("\n" + bar)
    return "\n".join(L)


def review_questions(m):
    return [
        f"Is {m['payment_pct']}% a believable share of this queue for invoice and payment work?",
        "Does 'last edit date' stand in for closure date, or is there a true resolution date field?",
        f"Should the {m['quality']['dropped_incomplete']:,} rows missing a key field be fixed at source or written off?",
        f"Is {m['minutes_per_ticket']} minutes per ticket a fair handling estimate for the savings figure?",
        "Which of the intent groups below is actually the most painful for your team?",
        "Are the top suppliers listed the ones you would have named yourself?",
    ]


# --- 6. HTML REPORT ----------------------------------------------------------

CSS = """
:root{
  --paper:#eef1f5; --card:#ffffff; --ink:#15202b; --muted:#5a6b7c;
  --rule:#c7d0da; --steel:#2f5873; --steel-soft:#8fb0c8;
  --amber:#a8630f; --green:#2c6349; --shadow:rgba(21,32,43,.08);
}
@media (prefers-color-scheme: dark){
  :root{
    --paper:#111820; --card:#18212c; --ink:#e6edf4; --muted:#9bacbd;
    --rule:#2c3a49; --steel:#7fb0d4; --steel-soft:#3d5c75;
    --amber:#e0a461; --green:#6cc29a; --shadow:rgba(0,0,0,.4);
  }
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:"Inter","Segoe UI",system-ui,-apple-system,sans-serif;
  font-size:16px; line-height:1.55;
  font-variant-numeric:tabular-nums;
}
.wrap{max-width:60rem;margin:0 auto;padding:2.5rem 1.25rem 4rem}
header.masthead{border-bottom:3px solid var(--steel);padding-bottom:1rem;margin-bottom:2rem}
h1{font-size:1.75rem;font-weight:650;letter-spacing:-.015em;margin:0 0 .35rem}
.meta{color:var(--muted);font-size:.875rem;margin:0}
h2{
  font-size:1.0625rem;font-weight:650;margin:2.75rem 0 .35rem;
  padding-left:.7rem;border-left:4px solid var(--steel);
}
.lede{color:var(--muted);font-size:.9375rem;margin:0 0 1rem;max-width:46rem}
.hero{
  background:var(--card);border:1px solid var(--rule);border-radius:4px;
  padding:1.5rem;margin-bottom:.5rem;box-shadow:0 1px 2px var(--shadow);
}
.hero .figure{font-size:3rem;font-weight:680;line-height:1;letter-spacing:-.03em}
.hero .figure span{font-size:1.125rem;font-weight:500;color:var(--muted);letter-spacing:0}
.split{display:flex;height:2.25rem;border-radius:3px;overflow:hidden;margin:1.25rem 0 .6rem}
.split div{display:flex;align-items:center;padding:0 .7rem;font-size:.8125rem;font-weight:600;color:#fff;white-space:nowrap}
.split .a{background:var(--steel)}
.split .b{background:var(--steel-soft);color:var(--ink)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(11rem,1fr));gap:.75rem;margin:1rem 0}
.kpi{background:var(--card);border:1px solid var(--rule);border-radius:4px;padding:.85rem .95rem}
.kpi b{display:block;font-size:1.5rem;font-weight:660;line-height:1.15}
.kpi span{font-size:.8125rem;color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:.9375rem;margin:.5rem 0 .25rem}
th{text-align:left;font-weight:600;color:var(--muted);font-size:.8125rem;
   border-bottom:1px solid var(--rule);padding:.4rem .5rem .4rem 0}
td{padding:.42rem .5rem .42rem 0;border-bottom:1px solid var(--rule)}
td.n,th.n{text-align:right;padding-right:0}
.track{background:var(--rule);height:.5rem;border-radius:2px;min-width:3rem}
.track i{display:block;height:100%;border-radius:2px;background:var(--steel)}
.track.warm i{background:var(--amber)}
.note{font-size:.8125rem;color:var(--muted);margin:.5rem 0 0}
.flag{border-left:4px solid var(--amber);background:var(--card);padding:.8rem 1rem;
      border-radius:0 4px 4px 0;margin:1rem 0;font-size:.9375rem}
.ask{background:var(--card);border:1px solid var(--rule);border-radius:4px;padding:1.25rem 1.5rem}
.ask li{margin-bottom:1.5rem}
.ask .line{display:block;border-bottom:1px dashed var(--rule);height:1.6rem;margin-top:.3rem}
footer{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--rule);
       font-size:.8125rem;color:var(--muted)}
@media print{body{background:#fff}.wrap{padding:0}}
"""


def _bar(value, maximum, warm=False):
    width = 0 if not maximum else round(value / maximum * 100)
    cls = "track warm" if warm else "track"
    return f'<div class="{cls}"><i style="width:{width}%"></i></div>'


def _rows(items, total, warm=False):
    if not items:
        return '<tr><td colspan="4" class="note">Nothing found in this window.</td></tr>'
    top = max(n for _, n in items) if items else 0
    out = []
    for label, n in items:
        out.append(
            f"<tr><td>{html.escape(str(label))}</td>"
            f'<td class="n">{n:,}</td>'
            f'<td class="n">{pct(n, total)}%</td>'
            f"<td>{_bar(n, top, warm)}</td></tr>"
        )
    return "".join(out)


def render_html(m):
    q = m["quality"]
    e = html.escape

    monthly_rows = "".join(
        f'<tr><td>{e(lbl)}</td><td class="n">{a:,}</td><td class="n">{p:,}</td>'
        f'<td class="n">{pct(p, a)}%</td></tr>'
        for lbl, a, p in m["monthly"]
    )

    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>FootPrints invoice tickets — {m['window_start']:%b %Y} to {m['window_end']:%b %Y}</title>",
        f"<style>{CSS}</style></head><body><div class='wrap'>",
        "<header class='masthead'><h1>What the invoice ticket queue looks like</h1>",
        f"<p class='meta'>Tickets submitted between {m['window_start']:%d %B %Y} and "
        f"{m['window_end']:%d %B %Y}.<br>Read from {e(m['source_file'])}, built "
        f"{e(m['generated'])}.</p></header>",
    ]

    # Hero
    parts.append("<div class='hero'>")
    parts.append(
        f"<div class='figure'>{m['payment_total']:,} <span>of {m['window_total']:,} tickets "
        f"are about an invoice or a payment</span></div>"
    )
    parts.append(
        f"<div class='split'><div class='a' style='width:{max(m['payment_pct'], 8)}%'>"
        f"Invoice &amp; payment {m['payment_pct']}%</div>"
        f"<div class='b' style='width:{max(m['other_pct'], 8)}%'>Other requests {m['other_pct']}%</div></div>"
    )
    parts.append(
        f"<p class='note'>{m['matched_by_type']:,} were already categorised as payment work in "
        f"FootPrints. A further {m['matched_by_text_only']:,} were found only by reading the "
        f"request text, which is the gap between how tickets are filed and what they are about.</p></div>"
    )

    # 1. Data quality
    parts.append("<h2>1. How much of the export we could use</h2>")
    parts.append(
        "<p class='lede'>Rows are excluded when a field we need is blank, not because the "
        "request was invalid. These are real tickets someone worked.</p>"
    )
    parts.append("<div class='kpis'>")
    parts.append(f"<div class='kpi'><b>{q['total_rows']:,}</b><span>rows in the export</span></div>")
    parts.append(f"<div class='kpi'><b>{q['usable_pct']}%</b><span>usable for analysis</span></div>")
    parts.append(f"<div class='kpi'><b>{q['dropped_incomplete']:,}</b><span>dropped, key field blank</span></div>")
    parts.append(f"<div class='kpi'><b>{q['duplicate_ids']:,}</b><span>rows share a ticket number</span></div>")
    parts.append("</div>")
    parts.append("<table><thead><tr><th>Field left blank</th><th class='n'>Rows</th>"
                 "<th class='n'>Share of export</th><th></th></tr></thead><tbody>")
    parts.append(_rows(list(q["missing_breakdown"].items()), q["total_rows"], warm=True))
    parts.append("</tbody></table>")
    if q["dropped_bad_date"]:
        parts.append(
            f"<p class='note'>A further {q['dropped_bad_date']:,} rows had a submit date that "
            "could not be read and were also set aside.</p>"
        )

    # 2. Volume
    parts.append(f"<h2>2. Intake over {m['months']} months</h2>")
    parts.append("<p class='lede'>Month by month, so a one-off spike is not mistaken for a trend.</p>")
    parts.append("<table><thead><tr><th>Month</th><th class='n'>All tickets</th>"
                 "<th class='n'>Invoice &amp; payment</th><th class='n'>Share</th></tr></thead><tbody>")
    parts.append(monthly_rows or "<tr><td colspan='4'>No rows.</td></tr>")
    parts.append("</tbody></table>")
    parts.append(
        "<p class='note'>The first and last month in the window are usually partial, "
        "so compare the middle months.</p>"
    )

    if m["empty"]:
        parts.append("<div class='flag'>No invoice or payment tickets matched in this window, "
                     "so the remaining sections are empty. Check the request-type values in the "
                     "export before reading anything into this.</div>")
        parts.append("</div></body></html>")
        return "".join(parts)

    # 3. References
    parts.append("<h2>3. Can a ticket be matched to a document?</h2>")
    parts.append(
        "<p class='lede'>Nothing can be looked up or automated unless the ticket names a PO or "
        "invoice. This is the ceiling on any straight-through handling.</p>"
    )
    parts.append("<div class='kpis'>")
    parts.append(f"<div class='kpi'><b>{m['any_id_pct']}%</b><span>name a PO or invoice</span></div>")
    parts.append(f"<div class='kpi'><b>{m['po_pct']}%</b><span>name a PO number</span></div>")
    parts.append(f"<div class='kpi'><b>{m['invoice_pct']}%</b><span>name an invoice number</span></div>")
    parts.append(f"<div class='kpi'><b>{m['repeat_po_tickets']:,}</b><span>tickets about a PO raised more than once</span></div>")
    parts.append("</div>")
    parts.append("<table><thead><tr><th>Purchase order raised most often</th><th class='n'>Tickets</th>"
                 "<th class='n'>Share</th><th></th></tr></thead><tbody>")
    parts.append(_rows(m["top_pos"], m["payment_total"], warm=True))
    parts.append("</tbody></table>")
    parts.append("<table><thead><tr><th>Sender domain outside the company</th><th class='n'>Tickets</th>"
                 "<th class='n'>Share</th><th></th></tr></thead><tbody>")
    parts.append(_rows(m["top_suppliers"], m["payment_total"]))
    parts.append("</tbody></table>")
    parts.append(
        f"<p class='note'>{m['external_pct']}% of payment tickets came from an address outside "
        "the company. Repeat POs and repeat senders are the cheapest thing to fix: one upstream "
        "correction removes a run of tickets.</p>"
    )

    # 4. Intent
    parts.append("<h2>4. Why people are writing in</h2>")
    parts.append("<p class='lede'>Grouped by what the requester asked for, read from the subject "
                 "line and request text.</p>")
    parts.append("<table><thead><tr><th>Request</th><th class='n'>Tickets</th>"
                 "<th class='n'>Share</th><th></th></tr></thead><tbody>")
    parts.append(_rows([(i, n) for i, n, _ in m["intents"]], m["payment_total"]))
    parts.append("</tbody></table>")
    parts.append(
        "<p class='note'>Most common words in the request text: "
        + ", ".join(f"{e(w)} ({n})" for w, n in m["keywords"][:8])
        + ".</p>"
    )

    # 5. Turnaround
    parts.append("<h2>5. How long tickets stay open</h2>")
    parts.append(
        "<p class='lede'>Measured from submission to the last edit on closed tickets, because the "
        "export has no separate resolution date. Treat it as an upper bound on handling time.</p>"
    )
    parts.append("<div class='kpis'>")
    if m["median_days"] is not None:
        parts.append(f"<div class='kpi'><b>{m['median_days']}</b><span>days for a typical ticket</span></div>")
        parts.append(f"<div class='kpi'><b>{m['p90_days']}</b><span>days for the slowest tenth</span></div>")
    parts.append(f"<div class='kpi'><b>{m['open_pct']}%</b><span>still open at export</span></div>")
    parts.append(f"<div class='kpi'><b>{m['urgent_pct']}%</b><span>worded as urgent or escalated</span></div>")
    parts.append("</div>")
    if m["age_buckets"]:
        parts.append("<table><thead><tr><th>Time to close</th><th class='n'>Tickets</th>"
                     "<th class='n'>Share of closed</th><th></th></tr></thead><tbody>")
        parts.append(_rows([(b, n) for b, n, _ in m["age_buckets"]], m["closed_total"]))
        parts.append("</tbody></table>")
    if m["median_days"] is not None and m["mean_days"] and m["mean_days"] > m["median_days"] * 1.5:
        parts.append(
            f"<div class='flag'>The average of {m['mean_days']} days is well above the median of "
            f"{m['median_days']}. A small number of long-running tickets is pulling it up, so quote "
            "the median in any SLA conversation.</div>"
        )

    # 6. Automation
    parts.append("<h2>6. Where an agent could take work off the queue</h2>")
    parts.append(
        "<p class='lede'>Each payment ticket placed in one of three buckets, based on whether it "
        "names a document, what it asks for, and whether it is escalated.</p>"
    )
    parts.append("<table><thead><tr><th>Handling route</th><th class='n'>Tickets</th>"
                 "<th class='n'>Share</th><th></th></tr></thead><tbody>")
    parts.append(_rows([(t, n) for t, n, _ in m["tiers"]], m["payment_total"]))
    parts.append("</tbody></table>")
    parts.append(
        f"<div class='flag'>{m['auto_candidates']:,} tickets ({m['auto_pct']}%) are a status or "
        f"expedite question about a named document. At an assumed {m['minutes_per_ticket']} minutes "
        f"of handling each, that is roughly {m['hours_saved_window']} analyst hours across these "
        f"{m['months']} months, or about {m['hours_saved_year']} a year. The minutes figure is an "
        "assumption, not a measurement — please replace it with your own.</div>"
    )

    # 7. Questions
    parts.append("<h2>7. What we need you to confirm</h2>")
    parts.append("<p class='lede'>Every number above rests on a judgement call. These are the ones "
                 "worth challenging before this goes any further.</p>")
    parts.append("<div class='ask'><ol>")
    for qn in review_questions(m):
        parts.append(f"<li>{e(qn)}<span class='line'></span></li>")
    parts.append("</ol></div>")

    parts.append(
        f"<footer>Built from {e(m['source_file'])} on {e(m['generated'])}. "
        "Text classification uses keyword rules, so counts in sections 3 to 6 carry a small "
        "margin of error; the volume and data-quality figures do not.</footer>"
    )
    parts.append("</div></body></html>")
    return "".join(parts)


# --- 7. ENTRY POINT ----------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Build the FootPrints ticket dashboard.")
    ap.add_argument("--file", default=EXCEL_FILE)
    ap.add_argument("--sheet", default=SHEET_NAME)
    ap.add_argument("--months", type=int, default=WINDOW_MONTHS)
    ap.add_argument("--out", default=OUTPUT_DIR)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    print(f"Reading {args.file} ...")
    m = build_metrics(args.file, args.sheet, args.months)

    txt_path = os.path.join(args.out, "ticket_dashboard.txt")
    html_path = os.path.join(args.out, "ticket_dashboard.html")

    text = render_text(m)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render_html(m))

    csv_path = None
    if not m["empty"]:
        csv_path = os.path.join(args.out, "payment_tickets_enriched.csv")
        cols = [COL["ticket"], COL["submitted"], COL["status"], COL["type"],
                COL["title"], "po", "invoice", "intent", "domain", "urgent",
                "escalated", "days_to_last_touch", "tier"]
        m["df_pay"][cols].to_csv(csv_path, index=False)

    print(text)
    print(f"\nWritten:\n  {html_path}  (share this one)\n  {txt_path}")
    if csv_path:
        print(f"  {csv_path}  (backing rows for spot checks)")


if __name__ == "__main__":
    main()
