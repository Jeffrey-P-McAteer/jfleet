#!/bin/bash

VM_IMAGE="build/jfleet-node.qcow2"

if ! [[ -e "$VM_IMAGE" ]] ; then
  virt-builder centosstream-9 -o "$VM_IMAGE" --size 20G
fi


virt-customize -a "$VM_IMAGE" \
  --run-command 'mkdir -p /opt/pycomms/' \
  --copy-in login-controls/autologin.conf:'/etc/systemd/system/getty@tty1.service.d/autologin.conf' \
  --copy-in login-controls/user:/etc/sudoers.d/user \
  --copy-in pycomms/pycomms_server.py:/opt/pycomms/pycomms_server.py \
  --copy-in pycomms/pycomms-server.service:/etc/systemd/system/pycomms-server.service \
  --run-command 'systemctl enable pycomms-server.service' \
  --run-command 'echo done'


