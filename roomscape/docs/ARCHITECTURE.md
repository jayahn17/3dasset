# Roomscape — architecture

The system in one line: **an iPad turns a room into posed RGB-D, a 4080 turns
that into a compressed Gaussian splat plus a collision mesh, and a headset
turns that into a place you can stand in.**

Read [GAMEPLAN.md](GAMEPLAN.md) first for *why*. This document is *what*.

---

## Component map

```
┌─ roomscape/capture/ ────────────────────────────────────────────────┐
│  iPad Pro (LiDAR) — extends mobile/CrateScannerApp                   │
│                                                                      │
│  ARKit session (gravity-aligned, world tracking, scene mesh)        │
│    ├─ keyframe track   1600–2000px JPEG + K + pose        2–4 Hz     │
│    ├─ depth track      256×192 uint16 mm + confidence     matched    │
│    ├─ room mesh        ARMeshAnchor → room_mesh.obj       on finish  │
│    ├─ coverage HUD     which directions are still unseen  live       │
│    └─ sharpness gate   variance-of-Laplacian reject       live       │
│                                                                      │
│  → session/ (manifest v2) → zip → upload                            │
└──────────────────────────────────────────────────────────────────────┘
                                   │  HTTPS, resumable
                                   ▼
┌─ roomscape/service/ ────────────────────────────────────────────────┐
│  ingest API + job queue (on the 4080 box)                            │
│  • verify + unpack + content-address every input                     │
│  • privacy pass: person/face detect → blur before anything persists  │
│  • enqueue reconstruction; registry row from the first byte          │
└──────────────────────────────────────────────────────────────────────┘
                                   ▼
┌─ roomscape/pipeline/ ───────────────────────────────────────────────┐
│  1  normalize     session v2 → canonical frames + intrinsics         │
│  2  pose refine   ARKit priors → GLOMAP/COLMAP BA → fix scale+gravity│
│  3  init          LiDAR depth → dense seed cloud (not SfM sparse)    │
│  4  train         gsplat / splatfacto + depth loss + appearance model│
│  5  clean         floaters, crop to bounds, floor snap, window fix   │
│  6  compress      → compressed splat, ≤80 MB, LOD chunks             │
│  7  proxy         ARKit mesh or nvblox TSDF → collision + navmesh    │
│  8  publish       artifacts + manifest → object store + registry     │
└──────────────────────────────────────────────────────────────────────┘
                                   ▼
┌─ roomscape/viewer-web/  and  roomscape/viewer-quest/ ───────────────┐
│  scene.json  {splat chunks, collision mesh, spawn point, scale, up}  │
│                                                                      │
│  Tier A  WebXR (PlayCanvas, MIT) — Quest browser, desktop, iOS       │
│  Tier A' Quest native (Unity+OpenXR or Vulkan) — 2–3× budget         │
│  Tier B  4080 renders full-fat splat → Air Link stream (home only)   │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Stage contracts

Each stage reads and writes files. No stage imports another's internals; the
boundary is the artifact. This is the same discipline as `assetpipe`'s five
swappable stages, and it means any stage can be replaced (or run by hand) in
isolation.

| Stage | In | Out | Owner module |
|---|---|---|---|
| capture | — | `session/` (manifest v2 + jpg + png16 + obj) | Swift, `mobile/CrateScannerApp` |
| normalize | `session/` | `work/frames.json`, undistorted images | `pipeline/normalize.py` |
| pose refine | `work/frames.json` | `work/sparse/` (COLMAP text model) | `pipeline/poses.py` |
| init | depth + poses | `work/seed.ply` | `pipeline/seed.py` |
| train | sparse model + seed | `work/splat.ply` | `pipeline/train.py` → gsplat |
| clean | `splat.ply` | `work/splat_clean.ply` | `pipeline/clean.py` |
| compress | `splat_clean.ply` | `out/scene.sog` + LOD chunks | `pipeline/compress.py` |
| proxy | room mesh / depth | `out/collision.glb` | `pipeline/proxy.py` |
| publish | `out/` | registry rows + object-store keys | `service/publish.py` |

**Everything after `normalize` is deterministic given a config + input hash.**
That is what makes "re-run every scene when the trainer improves" a command
rather than a project.

---

## Why the poses come from ARKit, and what that buys

A pure video → COLMAP pipeline recovers geometry **up to an arbitrary
similarity transform**: unknown scale, unknown orientation. Every such pipeline
then needs a human to scale and level the scene by hand, or it guesses.

ARKit hands us, for free:

- **Metric scale** — the LiDAR measures in meters, so a door is 2.03 m and VR
  locomotion feels right without calibration.
- **Gravity alignment** — ARKit's default world alignment puts +Y along the
  real up vector, so the floor is already horizontal. No tilt, no VR nausea
  from a 3° lean.
- **A pose per frame** — turning cold SfM (minutes to hours, fails on blank
  walls) into bundle adjustment from a good initialization.
- **A room mesh** — the collision proxy, for free, at capture time.

This is the core reason D2 says iPad-first. It is not that the Quest cameras
are bad; it is that these four properties are exactly the ones a VR walkthrough
needs and exactly the ones monocular capture does not give you.

The one thing ARKit does *not* give us is drift-free tracking over five
minutes, which is why pose refinement is a required stage rather than an
optional one.

---

## The scene bundle

What the viewer downloads. Deliberately small and boring:

```jsonc
// scene.json
{
  "schema": "roomscape/scene/1",
  "id": "scn_01J...",
  "name": "living room",
  "captured_at": "2026-07-30T17:12:16Z",
  "up": [0, 1, 0],              // gravity, from ARKit — not guessed
  "scale": "metric",            // meters, always
  "spawn": { "position": [0, 1.6, 0], "yaw_deg": 0 },
  "bounds": { "min": [-3.1, -0.1, -2.4], "max": [3.4, 2.7, 2.9] },
  "splat": {
    "format": "sog",
    "gaussians": 940000,
    "bytes": 71300000,
    "lod": ["lod0.sog", "lod1.sog", "lod2.sog"]
  },
  "collision": "collision.glb",
  "provenance": { "session": "cap_01J...", "run": "run_01J...", "commit": "abc1234" }
}
```

`up`, `scale`, and `spawn` are the three fields that separate "a splat someone
posted online" from "a place". Most open-source splat viewers make you supply
them by hand. Ours come out of the capture.

---

## Two viewers, one scene format

The web and native viewers must consume the **identical** scene bundle. If they
diverge, we maintain two products. The native app's advantage should come only
from a bigger LOD level and a better sorter — not a different pipeline.

| | WebXR (Tier A) | Native (Tier A') | Local stream (Tier B) |
|---|---|---|---|
| Runtime | PlayCanvas / WebXR in Quest browser | Unity 6 + OpenXR, or Vulkan | desktop OpenXR app on the 4080 |
| Gaussians | 0.5–1.0 M | 1.5–2.5 M | 5–10 M |
| Ship gate | none — a URL | store review | none — home Wi-Fi |
| Use | the product | the store app | the demo, and our own daily driver |

Details and measurement method: [RENDER_TARGETS.md](RENDER_TARGETS.md).

---

## Relationship to `assetpipe`

Roomscape **calls** `assetpipe`; it does not fork it.

- `assetpipe` owns: SfM, splat backends, TSDF fusion, PLY handling, the object
  (generative) pipeline, the asset catalog.
- Roomscape owns: room-scale capture UX, the reconstruction *recipe* tuned for
  rooms, compression, the scene bundle, the viewers, and the system of record.

The doctrine from `docs/SCENE_SPLAT.md` holds and is the dividing line:
**splat = the room you walk through; TRELLIS = the clean objects inside it.**
Roomscape is the first half. If a room scan should also yield catalogued
objects, that is `assetpipe room` running on the same session — a second
consumer of the same capture, not a second capture.
