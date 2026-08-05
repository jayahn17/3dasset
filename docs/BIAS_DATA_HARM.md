# Bias · Data · Harm — a responsibility review of assetpipe / CrateScanner

Scope: the shipping loop in this repo — iPad LiDAR capture (`mobile/CrateScannerApp/`)
→ Google Drive sync → Linux fuse/curate (`assetpipe rgbd`, `assetpipe/scene/`)
→ detection (`assetpipe/detect/`) → generative completion (TRELLIS / 3DGUT)
→ metric export (`dims_mm.json`, `asset_trellis_mm.stl`, URDF) → twin catalog / `son`.

The point of writing this down is that none of it is currently written down. A grep
across the repo for `consent|privacy|retention|PII|anonymize|redact|blur (faces)`
returns nothing. Every answer below currently lives only in someone's head.

---

## 1. Bias — who might it get wrong, or leave out?

### The sensor is biased before any model runs

iPad LiDAR returns nothing useful on three whole material classes: **transparent**
(glass, bottles, acrylic), **black matte** (speakers, camera bodies, dark fabric),
and **specular** (chrome, polished steel, mirrors). Those surfaces produce depth
holes. The cleanup path then treats holes-adjacent noise as clutter — `--clean`
runs outlier rejection → RANSAC ground plane → cluster stripping
(`assetpipe/scene/splat.py`) — so the pipeline's response to "I couldn't see this"
is quietly indistinguishable from "this wasn't part of the object."

Lighting compounds it. `assetpipe/scene/rgbd_curate.py` scores every frame on
sharpness, brightness, contrast and rejects the bad ones; `discover.grade()` gates
tracks and returns `"blurry"`. A dim apartment produces more rejected frames, fewer
keyframes, a worse asset — and the message the user gets is "your scan was bad,"
not "this system needs studio light."

The repo's whole tuning history points one way: it's called **CrateScanner**, the
zero-dep reconstructor is `procedural_box.py`, the smoke test is a box test, and the
canonical demo classes are `"cardboard box,mug"`. Matte, opaque, convex, beige,
right-angled. That's the object this works best on, and everything else is downhill
from it in a direction nobody has measured.

### The priors are biased

The README states the value proposition honestly: photogrammetry "cannot invent the
underside it never scanned. A generative image-to-3D model can." Everything the
generator invents comes from an Objaverse-scale prior that skews Western, consumer,
e-commerce, English-labeled. Scan a rice cooker, a molcajete, a tiffin carrier, a
prayer mat, a mobility aid, an insulin pump, a hand-built lab fixture — the model
completes it toward the nearest thing it has seen a thousand of. The failure is not
a crash; it's a confident, watertight, plausible-looking wrong object.

And the wrongness is laundered into authority downstream: the queue scales the GLB
by RGB-D `dims.json` and emits `asset_trellis_mm.stl` for Onshape import. A
hallucinated face arrives in CAD wearing the same units and the same confidence as a
measured one.

### The vocabulary is biased

`discover.DEFAULT_CLASSES` is a specific person's desk:

> mouse, keyboard, monitor, laptop, phone, headphones, speaker, camera, remote,
> charger, cable, book, notebook, pen, cup, mug, bottle, can, box, bag, backpack,
> shoe, hat, clothes, chair, lamp, plant, picture frame, clock, toy, tool, plate,
> bowl, scissors, stapler, wallet, keys, glasses, watch …

No walker, no cane, no medication, no religious object, no non-Western kitchenware,
no childcare equipment. YOLO-World is open-vocab so this is "only a default" — but
defaults are what almost everyone runs, and its text encoder is English, so a user
who types the word they actually use for the object ("banchan container") gets low
confidence or silence.

### Access is biased

Using this end-to-end requires an iPad Pro with LiDAR, a Mac with Xcode to build an
app that isn't on the App Store, and an RTX 4080 Linux box. That's a multi-thousand
dollar rig plus a developer account. The excluded group isn't an edge case — it's
most people, and it correlates with exactly the populations whose objects are least
represented in the priors above.

**Fix:**

