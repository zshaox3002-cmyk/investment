# Migration Diff Report

## Check 1: Holdings Total Market Value

- DB total (B+C): ¥756,031
- CSV total: ¥756,031
- Diff: 0.00% → ✅ OK

## Check 2: C-Tranche Position Ratio

- rules.yaml active_breaches[active_position_total]: 48.38%
- DB computed (C / excl-D total): 45.90%
- Note: rules.yaml uses base ¥1,285,798; DB uses live quotes. Difference is expected.

## Check 3: Alert Count

- alerts/*.md files: 18
- DB migrated alerts (with body_path): 18
- Status: ✅ OK

## Check 4: Thesis Scores (frontmatter vs DB)

| File | Frontmatter score | DB current_score | Match |
|------|-------------------|-----------------|-------|
| 000568_thesis.md | 3.0 | 3.0 | ✅ |
| 001280_thesis.md | 2.5 | 2.5 | ✅ |
| 002594_thesis.md | 3.2 | 3.2 | ✅ |
| 02015_thesis.md | 2.8 | 2.8 | ✅ |
| 600219_thesis.md | 3.27 | 3.27 | ✅ |
| 601012_thesis.md | 2.8 | 2.8 | ✅ |
| 601318_thesis.md | 3.0 | 3.0 | ✅ |

Thesis scores: ✅ all match

## Check 5: Known Data Conflicts (informational)

These conflicts existed in the source files and are preserved for review.

### 5a: 600219 current_price
- holdings.csv current_price: 5.5
- portfolio_ts.csv price: 5.68
- DB uses portfolio_ts.csv price (more recent). holdings.csv value is stale.

### 5b: 513010 target_value vs b_tranche plan
- core_etf.csv target_value: 160725 (final target)
- b_tranche_execution_plan.yaml phase1 amount: 80000 (phase 1 only)
- These are different concepts: final target vs phase 1 amount. Not a bug.

### 5c: C-tranche ratio denominator mismatch
- rules.yaml uses base ¥1,285,798 (excl. RSU): C ratio = 48.38%
- daily reports use C_market_value / C_allocation: ratio = 188.9%
- DB v_portfolio_snapshot uses live quotes / excl-D total: 45.90%
- Resolution: DB is the canonical source. rules.yaml base is a snapshot.
