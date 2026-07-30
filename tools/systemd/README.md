# Hands-off ingestion on the Linux box

Two user services (+ optional 3DGUT queue). Together they mean: a scan that lands
in Google Drive is fused (and, when routed as furniture/scene, 3DGUT-trained)
without anyone typing a command.

```
Drive ──rgbd-drive-sync.timer (every 2 min)──┐
                                             ├─▶ captures/inbox ─▶ rgbd-watcher ─▶ demo_out/
iPad ──POST /rgbd/upload────────────────────-┘         │
                                                       └─▶ rgbd-3dgut-queue ─▶ *_3dgut + RESULT
```

## Install

```bash
mkdir -p ~/.config/systemd/user
cp ~/3dasset/tools/systemd/*.service ~/3dasset/tools/systemd/*.timer \
   ~/.config/systemd/user/

# edit the two paths/remote names if yours differ
systemctl --user daemon-reload
systemctl --user enable --now rgbd-watcher.service
systemctl --user enable --now rgbd-drive-sync.timer
systemctl --user enable --now rgbd-3dgut-queue.service   # optional GPU train

# survive logout (otherwise user services stop when you disconnect)
sudo loginctl enable-linger $USER
```

## Watch it work

```bash
systemctl --user status rgbd-watcher rgbd-3dgut-queue
journalctl --user -u rgbd-watcher -u rgbd-3dgut-queue -f
systemctl --user list-timers rgbd-drive-sync
ls ~/3dasset/captures/{inbox,done,failed}
cat ~/3dasset/captures/status/CrateScan-*.json
cat ~/3dasset/captures/status/.3dgut_done.json
```

## One-time rclone setup

```bash
rclone config          # create a remote named "gdrive" (Google Drive)
rclone lsd gdrive:     # verify
```

The sync pulls **from** `gdrive:CrateScans/Packages` — the folder the iPad app
writes into. `--drive-shared-with-me` is there so a folder someone shared with
you also works; drop it if you own the folder.

## Not using Drive?

If you run the Tailscale route only, enable just `rgbd-watcher` and skip the
timer — `POST /rgbd/upload` writes into the same inbox.
