# SV_OPS_DAILY_PRODUCTION_VOLUMES — Semantic Architecture
# Co-authored with CoCo

## Overview

The Operations Daily Production Volumes semantic view provides a governed, self-service analytics interface over the daily production data in `ENT_DEV.OPS`. It enables Cortex Analyst to answer natural-language questions about oil, gas, and water production; injection operations; well-bore conditions; and operational KPIs across the GCR and Permian business units.

**Deployment**: `ENT_DEV.OPS.SV_OPS_DAILY_PRODUCTION_VOLUMES`
**Source**: `ENT_DEV.OPS.DAILY_PRODUCTION_VOLUMES` (view)
**Grain**: One row per completion per calendar production day (COMPLETION_ID + VOLUME_DATE)
**Scale**: ~29M rows, 14,286 completions, dates from 1970 to present
**Cross-domain key**: API_10 (joins to Global Wells and indirectly to Land via ARRG_KEY)

---

## Data Model

```
┌─────────────────────────────────────────────────────────────┐
│         SV_OPS_DAILY_PRODUCTION_VOLUMES                     │
├─────────────────────────────────────────────────────────────┤
│ DIMENSIONS (6)          │ TIME (1)       │ FACTS (10)       │
│ • BU                    │ • VOLUME_DATE  │ • OIL_VOLUME     │
│ • AREA                  │                │ • GAS_VOLUME     │
│ • API_10 (cross-domain) │                │ • WATER_VOLUME   │
│ • WELL_NAME             │                │ • GAS_INJ_VOLUME │
│ • COMPLETION_ID (PK)    │                │ • WATER_INJ_VOL  │
│ • COMPLETION_NAME       │                │ • TUBING_PRESS   │
│                         │                │ • CASING_PRESS   │
│                         │                │ • BHP            │
│                         │                │ • BHT            │
│                         │                │ • CHOKE_SIZE     │
├─────────────────────────────────────────────────────────────┤
│ METRICS (18)                                                │
│ Volumes: TOTAL_OIL/GAS/WATER/GAS_INJ/WATER_INJ             │
│ Composite: TOTAL_BOE, TOTAL_LIQUID, NET_GAS, NET_BOE        │
│ Ratios: WATER_CUT, OIL_CUT, GAS_OIL_RATIO                  │
│ Well Counts: PRODUCING/INJECTION/TOTAL/SHUT_IN_WELL_COUNT   │
│ Rates: AVG_OIL_PER_WELL, AVG_BOE_PER_WELL                  │
├─────────────────────────────────────────────────────────────┤
│ VERIFIED QUERIES (6)                                        │
│ Single-well daily, Monthly by BU, Top wells, Water cut,     │
│ Cross-domain (Global Wells), BOE per well by area           │
└─────────────────────────────────────────────────────────────┘
```

---

## Relationships

### Internal (within the source view)
| Parent | Child | Join | Type |
|--------|-------|------|------|
| IOT_ASSET_ATTR_DT (header) | IOT_ASSET_ATTR_DAY (daily) | HK_ASSET | INNER |
| IOT_ATTR_DFNTN | IOT_ASSET_ATTR_DAY | HK_ATTR | INNER (filtered) |
| View output | L48_DEV.L48_BI.DAILY_PRODUCTION_VOLUMES | COMPLETION_ID=PDEN_ID + VOLUME_DATE | INNER |

### Cross-Domain
| Source Key | Target | Join Pattern | Coverage |
|-----------|--------|--------------|----------|
| API_10 | L48_PRD.L48_WELLS.WELL | LEFT(WELL_GOVERNMENT_ID, 10), WO level | 100% |
| API_10 → ARRG_KEY | L48_PRD.L48_LAND.QUORUM_QLREPORT_QDV_RELATED_WELL_AGMT | LEFT(API_NUMBER, 10) | 14% (fan-out: median 22x) |

---

## Metrics Summary

| Category | Metrics | Aggregation |
|----------|---------|-------------|
| Stream Volumes | TOTAL_OIL/GAS/WATER_PRODUCTION, TOTAL_GAS/WATER_INJECTION | SUM |
| Equivalent | TOTAL_BOE, TOTAL_LIQUID, NET_GAS_PRODUCTION, NET_BOE | SUM (derived) |
| Ratios | WATER_CUT, OIL_CUT, GAS_OIL_RATIO | Ratio of SUMs |
| Well Counts | PRODUCING/INJECTION/TOTAL/SHUT_IN_WELL_COUNT | COUNT DISTINCT |
| Per-Well Rates | AVG_OIL_PER_WELL, AVG_BOE_PER_WELL | SUM / COUNT |

