    completed = int(df["Business Status"].eq("Closed / resolved").sum())
    waiting = int(df["Waiting for Supplier"].sum())
    unknown_status = total - active - completed
    dated = df.dropna(subset=["Date Submitted"]).copy()
    period = (f"{dated['Date Submitted'].min():%b %d, %Y} to {dated['Date Submitted'].max():%b %d, %Y}"
              if len(dated) else "Submission dates unavailable")
    coverage = f"{dated['Date Submitted'].dt.to_period('M').nunique()} calendar months present" if len(dated) else "No valid submission dates"

    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.spines.top": False,
                         "axes.spines.right": False, "axes.edgecolor": "#dddddd"})
    fig = plt.figure(figsize=(17, 15), facecolor="white")
    fig.text(.06, .967, "FootPrints | Business review", fontsize=25, fontweight="bold", color="#203345")
    fig.text(.06, .941, f"Tickets submitted: {period}  |  {coverage}", fontsize=11, color="#536270")
    cards = [("TICKETS IN EXTRACT", total, "Unique ticket numbers"),
             ("STILL ACTIVE", active, f"{active / total:.0%} of all tickets"),
             ("CLOSED / RESOLVED", completed, f"{completed / total:.0%} of all tickets"),
             ("WAITING FOR SUPPLIER", waiting, "Included in active count")]
    for x, (title, value, note) in zip([.06, .29, .52, .75], cards):
        fig.text(x, .9, title, fontsize=10, color="#536270", fontweight="bold")
        fig.text(x, .866, f"{value:,}", fontsize=29, color=TEAL if title == "CLOSED / RESOLVED" else BLUE, fontweight="bold")
        fig.text(x, .846, note, fontsize=10, color="#536270")
    grid = fig.add_gridspec(3, 2, left=.19, right=.94, top=.80, bottom=.10, wspace=.88, hspace=.72)
    axes = [fig.add_subplot(grid[i, j]) for i in range(3) for j in range(2)]

    ax = axes[0]
    ax.set_title("1. How much work arrived each week?", loc="left", fontsize=12, fontweight="bold", pad=14)
    if len(dated):
        submitted = dated["Date Submitted"].dt.normalize()
        monday = submitted - pd.to_timedelta(submitted.dt.weekday, unit="D")
        weeks = pd.date_range(monday.min(), monday.max(), freq="W-MON")
        counts = monday.value_counts().reindex(weeks, fill_value=0)
        partial = [(w < submitted.min()) or (w + pd.Timedelta(days=6) > submitted.max()) for w in weeks]
        ax.bar(range(len(weeks)), counts.values, color=["#aec8d9" if p else BLUE for p in partial])
        step = max(1, int(np.ceil(len(weeks) / 7)))
        ax.set_xticks(range(0, len(weeks), step), [w.strftime("%b %d") for w in weeks[::step]], rotation=25)
        ax.set_ylabel("Tickets received")
        ax.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
        ax.grid(axis="y", alpha=.15)
    else:
        ax.text(.5, .5, "No valid submission dates", ha="center", transform=ax.transAxes)
    horizontal(axes[1], df["Business Status"].value_counts().sort_values(), "2. What is the current state of the work?", denominator=total)
    horizontal(axes[2], top_counts(df["Request Type"]), "3. What are people asking for?")
    horizontal(axes[3], top_counts(df["Request Sub-Type"]), "4. What specific help do they need?")
    horizontal(axes[4], top_counts(df["Commodity Group"]), "5. Which commodities generate the most work?")
    horizontal(axes[5], top_counts(df.loc[df["Is Active"], "Request Type"]), "6. Which requests still need attention?", color=AMBER)
    footer = ("Status is current at export. Active work covers this extract only; older tickets may be missing.\n"
              "Light weekly bars indicate partial observed weeks. Missing dates are excluded from the weekly chart only.\n"
              f"Data checks: {quality['Rows without ticket number']:,} rows without IDs excluded; "
              f"{quality['Repeated ticket rows consolidated']:,} repeated rows consolidated; "
              f"{quality['Tickets without submission date']:,} tickets lack submission dates; "
              f"{unknown_status:,} tickets have other / unknown statuses.")
    fig.text(.06, .035, footer, fontsize=9, color="#536270", linespacing=1.6)
    fig.savefig(output / "business_dashboard.png", dpi=170, facecolor="white")
    plt.close(fig)

    request_table = category_table(df, "Request Type")
    commodity_table = category_table(df, "Commodity Group")
    top_request = request_table.loc[request_table["Request Type"].ne(UNKNOWN)]
    top_commodity = commodity_table.loc[commodity_table["Commodity Group"].ne(UNKNOWN)]
    request_sentence = (f"The largest recorded request category is **{top_request.iloc[0]['Request Type']}**, with **{top_request.iloc[0]['Tickets']:,} tickets** ({top_request.iloc[0]['Tickets'] / total:.1%} of the extract)." if len(top_request) else "Request types need to be recorded before the main demand driver can be identified.")
    commodity_sentence = (f"The highest-volume recorded commodity is **{top_commodity.iloc[0]['Commodity Group']}**, with **{top_commodity.iloc[0]['Tickets']:,} tickets**." if len(top_commodity) else "Commodity information is not recorded.")
    report = f"""# FootPrints business summary

Submission coverage: **{period}** ({coverage}). All counts use unique ticket numbers in the worksheet. Status reflects the export snapshot.

| Business measure | Result | Meaning |
|---|---:|---|
| Tickets in extract | {total:,} | Total unique tickets available for this review |
| Still active | {active:,} ({active / total:.1%}) | Open, In Progress, or Pending Supplier Response |
| Closed / resolved | {completed:,} ({completed / total:.1%}) | Completed share of this extract; not a monthly closure rate |
| Waiting for supplier | {waiting:,} | Subset of active tickets; do not add it to active count |
| Other / unknown status | {unknown_status:,} | Needs source-owner confirmation |

## What the data says

- {request_sentence}
- {commodity_sentence}
- **{active:,} tickets remain active** in the extract. This is the work to inspect for ownership and next steps.
- **{waiting:,} tickets are waiting for supplier response.** Review what information is needed and who should follow up. Status alone does not establish how long they have waited.

## Business discussion

1. For the highest-volume requests, what generates repeat contact? Review ticket examples before assigning a cause.
2. For active tickets, is there a clear owner, next action, and target follow-up date?
3. For supplier-dependent tickets, could clearer instructions or required intake information reduce follow-ups?

## Confidence in the results

- Source rows: {quality['Source rows']:,}; rows without ticket numbers excluded: {quality['Rows without ticket number']:,}; repeated-ID rows consolidated: {quality['Repeated ticket rows consolidated']:,}.
- Missing submission date: {quality['Tickets without submission date']:,}; missing request type: {quality['Tickets without request type']:,}; missing sub-type: {quality['Tickets without request sub-type']:,}; missing commodity: {quality['Tickets without commodity']:,}.
- Missing categories stay visible. Repeated IDs retain the latest Last Edit Date, then Date Submitted; tied records use the last source row. Confirm this consolidation rule and status mapping with the source owner.
- Last Edit Date does not establish resolution time. This report does not estimate SLA compliance, financial impact, root cause, or automation savings.
- A three-month extract describes observed workload; it does not establish seasonality or include all older unresolved tickets. First and last weeks may be incomplete. No missing weeks or source completeness can be confirmed from ticket dates alone.

## Capture business feedback

| Observation / ticket example | Business explanation | Agreed next action | Owner |
|---|---|---|---|
|  |  |  |  |
"""
    (output / "business_summary.md").write_text(report, encoding="utf-8")
    safe_csv(request_table, output / "request_type_summary.csv")
    # Keep the useful ticket-level context for checking examples and classifications.
    safe_csv(df, output / "ticket_detail.csv")
    print(f"Created {output / 'business_dashboard.png'}")
    print(f"Created {output / 'business_summary.md'}")
    print(f"Created {output / 'request_type_summary.csv'}")
    print(f"Created {output / 'ticket_detail.csv'}")
    print(f"Total unique tickets: {total:,} | Active: {active:,} | Completed: {completed:,} | Waiting: {waiting:,}")
    return df, quality


if __name__ == "__main__":
    run_business_eda()
