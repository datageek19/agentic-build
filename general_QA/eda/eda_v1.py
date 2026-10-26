# eda-v1
# footprint sample data - EDA

import os
import re
from collections import Counter
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

# --- CONFIGURATION ---
EXCEL_FILE = "C:/Users/qw597f/footprint_data_eda/Invoice FP Tickets Jan thru Oct.xlsx"  
SHEET_NAME = "report-1"                    

def run_consolidated_pipeline():
    if not os.path.exists(EXCEL_FILE):
        raise FileNotFoundError(f"Target file '{EXCEL_FILE}' not found.")

    # Create isolated output folders
    os.makedirs('data', exist_ok=True)
    os.makedirs('images', exist_ok=True)

    print(" [1/4] Ingesting dataset and clearing structural blanks...")
    df = pd.read_excel(EXCEL_FILE, sheet_name=SHEET_NAME, engine='openpyxl')
    df.columns = df.columns.str.strip()
    print(f"Total raw records in Excel before pipeline processing: {len(df)}")

    # Clean text fields
    string_cols = ['Ticket Number', 'Status', 'Commodity Group', 'Request Type', 'Request Sub-Type', 
                   'Subject (Title)', 'Request Detail', 'Email Address']
    for col in string_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    # --- STAGE 1: BLANK FILTERING ---
    invalid_nulls = ['nan', 'none', 'null', '']
    df = df[
        df['Ticket Number'].notna() & (~df['Ticket Number'].str.lower().isin(invalid_nulls)) &
        df['Request Type'].notna() & (~df['Request Type'].str.lower().isin(invalid_nulls)) &
        df['Request Sub-Type'].notna() & (~df['Request Sub-Type'].str.lower().isin(invalid_nulls))
    ]

    valid_statuses = ['Closed', 'Resolved', 'Open', 'Pending Supplier Response', 'In Progress']
    df = df[df['Status'].isin(valid_statuses)]

    df['Date Submitted'] = pd.to_datetime(df['Date Submitted'], errors='coerce')
    df['Last Edit Date'] = pd.to_datetime(df['Last Edit Date'], errors='coerce')
    df = df.dropna(subset=['Date Submitted']).sort_values('Date Submitted')
    
    df['Days to Resolution'] = (df['Last Edit Date'] - df['Date Submitted']).dt.days
    df['Days to Resolution'] = df['Days to Resolution'].clip(lower=0) 
    df['Year_Month'] = df['Date Submitted'].dt.to_period('M')

    # --- STAGE 2: FILTER FINANCIAL TRACKING ROWS ---
    payment_keywords = ['invoice', 'payment', 'paid', 'remittance', 'po', 'pr', 'p-card', 'card', 'wire']
    payment_mask = (
        df['Request Type'].str.contains('Invoice|Payment|PO|PR|Card', case=False, na=False) |
        df['Request Detail'].str.contains('|'.join(payment_keywords), case=False, na=False)
    )
    pay_df = df[payment_mask].copy()

    # --- STAGE 3: FIELD CONTEXTUAL TEXT MINING ---
    print(" [2/4] Cross-mining columns to map values per Ticket Number...")
    
    # patterns matching (e.g., PO#, PO #, INV, IR, PAY, CHS, V000)
    po_pattern = r'\b(?:po[#\s:]*)(700\d{7}|701\d{7}|702\d{6,7}|703\d{6,7})\b|\b(700\d{7}|701\d{7}|702\d{6,7}|703\d{6,7})\b'
    invoice_pattern = r'\b(INV|IR|PAY|PMT|CHS|IV|FLY|V\d{3,})\b\s*[#\s:]*\d*[-_\w]*'

    def extract_field_metadata(row):
        title = str(row['Subject (Title)'])
        detail = str(row['Request Detail'])
        
        # 1. PO Extraction
        po_match = re.search(po_pattern, title, re.IGNORECASE)
        if not po_match:
            po_match = re.search(po_pattern, detail, re.IGNORECASE)
        
        extracted_po = 'No PO Found'
        if po_match:
            groups = [g for g in po_match.groups() if g]
            if groups: extracted_po = groups[0]
        
        # 2. Invoice ID Extraction 
        inv_match = re.search(invoice_pattern, title, re.IGNORECASE)
        if not inv_match:
            inv_match = re.search(invoice_pattern, detail, re.IGNORECASE)
        extracted_inv = inv_match.group(0).upper() if inv_match else 'No Invoice Found'
        
        # 3. Categorize Action Intent
        combined_text = (title + " " + detail).lower()
        if 'reject' in combined_text: intent = 'Reject Request'
        elif any(x in combined_text for x in ['cancel', 'void', 'delete']): intent = 'Cancellation Request'
        elif any(x in combined_text for x in ['expedite', 'urgent']): intent = 'Expedite Request'
        elif any(x in combined_text for x in ['status', 'unpaid', 'past due', 'late']): intent = 'Payment Status Query'
        else: intent = 'General Operations Support'
        
        return pd.Series([extracted_po, extracted_inv, intent])

    pay_df[['Extracted_PO', 'Extracted_Invoice', 'Action_Intent']] = pay_df.apply(extract_field_metadata, axis=1)

    # 4. AUTOMATION STRATIFICATION
    urgent_terms = ['urgent', 'escalat', 'block', 'stop', 'late', 'critical', 'delay', 'overdue', 'warning']
    pay_df['Is_Urgent'] = pay_df['Request Detail'].str.contains('|'.join(urgent_terms), case=False, na=False).astype(int)

    def extract_domain(email):
        match = re.search(r'@([\w.-]+)', str(email).lower())
        return match.group(1) if match else 'unknown.com'
    pay_df['Supplier_Domain'] = pay_df['Email Address'].apply(extract_domain)
    
    internal_domains = ['boeing.com', 'ariba.com', 'internal', 'nan', 'unknown.com']
    pay_df['Is_External'] = (~pay_df['Supplier_Domain'].isin(internal_domains)).astype(int)

    pay_df['Text_Length'] = pay_df['Request Detail'].apply(len)
    def assign_tier(row):
        if row['Text_Length'] < 120 and row['Days to Resolution'] <= 2:
            return 'Tier 1: RPA Bot Candidate (Short Text/Fast)'
        elif row['Is_Urgent'] == 1:
            return 'Tier 3: Manual Triage (High Urgency)'
        else:
            return 'Tier 2: NLP Agent routing (Standard Text)'
    pay_df['Automation_Category'] = pay_df.apply(assign_tier, axis=1)

    # Clean text words
    stop_words = {
        'the', 'and', 'to', 'of', 'for', 'in', 'a', 'is', 'it', 'you', 'this', 'that', 'with', 'on', 'please', 
        'from', 'we', 'have', 'be', 'are', 'as', 'at', 'i', 'our', 'hi', 'team', 'am', 'your', 'can', 'or', 'an',
        'ariba', 'boeing', 'ticket', 'support', 'hello', 'dear', 'com', 'thanks', 'thank', 'regards', 'help', 'need'
    }
    all_words = []
    for txt in pay_df['Request Detail']:
        words = re.findall(r'\b[a-zA-Z]{3,}\b', txt.lower())
        all_words.extend([w for w in words if w not in stop_words])
    
    # 5. MASTER TICKET DATAFRAME WITH EXTRACTED PO/INV number
    # Groups extraction metrics explicitly per ticket record
    ticket_audit_log = pay_df[['Ticket Number', 'Subject (Title)', 'Extracted_PO', 'Extracted_Invoice', 'Action_Intent', 'Status', 'Days to Resolution']].copy()

    # --- STAGE 4: OUTPUT SUMMARY DATAFRAMES ---
    tables = {
        'ticket_master_audit_log': ticket_audit_log, # Contains granular extracted values per ticket
        'automation_readiness': (pay_df['Automation_Category'].value_counts(normalize=True) * 100).round(2).to_frame(name='Pct Share'),
        'top_suppliers': pay_df[pay_df['Is_External'] == 1]['Supplier_Domain'].value_counts().head(5).to_frame(name='Ticket Volume'),
        'root_cause_keywords': pd.DataFrame(Counter(all_words).most_common(10), columns=['Keyword', 'Count']),
        'sla_matrix': pay_df.groupby(['Request Type', 'Status'])['Days to Resolution'].mean().unstack().fillna(0).round(1),
        'action_intent_distribution': pay_df['Action_Intent'].value_counts().to_frame(name='Volume'),
        'top_problem_pos': pay_df[pay_df['Extracted_PO'] != 'No PO Found']['Extracted_PO'].value_counts().head(10).to_frame(name='Friction Count'),
        'top_problem_invoices': pay_df[pay_df['Extracted_Invoice'] != 'No Invoice Found']['Extracted_Invoice'].value_counts().head(10).to_frame(name='Friction Count'),
        
        # Distribution crosstabs
        'distribution_request_type_by_status': pd.crosstab(pay_df['Request Type'], pay_df['Status'], margins=True),
        'distribution_request_subtype_by_status': pd.crosstab(pay_df['Request Sub-Type'], pay_df['Status'], margins=True),
        'distribution_request_type_by_commodity': pd.crosstab(pay_df['Commodity Group'], pay_df['Request Type'], margins=True),
        'distribution_request_subtype_by_commodity': pd.crosstab(pay_df['Commodity Group'], pay_df['Request Sub-Type'], margins=True)
    }

    # --- STAGE 5: DATA VISUALIZATION ---
    print(" [3/4] Exporting clean data visualization files...")
    sns.set_theme(style="whitegrid")

    # Chart 1: Friction Heatmap Matrix
    heatmap_data = pd.crosstab(pay_df['Commodity Group'], pay_df['Request Type'])
    safe_commodities = heatmap_data.index.intersection(pay_df['Commodity Group'].value_counts().nlargest(8).index)
    safe_requests = heatmap_data.columns.intersection(pay_df['Request Type'].value_counts().nlargest(5).index)
    plt.figure(figsize=(10, 6))
    sns.heatmap(heatmap_data.loc[safe_commodities, safe_requests], annot=True, fmt="d", cmap="YlGnBu", cbar=True)
    plt.title("Ticket Volume by Commodity and Request Type", fontsize=12, weight='bold')
    plt.xticks(rotation=20, ha='right')
    plt.tight_layout()
    plt.savefig('images/ticket_heat_matrix.png', dpi=300)
    plt.close()

    # Chart 2: Cumulative Area + Line Trend
    plt.figure(figsize=(11, 5))
    trend_data = pay_df.groupby(['Year_Month', 'Status']).size().unstack(fill_value=0)
    trend_data.index = trend_data.index.astype(str)
    trend_data.plot(kind='area', stacked=True, alpha=0.4, ax=plt.gca(), cmap='tab10')
    trend_data.sum(axis=1).plot(kind='line', marker='o', color='black', linewidth=2.0, ax=plt.gca())

    plt.title("Monthly Ticket Trends by Status", fontsize=12, weight='bold')
    plt.tight_layout()
    plt.savefig('images/monthly_status_trends.png', dpi=300)
    plt.close()
    # Chart 3: Request Sub-Type Share
    plt.figure(figsize=(6, 6))
    subtypes = pay_df['Request Sub-Type'].value_counts().drop(labels=['nan', 'None'], errors='ignore')
    top_subtypes = subtypes.nlargest(5)
    if len(subtypes) > 5: top_subtypes['Other'] = subtypes.iloc[5:].sum()
    top_subtypes.plot(kind='pie', autopct='%1.1f%%', startangle=90, cmap='Blues_r', wedgeprops={'edgecolor': 'black', 'linewidth': 0.6})
    plt.title("Ticket Share by Request Sub-Type", fontsize=12, weight='bold')
    plt.tight_layout()
    plt.savefig('images/request_subtype_share.png', dpi=300)
    plt.close()
    # Chart 4: Scatter Density Matrix Map
    plt.figure(figsize=(9, 5))
    sns.scatterplot(data=pay_df, x='Text_Length', y='Days to Resolution', hue='Automation_Category', palette='Set1', alpha=0.6, s=60)
    plt.axvline(x=120, color='gray', linestyle='--')
    plt.axhline(y=2, color='gray', linestyle='--')
    plt.title("Ticket Complexity vs. Days to Resolution", fontsize=12, weight='bold')
    plt.xlim(0, 1000)
    plt.tight_layout()
    plt.savefig('images/automation_complexity_mapping.png', dpi=300)
    plt.close()
    # Chart 5: Request Type Distribution
    plt.figure(figsize=(10, 6))
    request_type_counts = pay_df['Request Type'].value_counts()
    sns.barplot(x=request_type_counts.values, y=request_type_counts.index, hue=request_type_counts.index, palette="viridis", legend=False)
    plt.title("Volume Distribution by Request Type", fontsize=12, weight='bold')
    plt.tight_layout()
    plt.savefig('images/request_type_distribution.png', dpi=300)
    plt.close()
    # Chart 6: Extraction Completeness Metrics (MATCH RATE CHART)
    plt.figure(figsize=(9, 5))
    po_found = (pay_df['Extracted_PO'] != 'No PO Found').sum()
    inv_found = (pay_df['Extracted_Invoice'] != 'No Invoice Found').sum()
    match_metrics = pd.Series({'PO Mapped': po_found, 'Invoice Mapped': inv_found, 'Total Audited': len(pay_df)})
    sns.barplot(x=match_metrics.values, y=match_metrics.index, hue=match_metrics.index, palette="coolwarm", legend=False)
    plt.title("Metadata Extraction Yield Profile per Payment Ticket", fontsize=12, weight='bold')
    plt.xlabel("Count of Successfully Identified Rows")
    plt.tight_layout()
    plt.savefig('images/metadata_extraction_yield.png', dpi=300)
    plt.close()
    # --- STAGE 6: FILE PATH WRITE EXPORTS ---
    print(" [4/4] Writing comprehensive data metrics and audit logs to disk...")
    # Granular Per-Ticket Export
    tables['ticket_master_audit_log'].to_csv('data/ticket_master_extraction_audit.csv', index=False)
    # Summary Table Exports
    tables['automation_readiness'].to_csv('data/stats_automation_readiness.csv', index=True)
    tables['top_suppliers'].to_csv('data/stats_top_suppliers.csv', index=True)
    tables['root_cause_keywords'].to_csv('data/stats_root_cause_keywords.csv', index=False)
    tables['sla_matrix'].to_csv('data/stats_sla_matrix.csv', index=True)
    tables['action_intent_distribution'].to_csv('data/stats_subject_action_intent.csv', index=True)
    tables['top_problem_pos'].to_csv('data/stats_top_problem_pos.csv', index=True)
    tables['top_problem_invoices'].to_csv('data/stats_top_problem_invoices.csv', index=True)
    # Distribution matrix data exports
    tables['distribution_request_type_by_status'].to_csv('data/distribution_request_type_by_status.csv', index=True)
    tables['distribution_request_subtype_by_status'].to_csv('data/distribution_request_subtype_by_status.csv', index=True)
    tables['distribution_request_type_by_commodity'].to_csv('data/distribution_request_type_by_commodity.csv', index=True)
    tables['distribution_request_subtype_by_commodity'].to_csv('data/distribution_request_subtype_by_commodity.csv', index=True)
    print("\n Run successful! 'data/ticket_master_extraction_audit.csv' generated with granular per-ticket data.")
    return tables


# run main script
if __name__ == "__main__":
    run_consolidated_pipeline()

