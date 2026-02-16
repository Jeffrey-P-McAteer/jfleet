#!/bin/sh
# NBD connection script
# Usage: nbdroot <server> <port> <device>

SERVER="$1"
PORT="$2"
DEVICE="$3"

echo "NBD: Loading nbd module..."
modprobe nbd nbds_max=16 max_part=16 2>/dev/null || modprobe nbd

# Create device if needed
if [ ! -b "$DEVICE" ]; then
    echo "NBD: Creating $DEVICE..."
    mknod "$DEVICE" b 43 0 2>/dev/null || true
fi

# Wait for network
echo "NBD: Waiting for network..."
for i in 1 2 3 4 5 6 7 8 9 10; do
    if ip route | grep -q default && ip addr show | grep -q "inet.*scope global"; then
        echo "NBD: Network is ready"
        break
    fi
    sleep 1
done

# Test connectivity
if ping -c 1 -W 2 "$SERVER" >/dev/null 2>&1; then
    echo "NBD: Server $SERVER is reachable"
else
    echo "NBD: Warning - cannot ping $SERVER"
fi

# Connect with nbd-client
echo "NBD: Connecting $DEVICE to $SERVER:$PORT..."

# Try with -name export first
if nbd-client "$SERVER" "$PORT" "$DEVICE" -persist -name export 2>/dev/null; then
    echo "NBD: Connected with -name export"
elif nbd-client "$SERVER" "$PORT" "$DEVICE" -persist 2>/dev/null; then
    echo "NBD: Connected without -name"
else
    echo "NBD: Connection failed!"
    return 1
fi

# Verify device is readable
sleep 2
if [ -b "$DEVICE" ] && dd if="$DEVICE" of=/dev/null bs=512 count=1 2>/dev/null; then
    echo "NBD: $DEVICE is readable - SUCCESS"
    return 0
else
    echo "NBD: $DEVICE is not readable - FAILED"
    return 1
fi

