# Cloudflare external scheduler

## Role and safety boundary

Cloudflare Worker `saudi-agent-scheduler` is an external, idempotent trigger for the existing GitHub Actions schedule watchdog. It does **not** run the Python trading engine, access Sahm, create recommendations, or place orders. GitHub Actions remains the source of code and the watchdog remains responsible for deciding whether a daily workflow needs a fallback.

## Schedule

- Cron: `35 13 * * SUN-THU` UTC (16:35 Asia/Riyadh, Sunday–Thursday).
- The existing GitHub watchdog schedule at 16:30 Riyadh is preserved as a first attempt.
- Cloudflare dispatches `.github/workflows/schedule-watchdog.yml` five minutes later as an independent recovery trigger.
- The Worker checks the Riyadh weekday and local time again. It refuses to dispatch outside 16:35–16:50 Riyadh, so a delayed invocation after midnight cannot start a stale recommendation run.

## Idempotency

A SQLite-backed Durable Object stores one record per Riyadh calendar date. It uses a short lease while dispatching and records the accepted GitHub dispatch. Repeated invocations for the same date are suppressed after a successful dispatch. Failed dispatches are recorded and visible through `GET /health`.

## Required secret

Set the Cloudflare Worker secret `GH_TOKEN`. Use a GitHub fine-grained personal access token restricted to this repository with:
- Actions: Read and write

The token is used only to call GitHub's `workflow_dispatch` endpoint for `schedule-watchdog.yml`. Never put it in Git, Wrangler configuration, source code, or chat. Set it through the Cloudflare dashboard or `wrangler secret put GH_TOKEN`.

Until `GH_TOKEN` is configured, the Worker will safely refuse to dispatch and record a failure in its logs/state. The existing GitHub schedule remains active during setup.

## Deployment

From the repository root, after review:
1. Deploy `wrangler.jsonc` and `src/index.js` with the Cloudflare Workers deployment workflow or Wrangler.
2. Set `GH_TOKEN` as a Worker secret.
3. Confirm `GET https://saudi-agent-scheduler.<account-subdomain>.workers.dev/health` reports `githubTokenConfigured: true`.
4. Use Cloudflare Worker logs to confirm the next scheduled invocation and GitHub's accepted dispatch.
5. Keep the GitHub-native watchdog schedule until at least one successful Cloudflare-triggered dispatch has been verified. Only then consider removing the GitHub schedule in a separate reviewed change.

## Operational guarantees

- Existing daily and morning-review workflows are unchanged.
- No Sahm API key, Telegram token, or broker credentials are copied to Cloudflare.
- Trading execution remains disabled.
- A successful GitHub dispatch means only that GitHub accepted the watchdog workflow; it does not mean recommendations were generated or delivered.
