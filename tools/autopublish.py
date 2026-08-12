#!/usr/bin/env python3
"""Publish finished scans to the customer dashboard, unattended.

The last automatic step in the chain: the GPU box finishes an asset, this
notices, and the scan appears on the Vercel site with nobody typing anything.
Driven from tools/pipeline_agent.py on the 10-minute cron.

It never re-implements publishing. It answers one question — *which assets are
ready and not yet published* — then shells out to tools/publish_dashboard.py,
which owns the upload, the manifest merge and the blob store.

    python tools/autopublish.py --dry-run --verbose   # what would go, why, and
                                                      # whether this box could
                                                      # publish it at all
    python tools/autopublish.py                       # publish what is ready
    python tools/autopublish.py --force DC22F084      # republish one asset
    python tools/autopublish.py --list                # every asset + skip reason

The blob token comes from ~/.config/3dasset/blob.env (mode 0600, outside the
repo). docs/DASHBOARD.md has the one-time setup, and `--dry-run` is what
verifies it: it exits 0 only when a real publish would work, and otherwise with
the exit code a real run would fail with (2 no token, 1 no manifest URL).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
PUBLISHER = ROOT / "tools" / "publish_dashboard.py"
CACHE_DIR = ROOT / ".publish_cache"
AGENT_DIR = ROOT / "logs" / "agent"
LEDGER = AGENT_DIR / "publish_ledger.json"
LOCK = AGENT_DIR / "autopublish.lock"
LOG = AGENT_DIR / "autopublish.log"
SUMMARY = AGENT_DIR / "autopublish_last.json"
STATUS_DIR = ROOT / "captures" / "status"
GPU_DONE = STATUS_DIR / ".gpu_route_done.json"
LOCAL_MANIFEST = ROOT / "web" / "public" / "manifest.json"

# Where the blob token may live, most specific first. Outside the repo on
# purpose: a gitignored in-repo file still gets swept into `--uploader local`
# copies and result zips, and survives a `git add -f`.
ENV_FILES = [
    os.environ.get("DASHBOARD_ENV_FILE"),
    str(Path.home() / ".config" / "3dasset" / "blob.env"),
    str(ROOT / ".blob.env"),
]

TOKEN_KEY = "BLOB_READ_WRITE_TOKEN"
URL_KEY = "DASHBOARD_MANIFEST_URL"
# A read-write token is vercel_blob_rw_<store>_<secret>; both the uploader and
# derive_manifest_url read the store id back out of it by splitting on "_".
TOKEN_SHAPE = "vercel_blob_rw_"

# What run_publisher always asks publish_dashboard for. Named constants because
# three things have to agree on them: the command we build, the blob path the
# manifest is PUT to, and which .publish_cache/<prefix>.json record is ours.
# --prefix is never passed, so the publisher's default applies.
PUBLISH_PREFIX = "dashboard"
PUBLISH_ACCESS = "private"
MANIFEST_BLOB_PATH = f"{PUBLISH_PREFIX}/manifest.json"

# A capture id, as publish_dashboard's scan_key produces it. Anything that is
# not 8 hex is scan_key's directory-name fallback — `runs`, `mouse`, `box_demo`,
# `bench_A7C9_fixed`, `crate_20260730_scooter`. Those are internal benchmark and
# debug output. Automation must never put them on a customer dashboard, so the
# id shape *is* the allowlist.
ASSET_ID = re.compile(r"^[0-9A-F]{8}$")

# Written at the END of a stage, after the artifacts they describe: asset.json
# and dims.json are dumped last by rgbd_object_asset (they reference the ply /
# glb / splat paths, so they cannot exist before those do), cad_report.json by
# splat_to_cad, sim_export.json by sim-export. Their presence is the pipeline's
# own "this stage completed" signal — we do not invent a new marker file.
# route_decision.json is deliberately NOT here: the router writes it *before*
# the route runs, so it says "started", not "finished".
READY_MARKERS = ("asset.json", "dims.json", "cad_report.json", "sim_export.json")

# How long an output tree must be untouched before we believe it. A 3DGUT train
# writes into demo_out/<id>_3dgut for tens of minutes while cron ticks every 10,
# and the .splat export itself takes seconds — publish mid-write and the
# customer downloads a truncated file that opens as garbage.
QUIET_SECONDS = 300

# Breaking a lock whose owner is still uploading would run two manifest PUTs
# against the same stable pathname and one would lose the other's assets, so a
# live pid is always respected. This timeout only covers the case where the pid
# is dead but the file survived (kill -9, reboot, OOM). A full cold publish is
# ~390 MB, which is well under an hour on this uplink; 2 h leaves generous room
# and still self-heals within a few cron ticks instead of needing a human.
STALE_LOCK_SECONDS = 7200

# Ceiling on one publish_dashboard run. Killing it mid-upload is safe: the
# publisher tolerates per-file failures, its content-hash cache skips what
# already landed, and a killed run records nothing at all — so the next tick
# simply resumes.
PUBLISH_TIMEOUT = 2700

LOG_MAX_BYTES = 2_000_000

# publish_dashboard's exit code for "some files failed, the rest landed", as
# distinct from 1, which means nothing was written at all.
#
# This is THE bug this file was rebuilt around. The publisher used to return 0
# for a run that lost 3 of 13 files, autopublish recorded the whole batch as
# published, and because a failed upload does not change the SOURCE tree the
# signature never moved again — so classify() answered "already published,
# unchanged" forever and those customer downloads were gone silently. Worse for
# an asset that was already live: merge_manifests holds its old record, so the
# new files never reached the manifest either.
#
# The exit code is never trusted on its own. run_publisher also reads the
# failure count out of the manifest the publisher just wrote and scrapes the
# failed ids out of its output, so a regression in the exit code cannot bring
# the silent version back. Any OTHER non-zero rc records nothing at all.
PUB_RC_PARTIAL = 5

# autopublish's own rc for "part of the batch did not fully publish". Distinct
# from 1 so pipeline_agent can name the assets that are missing and escalate,
# instead of reporting an unqualified success.
RC_PARTIAL = 5

# Bumped whenever signature()'s inputs change. Stored per ledger record: without
# it, adding the hero image to the digest would move EVERY stored signature at
# once, classify() would read that as "changed since last publish", and the 15
# baselined assets — hand-made test fixtures included — would stampede onto the
# customer dashboard on the first tick after the upgrade.
SIG_VERSION = 2

# An O_EXCL lock is created empty and gets its pid a moment later. A reader that
# lands in that window sees an unparseable lock; treating it as a dead owner and
# breaking it would defeat the exclusion the O_EXCL just bought. Anything
# younger than this with no readable pid is assumed to be a live owner mid-write.
LOCK_CREATE_GRACE = 30


# ---------------------------------------------------------------------------
# plumbing


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


class Logger:
    """stdout for the operator, logs/agent/autopublish.log for the record."""

    def __init__(self, verbose: bool) -> None:
        self.verbose = verbose
        self.lines: list[str] = []
        AGENT_DIR.mkdir(parents=True, exist_ok=True)
        # cron.log is already 697 KB and unrotated; do not add a second one.
        try:
            if LOG.is_file() and LOG.stat().st_size > LOG_MAX_BYTES:
                LOG.replace(LOG.with_suffix(".log.1"))
        except OSError:
            pass

    def __call__(self, msg: str, *, debug: bool = False) -> None:
        if debug and not self.verbose:
            return
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        self.lines.append(line)
        print(line, flush=True)

    def flush(self) -> None:
        if not self.lines:
            return
        try:
            with LOG.open("a") as fh:
                fh.write("\n".join(self.lines) + "\n")
        except OSError:
            pass
        self.lines = []


def load_env_file(log: Logger) -> str | None:
    """Read KEY=VALUE secrets, without ever letting the file override a real env.

    An explicit `export BLOB_READ_WRITE_TOKEN=...` in the shell must win, so a
    human debugging with a different token is not silently switched back to the
    cron one.
    """
    for cand in ENV_FILES:
        if not cand:
            continue
        p = Path(cand).expanduser()
        if not p.is_file():
            continue
        try:
            text = p.read_text()
        except OSError as e:
            log(f"cannot read {p}: {e}")
            continue
        loaded = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and val and key not in os.environ:
                os.environ[key] = val
                loaded.append(key)
        if loaded:
            # Names only. The value never reaches stdout or the log.
            log(f"loaded {', '.join(sorted(loaded))} from {p}", debug=True)
        return str(p)
    return None


def setup_hint(log: Logger, env_file: str | None, *, need_token: bool,
               need_url: bool) -> None:
    """The one copy of "how to fix the env file".

    Printed by the --dry-run environment check and by the live no-token path.
    They are the same instruction to the same human, and two copies is how the
    two drift into disagreeing about which lines the file needs.
    """
    if need_token:
        log("  Get the token: Vercel dashboard > Storage > Blob store "
            "store_sf4PvVI6x7HEvyHw > copy the vercel_blob_rw_... value.")
    if env_file:
        log(f"  Add the missing line(s) to {env_file}:")
    else:
        log("  Create the env file — outside the repo, mode 0600:")
        log("    mkdir -p ~/.config/3dasset && chmod 700 ~/.config/3dasset")
        log("    install -m 600 /dev/null ~/.config/3dasset/blob.env")
        log("    $EDITOR ~/.config/3dasset/blob.env   # do not echo the token, "
            "it lands in ~/.bash_history")
    if need_token:
        log(f"    {TOKEN_KEY}=vercel_blob_rw_...")
    if need_url:
        log(f"    {URL_KEY}=https://<store>.{PUBLISH_ACCESS}.blob."
            f"vercel-storage.com/{MANIFEST_BLOB_PATH}")
    log('  docs/DASHBOARD.md, "One-time setup", has the whole procedure.')


def recorded_manifest_url(token: str) -> tuple[str | None, Path]:
    """The manifest URL the last successful publish recorded, if there is one.

    The publisher's second choice after --manifest-url / $DASHBOARD_MANIFEST_URL
    and the reason a box with no DASHBOARD_MANIFEST_URL line can still merge
    safely — so the dry-run verdict has to look here too, or it would report a
    working box as broken.

    Validated exactly as UploadLedger validates its own head: a record written
    for a different store, prefix or access is not about our destination, and
    the publisher discards the whole file when the head disagrees. Reading it
    directly (rather than constructing an UploadLedger) is deliberate — this
    runs with no uploader and, in the failure case being diagnosed, no token.
    """
    path = CACHE_DIR / f"{PUBLISH_PREFIX}.json"
    doc = _load_json(path, None)
    if not isinstance(doc, dict):
        return None, path
    store = token.split("_")[3] if token.count("_") >= 4 else ""
    if not store:
        return None, path
    want = {"dest": f"vercel:{store}",
            "prefix": PUBLISH_PREFIX, "access": PUBLISH_ACCESS}
    pub = sys.modules.get("publish_dashboard")
    version = getattr(getattr(pub, "UploadLedger", None), "VERSION", None)
    if version is not None:
        want["version"] = version
    if any(doc.get(k) != v for k, v in want.items()):
        return None, path
    return doc.get("manifest_url") or None, path


class DryEnv(NamedTuple):
    rc: int                    # 0 ready, else the rc a REAL run would exit with
    manifest_url: str | None   # what run_publisher would pass as --manifest-url
    note: str


def dry_env_report(log: Logger) -> DryEnv:
    """--dry-run's answer to "could this box actually publish?".

    docs/DASHBOARD.md offers `--dry-run --verbose` as THE check for the one-time
    blob.env setup, and this used to be unreachable: the dry path returned at
    "nothing to publish" or at the command preview, both before load_env_file()
    ran. A box with no token, or with the DASHBOARD_MANIFEST_URL line missing,
    got a clean exit 0 from the documented verification and found out from cron
    ten minutes later — as the rc-3-forever MERGE ABORTED loop. So the dry path
    loads the environment for real and reports what it found.

    Presence and provenance only. The token VALUE never reaches stdout or the
    log; nothing here is worth leaking a write credential for.

    The rc is 0 when a real publish would work and otherwise the exit code a
    REAL run would fail with, so the dry verdict and the live one cannot give
    two different answers about the same box:

      2  no token — _run stops there, before the publisher is ever started.
      1  a token but no manifest URL that merge can trust. The publisher would
         fall back to a URL derived from the token; a 404 on a guess is
         indistinguishable from an empty dashboard, so it aborts with its rc 3,
         which autopublish reports as rc 1 — on every tick, forever.
    """
    # Captured before the file is read: load_env_file never overwrites a value
    # already in the environment, so anything here came from the shell.
    exported = {k for k in (TOKEN_KEY, URL_KEY) if os.environ.get(k)}
    env_file = load_env_file(log)
    token = os.environ.get(TOKEN_KEY)
    url = os.environ.get(URL_KEY)
    recorded, cache_path = recorded_manifest_url(token or "")

    def origin(key: str) -> str:
        return "exported in this shell" if key in exported else str(env_file)

    log("[dry-run] publish environment — can this box publish for real?")
    if env_file:
        log(f"[dry-run]   env file: {env_file}")
    else:
        log(f"[dry-run]   env file: NONE — none of these exists: "
            f"{', '.join(p for p in ENV_FILES if p)}")

    warned = False
    # Structural, not cosmetic: the store id is token.split("_")[3] in both the
    # uploader and derive_manifest_url, so fewer than four underscores means
    # there is no store to talk to and every PUT is rejected. That is a failure,
    # not a warning — pasting the store id where the token goes has to be caught
    # here rather than by cron. The vercel_blob_rw_ prefix is the documented
    # shape but nothing reads it, so a mismatch only warns.
    bad_token = token is not None and token.count("_") < 4
    if token:
        log(f"[dry-run]   {TOKEN_KEY}: present, from {origin(TOKEN_KEY)} "
            f"(presence only — the value is never printed)")
        if bad_token:
            log(f"[dry-run]     NOT A BLOB TOKEN: no store id in it. A blob RW "
                f"token is {TOKEN_SHAPE}<store>_<secret>; the store id is read "
                f"back out of it, so every upload would be rejected.")
        elif not token.startswith(TOKEN_SHAPE):
            warned = True
            log(f"[dry-run]     WARNING: does not start with {TOKEN_SHAPE}, "
                f"which is the documented shape of a blob RW token.")
    else:
        log(f"[dry-run]   {TOKEN_KEY}: MISSING")

    if url:
        log(f"[dry-run]   {URL_KEY}: {url} (from {origin(URL_KEY)})")
        if urlparse(url).path.strip("/") != MANIFEST_BLOB_PATH:
            warned = True
            # An EXPLICIT url that 404s is taken at face value: "nothing
            # published yet", merge onto {}, and this run becomes the entire
            # catalogue. So a typo here is destructive, not merely unhelpful.
            log(f"[dry-run]     WARNING: that path is not /{MANIFEST_BLOB_PATH},"
                f" which is where the publisher PUTs the manifest. Merge would "
                f"read one location and write another; a 404 on an explicitly "
                f"given URL is trusted as an empty dashboard, so the next real "
                f"publish would replace the catalogue with just its own assets.")
    elif recorded:
        log(f"[dry-run]   {URL_KEY}: not set — merge would use the URL the last "
            f"successful publish recorded: {recorded}")
        log(f"[dry-run]     (from {cache_path}; delete that file and this box "
            f"has no manifest URL at all)")
    else:
        log(f"[dry-run]   {URL_KEY}: MISSING — and {cache_path} records no URL "
            f"either, so merge would have only a guess to work from")

    if not token or bad_token:
        log("[dry-run] VERDICT: this box CANNOT publish — "
            + ("no blob token. A real run exits 2 before it starts the "
               "publisher, so nothing would reach the dashboard."
               if not token else
               "the value set as the blob token is not one. A real run would "
               "get past the token check and then fail every upload."))
        setup_hint(log, env_file, need_token=True, need_url=not (url or recorded))
        return DryEnv(2, url or recorded,
                      "no blob token" if not token else "malformed blob token")
    if not (url or recorded):
        log("[dry-run] VERDICT: this box CANNOT publish safely — a token, but "
            "no manifest URL. The publisher would guess one from the token, "
            "refuse to merge onto a guess that 404s (MERGE ABORTED, its rc 3, "
            "reported here as rc 1), and do that on every 10-minute tick until "
            "the URL is configured.")
        setup_hint(log, env_file, need_token=False, need_url=True)
        return DryEnv(1, None, "no manifest URL")
    if warned:
        # It would run and exit 0 — which is exactly why this cannot be a bare
        # "READY". The warned-about cases do the wrong thing successfully.
        log(f"[dry-run] VERDICT: READY WITH WARNINGS — a real publish would run "
            f"and merge onto {url or recorded}, but read the WARNING above: it "
            f"would not do what you want.")
        return DryEnv(0, url or recorded, "ready, with warnings")
    log(f"[dry-run] VERDICT: READY — a real publish would upload to the "
        f"{PUBLISH_ACCESS} blob store and merge onto {url or recorded}.")
    return DryEnv(0, url or recorded, "ready")


def acquire_lock(log: Logger) -> tuple[Path | None, str]:
    """(lock, status) where status is ok | busy | wedged.

    cron fires every 10 minutes with no flock wrapper and a publish can run for
    minutes, so overlap is the normal case, not the edge case.

    Taking the lock is ONE syscall that both tests and claims it:
    os.open(O_CREAT|O_EXCL). It used to be `if LOCK.is_file()` at the top and
    `LOCK.write_text()` twenty lines later — a check-then-write with a window
    wide enough for a human running --force as the cron tick starts. Both
    "acquired", both ran publish_dashboard, and both PUT the same stable
    dashboard/manifest.json pathname, which is precisely what this lock exists
    to prevent.
    """
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    # Two passes at most: the second only happens after clearing a dead owner's
    # lock, and it must not win by force — another process may have cleared it
    # first, in which case losing the re-take is the lock working correctly.
    for attempt in (1, 2):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            pass
        except OSError as e:
            log(f"cannot create {LOCK}: {e}")
            return None, "wedged"
        else:
            with os.fdopen(fd, "w") as fh:
                fh.write(f"{os.getpid()} {time.time():.0f}\n")
            return LOCK, "ok"

        pid, started = 0, 0.0
        try:
            parts = LOCK.read_text().split()
            pid, started = int(parts[0]), float(parts[1])
        except FileNotFoundError:
            continue                      # owner released it; try to take it
        except Exception:
            pass                          # empty or garbled — handled below
        if not started:
            try:
                started = LOCK.stat().st_mtime
            except OSError:
                continue
        alive = pid > 0 and Path(f"/proc/{pid}").exists()
        age = time.time() - started
        if not pid and age < LOCK_CREATE_GRACE:
            # See LOCK_CREATE_GRACE: almost certainly an owner between the
            # O_EXCL create and writing its pid.
            log(f"another autopublish is claiming the lock ({age:.0f}s ago) — skip")
            return None, "busy"
        if alive and age < STALE_LOCK_SECONDS:
            log(f"another autopublish is running (pid {pid}, {age / 60:.0f} min) — skip")
            return None, "busy"
        if alive:
            # Deliberately not broken: see STALE_LOCK_SECONDS. A human has to
            # look, because killing a live uploader is worse than waiting.
            log(f"WEDGED: autopublish pid {pid} has held the lock {age / 3600:.1f} h "
                f"— inspect it, then `kill {pid} && rm {LOCK}`")
            return None, "wedged"
        if attempt == 2:
            log("another autopublish took the lock while it was being cleared — skip")
            return None, "busy"
        log(f"clearing stale lock from dead pid {pid or '?'} ({age / 60:.0f} min old)")
        try:
            LOCK.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            log(f"cannot clear {LOCK}: {e}")
            return None, "wedged"
    return None, "busy"


def release_lock(lock: Path | None) -> None:
    """Remove the lock only if it is still ours.

    A stale-lock breaker in another process may already have cleared and re-taken
    it (we would have to be dead for that, but a paused process looks dead
    enough to /proc for exactly as long as its pid is gone). Unlinking that
    owner's lock would let a third process in.
    """
    if not lock:
        return
    try:
        owner = int(lock.read_text().split()[0])
    except Exception:
        owner = os.getpid()   # unreadable: assume ours rather than leak a lock
    if owner != os.getpid():
        return
    try:
        lock.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# is this asset finished?


def newest_mtime(sources: list[str]) -> float:
    """Newest mtime anywhere under the asset's output dirs.

    Deliberately the WHOLE tree, not just the deliverables the publisher would
    ship. During a 3DGUT train the only things moving are checkpoints under
    3dgut_runs/, which the publisher's allowlist skips — read only the
    deliverables and a training run looks perfectly quiet. The full walk of
    demo_out + cad_out + sim_out is ~6700 files in 0.02 s, so there is nothing
    to optimise here.
    """
    newest = 0.0
    for src in sources:
        base = ROOT / src
        for dirpath, _dirnames, filenames in os.walk(base):
            try:
                newest = max(newest, os.stat(dirpath).st_mtime)
            except OSError:
                pass
            for fn in filenames:
                try:
                    newest = max(newest, os.lstat(os.path.join(dirpath, fn)).st_mtime)
                except OSError:
                    pass
    return newest


def _deliverables_digest(rec: dict):
    """(path, size, mtime) over everything gather() put in rec["files"]."""
    h = hashlib.sha256()
    for f in sorted(rec["files"], key=lambda x: x["path"]):
        try:
            mtime = (ROOT / f["path"]).stat().st_mtime_ns
        except OSError:
            mtime = 0
        h.update(f"{f['path']}\0{f['bytes']}\0{mtime}\n".encode())
    return h


def signature(rec: dict) -> str:
    """Content signature of an asset: which files, how big, how new.

    Cheap on purpose — hashing 390 MB every 10 minutes to answer "did anything
    change" would be absurd, and the publisher does its own content hashing at
    upload time to decide what to actually send.

    The hero image is part of it, and that is not decoration. gather() keeps it
    in `preview_src`, which is NOT one of rec["files"] — those come only from the
    DELIVERABLES patterns — and the two do not land together: in
    demo_out/CrateScan-7C3DD25E_3dgut every deliverable was written at 19:10:59
    and render_check/contact_sheet.png at 20:08:27, 57 minutes later, far past
    the 300 s quiet gate. Hash the deliverables alone and an asset published in
    that window keeps a preview-less card forever: the preview appearing (or
    being regenerated) never moves the digest, so classify() says "already
    published, unchanged" and the image is never uploaded. newest_mtime() does
    walk render_check/, so the quiet timer was fine — only the change detector
    was blind.
    """
    h = _deliverables_digest(rec)
    src = rec.get("preview_src")
    if src:
        # gather() stores an absolute path; hash it relative to the repo so the
        # digest does not depend on where the checkout lives.
        p = Path(src)
        try:
            st = p.stat()
            size, mtime = st.st_size, st.st_mtime_ns
        except OSError:
            size, mtime = 0, 0
        h.update(f"preview\0{os.path.relpath(p, ROOT)}\0{size}\0{mtime}\n".encode())
    return h.hexdigest()[:32]


def signature_v1(rec: dict) -> str:
    """The pre-preview signature, kept only to migrate old ledger records.

    v1 hashed the deliverables and nothing else, so on upgrade every stored
    digest differs from v2's — and "differs" means "publish". Recomputing v1
    tells a scheme change apart from a content change: same v1 digest means the
    asset genuinely did not move, so the record is restamped in place instead of
    republishing the entire historical backlog.
    """
    return _deliverables_digest(rec).hexdigest()[:32]


def write_ledger(ledger: dict) -> None:
    """Write the ledger atomically.

    A torn ledger reads back as no ledger at all, which re-triggers the baseline
    seed and forgets everything that has been published.
    """
    tmp = LEDGER.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger, indent=2) + "\n")
    os.replace(tmp, LEDGER)


def gather_assets(log: Logger) -> dict[str, dict] | None:
    """publish_dashboard.gather() is the single source of truth for grouping.

    Imported, not reimplemented: a second copy of scan_key would drift from the
    publisher's and we would pass --only ids that match nothing.
    """
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import publish_dashboard as pub
    except Exception as e:  # noqa: BLE001 — a broken publisher is a soft failure
        log(f"cannot import tools/publish_dashboard.py: {type(e).__name__}: {e}")
        return None
    try:
        return pub.gather(False)
    except TypeError:
        return pub.gather()


def classify(assets: dict[str, dict], ledger: dict, quiet: float,
             force: set[str]) -> list[dict]:
    """Every asset with a verdict and, when skipped, the reason why."""
    gpu_done = _load_json(GPU_DONE, {})
    now = time.time()
    rows: list[dict] = []

    for key, rec in sorted(assets.items(), key=lambda kv: -kv[1]["updated"]):
        row = {
            "id": key,
            "files": len(rec["files"]),
            "kinds": rec.get("kinds", []),
            "updated": rec["updated"],
            "sig": None,
            "publish": False,
            "migrate": False,
            "reason": "",
        }
        rows.append(row)
        forced = key in force

        # --force does NOT reach the two gates below, and the docs say so. They
        # are what keeps internal output off a customer dashboard and what
        # guarantees a stage actually finished; the escape hatch is for the GPU
        # verdict and the quiet timer. Saying it out loud beats the silent skip
        # an operator used to get.
        if not ASSET_ID.match(key):
            row["reason"] = "not a capture id (internal benchmark/debug output)"
            if forced:
                row["reason"] += " — --force cannot override this gate"
            continue

        names = {f["name"] for f in rec["files"]}
        markers = sorted(names & set(READY_MARKERS))
        if not markers:
            row["reason"] = "no completion marker yet " \
                            f"(want one of {'/'.join(READY_MARKERS)})"
            if forced:
                row["reason"] += " — --force cannot override this gate " \
                                 "(the stage never finished; there may be " \
                                 "nothing whole to ship)"
            continue
        row["markers"] = markers

        # The GPU route is the long pole. If this capture went through the
        # queue, its verdict is authoritative — publishing while the route is
        # still in flight ships whatever half of the asset exists so far.
        #
        # NOTE: captures/status/.3dgut_queue.lock is NOT the busy signal it
        # looks like. watch_3dgut_queue takes it once at boot and releases it in
        # a `finally` when the process exits, so with --iterations 30000 it is
        # held permanently. Gating on it would mean autopublish never ran.
        gpu = gpu_done.get(f"CrateScan-{key}")
        if (STATUS_DIR / f"CrateScan-{key}.json").is_file():
            if gpu is None:
                row["reason"] = "GPU route queued or in flight (no ledger entry)"
                if not forced:
                    continue
            elif not gpu.get("ok"):
                row["reason"] = f"GPU route failed (route={gpu.get('route')}) " \
                                f"— fix it or --force {key}"
                if not forced:
                    continue

        quiet_for = now - newest_mtime(rec.get("sources", []))
        row["quiet_for"] = round(quiet_for)
        if quiet_for < quiet:
            row["reason"] = f"still being written ({quiet_for:.0f}s quiet, need {quiet:.0f}s)"
            if not forced:
                continue

        row["sig"] = signature(rec)
        prev = ledger.get("assets", {}).get(key)
        if forced:
            row["publish"] = True
            row["reason"] = "forced"
        elif prev is None:
            row["publish"] = True
            row["reason"] = "new"
        elif prev.get("signature") == row["sig"]:
            row["reason"] = "already published, unchanged"
        elif prev.get("sig_version", 1) < SIG_VERSION \
                and prev.get("signature") == signature_v1(rec):
            # The digest moved because the SCHEME moved (the hero image joined
            # it), not because the asset did. Restamp the record; republishing
            # here would ship the whole baselined backlog in one tick.
            row["migrate"] = True
            row["reason"] = ("already published, unchanged "
                             f"(ledger record migrated to signature v{SIG_VERSION})")
        else:
            row["publish"] = True
            row["reason"] = "changed since last publish"
    return rows


# ---------------------------------------------------------------------------
# publish


class PublishResult(NamedTuple):
    rc: int             # publish_dashboard's exit code
    failed: set[str]    # ids it could not fully upload (empty = it named none)
    failures: int       # files that failed, per the manifest stats; -1 unknown


# A line that both admits a problem and names an asset. Every shape the
# publisher emits carries the id: a per-file failure prints the blob dest, whose
# first path segment IS the asset id ("!! DC22F084/scan/mesh_mm.stl: HTTPError"),
# the hero image prints "!! preview DC22F084: ...", and the merge prints
# "holding published DC22F084 — this run had upload failures for it".
# Requiring the failure word is what keeps the OTHER hold line out — "holding
# published X — it is newer than what we built" is not an upload failure, and
# treating it as one would make every tick retry an asset forever.
FAILED_LINE = re.compile(r"!!|fail", re.I)


def parse_failed_ids(text: str, wanted: list[str]) -> set[str]:
    """Which of the ids we asked for appear on one of the publisher's failure lines.

    Intersected with this batch on purpose: it makes a false positive
    impossible ("!! manifest upload failed" names no asset) and it does not
    depend on the exact wording, so the publisher can improve its message
    without silently disabling this.
    """
    hit: set[str] = set()
    for line in text.splitlines():
        if not FAILED_LINE.search(line):
            continue
        for i in wanted:
            if re.search(rf"(?<![0-9A-Za-z]){re.escape(i)}(?![0-9A-Za-z])", line):
                hit.add(i)
    return hit


def manifest_failures(since: float) -> int:
    """The failure count publish_dashboard recorded in the manifest it just wrote.

    Belt and braces for the exit code. The demonstrated bug had rc 0 on a run
    that lost 3 of 13 files while a stats block reading
    {"files": 2, "failures": 3} sat on disk the whole time — so this reads the
    evidence instead of only believing the verdict.

    -1 when the manifest is older than this run: publish_dashboard deliberately
    writes nothing when every upload failed, and the previous run's file must
    never be mistaken for this run's result.
    """
    try:
        # 2 s of slack for coarse mtime granularity on network/older filesystems.
        if LOCAL_MANIFEST.stat().st_mtime < since - 2:
            return -1
    except OSError:
        return -1
    stats = _load_json(LOCAL_MANIFEST, {}).get("stats") or {}
    n = stats.get("failures")
    return int(n) if isinstance(n, (int, float)) and not isinstance(n, bool) else -1


def publisher_cmd(ids: list[str], manifest_url: str | None,
                  verbose: bool) -> list[str]:
    """The exact publish_dashboard invocation — built in ONE place.

    The real run executes this and the dry run previews it. They used to be two
    separate literals and the preview's was missing --manifest-url, so the
    command shown to an operator verifying their setup was not the command
    automation runs, and the one thing that setup step gets wrong — the missing
    DASHBOARD_MANIFEST_URL line — could not be seen in the preview.

    The token is never here. argv is world-readable in `ps`, this command line
    is logged, and cron.log gets pasted into reports.
    """
    cmd = [
        sys.executable, str(PUBLISHER),
        "--uploader", "vercel",
        "--access", PUBLISH_ACCESS,
        "--merge",
    ]
    if manifest_url:
        cmd += ["--manifest-url", manifest_url]
    for i in ids:
        # Full 8-hex, never a fragment: --only is a substring match against the
        # uppercased key, so a short id would sweep in unrelated assets.
        cmd += ["--only", i]
    if verbose:
        cmd.append("--verbose")
    return cmd


def run_publisher(ids: list[str], token: str, manifest_url: str | None,
                  verbose: bool, log: Logger) -> PublishResult:
    cmd = publisher_cmd(ids, manifest_url, verbose)

    # Token by environment, never on argv — argv is world-readable in `ps`, and
    # this command line ends up in cron.log and in agent reports people paste.
    env = {**os.environ, TOKEN_KEY: token}

    log(f"publishing {len(ids)}: {', '.join(ids)}")
    log("  " + " ".join(cmd), debug=True)   # safe to log: no secret on argv
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True,
                              text=True, timeout=PUBLISH_TIMEOUT)
    except subprocess.TimeoutExpired:
        log(f"publish_dashboard exceeded {PUBLISH_TIMEOUT}s — killed; "
            f"next run resumes (uploads already landed are cached)")
        # Killed mid-upload: not one id in this batch is proven, so none of them
        # may be recorded. rc 1 says exactly that to the caller.
        return PublishResult(1, set(ids), -1)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if out:
        for line in out.splitlines():
            log(f"  | {line}", debug=not (
                line.startswith("manifest") or " assets · " in line))
    if err:
        for line in err.splitlines():
            log(f"  ! {line}")

    failed = parse_failed_ids(f"{out}\n{err}", ids)
    failures = manifest_failures(started)
    if proc.returncode == PUB_RC_PARTIAL or failures > 0 or failed:
        log(f"publish_dashboard reported "
            f"{failures if failures >= 0 else 'some'} failed file(s)"
            + (f" across {', '.join(sorted(failed))}" if failed else ""))
    elif proc.returncode != 0:
        log(f"publish_dashboard exited {proc.returncode} — nothing recorded, "
            f"will retry next run")
    return PublishResult(proc.returncode, failed, failures)


def recheck_quiet(todo: list[dict], assets: dict[str, dict], quiet: float,
                  force: set[str], log: Logger) -> tuple[list[dict], list[dict]]:
    """Re-walk the output trees with the lock held, right before uploading.

    classify() sampled `now - newest_mtime(...)` ONCE, and everything after it —
    reading the token file, waiting for this lock, and then a publisher run that
    may stream for PUBLISH_TIMEOUT — happens later with nothing re-walking the
    tree. A write that starts inside that gap is uploaded mid-write: the
    uploader hands `requests` an open file and Content-Length comes from fstat
    at request time, so a file being rewritten in place is PUT short. And it
    sticks — the publisher's upload cache records that URL under the hash of the
    file's FINAL content, so the next run skips the re-upload and the truncation
    becomes permanent.

    One extra walk (~6700 files, 0.02 s) closes the classify→upload half of the
    window. The other half — a write that starts while the publisher is already
    running — cannot be closed from here, so _run also re-walks afterwards and
    refuses to record any id whose tree moved during the upload.
    """
    now = time.time()
    ready: list[dict] = []
    moved: list[dict] = []
    for r in todo:
        quiet_for = now - newest_mtime(assets.get(r["id"], {}).get("sources", []))
        r["quiet_for"] = round(quiet_for)
        if quiet_for >= quiet:
            ready.append(r)
        elif r["id"] in force:
            log(f"{r['id']}: written {quiet_for:.0f}s ago — uploading anyway "
                f"because --force was given; a file still being written is PUT "
                f"truncated")
            ready.append(r)
        else:
            moved.append(r)
            log(f"{r['id']}: tree moved after it was classified "
                f"({quiet_for:.0f}s quiet, need {quiet:.0f}s) — deferred to the "
                f"next run rather than uploading a half-written file")
    return ready, moved


def seed_baseline(rows: list[dict], ledger: dict, force: set[str],
                  log: Logger, dry: bool) -> None:
    """First run: record what already exists instead of publishing it.

    The same reasoning as pipeline_agent's first-run stampede guard. There are
    16 finished captures on this box and only 3 were ever published — the rest
    include hand-made test fixtures (AB12CD34, BC34EF56). An automation that
    ships its entire backlog the first time it runs is not automation, it is an
    accident. From here on anything that finishes, or is reprocessed so its
    signature moves, publishes itself; the historical backlog stays a human
    decision via --force.

    Forced ids are left out of the baseline on purpose: they are about to be
    published, and if that publish fails they must remain unrecorded so the
    next run retries them.
    """
    published = {a["id"] for a in _load_json(LOCAL_MANIFEST, {}).get("assets", [])
                 if a.get("id")}
    n = 0
    for row in rows:
        if row["sig"] is None or row["id"] in force:
            continue
        n += 1
        ledger["assets"][row["id"]] = {
            "signature": row["sig"],
            "sig_version": SIG_VERSION,
            "files": row["files"],
            "updated": row["updated"],
            "published_at": None,
            "baseline": True,
            "status": "published-before-automation"
            if row["id"] in published else "baseline",
        }
        # Suppress it for this run only. A later change to the tree moves the
        # signature and it publishes normally.
        if row["publish"]:
            row["publish"] = False
            row["reason"] = "baseline (first run) — publishes when it changes"
    verb = "would record" if dry else "recorded"
    log(f"first run — {verb} {n} existing asset(s) as the baseline; "
        f"none of them are published.")
    log("  New scans from now on publish by themselves. To ship one of these "
        "now: python tools/autopublish.py --force <ID>")


# ---------------------------------------------------------------------------


def run(dry: bool, verbose: bool, force: set[str], quiet: float,
        list_only: bool) -> int:
    log = Logger(verbose)
    try:
        return _run(log, dry, verbose, force, quiet, list_only)
    finally:
        log.flush()


def _run(log: Logger, dry: bool, verbose: bool, force: set[str], quiet: float,
         list_only: bool) -> int:
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {"at": time.time(), "ready": [], "published": [], "failed": [],
               "deferred": [], "skipped": 0, "rc": 0, "note": ""}

    def finish(rc: int, note: str = "") -> int:
        summary["rc"] = rc
        summary["note"] = note
        try:
            SUMMARY.write_text(json.dumps(summary, indent=2) + "\n")
        except OSError:
            pass
        return rc

    assets = gather_assets(log)
    if assets is None:
        return finish(1, "publish_dashboard import failed")

    ledger = _load_json(LEDGER, None)
    first_run = ledger is None
    if first_run:
        ledger = {"version": 1, "assets": {}}
    ledger.setdefault("assets", {})

    rows = classify(assets, ledger, quiet, force)
    if first_run:
        seed_baseline(rows, ledger, force, log, dry or list_only)
    migrated = [r for r in rows if r["migrate"]]
    todo = [r for r in rows if r["publish"]]
    summary["ready"] = [r["id"] for r in todo]
    summary["skipped"] = len(rows) - len(todo)

    if list_only or verbose:
        for r in rows:
            mark = "PUBLISH" if r["publish"] else "skip   "
            log(f"  {mark} {r['id']:24s} {r['files']:3d} files  {r['reason']}")

    # --force that quietly does nothing is how an operator ends up believing an
    # asset shipped. Both cases are said out loud, at any verbosity.
    for fid in sorted(force - {r["id"] for r in rows}):
        log(f"--force {fid}: no asset has that id — `--list` shows every id "
            f"(they are the 8-hex capture id, uppercase)")
    for r in rows:
        if r["id"] in force and not r["publish"]:
            log(f"--force {r['id']}: NOT published — {r['reason']}")

    if list_only:
        return finish(0, "list only")

    if first_run and not dry:
        # Persist the baseline even if the forced publish below fails — the
        # suppression must not silently expire into a 15-asset stampede.
        write_ledger(ledger)
    elif migrated and not dry:
        # Restamp records whose digest only moved because the signature scheme
        # did. Written before any publishing so it survives a failure below.
        for r in migrated:
            entry = dict(ledger["assets"].get(r["id"], {}))
            entry["signature"] = r["sig"]
            entry["sig_version"] = SIG_VERSION
            ledger["assets"][r["id"]] = entry
        write_ledger(ledger)
        log(f"migrated {len(migrated)} ledger record(s) to signature "
            f"v{SIG_VERSION}; they are unchanged, nothing was republished")

    if dry:
        # Checked BEFORE "nothing to publish" returns: the documented
        # verification of the one-time setup is `--dry-run --verbose`, and on a
        # steady-state box there is never anything to publish, so a report that
        # only ran when something was ready was a report nobody ever saw.
        env = dry_env_report(log)
        # A dry run stops before the publisher on purpose. publish_dashboard
        # --dry-run no longer clobbers web/public/manifest.json (it writes
        # manifest.dry-run.json beside it), but it is still a write into
        # web/public/ and a walk of every asset, and this command must stay
        # read-only outside logs/agent.
        if todo:
            log(f"[dry-run] would publish {len(todo)}: "
                f"{', '.join(r['id'] for r in todo)}")
            log("[dry-run] command: "
                + " ".join(publisher_cmd([r["id"] for r in todo],
                                         env.manifest_url, verbose)))
            summary["published"] = [r["id"] for r in todo]
        else:
            log(f"[dry-run] nothing to publish ({len(rows)} asset(s) checked)")
        return finish(env.rc, f"dry run: {env.note}")

    if not todo:
        log(f"nothing to publish ({len(rows)} asset(s) checked)", debug=True)
        return finish(0, "nothing to publish")

    env_file = load_env_file(log)
    token = os.environ.get(TOKEN_KEY)
    if not token:
        # Actionable, and the ledger stays untouched so this retries by itself
        # the moment the file appears. Nothing here prints the token.
        log("NO BLOB TOKEN — cannot publish "
            f"{', '.join(r['id'] for r in todo)}")
        log(f"  (searched: {', '.join(p for p in ENV_FILES if p)})"
            if not env_file else f"  ({env_file} has no {TOKEN_KEY})")
        setup_hint(log, env_file, need_token=True,
                   need_url=not os.environ.get(URL_KEY))
        log("  Verify the fix without uploading anything: "
            "python tools/autopublish.py --dry-run --verbose")
        return finish(2, "no blob token")

    lock, status = acquire_lock(log)
    if lock is None:
        return finish(0 if status == "busy" else 4, status)

    try:
        # The quiet gate was sampled in classify(), before the token load and
        # before the wait for this lock. Re-validate it now that nothing else
        # can start a publish — see recheck_quiet for what a mid-write upload
        # does and why it never heals by itself.
        todo, deferred = recheck_quiet(todo, assets, quiet, force, log)
        summary["deferred"] = [r["id"] for r in deferred]
        if not todo:
            return finish(0, "every candidate moved after classification")

        ids = [r["id"] for r in todo]
        summary["ready"] = ids
        # The other half of the same window: nothing re-walks during the
        # publisher's run, so snapshot each tree and compare afterwards.
        before = {i: newest_mtime(assets[i].get("sources", [])) for i in ids}

        res = run_publisher(ids, token, os.environ.get(URL_KEY),
                            verbose, log)
        if res.rc not in (0, PUB_RC_PARTIAL):
            # 1/2/3/4: the publisher wrote nothing, or nothing that can be
            # trusted. Not one id is recorded, so the next tick retries the
            # whole batch. An exit code this file does not recognise lands here
            # too, which is the safe side to be wrong on.
            return finish(1, f"publish_dashboard rc={res.rc}")

        # Three independent signals, any one of which means "do not claim this
        # batch published": the exit code, the failure count in the manifest the
        # publisher just wrote, and the ids named on its failure lines.
        partial = res.rc == PUB_RC_PARTIAL or res.failures > 0 or bool(res.failed)
        lost = set(res.failed)
        if partial and not lost:
            # Failures happened but nothing named an asset. Attribute them to
            # the whole batch: recording a "published" that cannot be proven is
            # exactly what made this class of data loss silent.
            lost = set(ids)
            log("publish_dashboard reported failures without naming an asset — "
                "recording none of this batch")

        moved = [i for i in ids
                 if newest_mtime(assets[i].get("sources", [])) > before[i]]
        for i in moved:
            log(f"{i}: its output tree changed WHILE it was uploading — not "
                f"recorded, because whatever landed may be truncated. The "
                f"publisher caches by the file's final content hash, so a plain "
                f"retry would skip it; force the re-PUT:")
            log(f"    python tools/publish_dashboard.py --uploader vercel "
                f"--access private --merge --force-upload --only {i}")
        lost |= set(moved)

        now = time.time()
        done = [r for r in todo if r["id"] not in lost]
        for r in done:
            ledger["assets"][r["id"]] = {
                "signature": r["sig"],
                "sig_version": SIG_VERSION,
                "files": r["files"],
                "updated": r["updated"],
                "published_at": now,
                "baseline": False,
                "status": "published",
            }
        write_ledger(ledger)
        summary["published"] = [r["id"] for r in done]
        summary["failed"] = sorted(lost)

        if lost:
            log(f"PARTIAL PUBLISH: {', '.join(sorted(lost))} did not fully "
                f"upload"
                + (f"; {', '.join(r['id'] for r in done)} did" if done else ""))
            log("  The failed ids are deliberately NOT in the ledger, so the "
                "next run retries them. See the ! lines above for the files.")
            return finish(RC_PARTIAL,
                          f"partial publish: {len(lost)} asset(s) not recorded"
                          + (f", {res.failures} file(s) failed"
                             if res.failures > 0 else ""))
        log(f"published {len(done)} asset(s) -> dashboard")
        return finish(0, "published")
    finally:
        release_lock(lock)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be published AND whether this box "
                         "is configured to publish it; upload nothing. Exits 0 "
                         "only if a real publish would work, else with the rc a "
                         "real run would fail with (2 no token, 1 no manifest "
                         "URL)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--force", action="append", default=[], metavar="ID",
                    help="publish this capture id even if unchanged, still "
                         "queued or already published (repeatable)")
    ap.add_argument("--list", action="store_true", dest="list_only",
                    help="show every asset and why it is or is not ready")
    ap.add_argument("--quiet-seconds", type=float, default=QUIET_SECONDS,
                    help=f"an output tree must be untouched this long before it "
                         f"is believed finished (default {QUIET_SECONDS}). "
                         f"DANGEROUS below the default and 0 disables the check "
                         f"entirely: a file still being written is PUT "
                         f"truncated and the upload cache makes that permanent")
    args = ap.parse_args()
    return run(args.dry_run, args.verbose, {f.upper() for f in args.force},
               args.quiet_seconds, args.list_only)


if __name__ == "__main__":
    sys.exit(main())
