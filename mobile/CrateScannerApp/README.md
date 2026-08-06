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

The app opens on a three-step **intro** the first time it runs (reopen it any
time with the **?** button on the scan screen). Then:

1. Pick a **capture mode** — one tap on a card:
   | Mode | What it does |
   |------|--------------|
   | **Video** | continuous frame stream while you walk (was called "Auto") |
   | **Photo** | one 12 MP still per shutter tap |
   | **Detail** | Video + automatic 12 MP stills — best quality |
2. **Start Scan**, then walk a slow lap around the object. Watch the frame count
   climb in the banner.
   - **Stand still and a yellow arrow appears in AR**, pointing the way to keep
     orbiting, with the same instruction in a banner. Following it is what gives
     fusion the parallax it needs — a stationary scan yields no new geometry.
3. **Finish Scan**. (Measure → Fit is optional, only for crate dimensions; with
   a box placed, tap the floor to move it.)
4. Review screen: auto-uploads to the worker / Drive if configured, or
   **Share full package (mesh + RGB-D)** → AirDrop to Mac → `scp` to Linux.

Everything is a tap — there are no sliders or segmented controls to drag, and
the only gesture on the camera view is a single tap.

### App icon

The icon lives in `CrateScanner/Assets.xcassets/AppIcon.appiconset` and is
generated from source, so tweaking the artwork is a code edit:

```bash
cd mobile/CrateScannerApp
swift tools/make_app_icon.swift \
    CrateScanner/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png
```

`View/CubeMark.swift` draws the same cube as vectors for the intro screen; keep
the two in step if you change one.

### Google Drive destination

Finished scans upload into a **`CrateScans`** folder that the app creates and
owns, signed in as **jayahn@berkeley.edu**. The values live in
`Support/GoogleDriveConfig.swift` (`folderName`, `accountHint`, `folderID`).

Nothing to set up if you were already connected — the scope is unchanged, so the
existing sign-in keeps working. The review screen labels the connected account
and warns (pausing auto-sync) if it's ever someone else.

**Pointing this at a different account** means adding that address under **APIs &
Services → OAuth consent screen → Test users** in the Cloud project that owns
the client ID. Miss that and sign-in returns `Error 403: access_denied`, which
looks like a broken client but is only the tester list. Also check the consent
screen's User type: **Internal** rejects accounts outside the org outright.

**Pointing this at an existing shared folder** takes two changes together, and
they only work as a pair:

```swift
static let folderID = "…"                                  // from the folder URL
static let scope = "https://www.googleapis.com/auth/drive"  // must widen too
```

`drive.file` — the current, narrow scope — only grants access to files the app
itself created, so it cannot write into a folder made by someone else; uploads
fail with "File not found" however correct the id is. The wide scope fixes that
but is *restricted*, so the consent screen must stay in Testing. When the scope
changes, the app notices the stored grant no longer matches and shows **"Drive
permission changed — sign in again"** rather than failing mid-upload.

On Linux the puller is `rclone copy gdrive:CrateScans …`, with that `gdrive:`
remote authorised for the same account.

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
