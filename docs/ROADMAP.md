# Roadmap — from "box" to "everything"

Each phase is shippable and de-risks the next. The stage interfaces don't
change between phases — you only swap implementations.

## Phase 0 — Skeleton (done ✓)
- Five swappable stages, data model, catalog, control-center viewer.
- **Box test** runs end-to-end with zero dependencies:
  `python -m assetpipe demo` → meshes + URDF + SQLite twin + HTML viewer.
- Test suite green.

## Phase 1 — Real perception on real images
- Wire **YOLO-World** (`detect/yolo_world.py`): `pip install ultralytics`.
- Run on a folder of phone photos of a **real box**; confirm label + bbox.
- Add **Grounded SAM 2** for masks + `track_id`.
- **Exit:** point the camera at a box, get the right label and a clean crop.

## Phase 2 — Real reconstruction
- Stand up a **TRELLIS 2** (or Hunyuan3D) HTTP service; implement the POST in
  `reconstruct/trellis.py`. Single photo → textured GLB.
- Carry GLB through digitalize (add `trimesh` for GLB→OBJ / convex-hull
  collision).
- **Exit:** one photo of a box → textured 3D asset in the twin.

## Phase 3 — Quest 3 capture
- Build the Unity recorder (see `docs/QUEST3_CAPTURE.md`): PCA frames + pose +
  intrinsics (+ depth) → session upload.
- Run `assetpipe run --quest-session ...`; use pose + depth for **metric size**
  and **world anchoring** (fills `Asset.world_pose` / `location`).
- **Exit:** record a box from the headset; it lands in the twin at true scale
  and a real room location.

## Phase 4 — Beyond the box
- **Multi-view scan** path via **Nerfstudio + gsplat** for irregular items
  (`reconstruct/nerfstudio.py`): walk-around capture → high-fidelity mesh.
- **Open vocabulary at scale**: clothes, shoes, books, electronics — the
  detector already takes arbitrary text classes.
- **Articulated objects**: swap in **URDF-Anything** to emit jointed URDFs
  (drawers, laptops, doors).
- **Exit:** "record anything," rigid or articulated.

## Phase 5 — Product: the Life Twin Control Center
- Replace the static HTML viewer with a real web app over `AssetCatalog`
  (three.js/GLB, search, per-user auth, cloud sync).
- **Where-was-it** timeline: because every asset carries `world_pose`,
  `location`, and `created_at`, show an object's location history.
- Multi-tenant catalog; export per-user asset packs (mesh + URDF bundles).

## Cross-cutting concerns to plan for
- **Privacy**: passthrough frames are camera data — process on-device where
  possible, get consent, and don't upload raw frames you don't need.
- **Dedup / re-identification**: recognizing an object you've already recorded
  (embedding match on the crop) so you update rather than duplicate an asset.
- **Scale accuracy**: always prefer depth+intrinsics over monocular guesses.
- **Storage**: meshes/GLBs are large — keep them out of git (see `.gitignore`)
  and in object storage keyed by `asset_id`.
