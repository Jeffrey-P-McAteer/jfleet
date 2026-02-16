#!/bin/sh
# Mount NBD root device

if [ ! -f /tmp/nbdroot.info ]; then
    exit 0
fi

nbdroot=$(cat /tmp/nbdroot.info)
server="${nbdroot%:*}"
port="${nbdroot##*:}"

info "NBD: Connecting to $server:$port"

# Call our NBD connection script
/sbin/nbdroot "$server" "$port" /dev/nbd0

if [ $? -eq 0 ]; then
    info "NBD: Root device /dev/nbd0 is ready"
    # Wait a moment for udev to catch up
    udevadm settle --timeout=10 || sleep 2
else
    warn "NBD: Failed to connect to $server:$port"
    return 1
fi

