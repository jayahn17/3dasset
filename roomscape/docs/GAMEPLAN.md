# Roomscape — game plan

*Our own Hyperscape: capture a real room, reconstruct it as a Gaussian splat on
our 4080, walk through it in VR — with every byte of capture data staying in
our system.*

Working name **Roomscape**. Do not ship under the name "Hyperscape" — it is
Meta's product name and a trademark problem the day we submit to their store.

---

## 0. What Meta actually does, and where we differ

Meta Hyperscape Capture: the user sweeps a room with a Quest 3 (~5 min), the
scan uploads to Meta's cloud, a fleet of datacenter GPUs trains a Gaussian
splat, and the finished scene is **rendered in the cloud and video-streamed
back** to the headset. That last part is the whole trick — it is why their
scenes look better than anything a standalone headset can rasterize, and it is
also why they need a datacenter and we do not have one.

| | Meta Hyperscape | Roomscape |
|---|---|---|
| Capture device | Quest 3 passthrough cameras (1280×960, mono, no metric depth by default) | **iPad Pro LiDAR** — 12 MP color, 256×192 metric depth, gravity-aligned poses |
| Reconstruction | Meta cloud GPU fleet | one RTX 4080, 16 GB, ours |
| Delivery | cloud-rendered video stream | **on-device render** of a compressed splat (+ optional local PC stream) |
| Where the data lives | Meta | our object store, our registry, our retention policy |
| Quality ceiling | very high (datacenter renderer) | lower per-frame, but *ours*, offline-capable, and no per-user GPU cost |

**We are not trying to beat their renderer.** We are building the same loop —
capture → splat → walkable VR scene — with a capture device that is *better*
than theirs (LiDAR gives us metric scale and gravity for free) and a delivery
model that costs us nothing per viewer.

Two quality tiers, both worth having:

- **Tier A — on-device.** Compressed splat (~0.5–1.5 M gaussians) rendered
  natively on Quest 3 or in the Quest browser via WebXR. Portable, offline,
  store-shippable. This is the product.
- **Tier B — local stream.** Full-fat splat (5–10 M gaussians) rendered on the
  4080 and streamed to the headset over Wi-Fi 6E (Air Link / Steam Link path).
  Hyperscape-class quality inside our own house, zero cloud. This is the demo
  that makes people's jaw drop, and it is nearly free once Tier A exists.

---

## 1. Three hard truths that shape everything

**1. Licensing decides the codebase before performance does.**
The original INRIA `gaussian-splatting` repo and everything built on its
`diff-gaussian-rasterization` (Mip-Splatting, Scaffold-GS, and most 2024-era
papers) ship under a **non-commercial research license** that restricts the
software *and its outputs*. If we train with it, we cannot put the scene in the
Meta store. The whole training and rendering chain must be Apache-2.0 / MIT
from day one. See [OSS_LICENSE_MAP.md](OSS_LICENSE_MAP.md). This is a
one-way door — pick wrong and we re-train every scene later.

**2. Rendering, not training, is the binding constraint.**
Training a room on a 4080 is an hour, once. Rendering it at 72 Hz × 2 eyes ×
2064×2208 on a mobile SoC is *every frame, forever*. Every upstream decision —
gaussian count, SH degree, compression format, LOD scheme — is downstream of a
frame budget of **13.8 ms**. Design from the frame budget backwards.

**3. A splat is not a place until it has collision.**
You cannot walk through a point cloud. Teleport targets, floor height, and
"don't fall through the world" need a mesh. We already produce one — the ARKit
scene mesh from the iPad, plus the nvblox/TSDF path in
`assetpipe/scene/nvblox_fuse.py`. Splat for the eyes, mesh for the feet.
Carrying both through the pipeline from the start avoids a painful retrofit.

---

## 2. The loop

```
 iPad Pro (LiDAR)              4080 / Linux                      Quest 3 / web / iOS
┌────────────────────┐   ┌───────────────────────────┐   ┌──────────────────────────┐
│ Roomscape Capture  │   │ ingest → normalize        │   │ WebXR viewer (PlayCanvas)│
│ • 12MP keyframes   │──▶│ pose refine (ARKit→GLOMAP)│──▶│ Quest native (Unity/OXR) │
│ • 256×192 depth+cf │   │ gsplat train (+depth sup) │   │ iOS Metal viewer         │
│ • gravity + metric │   │ align / crop / defloat    │   │                          │
│ • ARKit room mesh  │   │ compress → SOG/spz        │   │ teleport on mesh proxy   │
└────────────────────┘   │ collision mesh (nvblox)   │   └──────────────────────────┘
         │               └───────────────────────────┘              ▲
         │                          │                               │
         └──────────────────────────┴───────────────────────────────┘
                        our registry: sessions, artifacts,
                        training runs, consent, retention
```

