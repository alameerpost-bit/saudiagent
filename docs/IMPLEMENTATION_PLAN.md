# Saudi Agent — Phased Implementation Plan

## Phase 0 — Baseline and protection (this branch)
- Record repository identity, active workflows, data flow, and safety boundaries.
- Keep `main` and all scheduled workflows unchanged.
- Add architecture and operational documentation.
- Validate this branch through the existing GitHub Actions test workflow before merge.

## Phase 1 — Reliability and observability
- Add a consistent run ID and explicit run status for daily generation and morning review.
- Log market date, provider, market timestamp, retrieval timestamp, delayed flag, requested/received universe counts, and rejection reasons.
- Make Telegram messages distinguish successful scan, no valid signal, incomplete data, and technical failure.
- Review watchdog behavior for missing, queued, in-progress, failed, and cancelled runs; avoid duplicate fallback dispatches.
- Add tests for workflow-independent business logic and data-quality edge cases.
- Do not change signal thresholds or recommendation sizing.

## Phase 2 — Data contract and provider quality
- Define normalized schemas for TASI, instrument quotes, turnover-ranked universe, and historical bars.
- Keep Sahm current-price data authoritative where validated.
- Prohibit silent fallback from failed current quotes to stale historical/vendor values.
- Add provider health and latency checks; record delayed status explicitly.
- Verify official provider documentation and API terms before adding any provider.

## Phase 3 — Saudi market analysis modules
- Separate technical calculations from candidate selection and message formatting.
- Add Saudi issuer financial metrics from official filings, with sector-specific metric handling.
- Add official Tadawul disclosures/news with publication time and source links.
- Add TASI regime/breadth context.
- Keep each module independently testable; a missing module must not fabricate a positive signal.

## Phase 4 — Portfolio controls
- Move capital and risk settings into validated configuration while preserving existing defaults.
- Add portfolio holdings and lot ledger based on broker statements or confirmed fills.
- Add sizing, exposure, liquidity, fee, and slippage checks in SAR.
- Make rebalancing and cost-basis calculations proposal-only.
- Do not apply US federal tax or wash-sale rules to Saudi holdings.

## Phase 5 — Sahm account integration (read-only first)
- Verify official Sahm API documentation and granted scopes.
- Start with account, holdings, order-status, and fill reads if officially supported.
- Store credentials only in GitHub/hosting secrets or secure environment variables.
- Test authentication, rate limits, pagination, stale responses, and account identity.
- Keep all trading/order endpoints disabled.

## Phase 6 — Optional order workflow (only after separate approval)
- Implement an isolated broker adapter only if official permissions and API support are confirmed.
- Require a fresh risk PASS bound to the exact order fingerprint.
- Require explicit user approval for each order, with no approve-all behavior.
- Never place orders from scheduled or background runs.
- Keep live execution disabled by default and test in a paper/sandbox environment first.

## Acceptance criteria for every phase
- Automated tests pass.
- Existing daily and morning workflow schedules remain intact.
- No secrets or personal account data are committed.
- No order execution is introduced unintentionally.
- Recommendation outputs disclose source, exchange timestamp, retrieval timestamp, and data status.
- Changes are reviewed in a pull request before merging to `main`.