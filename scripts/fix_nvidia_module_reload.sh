#!/bin/bash
# Reload the NVIDIA kernel modules so the loaded version matches userspace
# (580.159.03 loaded vs 580.173.02 installed — the DKMS module for .173 is
# already built; it just was never loaded because the GPU was in use).
#
# MUST run as root. Stops gdm3 (kills the desktop session!), unloads the
# stale modules, loads the fresh ones, restarts gdm3. If rmmod fails it
# restarts gdm3 and exits 1 — in that case a normal reboot is the fix.
# scripts/bench_autopilot.sh (already running, detached) picks up the
# benchmark work automatically as soon as nvidia-smi works.

exec >> /home/jaeahn-jammy/3dasset/logs/gpu_fix.log 2>&1
echo "=== gpu fix start $(date)"

systemctl stop gdm3
sleep 5
systemctl stop nvidia-persistenced 2>/dev/null || true

rmmod nvidia_uvm nvidia_drm nvidia_modeset nvidia
if lsmod | grep -q '^nvidia '; then
    echo "!! rmmod failed — modules still held:"
    lsmod | grep nvidia
    fuser -v /dev/nvidia* 2>&1 | head -20
    echo "!! clean reboot required; restarting gdm3"
    systemctl start gdm3
    exit 1
fi

modprobe nvidia && modprobe nvidia_uvm && modprobe nvidia_modeset && modprobe nvidia_drm
nvidia-smi || echo "!! nvidia-smi still failing after reload"
systemctl start gdm3
echo "=== gpu fix end $(date)"
