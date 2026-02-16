#!/bin/sh
# NBD root mount hook for dracut
# Load dracut command line library
type getarg >/dev/null 2>&1 || . /lib/dracut-lib.sh

echo "NBD: Hook script starting..." >> /dev/kmsg

# Parse nbdroot from kernel cmdline
nbdroot=$(getarg nbdroot=)

if [ -z "$nbdroot" ]; then
    # Also check /proc/cmdline directly as fallback
    for param in $(cat /proc/cmdline); do
        case "$param" in
            nbdroot=*)
                nbdroot="${param#nbdroot=}"
                break
                ;;
        esac
    done
fi

if [ -z "$nbdroot" ]; then
    echo "NBD: No nbdroot parameter found in cmdline" >> /dev/kmsg
    exit 0
fi

server="${nbdroot%:*}"
port="${nbdroot##*:}"

echo "NBD: Connecting to $server:$port" >> /dev/kmsg

# Load NBD module with increased device count
echo "NBD: Loading nbd kernel module..." >> /dev/kmsg
modprobe nbd nbds_max=16 max_part=16 || modprobe nbd
sleep 1

# Check if module loaded
if ! lsmod | grep -q nbd; then
    echo "NBD: ERROR - nbd module failed to load!" >> /dev/kmsg
    exit 1
fi

echo "NBD: nbd module loaded" >> /dev/kmsg

# Create device nodes manually if they dont exist
if [ ! -b /dev/nbd0 ]; then
    echo "NBD: Creating /dev/nbd0 device node..." >> /dev/kmsg
    mknod /dev/nbd0 b 43 0
    chmod 660 /dev/nbd0
fi

# Verify device node exists
if [ -b /dev/nbd0 ]; then
    echo "NBD: /dev/nbd0 device node exists" >> /dev/kmsg
    ls -l /dev/nbd0 >> /dev/kmsg 2>&1
else
    echo "NBD: ERROR - /dev/nbd0 still does not exist!" >> /dev/kmsg
    exit 1
fi

# Wait for network to be up
echo "NBD: Waiting for network..." >> /dev/kmsg
i=0
while [ $i -lt 60 ]; do
    if ip addr show | grep -q "inet.*scope global"; then
        echo "NBD: Network is configured" >> /dev/kmsg
        ip addr show >> /dev/kmsg 2>&1
        break
    fi
    sleep 1
    i=$((i + 1))
done

if ! ip route | grep -q default; then
    echo "NBD: WARNING - No default route yet" >> /dev/kmsg
    ip route >> /dev/kmsg 2>&1
fi

# Test connectivity to server
echo "NBD: Testing connectivity to $server..." >> /dev/kmsg
if ping -c 1 -W 3 "$server" >> /dev/kmsg 2>&1; then
    echo "NBD: Server $server is reachable" >> /dev/kmsg
else
    echo "NBD: WARNING - Cannot ping server $server" >> /dev/kmsg
fi

# Use nbd-client to connect
echo "NBD: Executing: nbd-client $server $port /dev/nbd0 -persist -name export" >> /dev/kmsg

if command -v nbd-client >/dev/null 2>&1; then
    # Try with -name export first (newer protocol)
    nbd-client "$server" "$port" /dev/nbd0 -persist -name export >> /dev/kmsg 2>&1
    NBD_RESULT=$?

    # If that failed, try without -name
    if [ $NBD_RESULT -ne 0 ]; then
        echo "NBD: Retry without -name parameter..." >> /dev/kmsg
        nbd-client "$server" "$port" /dev/nbd0 -persist >> /dev/kmsg 2>&1
        NBD_RESULT=$?
    fi

    if [ $NBD_RESULT -eq 0 ]; then
        echo "NBD: nbd-client connection successful" >> /dev/kmsg
    else
        echo "NBD: ERROR - nbd-client failed with code $NBD_RESULT" >> /dev/kmsg
    fi
else
    echo "NBD: ERROR - nbd-client command not found!" >> /dev/kmsg
    exit 1
fi

# Wait a moment for device to be ready
sleep 3

# Verify device is connected and readable
if [ -b /dev/nbd0 ]; then
    echo "NBD: Checking if /dev/nbd0 is readable..." >> /dev/kmsg
    if dd if=/dev/nbd0 of=/dev/null bs=512 count=1 2>>/dev/kmsg; then
        echo "NBD: SUCCESS - /dev/nbd0 is readable!" >> /dev/kmsg
    else
        echo "NBD: ERROR - /dev/nbd0 exists but is not readable" >> /dev/kmsg
        cat /sys/block/nbd0/pid >> /dev/kmsg 2>&1 || echo "NBD: No PID file" >> /dev/kmsg
    fi

    # Show device info
    blockdev --getsize64 /dev/nbd0 >> /dev/kmsg 2>&1 || true
else
    echo "NBD: ERROR - /dev/nbd0 disappeared!" >> /dev/kmsg
fi

echo "NBD: Mount hook complete" >> /dev/kmsg


