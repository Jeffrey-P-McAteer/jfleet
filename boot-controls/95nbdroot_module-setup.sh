#!/bin/bash

check() {
    return 0
}

depends() {
    echo network
}

cmdline() {
    echo "rd.neednet=1"
}

install() {
    inst_hook cmdline 30 "$moddir/parse-nbdroot.sh"
    inst_hook pre-mount 30 "$moddir/mount-nbdroot.sh"
    inst_multiple nbd-client modprobe mknod cat grep sleep dd blockdev ping
    inst_simple "$moddir/nbdroot.sh" /sbin/nbdroot
}

installkernel() {
    instmods nbd
}
