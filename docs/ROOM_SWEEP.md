# Room sweep — one video, every object

The per-object path (views → generator → GLB) always worked. What did not
scale was *producing the view stacks*: pick an object, shoot 12 photos, run it,
repeat fifty times. The room sweep replaces that with one walk-around.

    assetpipe room sweep.mp4 --location "office" --out room_out

    video ─▶ frames ─▶ detect + TRACK every instance ─▶ one view stack per object
                                                        │
                                for each track that passes the gate:
                                generate (TRELLIS) ─▶ model.glb ─▶ twin.db ─▶ viewer

Outputs land in `room_out/`: `sweep.json` (every track and its verdict),
`<asset_id>/model.glb` per reconstructed object, `twin.db`, and
`control_center.html`.

## The tracker is the trick

A detector alone gives you "a mouse" in frame 12 and "a mouse" in frame 13
with no idea they are the same mouse. A **tracker** gives every physical object
a persistent id, so its views accumulate into one folder and one asset. That is
the difference between 50 assets and 600 duplicates.

Two discovery backends, same output:

| backend | what it is | weights |
|---|---|---|
| `sam3` | SAM 3 promptable *concept* segmentation — detects, segments and tracks **all instances** of a text concept in one video pass. The right tool. | gated, see below |
| `track` | YOLO-World (open-vocab boxes) + our IoU tracker + SAM 2 for masks. | auto-download, works today |

`--backend auto` (the default) picks `sam3` when `sam3.pt` is present, else `track`.

> **Ultralytics' own tracker does not work here.** On 8.4.90, BoT-SORT/ByteTrack
> never attaches to `YOLOWorld` — `boxes.id` comes back `None` on every frame,
> stills *and* video, even with detections well above `new_track_thresh`. So
> `discover._IouTracker` does the association itself. At walking pace an object
> barely moves between frames, so IoU + a label hint is sufficient and fully
> predictable. SAM 3 does its own tracking and does not use it.

## Getting the SAM 3 weights (gated — you must do this)

`facebook/sam3` is `gated: manual` on Hugging Face; anonymous download returns
**401**. No token or permission on this machine can bypass it — Meta has to
approve you.

1. Sign in and request access at <https://huggingface.co/facebook/sam3>.
   Approval is a manual review.
2. Once approved, create a token at <https://huggingface.co/settings/tokens>.
3. Download `sam3.pt` (3.45 GB) into the repo root:

   ```bash
   pip install huggingface_hub
   hf download facebook/sam3 sam3.pt --local-dir .   # asks for the token
   ```

`--backend auto` then switches to SAM 3 with no other change.

## The quality gate

Not every track deserves an asset. A pen glimpsed in two blurry frames
generates a confident-looking blob, and a closet full of those is worse than an
empty one — so tracks are graded, and only `ok` ones reach the GPU:

| status | meaning |
|---|---|
| `ok` | reconstructed |
| `too_few_views` | seen in fewer than `--min-views` frames |
| `too_small` | never covered enough of the frame to carry detail |
| `blurry` | best view still fails the Laplacian sharpness floor |
| `generate_failed` | the generator errored; the batch carried on |

Everything is reported in `sweep.json`, including the rejects — "seen but not
reconstructed" is honest, and tells you exactly what to walk back and re-shoot.

## Two traps, both paid for already

**Discovery runs in its own process.** `torch.cuda.empty_cache()` is *not*
enough: it returns cached blocks to torch's allocator but cannot destroy the
CUDA context, which pins hundreds of MB of VRAM for the life of the process.
With the detector still holding that context, TRELLIS's first model load OOM'd
(`cudaMalloc` error 2) on a real run. Only process exit gives a CUDA context
back to the OS, so `room.py` forks discovery into a child that dies before the
generator is asked for a single byte. Never run discovery and generation
resident together on this box.

**Generated assets have no size.** A GLB from TRELLIS is normalised — it knows
its shape, not its scale. Dimensions are stored with `scale: "relative"` and
`assetpipe list` prints them as `rel`, not centimetres, because nobody measured
them. Metric scale needs a metric source: Quest 3 camera poses, or a COLMAP
scan with a known reference. That is the next phase, and it is also what lets
assets be *placed* in a room rather than piled in a catalog.

## What actually limits quality

**View diversity, not view count.** Three photos taken from nearly the same
spot are one viewpoint repeated, and the generator answers with a hull-shaped
blob. A real sweep — walking around the room at 2 fps — gives each object
frames spanning a wide arc, which is the regime this is built for. If assets
come out soft, the fix is almost always to re-shoot with more angles per
object, not to change a flag.
