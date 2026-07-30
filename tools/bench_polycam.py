#!/usr/bin/env python3
"""Benchmark against Polycam (manual web upload — no public capture API).

Polycam's programmatic capture API is contact-sales only
(see docs/INDUSTRY_BENCHMARK.md). This tool still makes a fair side-by-side:

  1. prepare  — validate a bench_export bundle + write upload instructions
  2. ingest   — drop a downloaded Polycam GLB/OBJ/PLY/ZIP into the bench folder
  3. compare  — score vs our metric dims.json (scale + shape)

Polycam Web photogrammetry accepts 20–2000 JPG/PNG (or video 15s–30min):
  https://learn.poly.cam/hc/en-us/articles/30549121659412

Usage:
  # after bench_export.py ...
  python tools/bench_polycam.py prepare bench_out/CrateScan-1B38880A
  # upload photos/ (or polycam_upload.zip) at https://poly.cam → Photogrammetry
  # download GLB/OBJ, then:
  python tools/bench_polycam.py ingest bench_out/CrateScan-1B38880A ~/Downloads/foo.glb
  python tools/bench_polycam.py compare bench_out/CrateScan-1B38880A \\
      --truth demo_out/CrateScan-1B38880A/dims.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

UPLOAD_HELP = "https://learn.poly.cam/hc/en-us/articles/30549121659412"
POLYCAM_WEB = "https://poly.cam/"
CREATE_HINT = (
    "Tools → Photogrammetry Creator and Viewer → Create Photogrammetry Model"
)


def _photos(bundle: Path) -> list[Path]:
    photo_dir = bundle / "photos"
    if not photo_dir.is_dir():
        raise SystemExit(f"No photos/ in {bundle} — run tools/bench_export.py first")
    pics = sorted(
        p
        for p in photo_dir.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    return pics


def cmd_prepare(bundle: Path, *, zip_upload: bool) -> int:
    pics = _photos(bundle)
    n = len(pics)
    if n < 20:
        raise SystemExit(
            f"Polycam needs ≥20 images; bundle has {n}. "
            f"Re-run: python tools/bench_export.py … --photos 60"
        )
    if n > 2000:
        raise SystemExit(f"Polycam max is 2000 images; bundle has {n}")

    out_dir = bundle / "polycam_photo"
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = None
    if zip_upload:
        zip_path = bundle / "polycam_upload.zip"
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in pics:
                zf.write(p, arcname=p.name)
        print(f"✔ upload zip  {zip_path}  ({n} images)")

    readme = out_dir / "UPLOAD.md"
    readme.write_text(
        f"""# Polycam benchmark upload

Bundle: `{bundle.resolve()}`
Photos: **{n}** in `{bundle / "photos"}`
{"Zip: `" + str(zip_path.resolve()) + "`" if zip_path else ""}

Polycam has **no public self-serve capture API** (contact-sales). Benchmark via web:

1. Open [{POLYCAM_WEB}]({POLYCAM_WEB}) and sign in
2. {CREATE_HINT}
3. Upload the **JPG/PNG set** from `photos/` (or unpack the zip)
   - Docs: {UPLOAD_HELP}
   - Enable **Isolate object from environment** for object orbits
   - Enable **Sequential** (frames are already in capture order)
4. Process as **Photogrammetry** (mesh) and/or **Gaussian Splat** (Pro)
5. Export **GLB** (preferred) or OBJ/PLY
6. Ingest + score:

```bash
python tools/bench_polycam.py ingest {bundle} /path/to/polycam_export.glb
python tools/bench_polycam.py compare {bundle} \\
    --truth demo_out/<same-id>/dims.json
```

