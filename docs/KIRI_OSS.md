# Open-source KIRI Engine (`tools/kiri_oss.py`)

KIRI is the object-scan benchmark we score against, but it is a cloud black box:
photos in, a scale-free mesh/splat out, $1 a scan, results deleted after 3 days.
`tools/kiri_oss.py` reproduces its **documented** pipeline from open source so the
benchmark runs offline, can be inspected at every stage, and — unlike KIRI —
closes to metric.

```bash
conda activate assetpipe
python tools/kiri_oss.py captures/work/CrateScan-XXXX/*/session \
    --out kiri_oss_out/xxxx \
    --truth demo_out/CrateScan-XXXX/object_asset/dims.json
```

Stages run in order and each skips when its output exists (`--force` to redo):
`ingest, curate, mask, sfm, mvs, gs, scale, isolate, validate, report`. `gs` is off by
default because it wants the GPU for ~40 min at 30k iterations.

## Stage for stage, against what KIRI ships

| KIRI | here | why this is the right match |
|---|---|---|
| 3D Scan Prep (blur/weak reject) | Laplacian-variance + exposure curation | their prep tool's job, same statistics |
| AI object masking (`isMask`) | **BiRefNet** via `rembg` | not a guess — KIRI's own AGPL prep tool ships BiRefNet, MaskFormer and YOLO in `_AI_Models/` |
| Photo Scan (photogrammetry) | AliceVision SfM + MVS + texturing | Meshroom's engine; the same incremental-SfM→MVS→texture chain |
| 3DGS Scan | AliceVision poses → 3DGUT (gsplat kernels) | RGB-only poses, so it is comparable to their product |
| 3DGS to Mesh | TSDF over splat depth *(not yet wired)* | their page describes "predicted normal and depth maps to regulate the surface" |
| *(nothing — KIRI is scale-free)* | Umeyama fit to the capture's own AR track | the differentiator |

Masks deliberately do **not** enter SfM. On a shiny low-texture couch the leather
carries almost no features and the rug/room carry them all; masking the solve is
an own-goal. KIRI exposes `isMask` separately from the solve for the same reason,
so masks enter at `prepareDenseScene` and steer the dense/meshing stage only.

## The metric bridge

KIRI returns an arbitrary-scale asset because it only ever sees RGB. Here the
same frames carry an AR pose track that `ingest` deliberately withholds from the
solve, so it is an honest external ruler rather than a leak: fit the SfM camera
centres onto the AR camera centres (Umeyama similarity) and read off the scale.
The **full** transform is applied, not just the scale — that lands the
reconstruction in the gravity-aligned AR world frame, which makes floor removal
and an upright L×W×H geometry rather than guesswork.

Depth is used for exactly one more thing: the floor datum. Masking removes the
ground from the images, so the RGB-only mesh has no floor to measure height
against — it can only measure from the lowest surface it reconstructed, which on
a sofa is the underside of the base (9 in up). `ingest` reads the true floor
plane off the LiDAR depth, and `isolate` quotes height from both.

## Result on `crate_20260805_13_56_30` (CrateScan-BD96A176)

A tan leather sofa in a cluttered room, 31 iPad keyframes, 278° of azimuth
coverage with an 82° gap — low-texture, shiny, partial coverage. Squarely KIRI's
hard case.

| branch | L × W × H (in) | notes |
|---|---|---|
| photogrammetry (AliceVision MVS) | **81.09 × 34.22 × 35.27** | 209k verts, height off LiDAR floor |
| 3DGS (3DGUT, 30k iters, 1M cap) | *visual asset* | 11,139-point object crop = 1.11% of the scene splat; centres only, not a splat |
| ours, existing RGB-D `object_asset` | 100.75 × 58.09 × 44.70 | free OBB, only 7 ICP-merged frames |

206 × 87 × 90 cm is a textbook 3-seat sofa. Read L and W as measurements of the
mesh and **H as a standing height, not a bounding box**: the isolated PLY's own
vertical extent is 25.10 in, and the published 35.27 in is mesh-top minus the
LiDAR floor plane, so 10.17 in of it is the empty space under a sofa whose legs
the isolation removed.

Pipeline facts for that run:

- curate kept 28/31 frames — note that is a fixed 90% quota
  (`max(20, round(0.9 n))`), not a quality decision; BiRefNet masked the subject
  to 48.7% of frame
- SfM registered **28/28 views**, 15,626 landmarks, in 95 s
- photogrammetry branch: **413 s of stage time (6 min 53 s)**, of which BiRefNet
  masking alone is ~220 s. End-to-end elapsed was 550 s. Hardware is an RTX 4080
  SUPER (16 GB)
