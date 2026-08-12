#!/usr/bin/env python3
"""Pipeline babysitter agent: keep the Drive→asset automation healthy.

Deterministic core (safe to run from cron every few minutes):
  1. Drive folder scan — ALL files, not just CrateScan-*.zip. Any new .zip is
     pulled and normalized into captures/inbox as CrateScan-<8HEX>.zip so the
     rest of the chain picks it up; non-zip uploads (photos etc.) are flagged.
  2. Watcher health — drive_autopilot / watch_inbox / watch_3dgut_queue; dead
     watchers are restarted with their canonical commands.
  3. New results in captures/outbox and failed/rejected sessions since the
     last run are reported.
  4. Disk guard.

AI escalation: when anomalies are found and the `claude` CLI is available,
a short headless Claude call turns the raw findings into a diagnosis appended
to the report (text only — no tools).

State: logs/agent/state.json   Report: logs/agent/latest.md (+ timestamped)
Run:   python tools/pipeline_agent.py [--no-ai] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# CrateScans — the folder the iPad app uploads to, and the one the watchers are
# pointed at. FOLDER_ID is what gets formatted into the watcher commands, so it
# has to be the live one; the old Engin170_sync folder stays in FOLDER_IDS only
# so anything still sitting in it drains rather than being stranded.
FOLDER_ID = "1DpLpm4m4TxUn_Vn9Rfhr0o39HKM-Xkbl"
LEGACY_FOLDER_ID = "1_HjCyP9-Th3UTjK6Kk89vTuud6ltL4AI"  # Engin170_sync (drain only)
FOLDER_IDS = [
    FOLDER_ID,
    LEGACY_FOLDER_ID,
]
AGENT_DIR = ROOT / "logs" / "agent"
STATE = AGENT_DIR / "state.json"
MIN_FREE_GB = 25

sys.path.insert(0, str(ROOT / "tools"))
from drive_rgbd_autopilot import _download_drive_file  # noqa: E402

WATCHERS = {
    "drive_autopilot": (
        "tools/drive_rgbd_autopilot.py",
        "nohup python tools/drive_rgbd_autopilot.py --folder-id {fid} --interval 60 "
        ">> logs/drive_autopilot.log 2>&1 &",
    ),
    "watch_inbox": (
        "tools/watch_inbox.py",
        "nohup python tools/watch_inbox.py --mode object >> logs/watch_inbox.log 2>&1 &",
    ),
    "watch_3dgut_queue": (
        "tools/watch_3dgut_queue.py",
        "nohup python tools/watch_3dgut_queue.py --folder-id {fid} --interval 30 "
        "--iterations 30000 >> logs/watch_3dgut.log 2>&1 &",
    ),
}


def list_folder_all(folder_id: str) -> list[tuple[str, str]]:
    """Public Drive folder → [(filename, file_id)] for EVERY listed file."""
    url = f"https://drive.google.com/drive/folders/{folder_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="ignore")
    pairs: dict[str, str] = {}
    for m in re.finditer(r'data-id="([^"]+)"[^>]*data-tooltip="([^"]+\.[A-Za-z0-9]{2,5})"', html):
        pairs[m.group(2)] = m.group(1)
    for m in re.finditer(
        r'\\x22(1[-A-Za-z0-9_]{20,})\\x22,\\x5b\\x22' + re.escape(folder_id)
        + r'\\x22\\x5d,\\x22([^\\]+?\.[A-Za-z0-9]{2,5})\\x22', html):
        pairs[m.group(2)] = m.group(1)
    return sorted(pairs.items())


def load_state() -> dict:
    if STATE.is_file():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {"seen_drive": [], "seen_results": [], "seen_status": {}}


def watcher_pid(script: str) -> int | None:
    out = subprocess.run(["pgrep", "-f", script], capture_output=True, text=True)
    for pid in out.stdout.split():
        if pid.strip().isdigit():
            return int(pid)
    return None


def restart_watcher(name: str, cmd: str, dry: bool) -> str:
    if dry:
        return f"[dry-run] would restart {name}"
    subprocess.run(
        cmd.format(fid=FOLDER_ID),
        shell=True, cwd=ROOT,
        executable="/bin/bash",
        env={**__import__("os").environ,
             "PATH": f"{Path.home()}/miniconda3/envs/assetpipe/bin:" + __import__("os").environ["PATH"]},
    )
    time.sleep(2)
    return f"restarted {name} (pid {watcher_pid(WATCHERS[name][0])})"


def norm_name(fname: str) -> str:
    """Any zip → CrateScan-<8HEX>.zip so the existing chain accepts it."""
    if re.fullmatch(r"CrateScan-[A-Fa-f0-9]{8}\.zip", fname):
        return fname
    h = hashlib.sha1(fname.encode()).hexdigest()[:8].upper()
    return f"CrateScan-{h}.zip"


def autopublish(dry: bool) -> tuple[list[str], list[str]]:
    """Finished assets → the customer dashboard. Returns (actions, anomalies).

    A subprocess, not an import. autopublish shells out to publish_dashboard,
    which can run for minutes on a cold ~390 MB upload; behind a process
    boundary a crash, a hang or a SystemExit anywhere in that chain cannot take
    watcher supervision down with it, and the timeout is actually enforceable.

    Its own lock handles the overlap this creates: a publish that outlives the
    10-minute cron interval simply makes the next tick's autopublish a no-op.
    """
    cmd = [sys.executable, str(ROOT / "tools" / "autopublish.py")]
    if dry:
        cmd.append("--dry-run")
    try:
        # Slightly longer than autopublish's own publish timeout so it gets to
        # release its lock rather than being killed still holding it.
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              timeout=3000)
    except subprocess.TimeoutExpired:
        return [], ["autopublish timed out — check logs/agent/autopublish.log"]
    except Exception as e:  # noqa: BLE001 — never break the cron loop
        return [], [f"autopublish failed to start: {type(e).__name__}: {e}"]

    summary: dict = {}
    try:
        summary = json.loads((AGENT_DIR / "autopublish_last.json").read_text())
    except Exception:
        pass
    published = summary.get("published") or []
    failed = summary.get("failed") or []

    def tail() -> str:
        """The most informative last line, stderr first.

        `(proc.stdout or proc.stderr or "")` short-circuited on stdout, and
        autopublish's Logger writes every line to stdout — so stderr, the only
        place an uncaught traceback goes, was always discarded and the report
        quoted a harmless verdict line instead of the actual error. Whatever
        this returns also ends up in the anomaly list that main() builds the
        haiku escalation prompt from, so it has to be the real one.
        """
        for stream in (proc.stderr, proc.stdout):
            lines = (stream or "").strip().splitlines()
            if lines:
                return lines[-1].strip()
        return "see logs/agent/autopublish.log"

    verb = "would publish" if dry else "PUBLISHED to dashboard"
    if proc.returncode == 0:
        if not published:
            return [], []                       # steady state: nothing new
        return [f"{verb}: {', '.join(published)}"], []
    if proc.returncode == 2:
        ready = summary.get("ready") or []
        return [], ["autopublish: no blob token — "
                    f"{', '.join(ready) or 'asset(s)'} ready but not uploaded. "
                    "Create ~/.config/3dasset/blob.env (see docs/DASHBOARD.md)"]
    if proc.returncode == 4:
        return [], ["autopublish: publish lock is wedged — " + tail()]
    if proc.returncode == 5:
        # Some files did not upload. Those ids are deliberately absent from the
        # ledger, so the next tick retries them on its own — but it has to be
        # visible: a permanent failure (413, revoked token, a file deleted
        # underneath) would otherwise retry forever in silence while the
        # customer sees an asset with missing downloads. This is the anomaly
        # that used to be an unqualified "PUBLISHED" line.
        acts = [f"{verb}: {', '.join(published)}"] if published else []
        return acts, ["autopublish: PARTIAL publish — "
                      f"{', '.join(failed) or 'some asset(s)'} not fully "
                      "uploaded and NOT recorded as published (retried next "
                      "tick; if it repeats, the failure is permanent). "
                      + tail()]
    return [], [f"autopublish exited {proc.returncode}: " + tail()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    findings: list[str] = []
    anomalies: list[str] = []
    actions: list[str] = []

    # 1. Drive scan (all files, all watched folders)
    files: list[tuple[str, str]] = []
    for fid_ in FOLDER_IDS:
        try:
            files += list_folder_all(fid_)
        except Exception as e:  # noqa: BLE001
            anomalies.append(f"Drive folder {fid_} listing failed: {e}")
    new = [(n, fid) for n, fid in files if n not in state["seen_drive"]]
    for name, fid in new:
        if name.lower().endswith(".zip"):
            dest = ROOT / "captures" / "inbox" / norm_name(name)
            if (ROOT / "captures" / "done" / dest.name).is_file() or \
               (ROOT / "captures" / "status" / f"{dest.stem}.json").is_file():
                state["seen_drive"].append(name)  # already processed — skip
                continue
            if args.dry_run:
                actions.append(f"[dry-run] would pull {name} → {dest.name}")
            elif _download_drive_file(fid, dest):
                actions.append(f"pulled new Drive zip {name} → inbox/{dest.name}")
            else:
                anomalies.append(f"failed to download new Drive zip {name} ({fid})")
                continue
        else:
            anomalies.append(
                f"non-zip upload on Drive: {name} — photos-only intake not automated yet "
                f"(needs marker scale: tools/scale_from_marker.py)")
        state["seen_drive"].append(name)

    # 2. Watcher health
    for wname, (script, cmd) in WATCHERS.items():
        pid = watcher_pid(script)
        if pid:
            findings.append(f"{wname}: alive (pid {pid})")
        else:
            anomalies.append(f"{wname}: DEAD")
            actions.append(restart_watcher(wname, cmd, args.dry_run))

    # 3. New results + failures
    outbox = ROOT / "captures" / "outbox"
    for z in sorted(outbox.glob("*_RESULT.zip")):
        if z.name not in state["seen_results"]:
            findings.append(f"NEW RESULT: {z.name} ({z.stat().st_size // 1_000_000} MB)")
            state["seen_results"].append(z.name)
    for st in sorted((ROOT / "captures" / "status").glob("CrateScan-*.json")):
        try:
            d = json.loads(st.read_text())
        except Exception:
            continue
        key = st.stem
        sig = f"{d.get('ok')}|{d.get('usable_asset')}"
        if state["seen_status"].get(key) == sig:
            continue
        state["seen_status"][key] = sig
        if not d.get("ok"):
            anomalies.append(f"session {key} failed: {(d.get('stdout_tail') or ['?'])[-1]}")

    # 3b. Publish finished assets to the customer dashboard. This is the last
    # automatic step: computation lands here, the scan appears on the website.
    # It lives here rather than in watch_3dgut_queue because the publisher also
    # ships cad_out/ and sim_out/ artifacts, which never touch the GPU queue.
    pub_actions, pub_anomalies = autopublish(args.dry_run)
    actions += pub_actions
    anomalies += pub_anomalies

    # 4. Disk
    du = subprocess.run(["df", "--output=avail", "-BG", str(ROOT)], capture_output=True, text=True)
    free_gb = int(du.stdout.strip().split()[-1].rstrip("G"))
    if free_gb < MIN_FREE_GB:
        anomalies.append(f"disk low: {free_gb}G free (< {MIN_FREE_GB}G)")

    # Report
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# pipeline agent — {ts}", ""]
    lines += [f"- drive files listed: {len(files)}  (new this run: {len(new)})",
              f"- disk free: {free_gb}G", ""]
    if actions:
        lines += ["## actions"] + [f"- {a}" for a in actions] + [""]
    if anomalies:
        lines += ["## anomalies"] + [f"- {a}" for a in anomalies] + [""]
    lines += ["## status"] + [f"- {f}" for f in findings]

    # AI escalation on anomalies
    if anomalies and not args.no_ai:
        try:
            prompt = (
                "You are the on-call agent for a 3D-scan pipeline (Drive → fuse → "
                "TRELLIS/3DGUT route → export). Diagnose these findings in ≤6 lines, "
                "most-urgent first, with one concrete fix command each:\n"
                + "\n".join(f"- {a}" for a in anomalies))
            claude_bin = str(Path.home() / ".local" / "bin" / "claude")
            out = subprocess.run(
                [claude_bin, "-p", prompt, "--model", "claude-haiku-4-5-20251001"],
                capture_output=True, text=True, timeout=120)
            if out.returncode == 0 and out.stdout.strip():
                lines += ["", "## AI diagnosis", out.stdout.strip()]
        except Exception as e:  # noqa: BLE001
            lines += ["", f"(AI escalation unavailable: {e})"]

    report = "\n".join(lines) + "\n"
    (AGENT_DIR / "latest.md").write_text(report)
    (AGENT_DIR / f"report-{datetime.now().strftime('%Y%m%d-%H%M')}.md").write_text(report)
    if not args.dry_run:
        STATE.write_text(json.dumps(state, indent=2) + "\n")
    print(report)
    return 1 if anomalies else 0


if __name__ == "__main__":
    sys.exit(main())
