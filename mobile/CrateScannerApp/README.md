# CrateScanner (packaged for assetpipe)

LiDAR scan app for **iPad Pro / iPhone Pro** → zip for Linux `assetpipe`.

This folder is the full app sources, already wired for:

1. Live ARKit LiDAR mesh + dimensions (original MVP)
2. **RGB-D session recording** (`SessionExporter`) while you scan
3. **Share full package** → `mesh.obj` + `measurement.json` + `session/` for Linux

Upstream origin: [NathanJim17/E170_Client_Project](https://github.com/NathanJim17/E170_Client_Project)

---

## Mac — open & run on iPad

### First time (if you don’t have an `.xcodeproj` yet)

1. Xcode → **File → New → Project → iOS → App**
2. Product Name: `CrateScanner`, Interface: **SwiftUI**, Language: **Swift**
3. Delete template `ContentView.swift` / `*App.swift`
4. Drag the entire `CrateScanner/` folder into the project (**Copy items if needed**, target membership checked)
5. Add **Privacy – Camera Usage Description** (or use included `Info.plist`)
6. Deployment **iOS 17+**, Team = your Apple ID
7. Destination = **iPad** (LiDAR) → **⌘R**

### If you already have a working Xcode project

Replace/merge these files from this package into your project (ensure target membership):

| File | Role |
|------|------|
| `Support/SessionExporter.swift` | **NEW** — RGB-D frames |
| `Support/PackageExporter.swift` | **NEW** — Linux zip |
| `ViewModel/ScanViewModel.swift` | Wired recorder + package export |
| `View/ResultReviewView.swift` | Share package / session buttons |
| `View/ScanView.swift` | Shows RGB-D frame count |

Then **⌘R** on iPad.

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
    color/*.jpg
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
# open mobile/CrateScannerApp/CrateScanner/ in your Xcode project
```

Do not commit scan zips.