- scale bridge: SfM unit = 1.3792 m from 28 cameras, fit RMS **30 mm** —
  per-camera residuals run 4.4–69.8 mm with a **median of 24 mm**, so one camera
  in the bridge is 7 cm out and a single RMS hides that

On denominators: the capture is a **11.80 m camera path**, the widest baseline
between any two cameras is **3.33 m**, and the bounding-box diagonal of the
camera centres is 4.56 m. Quoting the RMS against the last of those (0.66%) is
the flattering choice — against the path it is 0.25%, against the widest true
baseline 0.90%. The azimuth coverage is 278°, not the ~180° an eyeballed orbit
suggests, with an 82° gap.

### Independent validation — `validate` stage

The LiDAR depth is fused separately (never used by SfM or MVS) and compared to
the RGB-only mesh in the same frame. This is a stage, not prose: run it and get
`validate.json`.

| direction | median | mean | p95 | within 20 mm |
|---|---|---|---|---|
| mesh → LiDAR (accuracy) | 9.2 mm | 15.2 mm | 44.8 mm | 76.3% |
| LiDAR → mesh (completeness) | **31.3 mm** | 119.0 mm | 685.2 mm | **34.1%** |

**Both directions or neither.** mesh → LiDAR alone is biased low: it asks only
whether reconstructed surface sits near some depth sample, and is silent about
every surface the RGB mesh never built. The completeness direction is far worse
and is the honest headline — the reconstruction is locally accurate and
substantially incomplete. Note too that 9.2 mm is *smaller than the 24 mm median
camera residual* of the very transform that placed the mesh in the LiDAR frame,
which is another reason not to read it as an error bar.

**Sample the surface, not the vertices.** An earlier version of this stage
queried the mesh's vertex list and reported 84.0% within 20 mm. Vertices are not
an area-uniform sample — they crowd onto small, well-reconstructed triangles and
under-count the large flat ones spanning the badly-reconstructed leather — so
that number was ~8 points optimistic. `stage_validate` now area-samples 400k
points off the surface, which is also what makes the two directions comparable,
since the LiDAR side is voxel-downsampled and therefore already area-fair.

The auto-detected floor (−1.1425 m) matched an independent hand fuse. Nothing
here supports a tolerance tighter than the repo-wide **± 1–2 in**.

## Error budget against a ±0.25 in (6.35 mm) spec

Measured on this capture, not estimated. **The spec is not met today — by roughly
an order of magnitude — and three separate terms individually exceed the whole
budget.**

| term | measured today | budget is 6.35 mm | floor, and what sets it |
|---|---|---|---|
| Re-run repeatability (one knob: `--mvs-downscale` 2→1) | **ΔW 17.8 mm**, ΔL 8.9 mm, ΔH 1.0 mm | **2.8× over** | caps everything else — no definitional fix buys anything until this is fixed |
| Scale — statistical (jackknife SE) | **6.9 mm per 2 m** (0.34% 1σ) | at the limit | ~2 mm after outlier trim; ~0.3–1.4 mm with a fiducial bar |
| Scale — **drift** (the unreported term) | **95.8 mm per 2 m** | **15× over** | residual lag-1 autocorr **+0.83** — it is drift, so one global similarity is the wrong model |
| Estimator bias (`minAreaRect` is an extremal statistic) | one-sided **+19…85 mm** | **3–13× over** | ~0.05 mm if replaced by a 6-plane box fit (a mean statistic, beats noise by √N) |
| Bounding-box convention (identical points) | **260 mm** spread on W across rect/PCA/OBB | — | 0 mm — freeze one convention (already done: min-area rect) |
| Output quantization (round-to-nearest 0.25 in) | up to **3.175 mm** | eats **50%** | 0 mm — report raw mm, `ceil` for crate padding |
| The object itself (soft sofa) | cushion creep **10–40 mm** | **unbounded** | not reducible by code; a sofa has no ¼-inch dimension |

`ΔH = 1.0 mm` against `ΔW = 17.8 mm` is the most useful line in the table: H is
anchored to the LiDAR floor plane, the other two axes are pure RGB geometry. The
axis with a metric datum is **~18× more stable**. That is the whole strategy in
one number.

### What would actually reach it

Only for a **rigid** object — a crate, not a sofa — and only with all of these:

1. **Replace `minAreaRect` with a robust 6-plane box fit.** Free, software-only,
   biggest single win: it converts an extremal statistic (max of 200k noisy
   points, one-sided bias) into a mean statistic over ~13k points per face.
