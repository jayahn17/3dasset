# Dual-machine playbook — Mac (iPad capture) + Linux (nvblox fuse)

You build/run the **Swift app on Mac → iPad**.  
You run **assetpipe / nvblox on this Linux box**.  
**Git keeps code in sync** — do **not** commit scan RGB-D (too big); transfer sessions with `scp`/AirDrop.

> Your iPhone is broken → use an **iPad Pro with LiDAR** (2020+). Same ARKit APIs.

---

## 0. What you need

| Machine | Role |
|---------|------|
| **Mac** | Xcode 15+, Apple ID, build CrateScanner to iPad |
| **iPad Pro (LiDAR)** | Capture only — USB to Mac for install |
| **Linux (this 4080 box)** | `assetpipe rgbd` → **nvblox** (Open3D TSDF only if nvblox import fails) |
| **GitHub** | `jayahn17/3dasset` + `NathanJim17/E170_Client_Project` |

---

## 1. Git setup (both machines)

### Linux (already have `~/3dasset`)

```bash
cd ~/3dasset
git status
git pull origin claude/3d-asset-recording-quest-e1jvtx   # or your working branch
# Keep local uncommitted work; commit when ready (see §6)
```

Key paths on Linux after pull:

- `docs/CRATESCANNER_BRIDGE.md`
- `docs/NVBLOX_WORKFLOW.md`
- `mobile/CrateScanner/SessionExporter.swift` ← copy into iOS app
- `python -m assetpipe rgbd` / `cratescan`

### Mac

```bash
# Code for the server pipeline (docs + SessionExporter)
git clone https://github.com/jayahn17/3dasset.git
cd 3dasset
git checkout claude/3d-asset-recording-quest-e1jvtx   # match Linux branch
git pull

# The iOS app
git clone https://github.com/NathanJim17/E170_Client_Project.git
cd E170_Client_Project
```

**Live sync habit (both sides):**

```bash
# Before work
git pull

# After you change code on either machine
git add -A
git commit -m "Describe change"
git push
# Other machine:
git pull
```

Never `git add` a full `session/` with hundreds of JPG/PNG depth frames. Put scans under a local folder ignored by git (see §5).

---

## 2. Mac — open CrateScanner in Xcode

1. On Mac: open **Xcode**.
2. **File → Open** → `E170_Client_Project` (or create an iOS App target and drag `CrateScanner/` in — follow that repo’s README).
3. Signing:
   - Select the **CrateScanner** target → **Signing & Capabilities**
   - Team = your **Personal Team** (free Apple ID is OK for device testing)
   - Bundle ID unique, e.g. `com.yourname.CrateScanner`
4. Deployment target **iOS 17+**.
5. Plug in **iPad** with cable → Trust computer → select iPad as run destination.
6. Enable **Developer Mode** on iPad if prompted (Settings → Privacy & Security).

### Confirm LiDAR

App already gates on LiDAR (`LiDARAvailability`). If you see “unsupported device”, that iPad has no LiDAR — you need iPad Pro (2020+) / LiDAR model.

---

## 3. Mac — add RGB-D session export (Path B)

### 3a. Copy Swift file

From the `3dasset` clone on Mac:

```bash
cp ~/3dasset/mobile/CrateScanner/SessionExporter.swift \
   ~/E170_Client_Project/CrateScanner/Support/
```

In Xcode: confirm `SessionExporter.swift` is in the app target (checkmark in File Inspector).

### 3b. Wire into `ScanViewModel.swift`

Add property:

```swift
private var sessionExporter: SessionExporter?
```

In `runSession(resetting:)` after `session.run(...)`:

```swift
if resetting || sessionExporter == nil {
    sessionExporter = try? SessionExporter(minInterval: 0.25)  // ~4 Hz
}
```

In `session(_:didUpdate frame:)` (the existing feedback method), also:

```swift
Task { @MainActor in
    self.sessionExporter?.maybeRecord(frame)
}
```

In `capture()`, after you freeze the mesh, finalize session:

```swift
if let url = try? sessionExporter?.finalize(
    objectHint: "machine",
    location: "shop"
) {
    // Keep path for share — e.g. store on viewModel
    self.lastSessionURL = url
}
```

Add:

```swift
@Published private(set) var lastSessionURL: URL?
```

### 3c. Share session from review UI

In `ResultReviewView`, add a button **“Share RGB-D session”** that zips `lastSessionURL` and opens `ShareSheet`.

Quick zip helper:

```swift
func zipSession(_ dir: URL) throws -> URL {
    let zipURL = dir.appendingPathExtension("zip")
    try? FileManager.default.removeItem(at: zipURL)
    let coordinator = NSFileCoordinator()
    var err: NSError?
    coordinator.coordinate(readingItemAt: dir, options: .forUploading, error: &err) { tmp in
        try? FileManager.default.copyItem(at: tmp, to: zipURL)
    }
    if let err { throw err }
    return zipURL
}
```

