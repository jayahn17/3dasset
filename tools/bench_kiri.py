#!/usr/bin/env python3
"""Submit a bench_export bundle to the Kiri Engine API and download the result.

Kiri is the object-scan industry benchmark: RGB-only photogrammetry / neural
reconstruction / 3DGS. It never sees our depth or poses, and its output is
NOT metric — score it with bench_compare.py against our dims.json truth.

Setup (once):
    1. Developer account: https://www.kiriengine.app/api/signup
    2. Key:               https://www.kiriengine.app/api/keys
    3. export KIRI_API_KEY=kiri-...
    Pricing: $1/scan, 10 free credits on signup ($500 min recharge after).

Usage:
    python tools/bench_kiri.py bench_out/CrateScan-BE53A423 --mode photo
    python tools/bench_kiri.py bench_out/CrateScan-BE53A423 --mode 3dgs --mesh
    python tools/bench_kiri.py --status <serialize-id> --out bench_out/x/kiri_photo
    python tools/bench_kiri.py --balance

Modes: photo (photogrammetry), featureless (smooth/reflective objects),
3dgs (Gaussian splat PLY, --mesh adds a mesh). Results auto-delete server-side
after 3 days — this tool downloads immediately on success.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import zipfile

import requests

BASE = "https://api.kiriengine.app/api/v1/open"
UPLOAD_PATHS = {
    "photo": "/photo/image",
    "featureless": "/featureless/image",
    "3dgs": "/3dgs/image",
}
STATUS_NAMES = {-1: "uploading", 0: "processing", 1: "FAILED", 2: "done",
                3: "queuing", 4: "expired"}


def _key() -> str:
    key = os.environ.get("KIRI_API_KEY", "")
    if not key:
        raise SystemExit(
            "KIRI_API_KEY is not set.\n"
            "  1. Sign up (10 free credits): https://www.kiriengine.app/api/signup\n"
            "  2. Create a key:              https://www.kiriengine.app/api/keys\n"
            "  3. export KIRI_API_KEY=kiri-..."
        )
    return key


def _headers() -> dict:
    return {"Authorization": f"Bearer {_key()}"}


def _check(resp: requests.Response) -> dict:
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok", False) or body.get("code") != 0:
        raise SystemExit(f"Kiri API error {body.get('code')}: {body.get('msg')}")
    return body["data"]


def balance() -> int:
    data = _check(requests.get(f"{BASE}/balance", headers=_headers(), timeout=30))
    return int(data["balance"])


def upload(bundle_dir: str, mode: str, *, mesh: bool, mask: bool,
           file_format: str, quality: int) -> str:
    photo_dir = os.path.join(bundle_dir, "photos")
    photos = sorted(
        os.path.join(photo_dir, f) for f in os.listdir(photo_dir)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    )
    if len(photos) < 20:
        raise SystemExit(f"Kiri needs >=20 photos, bundle has {len(photos)} "
                         f"(re-run bench_export with --photos 60)")
    if len(photos) > 300:
        photos = photos[:300]
        print(f"note: capped at 300 photos (Kiri max)", file=sys.stderr)

    data: dict = {"fileFormat": file_format}
    if mode == "photo":
        data.update(modelQuality=str(quality), textureQuality="1",
                    isMask=str(int(mask)), textureSmoothing="0")
    elif mode == "3dgs":
        data.update(isMesh=str(int(mesh)), isMask=str(int(mask)))

    files = [("imagesFiles", (os.path.basename(p), open(p, "rb"), "image/jpeg"))
             for p in photos]
    try:
        print(f"uploading {len(photos)} photos to {mode} endpoint "
              f"(1 credit = $1)...")
        body = _check(requests.post(
            f"{BASE}{UPLOAD_PATHS[mode]}", headers=_headers(),
            data=data, files=files, timeout=600,
        ))
    finally:
        for _, (_, fh, _) in files:
            fh.close()
    return body["serialize"]


def wait_and_download(serialize: str, out_dir: str, *,
                      poll_s: int = 20, timeout_s: int = 45 * 60) -> str:
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    while True:
        data = _check(requests.get(
            f"{BASE}/model/getStatus", headers=_headers(),
            params={"serialize": serialize}, timeout=30,
        ))
        status = int(data["status"])
        name = STATUS_NAMES.get(status, str(status))
        print(f"  [{time.time() - t0:5.0f}s] status: {name}")
        if status == 2:
            break
        if status in (1, 4):
            raise SystemExit(f"Kiri job {serialize} ended as {name}")
        if time.time() - t0 > timeout_s:
            raise SystemExit(
                f"Timed out after {timeout_s}s. Job may still finish — retry:\n"
                f"  python tools/bench_kiri.py --status {serialize} --out {out_dir}"
            )
        time.sleep(poll_s)

    data = _check(requests.get(
        f"{BASE}/model/getModelZip", headers=_headers(),
        params={"serialize": serialize}, timeout=30,
    ))
    url = data["modelUrl"]
    zip_path = os.path.join(out_dir, "kiri_result.zip")
    print(f"downloading {url[:80]}...")
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(zip_path, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(out_dir)
    print(f"extracted -> {out_dir}: {sorted(os.listdir(out_dir))}")
    return out_dir


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("bundle", nargs="?",
                    help="bench_export output dir (has photos/)")
    ap.add_argument("--mode", default="photo",
                    choices=["photo", "featureless", "3dgs"])
    ap.add_argument("--mesh", action="store_true",
                    help="3dgs mode: also produce a mesh")
    ap.add_argument("--no-mask", action="store_true",
                    help="disable auto object masking")
    ap.add_argument("--format", default="glb", dest="file_format",
                    choices=["obj", "fbx", "stl", "ply", "glb", "gltf", "usdz", "xyz"])
    ap.add_argument("--quality", type=int, default=0,
                    help="photo mode modelQuality: 0=High 1=Med 2=Low 3=Ultra")
    ap.add_argument("--status", metavar="SERIALIZE",
                    help="resume: poll+download an existing job")
    ap.add_argument("--out", help="output dir (default <bundle>/kiri_<mode>)")
    ap.add_argument("--balance", action="store_true", help="print credit balance")
    args = ap.parse_args()

    if args.balance:
        print(f"Kiri balance: {balance()} credits")
        return
    if args.status:
        out = args.out or "."
        wait_and_download(args.status, out)
        return
    if not args.bundle:
        ap.error("need a bench_export bundle dir (or --status/--balance)")

    out_dir = args.out or os.path.join(args.bundle, f"kiri_{args.mode}")
    serialize = upload(
        args.bundle, args.mode, mesh=args.mesh, mask=not args.no_mask,
        file_format=args.file_format, quality=args.quality,
    )
    print(f"job accepted: serialize={serialize}")
    with open(os.path.join(args.bundle, f"kiri_{args.mode}_job.json"), "w") as fh:
        json.dump({"serialize": serialize, "mode": args.mode,
                   "file_format": args.file_format}, fh, indent=2)
    wait_and_download(serialize, out_dir)
    print(
        "\nScore it against our metric truth:\n"
        f"  python tools/bench_compare.py {out_dir}/<model file> "
        f"--truth demo_out/<session>/dims.json --label kiri_{args.mode}"
    )


if __name__ == "__main__":
    main()