---

## Decision Log

| ID | Decision | Context | Reasoning | Alternatives Considered | Selected Approach | Impact |
|----|----------|---------|-----------|------------------------|-------------------|--------|
| D01 | Use view as base_table, not underlying IOT tables | The view pre-joins 4 tables into a clean grain | The view is the governed interface; modeling raw IOT tables would require replicating complex EAV pivot logic | Model IOT tables directly with relationships | Single-table semantic view over the view | Simplicity; no relationship complexity; depends on view maintenance |
| D02 | Exclude STATIC_PRESSURE and CHOKE_POSITION | Both are 100% NULL in 2026 data | Dead columns add noise; no analytical value | Include with "sparse" warnings | Excluded from semantic model entirely | Cleaner model; if data revives, can be re-added |
| D03 | API_10 as cross-domain key, not ARRG_KEY | ARRG_KEY not native to production; 14% coverage; 22x median fan-out | Fan-out would make any join produce incorrect aggregates | Declare ARRG_KEY as dimension | API_10 only; ARRG_KEY documented as indirect path | Safe joins; no cardinality explosion |
| D04 | 6:1 MCF-to-BOE conversion factor | Industry standard for US onshore; confirmed by IOT attribute definitions | No company-specific factor found in metadata | Use 5.8:1 (thermal equivalent) | 6.0 (industry volumetric standard) | Consistent with industry reporting |
| D05 | Negative volumes summed as-is | 0.19% of oil rows are negative; industry practice for prior-period adjustments | Adjustments must net against gross to give correct totals | Use ABS() or filter negatives | Include in SUM | Mathematically correct net production |
| D06 | No ACTIVE_IND filter — use volume > 0 | No status flag exists in this domain (unlike Land) | Production activity is self-evident from non-zero volumes | Require external status lookup | Document in custom_instructions rule #3 | Users get clear guidance |
| D07 | Deploy to ENT_DEV.OPS (same schema as source) | User-confirmed target | Co-location with source data simplifies grants and dependency tracking | Deploy to DATASCIENCE_DEV.DS_ENT_BI (with Land views) | ENT_DEV.OPS per user direction | Separate from Land views physically; same domain logically |

---

## Governance

### Security
- **Target role**: `ENT_DEV_OPS_DEVELOPER_FRL` (26 users, owned by DEV_SECADMIN_PF_FRL)
- **Least-privilege grants**: Database USAGE → Schema USAGE → Semantic View SELECT → Source View SELECT → Warehouse USAGE
- **Cross-domain**: Read-only access to L48_PRD.L48_WELLS for API_10 enrichment queries

### Data Quality
- All volumes are allocated (VERSION_NAME='ACTUAL'); no revision history
- 0.45% of rows have all-null volumes (orphan/invalid records)
- 25.56% are all-zero (shut-in days — valid)
- Negative values (~0.2% of records) are legitimate adjustments
- Pressures are 72-100% NULL — documented in descriptions

### Assumptions
1. UOM: Oil/Water in BBL, Gas in MCF, Pressure in PSI, Temperature in °F (inferred from data magnitudes; not explicitly stored)
2. BOE conversion: 6.0 MCF/BOE (industry standard, not company-specific)
3. COMPLETION_ID = PDEN_ID mapping is 1:1 (confirmed by join integrity)
4. BU/AREA assignment is static per completion (confirmed: 0 changes over time)

### Risks
1. **L48 legacy dependency**: The source view INNER JOINs to L48_DEV.L48_BI.DAILY_PRODUCTION_VOLUMES for STATIC_PRESSURE and CHOKE_POSITION (both dead). If L48 table is deprecated, the view would break.
2. **Date range includes 1970 data**: Historical/migrated data may have quality issues.
3. **No agent exists yet**: The semantic view is deployed standalone; agent creation is a separate step.

### Limitations
- No decline curve analysis (requires window functions beyond metric scope)
- No revenue/economics (not in this domain)
- No measured vs. allocated comparison (only ACTUAL version exists)
- ARRG_KEY cross-domain merge is unsafe without curated business rules

### Open Questions
1. Should the L48 legacy join be removed from the source view since both columns it provides are dead?
2. Is there a company-specific BOE conversion factor that should override the 6.0 standard?
3. Should this semantic view eventually move to DATASCIENCE_DEV.DS_ENT_BI to co-locate with Land views?

---

## Change Log

| Date | Author | Change | Version |
|------|--------|--------|---------|
| 2026-07-31 | CORTEX_CODE | Initial creation — Stages 0-7 complete | 1.0 |
