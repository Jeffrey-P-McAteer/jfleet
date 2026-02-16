#!/bin/sh

# Source dracut library for getarg function
type getarg >/dev/null 2>&1 || . /lib/dracut-lib.sh

# Parse nbdroot= parameter

[ -z "$root" ] && root=$(getarg root=)
nbdroot=$(getarg nbdroot=)

if [ -n "$nbdroot" ]; then
    info "NBD: Parsing nbdroot=$nbdroot"

    # Set root to nbd device
    root="block:/dev/nbd0"
    rootok=1

    # Save NBD server info for mount script
    echo "$nbdroot" > /tmp/nbdroot.info

    info "NBD: Set root=$root"
fi
