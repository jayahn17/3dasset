#!/usr/bin/env python3
"""Fold an extra reconstruction into the block it belongs to.

    python tools/asset_merge.py <file> [--name table] [--dry-run]
    python tools/asset_merge.py --drive-kiri          # pull CrateScans/kiri_engine

One object should be ONE block on the dashboard carrying every reconstruction
of it, not a new card per tool. A KIRI export of the coffee table belongs
*inside* `coffee_table_20260811` next to its photogrammetry mesh, its splat and
its generative mesh — publishing it as `table_kiri` splits one object across two
cards and makes the dashboard read like a file dump.

**Resolution order**, most trustworthy first:

  1. exact block id                    `coffee_table_20260811`
  2. the capture ledger                `crate_20260811_20_14_43` -> chair_20260811
     (captures/status/.recon3_done.json records zip -> asset name, which is the
     only thing that can map a timestamped scan onto its friendly block)
  3. alias table                       captures/status/asset_aliases.json
  4. word overlap                      `table` -> coffee_table_20260811
                                       `sofa`  -> sofa_20260805

Word overlap is last because it is the only one that can be WRONG. It refuses
on a tie: two blocks matching `table` equally well is a question for a human,
not a coin flip, because merging into the wrong block is invisible afterwards.

**What is deliberately NOT copied.** `NO_METRIC_SCALE.txt` is ASSET-level: drop
it into a block that holds a real RGB-D measurement and that block stops
publishing its dimensions. A scale-free KIRI mesh merged into a metric block
must therefore arrive as a *file*, never with its scale marker. It lands as
`kiri_visual.glb`, which `web/lib/formats.ts isAltReconstruction()` already
matches by name (`kiri|kiri_oss|meshroom_visual`) and treats as a second
reconstruction that must not be measured — so the block keeps its own metric
dims and the KIRI mesh is offered for looking.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / "demo_out"
LEDGER = REPO / "captures/status/.recon3_done.json"
ALIASES = REPO / "captures/status/asset_aliases.json"

# Which deliverable name an incoming mesh should take. The name is the contract:
# the publisher's DELIVERABLES allowlist matches on it, and the web layer infers
# provenance from it.
KIND_NAMES = {
    "kiri": "kiri_visual.glb",
    "kiri_obj": "3DModel.obj",
}
# Words that say nothing about WHICH object this is.
NOISE = {"kiri", "engine", "3dmodel", "model", "scan", "crate", "cratescan",
         "export", "mesh", "final", "web", "visual", "asset", "obj", "glb", "zip"}


def log(m: str) -> None:
    print(m, flush=True)


def blocks() -> list[str]:
    """Existing dashboard blocks — a directory under demo_out/ with a deliverable."""
    out = []
    for d in sorted(DEMO.iterdir()):
        if not d.is_dir():
            continue
        if any(d.glob("*.glb")) or any(d.glob("*.splat")) or (d / "object_asset").is_dir():
            out.append(d.name)
    return out


def words(s: str) -> set[str]:
    toks = re.split(r"[^A-Za-z0-9]+", s.lower())
    return {t for t in toks if t and not t.isdigit() and t not in NOISE}


def from_ledger(stem: str) -> str | None:
    """A timestamped capture name only maps to its block via the ledger."""
    try:
        led = json.loads(LEDGER.read_text())
    except Exception:
        return None
    for key, rec in led.items():
        if not isinstance(rec, dict):
            continue
        if Path(key).stem.lower() == stem.lower() or rec.get("name", "").lower() == stem.lower():
            name = rec.get("name")
            if name and (DEMO / name).is_dir():
                return name
    return None


def resolve(stem: str, known: list[str]) -> tuple[str | None, str]:
    """-> (block, why). block is None when nothing is confident enough."""
    for b in known:                                   # 1. exact
        if b.lower() == stem.lower():
            return b, "exact block id"

    led = from_ledger(stem)                           # 2. ledger
    if led:
        return led, "capture ledger"

    try:                                              # 3. alias table
        al = {k.lower(): v for k, v in json.loads(ALIASES.read_text()).items()}
        if stem.lower() in al and al[stem.lower()] in known:
            return al[stem.lower()], "alias table"
    except Exception:
        pass

    w = words(stem)                                   # 4. word overlap
    if not w:
        return None, "name carries no usable words"
    scored = []
    for b in known:
        hit = w & words(b)
        if hit:
            # longer shared words are stronger evidence than short ones
            scored.append((sum(len(x) for x in hit), len(hit), b))
    if not scored:
        return None, f"no block shares a word with {sorted(w)}"
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][:2] == scored[1][:2]:
        return None, (f"ambiguous — {scored[0][2]} and {scored[1][2]} match "
                      f"{sorted(w)} equally well")
    return scored[0][2], f"word match on {sorted(w & words(scored[0][2]))}"


def stage(src: Path, work: Path) -> list[Path]:
    """Unwrap a zip if needed; return the mesh files worth merging."""
    if zipfile.is_zipfile(src):
        work.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(src) as z:
            z.extractall(work)
        root = work
    else:
        root = src.parent
    keep = [p for p in root.rglob("*") if p.suffix.lower() in (".glb", ".obj", ".mtl", ".jpg", ".png")]
    return keep or ([src] if src.suffix.lower() in (".glb", ".obj") else [])


def merge(files: list[Path], block: str, dry: bool) -> list[str]:
    dest = DEMO / block
    written = []
    glbs = [f for f in files if f.suffix.lower() == ".glb"]
    if glbs:
        big = max(glbs, key=lambda p: p.stat().st_size)
        target = dest / KIND_NAMES["kiri"]
        written.append(target.name)
        if not dry:
            shutil.copy2(big, target)
    else:                       # OBJ + MTL + texture only render together
        for f in files:
            if f.suffix.lower() in (".obj", ".mtl", ".jpg", ".png"):
                target = dest / f"3DModel{f.suffix.lower()}"
                written.append(target.name)
                if not dry:
                    shutil.copy2(f, target)
    return written


def publish(block: str) -> int:
    apy = Path.home() / "miniconda3/envs/assetpipe/bin/python"
    return subprocess.run(
        [str(apy if apy.is_file() else sys.executable),
         str(REPO / "tools/publish_dashboard.py"), "--uploader", "vercel",
         "--access", "private", "--only", block], cwd=REPO).returncode


def handle(src: Path, forced: str | None, dry: bool, do_publish: bool) -> int:
    known = blocks()
    stem = forced or src.stem
    block, why = resolve(stem, known)
    if not block:
        log(f"  ?  {src.name}: {why}")
        log(f"     add a line to {ALIASES.relative_to(REPO)} "
            f'(e.g. {{"{stem}": "coffee_table_20260811"}}) or pass --name')
        return 1
    work = REPO / "recon_work" / "merge_staging" / stem
    files = stage(src, work)
    if not files:
        log(f"  !  {src.name}: nothing mergeable inside")
        return 1
    written = merge(files, block, dry)
    log(f"  ok {src.name}  ->  {block}   [{why}]   +{', '.join(written)}")
    if dry or not do_publish:
        return 0
    return publish(block)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--name", help="override the name used for matching")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--drive-kiri", action="store_true",
                    help="pull everything from Drive CrateScans/kiri_engine first")
    args = ap.parse_args()

    srcs = list(args.files)
    if args.drive_kiri:
        sys.path.insert(0, str(REPO))
        import importlib.util
        from tools.recon3_autopilot import list_folder, FOLDERS
        spec = importlib.util.spec_from_file_location(
            "d", REPO / "tools/drive_rgbd_autopilot.py")
        d = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(d)
        _, folders = list_folder(FOLDERS[0][1])
        kid = folders.get("kiri_engine")
        if not kid:
            log("no kiri_engine folder in Drive"); return 1
        kf, _ = list_folder(kid)
        dest = REPO / "captures/kiri_engine"
        dest.mkdir(parents=True, exist_ok=True)
        for n, fid in sorted(kf.items()):
            p = dest / n
            if not (p.exists() and p.stat().st_size > 1000):
                d._download_drive_file(fid, p)
            srcs.append(p)

    if not srcs:
        log("nothing to merge"); return 0
    log(f"blocks: {', '.join(blocks())}\n")
    bad = 0
    for s in srcs:
        bad += 1 if handle(s, args.name, args.dry_run, not args.no_publish) else 0
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
