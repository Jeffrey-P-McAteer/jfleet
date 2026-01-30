#!/bin/bash

VM_IMAGE="out/jfleet-node.qcow2"
VM_SIZE=20G

set -e

OUT_DIR=$(dirname "$VM_IMAGE")

mkdir -p "$OUT_DIR"
mkdir -p "$OUT_DIR"/completed

if ! [[ -e "$VM_IMAGE" ]] ; then
  virt-builder centosstream-9 -o "$VM_IMAGE" --size "$VM_SIZE"
fi

customize_step() {
  STEP_NAME="$1"
  FLAG_FILE="$OUT_DIR/completed/$STEP_NAME"
  if [[ -e "$FLAG_FILE" ]] ; then
    echo "Step $STEP_NAME completed, skipping."
  else
    virt-customize -a "$VM_IMAGE" "${@:2}"
    touch "$FLAG_FILE"
  fi
}

customize_step install-packages --install vim,git,bash-completion,python

customize_step create-user \
  --run-command 'useradd -m -G wheel -s /bin/bash user' \
  --run-command 'mkdir -p /etc/systemd/system/getty@tty1.service.d/' \
  --copy-in login-controls/autologin.conf:'/etc/systemd/system/getty@tty1.service.d/' \
  --run-command 'mkdir -p /etc/sudoers.d/' \
  --copy-in login-controls/user:/etc/sudoers.d/

customize_step setup-pycomms \
  --run-command 'mkdir -p /opt/pycomms/' \
  --copy-in pycomms/pycomms_server.py:/opt/pycomms/ \
  --copy-in pycomms/pycomms-server.service:/etc/systemd/system/ \
  --run-command 'systemctl enable pycomms-server.service' \


virt-customize -a "$VM_IMAGE" --run-command 'echo my hostname is $(hostname)'

