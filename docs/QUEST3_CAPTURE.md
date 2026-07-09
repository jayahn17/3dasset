# Quest 3 Capture — Passthrough Camera API integration

This is the on-device half of the pipeline. It produces the frames that
`assetpipe/capture/quest3.py` consumes. Everything here targets **Horizon OS
v76+**, where the Passthrough Camera API (PCA) is publicly available and
store-publishable.

## What the headset gives you

| Signal | API | Use in pipeline |
|---|---|---|
| RGB frames (≤1280×960 @30 FPS) | Passthrough Camera API | detection + reconstruction input |
| Camera pose per frame | PCA / OpenXR tracking | metric scale, world anchoring |
| Camera intrinsics (fx, fy, cx, cy) | PCA | back-project mask → metric size |
| Depth | Depth API | robust sizing + occlusion |
| Room mesh / planes | Scene API | ground plane, placement, "location" |
| On-device detection | AI Building Blocks | live labels without a server |

## Recommended UX for "record this object"

1. User looks at an object and triggers record (pinch / controller / voice).
2. On-device YOLO-World (or AI Building Blocks) proposes a label + box.
3. Two capture modes:
   - **Quick**: grab one well-framed keyframe → single-image reconstruction.
   - **Scan**: prompt the user to walk 180–360° around it (~20–60 frames);
     SAM 2 keeps it on one `track_id` → multi-view reconstruction.
4. Frames + pose + intrinsics (+ depth) are written to a **session** and
   uploaded, or detections are streamed live.

## Session schema (what `Quest3SessionSource` reads)

```
session/
  manifest.json
  color/0000.jpg  0001.jpg ...
  depth/0000.png  ...            (optional, 16-bit mm)
```

```jsonc
// manifest.json
{
  "object_hint": "cardboard box",     // optional label from on-device detector
  "location": "garage shelf",         // optional, from Scene API anchor
  "frames": [
    {
      "id": "f00000",
      "t": 0.0,                         // seconds
      "color": "color/0000.jpg",
      "depth": "depth/0000.png",        // optional
      "pose":  [ /* 16 floats, 4x4 camera-to-world, row-major */ ],
      "intrinsics": [ fx, fy, cx, cy ]
    }
    // ...
  ]
}
```

## Unity C# reference (PCA → session frame)

> Sketch based on Meta's `Unity-PassthroughCameraApiSamples`. Grabs the
> WebCamTexture the PCA exposes, reads the camera pose, and appends a frame.

```csharp
using PassthroughCameraSamples;   // from Meta's sample package
using UnityEngine;

public class SessionRecorder : MonoBehaviour {
    WebCamTextureManager camMgr;   // provides the passthrough WebCamTexture
    int frame;

    void CaptureFrame() {
        var tex = camMgr.WebCamTexture;                 // 1280x960 RGB
        var intr = PassthroughCameraUtils.GetCameraIntrinsics(
                       PassthroughCameraEye.Left);       // fx, fy, cx, cy
        var pose = PassthroughCameraUtils.GetCameraPoseInWorld(
                       PassthroughCameraEye.Left);       // position + rotation

        // 1) encode `tex` to color/{frame}.jpg
        // 2) append manifest entry: id, t=Time.time, color, pose(4x4), intrinsics
        // 3) (optional) sample EnvironmentDepth -> depth/{frame}.png
        frame++;
    }
}
```

Then upload the `session/` directory and run:

```bash
python -m assetpipe run --quest-session /path/to/session --location "garage"
```

## Live-streaming alternative

If you run detection on-device (AI Building Blocks / on-device YOLO-World),
stream per-detection payloads (label, box, mask PNG, pose) to a small server
that constructs `Detection` objects and feeds `AssetPipeline`. This keeps the
40–60 ms capture-to-label loop on the headset and reserves the network/GPU for
the heavy reconstruction step.

## References
- PCA overview — https://developers.meta.com/horizon/documentation/spatial-sdk/spatial-sdk-pca-overview/
- Passthrough Camera (Android) — https://developers.meta.com/horizon/documentation/android-apps/passthrough-camera/
- New-era MR blog (specs, AI Building Blocks) — https://developers.meta.com/horizon/blog/new-era-mixed-reality-passthrough-camera-api-machine-learning-computer-vision/
- Meta sample project — search "Unity-PassthroughCameraApiSamples" on GitHub
