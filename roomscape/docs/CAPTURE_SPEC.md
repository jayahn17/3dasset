# Capture spec — session manifest v2

A superset of the CrateScanner schema that `SessionExporter.swift` writes and
`assetpipe/scene/rgbd_session.py` reads. **Additive only** — every v1 session
stays valid, and every existing tool keeps working. New fields are how a
room-scale sweep differs from an object scan.

---

## What went wrong in the July 30 export (the motivating bug)

The package `crate_20260730_17_12_16` contained 16 keyframes at 4032×3024 with
poses and intrinsics — and **zero depth images**, plus `"frames": []`.

Two independent causes, both worth fixing before P1:

1. **`captureHighResolutionFrame` returns an ARFrame with no `sceneDepth`.**
   `SessionExporter.recordKeyframe` guards on
   `frame.smoothedSceneDepth ?? frame.sceneDepth` and silently omits the depth
   entry when it is nil — which is always, on the high-res path. Fix: cache the
   most recent streaming `ARFrame` depth and attach the nearest-timestamp depth
   map to the keyframe, recording the timestamp delta.
2. **The streaming track recorded nothing**, so the LiDAR data that *was*
   available never landed either.

Both failures were silent. **v2 requires a self-check**: `finalize()` computes
the actual on-disk counts and coverage and writes them into the manifest, and
the capture UI refuses to present a session as complete if `depth_frames == 0`
in a mode that promised depth. A capture that quietly loses its LiDAR is worse
than one that fails loudly, because you discover it an hour later on the Linux
box.

---

## Directory layout

```
session/
  manifest.json
  frames/    0000.jpg  0001.jpg  ...      color, 1600–2000 px wide
  depth/     0000.png  0001.png  ...      uint16, millimetres, 256×192
  conf/      0000.png  0001.png  ...      uint8 ARKit confidence 0/1/2
  keyframes/ k0000.jpg ...                optional 12 MP stills (Detail/hybrid)
  room_mesh.obj                           ARMeshAnchor merge — collision proxy
  preview.jpg                             one representative frame for the UI
```

Rationale for the numbers:

- **1600–2000 px, not 12 MP.** 800 frames × 12 MP is ~50 GB and cannot be
  trained on a 16 GB card without downscaling anyway. Downscale once, at
  capture, where the ISP does it well.
- **Depth stays native 256×192.** Do not upsample on device — the trainer
  should see the sensor's real resolution and confidence, and decide.
- **Confidence is not optional.** ARKit's low-confidence depth is badly wrong
  on dark, shiny, and distant surfaces. Depth supervision without a confidence
  mask actively damages a scene; this is the field that makes the LiDAR
  advantage real rather than theoretical.

---

## manifest.json

```jsonc
{
  "schema": "roomscape/session/2",
  "source": "cratescanner",
  "session_id": "cap_01J8Z9...",
  "captured_at": "2026-07-30T17:12:16Z",
  "mode": "room",                       // "object" | "room" | "hybrid"
  "depth_unit": "mm",

  "device": {
    "model": "iPad Pro 13-inch (M4)",
    "os": "iPadOS 26.1",
    "has_lidar": true,
    "app_version": "1.4.0 (208)"
  },

  // ── the fields that make a scene VR-ready without human intervention ──
  "world": {
    "alignment": "gravity",             // ARKit default: +Y is real up
    "up": [0, 1, 0],
    "scale": "metric",
    "floor_y": -1.42,                   // from plane detection, metres
    "origin_note": "ARKit world origin = first tracked frame"
  },

  "coverage": {
    "azimuth_bins": 36,                 // 10° each
    "azimuth_seen": 34,
    "elevation_bins_seen": [1, 1, 1, 0],
    "score": 0.91,                      // gate for P1 exit criteria
    "loop_closed": true
  },

  "counts": {                           // computed at finalize, never trusted from memory
    "frames": 612,
    "depth_frames": 612,
    "conf_frames": 612,
    "keyframes": 0,
    "bytes": 1843200000
  },

  "consent": {
    "captured_by": "jayahn",
    "space_type": "private_residence",
    "people_present": false,
    "blur_faces_on_ingest": true
  },

  "frames": [
    {
      "id": "f00000",
      "t": 12018.295328833,             // ARKit timestamp, seconds
      "color": "frames/0000.jpg",
      "color_size": [1920, 1440],
      "depth": "depth/0000.png",
      "depth_size": [256, 192],
      "conf": "conf/0000.png",
      "depth_dt_ms": 8.3,               // |t_depth − t_color|; 0 when same frame
      "pose": [ /* 16 floats, 4×4 camera-to-world, row-major */ ],
      "intrinsics": [3014.85, 3014.85, 2009.08, 1517.33],  // at color_size
      "tracking": "normal",             // ARCamera.TrackingState
      "sharpness": 142.7,               // variance of Laplacian
      "exposure_ms": 16.6,
      "iso": 320
    }
  ],

  "keyframes": [ /* same shape; present only in object/hybrid mode */ ],
  "room_mesh": "room_mesh.obj",
  "object_hint": null,
  "location": "living room"
}
```

