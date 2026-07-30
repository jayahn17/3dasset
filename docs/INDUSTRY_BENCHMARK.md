# Industry benchmark — iPad RGB-D captures vs commercial APIs

**Goal (2026-07-24):** run our CrateScanner sessions through the best commercial
services via API, so the open-source pipeline (see `docs/OBJECT_ASSET_PLAN.md`)
has an honest industry yardstick. Second goal: pick the API strategy for the
B2C "household digital asset memory" direction — every object and room in a
home digitized, beyond the factory/kitchen-robot datasets.

**The structural finding up front:** *no commercial API accepts our depth or
poses.* Kiri, Marble, and everything surveyed ingest RGB photos/video only
(LiDAR modes are consumer-app-only, processed on device). Two consequences:

1. Every API benchmark sees strictly less data than our pipeline. That is the
   benchmark story, not a flaw: they represent "what a user gets from RGB
   alone," we represent "what the same capture gets with depth + poses."
2. **Metric scale is our moat.** Kiri output is explicitly non-metric; Marble
   ships a `metric_scale_factor` but it is model-estimated, not measured.
   Our dims.json (on-device mesh + TSDF cross-check) is measurement truth the
   APIs cannot have — and `bench_compare.py` quantifies exactly that gap.

---

## 1. The two primary benchmarks

### Kiri Engine API — object-scan yardstick

- RGB only: 20–300 photos or ≤3 min 1080p video per scan. No depth/LiDAR/pose
  ingest (app-only features). Docs: <https://docs.kiriengine.app/>
- Three engines, 1 credit each: **Photo Scan** (photogrammetry, quality 0–3,
  auto-masking), **Featureless** (neural, for smooth/reflective objects),
  **3DGS** (splat PLY, optional mesh conversion via `isMesh`).
- Formats: obj/fbx/stl/ply/glb/gltf/usdz/xyz, textures up to 8K.
- Flow: multipart upload → `serialize` id → poll `getStatus` → `getModelZip`
  (URL lives 60 min; assets auto-delete after **3 days** — download promptly).
- Pricing: **$1/scan**, 10 free signup credits, then $500 minimum recharge.
- **Not metric** — arbitrary scale, by their own docs.

### World Labs Marble (World API) — scene-level yardstick

- Public API since Jan 2026: `https://api.worldlabs.ai/marble/v1`, header
  `WLT-Api-Key`. Docs: <https://docs.worldlabs.ai/>
- Inputs: text / image / pano / **multi-image (≤8, `reconstruct_images`)** /
  **video (≤30 s, ≤100 MB)** / depth-pano. Generative world model: faithfully
  renders what the capture shows, *hallucinates the rest* — full coverage,
  zero holes, no fidelity guarantee for unseen regions.
- Outputs: Gaussian splats (SPZ/PLY, ~2M full-res; **PLY export free**),
  collider GLB, HQ textured mesh (3,500 credits ≈ $2.80), pano. World
  metadata carries `metric_scale_factor` + `ground_plane_offset` (OpenCV
  convention — matches our normalized poses).
- Pricing: ~**$1.28/world** (1,600 credits) for video/multi-image on
  marble-1.1; $5 minimum credit purchase, no free API tier.
- The `depth-pano` prompt type is the one programmatic hook for injecting our
  measured geometry later.

Division of labor: **Kiri = object assets** (its masking + mesh path matches
our object_asset bundle), **Marble = room/scene worlds** (its video input
matches our sweep captures). They are complementary, not competing.

## 2. Tooling (all in `tools/`, run in the `assetpipe` env)

| Tool | What it does |
|---|---|
| `bench_export.py` | session zip/dir → `bench_out/<name>/photos/` (curated, sharpness × viewpoint-diverse — same selection our trainer uses) + `video.mp4` + `meta.json` |
| `bench_kiri.py` | bundle → Kiri photo/featureless/3dgs job → poll → download+extract. `--balance`, `--status <id>` resume. Needs `KIRI_API_KEY`. |
| `bench_marble.py` | bundle → Marble video or ≤8-image generate → poll → free splat PLY export (+`--mesh` GLB). Auto-subsamples video to fit the 30 s cap. `--world <id>` resume. Needs `WORLDLABS_API_KEY`. |
| `bench_polycam.py` | **manual web** path (no public capture API): `prepare` → upload guide + optional zip; `ingest` downloaded GLB/OBJ/PLY/ZIP; `compare` via `bench_compare`. |
| `bench_compare.py` | any returned asset (GLB/PLY/OBJ) vs our `dims.json` → **scale error** (is it metric?) + **shape error after best uniform rescale** (aspect fidelity). Writes `*_bench.json`. |

