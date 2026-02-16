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

sudo rm -rf /tmp/pxeboot || true

sudo uv run network-boot-server.py eth0 "$VM_IMAGE"

