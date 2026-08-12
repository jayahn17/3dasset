#!/usr/bin/env python3
"""Give an AliceVision reconstruction real-world scale using the ARKit poses.

The two halves of the problem are complementary, not competing:

  * AliceVision reconstructs excellent geometry but in arbitrary SfM units —
    Structure-from-Motion cannot recover absolute scale from images alone.
  * The RGB-D session carries ARKit camera poses in METRES, and those poses are
    good even when the depth FUSION of them is poor. Fusing a handful of depth
    frames into an object is where the RGB-D route falls down; the camera track
    itself is a different and much easier quantity.

So: solve the similarity transform (scale + rotation + translation) that maps
AliceVision's camera centres onto ARKit's, and the scale factor is the metre
conversion for the whole reconstruction. Umeyama gives the closed form.

The RESIDUAL is the point of this script as much as the scale. A good fit means
the two camera tracks genuinely agree and the scale can be trusted; a large one
means ARKit drifted or SfM bent, and the number must not be quoted.
"""
import json
import re
import sys
from pathlib import Path

import numpy as np

sfm_path = Path(sys.argv[1])
session = Path(sys.argv[2])
mode = sys.argv[3] if len(sys.argv) > 3 else "video"   # how filenames map to frames

sfm = json.loads(sfm_path.read_text())
pose_by_id = {p["poseId"]: p["pose"]["transform"] for p in sfm["poses"]}

sfm_centres, names = {}, {}
for v in sfm["views"]:
    t = pose_by_id.get(v["poseId"])
    if t is None:
        continue
    name = Path(v["path"]).name
    sfm_centres[name] = np.array([float(x) for x in t["center"]], dtype=np.float64)
print(f"  AliceVision poses: {len(sfm_centres)}")

man = json.loads((session / "manifest.json").read_text())
frames = man.get("keyframes") or man.get("frames")
ark = {}
for i, fr in enumerate(frames):
    c2w = np.array([float(x) for x in fr["pose"]], dtype=np.float64).reshape(4, 4)
    ark[Path(fr["color"]).name] = c2w[:3, 3]          # camera centre, metres
    ark[f"{i:05d}.jpg"] = c2w[:3, 3]                  # video prep renamed to index
print(f"  ARKit poses (metric): {len(frames)}")

pairs = [(n, sfm_centres[n], ark[n]) for n in sfm_centres if n in ark]
print(f"  matched by filename : {len(pairs)}")
if len(pairs) < 6:
    raise SystemExit("  too few matches to fit a similarity transform")

A = np.stack([p[1] for p in pairs])   # SfM units
B = np.stack([p[2] for p in pairs])   # metres

# --- Umeyama similarity: B ~= s * R @ A + t -------------------------------
mA, mB = A.mean(0), B.mean(0)
A0, B0 = A - mA, B - mB
C = (B0.T @ A0) / len(A)
U, D, Vt = np.linalg.svd(C)
S = np.eye(3)
if np.linalg.det(U) * np.linalg.det(Vt) < 0:
    S[2, 2] = -1
R = U @ S @ Vt
varA = (A0 ** 2).sum() / len(A)
s = float(np.trace(np.diag(D) @ S) / varA)
t = mB - s * R @ mA

pred = (s * (R @ A.T).T) + t
err = np.linalg.norm(pred - B, axis=1)
span = float(np.linalg.norm(B.max(0) - B.min(0)))

print()
print(f"  SCALE (SfM unit -> metres): {s:.6f}")
print(f"  fit residual: mean {err.mean()*1000:.1f} mm | median {np.median(err)*1000:.1f} mm | max {err.max()*1000:.1f} mm")
print(f"  camera track span: {span:.2f} m   residual as % of span: {100*err.mean()/span:.2f}%")
verdict = ("TRUSTWORTHY" if err.mean() < 0.05 and err.mean()/span < 0.02
           else "SUSPECT — do not quote")
print(f"  verdict: {verdict}")
print()
result = {"scale": s, "residual_mean_m": float(err.mean()),
          "residual_max_m": float(err.max()), "n_pairs": len(pairs),
          "span_m": span, "verdict": verdict,
          # The full similarity, not just the scale: R also carries the SfM
          # frame into ARKit's gravity-aligned Y-up world, i.e. it is what
          # makes the mesh render upright, not only metric.
          "rotation": R.tolist(), "translation": t.tolist(),
          "target_frame": "arkit_world_y_up"}
print(json.dumps(result))
if len(sys.argv) > 4:
    Path(sys.argv[4]).write_text(json.dumps(result, indent=2))
    print(f"  transform written to {sys.argv[4]}")
