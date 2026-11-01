# Payment ticket analysis - stakeholder summary

Source file: `Invoice FP Tickets Jan thru Oct.xlsx`, sheet `report-1`
Scope: six-digit Ticket Number, populated Request Type, and payment terms in Request Sub-Type or Subject (Title). Results below refer to this subset.
Source rows before scope filter: 4,348; rows with populated Type and Sub-Type before payment filter: 923.
Because payment terms define the input scope, the payment-classification share below does not estimate the payment share of the full export. The non-payment review cannot detect payment tickets excluded at input.
Generated: 28 Oct 2026 09:14
Reporting window: **13 Jul 2026 to 13 Oct 2026** (3 months, anchored on `data_max`)
Full extract covers: 02 Jan 2026 to 13 Oct 2026

## 1. What the data contains

- **895** rows were read from the sheet; **895** (100.0%) are usable tickets after exclusions.
- **0** rows shared a Ticket Number with another row. No duplicate ticket numbers were found.
- **25** rows carry a Status outside the five expected values. These rows are retained.
- **297** tickets fall inside the reporting window (33.2% of all usable tickets).

### How the raw row count becomes the payment ticket count

| Step | Rows in | Removed | Of those, look payment related | Rows out |
|---|---|---|---|---|
| 0. Raw rows read from the sheet | 895 | 0 | 0 | 895 |
| 1. Drop rows with no Ticket Number | 895 | 0 | 0 | 895 |
| 2. Deduplicate to one row per ticket | 895 | 0 | 0 | 895 |
| 3. Drop rows with no Request Type | 895 | 0 | 0 | 895 |
| 4. Drop rows with no usable Date Submitted | 895 | 0 | 0 | 895 |
| 5. Status filter | 895 | 0 | 0 | 895 |
| 6. Outside the reporting window | 895 | 598 |  | 297 |
| 7. Not classified as payment related | 297 | 0 |  | 297 |

Read this table before reacting to the payment ticket count. Most of the reduction is usually steps 2 and 6, and neither is a data loss: step 2 collapses repeat updates of the same ticket, and step 6 simply excludes tickets from outside the reporting window. The column to watch is **of those, look payment related** on step 3 - if it is large, the Request Sub-Type completeness rule is discarding genuine payment tickets and should be relaxed.

Full column-by-column detail is in the workbook `footprints_eda_tables.xlsx`, sheet **01 Column Profile**; the exclusion trail is on sheet **03 Row Waterfall**.

## 2. Headline volumes

| Measure | Value | Denominator |
|---|---|---|
| Tickets in window | 297 | all usable tickets (895) |
| Average per month | 74.2 | months in window |
| Payment-related tickets | 297 | tickets in window (297) |
| Payment classification within selected tickets | 100.0% | selected tickets in window |
| Payment tickets still open | 26 | payment tickets (297) |

Of the 297 payment tickets, 277 (93.3%) are identified by Request Type or Sub-Type. The remainder were matched on free text alone and should be sampled before the figure is quoted externally. **Requires data validation.**

## 3. Could an agent act on these tickets?

- A PO number was found in **241** payment tickets (81.1%); an invoice reference in **94** (31.6%).
- **77** tickets contain both; **39** (13.1%) contain neither, so an agent would have to go back to the requester before it could look anything up.

Tier breakdown (sheet **13 Handling Tiers**):

| Tier | Tickets | % of payment tickets |
|---|---|---|
| A - Agent-ready candidate | 63 | 21.2% |
| B - Agent-assisted, needs a lookup | 54 | 18.2% |
| C - Manual review first | 180 | 60.6% |

These tiers are inferred from ticket text only. They indicate where to look first, not what an agent will achieve. **Requires data validation.**

## 4. How long tickets take

Median elapsed days from submission to last edit, closed payment tickets only: **7.0** days (n=271). 90th percentile: 21.0 days.

`Last Edit Date` records the last change to the ticket, not a resolution event. For an open ticket it is simply the last time somebody touched it. Treat this as a rough elapsed-time proxy until a true closure timestamp is available. **Requires data validation.**

## 5. How confident can you be in the payment count?

The payment flag is applied to all 297 selected tickets in the window. A review sample checks whether flagged tickets are genuinely payment related. Tickets excluded at input are outside this sample:

| Population | Size | Rows to review | Confidence | Margin of error |
|---|---|---|---|---|
| Flagged as payment related | 297 | 168 | 95% | +/- 5.0% |

Review the flagged-ticket sample in `extraction_sample_for_manual_review_REVIEWED.csv` to estimate the rule's precision. To measure missed payment tickets, sample records excluded by the input filter separately; this report does not estimate that rate.

Sheet 21 lists the review effort needed at other confidence levels - relaxing to 90% confidence and +/- 10% cuts the reading substantially.


## 6. What this analysis cannot tell you

- **Ownership and workload by team.** The verified schema has no assignee, queue or resolver-group column, so tickets cannot be attributed to a team.
- **Financial exposure.** There is no amount, currency, due date or payment-terms column. No value can be attached to the backlog.
- **System linkage.** `Ticket Number` is a FootPrints key. Nothing in this export joins to SAP S/4HANA or Ariba document numbers, supplier IDs or PO line items. Extracted PO and invoice strings are text found in a message, not validated keys.
- **Extraction accuracy.** The yields above count matches, not correct matches. `extraction_sample_for_manual_review.csv` is a blank review sheet for that check.
- **Whether a ticket was actually resolved correctly.** No outcome or resolution-code field exists.

## 7. Suggested next step

Review the payment-ticket sample to check keyword accuracy. To estimate the payment share of the full extract, also review tickets excluded by the initial filter; those tickets are outside this report.
