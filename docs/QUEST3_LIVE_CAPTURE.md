# Quest 3 → live point cloud: the three capture routes

Goal: press one button on the headset, watch the point cloud build. Three
routes, from zero-install to "direct camera access".

## Route A — Quest browser, HTTPS live page (try first, may work)

The Quest browser only exposes `getUserMedia` on **secure (https) pages**,
like every Chromium. Whether it then offers the **passthrough camera** as a
video device depends on the Horizon OS / browser version (Meta gated camera
access behind the Passthrough Camera API rollout; recent builds + the
headset-cameras permission prompt are required).

1. In the headset browser open `https://<4080-ip>:8443/live`
2. Accept the self-signed-certificate warning (once)
3. Tap **● Start** → if a camera permission prompt appears and the preview
   shows passthrough, you have the full Scaniverse loop on-headset
4. If it reports *camera blocked / no camera*, the browser on this OS
   version doesn't expose the cameras to web pages → Route B or C.

## Route B — record → pick (works today, zero install)

1. Meta button → Camera → **Record** → slow orbit of the object
2. Headset browser → `http://<4080-ip>:8090/scan` → **● Scan** → pick the
   recording
3. The page shows the cloud **building during reconstruction**, then links:
   `scene_clean.ply` (background removed), `scene_mesh.glb`, viewer.

## Route C — direct camera access: Meta's open-source PCA app (the real fix)

Meta's **Passthrough Camera API** (Horizon OS v74+) gives *native* apps the
RGB camera feed, and Meta open-sourced working samples:
<https://github.com/oculus-samples/Unity-PassthroughCameraApiSamples>
(Apache-2.0 / MIT-licensed samples; needs the "Headset Cameras" permission).

The worker is already the receiving end — the on-device app only has to do
this, ~50 lines in the sample's `WebCamTexture`-style loop:

    POST http://<4080-ip>:8090/live/start            -> {session_id}
    every ~700 ms:
      JPEG-encode the current PCA frame (960 px wide is plenty)
      POST /live/{sid}/frame   multipart field "image"
      (optional: camera pose from OVRCameraRig as form field "pose")
    on Stop:
      POST /live/{sid}/finish  -> ply / splat / clean / mesh URLs

The in-headset UI can render the growing cloud by polling
`GET /live/{sid}/cloud?since=V` — same JSON the web page uses (base64
float32 positions + uint8 colors), trivially drawable as a Unity point
mesh in the same VR session. That is the full Scaniverse experience with
nothing between the cameras and the pipeline.

Roadmap fit: this is the Phase-3 "PCA recorder" (docs/ROADMAP.md) — the
`/live` endpoints replaced the custom protocol it was going to need.

## Meshing is separate (already automatic)

Whatever the capture route, `finish` runs the same post chain:
background removal (`scene/clean.py`) → vertex-colored GLB
(`scene/mesh.py`, open3d Poisson). Quality ladder for the mesh step:
sparse SfM (now) → dense cloud (VGGT) → TRELLIS image→textured-mesh
(`services/trellis_server.py`) for Meshy-grade assets.
