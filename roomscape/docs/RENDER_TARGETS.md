# Render targets and frame budgets

Everything upstream — gaussian count, SH degree, compression, LOD — is a
consequence of what a Quest 3 can rasterize in **13.8 ms**. This document holds
the budget and, more importantly, how we *measure* it, so the numbers below get
replaced with real ones from our own hardware in P0.

> The figures marked *(estimate)* are my priors, not measurements. The point of
> P0 is to overwrite them. Do not design against an estimate once a measurement
> exists.

---

## The hardware

**Quest 3** — Snapdragon XR2 Gen 2, 8 GB RAM shared between CPU and GPU,
2064×2208 per eye, 72 / 90 / 120 Hz. At 72 Hz the whole frame — both eyes,
sort, compositor, and everything else the app does — fits in 13.8 ms.

Three things make splats specifically hard on this chip:

1. **Sorting.** Correct alpha blending needs gaussians sorted back-to-front,
   per view, per frame. That is a GPU radix sort over ~1 M keys, every frame.
2. **Overdraw.** Splats are semi-transparent; a pixel may be touched dozens of
   times. Mobile GPUs are fill-rate bound and hate this.
3. **Memory.** 8 GB shared, and the OS wants a lot of it. Raw 3DGS with SH
   degree 3 is ~236 B/gaussian — 1 M gaussians is 236 MB of *live* data before
   any sort buffers. Compression is not an optimization; it is the entry fee.

---

## Budget table

| | Quest browser (WebXR) | Quest native | Desktop 4080 | iPad Pro (M-series) |
|---|---|---|---|---|
| Target rate | 72 Hz | 72 Hz (90 stretch) | 90+ Hz | 60 Hz |
| Frame budget | 13.8 ms | 13.8 ms | 11.1 ms | 16.6 ms |
| Gaussians *(estimate)* | 0.5–1.0 M | 1.5–2.5 M | 5–10 M | 1–2 M |
| SH degree | 0–1 | 1–2 | 3 | 1–2 |
| Resolution scale | 0.7–0.8 | 0.8–0.9 | 1.0 | 1.0 |
| Foveation | fixed, level 2–3 | fixed 2–3 (+ eye-tracked on Pro) | off | n/a |
| Scene bytes | ≤80 MB | ≤150 MB | unbounded | ≤80 MB |

**SH degree is the cheapest lever nobody uses.** Degree 3 is 45 of the 59
floats in a raw gaussian. Dropping to degree 1 costs a little view-dependent
sparkle on glossy surfaces and saves ~60% of the payload. For an indoor room —
mostly diffuse walls, floor, furniture — this is close to free. Bake the
decision per scene: measure PSNR at degree 3 / 1 / 0 during P2 and keep the
lowest that stays within ~0.5 dB.

---

## Techniques, in order of payoff

1. **Compress the payload** (~236 B → ~25 B/gaussian). Non-negotiable.
2. **Cull.** Frustum-cull chunked gaussians, and distance-cull with a per-chunk
   screen-space-error metric. A room viewed from inside has most of its
   gaussians behind the viewer.
3. **Sort once for both eyes.** Sorting per eye doubles the most expensive
   pass for a difference the user cannot see at IPD separation. Sort from a
   mid-eye viewpoint. Standard practice; verify no visible artifacts at close
   range.
4. **Sort incrementally.** Frame-to-frame the ordering barely changes. A
   partially-updated sort (or a coarse bucketed sort refined over frames) trades
   rare, subtle blending errors for a large win.
5. **LOD.** Chunk the scene spatially; load and render coarse levels for distant
   chunks. Also what makes time-to-first-pixel <3 s possible.
6. **Reduce overdraw.** Opacity-threshold pruning and scale clamping in the
   cleanup stage kill huge, near-transparent gaussians — they cost the most
   fill and contribute the least.
7. **Resolution scale + foveation.** Blunt but effective, and the last resort
   the runtime can apply dynamically when a frame budget is missed.

---

## How we measure (do this in P0, before opinions form)

Build a bench scene early and never change it — a single, real, mid-size room
from P1, plus decimated variants at 0.25 / 0.5 / 1 / 2 / 4 M gaussians. Then:

- **Quest browser:** Chrome/Edge remote devtools against the Quest browser for
  JS-side frame timing; `OVR Metrics Tool` (or the Meta Quest Developer Hub
  performance overlay) for real GPU/CPU frame time, stale-frame count, and
  thermal state. WebXR's `XRFrame` timing alone will lie to you about the
  compositor.
- **Quest native:** MQDH performance overlay, plus `ovrmetricstool` CSV export.
- **Thermals matter more than peak.** Run 10 minutes, not 30 seconds. A splat
  renderer that hits 72 Hz cold and 50 Hz warm is a broken product, and this is
  the single most common way mobile VR demos lie.

Record every run in `roomscape/docs/BENCH.md`: date, device, build, scene,
gaussian count, format, mean/p99 frame time, stale frames, thermal state at
minute 10. Ten lines per run. Without this file, the perf work becomes folklore.

**Definition of "passes":** p99 frame time < 13.8 ms and stale-frame rate < 1%
over a 10-minute session, measured at minute 10, not minute 1.

---

## Tier B — the local stream escape hatch

When on-device quality disappoints (it will, next to Meta's cloud renderer), we
have a path that does not require a datacenter: render the **uncompressed,
full-count** splat on the 4080 in a desktop OpenXR app and stream it to the
headset over Wi-Fi 6E via Air Link (or a Steam Link / ALVR-style path).

- Quality ceiling jumps to 5–10 M gaussians, SH degree 3, full resolution.
- Costs roughly a day of work once the desktop viewer from P0/P3 exists.
- Constraints: same house, a good 6 GHz AP, and ~30–40 ms added latency, which
  is fine for a walkthrough and not fine for anything twitchy.

This is the demo tier and, honestly, probably how *we* will look at our own
scenes day to day. It also serves as the reference image: when a Tier A scene
looks wrong, compare against Tier B to tell "the reconstruction is bad" from
"the mobile renderer is cutting corners."
