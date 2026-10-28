
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
