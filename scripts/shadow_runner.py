#!/usr/bin/env python3
"""Locked, silent external scheduler entry point for Saudi Agent shadow validation."""
import fcntl
import os
import subprocess
import sys
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    operation = sys.argv[1] if len(sys.argv) > 1 else ""
    if operation not in {"run", "review"}:
        print("Usage: python scripts/shadow_runner.py {run|review}", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env["SAUDI_AGENT_SHADOW"] = "true"
    env.setdefault("SAUDI_AGENT_RUN_ID", f"external-{uuid.uuid4().hex}")
    env.setdefault("SIGNALS_STATE_PATH", str(ROOT / "data" / "previous_signals.json"))
    audit_dir = Path(env.get("SAUDI_AGENT_AUDIT_DIR", ROOT / "data" / "audit"))
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / f"{operation}-{env['SAUDI_AGENT_RUN_ID']}.json"
    env["SAUDI_AGENT_AUDIT_PATH"] = str(audit_path)

    lock_path = Path(env.get("SAUDI_AGENT_LOCK_PATH", ROOT / "data" / "runtime.lock"))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[Shadow] يوجد تشغيل آخر؛ تم رفض التشغيل المتداخل.", file=sys.stderr)
            return 3

        run = subprocess.run(
            [sys.executable, str(ROOT / "bot.py"), operation],
            cwd=ROOT, env=env, check=False,
        )
        verify = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "verify_run_audit.py")],
            cwd=ROOT, env=env, check=False,
        )
        if run.returncode:
            return run.returncode
        return verify.returncode


if __name__ == "__main__":
    raise SystemExit(main())