End-to-end example (object session):

```bash
conda activate assetpipe
python tools/bench_export.py captures/done/CrateScan-BE53A423.zip --photos 60
export KIRI_API_KEY=kiri-...
python tools/bench_kiri.py bench_out/CrateScan-BE53A423 --mode photo
python tools/bench_compare.py bench_out/CrateScan-BE53A423/kiri_photo/*.glb \
    --truth demo_out/CrateScan-BE53A423/dims.json --label kiri_photo
```

Room session through Marble:

```bash
export WORLDLABS_API_KEY=...
python tools/bench_marble.py bench_out/CrateScan-5C0D0369 --input video
```

Object session through **Polycam** (web upload — no public capture API):

```bash
python tools/bench_export.py captures/done/CrateScan-1B38880A.zip --photos 60
python tools/bench_polycam.py prepare bench_out/CrateScan-1B38880A --zip
# Upload photos/ (or polycam_upload.zip) at https://poly.cam → Photogrammetry
# Enable Isolate object + Sequential; export GLB; then:
python tools/bench_polycam.py ingest bench_out/CrateScan-1B38880A ~/Downloads/model.glb
python tools/bench_polycam.py compare bench_out/CrateScan-1B38880A \
    --truth demo_out/CrateScan-1B38880A/dims.json
```

All 7 archived sessions are already exported under `bench_out/`.

## 3. Evaluation protocol

Per session, per service, record in the session's `*_bench.json`:

1. **Scale honesty** — `scale_error_pct` from bench_compare. Expected: Kiri
   fails (non-metric by design), Marble approximate, ours ≤ measurement noise.
2. **Shape fidelity** — `shape_error_pct_mean` after the best uniform rescale.
   This is the *fair* cross-service number (removes the scale advantage).
3. **Completeness** — visual: unseen-side handling (hole vs hallucination vs
   TRELLIS completion). Contact sheet from bench_export + result screenshots.
4. **Turnaround + cost** — wall time and $ per asset.
5. **Delivery format** — what a downstream consumer (MuJoCo via
   `assetpipe sim-export`, web viewer) can ingest without manual fixes.

Caveats for fairness:

- Archived sessions are **640×480** color (old app builds). The current app
  captures 1920×1440 — recapture before drawing quality conclusions; the APIs
  deserve the high-res input. 640×480 results are a *lower bound* on them.
- Kiri photo mode wants orbit coverage with overlap; our curated 60 frames
  satisfy it. Marble multi-image wants ≤8 *wide-baseline* views; the client
  auto-spreads picks across the sweep.
- Marble worlds are one-room only; multi-room sweeps must be split.

## 4. B2C: household asset memory at scale

The target: a user walks their home with an iPhone/iPad; every room becomes a
navigable scene, every object a metric catalog asset ("asset memory" — resale,
insurance, moving, robot grounding). What the benchmark implies for it:

- **Per-unit economics.** Commercial APIs: $1/object (Kiri) + $1.28/room
  (Marble) — a 200-object household is ~$210 *per full digitization*, before
  re-scans. Our pipeline's marginal cost is GPU-minutes; the APIs are the
  price ceiling to undercut, and the quality bar to match.
- **The RGB-only ceiling is real.** No API uses the depth the device already
  measured. Household objects are exactly the hard cases (textureless walls,
  glossy appliances, dark fabric) where measured depth wins — that is the
  technical wedge.
- **Scale is the product feature.** "Will this couch fit through that door"
  requires metric truth; Kiri can't answer it, Marble approximates it, our
  dims.json states it to 0.25″.
