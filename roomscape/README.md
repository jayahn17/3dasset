# Roomscape

**Our own Hyperscape Capture, built on open source.** Sweep a real room with an
iPad Pro, reconstruct it as a Gaussian splat on the 4080, and walk through it in
VR — with every frame of capture data staying in our system.

Status: **plan only.** No implementation code yet. The plan is the deliverable;
read it before writing anything.

📖 **[docs/GAMEPLAN.md](docs/GAMEPLAN.md)** ← start here

| Doc | What it settles |
|---|---|
| [GAMEPLAN.md](docs/GAMEPLAN.md) | phases P0–P6, decisions + kill criteria, risks, effort |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | components, stage contracts, the scene bundle |
| [OSS_LICENSE_MAP.md](docs/OSS_LICENSE_MAP.md) | which 3DGS repos we can legally ship — **read before adding any dependency** |
| [CAPTURE_SPEC.md](docs/CAPTURE_SPEC.md) | session manifest v2, room-mode capture UX, validation |
| [RENDER_TARGETS.md](docs/RENDER_TARGETS.md) | the 13.8 ms frame budget and how we measure it |
| [DATA_MODEL.md](docs/DATA_MODEL.md) | registry schema, provenance, privacy, retention |
| [STORE_PATH.md](docs/STORE_PATH.md) | Meta Horizon Store + Apple submission reality |

---

## The idea in one paragraph

Meta's Hyperscape scans a room with a Quest, trains a splat on their cloud GPU
fleet, and **streams the rendered result back** to the headset — which is why it
looks so good and why it needs a datacenter. We do the same loop with two
substitutions: capture on an **iPad Pro**, whose LiDAR hands us metric scale,
gravity alignment, and a collision mesh that headset cameras do not; and
**render on-device** from a compressed splat, so there is no per-viewer GPU
cost. We give up some per-frame fidelity. We get a system that is ours, works
offline, and costs nothing to let someone use.

---

## Layout

```
roomscape/
  docs/            the plan (this is what exists today)
  capture/         iPad room-mode capture — extends mobile/CrateScannerApp
  pipeline/        session → poses → gsplat → clean → compress → bundle
  service/         ingest API, job queue, registry, blob store
  viewer-web/      PlayCanvas WebXR viewer (Tier A — ships first, no store)
  viewer-quest/    native Quest app (Tier A′ — store path)
  scenes/          local scene bundles (gitignored)
```

## Relationship to the rest of `3dasset`

Roomscape is the **product layer**; `assetpipe` is the engine. We reuse its SfM,
splat backends, TSDF fusion, and PLY tooling rather than forking them, and we
keep the doctrine from `docs/SCENE_SPLAT.md`:

> **splat = the room you walk through; TRELLIS = the clean objects inside it**

Rooms are Roomscape. Objects stay in `assetpipe`.

---

## Next action

**P0 — First light** (2–4 days): take the iPad capture already on disk, push it
through gsplat, and stand inside it in the Quest browser. Bad quality is fine;
the point is to close the loop before building anything on assumptions. Full
task list in [GAMEPLAN.md § P0](docs/GAMEPLAN.md).

Two things block nothing else and should start immediately:

1. `docs/LICENSE_AUDIT.md` — verify every candidate dependency's license at a
   pinned commit. A research-licensed trainer discovered at submission time
   means re-training every scene.
2. Pick **the benchmark room** — one well-lit, textured, ~4×5 m space that every
   phase is measured against.
