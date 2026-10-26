"""Run with: streamlit run dashboard.py"""
from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from metrics import (
    apply_snapshot, drivers, export_frame, prepare, reference_repeats,
    select_cohort, summary, weekly_demand,
)

st.set_page_config(page_title="FootPrints | Business review", page_icon="📊", layout="wide")
st.title("FootPrints business review")
st.caption("Ticket demand, work needing attention, and opportunities for business review")


@st.cache_data(show_spinner=False)
def sheets(blob):
    return pd.ExcelFile(io.BytesIO(blob), engine="openpyxl").sheet_names


@st.cache_data(show_spinner=False)
def read_sheet(blob, sheet):
    return pd.read_excel(io.BytesIO(blob), sheet_name=sheet, engine="openpyxl", dtype={"Ticket Number": "string"})


def download_csv(label, frame, filename, key):
    st.download_button(label, export_frame(frame).to_csv(index=False).encode("utf-8-sig"),
                       file_name=filename, mime="text/csv", key=key)


def bar(frame, category, value="Tickets", color=None, height=280):
    chart = alt.Chart(frame).mark_bar(cornerRadiusEnd=3).encode(
        x=alt.X(f"{value}:Q", title="Tickets", axis=alt.Axis(tickMinStep=1)),
        y=alt.Y(f"{category}:N", sort="-x", title=None),
        tooltip=[alt.Tooltip(f"{category}:N"), alt.Tooltip(f"{value}:Q", format=",")],
    )
    if color:
        chart = chart.encode(color=alt.Color(f"{category}:N", scale=color, legend=None))
    else:
        chart = chart.mark_bar(color="#237a89", cornerRadiusEnd=3)
    st.altair_chart(chart.properties(height=height), width="stretch")


def grouped_top(frame, group, limit=8):
    counts = frame[group].value_counts().rename_axis(group).reset_index(name="Tickets")
    if len(counts) > limit:
        rest = int(counts.iloc[limit:]["Tickets"].sum())
        counts = pd.concat([counts.head(limit), pd.DataFrame({group: ["All remaining categories"], "Tickets": [rest]})], ignore_index=True)
    return counts


with st.sidebar:
    st.header("Source and scope")
    uploaded = st.file_uploader("Upload FootPrints Excel export", type=["xlsx"])
    with st.expander("Or use a workbook on this computer"):
        local_path = st.text_input("Full Excel path", value=os.environ.get("FOOTPRINT_EXCEL", ""), key="local_path")

try:
    blob = uploaded.getvalue() if uploaded else (Path(local_path).read_bytes() if local_path else None)
except OSError as exc:
    st.error(f"Cannot read workbook: {exc}")
    st.stop()

if blob is None:
    st.info("Upload your workbook to calculate the dashboard from the actual ticket data.")
    st.markdown("""
    **The business review covers:**

    - **Demand:** tickets received by week and request type.
    - **Work needing attention:** active tickets, supplier follow-ups, and aging once the export date is confirmed.
    - **Business drivers:** request sub-types, commodities, and repeated reference candidates.
    - **Review and decisions:** ticket examples, data gaps, and downloadable business comments.

    Counts are based on unique ticket numbers. No sample results are shown.
    """)
    st.stop()

try:
    available_sheets = sheets(blob)
    sheet = st.sidebar.selectbox("Worksheet", available_sheets,
                                 index=available_sheets.index("report-1") if "report-1" in available_sheets else 0)
    raw = read_sheet(blob, sheet)
    raw.columns = raw.columns.map(lambda x: str(x).strip())
    completion_choices = ["No confirmed completion field"] + [
        col for col in raw.columns if col not in ["Date Submitted", "Last Edit Date"]
    ]
    with st.sidebar.expander("Optional: actual completion date"):
        completion_field = st.selectbox("Actual closure / resolution timestamp", completion_choices)
        confirmed_completion = st.checkbox("Business owner confirms this field records actual completion", value=False)
        st.caption("Last Edit Date is an update timestamp and is excluded from this selection.")
    completion_field = completion_field if completion_field != completion_choices[0] and confirmed_completion else None
    base, quality, rejected, duplicates = prepare(raw, completion_field)