Full component detail in [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 3. Decisions made (and what would reverse them)

| # | Decision | Why | Kill criteria |
|---|---|---|---|
| D1 | **gsplat (Apache-2.0) is the trainer spine** | only mature, permissively-licensed 3DGS trainer; already half-wired in `assetpipe/scene/backends.py` | if depth-supervised gsplat can't beat a room baseline in P2, evaluate Brush (Apache/MIT) before touching anything research-licensed |
| D2 | **iPad-first capture, Quest capture in P6** | LiDAR gives metric scale + gravity + a mesh; Quest PCA gives 1280×960 mono and no metric depth. Capture quality is the top of the funnel — start with the best sensor | if Quest PCA + Depth API reconstructions land within ~15% of iPad on the same room, collapse to Quest-only for the consumer story |
| D3 | **WebXR ships before native** | Quest browser runs WebXR today with no store review, no signing, no APK. Fastest path to "I am standing in my room" | if we can't hold 72 Hz at ≥0.5 M gaussians in the Quest browser, native moves ahead of P3 |
| D4 | **On-device render, not cloud render** | we have one GPU, not a fleet; per-viewer GPU cost is the thing that kills hobby-scale products | never for Tier A. Tier B (local stream) already covers the "wow" demo |
| D5 | **Extend the existing session manifest, don't invent a format** | `SessionExporter.swift` + `assetpipe/scene/rgbd_session.py` already agree on a schema; a v2 superset keeps every existing tool working | none — additive only |
| D6 | **Splat for pixels, mesh for physics** | see hard truth 3 | none |
| D7 | **SQLite + local object store first, Postgres/S3 when it hurts** | single-user, single-box today; the schema matters, the engine doesn't | >5 concurrent capture devices or any multi-tenant sharing |

---

## 4. Phases

Effort is calibrated for **one person part-time with Claude**, in working days.
Each phase ships something you can stand inside or point at. Nothing after P0
is worth starting until P0 says the loop closes.

---

### P0 — First light (2–4 days)

**Goal:** prove the entire loop end-to-end at bad quality, with data we already
have on disk, before writing any new capture code.

The 16-keyframe iPad export in `~/Downloads/crate_20260730_17_12_16 2/` is
already valid 3DGS input: posed, intrinsics-tagged, gravity-aligned. 16 views
is far too few for a room, but it is enough to wire every stage together.

Work:
1. `roomscape/pipeline/ingest.py` — read a CrateScanner session manifest, emit a
   COLMAP-format sparse model directly from ARKit poses + `K_color` (no SfM).
   `assetpipe/scene/backends.py::_write_colmap_text_pinhole` already does most
   of this; lift and generalize it.
2. Train with gsplat/splatfacto in the existing `roomrecon` env. 7k iterations,
   images downscaled to ~1600 px.
3. Export `.ply` → compress → serve over HTTPS from the 4080.
4. Load it in a stock PlayCanvas/SuperSplat viewer on desktop, then open the
   same URL in the **Quest 3 browser** and hit "Enter VR".

**Exit:** you are standing inside a (bad, hole-ridden, 16-view) splat of a real
crate, on the headset, from our own pipeline. Screenshot it. Write down the
measured frame time.

**Risk:** ARKit poses may not be accurate enough to train directly. Mitigation:
this is exactly what P0 is for — if it fails, we learn it in 3 days for free
and P2's pose-refinement step becomes mandatory rather than optional.

---

### P1 — Room-scale capture (5–8 days)

**Goal:** an iPad capture mode that produces the input a room actually needs.

Today's app captures 16 stills for an object. A room needs **300–800 posed
views with depth**, even coverage, and no motion blur. The current Detail mode
is the wrong shape and, as the download proved, the keyframe path drops depth
entirely (`captureHighResolutionFrame` returns no `sceneDepth`).

Work:
- **Room mode** in `mobile/CrateScannerApp`: continuous keyframes at 2–4 Hz,
  ~1600–2000 px wide (not 12 MP — 800 × 12 MP is 50 GB), every one carrying
  color + depth + confidence + pose + intrinsics.
- Fix the depth gap: for room mode, pull depth from the *streaming* ARFrame at
  the nearest timestamp rather than from the high-res still.
- **Coverage HUD**: a live shell showing which directions/heights have been
  seen, so the user sweeps the room instead of guessing. `Support/GuidedCapture.swift`
  and the AR keyframe markers in `ScanViewModel` are the starting point.
- **Sharpness gate**: variance-of-Laplacian per candidate frame; reject blur.
  `Support/CaptureGate.swift` already gates on motion — extend it.
- Export the **ARKit scene mesh** (`ARMeshAnchor`) into the session as
  `room_mesh.obj` — free collision proxy and free splat initialization points.
- Session v2 manifest — see [CAPTURE_SPEC.md](CAPTURE_SPEC.md).

**Exit:** one 5-minute sweep of a real room produces a session with ≥300 posed
RGB-D frames, a room mesh, gravity, and a coverage report ≥85%, and it uploads
to the 4080 unattended.

**Risk:** thermal throttling and storage on a 5-minute continuous capture.
Mitigate by writing JPEG at quality ~0.8 on a background queue (already the
pattern in `SessionExporter.ioQueue`) and capping session size.

---

### P2 — Reconstruction that looks good (8–12 days)

**Goal:** turn a P1 session into a scene worth wearing a headset for.

Work, in order of expected payoff:
1. **Pose refinement.** ARKit poses drift over a 5-minute sweep. Feed them as
   priors into GLOMAP/COLMAP bundle adjustment rather than solving from
   scratch — order-of-magnitude faster than cold SfM and more robust in
   textureless rooms. Keep ARKit's metric scale and gravity by fixing the
   similarity transform after BA.
2. **Depth-supervised training.** Initialize gaussians from the LiDAR point
   cloud (not SfM sparse points) and add a depth loss weighted by ARKit's
   confidence map. This is the single biggest quality lever the iPad gives us
   over any phone-video pipeline, and it fixes the classic 3DGS failure mode:
   blank walls, floors, ceilings.
3. **Exposure handling.** Auto-exposure drift across a sweep bakes seams into
   the splat. Enable per-image appearance modeling (bilateral grid).
4. **Cleanup.** Floater removal, crop to room bounds, floor snap to gravity,
   sky/window handling. `assetpipe/scene/clean.py` and `splat.py::denoise` /
   `drop_far_field` already implement pieces of this on point clouds.
5. **Compression.** Raw 3DGS with SH degree 3 is ~236 B/gaussian — 1 M
   gaussians is 236 MB, which is a non-starter over the network and in 8 GB of
   shared headset RAM. Compressed formats land near 20–35 B/gaussian. Target
   **≤80 MB per room**.
6. **Collision mesh** carried alongside, from the ARKit mesh or nvblox.

**Exit:** a room scene that (a) is recognizably *the room* from any standing
position, (b) is ≤80 MB, (c) has correct metric scale — measure a doorway in
VR and compare to a tape measure, ≤2 cm error — and (d) is level, no floor tilt.

**Risk:** 16 GB VRAM caps gaussian count during training. Mitigation: densify
with a hard cap, train at 1600 px, and use the sparse-Adam path. If a room
still OOMs, tile it and merge.

---

### P3 — The viewer (8–12 days)

**Goal:** a real VR experience, not a splat dumped in a browser tab.

Work:
- PlayCanvas (MIT) WebXR app in `roomscape/viewer-web/`: scene list, load,
  teleport locomotion against the collision mesh, snap turn, height calibration,
  a comfort vignette, and a reset-to-origin.
- **Perf work is the phase, not a footnote**: resolution scaling, fixed
  foveated rendering, frustum + distance culling of gaussian chunks, and a
  sort strategy that survives stereo (one sort from a mid-eye viewpoint).
- LOD / progressive load so a scene starts rendering in <3 s instead of after
  an 80 MB download.
- Budgets and how to measure them: [RENDER_TARGETS.md](RENDER_TARGETS.md).

**Exit:** 72 Hz sustained (frame time <13.8 ms, dropped-frame rate <1%) on
Quest 3 in-browser, with a scene from P2, for 10 minutes without thermal
collapse. Someone who did not build it can put on the headset and walk around
without instruction.

---

### P4 — Our system of record (5–8 days)

**Goal:** "record all the data in our system" — made literal.

Work:
- Upload service on the 4080: authenticated session upload, resumable, with a
  job queue that auto-runs P2 on arrival. `services/capture_worker.py` and
  `scripts/watch_ipad_captures.sh` are the seed.
- Registry (SQLite → Postgres later): `capture_session`, `device`, `scene`,
  `artifact`, `training_run`, `view_event`. Every artifact content-addressed.
- **Privacy is a feature here, not a compliance chore** — this is camera data
  of private homes. Person/face detection + blur at ingest, per-scene ACL,
  explicit retention policy for raw frames (they are 10–50× the size of the
  output and rarely needed after training), and a real delete that reaches the
  object store. See [DATA_MODEL.md](DATA_MODEL.md).
- Reproducibility: every scene records the exact commit, config, and input
  hash that produced it. When we improve the trainer we can re-run every scene.

**Exit:** tap "upload" on the iPad; 60 minutes later the scene is live in the
viewer's scene list with zero manual steps, and `roomscape scenes ls` shows its
full provenance.

---

### P5 — Native Quest app + store (10–15 days)

**Goal:** an installable Quest 3 app, and a submission that survives review.

Work:
- Unity 6 + OpenXR + a permissively-licensed splat renderer (aras-p's
  `UnityGaussianSplatting` is MIT), *or* PlayCanvas packaged as a WebView app,
  *or* native Vulkan. Decide after P3 tells us how much headroom the browser
  costs us — expect native to buy 2–3× the gaussian budget.
- Meta developer org, app signing, release channels, Data Use Checkup for any
  camera permission, age rating, store assets.
- See [STORE_PATH.md](STORE_PATH.md) — the review process for camera-derived
  and user-generated spatial content has real requirements; start the paperwork
  during P3, not after P5.

**Exit:** the app is installed from a Meta release channel on a headset that
was never plugged into our dev machine.

---

### P6 — Close the loop: capture on the headset (10–15 days)

**Goal:** the actual Hyperscape gesture — scan and view on the same device.

Work: Passthrough Camera API recorder (Horizon OS v76+; `docs/QUEST3_CAPTURE.md`
already sketches the Unity side and `assetpipe/capture/quest3.py` consumes the
schema), plus the Depth API for scale. Same session format, same backend.
Quality will be below the iPad — 1280×960 vs 12 MP — so this is a *convenience*
tier, not a replacement. Also in this phase: the iOS viewer, so a scan can be
shown to someone who does not own a headset.

**Exit:** scan a room with the headset, take it off for an hour, put it back on,
walk through the scene.

---

## 5. The numbers we are designing against

| Budget | Target | Why |
|---|---|---|
| Quest 3 frame time | **<13.8 ms** (72 Hz) | below this, VR is nauseating, not merely slow |
| Gaussians, in-browser WebXR | 0.5–1.0 M | measure in P0; this is the number the whole pipeline serves |
| Gaussians, native | 1.5–2.5 M | expected 2–3× headroom over browser |
| Scene download | ≤80 MB | ~20–35 B/gaussian compressed vs 236 B raw |
| Time-to-first-pixel | <3 s | needs progressive/LOD load |
| Capture sweep | ≤5 min, 300–800 views | longer and users quit; fewer and walls go blank |
| Train time per room | ≤60 min on the 4080 | keeps the iterate-tonight loop alive |
| Metric accuracy | ≤2 cm over a doorway | the LiDAR advantage, made checkable |

---

## 6. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| A research-licensed dependency sneaks into the shipping path | **fatal** — blocks commercial release | license gate in P0, re-audited at every dependency add; see OSS_LICENSE_MAP |
| Quest 3 can't hold 72 Hz at a useful gaussian count | high | measured in P0 before we build on it; native path and aggressive LOD in reserve; Tier B stream as the quality escape hatch |
| ARKit pose drift ruins large rooms | high | pose refinement in P2 is planned, not hoped-for; loop-closure guidance in the capture HUD |
| 16 GB VRAM caps room size | medium | cap densification, train at 1600 px, tile-and-merge for large spaces |
| Store review rejects camera-derived content | medium | start Data Use Checkup during P3; keep raw-frame handling defensible and documented |
| Raw frame storage grows without bound | medium | retention policy + content-addressed store + "delete raws after N days, keep the scene" |
| Scope creep into the object/asset pipeline | medium | Roomscape is the *room shell* only. Objects stay in `assetpipe` (the generative path). Same doctrine as `docs/SCENE_SPLAT.md` |
| Quality gap vs Meta demoralizes the project | low but real | Tier B exists precisely so the ceiling is visible; judge Tier A against "is this a place I recognize", not against a datacenter |

---

## 7. What we already have (do not rebuild)

| Need | Already in this repo |
|---|---|
| iOS LiDAR capture app | `mobile/CrateScannerApp` — ARKit session, depth PNG16 writer, pose/intrinsics capture, zip export, Drive upload |
| Session schema + reader | `SessionExporter.swift` ↔ `assetpipe/scene/rgbd_session.py` |
| ARKit poses → COLMAP model | `assetpipe/scene/backends.py::_write_colmap_text_pinhole` |
| SfM | `backends.py::_run_colmap_sparse` (pycolmap, no colmap binary needed) |
| Splat training | `SplatfactoSceneBackend`, `ThreeDGutSceneBackend`, `_splat_driver.py` cross-env bridge, `roomrecon` conda env |
| Splat loading / cleanup / rendering | `assetpipe/scene/splat.py` (PLY parse, denoise, far-field drop, orbit render) |
| TSDF / collision mesh | `assetpipe/scene/nvblox_fuse.py`, `room.py` |
| Ingest watcher | `services/capture_worker.py`, `scripts/watch_ipad_captures.sh`, `tools/watch_inbox.py` |
| Doctrine: splat = room, generative = objects | `docs/SCENE_SPLAT.md` |

Roomscape is the **product layer** on top of that — capture UX, delivery,
viewers, and the system of record. It should call into `assetpipe`, not fork it.

---

## 8. Facts to re-verify before committing code

My knowledge has a mid-2026 cutoff and several of these move fast. Check at
HEAD, in writing, before any of them becomes load-bearing:

- **What Hyperscape Capture actually does today.** The store listing
  ([meta.com/experiences/meta-horizon-hyperscape-capture-beta](https://www.meta.com/experiences/meta-horizon-hyperscape-capture-beta/8798130056953686/))
  is a JS shell that gave up nothing on fetch — 3.6★, 302 reviews, "Everyone",
  early access, and no stated mechanics. The cloud-render/stream model in §0 is
  from public reporting, not from that page. Before we commit to differing from
  them deliberately, install the beta on the Quest 3 and time it yourself: scan
  duration, upload size, turnaround, whether the finished scene works offline
  (that single observation settles on-device vs streamed), and whether scenes
  can be shared. One evening of hands-on beats any amount of my recall.
- License text at HEAD for **gsplat, nerfstudio, 3dgrut, PlayCanvas engine,
  SuperSplat, UnityGaussianSplatting, spz** — and whether any transitively pull
  in `diff-gaussian-rasterization`.
- Current Horizon OS version and **Passthrough Camera API** availability,
  store-publishability, and whether **WebXR** exposes camera access yet.
- Whether Meta's store review terms restrict apps that reconstruct and share
  scans of private spaces.
- Current best compressed splat format and which viewers read it natively
  (SOG / compressed-PLY / spz all moved during 2025–26).
- gsplat's current depth-supervision and bilateral-grid APIs (`roomrecon` is
  pinned at gsplat 1.4.0 per `docs/SCENE_SPLAT.md` — likely needs a bump).

---

## 9. Open questions for you

1. **Rooms or objects?** Roomscape assumes the deliverable is the *space*. If
   the real goal is still the crate/asset pipeline with a prettier viewer, P1
   and P2 change shape a lot.
2. **Who else sees a scene?** Solo tool, or share links to other people? Sharing
   pulls multi-tenancy, auth, and content moderation into P4 instead of "later."
3. **Is the Meta store a real goal or a stretch goal?** If real, the license
   gate and Data Use Checkup start now. If it is "someday", P3's WebXR viewer
   may be the whole product for a long while.
4. **What's the first room?** Pick a specific, well-lit, textured, ~4×5 m room
   and make it the benchmark scene for every phase.
