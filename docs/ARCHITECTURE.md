# Saudi Agent — Architecture Baseline

## Project identity
- Canonical project: **Saudi Agent**
- Repository: `alameerpost-bit/saudiagent`
- Default branch: `main`
- Development branch: `feature/saudi-agent-v2-foundation`
- Market: Saudi Exchange (Tadawul), with TASI as the benchmark
- Intended broker/data integration: Sahm Capital, subject to documented API capabilities and permissions

This repository is the sole active project. “Saudi Trading Agent” is not a separate implementation or repository.

## Existing production path — preserve during foundation work

GitHub Actions discovers workflows under `.github/workflows/`. The active workflows are:

| Workflow | Current UTC schedule | Riyadh time | Purpose |
|---|---:|---:|---|
| `daily.yml` | 13:15, Sun–Thu | 16:15 | Generate closing-session recommendations |
| `morning-review.yml` | 07:15, Sun–Thu | 10:15 | Review previous recommendations |
| `schedule-watchdog.yml` | 13:30, Sun–Thu | 16:30 | Detect missing/failed daily execution and alert/dispatch fallback |
| `tests.yml` | push / pull request | — | Run unit tests |
| `backtest.yml` | manual | — | Run historical strategy evaluation |

The top-level `github/workflows/` directory is a legacy copy, not a GitHub Actions discovery path. Do not delete or modify it until its history and external references are reviewed. Its YAML files do not create active schedules.

The daily workflow writes `data/previous_signals.json` back to the repository for the morning review. Preserve this hand-off unless a replacement is implemented and tested.

## Current system boundaries
1. `bot.py` remains the existing recommendation engine.
2. Sahm is the primary source for current market universe, quotes, and market summary where validated.
3. Yahoo Finance is used for historical/technical data where currently implemented; it must not silently replace a failed Sahm current quote.
4. Delayed quotes may support post-close analysis and explicitly delayed review, but must not be represented as real-time execution prices.
5. Telegram is the notification channel.
6. No broker order execution is implemented or enabled by this foundation.

## Target modular architecture

### Data and quality
- Provider adapters: Sahm first; other providers only after source, terms, latency, and schema are verified.
- Normalize symbol, SAR price/value, exchange timestamp, retrieval timestamp, provider, and delayed/real-time status.
- Reject missing, stale, malformed, wrong-session, or insufficient-coverage data.
- Keep source timestamp distinct from retrieval timestamp.

### Analysis
- Technical analysis: indicators, trend, liquidity, volatility, and setup conditions.
- Financial analysis: Saudi issuer financial statements and filings; do not reuse SEC-only logic for Tadawul issuers.
- News and disclosures: prioritize official Tadawul issuer announcements; retain original source and publication time.
- Screener: candidate discovery, not a buy recommendation by itself.
- TASI context: market regime and breadth should qualify individual-stock signals.

### Portfolio and risk
- Budgeting and sizing in SAR, with user-configured capital only.
- Position and portfolio exposure limits, fees, slippage, liquidity, and lot-size rules.
- Cost-basis ledger based on actual fills/statements; no US tax or wash-sale assumptions.
- Rebalancing creates proposals only.
- Risk validation is a mandatory gate before any order proposal can advance.

### Execution boundary
- Sahm order adapter is out of scope until official API documentation, account permissions, order lifecycle, and read-only versus trade scopes are verified.
- No API keys in source files, commits, issues, or chat.
- No automated, scheduled, or background order placement.
- Any future order requires a fresh risk check and explicit user approval for that exact order. Execution remains disabled by default.

## Non-negotiable operating rules
- Preserve existing daily and morning workflow schedules during foundation work.
- Every recommendation must disclose provider, exchange timestamp, retrieval timestamp, and delayed/real-time status.
- Suppress actionable entry recommendations when required data is unavailable, stale, inconsistent, or below configured coverage.
- Distinguish a watchlist candidate, technical setup, recommendation, and executable order.
- Never infer available capital or invent portfolio holdings/cost basis.
- No changes to live strategy thresholds without documented tests and explicit review.
- Backtests must include fees and slippage and compare performance with TASI over matching periods.

## Current known configuration requiring later review
- `bot.py` currently defines `CAPITAL = 1000` SAR and derived exposure limits in code. This foundation does not change those values. A later configuration change must preserve current behavior unless the user explicitly supplies a different budget.
- The current scan requests the top 50 TASI shares by traded value from Sahm and limits outgoing signals to three.
- Current schedule is 16:15 Riyadh for closing recommendations and 10:15 Riyadh for morning review.