### Field notes that matter downstream

- **`intrinsics` are always stated at `color_size`.** The v1 bug where `K` was
  scaled to the *depth* resolution for stream frames but the *color* resolution
  for keyframes (`K_color`) cost debugging time. v2: one convention, one field,
  always matching the image it describes.
- **`pose` is camera-to-world, row-major, ARKit convention** (−Z forward, +Y
  up). The converter to COLMAP's world-to-camera lives in exactly one place:
  `pipeline/normalize.py`. Never inline that flip anywhere else.
- **`depth_dt_ms`** lets the trainer down-weight depth that was captured while
  the camera was moving relative to its color frame.
- **`sharpness`** is recorded rather than acted on at capture time when it is
  marginal — the pipeline may want more frames than the device would have kept.
- **`coverage.score`** is the P1 exit gate and the capture HUD's progress bar.
  One number the user can chase.

---

## Capture UX for room mode

The thing that separates a scan that reconstructs from one that does not is
**coverage and parallax**, and users have no intuition for either. The app has
to lead.

1. **Frame the room, tap start.** Announce the target: "walk the perimeter, then
   the middle, ~4 minutes."
2. **Live coverage shell** — a translucent dome or wireframe box around the user
   that fills in as directions get seen. Unfilled patches read as "go look
   there" without instruction. `Support/GuidedCapture.swift` and the AR frustum
   markers already in `ScanViewModel.dropKeyframeMarker` are the parts to build
   on.
3. **Move-slower nudge** when angular velocity would blur the next frame — the
   sharpness gate should be visible, not silent.
4. **Loop-closure prompt** — return to the starting viewpoint at the end. This
   single instruction removes most of the drift that pose refinement would
   otherwise have to fix.
5. **Two heights.** Prompt for a second pass at ~1.1 m and ~1.7 m. Splats look
   convincing only from viewpoints near ones they were trained on, and a VR user
   stands, crouches, and leans.
6. **Don't stand still and spin.** Pure rotation gives zero parallax and is the
   classic way to produce a scan that will not solve. Detect it and say so.

---

## Validation, on device and on ingest

The device writes; the server verifies. Both run the same checks so a bad
capture is caught while the user is still in the room:

| Check | Threshold | Action |
|---|---|---|
| depth frames present when `mode == "room"` | > 0.95 × frames | hard fail |
| coverage score | ≥ 0.85 | warn, offer to continue capturing |
| median sharpness | above per-device floor | warn |
| tracking-normal fraction | ≥ 0.9 | warn — relocalization events cause pose jumps |
| translation extent | ≥ 1.5 m | hard fail — a pure-rotation scan will not solve |
| pose continuity | no jump > 0.5 m between consecutive frames | flag frames for BA to distrust |
| manifest counts vs files on disk | exact | hard fail |
