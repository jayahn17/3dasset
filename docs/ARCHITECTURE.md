# Architecture — 3D Asset Recording → Digital Twin

## The problem, stated precisely

A user wearing a **Meta Quest 3** looks at their real-world objects — a box,
clothes, shoes, books, *anything* — and, whenever they choose, "records" an
object. The system **identifies** it, **reconstructs** a 3D model of it, and
files it into their personal **digital twin** ("Life Twin Control Center →
Asset") so they can later answer: *what did I own, and where was it?*

That decomposes into five stages, each of which is a well-studied problem
with strong open-source options in 2025–2026:

```
 ┌──────────┐   ┌──────────┐   ┌───────────────┐   ┌──────────────┐   ┌──────────┐
 │ CAPTURE  │──▶│ IDENTIFY │──▶│ RECONSTRUCT   │──▶│ DIGITALIZE   │──▶│ ORGANIZE │
 │ Quest 3  │   │ 3D → 2D  │   │ 2D → 3D       │   │ mesh + URDF  │   │  twin    │
 │ PCA/Depth│   │ detect + │   │ generative or │   │ + metadata   │   │ catalog  │
 │  frames  │   │ segment  │   │ multi-view    │   │              │   │ + viewer │
 └──────────┘   └──────────┘   └───────────────┘   └──────────────┘   └──────────┘
   RGB/pose      YOLO-World      TRELLIS 2 /         OBJ/GLB → URDF     SQLite +
   +depth        Grounding DINO  Hunyuan3D           (obj2urdf/          control
                 + SAM 2         Nerfstudio+gsplat    scikit-robot)      center
```

This repo implements that chain as **five swappable stages** (`assetpipe/`).
Each stage is an interface with (a) a **zero-dependency implementation** that
runs today, and (b) **adapter stubs** for the heavy state-of-the-art models,
so you upgrade one box at a time without touching the others.

Note the vision's phrase "convert 3D object to 2D to identify": that is the
**IDENTIFY** stage — the world is 3D, the camera gives 2D frames, and we run
detection/segmentation in 2D. Getting back to 3D is the **RECONSTRUCT** stage.

---

## Stage 1 — CAPTURE (Meta Quest 3)

Meta shipped the **Passthrough Camera API (PCA)** to public release in Horizon
OS **v76** (Unity, Unreal/Native, Android/Spatial SDK; WebXR following). Apps
using it are publishable to the Meta Horizon Store.

- **Front RGB cameras**, up to **1280×960 @ 30 FPS**, 40–60 ms latency.
- **Per-frame camera pose + intrinsics** → assets can be reconstructed at
  metric scale and anchored to a world location (critical for "where was it").
- **Depth API + Scene API** give geometry priors (scale, ground plane, room
  mesh) that make sizing and placement robust.
- **AI Building Blocks** run object detection / LLM-on-camera **on-device**.

Two integration shapes (both supported by this repo's `capture/` sources):

| Shape | On-device | Off-device (this pipeline) | Best for |
|---|---|---|---|
| **Stream results** | PCA + on-device detector (YOLO-World / AI Building Blocks) | receives crops+masks+poses | low latency, live UX |
| **Record session** | PCA records color+pose+depth to a session dir, uploads | runs detection + heavy reconstruction on GPU | max quality |

See [`docs/QUEST3_CAPTURE.md`](QUEST3_CAPTURE.md) for the Unity C# reference and
the session schema `Quest3SessionSource` expects.

> A folder of phone photos (`FolderSource`) is a perfectly good stand-in while
> the headset app is being built — the downstream stages don't care.

---

## Stage 2 — IDENTIFY (open-vocabulary detection + segmentation)

To "track *every* object, clothes to anything" you cannot use a fixed 80-class
detector. You need **open-vocabulary** models that take class names as text.

| Model | Role | Trade-off |
|---|---|---|
| **YOLO-World** | real-time, text-prompted detection | fast, always-on; lower ceiling |
| **Grounding DINO 1.5** | open-set detection, SOTA accuracy (54.3 AP COCO, 55.7 LVIS zero-shot) | heavier; use on keyframes |
| **SAM 2 / Grounded SAM 2** | box → pixel-perfect mask, **tracks instances across video** | gives the matte for reconstruction + a stable `track_id` |

**Recommended topology:** YOLO-World runs on every frame (cheap); promising
detections are escalated to **Grounded SAM 2**, which produces a clean mask and
a `track_id` that keeps one physical object as one asset while the user walks
around it. That mask is exactly what the reconstructor needs to matte the
object off its background.

Adapters: `assetpipe/detect/yolo_world.py`, `assetpipe/detect/grounded_sam.py`.
Fallback that runs with nothing installed: `heuristic.py`.

---

## Stage 3 — RECONSTRUCT (2D frames → 3D mesh)

Two families, both worth having — the pipeline picks per object:

### A. Single-image generative — the "one glance = one asset" path
Feed one clean, background-removed crop → full textured mesh in seconds.
- **TRELLIS 2** (Microsoft Research) — strongest open-source image-to-3D,
  PBR output; preferred over InstantMesh in ~94% of a user study.
- **Hunyuan3D 2.1** (Tencent) — leading alternative; DiT shape + Paint texture.

Best for isolated, hard-to-scan items and the fast capture UX. Run it as a
local GPU **HTTP service** and keep the CUDA deps out of this package
(`assetpipe/reconstruct/trellis.py`).

### B. Multi-view scan — the high-fidelity path
User walks around the object; SAM 2 keeps it on one track; frames →
**Nerfstudio** (COLMAP poses → `splatfacto` Gaussian splat → surface mesh via
SDF/Poisson). Higher metric accuracy; needs more views and compute
(`assetpipe/reconstruct/nerfstudio.py`).

### C. Procedural (this repo's runnable default) — the "box test"
Cuboid-ish items (boxes, packages, furniture, appliances) are well
approximated by an **oriented bounding box**. Given dimensions (from a real
backend, or from depth + intrinsics, or a fallback) we emit a clean cuboid
mesh with **zero ML dependencies**. This is the right first milestone and is
what `python -m assetpipe demo` exercises (`reconstruct/procedural_box.py`).

**Sizing:** when the Quest depth frame + intrinsics are present, back-project
the mask to metric size: `width ≈ (x₂−x₁)·z / fx`, etc. Hook is marked in
`procedural_box._metric_from_depth`.

---

## Stage 4 — DIGITALIZE (mesh → simulatable twin)

- **Mesh**: OBJ for the procedural path (pure stdlib writer); GLB carried
  through for textured generative/scan meshes (`digitalize/mesh_io.py`).
- **URDF**: wrap the mesh as a single-link robot with mass + solid-cuboid
  inertia + collision, so it drops straight into **PyBullet / Isaac Sim /
  Gazebo / MuJoCo** (`digitalize/urdf.py`). Tools that do the same for arbitrary
  meshes: `obj2urdf`, **scikit-robot** (`convert-urdf-mesh`), **Phobos** (Blender).
- **Articulated objects** (a drawer, a laptop, a door): a single rigid link
  isn't enough. **URDF-Anything** reconstructs articulated URDFs (joints +
  parts) from images via a 3D multimodal LLM — the drop-in upgrade when you go
  beyond rigid props.

Why URDF and not just a mesh? A URDF makes the asset **actionable** in
simulation — grasp it, stack it, drop it — which is what turns a catalog of
scans into a usable digital twin.

---

## Stage 5 — ORGANIZE (the digital twin / control center)

- **Catalog** (`catalog/store.py`): one **SQLite** table is the source of
  truth — label, category, mesh path, URDF path, dimensions, timestamp,
  source, **location**, **world pose**, tags. Indexed by label/category/
  location so the product question ("what did I have and where?") is a query.
  Point the real control center's API at this schema, or sync rows to a cloud DB.
- **Control center** (`catalog/viewer.py`): a **self-contained HTML** file with
  every asset's metadata and mesh embedded, plus a tiny dependency-free canvas
  3D renderer (orbit on drag). No server, no CDN — open it anywhere. Swap for a
  real three.js/GLB web app in production.

---

## Data model

`assetpipe/types.py` defines the four records that flow through the stages:
`Frame → Detection → Reconstruction → Asset`. `Asset` is what the twin stores.
`Detection.track_id` and `Reconstruction.world_pose` are the fields that make
"the same object, seen over time, placed in the world" work.

## Extending

Every stage is an ABC. To add a backend: implement the interface, drop the file
in the sub-package, and pass it to `AssetPipeline`. Nothing else changes. See
[`ROADMAP.md`](ROADMAP.md) for the phased plan from "box" to "everything."

## Sources

- Meta Passthrough Camera API — https://developers.meta.com/horizon/documentation/spatial-sdk/spatial-sdk-pca-overview/ · https://www.uploadvr.com/quest-passthrough-camera-api-experimental-out-now/
- YOLO-World / Grounding DINO / Grounded SAM 2 — https://pyimagesearch.com/2026/01/19/grounded-sam-2-from-open-set-detection-to-segmentation-and-tracking/ · https://arxiv.org/pdf/2405.10300
- TRELLIS / Hunyuan3D — https://github.com/microsoft/TRELLIS · https://blog.datameister.ai/3d-generative-ai-image-based-3d-reconstruction
- Nerfstudio / gsplat — https://github.com/nerfstudio-project/nerfstudio · https://github.com/nerfstudio-project/gsplat
- Mesh → URDF — https://github.com/alaflaquiere/obj2urdf · https://scikit-robot.readthedocs.io/en/latest/reference/how_to_create_urdf_from_cad.html · https://github.com/dfki-ric/phobos
- Articulated digital twins — URDF-Anything — https://arxiv.org/pdf/2511.00940
