# Mac ↔ Linux workflow — iPad RGB-D → nvblox

**This is the only capture path we use going forward.**

Do **not** use old Camera HEICs, gym photo folders, plain video, or COLMAP-only dumps.
Capture is **iPad Pro (LiDAR) + CrateScanner (Xcode)** → RGB-D session → **Linux nvblox**.

```text
iPad CrateScanner (Xcode)
  LiDAR depth + RGB + pose + K
        │  Share full package zip
        ▼
Mac (AirDrop / Files)
        │  scp / shared folder
        ▼
Linux 4080
  python -m assetpipe rgbd session/ --backend nvblox
        ▼
  mesh / ply / glb
```

---

## Repos

| Repo | Role |
|------|------|
| [jayahn17/3dasset](https://github.com/jayahn17/3dasset) | Linux pipeline + packaged iOS sources |
| Branch | `claude/3d-asset-recording-quest-e1jvtx` |

App sources in this repo: [`mobile/CrateScannerApp/`](mobile/CrateScannerApp/)

---

## Every session (Mac)

```bash
cd ~/3dasset
git pull origin claude/3d-asset-recording-quest-e1jvtx
```

After you change Swift/docs on Mac:

```bash
git add mobile/ docs/
git commit -m "Describe change"
git push
```

On Linux: `git pull` before fusing.

**Never commit** scan zips or `captures/` / `demo_out/` (gitignored).

---

## Mac — build & run on iPad

1. Open Xcode project that contains `CrateScanner/` from `mobile/CrateScannerApp/CrateScanner/`
   - First time: see [`mobile/CrateScannerApp/README.md`](mobile/CrateScannerApp/README.md)
   - Or merge updated Swift files into your existing project, then ⌘R
2. Destination = **iPad** (LiDAR), not Simulator
3. ⌘R → grant Camera

Key files (must be in the app target):

- `Support/SessionExporter.swift` — records RGB-D
- `Support/PackageExporter.swift` — builds Linux zip
- `ViewModel/ScanViewModel.swift` — wired recorder
- `View/ResultReviewView.swift` — **Share full package**

---

## iPad — capture

1. Slow orbit; banner should show **RGB-D** frame count rising  
2. Place Box → Fit → **Capture**  
3. Tap **Share full package (mesh + RGB-D)**  
4. AirDrop / Save to Files on Mac  

Zip contains:

```text
measurement.json
mesh.obj
session/
  manifest.json
  color/*.jpg
  depth/*.png          # LiDAR, mm
README_LINUX.txt
```

---

## Mac → Linux transfer

```bash
# On Mac (set USER + LINUX_IP)
scp ~/Downloads/CrateScan-XXXXXXXX.zip USER@LINUX_IP:~/3dasset/captures/
```

Keep a second terminal on Linux:

```bash
watch -n 2 'ls -la ~/3dasset/captures'
```

Or use Cursor Remote SSH into Linux from the Mac.

---

## Linux — nvblox fuse

```bash
conda activate assetpipe
cd ~/3dasset
mkdir -p captures
unzip -o captures/CrateScan-XXXXXXXX.zip -d captures/scan1

# Must be true
python -m assetpipe rgbd captures/scan1/session --inspect-only

# Preferred
python -m assetpipe rgbd captures/scan1/session --backend nvblox \
  --out demo_out/ipad_scan1

# If nvblox_torch fails to import on this box (ABI), temporary only:
python -m assetpipe rgbd captures/scan1/session --backend open3d \
  --out demo_out/ipad_scan1
```

Optional (on-device mesh dims, not the nvblox path):

```bash
python -m assetpipe cratescan captures/scan1 --out demo_out/ipad_crate1
```

---

## What we are *not* doing

| Skip | Why |
|------|-----|
| `images/Chest`, HEIC albums | RGB-only, no LiDAR poses |
| Camera.app / Quest passthrough MP4 | No depth+pose session |
| COLMAP as primary for this product | Guessed geometry; no metric depth |
| Open3D as the product fuse | Fallback only if nvblox won’t load |

---

## Related docs

- [`mobile/CrateScannerApp/README.md`](mobile/CrateScannerApp/README.md) — Xcode package details  
- [`docs/CRATESCANNER_BRIDGE.md`](docs/CRATESCANNER_BRIDGE.md) — app ↔ assetpipe  
- [`docs/NVBLOX_WORKFLOW.md`](docs/NVBLOX_WORKFLOW.md) — server fuse details  
- [`docs/DUAL_MACHINE_PLAYBOOK.md`](docs/DUAL_MACHINE_PLAYBOOK.md) — longer dual-machine notes  

---

## Checklist before each Linux fuse

- [ ] Mac `git pull` / app rebuilt with SessionExporter  
- [ ] iPad RGB-D count &gt; 0 during scan  
- [ ] Zip has `session/manifest.json` + `color/` + `depth/`  
- [ ] `rgbd --inspect-only` → `ok_for_nvblox: true`  
- [ ] Fuse with `--backend nvblox` (or `auto`)
