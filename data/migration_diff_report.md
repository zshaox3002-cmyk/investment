# Migration Diff Report

## Check 1: Holdings Total Market Value

- DB total (B+C): ¥613,005
- CSV total: ¥724,379
- Diff: 15.38% → ⚠️ DIFF

## Check 2: C-Tranche Position Ratio

- rules.yaml active_breaches[active_position_total]: 48.38%
- DB computed (C / excl-D total): 38.95%
- Note: rules.yaml uses base ¥1,285,798; DB uses live quotes. Difference is expected.

## Check 3: Alert Count

- alerts/*.md files: 22
- DB distinct migrated alert paths: 22
- Status: ✅ OK

## Check 4: Thesis Scores (frontmatter vs DB)

| File | Frontmatter score | DB current_score | Match |
|------|-------------------|-----------------|-------|
| 000568_thesis.md | 3.0 | 3.0 | ✅ |
| 001280_thesis.md | 2.5 | 2.5 | ✅ |
| 002594_thesis.md | 3.2 | 3.2 | ✅ |
| 02015_thesis.md | 2.8 | 2.8 | ✅ |
| 600219_thesis.md | 3.27 | 3.27 | ✅ |
| 601012_thesis.md | 2.45 | 2.45 | ✅ |
| 601318_thesis.md | 3.0 | 3.0 | ✅ |

Thesis scores: ✅ all match

## Check 5: Known Data Conflicts (informational)

These conflicts existed in the source files and are preserved for review.

### 5a: 600219 current_price
- holdings.csv current_price: 5.5
- portfolio_ts.csv price: 5.2
- DB uses portfolio_ts.csv price (more recent). holdings.csv value is stale.

### 5b: 513010 target_value vs b_tranche plan
- core_etf.csv target_value: 160725 (final target)
- b_tranche_execution_plan.yaml phase1 amount: 80000 (phase 1 only)
- These are different concepts: final target vs phase 1 amount. Not a bug.

### 5c: C-tranche ratio denominator mismatch
- rules.yaml uses base ¥1,285,798 (excl. RSU): C ratio = 48.38%
- daily reports use C_market_value / C_allocation: ratio = 188.9%
- DB v_portfolio_snapshot uses live quotes / excl-D total: 38.95%
- Resolution: DB is the canonical source. rules.yaml base is a snapshot.

## Check 7: Phase 4 Risk Tables

- Tables present: risk_metrics, correlation_matrix, risk_contribution → ✅ OK

## Check 6: Phase 2 Onboarding Tables

- Tables present: user_profile, goals, asset_inventory → ✅ OK

## Check 8: Phase 5 Attribution Tables

- Tables present: performance_attribution, benchmark_quotes → ✅ OK

## Check 9: Phase 6 Causal Extension Columns

- Columns present: validation_status, revision_log, scope_layer, credibility_tier → ✅ OK

## Check 10: Phase 7 Tables

- Tables present: 7 Phase 7 tables → ✅ OK
