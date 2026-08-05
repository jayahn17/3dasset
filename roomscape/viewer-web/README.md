# viewer-web/

PlayCanvas (MIT) WebXR viewer — **Tier A, and the thing that ships first.**

The Quest browser runs WebXR over HTTPS with no store, no APK, no review. A URL
is the whole distribution mechanism, so this reaches a headset in days and
iterates on a page refresh.

Scope: scene list, progressive/LOD load (<3 s to first pixel), teleport
locomotion against `collision.glb`, snap turn, height calibration, comfort
vignette, and the perf work — culling, one sort for both eyes, resolution
scaling, fixed foveation.

Consumes the same `scene.json` bundle as the native viewer. If the two diverge,
we are maintaining two products.

Budget and measurement method: [../docs/RENDER_TARGETS.md](../docs/RENDER_TARGETS.md)