except (ValueError, KeyError, TypeError, OSError) as exc:
    st.error(f"Unable to prepare this workbook: {exc}")
    st.stop()

dated = base.dropna(subset=["Date Submitted"])
if dated.empty:
    st.error("No valid submission dates were found. Correct Date Submitted in the workbook before using the dashboard.")
    st.dataframe(pd.DataFrame(quality.items(), columns=["Check", "Value"]).astype(str), hide_index=True)
    st.stop()

minimum, maximum = dated["Date Submitted"].min().date(), dated["Date Submitted"].max().date()
suggested_snapshot = pd.concat([base["Date Submitted"], base["Last Edit Date"], base["confirmed_completion_date"]]).max().date()
with st.sidebar:
    date_window = st.date_input("Tickets submitted between", value=(minimum, maximum), min_value=minimum, max_value=maximum)
    if len(date_window) != 2:
        st.info("Select both a start and end date.")
        st.stop()
    start, end = date_window
    scope = st.selectbox("Ticket scope", ["All imported tickets", "Payment-related keyword matches only"])
    request_options = sorted(base["Request Type"].unique())
    commodity_options = sorted(base["Commodity Group"].unique())
    request_filter = st.multiselect("Request type", request_options, placeholder="All request types")
    commodity_filter = st.multiselect("Commodity group", commodity_options, placeholder="All commodity groups")
    st.divider()
    st.subheader("Aging")
    snapshot_value = st.date_input("Export / snapshot as-of date", value=suggested_snapshot)
    confirmed_snapshot = st.checkbox("I confirm this is the actual export as-of date", value=False)
    st.caption("The suggested date is the latest observed timestamp. Confirm against the source export.")
    age_threshold = st.number_input("Review active tickets older than (days)", min_value=0, max_value=3650, value=14)
    st.caption("This is a business review threshold, not an SLA.")

try:
    base = apply_snapshot(base, snapshot_value if confirmed_snapshot else None)
except ValueError as exc:
    st.error(str(exc))
    st.stop()

view = select_cohort(base, start, end, scope != "All imported tickets", request_filter or None, commodity_filter or None)
m = summary(view, age_threshold)
dataset_hash = hashlib.sha256(blob + sheet.encode()).hexdigest()
context = {"dataset_sha256": dataset_hash, "sheet": sheet, "submission_start": str(start),
           "submission_end": str(end), "scope": scope, "request_types": request_filter or ["All"],
           "commodities": commodity_filter or ["All"], "snapshot_date": str(snapshot_value) if confirmed_snapshot else None,
           "age_threshold_days": int(age_threshold), "completion_field": completion_field}

months = dated["Date Submitted"].dt.to_period("M").nunique()
st.markdown(f"**Submitted {start:%b %d, %Y} – {end:%b %d, %Y}** · {scope} · **{len(view):,} unique tickets in view**")
st.caption(f"Workbook coverage: {minimum:%b %d, %Y} – {maximum:%b %d, %Y} · {months} calendar months present · Worksheet: {sheet}")
st.caption("Statuses reflect the source snapshot. Active counts cover tickets received in this selected cohort; older tickets outside the export are not included.")
if quality["missing_submission_date"] or quality["rows_missing_ticket_id"] or quality["repeated_id_rows_removed"]:
    st.info(f"Data preparation: {quality['rows_missing_ticket_id']:,} rows without ticket IDs excluded; "
            f"{quality['repeated_id_rows_removed']:,} repeated-ID rows consolidated; "
            f"{quality['missing_submission_date']:,} unique tickets without valid submission dates are outside date-filtered metrics. See Data quality.")
if scope != "All imported tickets":
    st.info("Payment scope is based on keywords in request fields and ticket text. Business validation is needed for inclusion and exclusions.")
if not confirmed_snapshot:
    st.caption("Aging remains unavailable until the actual export date is confirmed in the sidebar.")
if view.empty:
    st.warning("No tickets match these filters. Broaden the date, request type, commodity, or keyword scope.")

overview, business, attention, review, dq = st.tabs(
    ["Business overview", "Demand drivers", "Tickets to review", "Business comments", "Data quality & definitions"]
)

