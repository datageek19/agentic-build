# Payment ticket analysis - summary tables

Source file: `Invoice FP Tickets Jan thru Oct.xlsx`, sheet `report-1`  
Generated: 28 Oct 2026 09:14  
Reporting window: **13 Jul 2026 to 13 Oct 2026**  
Full extract covers: 02 Jan 2026 to 13 Oct 2026  
Usable tickets after exclusions: **895**

Two row-level files are written separately as CSV, because they are meant to be filtered and filled in: `ticket_audit_log.csv` and `extraction_sample_for_manual_review.csv`.

## Contents

- [01 Column Profile](#01-column-profile) - Every column: populated count and %, distinct values, quality notes
- [02 Data Quality](#02-data-quality) - Named integrity checks with rows affected and the action taken
- [03 Row Waterfall](#03-row-waterfall) - Raw rows to final rows, one line per exclusion rule
- [04 Metrics](#04-metrics) - Every metric with its value, calculation, denominator and validation status
- [05 Monthly Volume](#05-monthly-volume) - Tickets per month, payment related vs other
- [06 Status Mix](#06-status-mix) - Ticket status breakdown for the reporting window
- [07 Request Type](#07-request-type) - Top 10 Request Type values
- [08 Request Sub-Type](#08-request-sub-type) - Top 10 Request Sub-Type values
- [09 Commodity Group](#09-commodity-group) - Top 10 Commodity Group values
- [10 Payment Evidence](#10-payment-evidence) - How each payment ticket was identified: category, free text, or both
- [11 Identifiers](#11-identifiers) - Whether a PO or invoice reference was found in the ticket text
- [12 Action Intent](#12-action-intent) - What the requester is asking for
- [13 Handling Tiers](#13-handling-tiers) - How payment tickets would route under an agentic workflow
- [14 Duration Bands](#14-duration-bands) - Closed payment tickets grouped by elapsed days (proxy measure)
- [15 Requester Domains](#15-requester-domains) - Top external email domains submitting payment tickets
- [16 Repeat POs](#16-repeat-pos) - PO numbers appearing in more than one ticket
- [17 Repeat Invoices](#17-repeat-invoices) - Invoice references appearing in more than one ticket
- [18 Frequent Terms](#18-frequent-terms) - Most frequent words in Request Detail, boilerplate removed
- [20 Sampling Plan](#20-sampling-plan) - Sample sizes needed to put an error bar on the payment classification
- [21 Sample Size Options](#21-sample-size-options) - Required sample size at other confidence levels and margins of error
- [22 Estimates+Intervals](#22-estimates+intervals) - Which figures are a census, which are estimates, and their intervals

## 01 Column Profile

_Every column: populated count and %, distinct values, quality notes_

| Column | Type | Populated (rows) | Populated % | Blank / missing (rows) | Distinct values | Most common value | Most common % of populated | Range / examples | Quality note | In verified schema |
|---|---|---|---|---|---|---|---|---|---|---|
| Ticket Number | text | 895 | 100.0 | 0 | 895 | 396813 | 0.1 | 396813 \| 396836 \| 396853 |  | yes |
| Status | text | 895 | 100.0 | 0 | 7 | Closed | 96.9 | Closed \| Pending Customer Response \| Pending Customer Action |  | yes |
| Subject (Title) | text | 895 | 100.0 | 0 | 863 | Request to Cancel the invoice 135490-1  associated with  PO | 0.4 | PO: 7002662869 - Pay Invoice \| IR21270-481423 Help \| Int'l Supplier - PO 7001107885 Outstandi | 2 value(s) with leading/trailing spaces | yes |
| Last Edit Date | date | 895 | 100.0 | 0 | 212 |  |  | 2026-01-06 to 2026-10-15 |  | yes |
| Date Submitted | date | 895 | 100.0 | 0 | 201 |  |  | 2026-01-02 to 2026-10-13 |  | yes |
| Assignees | text | 895 | 100.0 | 0 | 14 | TCS Invoicing: | 20.8 | TCS Invoicing: EXI-Pawar, Khushi V \| TCS Invoicing: EXI-Mugale, Jitendra B \| TCS Invoicing: EXI-Shetty, Sujanya S |  | NO - unexpected column |
| Commodity Group | text | 871 | 97.3 | 24 | 26 | Factory Consumables | 22.0 | Factory Consumables \| Labor Services - Excludes Contract labor \| Consulting |  | yes |
| Request Type | text | 895 | 100.0 | 0 | 8 | Invoice Status / Payment Support | 50.7 | Invoice Status / Payment Support \| Ariba PO/PR/POC Support \| Miscellaneous |  | yes |
| Request Sub-Type | text | 895 | 100.0 | 0 | 23 | Ariba PO Pymnt Status | 46.6 | Ariba PO Pymnt Status \| Purchase Order Status \| Find Procurement Agent |  | yes |
| Email Address | text | 895 | 100.0 | 0 | 403 | pfolkes@precisetoolco.com | 3.1 | alexander.j.pfeifer@boeing.com \| justin.l'hote@boeing.com \| janine.bledsoe@adeccona.com |  | yes |
| Request Detail | text | 895 | 100.0 | 0 | 895 | Entered on 01/26/2026 at 10:25:18 AM PST (GMT-0800) by EXI-P | 0.1 | Entered on 01/26/2026 at 10:25:18 AM PST \| Entered on 01/16/2026 at 10:07:20 AM PST \| Entered on 01/16/2026 at 1:07:34 PM PST | 26 value(s) with leading/trailing spaces | yes |


## 02 Data Quality

_Named integrity checks with rows affected and the action taken_

| Check | Rows affected | % of raw rows | Why it matters | Action taken by this script |
|---|---|---|---|---|
| Ticket Number blank or placeholder | 0 | 0.0 | Row cannot be tied to a ticket; it cannot be counted or deduplicated. | Row excluded, counted in the waterfall. |
| Duplicate Ticket Number (0 ticket id(s) appear more than once) | 0 | 0.0 | FootPrints exports often carry one row per update. If so, every volume count can be inflated. | Deduplicated. |
| Request Type blank or placeholder | 0 | 0.0 | Missing category information may limit reporting. | Row excluded by the input filter. |
| Request Sub-Type blank or placeholder | 0 | 0.0 | Missing category information may limit reporting. | Request Type excluded; blank Request Sub-Type retained if Subject matches. |
| Status blank or placeholder | 0 | 0.0 | Open vs closed cannot be determined. | Row kept; reported as '(blank)'. |
| Status value outside the five known values (Pending Customer Action, Pending Customer Response, Responded To) | 25 | 2.8 | Unexpected statuses are reported and retained. | Row kept and reported. No status filter is applied. |
| Date Submitted missing or unparseable | 0 | 0.0 | Ticket cannot be placed in a reporting period. | Row excluded, counted in the waterfall. |
| Last Edit Date missing or unparseable | 0 | 0.0 | Duration cannot be computed for this ticket. | Excluded from duration metrics only; still counted in volume. |
| Last Edit Date earlier than Date Submitted | 0 | 0.0 | Negative elapsed time indicates a timestamp or export problem. | Excluded from duration metrics and reported here. |
| Request Detail blank | 0 | 0.0 | No free text for an agent to read. | Flagged and routed to manual review. |
| Request Detail shorter than 20 characters | 0 | 0.0 | Too little content to classify or investigate. | Flagged and routed to manual review. |
| Email Address blank | 0 | 0.0 | Requester cannot be attributed to an internal team or a supplier. | Reported as 'unknown'. |
| Email Address without an @domain | 0 | 0.0 | Domain parsing fails; supplier attribution is unreliable. | Reported as 'unknown'. |


## 03 Row Waterfall

_Raw rows to final rows, one line per exclusion rule_

| Step | Rows in | Rows removed | Of the removed, look payment related | Rows out | % of raw retained | Rule |
|---|---|---|---|---|---|---|
| 0. Raw rows read from the sheet | 895 | 0 | 0 | 895 | 100.0 | No filter. |
| 1. Drop rows with no Ticket Number | 895 | 0 | 0 | 895 | 100.0 | Ticket Number is blank or a placeholder. |
| 2. Deduplicate to one row per ticket | 895 | 0 | 0 | 895 | 100.0 | Keep the most recently edited row per Ticket Number. Removed rows are repeat updates of a ticket that is still counted, NOT lost tickets. |
| 3. Drop rows with no Request Type | 895 | 0 | 0 | 895 | 100.0 | Request Type must be populated; a blank Sub-Type may still have a matching Subject. |
| 4. Drop rows with no usable Date Submitted | 895 | 0 | 0 | 895 | 100.0 | Ticket cannot be placed in a reporting period. |
| 5. Status filter | 895 | 0 | 0 | 895 | 100.0 | NOT APPLIED. Unexpected statuses are reported in the quality checks. |
| 6. Outside the reporting window | 895 | 598 |  | 297 | 33.2 | Date Submitted outside 13 Jul 2026 to 13 Oct 2026. These are real tickets from other periods, not data problems. Set WINDOW_MONTHS to widen. |
| 7. Not classified as payment related | 297 | 0 |  | 297 | 33.2 | No payment term in Request Type / Sub-Type and none in Subject / Request Detail. See sheet 19 for what these tickets are, and the non-payment review … |


## 04 Metrics

_Every metric with its value, calculation, denominator and validation status_

| ID | Metric | Value | Calculation | Denominator | Denominator value | Scope | Validation |
|---|---|---|---|---|---|---|---|
| V1 | Tickets in reporting window | 297 | Count of rows surviving the exclusion ladder with Date Submitted inside the window. | Clean tickets, all periods |  | Window | Computed from the local file |
| V2 | Average tickets per month | 74.2 | Window ticket count divided by the number of calendar months present. | Months present in window | 4 | Window | Computed from the local file |
| V3 | Payment-related tickets | 297 | Request Type/Sub-Type matches a payment term, OR Subject/Request Detail matches a payment term (word-boundary match). | tickets in window | 297 | Window | Computed from the local file |
| V4 | Payment share of selected tickets | 100 | Payment tickets / tickets in window x 100. | tickets in window | 297 | Window | Computed from the local file |
| V5 | Payment tickets evidenced by category, not only text | 277 | Payment tickets whose Request Type or Sub-Type matches a payment term. | payment tickets | 297 | Payment tickets | Computed. The free-text-only group needs a manual sample check - Requires data validation |
| S1 | Payment tickets still open | 26 | Payment tickets whose Status is not Closed or Resolved. | payment tickets | 297 | Payment tickets | Computed from the local file |
| S2 | Open share of payment tickets | 8.8 | Open payment tickets / payment tickets x 100. | payment tickets | 297 | Payment tickets | Computed from the local file |
| R1 | Closed payment tickets with a usable duration | 271 | Closed/Resolved payment tickets where Last Edit Date >= Date Submitted and both parse. | closed payment tickets | 271 | Closed payment tickets | Computed from the local file |
| R2 | Median days from submission to last edit (closed) | 7 | Median of (Last Edit Date - Date Submitted) in days, closed tickets only. | closed payment tickets with a usable duration | 271 | Closed payment tickets | Proxy only. Last Edit Date is the last touch, not a resolution event - Requires data validation |
| R3 | 90th percentile days to last edit (closed) | 21 | 90th percentile of the same measure. | closed payment tickets with a usable duration | 271 | Closed payment tickets | Proxy only - Requires data validation |
| R4 | Median age of open payment tickets (days) | 7 | Median of (Last Edit Date - Date Submitted) for tickets not Closed/Resolved. | open payment tickets with a usable duration | 26 | Open payment tickets | Measures time to last touch, not time outstanding - Requires data validation |
| E1 | Payment tickets with a PO number | 241 | Subject or Request Detail contains a number matching a verified PO pattern. | payment tickets | 297 | Payment tickets | Yield only, not accuracy. Review the manual sample CSV - Requires data validation |
| E2 | PO numbers explicitly labelled 'PO' in the text | 210 | Subset of E1 where the number is preceded by a PO cue. | payment tickets with a PO number | 241 | Payment tickets | Computed from the local file |
| E3 | Payment tickets with an invoice reference | 94 | Prefix (INV/IR/IV/PMT/PAY/CHS/FLY) followed by at least 3 digits, or a V-number. | payment tickets | 297 | Payment tickets | Yield only, not accuracy - Requires data validation |
| E4 | Invoice references counted by the broad pattern | 76 | Broad identifier pattern; may match ordinary words without a document number. | payment tickets | 297 | Payment tickets | For comparison only; not a validated identifier count. |
| E5 | Payment tickets with neither identifier | 39 | No PO number and no invoice reference found in Subject or Request Detail. | payment tickets | 297 | Payment tickets | Computed from the local file |
| O1 | Payment tickets from an external supplier domain | 26 | Email domain is not in the internal-domain list and is not blank. | payment tickets | 297 | Payment tickets | Email domain is not a verified supplier identity - Requires data validation |
| O2 | Distinct requester domains | 8 | Distinct @domain values among payment tickets. | payment tickets | 297 | Payment tickets | Computed from the local file |
| TA | Tier A - Agent-ready candidate | 63 | See the tier rules printed in the dashboard. | payment tickets | 297 | Payment tickets | Rule-based estimate from text only - Requires data validation |
| TB | Tier B - Agent-assisted, needs a lookup | 54 | See the tier rules printed in the dashboard. | payment tickets | 297 | Payment tickets | Rule-based estimate from text only - Requires data validation |
| TC | Tier C - Manual review first | 180 | See the tier rules printed in the dashboard. | payment tickets | 297 | Payment tickets | Rule-based estimate from text only - Requires data validation |


## 05 Monthly Volume

_Tickets per month, payment related vs other_

| Submission Month | Payment related | Other | Total | Payment % |
|---|---|---|---|---|
| 2026-07 | 63 | 0 | 63 | 100.0 |
| 2026-08 | 98 | 0 | 98 | 100.0 |
| 2026-09 | 102 | 0 | 102 | 100.0 |
| 2026-10 | 34 | 0 | 34 | 100.0 |


## 06 Status Mix

_Ticket status breakdown for the reporting window_

| Status | Tickets | % of tickets in window |
|---|---|---|
| Closed | 270 | 90.9 |
| Pending Customer Response | 19 | 6.4 |
| Responded To | 4 | 1.3 |
| Pending Customer Action | 1 | 0.3 |
| Resolved | 1 | 0.3 |
| Pending Supplier Response | 1 | 0.3 |
| Open | 1 | 0.3 |


## 07 Request Type

_Top 10 Request Type values_

| Request Type | Tickets | % of tickets in window |
|---|---|---|
| Invoice Status / Payment Support | 142 | 47.8 |
| Ariba PO/PR/POC Support | 127 | 42.8 |
| Ariba System Issue/Support | 21 | 7.1 |
| Miscellaneous | 3 | 1.0 |
| Purchasing Cards | 2 | 0.7 |
| Ariba Supplier Activations/Changes/Support | 2 | 0.7 |


## 08 Request Sub-Type

_Top 10 Request Sub-Type values_

| Request Sub-Type | Tickets | % of tickets in window |
|---|---|---|
| Ariba PO Pymnt Status | 131 | 44.1 |
| Other PO Changes | 77 | 25.9 |
| Purchase Order Status | 23 | 7.7 |
| Urgent POC Requests | 19 | 6.4 |
| Receipt Support | 8 | 2.7 |
| Expedite Request | 7 | 2.4 |
| BCMS Invoice Pymnt Status | 7 | 2.4 |
| PO Support | 6 | 2.0 |
| Payment Terms | 4 | 1.3 |
| Find Procurement Agent | 3 | 1.0 |
| All other values | 12 | 4.0 |


## 09 Commodity Group

_Top 10 Commodity Group values_

| Commodity Group | Tickets | % of tickets in window |
|---|---|---|
| Factory Consumables | 57 | 19.2 |
| Capital Equipment | 39 | 13.1 |
| Construction Mgmt (Labor) | 30 | 10.1 |
| Building/Facility - Maintenance/Repair | 28 | 9.4 |
| IT Application Services | 19 | 6.4 |
| Hardware | 17 | 5.7 |
| Tooling | 11 | 3.7 |
| (blank) | 11 | 3.7 |
| Site Services BSC Only | 10 | 3.4 |
| Software | 10 | 3.4 |
| All other values | 65 | 21.9 |


## 10 Payment Evidence

_How each payment ticket was identified: category, free text, or both_

| Payment evidence | Tickets | % of payment tickets |
|---|---|---|
| Category and text agree (strongest) | 277 | 93.3 |
| Free text only (weakest - verify) | 20 | 6.7 |


## 11 Identifiers

_Whether a PO or invoice reference was found in the ticket text_

| Identifier found in the ticket text | Tickets | % of payment tickets |
|---|---|---|
| PO only | 164 | 55.2 |
| Both PO and invoice reference | 77 | 25.9 |
| Neither - agent must ask the requester | 39 | 13.1 |
| Invoice reference only | 17 | 5.7 |


## 12 Action Intent

_What the requester is asking for_

| Action Intent | Tickets | % of payment tickets |
|---|---|---|
| Reject Request | 145 | 48.8 |
| Unclassified / General Support | 88 | 29.6 |
| Payment Status Query | 42 | 14.1 |
| Cancellation Request | 13 | 4.4 |
| Expedite Request | 9 | 3.0 |


## 13 Handling Tiers

_How payment tickets would route under an agentic workflow_

| Handling Tier | Tickets | % of payment tickets |
|---|---|---|
| A - Agent-ready candidate | 63 | 21.2 |
| B - Agent-assisted, needs a lookup | 54 | 18.2 |
| C - Manual review first | 180 | 60.6 |


## 14 Duration Bands

_Closed payment tickets grouped by elapsed days (proxy measure)_

| Days from submission to last edit | Tickets | % of closed payment tickets with a usable duration |
|---|---|---|
| 0-1 days | 20 | 7.4 |
| 2-5 days | 77 | 28.4 |
| 6-10 days | 92 | 33.9 |
| 11-20 days | 52 | 19.2 |
| 21+ days | 30 | 11.1 |


## 15 Requester Domains

_Top external email domains submitting payment tickets_

| External requester domain | Tickets | % of external payment tickets |
|---|---|---|
| precisetoolco.com | 18 | 69.2 |
| apacheaerospace.com | 2 | 7.7 |
| parpasamerica.com | 2 | 7.7 |
| copeplastics.com | 1 | 3.8 |
| msn.com | 1 | 3.8 |
| blackhawkid.com | 1 | 3.8 |
| swagelok.com | 1 | 3.8 |


## 16 Repeat POs

_PO numbers appearing in more than one ticket_

| PO Number | Tickets |
|---|---|
| 7003199576 | 3 |
| 7003425648 | 3 |
| 7003382587 | 2 |
| 7003111892 | 2 |
| 7003318918 | 2 |
| 7001652758 | 2 |
| 7003303665 | 2 |
| 7003086103 | 2 |
| 7002879367 | 2 |
| 7001774250 | 2 |


## 17 Repeat Invoices

_Invoice references appearing in more than one ticket_

| Invoice Reference | Tickets |
|---|---|
| INV37939 | 2 |
| INV116269 | 1 |
| INV5332108634 | 1 |
| IR103SR00491-5243874 | 1 |
| INV176494 | 1 |
| INV4222001-313-7 | 1 |
| IR2511111E-4956232 | 1 |
| INV26000056 | 1 |
| IR38126-5288696 | 1 |
| INV116552 | 1 |


## 18 Frequent Terms

_Most frequent words in Request Detail, boilerplate removed_

| Term | Occurrences |
|---|---|
| invoice | 2,094 |
| entered | 1,792 |
| gmt | 1,781 |
| pdt | 1,780 |
| level | 1,673 |
| escalation | 1,447 |
| exi | 1,252 |
| queries | 1,186 |
| process | 1,172 |
| sushant | 1,170 |
| dumbre | 1,161 |
| suma | 1,123 |
| exchange | 1,114 |
| payment | 745 |
| services | 706 |


## 20 Sampling Plan

_Sample sizes needed to put an error bar on the payment classification_

| Population | Population size | Confidence level | Target margin of error | Required sample size | Sample drawn by this run | Margin of error at that sample (worst case) | What it measures |
|---|---|---|---|---|---|---|---|
| Flagged as payment related | 297 | 95% | +/- 5% | 168 | 168 | 5 | Precision: of the tickets the rule flagged, what share really are payment related |


## 21 Sample Size Options

_Required sample size at other confidence levels and margins of error_

| Population | Population size | Confidence level | Margin of error | Rows a reviewer must check |
|---|---|---|---|---|
| Flagged as payment related | 297 | 90% | +/- 10.0% | 56 |
| Flagged as payment related | 297 | 90% | +/- 7.5% | 86 |
| Flagged as payment related | 297 | 90% | +/- 5.0% | 142 |
| Flagged as payment related | 297 | 95% | +/- 10.0% | 73 |
| Flagged as payment related | 297 | 95% | +/- 7.5% | 109 |
| Flagged as payment related | 297 | 95% | +/- 5.0% | 168 |
| Flagged as payment related | 297 | 99% | +/- 10.0% | 107 |
| Flagged as payment related | 297 | 99% | +/- 7.5% | 149 |
| Flagged as payment related | 297 | 99% | +/- 5.0% | 206 |


## 22 Estimates+Intervals

_Which figures are a census, which are estimates, and their intervals_

| Figure | Value | Basis | Lower | Upper | Note |
|---|---|---|---|---|---|
| Tickets in the reporting window | 297 | Census |  |  | Census of every ticket in the window - there is no sampling error. The uncertainty is whether the rule classified correctly, which is measured by the… |
| Flagged as payment related | 297 | Census, rule-based |  |  | Census of every ticket in the window - there is no sampling error. The uncertainty is whether the rule classified correctly, which is measured by the… |
| Payment share of selected window (%) | 100 | Census, rule-based |  |  | Census of every ticket in the window - there is no sampling error. The uncertainty is whether the rule classified correctly, which is measured by the… |
| Rule precision (%) |  | Not yet measured |  |  | Review the selected-ticket sample to estimate precision; this does not measure misses outside the input filter. |
| Adjusted payment tickets (estimate) |  | Not yet measured |  |  | An estimate for the full source requires reviewing excluded tickets. |


## Charts

**Is this workload growing, flat or seasonal?**

![Is this workload growing, flat or seasonal?](charts/chart_01_monthly_volume.png)

**How much of the workload is still in flight?**

![How much of the workload is still in flight?](charts/chart_02_status_mix.png)

**Which request categories actually dominate?**

![Which request categories actually dominate?](charts/chart_03_request_type.png)

**Can an agent look a payment up without asking the requester?**

![Can an agent look a payment up without asking the requester?](charts/chart_04_identifier_availability.png)

**What share of payment tickets could be handled end to end?**

![What share of payment tickets could be handled end to end?](charts/chart_05_handling_tiers.png)

**How long do closed tickets take before the last update?**

![How long do closed tickets take before the last update?](charts/chart_06_duration_bands.png)