- **Hybrid is the endgame** (mirrors OBJECT_ASSET_PLAN's TRELLIS phase):
  measured geometry as the anchor, generative completion for unseen surfaces.
  Marble's depth-pano input even offers a path to *sell them our geometry*
  rather than compete head-on.

## 5. Our open-source side — pipelines and execution

The in-house half of the table. Orchestrator: `tools/bench_ours.py`
(assetpipe env); GPU stages queued in `scripts/bench_gpu_queue.sh`.

| # | Pipeline | Hardware | Industry counterpart |
|---|---|---|---|
| P1 | Full-session TSDF fuse (pose-fixed, auto voxel) → scene mesh + dims.json | CPU | Kiri Photo Scan |
| P2 | Object isolation from fused scene → object mesh/splat + 0.25″ measure | CPU | Kiri isolate/mask |
| P3 | Curate 48 → multi-frame ICP object asset | CPU | Kiri 3DGS |
| P4 | Lounge room session TSDF → room-shell mesh | CPU | Marble / Teleport |
| P5 | `assetpipe sim-export`: CoACD colliders + mass/inertia + URDF/MJCF/USD | CPU | **none — our moat** |
| P6 | 3DGUT splat (measured poses, depth-seeded) via `run_3dgut_from_session.py` | GPU (queued) | Kiri 3DGS / Teleport |
| P7 | TRELLIS completion GLB from curated photos, scored vs dims.json | GPU (queued) | Meshy / Tripo / Rodin |

Scoring semantics (see `bench_out/RESULTS.md` for the live table):

- **Scene AABB ratio** — our TSDF fuse vs the on-device ARKit mesh, two
  *independent* measurements of the same space (coverage-sensitive; ≈1.0 per
  axis = metrically consistent fuse). We learned the hard way that scoring
  the object crop against the on-device mesh is invalid: the on-device mesh
  is scene-scale unless the capture cropped to the object.
- **Object L×W×H** — the `object_crop` deliverable; this is the truth the
  API results get `bench_compare`d against.
- **Executed 2026-07-24** (driver fixed by reboot; loaded module now
  580.173): all CPU stages P1–P5 on 7 sessions + Lounge, then P6 3DGUT ×7
  and P7 TRELLIS ×7 on the RTX 4080 SUPER, zero failures. Full numbers in
  `bench_out/RESULTS.md`. Headlines: fuses metrically consistent on the
  clean sessions (scene-AABB ratios ≈1.0–1.2 vs the on-device mesh);
  TRELLIS from raw photos is scale-blind and shape-rough (best ~23%,
  worst 193% after rescale) — confirms the RGBA-crop + ICP-rescale
  sequencing in OBJECT_ASSET_PLAN before generative completion counts.
  Re-running everything is one command: `bash scripts/bench_autopilot.sh`
  (idempotent; add `--force` semantics via `tools/bench_ours.py --force`).

## 6. Competitor API survey (July 2026)

**Object reconstruction (multi-view photos of a real object):**

1. **Kiri Engine API** — the only self-serve true-photogrammetry API. Client
   built (`bench_kiri.py`).
2. **RealityScan 2.2** (Epic) — highest fidelity, scriptable via REST/gRPC +
   CLI with Linux support, **free under $1M revenue** — but self-hosted, not
   SaaS. The right *upper-bound* baseline if we want one beyond Kiri.
   <https://dev.epicgames.com/documentation/en-us/realityscan/remote-command-plugin>
3. Generative baselines (hallucinate unseen geometry — same tier as our
   TRELLIS path): **Meshy** multi-image (1–4 views, ~$0.50/model, USDZ out,
   API from Pro $20/mo, <https://docs.meshy.ai/en/api/multi-image-to-3d>);
   **Tripo3D** multiview (2–4 views, ~$0.30/run); **Rodin/Hyper3D** (≤5 views
   `condition_mode: concat`, strongest quality, but API needs Business
   $120/mo, <https://developer.hyper3d.ai>).

**Room/scene from a walkthrough video:**

1. **Teleport by Varjo** — the scene-level *reconstruction* API we didn't
   know about: up to **15-min video or 2,000 images** per capture → cloud
   3DGS (1M–100M splats), PLY export, webhooks, exposed training params.
   Pay-as-you-go **from $30/capture**, full API included, no free tier.
   <https://teleport.varjo.com/docs/> — the faithful-scene comparator that
   Marble (generative) is not; our full-length `video.mp4` exports fit its
   limits without subsampling. Worth a client next if scene benchmarking
   becomes the focus.
2. **Marble** — scene *generation/completion* benchmark. Client built
   (`bench_marble.py`).
3. **Scaniverse** (free app, no API) — July 2026 USDZ export bundles the
   splat with an auto-aligned mesh, aimed at robotics sim — worth one manual
   side-by-side. **Matterport** — metric real-estate standard, but phone
   ingestion is app-only (Import API is Enterprise E57 only).

**Dead ends confirmed:** Luma's 3D/capture API is fully retired (docs now
video/image gen only); **CSM.ai shut down Jan 2026** (team → Google
DeepMind); Polycam's capture API is contact-sales; Backflip has no public
API (scan→CAD focus). **No mainstream cloud API accepts raw RGB-D streams**
— depth stays trapped in capture apps; Matterport Enterprise E57 import is
the closest thing. Our depth/pose ingest genuinely has no commercial API
equivalent to benchmark against — RGB-derived results are the only
comparable industry output.
