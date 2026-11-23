import { DurableObject } from "cloudflare:workers";

const REPOSITORY = "alameerpost-bit/saudiagent";
const WATCHDOG_WORKFLOW = "schedule-watchdog.yml";
const GITHUB_API = "https://api.github.com";

function riyadhParts(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Riyadh",
    weekday: "short",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  return Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
}

function riyadhDateKey(parts) {
  return `${parts.year}-${parts.month}-${parts.day}`;
}

// The external trigger is deliberately restricted to the intended Riyadh
// window. A delayed invocation after midnight must never start yesterday's run.
function isAllowedWindow(parts) {
  const weekday = parts.weekday;
  const hour = Number(parts.hour);
  const minute = Number(parts.minute);
  return ["Sun", "Mon", "Tue", "Wed", "Thu"].includes(weekday)
    && hour === 16
    && minute >= 35
    && minute <= 50;
}

export class SchedulerState extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    ctx.blockConcurrencyWhile(async () => {
      ctx.storage.sql.exec(`
        CREATE TABLE IF NOT EXISTS dispatches (
          run_key TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          updated_at INTEGER NOT NULL,
          lease_until INTEGER NOT NULL,
          detail TEXT
        )
      `);
    });
  }

  claim(runKey, now, leaseMs) {
    const rows = this.ctx.storage.sql
      .exec("SELECT status, lease_until FROM dispatches WHERE run_key = ?", runKey)
      .toArray();
    const current = rows[0];
    if (current?.status === "dispatched") return false;
    if (current?.status === "in_flight" && current.lease_until > now) return false;

    this.ctx.storage.sql.exec(
      `INSERT INTO dispatches (run_key, status, updated_at, lease_until, detail)
       VALUES (?, 'in_flight', ?, ?, NULL)
       ON CONFLICT(run_key) DO UPDATE SET
         status = 'in_flight', updated_at = excluded.updated_at,
         lease_until = excluded.lease_until, detail = NULL`,
      runKey, now, now + leaseMs,
    );
    return true;
  }

  complete(runKey, status, now, detail = null) {
    this.ctx.storage.sql.exec(
      "UPDATE dispatches SET status = ?, updated_at = ?, lease_until = 0, detail = ? WHERE run_key = ?",
      status, now, detail, runKey,
    );
    return { ok: true, status };
  }

  getState(runKey) {
    const rows = this.ctx.storage.sql
      .exec("SELECT run_key, status, updated_at, lease_until, detail FROM dispatches WHERE run_key = ?", runKey)
      .toArray();
    return rows[0] ?? null;
  }
}

async function dispatchWatchdog(env, dateKey) {
  if (!env.GH_TOKEN) {
    throw new Error("Missing GH_TOKEN secret; GitHub dispatch was not attempted.");
  }

  const url = `${GITHUB_API}/repos/${REPOSITORY}/actions/workflows/${WATCHDOG_WORKFLOW}/dispatches`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GH_TOKEN}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main" }),
  });

  if (response.status !== 204) {
    const body = (await response.text()).slice(0, 1200);
    throw new Error(`GitHub workflow dispatch failed: HTTP ${response.status}; ${body}`);
  }
  console.log(JSON.stringify({
    event: "github_watchdog_dispatched",
    repository: REPOSITORY,
    workflow: WATCHDOG_WORKFLOW,
    riyadh_date: dateKey,
    accepted_at: new Date().toISOString(),
  }));
}

async function runScheduled(env) {
  const parts = riyadhParts();
  const dateKey = riyadhDateKey(parts);

  if (!isAllowedWindow(parts)) {
    console.warn(JSON.stringify({
      event: "scheduled_invocation_outside_guard_window",
      riyadh_date: dateKey,
      riyadh_time: `${parts.hour}:${parts.minute}`,
      action: "skipped",
    }));
    return;
  }

  const runKey = `daily-watchdog:${dateKey}`;
  const state = env.SCHEDULER_STATE.getByName("saudi-agent-watchdog");
  const now = Date.now();
  const claimed = await state.claim(runKey, now, 10 * 60 * 1000);

  if (!claimed) {
    console.log(JSON.stringify({ event: "duplicate_dispatch_suppressed", riyadh_date: dateKey }));
    return;
  }

  try {
    await dispatchWatchdog(env, dateKey);
    await state.complete(runKey, "dispatched", Date.now());
  } catch (error) {
    const detail = String(error?.message ?? error).slice(0, 1500);
    await state.complete(runKey, "failed", Date.now(), detail);
    console.error(JSON.stringify({
      event: "github_watchdog_dispatch_failed",
      riyadh_date: dateKey,
      detail,
    }));
    throw error;
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== "GET" || url.pathname !== "/health") {
      return new Response("Not Found", { status: 404 });
    }

    const parts = riyadhParts();
    const dateKey = riyadhDateKey(parts);
    const state = env.SCHEDULER_STATE.getByName("saudi-agent-watchdog");
    const latest = await state.getState(`daily-watchdog:${dateKey}`);

    return Response.json({
      service: "saudi-agent-scheduler",
      status: "ok",
      timezone: "Asia/Riyadh",
      schedule: "16:35 Sunday-Thursday",
      githubTokenConfigured: Boolean(env.GH_TOKEN),
      today: dateKey,
      dispatch: latest ? {
        status: latest.status,
        updatedAt: new Date(latest.updated_at).toISOString(),
        detail: latest.detail,
      } : null,
      tradingExecutionEnabled: false,
    });
  },

  async scheduled(controller, env, ctx) {
    ctx.waitUntil(runScheduled(env));
  },
};