2. **Fiducial scale, not the AR track.** `tools/alicevision_metric.py` already
   does `sfmTransform --method from_markers` and is validated to 0.1 mm; a rigid
   two-target bar (1.0–1.5 m, separation measured once with calipers) puts the
   scale term at 0.3–1.4 mm. The AR track cannot get there — it drifts 95 mm per
   2 m on this capture.
3. **`--max-edge 4032`** (currently 2016). One flag, ~4× compute; doubles every
   optical lever.
4. **Close the orbit loop** and feed per-frame LiDAR planes into a *joint* box +
   pose fit, rather than bounding a fused cloud.

Even then the honest floor is ~4.5 mm **1σ**. That clears 6.35 mm as a 1σ figure
but **not** as a 95/95 tolerance: at n=10 repeats the k-factor is 3.38, so a
defensible "±0.25 in" needs sd ≤ 1.7 mm.

### Two meanings of "0.25 in" — the repo already satisfies one

`assetpipe/scene/measure.py` sets `QUANTUM_IN = 0.25` as the *minimum reported
resolution* and rounds display values to it, and `web/lib/manifest.ts` says of
that same field: *"A ROUNDING CONVENTION, NOT AN ACCURACY CLAIM — real precision
here is ±1–2 in."* So the output is already **quantised** to 0.25 in and is
nowhere near **accurate** to it. A spec should say which it means.

## Two traps this exposed

**1. AliceVision writes its mesh in a different frame from its own SfM.**
`meshing`/`texturing` emit the OBJ rotated 180° about X relative to the pose
frame. A mesh dropped straight into the pose frame lands mirrored and about a
metre under the floor — and *still looks like a plausible couch*, which is what
makes it dangerous. It measured 81 × 41 × 36 in while being geometrically wrong.
Rather than hard-code the flip and have it silently invert on an AliceVision
upgrade, `_mesh_to_sfm_frame` scores both conventions against the sparse
landmarks (unambiguously in the pose frame) and takes the one the geometry
agrees with:

```
mesh frame = Rx180 (median vertex->landmark  identity 793 mm, Rx180 20 mm)
```

**2. A free 3D OBB is the wrong box for furniture.** It tilts to hug the point
soup and inflates every axis — it read this sofa as 100.75 in long. Up is known
in the AR frame, so `_gravity_dims` takes the footprint as a min-area rectangle
in the horizontal plane and height as a straight vertical measurement. That is
also why the existing `object_asset` row above is not a usable truth.

Two smaller ones: `texturing --colorMappingFileType` defaults to `none`, which
silently yields UVs and no baked map; and `trimesh.split()` on a 434k-face mesh
with thousands of shells OOM-kills the process, so isolation uses Open3D
clustering after a vertex dedup (the OBJ's UV-split vertices otherwise make every
triangle its own island).

## Where the 3DGS branch stands

**Do not measure off the splat here.** Two separate reasons, both worth knowing:

*Under-training.* At 7k iterations the splat is fog, not geometry: only 3% of
gaussians reach opacity 0.3, and measured length swings **143 → 95 → 70 in** as
the threshold moves 0.1 → 0.2 → 0.3. The repo's own precedent
(`bench_out/RESULTS.md`) needed 30k iterations and ~1M gaussians to reach ±1% on
a 285 mm object.

*Scene vs object.* Retraining at 30k fixed the fog (288k gaussians clear opacity
0.1) but exposed the real issue: we train on **unmasked** frames, so it is a
*scene* splat. The couch is continuous with the rug and the clutter, and
clustering runs away — it read 119 × 81 in at opacity 0.2. So the splat is
cropped to the mesh object's rotated footprint, giving an object point cloud
whose extent is *inherited from the mesh* and is therefore not an independent
measurement. The report prints it as an asset line, not a dims row, on purpose.

Three things not to overstate about that file. It is **11,139 points = 1.11% of
the 1,000,000-gaussian scene splat** (33.6% of the opacity-filtered subset — the
subset is the denominator that flatters). The 1M is a *ceiling*: the run hit
`--max-gaussians`, so it is not a converged count. And it is a point cloud of
gaussian **centres** — no opacity, scale, rotation or SH — so it is not a
gaussian splat and cannot be rendered as one.

To make the splat independently measurable it would need to be trained on the
masked cutouts, the way KIRI's `isMask` does for its 3DGS Scan.

The photogrammetry branch is the measurement-grade one; the splat branch is the
visual-fidelity one — the same division of labour KIRI ships.

## Not yet wired

- **3DGS → Mesh**: their "predicted normal and depth maps to regulate the
  surface" is PGSR/2DGS/GOF-class depth+normal regularisation followed by TSDF.
- **Featureless / NSR**: their answer to shiny/transparent subjects.
- Branch selection is by `--stages` today; there is no `--mode` flag.