with overview:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tickets received", f"{m['tickets_received']:,}", help="Distinct ticket IDs submitted in the selected window and matching all filters.")
    c2.metric("Still active in this cohort", f"{m['active_in_cohort']:,}", help="Current status is Open, In Progress, or Pending Supplier Response.")
    c3.metric("Waiting for supplier", f"{m['waiting_for_supplier']:,}", help="Subset of active tickets currently marked Pending Supplier Response.")
    c4.metric("Closed / resolved share", f"{m['closed_resolved_share_pct']:.1f}%" if len(view) else "—",
              help="Closed or Resolved tickets divided by every ticket in view, including unknown statuses. Snapshot cohort share; not a throughput or SLA measure.")
    st.caption(f"Closed / resolved: {m['closed_resolved']:,} · Other / unknown status: {m['other_unknown_status']:,}. "
               "Newer ticket cohorts have had less time to close.")
    a, b = st.columns(2)
    with a:
        st.subheader("How much work arrived each week?")
        weekly = weekly_demand(view, start, end)
        chart = alt.Chart(weekly).mark_bar(color="#237a89", cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
            x=alt.X("Week starting:T", title="Week starting Monday"),
            y=alt.Y("Tickets:Q", axis=alt.Axis(tickMinStep=1)),
            opacity=alt.condition(alt.datum.Coverage == "Partial week", alt.value(.45), alt.value(1)),
            tooltip=[alt.Tooltip("Week starting:T", format="%b %d, %Y"), "Tickets:Q", "Coverage:N"],
        )
        st.altair_chart(chart.properties(height=270), width="stretch")
        st.caption("Lighter bars are partial weeks in the selected window. Zero-ticket weeks are retained. Source omissions cannot be inferred from this extract.")
    with b:
        st.subheader("What is the current state of this work?")
        counts = view["status_group"].value_counts().rename_axis("Current state").reset_index(name="Tickets")
        bar(counts, "Current state", color=alt.Scale(domain=["Active", "Closed / resolved", "Other / unknown"], range=["#ca8a04", "#237a89", "#9ca3af"]))
    st.subheader("Where should the business focus?")
    signals = [
        ["Supplier follow-up", str(m["waiting_for_supplier"]), "Who owns follow-up, and what information is blocking progress?"],
        [f"Active longer than {age_threshold} days", str(m["active_older_than_threshold"]) if confirmed_snapshot else "Confirm export date", "Which cases need escalation or a clear next action?"],
        ["Other / unknown status", str(m["other_unknown_status"]), "Which source statuses should be mapped to active or completed?"],
        ["No PO or invoice reference detected", str(m["neither_reference_detected"]), "Is a reference required for this request, or was it supplied in an attachment?"],
    ]
    st.dataframe(pd.DataFrame(signals, columns=["Review topic", "Tickets", "Business discussion"]), hide_index=True, width="stretch")
    if completion_field:
        st.subheader("How long did completed tickets take?")
        l, r, n = st.columns(3)
        l.metric("Median completion days", m["median_completion_days"] if m["median_completion_days"] is not None else "—")
        r.metric("90th percentile days", m["p90_completion_days"] if m["p90_completion_days"] is not None else "—")
        n.metric("Completed tickets with valid timing", f"{m['completion_sample_n']:,} / {m['closed_resolved']:,}")
        st.caption(f"Uses confirmed field '{completion_field}'. Calendar elapsed days, only currently Closed / Resolved tickets with valid timestamps. Open tickets are excluded; long-running unresolved work is shown through aging.")
    else:
        st.caption("Resolution time and SLA compliance are unavailable: a validated completion timestamp and agreed SLA rules have not been supplied.")