Fairness note: Polycam sees **RGB only** — not our depth/poses. Metric truth
stays in our `dims.json`. Compare **shape error after rescale**; expect
scale to fail (same as Kiri).
"""
    )
    meta = {
        "service": "polycam",
        "mode": "web_manual",
        "n_photos": n,
        "photos_dir": str((bundle / "photos").resolve()),
        "upload_zip": str(zip_path.resolve()) if zip_path else None,
        "upload_docs": UPLOAD_HELP,
        "create_hint": CREATE_HINT,
        "result_dir": str(out_dir.resolve()),
        "api": "none_public_contact_sales",
    }
    (out_dir / "prepare.json").write_text(json.dumps(meta, indent=2))
    print(f"✔ prepared   {n} photos → {readme}")
    print(f"  open       {POLYCAM_WEB}")
    print(f"  then       ingest downloaded GLB into {out_dir}")
    return 0


def _extract_assets(src: Path, dest: Path) -> list[Path]:
    """Copy or unpack mesh/splat files into dest; return found asset paths."""
    dest.mkdir(parents=True, exist_ok=True)
    exts = {".glb", ".gltf", ".obj", ".ply", ".stl", ".fbx", ".usdz", ".splat"}
    found: list[Path] = []

    if src.is_dir():
        for p in src.rglob("*"):
            if p.suffix.lower() in exts and p.is_file():
                dst = dest / p.name
                shutil.copy2(p, dst)
                found.append(dst)
        return found

    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as zf:
            zf.extractall(dest / "_unzipped")
        for p in (dest / "_unzipped").rglob("*"):
            if p.suffix.lower() in exts and p.is_file():
                dst = dest / p.name
                shutil.copy2(p, dst)
                found.append(dst)
        return found

    if src.suffix.lower() in exts:
        dst = dest / src.name
        shutil.copy2(src, dst)
        found.append(dst)
        return found

    raise SystemExit(f"Unsupported Polycam download: {src} (want glb/obj/ply/zip)")


def cmd_ingest(bundle: Path, download: Path, *, label: str) -> int:
    if not download.exists():
        raise SystemExit(f"Download not found: {download}")
    out_dir = bundle / label
    # clear previous mesh copies but keep UPLOAD.md / prepare.json if same dir
    for p in list(out_dir.glob("*")):
        if p.name in {"UPLOAD.md", "prepare.json"}:
            continue
        if p.is_file():
            p.unlink()
        elif p.is_dir() and p.name == "_unzipped":
            shutil.rmtree(p)
    found = _extract_assets(download, out_dir)
    if not found:
        raise SystemExit(f"No mesh/splat files found in {download}")
    # prefer glb for compare
    preferred = sorted(
        found,
        key=lambda p: (
            0 if p.suffix.lower() == ".glb" else
            1 if p.suffix.lower() == ".obj" else
            2 if p.suffix.lower() == ".ply" else 3,
            p.name,
        ),
    )
    meta = {
        "service": "polycam",
        "label": label,
        "source_download": str(download.resolve()),
        "assets": [str(p.resolve()) for p in preferred],
        "primary": str(preferred[0].resolve()),
    }
    (out_dir / "ingest.json").write_text(json.dumps(meta, indent=2))
    print(f"✔ ingested {len(preferred)} asset(s) → {out_dir}")
    for p in preferred:
        print(f"  {p.name}")
    print(f"  primary  {preferred[0]}")
    print(
        f"\nNext:\n  python tools/bench_polycam.py compare {bundle} "
        f"--truth demo_out/<id>/dims.json --label {label}"
    )
    return 0


def cmd_compare(bundle: Path, *, truth: str, truth_key: str | None,
                label: str) -> int:
    out_dir = bundle / label
    ingest_path = out_dir / "ingest.json"
    if ingest_path.is_file():
        primary = Path(json.loads(ingest_path.read_text())["primary"])
    else:
        cands = sorted(out_dir.glob("*.glb")) + sorted(out_dir.glob("*.obj")) + sorted(
            out_dir.glob("*.ply")
        )
        if not cands:
            raise SystemExit(
                f"No ingested asset in {out_dir}. "
                f"Run: python tools/bench_polycam.py ingest {bundle} DOWNLOAD"
            )
        primary = cands[0]

    # Delegate to bench_compare for one scoring path
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parent / "bench_compare.py"),
        str(primary),
        "--truth",
        truth,
        "--label",
        f"polycam_{label}",
    ]
    if truth_key:
        cmd.extend(["--truth-key", truth_key])
    print(">>", " ".join(cmd), flush=True)
    return os.spawnv(os.P_WAIT, sys.executable, cmd)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare", help="Validate photos + write Polycam upload guide")
    p.add_argument("bundle", type=Path, help="bench_out/<name> from bench_export")
    p.add_argument("--zip", action="store_true", help="Also write polycam_upload.zip")
    p.set_defaults(fn=lambda a: cmd_prepare(a.bundle, zip_upload=a.zip))

    i = sub.add_parser("ingest", help="Copy Polycam download into bench folder")
    i.add_argument("bundle", type=Path)
    i.add_argument("download", type=Path, help="GLB/OBJ/PLY or ZIP from Polycam")
    i.add_argument("--label", default="polycam_photo",
                   help="Subfolder name (default polycam_photo; use polycam_3dgs etc.)")
    i.set_defaults(fn=lambda a: cmd_ingest(a.bundle, a.download, label=a.label))

    c = sub.add_parser("compare", help="Score ingested Polycam asset vs dims.json")
    c.add_argument("bundle", type=Path)
    c.add_argument("--truth", required=True, help="Our dims.json")
    c.add_argument("--truth-key", default=None)
    c.add_argument("--label", default="polycam_photo")
    c.set_defaults(
        fn=lambda a: cmd_compare(
            a.bundle, truth=a.truth, truth_key=a.truth_key, label=a.label
        )
    )

    args = ap.parse_args()
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
