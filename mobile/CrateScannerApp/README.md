# CrateScanner (packaged for assetpipe)

LiDAR scan app for **iPad Pro / iPhone Pro** → zip for Linux `assetpipe`.

This folder is the full app sources, already wired for:

1. Live ARKit LiDAR mesh + dimensions (original MVP)
2. **RGB-D session recording** (`SessionExporter`) while you scan
3. **Share full package** → `mesh.obj` + `measurement.json` + `session/` for Linux

Upstream origin: [NathanJim17/E170_Client_Project](https://github.com/NathanJim17/E170_Client_Project)

---

## Mac — open & run on iPad

The `.xcodeproj` is **generated from [`project.yml`](project.yml)** and is
gitignored. This is deliberate: the spec globs the `CrateScanner/` folder, so
Swift files that arrive in a `git pull` join the app target automatically. A
hand-maintained `.pbxproj` does not — new files land on disk and then fail to
build with `Cannot find 'SessionExporter' in scope`.

```bash
brew install xcodegen          # once

cd ~/3dasset/mobile/CrateScannerApp
git pull
xcodegen generate             # after EVERY pull that touches CrateScanner/
open CrateScanner.xcodeproj
```

Then in Xcode: **Signing & Capabilities → Team =** your Apple ID (this is not
committed, so re-select it after a regenerate — or fill in `DEVELOPMENT_TEAM`
in `project.yml`, which is your repo). Destination = **iPad**, then **⌘R**.

Verified: `xcodebuild -scheme CrateScanner` → **BUILD SUCCEEDED**, all 17
sources in the target.

> Simulator builds and runs, but has no LiDAR — `LiDARAvailability.isSupported`
> is false there, so you get `UnsupportedDeviceView`. Real scanning needs the
> device.

### Using the E170_Client_Project Xcode project instead

If you build from [NathanJim17/E170_Client_Project](https://github.com/NathanJim17/E170_Client_Project),
that repo owns its own `.pbxproj` — copy these files in and check target
membership by hand:

| File | Role |
|------|------|
| `Support/SessionExporter.swift` | **NEW** — must be added to the target |
| `Support/PackageExporter.swift` | **NEW** — must be added to the target |
| `ViewModel/ScanViewModel.swift` | replace — wired recorder + package export |
| `View/ResultReviewView.swift` | replace — share package / session buttons |
| `View/ScanView.swift` | replace — shows RGB-D frame count |

The two **NEW** files are the ones that silently go missing: replacing existing
files works over a plain `git pull`, but new paths are absent from that repo's
`.pbxproj` until you drag them in.

---

## iPad — capture & share

1. Slow orbit around the object (watch **RGB-D** count climb in the banner)
2. Place Box → Fit to Object → **Capture**
3. Review screen:
   - **Share full package (mesh + RGB-D)** ← use this
   - or Share RGB-D session only / STL/OBJ/USDZ
4. AirDrop zip to Mac → `scp` to Linux

Package layout:

```text
CrateScan-<id>.zip
  README_LINUX.txt
  measurement.json
  mesh.obj
  session/
    manifest.json
    images/*.jpg          # color frames (manifest key stays "color")
    depth/*.png
```

---

## Linux — fuse

```bash
cd ~/3dasset/captures
unzip -o ~/Downloads/CrateScan-XXXXXXXX.zip -d scan1

conda activate assetpipe
cd ~/3dasset

# Path A — mesh + dims
python -m assetpipe cratescan ~/3dasset/captures/scan1 --out demo_out/ipad1

# Path B — RGB-D (nvblox on Linux; auto → Open3D only if nvblox fails to load)
python -m assetpipe rgbd ~/3dasset/captures/scan1/session --inspect-only
python -m assetpipe rgbd ~/3dasset/captures/scan1/session --backend auto \
  --out demo_out/ipad1_rgbd
```

---

## Sync via git (Mac)

From the **3dasset** repo (this package lives at `mobile/CrateScannerApp/`):

```bash
cd ~/3dasset
git pull
cd mobile/CrateScannerApp && xcodegen generate && open CrateScanner.xcodeproj
```

Do not commit scan zips, or the generated `CrateScanner.xcodeproj`.
