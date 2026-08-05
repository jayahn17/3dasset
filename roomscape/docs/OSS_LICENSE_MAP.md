# The open-source 3DGS landscape, filtered by what we can actually ship

You asked to "scrape from all the open-source 3D Gaussian splatting." We can —
but not uniformly. **Most of the famous 3DGS repos cannot be used in a product.**
This document is the filter. It is the highest-consequence document in the
project, because a license mistake is only discovered at submission time, after
every scene has been trained with the tainted tool.

> **Confidence note.** Licenses change and my knowledge has a mid-2026 cutoff.
> Everything below is a starting hypothesis with a stated confidence. **Verify
> the LICENSE file at the pinned commit before any dependency is merged**, and
> record the verification in `roomscape/docs/LICENSE_AUDIT.md` with the date,
> commit SHA, and a copy of the license text. That file does not exist yet —
> creating it is P0 task #1.

---

## The rule

**Anything that touches a shipped artifact must be Apache-2.0, MIT, BSD, or
similar.** That includes the trainer, because research licenses in this field
typically restrict *the outputs*, not just the code.

The canonical trap: INRIA's `graphdeco-inria/gaussian-splatting` — the original
3DGS implementation — carries a non-commercial research license, and its CUDA
rasterizer `diff-gaussian-rasterization` carries the same terms. Dozens of
follow-up repos (many of the best-known quality improvements) simply `pip
install` that rasterizer, which pulls the restriction into anything built on
them. **A repo being on GitHub with a paper attached says nothing about whether
you can use it.**

Practical consequence: we do not train with the INRIA lineage at all, not even
"just for experiments," because experiments become the scenes you keep.

---

## Green — build on these

| Project | Believed license | Confidence | Role for us |
|---|---|---|---|
| **gsplat** (nerfstudio-project) | Apache-2.0 | high | **The trainer.** Independently written CUDA rasterizer, actively maintained, not INRIA-derived. Already the backend behind `assetpipe`'s splatfacto path |
| **nerfstudio** | Apache-2.0 | high | training harness, `splatfacto`, dataparsers, export. Already wired: `assetpipe/scene/_splat_driver.py` |
| **Brush** (Rust/WGPU) | Apache-2.0 / MIT | medium | fallback trainer *and* a cross-platform renderer — trains and views on web/Android/desktop from one codebase. Worth a serious look if gsplat's ergonomics fight us |
| **PlayCanvas engine** | MIT | high | **The web/WebXR viewer.** First-class gaussian-splat support and mature WebXR — the shortest path onto a Quest |
| **SuperSplat** (PlayCanvas) | MIT | high | splat editor/inspector — cropping, cleanup, format conversion, sanity checks during P0–P2 |
| **UnityGaussianSplatting** (aras-p) | MIT | medium-high | candidate native Quest renderer in P5 |
| **Babylon.js** | Apache-2.0 | high | alternative web viewer with WebXR + splat support |
| **@mkkellogg/gaussian-splats-3d** | MIT | medium | three.js splat renderer, if we end up in a three.js codebase |
| **spz** (Niantic) | MIT | medium | compressed splat container, ~10× smaller than raw PLY |
| **COLMAP** | BSD | high | SfM / bundle adjustment (already used via `pycolmap`) |
| **GLOMAP** | BSD-ish | medium | much faster global SfM; the pose-refinement candidate for P2 |
| **Open3D** | MIT | high | meshing, TSDF, point-cloud ops. Already a dependency |

## Yellow — verify carefully, or use for research only

| Project | Concern |
|---|---|
| **3DGRUT / 3DGUT** (NVIDIA) | core believed Apache-2.0 and it is genuinely the right trainer for fisheye + rolling shutter (i.e. Quest passthrough in P6) — but it builds on **OptiX**, whose SDK has its own redistribution terms. Fine for training on our box; check before shipping any renderer derived from it. Already wired as `--backend 3dgut` |
| **SOG / self-organizing gaussian compression** | the compression *idea* is published research; the *implementations* differ in license. Prefer whichever compressed format PlayCanvas reads under MIT |
| **nvblox** | NVIDIA license — we already use it for TSDF; it produces a mesh, not a shipped renderer, so the exposure is limited. Confirm anyway |
| Any model weights (YOLO-World, SAM 2, etc.) | weights carry separate licenses from code — YOLOv8-lineage weights are AGPL, SAM 2 is Apache-2.0. Only relevant if we use them for the privacy blur pass |

## Red — do not put in the shipping path

| Project | Why |
|---|---|
| `graphdeco-inria/gaussian-splatting` | non-commercial research license, restricts outputs |
| `diff-gaussian-rasterization` | same terms; the transitive-dependency trap |
| **Mip-Splatting**, **Scaffold-GS**, **Octree-GS**, and most 2024-era quality papers | built on the above unless independently reimplemented |
| Anything whose LICENSE says "research", "non-commercial", "academic", or "evaluation" | means what it says |

**Reimplementation is legitimate.** If a red-list paper describes a technique
we want (antialiasing, anchor-based density), implementing the *method* on top
of gsplat is fine — papers describe ideas, licenses bind code. That is exactly
what gsplat itself is.

---

## The gate

Add to the definition of done for any PR that adds a dependency:

1. Record name, repo URL, **pinned commit SHA**, license SPDX, and date checked
   in `LICENSE_AUDIT.md`.
2. Answer explicitly: *does this ship, or does it only run on our box?* Training
   tools that produce restricted outputs count as shipping.
3. Run a transitive check — for Python, inspect the actual installed dist-info
   metadata, not the README. The INRIA rasterizer is usually pulled in as a git
   dependency, which no license scanner catches by default.
4. For anything yellow, write down the specific clause and why we believe it is
   OK. "Probably fine" is not a record.

---

## Attribution

Apache-2.0 and MIT both require carrying the license text and copyright notice
in distributed binaries. Both the Quest app and the web viewer need a
**third-party licenses screen**, generated from `LICENSE_AUDIT.md` rather than
hand-maintained. Meta's store review and Apple's review both look for this.
Cheap to do in P3; annoying to retrofit in P5.
