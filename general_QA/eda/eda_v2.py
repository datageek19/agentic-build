# eda -v2
# dashboard v2

import os
import re
from collections import Counter
import pandas as pd
import numpy as np

# --- CONFIGURATION ---
EXCEL_FILE = "C:/Users/qw597f/footprint_data_eda/Invoice FP Tickets Jan thru Oct.xlsx"  
SHEET_NAME = "report-1"                    
OUTPUT_DASHBOARD_FILE = "data/business_granular_dashboard.txt"

def generate_advanced_dashboard():
    if not os.path.exists(EXCEL_FILE):
        raise FileNotFoundError(f"Target file '{EXCEL_FILE}' not found.")

    os.makedirs('data', exist_ok=True)
    
    # -------------------------------------------------------------
    # 1. INGESTION & DATA INTEGRITY METRICS (QUALITY AUDIT)
    # -------------------------------------------------------------
    print("Reading raw transaction dataset...")
    df_raw = pd.read_excel(EXCEL_FILE, sheet_name=SHEET_NAME, engine='openpyxl')
    total_raw_tickets = len(df_raw)
    
    df_raw.columns = df_raw.columns.str.strip()
    invalid_nulls = ['nan', 'none', 'null', '']
    
    # Clean text columns for corruption analysis
    check_cols = ['Ticket Number', 'Request Type', 'Request Sub-Type', 'Status']
    for col in check_cols:
        if col in df_raw.columns:
            df_raw[col] = df_raw[col].astype(str).str.strip()

    # Granular Corrupted Data Breakdown
    missing_ticket_num = df_raw['Ticket Number'].isna() | df_raw['Ticket Number'].str.lower().isin(invalid_nulls)
    missing_req_type = df_raw['Request Type'].isna() | df_raw['Request Type'].str.lower().isin(invalid_nulls)
    missing_subtype = df_raw['Request Sub-Type'].isna() | df_raw['Request Sub-Type'].str.lower().isin(invalid_nulls)
    
    is_corrupted = missing_ticket_num | missing_req_type | missing_subtype
    total_corrupted = is_corrupted.sum()
    
    # Clean Filtered Operations Gate
    df_clean = df_raw[~is_corrupted].copy()
    df_clean['Date Submitted'] = pd.to_datetime(df_clean['Date Submitted'], errors='coerce')
    df_clean['Last Edit Date'] = pd.to_datetime(df_clean['Last Edit Date'], errors='coerce')
    df_clean = df_clean.dropna(subset=['Date Submitted'])
    
    # -------------------------------------------------------------
    # 2. DYNAMIC 3-MONTH BUSINESS WINDOW FILTERING
    # -------------------------------------------------------------
    max_date = df_clean['Date Submitted'].max()
    three_months_ago = max_date - pd.DateOffset(months=3)
    df_3m = df_clean[df_clean['Date Submitted'] >= three_months_ago].copy()
    
    total_active_3m = len(df_3m)
    
    # Calculate operational metrics
    df_3m['Days to Resolution'] = (df_3m['Last Edit Date'] - df_3m['Date Submitted']).dt.days
    df_3m['Days to Resolution'] = df_3m['Days to Resolution'].clip(lower=0)
    
    # -------------------------------------------------------------
    # 3. GRANULAR FINANCIAL & PAYMENT CATEGORIZATION
    # -------------------------------------------------------------
    # Explicit Payment Classifications
    payment_keywords = ['invoice', 'payment', 'paid', 'remittance', 'po', 'pr', 'p-card', 'card', 'wire']
    
    is_payment_type = df_3m['Request Type'].str.contains('Invoice|Payment|PO|PR|Card', case=False, na=False)
    is_payment_detail = df_3m['Request Detail'].astype(str).str.contains('|'.join(payment_keywords), case=False, na=False)
    
    df_pay = df_3m[is_payment_type | is_payment_detail].copy()
    total_payment_tickets = len(df_pay)
    non_payment_tickets = total_active_3m - total_payment_tickets
    
    # -------------------------------------------------------------
    # 4. TEXT MINING & DATA EXTRACTION YIELD
    # -------------------------------------------------------------
    po_pattern = r'\b(?:po[#\s:]*)(700\d{7}|701\d{7}|702\d{6,7}|703\d{6,7})\b|\b(700\d{7}|701\d{7}|702\d{6,7}|703\d{6,7})\b'
    invoice_pattern = r'\b(INV|IR|PAY|PMT|CHS|IV|FLY|V\d{3,})\b\s*[#\s:]*\d*[-_\w]*'
    
    def extract_business_context(row):
        title = str(row.get('Subject (Title)', ''))
        detail = str(row.get('Request Detail', ''))
        combined = (title + " " + detail).lower()
        email = str(row.get('Email Address', '')).lower()
        
        # 1. PO Extraction
        po_match = re.search(po_pattern, title, re.IGNORECASE) or re.search(po_pattern, detail, re.IGNORECASE)
        po_found = None
        if po_match:
            groups = [g for g in po_match.groups() if g]
            if groups: po_found = groups[0]
            
        # 2. Invoice ID Extraction
        inv_match = re.search(invoice_pattern, title, re.IGNORECASE) or re.search(invoice_pattern, detail, re.IGNORECASE)
        inv_found = inv_match.group(0).upper() if inv_match else None
        
        # 3. Action Intent Categorization
        if 'reject' in combined: intent = 'Reject Request'
        elif any(x in combined for x in ['cancel', 'void', 'delete']): intent = 'Cancellation Request'
        elif any(x in combined for x in ['expedite', 'urgent']): intent = 'Expedite Request'
        elif any(x in combined for x in ['status', 'unpaid', 'past due', 'late']): intent = 'Payment Status Query'
        else: intent = 'General Operations Support'
        
        # 4. Supplier Domain Parsing
        domain_match = re.search(r'@([\w.-]+)', email)
        domain = domain_match.group(1) if domain_match else 'unknown.com'
        
        return pd.Series([po_found, inv_found, intent, domain])
    
    # Execute mining if data exists
    if total_payment_tickets > 0:
        df_pay[['Extracted_PO', 'Extracted_Invoice', 'Action_Intent', 'Supplier_Domain']] = df_pay.apply(extract_business_context, axis=1)
        
        # Performance & Yield Metrics
        po_count = df_pay['Extracted_PO'].notna().sum()
        inv_count = df_pay['Extracted_Invoice'].notna().sum()
        
        # Top Operational Bottlenecks
        top_pos = df_pay['Extracted_PO'].dropna().value_counts().head(3).to_dict()
        top_invs = df_pay['Extracted_Invoice'].dropna().value_counts().head(3).to_dict()
        
        # External Supplier Footprint (Excluding Internal Domains)
        internal_domains = ['boeing.com', 'ariba.com', 'internal', 'nan', 'unknown.com']
        df_external = df_pay[~df_pay['Supplier_Domain'].isin(internal_domains)]
        top_suppliers = df_external['Supplier_Domain'].value_counts().head(3).to_dict()
        
        # Intent Metrics Breakdown
        intent_summary = df_pay['Action_Intent'].value_counts()
        
        # Root Cause Analysis (Word Frequency Mining)
        stop_words = {'the', 'and', 'to', 'of', 'for', 'in', 'a', 'is', 'it', 'you', 'this', 'that', 'with', 'on', 'please', 
                      'from', 'we', 'have', 'be', 'are', 'as', 'at', 'i', 'our', 'hi', 'team', 'am', 'your', 'can', 'or', 'an'}
        words_list = []
        for txt in df_pay['Request Detail'].astype(str):
            found_words = re.findall(r'\b[a-zA-Z]{3,}\b', txt.lower())
            words_list.extend([w for w in found_words if w not in stop_words])
        top_keywords = Counter(words_list).most_common(5)
        
        # Operational SLA & Automation Complexity Sorting
        df_pay['Text_Length'] = df_pay['Request Detail'].astype(str).apply(len)
        urgent_terms = ['urgent', 'escalat', 'block', 'stop', 'late', 'critical', 'delay', 'overdue']
        df_pay['Is_Urgent'] = df_pay['Request Detail'].astype(str).str.contains('|'.join(urgent_terms), case=False, na=False)
        
        def assign_tier(row):
            if row['Text_Length'] < 120 and row['Days to Resolution'] <= 2:
                return 'Tier 1: Automation Pathway (Short Query/Fast Turnaround)'
            elif row['Is_Urgent']:
                return 'Tier 3: Manual Triage Required (High Priority Escalation)'
            else:
                return 'Tier 2: Algorithmic Routing (Standard Complexity)'
                
        df_pay['Automation_Category'] = df_pay.apply(assign_tier, axis=1)
        auto_breakdown = df_pay['Automation_Category'].value_counts()
        avg_resolution_days = df_pay['Days to Resolution'].mean()
    else:
        po_count = inv_count = avg_resolution_days = 0
        top_pos = top_invs = top_suppliers = {}
        intent_summary = auto_breakdown = pd.Series()
        top_keywords = []

    # -------------------------------------------------------------
    # 5. DRAFT THE GRANULAR BUSINESS DASHBOARD REPORT
    # -------------------------------------------------------------
    report = f"""================================================================================
    🏢 GRANULAR OPERATIONS & DATA INTEGRITY DASHBOARD
    ================================================================================
    Reporting Timeline: {three_months_ago.strftime('%B %Y')} to {max_date.strftime('%B %Y')} (3-Month Active Window)

    [SECTION 1: DATA QUALITY & PIPELINE GATEKEEPER]
    --------------------------------------------------------------------------------
    Your data was audited at a structural level to find hidden transactional drop-offs:

    • Total Historical Records Ingested: {total_raw_tickets:,} rows
    • Corrupted / Structurally Incomplete Rows Dropped: {total_corrupted:,} rows
        └─ Broken Down By Missing Critical Value:
        ├─ Missing Ticket Identifier: {missing_ticket_num.sum():,} occurrences
        ├─ Missing Request Category:  {missing_req_type.sum():,} occurrences
        └─ Missing Sub-Type Matrix:   {missing_subtype.sum():,} occurrences
    • Data Cleanliness Score: {((total_raw_tickets - total_corrupted) / total_raw_tickets * 100):.2f}% Secure

    [SECTION 2: 3-MONTH TRAILING WORKLOAD VOLUMES]
    --------------------------------------------------------------------------------
    Total Clean Operational Tickets: {total_active_3m:,} items

    📊 Core Split of Business Nature:
    ├── 💳 Financial & Payment Related Tickets: {total_payment_tickets:,} ({total_payment_tickets/total_active_3m*100:.1f}%)
    └── ⚙️ General Operational Queries:         {non_payment_tickets:,} ({non_payment_tickets/total_active_3m*100:.1f}%)

    Use code with caution.

    [SECTION 3: METADATA EXTRACTION CAPACITY (UNSTRUCTURED TEXT VALUE)]

    Out of {total_payment_tickets:,} payment tickets, our NLP models pulled hard tracking IDs:
    • Purchase Orders (POs) Successfully Mapped: {po_count:,} rows ({po_count/total_payment_tickets100:.1f}% Yield)
    • Invoice IDs Successfully Mapped:         {inv_count:,} rows ({inv_count/total_payment_tickets100:.1f}% Yield)
    ⚠️ Top Friction Point Targets (Highest Repeat Incidents):
    """
    for po, count in top_pos.items():
    report += f"  ├─ Top Problem PO:      [PO# {po}] -> Flagged {count} times\n"
    for inv, count in top_invs.items():
    report += f"  ├─ Top Problem Invoice: [INV# {inv}] -> Flagged {count} times\n"
    for supplier, count in top_suppliers.items():
    report += f"  └─ Top External Influx: [@{supplier}] -> Submitted {count} tickets\n"

    report += f"""
    [SECTION 4: INTENT STRATIFICATION & UNDERLYING ROOT CAUSES]

    Why are stakeholders submitting tickets? (Underlying Business Intent):
    """
    for intent, count in intent_summary.items():
    report += f"  • {intent:<28}: {count:<4} tickets ({count/total_payment_tickets*100:.1f}% Share)\n"
    report += "\n🔍 Top 5 Keywords Extracted from Free-Text Request Explanations:\n"
    for word, count in top_keywords:
    report += f"  └─ '{word}': Used {count} times across conversations\n"

    report += f"""
    [SECTION 5: STRATEGIC EFFICIENCY & AUTOMATION PATHWAYS]

    Average Processing Turnaround for Payment Disputes: {avg_resolution_days:.1f} Days
    Allocation Strategy for Financial Workflows:
    """
    for tier, count in auto_breakdown.items():
    report += f"  • {tier}: {count} tickets ({count/total_payment_tickets*100:.1f}% Share)\n"
    report += "================================================================================"
    # Export to text file
    with open(OUTPUT_DASHBOARD_FILE, "w", encoding="utf-8") as f:
    f.write(report)
    print(f"\nDashboard exported successfully to: {OUTPUT_DASHBOARD_FILE}\n")
    print(report)


if __name__ == "__main__":
    generate_advanced_dashboard()
