#!/usr/bin/env python3
"""Push `captures/outbox/*.zip` to the shared Drive folder, and remember what landed.

The pipeline builds RESULT zips and calls rclone to upload them. When rclone
has no remote configured that upload fails, the watcher logs one line and moves
on, and the outbox quietly grows — 659 MB across 15 files by the time anyone
looked. This drains it deliberately and tells you what is still pending.

    python tools/drive_push_outbox.py --check      # auth + what's pending, uploads nothing
    python tools/drive_push_outbox.py --dry-run    # exactly what would go, in order
    python tools/drive_push_outbox.py              # upload everything pending
    python tools/drive_push_outbox.py --only BENCHMARK --only 7C3DD25E

**Setup is a one-time interactive step and cannot be automated** — Google's
OAuth needs a browser:

    rclone config
      n) new remote → name: gdrive → storage: drive
      scope: 1 (full access; "drive.readonly" cannot upload)
      → authorise in the browser as the account that can EDIT the folder
    rclone lsf "gdrive,root_folder_id=1DpLpm4m4TxUn_Vn9Rfhr0o39HKM-Xkbl:"

Viewer access is not enough: the folder must be shared with that account as
**Editor** or every upload 403s.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUTBOX = REPO / "captures" / "outbox"
LEDGER = REPO / "captures" / "status" / ".drive_pushed.json"
FOLDER_ID = "1DpLpm4m4TxUn_Vn9Rfhr0o39HKM-Xkbl"  # CrateScans (the folder the iPad uploads to)
REMOTE = "gdrive"


def remote_spec(folder_id: str) -> str:
    return f"{REMOTE},root_folder_id={folder_id}:"


def rclone(*args: str, timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(["rclone", *args], capture_output=True, text=True,
                          timeout=timeout)


def check_auth(folder_id: str) -> tuple[bool, str]:
    """Can we actually write? Distinguish 'no remote' from 'no permission'."""
    if not any((Path.home() / ".config/rclone/rclone.conf",).__iter__()) or \
            not (Path.home() / ".config/rclone/rclone.conf").is_file():
        return False, ("no rclone config at ~/.config/rclone/rclone.conf — "
                       "run `rclone config` (see this file's docstring)")
    listed = rclone("listremotes", timeout=60)
    if f"{REMOTE}:" not in listed.stdout:
        return False, (f"no '{REMOTE}' remote configured "
                       f"(found: {listed.stdout.strip() or 'none'})")
    probe = rclone("lsf", remote_spec(folder_id), "--max-depth", "1", timeout=120)
    if probe.returncode != 0:
        return False, f"remote exists but the folder is unreachable: {probe.stderr.strip()[:200]}"
    return True, f"ok — folder reachable, {len(probe.stdout.split())} entries"


def load_ledger() -> dict:
    if LEDGER.is_file():
        try:
            return json.loads(LEDGER.read_text())
        except Exception:
            pass
    return {}


def save_ledger(led: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(led, indent=2, sort_keys=True))


def pending(only: list[str], led: dict, force: bool) -> list[Path]:
    files = sorted(OUTBOX.glob("*.zip"))
    if only:
        files = [f for f in files if any(o in f.name for o in only)]
    if force:
        return files
    # Re-upload when the local file changed size since it last landed.
    return [f for f in files
            if led.get(f.name, {}).get("size") != f.stat().st_size]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--folder-id", default=FOLDER_ID)
    ap.add_argument("--only", action="append", default=[],
                    help="substring filter; repeatable")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="report auth + pending, upload nothing")
    ap.add_argument("--force", action="store_true", help="ignore the ledger")
    args = ap.parse_args()

    if not OUTBOX.is_dir():
        print(f"no outbox at {OUTBOX}", file=sys.stderr)
        return 2

    led = load_ledger()
    todo = pending(args.only, led, args.force)
    total = sum(f.stat().st_size for f in todo)
    print(f"outbox   {OUTBOX}")
    print(f"pending  {len(todo)} file(s), {total/1e6:.0f} MB")
    for f in todo:
        print(f"   {f.name:48s} {f.stat().st_size/1e6:8.1f} MB")

    ok, why = check_auth(args.folder_id)
    print(f"\nauth     {'OK' if ok else 'NOT CONFIGURED'} — {why}")

    if args.check or args.dry_run:
        if not args.dry_run and not ok:
            print("\nNothing uploaded. Configure rclone first — see the docstring:")
            print("  python tools/drive_push_outbox.py --help")
        return 0 if ok else 1
    if not ok:
        print("\nrefusing to upload: fix auth first (--help for the one-time setup)",
              file=sys.stderr)
        return 1
    if not todo:
        print("\nnothing to do")
        return 0

    failed = []
    for f in todo:
        print(f"\n→ {f.name} ({f.stat().st_size/1e6:.0f} MB)")
        r = rclone("copyto", str(f), remote_spec(args.folder_id) + f.name,
                   "--progress", "--stats-one-line")
        if r.returncode == 0:
            led[f.name] = {"size": f.stat().st_size, "folder": args.folder_id}
            save_ledger(led)          # after each file: a crash keeps the record
            print(f"   ✓ uploaded")
        else:
            failed.append(f.name)
            print(f"   !! {r.stderr.strip()[:300]}", file=sys.stderr)

    print(f"\n{len(todo)-len(failed)}/{len(todo)} uploaded"
          + (f", failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