with business:
    st.subheader("What is generating the work?")
    left, right = st.columns(2)
    with left:
        st.markdown("**Request types**")
        bar(grouped_top(view, "Request Type"), "Request Type")
    with right:
        st.markdown("**Request sub-types**")
        bar(grouped_top(view, "Request Sub-Type"), "Request Sub-Type")
    dimension = st.selectbox("Compare business drivers by", ["Request Type", "Request Sub-Type", "Commodity Group", "requester_domain"])
    table = drivers(view, dimension).rename(columns={"Waiting_for_supplier": "Waiting for supplier", "Closed_resolved": "Closed / resolved",
                                                  "Median_active_age_days": "Median active age (days)", "Share_pct": "Share of tickets (%)", "requester_domain": "Requester email domain"})
    st.dataframe(table, hide_index=True, width="stretch")
    st.caption("Requester domains describe email sources; they do not establish supplier identity. Request types and commodities describe demand, not verified root causes.")
    download_csv("Download driver table", table, "business_drivers.csv", "download_drivers")
    st.subheader("Do tickets contain useful lookup references?")
    coverage = view["reference_coverage"].value_counts().rename_axis("Reference information").reset_index(name="Tickets")
    bar(coverage, "Reference information", height=200)
    st.caption("These are candidate IDs found in title and detail text. The chart is not an extraction-accuracy score or a match to SAP. Not every request needs both IDs; attachments are not examined.")
    left, right = st.columns(2)
    with left:
        st.markdown("**PO candidates appearing in multiple tickets**")
        repeats = reference_repeats(view)
        st.dataframe(repeats.loc[repeats["Distinct tickets"].gt(1)].head(10), hide_index=True, width="stretch")
    with right:
        st.markdown("**Invoice candidates appearing in multiple tickets**")
        repeats = reference_repeats(view, "invoice_candidates")
        st.dataframe(repeats.loc[repeats["Distinct tickets"].gt(1)].head(10), hide_index=True, width="stretch")
    st.caption("Repeated references are a follow-up signal. They do not prove duplicate tickets, financial exposure, or a recurring root cause; invoice identifiers may overlap across suppliers.")

with attention:
    st.subheader("Which tickets should we inspect?")
    choice = st.selectbox("Review queue", ["All active tickets", "Waiting for supplier", "Active beyond age threshold", "Status lookup process review", "All tickets in view"])
    if choice == "All active tickets":
        queue = view.loc[view["is_active"]]
    elif choice == "Waiting for supplier":
        queue = view.loc[view["waiting_supplier"]]
    elif choice == "Active beyond age threshold":
        queue = view.loc[view["is_active"] & view["age_days"].gt(age_threshold)]
        if not confirmed_snapshot:
            st.info("Confirm the export as-of date to use the aging queue.")
    elif choice == "Status lookup process review":
        queue = view.loc[view["status_lookup_review_candidate"]]
        st.info("Rule-based review candidates: payment keywords, status theme, a reference candidate, and no urgency wording detected. Validate intent, access, process steps, and human controls before estimating automation potential.")
    else:
        queue = view
    st.caption("Urgency wording and themes are tentative text signals, including quoted email history. They are not approved priority, business impact, or root-cause classifications.")
    columns = ["Ticket Number", "Subject (Title)", "Request Type", "Request Sub-Type", "Status", "Date Submitted", "age_days",
               "urgency_wording", "tentative_theme", "po_candidates", "invoice_candidates"]
    if st.checkbox("Include request detail in review table", value=False):
        columns.append("Request Detail")
    queue = queue.sort_values(["age_days", "Date Submitted"], ascending=[False, True], na_position="last")
    show = export_frame(queue[columns]).rename(columns={"age_days": "Active age (days)", "urgency_wording": "Urgency wording detected", "tentative_theme": "Tentative theme", "po_candidates": "PO candidates", "invoice_candidates": "Invoice candidates"})
    st.write(f"{len(show):,} tickets in this review queue")
    st.dataframe(show, hide_index=True, width="stretch")
    download_csv("Download ticket review queue", queue, "ticket_review_queue.csv", "download_queue")

