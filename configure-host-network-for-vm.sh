#!/usr/bin/env bash
set -euo pipefail

BRIDGE="br0"
TAP="tap0"
IFACE="wlan0"

# Ensure bridge-utils/iproute2 are installed
command -v ip >/dev/null || { echo "ip command not found"; exit 1; }

echo "[INFO] Setting up TAP + bridge for QEMU"

# Create bridge if it doesn't exist
if ! ip link show "$BRIDGE" >/dev/null 2>&1; then
    echo "[INFO] Creating bridge $BRIDGE"
    sudo ip link add name "$BRIDGE" type bridge
    sudo ip link set dev "$BRIDGE" up
else
    echo "[INFO] Bridge $BRIDGE already exists"
fi

# Create tap device if it doesn't exist
if ! ip link show "$TAP" >/dev/null 2>&1; then
    echo "[INFO] Creating TAP device $TAP"
    sudo ip tuntap add dev "$TAP" mode tap user "$USER"
    sudo ip link set "$TAP" up
else
    echo "[INFO] TAP $TAP already exists"
fi

# Attach TAP to bridge
if ! ip link show "$TAP" | grep -q "master $BRIDGE"; then
    echo "[INFO] Attaching $TAP to $BRIDGE"
    sudo ip link set "$TAP" master "$BRIDGE"
else
    echo "[INFO] TAP $TAP already attached to $BRIDGE"
fi

# Attach Wi-Fi interface to bridge
# WARNING: many Wi-Fi drivers do not support bridging
if ! ip link show "$IFACE" | grep -q "master $BRIDGE"; then
    echo "[INFO] Attaching $IFACE to $BRIDGE (may fail on Wi-Fi)"
    sudo ip link set "$IFACE" master "$BRIDGE" || echo "[WARN] Bridging Wi-Fi may not work"
else
    echo "[INFO] $IFACE already attached to $BRIDGE"
fi

echo "[INFO] Bridge $BRIDGE with TAP $TAP ready for QEMU"
