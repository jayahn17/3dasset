#!/usr/bin/env python3
"""Fire-and-forget KIRI Engine jobs: submit, then collect results automatically.

WHY THIS EXISTS, AND WHAT IT CANNOT DO

The KIRI web library at https://www.kiriengine.app/webapp/mymodel cannot be
automated. Their developer API has exactly two Model endpoints — "retrieve
status" and "download zip" — and **both require a `serialize` id you already
hold**. There is no list/enumerate endpoint, so scans made in the phone app or
the web app are not reachable programmatically at all; only jobs *you submitted
through the API* can be fetched, because only then do you know the id.

So the automation is: own the ids. Every submission is written to a ledger the
moment it is accepted, and a poller collects finished jobs later. That also
fixes the real operational problem — `tools/bench_kiri.py` BLOCKS for up to 45
minutes waiting on one job, which is fine by hand and useless from cron.

    export KIRI_API_KEY=kiri-...
    python tools/kiri_watch.py --submit bench_out/CrateScan-XXXX --mode 3dgs
    python tools/kiri_watch.py --poll          # from cron; downloads what is ready
    python tools/kiri_watch.py --list

Results are deleted server-side after ~3 DAYS. A submitted job that is never
polled is money spent and thrown away, so --poll belongs on a schedule.

Ledger: captures/status/.kiri_jobs.json   (serialize -> job record)

SCALE HONESTY: KIRI only ever sees RGB, so its output carries no real-world
scale. Every downloaded result gets a NO_METRIC_SCALE.txt beside it — the same
marker tools/rgb_only_trellis.py writes, and the one web/lib/manifest.ts keys
`hasMetricScale()` off. Without it the dashboard would offer a tape measure over
a scale-free mesh. See docs/PIPELINE_E2E.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

LEDGER = REPO / "captures/status/.kiri_jobs.json"
# KIRI deletes results after 3 days; warn well before that.
EXPIRY_DAYS = 3.0
WARN_AFTER_H = 36.0

NO_METRIC = """This asset has NO real-world scale.

It was reconstructed by KIRI Engine from RGB images only. No depth sensor and no
metric reference were involved, so its size is arbitrary and nothing derived
from it may be quoted as a measurement.

Use the RGB-D fuse (dims.json) for any dimension a customer will see.
"""


def log(m: str) -> None:
    print(f"[kiri-watch] {m}", flush=True)


def load() -> dict:
    try:
        return json.loads(LEDGER.read_text())
    except Exception:
        return {}


def save(d: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(d, indent=1))


def _bench():
    """bench_kiri holds the HTTP surface; this module only adds scheduling."""
    import bench_kiri

    return bench_kiri


def submit(bundle: str, mode: str, *, mesh: bool, mask: bool,
           file_format: str, quality: int) -> str:
    bk = _bench()
    serialize = bk.upload(bundle, mode, mesh=mesh, mask=mask,
                          file_format=file_format, quality=quality)
    jobs = load()
    jobs[serialize] = {
        "serialize": serialize,
        "mode": mode,
        "bundle": str(Path(bundle).resolve()),
        "out_dir": str(Path(bundle).resolve() / f"kiri_{mode}"),
        "file_format": file_format,
        "submitted_at": time.time(),
        "state": "submitted",
    }
    save(jobs)
    log(f"submitted {serialize} ({mode}) — 1 credit spent; --poll will collect it")
    return serialize


def poll(once: bool = True) -> dict:
    """Advance every unfinished job by one step. Never blocks on a single job."""
    bk = _bench()
    jobs = load()
    pending = [s for s, j in jobs.items()
               if j.get("state") not in ("done", "failed", "expired")]
    if not pending:
        log("nothing pending")
        return jobs

    import requests

    for serialize in pending:
        job = jobs[serialize]
        age_h = (time.time() - job.get("submitted_at", time.time())) / 3600.0
        try:
            data = bk._check(requests.get(
                f"{bk.BASE}/model/getStatus", headers=bk._headers(),
                params={"serialize": serialize}, timeout=30))
            status = int(data["status"])
        except SystemExit as exc:            # bench_kiri raises SystemExit on API error
            log(f"{serialize}: status check failed — {exc}")
            continue
        except Exception as exc:             # noqa: BLE001 — a flaky poll must not abort
            log(f"{serialize}: {type(exc).__name__}: {exc}")
            continue

        name = bk.STATUS_NAMES.get(status, str(status))
        job["state_raw"] = name
        job["checked_at"] = time.time()

        if status == 2:                      # done
            out = Path(job["out_dir"])
            try:
                bk.wait_and_download(serialize, str(out), poll_s=5, timeout_s=120)
            except SystemExit as exc:
                log(f"{serialize}: download failed — {exc}")
                jobs[serialize] = job
                continue
            (out / "NO_METRIC_SCALE.txt").write_text(NO_METRIC)
            job.update(state="done", downloaded_at=time.time())
            log(f"{serialize}: downloaded -> {out} (+NO_METRIC_SCALE.txt)")
        elif status == 1:
            job["state"] = "failed"
            log(f"{serialize}: FAILED server-side ({age_h:.1f} h old)")
        elif status == 4:
            job["state"] = "expired"
            log(f"{serialize}: EXPIRED — result deleted server-side, credit lost")
        else:
            job["state"] = "submitted"
            msg = f"{serialize}: {name} ({age_h:.1f} h)"
            if age_h > WARN_AFTER_H:
                msg += f" !! results are deleted after {EXPIRY_DAYS:g} days"
            log(msg)
        jobs[serialize] = job

    save(jobs)
    return jobs


def show() -> None:
    jobs = load()
    if not jobs:
        log("no jobs recorded")
        return
    print(f"{'serialize':<26} {'mode':<12} {'state':<10} {'age h':>7}  out")
    for s, j in sorted(jobs.items(), key=lambda kv: kv[1].get("submitted_at", 0)):
        age = (time.time() - j.get("submitted_at", time.time())) / 3600.0
        print(f"{s:<26} {j.get('mode',''):<12} {j.get('state',''):<10} "
              f"{age:>7.1f}  {j.get('out_dir','')}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--submit", metavar="BUNDLE", help="bench_export bundle dir to send")
    ap.add_argument("--mode", default="photo", choices=["photo", "featureless", "3dgs"])
    ap.add_argument("--mesh", action="store_true", help="3dgs: also produce a mesh")
    ap.add_argument("--no-mask", action="store_true")
    ap.add_argument("--format", default="glb", dest="file_format",
                    choices=["obj", "fbx", "stl", "ply", "glb", "gltf", "usdz", "xyz"])
    ap.add_argument("--quality", type=int, default=0)
    ap.add_argument("--poll", action="store_true", help="advance every pending job once")
    ap.add_argument("--list", action="store_true", dest="do_list")
    args = ap.parse_args()

    if not (args.submit or args.poll or args.do_list):
        ap.error("choose one of --submit / --poll / --list")
    if args.do_list:
        show()
        return 0
    if args.submit:
        if not os.environ.get("KIRI_API_KEY"):
            raise SystemExit(
                "KIRI_API_KEY is not set — submitting costs 1 credit ($1) per scan.\n"
                "  key: https://www.kiriengine.app/api/keys")
        submit(args.submit, args.mode, mesh=args.mesh, mask=not args.no_mask,
               file_format=args.file_format, quality=args.quality)
    if args.poll:
        if not os.environ.get("KIRI_API_KEY"):
            raise SystemExit("KIRI_API_KEY is not set — cannot poll")
        poll()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