with review:
    st.subheader("Capture the business interpretation and next step")
    st.caption("Comments remain in this browser session. Download the review log to keep or share it; this local dashboard does not synchronize comments across users.")
    notes_key = "notes_" + dataset_hash
    if notes_key not in st.session_state:
        st.session_state[notes_key] = []
    with st.form("business_review_form", clear_on_submit=True):
        topic = st.selectbox("Topic", ["Demand / request category", "Active or aged tickets", "Supplier follow-up", "Reference information", "Process improvement", "Data quality"])
        ticket_or_category = st.text_input("Ticket number or business category (optional)")
        observation = st.text_area("Business comment / interpretation")
        action = st.text_input("Proposed next action")
        owner = st.text_input("Owner / reviewer")
        decision = st.selectbox("Review status", ["Needs validation", "Action agreed", "Monitor", "No action required"])
        submitted = st.form_submit_button("Add business comment")
    if submitted:
        if not observation.strip():
            st.warning("Enter a business comment before adding the review item.")
        else:
            st.session_state[notes_key].append({"logged_at_utc": datetime.now(timezone.utc).isoformat(),
                "topic": topic, "ticket_or_category": ticket_or_category, "business_comment": observation,
                "next_action": action, "owner": owner, "review_status": decision,
                "filter_context": json.dumps(context), "metric_snapshot": json.dumps(m)})
            st.success("Comment added. Download the review log to retain it.")
    comments = pd.DataFrame(st.session_state[notes_key])
    if not comments.empty:
        st.dataframe(comments.drop(columns=["filter_context", "metric_snapshot"]), hide_index=True, width="stretch")
        download_csv("Download business review log", comments, "business_review_comments.csv", "download_comments")

with dq:
    st.subheader("Can we trust the interpretation?")
    st.caption("Quality checks cover the entire selected worksheet before dashboard filters. Counts may overlap. Missing request type, sub-type, commodity, and unfamiliar statuses are retained.")
    st.dataframe(pd.DataFrame(quality.items(), columns=["Check", "Value"]).astype(str), hide_index=True, width="stretch")
    st.caption("Repeated ticket IDs: retain the row with latest valid Last Edit Date, then Date Submitted, then source row. Conflicting creation dates and all repeated rows are exported for reconciliation. This rule should be confirmed with the source owner.")
    with st.expander("Metric definitions and boundaries", expanded=True):
        st.markdown("""
        | Metric | Definition / interpretation |
        |---|---|
        | Tickets received | Unique ticket IDs with a valid submission date in the selected window; all sidebar filters apply. |
        | Active in this cohort | Current status Open, In Progress, or Pending Supplier Response. This is the observed cohort's remaining work. |
        | Waiting for supplier | Current Pending Supplier Response count; included within active count. |
        | Closed / resolved share | Current Closed or Resolved count divided by all tickets in view. Other / unknown statuses stay in the denominator. |
        | Aged active tickets | Active tickets whose calendar age at the confirmed export date is strictly greater than the selected review threshold. Not an SLA breach. |
        | Completion days | Elapsed calendar days from submission to the explicitly validated completion field, for currently completed tickets with nonnegative timing. Shows median, P90, and sample coverage. |
        | Reference coverage | Distinct tickets with text-detected PO / invoice candidate references. No validation against SAP or attachments. |
        | Repeated reference | Number of distinct ticket IDs mentioning the same candidate reference; multiple mentions inside one ticket count once. |
        | Status lookup review | A rule-generated list for manual process discovery. No automation-readiness percentage or savings claim. |

        **Not measurable from the supplied fields alone:** true historical backlog, closures per period,
        first-response time, time spent waiting in each status, reopen rate, verified root cause, SLA compliance,
        supplier identity, invoice value / dollars at risk, handling effort, or automation savings.

        Status and date semantics need source-owner confirmation. Text dates are interpreted month/day;
        source timestamps are assumed to use one business timezone. All age and duration metrics use calendar days.
        """)
    proxy = view["closed_last_update_proxy_days"].dropna()
    if len(proxy):
        st.caption(f"Diagnostic only: median submission-to-last-update span for Closed / Resolved tickets is {proxy.median():.1f} days (n={len(proxy):,}). This is not resolution time and is excluded from business KPIs.")
    download_csv("Download all unique prepared tickets", base, "all_unique_prepared_tickets.csv", "download_master")
    download_csv("Download selected cohort", view, "selected_ticket_cohort.csv", "download_cohort")
    download_csv("Download rows missing ticket IDs", rejected, "rows_missing_ticket_ids.csv", "download_rejected")
    download_csv("Download repeated-ID audit", duplicates, "repeated_ticket_id_audit.csv", "download_duplicates")
    st.download_button("Download metrics and filter context", json.dumps({"context": context, "metrics": m, "quality": quality}, indent=2),
                       file_name="business_metrics.json", mime="application/json")
