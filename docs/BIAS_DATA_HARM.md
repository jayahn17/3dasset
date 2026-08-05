# Bias · Data · Harm — iPad image collection → Linux

Scope: what `CrateScanner` records on the iPad and how it reaches the 4080.

```text
SessionExporter.record()        10 Hz, every frame the camera sees, uncropped
  images/*.jpg    640 px color          ← no crop, no scrub, no face check
  depth/*.png     256×192 mm depth
  keyframes/*.jpg 12 MP full-res        ← Detail mode
  manifest.json   per-frame ARKit pose  ← the walking path through the room
        │
        ▼  Documents/CrateScans/  (Files-app visible to anything on the iPad)
        │
        ├─ DestinationStore  → Drive via Files provider   (auto-save ON by default,
        │                                                  no per-file sync status)
        ├─ GoogleDriveSync   → drive.file OAuth + Keychain (autoSync toggle)
        └─ WorkerUploader    → POST http://100.x.y.z:8090/rgbd/upload
                                                          (plaintext, no auth)
```

**1. Bias — who might it get wrong, or leave out?**

Collection hard-refuses non-LiDAR devices (`LiDARAvailability` → `UnsupportedDeviceView`),
throws away frames that fail the focus/steadiness gate, and walks the user around
360° of AR target viewpoints — so a shaky hand, a wheelchair, a one-handed hold, a
dim room, or an object pushed against a wall produces a thinner session, and what
the 640 px / 256×192 sampling drops at capture can never be recovered on Linux.

*Fix:* Report per-session depth coverage and rejected-frame count on the review
screen instead of silently shipping a thin session, and test the capture gate against
tremor, one-handed, and seated-height walkarounds.

**2. Data — whose data, and are they okay with it?**

`record()` fires every 100 ms for the whole scan with no crop to the object the app
is already tracking, so a two-minute capture is ~1,200 photos of someone's room plus
a metric pose track of the path walked through it, sitting in a Files-readable folder
and auto-copying to Drive the moment a destination is picked — with no consent field
anywhere and `NSCameraUsageDescription` as the only thing resembling a disclosure.

*Fix:* Crop each recorded frame to the ghost-box AABB before it hits disk, run a
local Vision face/screen blur pass before `finalize()`, and make auto-save opt-in per
destination with a `consent_scope` recorded in `manifest.json`.

**3. Harm — who could be hurt if it works? If it fails?**

Working, it quietly produces and ships a metric walkthrough of a private space to a
third party; failing, `WorkerUploader` posts that walkthrough over plaintext HTTP to
an endpoint with no authentication — Tailscale is doing all the security and nothing
in the code enforces that a Tailscale address is what got typed — while
`DestinationStore` gives no per-file status, so the user can't tell what left the
iPad or whether it arrived.

*Fix:* Require HTTPS or a shared token on the worker endpoint and reject bare-LAN
URLs unless explicitly overridden, surface per-package delivery state in the UI, and
auto-purge `Sessions/` once a package is confirmed delivered.
