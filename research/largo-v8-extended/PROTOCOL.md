# Frozen v8 long-history research protocol

The task is validation, not further parameter optimization. Do not replace the live strategy.

## Time ranges
- Initial training: October-December 2023.
- Evaluation: January 2024-April 2026, reported by signal month.
- Refit monthly on preceding data only. Exclude a prior-month signal when its outcome day is not strictly earlier than the test month's first signal day.
- Do not use coefficients fitted on May-August 2026 to predict 2024 or 2025.

## Frozen archived v8 recipe
- Same safety proxy flags, common stock, non-negative news flag, change 0-15%, structural risk 0-15%, digestion .2-1.5, body >=45%, pattern 50-90.
- Theme breadth >=.50 and rank <=5, OR audited catalyst directness >=14 and freshness >=7.
- Archived 17-feature transforms. Ridge alpha=20, prediction cutoff=.75.
- At most 3 names. Candidate score shortlist capped at 8.
- Every selected pair: max(prior gap correlation, prior close-return correlation) <=.50. Prior 60 sessions, at least 40 joint observations. Missing correlation rejects an additional pair, not the first stock.
- Prefer the largest feasible number of names, then mean predicted score minus .5 times mean pair correlation, matching optimize_v8.py.
- Allocation is 1/k across k selected names. One name uses 100%; two use 50% each. All cash only when no name is selected. This is the archived code's allocation, not fixed one-third slots.
- Target +3%, otherwise next-day close; cost assumption .30 percentage points. No intraday stop in the main policy.

## Comparisons, not optimized alternatives
- Recalculate v6.1 single-stock reference on the same reconstructed data.
- Recalculate v8 single-name selection as an attribution comparison.
- For the same chosen names, show opening exit and +/-3% target/stop outcome bounds; opening gaps fill at the opening proxy, not at a guaranteed stop level.
- Selected positions with missing outcomes remain missing. No outcome-based reselection or renormalization.

## Honest scope
This is a retrospective DAILY-OHLC proxy study. The candidate universe, share-count proxy and theme membership are retrieved now. Historical alerts are unverified and old news coverage is incomplete. Final daily features cannot reproduce 15:18 decisions. No new exact 09:06 executable results are claimed. The hypothesis itself was developed later, so causal monthly fitting does not make the whole dataset an independent historical live trial.

Actual GS Quant 2.1.12 is used for correlation calculations; ridge estimation is independent NumPy code. Goldman Sachs does not endorse the strategy or its results.

Raw responses, source snapshots, monthly models, daily trades, missing data and audit output must be retained. Artifacts have 90-day retention; final reports are also retained as repository text and a downloadable project ZIP.
