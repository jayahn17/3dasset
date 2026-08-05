# viewer-quest/

Native Quest 3 app — **Tier A′**, the store path (P5).

Runtime decided *after* P3 measures what the browser costs us. Candidates:
Unity 6 + OpenXR with `UnityGaussianSplatting` (MIT), PlayCanvas packaged in a
WebView, or native Vulkan. Expect native to buy 2–3× the gaussian budget over
WebXR — that headroom is the only thing that justifies the build.

Must consume the **identical** `scene.json` bundle as `viewer-web/`. Its
advantage should come from a larger LOD level and a better sorter, never from a
different pipeline.

Submission requirements: [../docs/STORE_PATH.md](../docs/STORE_PATH.md)