1. **Build a hard-case eval set and publish per-category numbers.** ~20 objects
   spanning glass / black matte / chrome / thin-wire / fabric / non-Western
   household / assistive devices, each scanned in a bright room and a dim one. Run
   it through `tools/bench_compare.py` and put the per-category pass rate in
   `docs/INDUSTRY_BENCHMARK.md`. A mean quality score over cardboard boxes will
   never surface any of this.
2. **Extend the honesty that already exists.** `discover.grade()` already reports
   "seen but not reconstructed" — extend it with `depth_coverage` and emit a
   per-asset `quality.json`, surfaced in the `view.html` HUD. Failing loudly on a
   glass bottle is correct behavior; shipping a hole-filled mesh silently is not.
3. **Mark generated geometry as generated.** Carry a per-vertex/per-face provenance
   flag through the TRELLIS path distinguishing measured from completed surface, and
   shade the invented region in the viewer. This is the single highest-leverage fix
   in the document — it also resolves half of §3.
4. **Open the vocabulary and log the misses.** Make open-vocab the default rather
   than `DEFAULT_CLASSES`, and log every prompt that returns zero detections so
   coverage gaps become data instead of silence.

---

## 2. Data — whose data, and are they okay with it?

### What is actually captured is not "an object"

A LiDAR walkaround records RGB frames, per-frame depth, and per-frame ARKit camera
pose (`SessionExporter.finalize` → `manifest.json`). That means the raw session
contains the inside of someone's home or lab: what's on the desk, mail, screens,
whiteboards, anyone who walks through frame — plus enough pose data to reconstruct a
**metric floorplan** of the space.

`--clean` removes background from the *output asset*. It does not remove anything
from the session zip. The room is still in the upload.

### Where it goes

iPad → `CrateScan-*.zip` → a **shared** Google Drive folder (`Engin170_sync`, whose
link is published in `docs/DRIVE_AUTOPILOT.md` in a public repo) → polled every 60s
by `tools/drive_rgbd_autopilot.py` onto a personal Linux box → `captures/inbox/` →
results pushed back to Drive via rclone → optionally POSTed to `son`'s
`/api/quest-asset-scan`. It is hands-off by design; that is the feature.

So: raw interior scans of several people's homes sit in one shared folder that every
current and former member can read, with no expiry, on a link that is in a public
document.

### Who consented

- **The device owner:** partially. `Info.plist` carries `NSCameraUsageDescription`
  — that's Apple's OS permission for camera access, not informed consent about
  uploading a room scan to a shared Drive.
- **Roommates, lab partners, family, anyone in frame:** not at all. They were never
  asked and there's no mechanism to ask them.
- **Whoever owns the object:** not asked. Scanning a product captures a
  manufacturer's design; scanning someone's belongings captures their belongings.
- **Upstream model data:** TRELLIS/Objaverse and YOLO-World weights carry their own
  licenses and their own unresolved consent questions. Assets generated through them
  may inherit terms that matter the moment anything here is commercialized.

**Fix:**

1. **Write `docs/DATA_POLICY.md`:** what is captured, where it lands, who can read
   it, how long it's kept, how to delete it. One page. It doesn't exist today.
2. **Make consent a capture step, not a checkbox.** The app should require a scan
   scope before export — *my own object* / *my own space* / *a shared space* — and
   refuse auto-upload for shared-space scans until the user confirms bystanders are
   absent or informed. `SessionExporter.finalize()` already writes `object_hint` and
   `location` into the manifest; add `consent_scope` next to them so the answer
   travels with the data.
3. **Minimize at the source.** The object path already computes the object AABB
   (`dims.json`). Crop frames to the object and drop non-keyframes *before*
   packaging the zip. Ship the crop, not the room. Full-room frames only when the
   user explicitly chooses room-sweep mode.
4. **Scrub before upload.** A face/screen detection pass that blurs people and
   displays on-device, before the zip leaves the iPad. There is no such pass
   anywhere in the pipeline right now.
5. **Fix the container.** Per-person Drive folders instead of one shared bucket, a
   membership review, and a retention job that deletes raw sessions once the
   `*_RESULT.zip` exists (30 days, say), keeping only derived assets. Take the
   folder link out of the public doc.

