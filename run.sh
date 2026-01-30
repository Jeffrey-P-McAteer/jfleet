#!/bin/bash

VM_IMAGE="$1"

echo "VM_IMAGE=$VM_IMAGE"

if ! [[ -e "$VM_IMAGE" ]] ; then
  echo "the file '$VM_IMAGE' does not exist! Pass a path to a .qcow2 or disk to boot."
  exit 1
fi







