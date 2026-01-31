#!/bin/bash

VM_IMAGE="$1"

echo "VM_IMAGE=$VM_IMAGE"

if ! [[ -e "$VM_IMAGE" ]] ; then
  echo "the file '$VM_IMAGE' does not exist! Pass a path to a .qcow2 or disk to boot."
  exit 1
fi

VM_IMG_EXT="${VM_IMAGE##*.}"

VM_IMG_FMT=raw
if [[ "$VM_IMG_EXT" = "qcow2" ]] ; then
  VM_IMG_FMT=qcow2
fi
echo "$VM_IMAGE is assumed to be in $VM_IMG_FMT format."

# Ctrl+A - X to kill vm

# sudo qemu-system-x86_64 \
#   -enable-kvm \
#   -machine q35 \
#   -cpu Skylake-Client-v4 \
#   -m 8G \
#   -drive file="$VM_IMAGE",if=virtio,format="$VM_IMG_FMT",cache=unsafe \
#   -netdev tap,id=net0,ifname=macvtap0,script=no,downscript=no \
#   -device virtio-net-pci,netdev=net0 \
#   -nographic -no-reboot

sudo qemu-system-x86_64 \
  -enable-kvm \
  -machine q35,accel=kvm \
  -cpu Skylake-Client-v4 \
  -m 8G \
  \
  -device ich9-ahci,id=sata \
  -drive file="$VM_IMAGE",if=none,id=disk0,format="$VM_IMG_FMT",cache=unsafe \
  -device ide-hd,drive=disk0,bus=sata.0 \
  \
  -netdev tap,id=net0,ifname=macvtap0,script=no,downscript=no \
  -device e1000,netdev=net0 \
  \
  -nographic -no-reboot