### 3d. Still export OBJ (Path A — dims)

Keep existing **Export OBJ** — useful for `assetpipe cratescan` even before session fuse works.

---

## 4. iPad — how to capture

1. Mac: ⌘R → app installs on iPad.
2. Grant **Camera**.
3. Scan flow (existing app):
   - Walk a **slow 360°** around the machine (60–120 s worth of motion).
   - Place ghost box → Fit → Capture.
4. Export:
   - **Share RGB-D session** (zip) → Files / AirDrop to Mac  
   - **and/or** Export OBJ + save `measurement.json` if you add that share.
5. Optional: put a **tape measure** in view for later check (doesn’t auto-scale ARKit; validates inches).

**Tips:** good light, slow moves, avoid pure chrome/black; stand back enough that LiDAR fills the mesh.

---

## 5. Move data Mac → Linux (not via git)

On Mac, after AirDrop/Files has `session-XXXXXXXX.zip`:

```bash
# From Mac (replace USER/HOST with this Linux box)
scp ~/Downloads/session-XXXXXXXX.zip USER@LINUX_IP:~/3dasset/captures/
```

On Linux:

```bash
mkdir -p ~/3dasset/captures
cd ~/3dasset/captures
unzip -o session-XXXXXXXX.zip -d scan1
# Expect: scan1/manifest.json  scan1/color/  scan1/depth/
```

Add to `~/3dasset/.gitignore` if needed:

```
captures/
demo_out/
```

### “Live” check loop (recommended)

Keep a terminal open on **both** machines:

**Mac:** after each scan, one `scp` line.  
**Linux:**

```bash
watch -n 2 'ls -la ~/3dasset/captures'
```

Or use Cursor Remote SSH: edit/run Linux from Mac in one window while Xcode runs locally.

True **frame-by-frame livestream** to nvblox is optional later (HTTP upload to `capture_worker`). For now: **scan → zip → scp → fuse** is the reliable loop.

---

## 6. Linux — fuse (this box)

```bash
conda activate assetpipe
cd ~/3dasset

# Gate — must print ok_for_nvblox: true
python -m assetpipe rgbd ~/3dasset/captures/scan1 --inspect-only

# Fuse — nvblox preferred (auto). Open3D only if nvblox_torch ABI broken on this box.
python -m assetpipe rgbd ~/3dasset/captures/scan1 \
  --backend auto \
  --out ~/3dasset/demo_out/ipad_scan1
# Force nvblox when the wheel loads:
#   --backend nvblox
# Temporary fallback only:
#   --backend open3d
```

Open `demo_out/ipad_scan1/scan_view.html` in a browser (or copy HTML+PLY back to Mac).

### If you only have OBJ from the app (no session yet)

```bash
# folder with mesh.obj + measurement.json
python -m assetpipe cratescan ~/3dasset/captures/obj_export \
  --out ~/3dasset/demo_out/ipad_crate1
```

---

## 7. What success looks like

| Check | Pass |
|-------|------|
| iPad runs app | LiDAR mesh draws live |
| `inspect-only` | `"ok_for_nvblox": true`, depth ≈ color count |
| Fuse | `scene.ply` / `scene_tsdf_mesh.ply` not empty |
| Dims | Within ~0.25–0.5″ of tape on a known edge |

| Fail | Fix |
|------|-----|
| `ok_for_nvblox: false` | SessionExporter not wired / no sceneDepth |
| Empty mesh | Too fast / dark / no Fit box coverage |
| scp fails | Same Wi‑Fi, SSH enabled on Linux, correct IP |

---

## 8. Suggested order today

1. **Mac:** clone both repos, open CrateScanner, run on **iPad** (mesh-only Path A first).  
2. **Mac:** AirDrop OBJ → **Linux** `cratescan` (proves Mac↔Linux transfer).  
3. **Mac:** add `SessionExporter`, rebuild, Capture + share zip.  
4. **Linux:** `rgbd --inspect-only` then `--backend auto` (nvblox if available).  
5. **Git:** commit Swift wiring on `E170_Client_Project`; commit docs/CLI on `3dasset`; push both; pull on the other machine.

---

## 9. Repo ownership cheat sheet

| Change | Repo |
|--------|------|
| SessionExporter wiring, share UI | `E170_Client_Project` |
| `assetpipe rgbd` / `cratescan` / docs | `3dasset` |
| Scan zips / PLYs | **local only** (`captures/`, `demo_out/`) |

---

## 10. Optional later

- POST zip to Linux `services/capture_worker.py` (no manual scp)
- Fix `nvblox_torch` ABI → `--backend nvblox`
- OpenMVS texture + TRELLIS underside
