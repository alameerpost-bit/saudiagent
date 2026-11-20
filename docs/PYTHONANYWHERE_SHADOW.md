# Saudi Agent — External Shadow Scheduler

This branch adds a **silent, parallel validation path** for an external Python scheduler.
It does not replace GitHub Actions and must not be merged into `main` until the
five-session comparison has been reviewed and separately approved.

## Safety guarantees

- `scripts/shadow_runner.py` accepts only `run` or `review`.
- It forces `SAUDI_AGENT_SHADOW=true`; the bot's `send()` function records
  `shadow_suppressed` and prints a preview instead of making a Telegram request.
- The runner takes a non-blocking process lock. A concurrent run/review is rejected.
- Audit and handoff JSON files are written through a temporary file and atomic rename.
- Each external execution gets a unique run ID. The source SHA is read from
  `SAUDI_AGENT_COMMIT_SHA`, `GITHUB_SHA`, or the checked-out Git commit.
- The existing trading strategy is not changed. No broker order API is called;
  automatic trading remains disabled.

## Required environment

Set these in the external host's task environment:

- `SAHMK_API_KEY`: required to read market data from Sahm.
- `SAHMK_BASE_URL`: optional; defaults to `https://api.sahmk.sa/api/v1`.
- `SIGNALS_STATE_PATH`: optional; defaults to this checkout's
  `data/previous_signals.json`.
- `SAUDI_AGENT_AUDIT_DIR`: optional; defaults to `data/audit`.
- `SAUDI_AGENT_LOCK_PATH`: optional; defaults to `data/runtime.lock`.

Do not configure Telegram credentials for the shadow host. Even if they are present,
the shadow guard suppresses Telegram requests.

## Task commands

Run from the repository checkout:

```bash
python3.12 scripts/shadow_runner.py run
python3.12 scripts/shadow_runner.py review
```

Use the same persistent checkout and data directory for both tasks so the morning
review reads the previous close handoff. Do not use an ephemeral filesystem.

## Riyadh schedule

Riyadh is UTC+3 year-round. Configure the host's UTC scheduler for Sunday through
Thursday:

| Task | Riyadh time | UTC time |
|---|---:|---:|
| Closing analysis | 16:15 | 13:15 |
| Morning review | 10:15 | 07:15 |

The scheduler is intentionally independent of GitHub Actions. Keep the current
GitHub workflows enabled during validation. The external runner is a parallel
shadow observer, not a production failover sender.

## Five-session acceptance criteria

For each of at least five trading sessions, inspect the external audit JSON and
compare it with the corresponding GitHub run:

1. The audit has `status=success`, `execution_mode=shadow`, a non-empty
   `run_id`, and a source `commit_sha`.
2. The audit verifier returns `shadow_validated`.
3. The audit records `telegram.status=shadow_suppressed`; no Telegram message is sent.
4. Closing run writes a successful handoff for the correct Saudi session date.
5. Morning review reads that exact handoff and uses Sahm quotes within its existing
   freshness window.
6. No overlapping execution occurs, and all failures remain visible in the audit.
7. Recommendations match the GitHub run when both have the same code revision and
   identical source data. Differences must be explained before any production switch.

A successful GitHub Actions workflow alone is not evidence of successful external
execution, and a successful external shadow run does not authorize disabling GitHub.
Do not enable production Telegram delivery, change the GitHub schedule, subscribe to
a paid host, or merge this branch without a separate explicit approval.