---

## 3. Harm — who could be hurt if it works? If it fails?

### If it works

The success case is the more dangerous one. What this builds is a cheap, hands-off
machine that converts any indoor space into metric 3D geometry plus an itemized
inventory of what's in it, and then *automatically syncs it off-device*.

- **Surveillance and physical safety.** A metric floorplan with door widths, room
  dimensions, valuables and their locations is a burglary-planning artifact and a
  stalking artifact. The autopilot design means the scan leaves the scanner's
  control without a human deciding it should.
- **Profiling.** "A digital twin of everything I own" is also means-testing
  evidence, insurance-adjustment evidence, landlord evidence, and ad-targeting
  material. The catalog schema is explicitly built to answer "what did I own and
  where was it?" — which is a valuable question to someone other than the owner.
- **Replication.** Metric STL export of arbitrary physical objects is a copying path
  for protected product designs and for keyed physical items.
- **Asymmetry.** It works best for whoever holds the hardware. If scanning becomes
  the way objects get catalogued in a lab, a warehouse, or a home, the people being
  scanned are systematically not the people holding the iPad.

### If it fails

- **Silent metric error is the top risk.** `dims_mm.json` and
  `asset_trellis_mm.stl` are consumed as CAD input in Onshape/Fusion
  (`docs/ONSHAPE.md`). No uncertainty band ships with those numbers. A bad scale
  factor or a TRELLIS-completed face means someone machines or prints a part that
  doesn't fit — or one that's load-bearing and shouldn't be trusted. This is the
  failure most likely to hurt a person physically.
- **Bad twins train bad robots.** URDF export produces sim assets with mass, inertia
  and collision geometry. Grasp planning against a hallucinated underside is a
  physical failure mode, not a rendering artifact.
- **Failure is not evenly distributed.** Per §1, it fails on dark, glossy,
  transparent objects and in dim rooms. The person whose scan silently fails is
  disproportionately the one with the less-photogenic object or the worse-lit
  apartment, and the system tells them "the scan didn't work."
- **It also fails open.** With no retention policy, captures that "fail" still
  persist indefinitely in a shared folder. Nothing is ever actually deleted.

**Fix:**

1. **Ship uncertainty, never a bare number.** Every `dims_mm.json` gets a tolerance
   derived from depth coverage and keyframe count; STL export warns loudly (or
   refuses) below a coverage threshold. In a CAD-input path, "no confidence value"
   should be treated as a bug.
2. **Never let generated surface enter a physics or CAD export unlabeled.** Reuse
   the provenance flag from §1 Fix 3: a TRELLIS-completed face must not silently
   become a URDF collision mesh.
3. **State the limit where someone will read it.** "Not validated for safety-
   critical, load-bearing, or medical use" in `docs/ONSHAPE.md` and in the exported
   `view.html` HUD — not buried in a README.
4. **Different defaults for spaces than for objects.** Room/space captures: no
   auto-upload, local-only by default, explicit per-scan opt-in to sync. The
   autopilot currently uploads whatever lands in the inbox with no human in the loop;
   add a review gate for anything flagged `consent_scope = shared`.
5. **Build the delete path.** `assetpipe purge <session>` plus a documented request
   route, so a person who was captured can actually be removed — from the inbox, the
   Drive folder, the catalog, and `son`.

---

## What I'd do first

Three changes carry most of the risk reduction:

1. **Provenance on generated geometry** (§1.3 / §3.2) — stops hallucinated surface
   from masquerading as measurement in CAD and in sim.
2. **Crop-and-scrub before upload** (§2.3 / §2.4) — the room stops leaving the
   device at all, which retires most of §3's surveillance surface at the source.
3. **A written data policy with retention and a delete path** (§2.1 / §3.5) — cheap,
   takes an afternoon, and it's the thing whose absence makes every other answer
   unverifiable.

The honest summary: this pipeline is already careful about *reconstruction* quality
— frames get scored, tracks get graded, low-confidence assets get reported as "seen
but not reconstructed." That same instinct has simply never been pointed at whose
room is in the frame, or at what happens when the confident-looking number is wrong.
