# Integration with `son` (LifeTwin / JayAsset) and `clawd`

This documents how `3dasset` (this repo) fits the existing ecosystem instead
of duplicating it. Read after `docs/ARCHITECTURE.md`.

## The three repos

| Repo | Role | 3D-pipeline status |
|---|---|---|
| **clawd** | Agent orchestration "brain" — specialist Jay desks (jayx, jayfin, jayrobot, jayresearch…), skills, playbooks. The pipeline plan's *"Jay is the orchestration brain, not the renderer."* | Orchestrates; no pipeline code |
| **son** | The LifeTwin / JayAsset **platform**: canonical schema, spatial model, `api/quest-asset-scan.js`, `src/son/cad_urdf_usd.py`, and control-center apps (`jay3d`, `jayasset`, `life-twin`) | Has the **contracts + converter + UI**; **no CV/reconstruction compute** |
| **3dasset** | Quest capture → open-vocab detect → 3D reconstruct → mesh | The **missing compute worker** |

`jay3d` and `jayasset` are **apps inside `son`** (`docs/visuals/jay3d-asset-closet-demo.html`,
`api/jayasset-store.js`, `docs/visuals/jayasset-vr-app.js`), not separate repos.

## What son already has (don't rebuild)

- **Canonical data model** — `src/son/spatial_assets.py`: `Asset`, `Room`,
  `Zone`, `SpatialAnchor`, `SpatialTransform`, `Document`, `AssetLink`,
  `Observation` (`ObservationSource.QUEST` already reserved), `AssetEvent`,
  `SceneSnapshot`. Plus the JS `TwinItem` zod schema in `schema/index.js`
  (`model_3d_state`, `model_3d_ref`, `preview_image_ref`, `capture_plan`).
- **Quest scan ingest API** — `api/quest-asset-scan.js`: accepts a scan of
  `detected_assets[]`, dedupes into the closet, emits **JayAsset drafts**, and
  gates persistence behind owner approval + privacy guard. Its
  `OPEN_PIPELINE_STACK` already names the same tools 3dasset uses (open-vocab
  detector + SAM masks, ObjectGS/OpenSplat3D splats, Open3D/PCL, RTAB-Map).
- **Mesh → URDF/USD converter** — `src/son/cad_urdf_usd.py`: OBJ/STL →
  one-link URDF (visual+collision+inertial) → `.usda` stage → manifest.
  **This is the canonical digitalizer** — richer than 3dasset's stdlib
  `digitalize/urdf.py` (adds USD, STL parsing).
- **The 6-tier acquisition plan** — `docs/physical_asset_3d_pipeline_plan.md`
  (official → marketplace → scan → multi-image → single-image → proxy), V0–V5
  grades, rights/provenance model.

## The gap 3dasset fills

son's `quest-asset-scan.js` **receives** `detected_assets[]` — but nothing in
son **produces** them. There is no code that runs the Quest Passthrough
Camera capture, the open-vocabulary detection + masks, or the 2D→3D
reconstruction. That producer is exactly `3dasset`:

```
  Quest 3 PCA ─▶ assetpipe (this repo) ─▶ son /api/quest-asset-scan ─▶ JayAsset closet ─▶ Life-Twin Control Center
    capture       detect + reconstruct        (contract + drafts)         (canonical records)      (jay3d / jayasset UI)
```

3dasset's stages map onto son's declared `OPEN_PIPELINE_STACK` step
`semantic_detection` (YOLO-World + Grounded SAM 2) and `object_splats`
(TRELLIS / Nerfstudio), and onto Tiers C/D/E of the acquisition plan.

## The bridge (implemented)

`assetpipe/integrations/son_twin.py` converts assetpipe catalog rows into the
exact payload `api/quest-asset-scan.js` consumes, and can POST it:

```bash
# emit the payload (preview) from the local twin catalog
python -m assetpipe export-son --out twin_out

# POST it to a running son instance (preview = no persist)
python -m assetpipe export-son --endpoint http://localhost:3000/api/quest-asset-scan
# add --ingest to persist (action=ingest_scan), subject to son's owner gate
```

Verified end-to-end: the payload is accepted by son's real handler and turned
into JayAsset drafts + closet updates (`tests/test_son_bridge.py` checks the
shape; a node harness confirmed the live handler returns `ok:true`).

### Field mapping (assetpipe → son detection)

| assetpipe `Asset` | son `detected_assets[]` | notes |
|---|---|---|
| `asset_id` | `asset_id` | stable id |
| `label` | `label` | open-vocab label |
| `category` | `kind` | container / clothing / … |
| `extra.detection_score` | `confidence` | detector score |
| `location` + `world_pose` | `location{room,zone,x,y,z,yaw}` | pose → xyz+yaw |
| `recon_method` | `jay3d_state` | procedural→"box proxy ready", trellis→"single-image mesh", nerfstudio→"multi-view scan mesh" |
| `source` | `evidence[]` | quest3→rgb+anchors+depth |
| `mesh_path` / `urdf_path` | `model_3d_ref` / `urdf_ref` | artifact handoff |

## Overlap resolution

- **URDF/USD**: prefer son's `cad_urdf_usd.py` as canonical when the two run
  together (it also emits USD). 3dasset's `digitalize/urdf.py` stays as a
  self-contained fallback (adds computed solid-cuboid inertia) for standalone
  runs. Feed assetpipe's `model.obj`/`model.glb` into son's converter.
- **Schema**: don't fork son's model. assetpipe's `Asset` is a lightweight
  working record; the bridge maps it onto son's canonical `TwinItem` / scan
  contract. son remains the source of truth.
- **Control center**: assetpipe's offline HTML viewer is a dev tool; the
  product UI is son's `life-twin-control-center` / `jay3d` / `jayasset`.

## Open decision (see chat)

Whether to (A) fold assetpipe into `son` as a `worker/` service, (B) keep it a
standalone GPU worker repo that talks to son's API over HTTP, or (C) a hybrid
(shared contract package). The bridge above works under all three.
