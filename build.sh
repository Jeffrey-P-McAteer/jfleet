#!/bin/bash

VM_IMAGE="out/jfleet-node.qcow2"

mkdir -p $(dirname "$VM_IMAGE")

if ! [[ -e "$VM_IMAGE" ]] ; then
  virt-builder centosstream-9 -o "$VM_IMAGE" --size 20G
fi


virt-customize -a "$VM_IMAGE" \
  --install vim,git,htop,tmux,bash-completion,python \
  --run-command 'useradd -m -G wheel -s /bin/bash user' \
  --run-command 'mkdir -p /opt/pycomms/' \
  --copy-in login-controls/autologin.conf:'/etc/systemd/system/getty@tty1.service.d/autologin.conf' \
  --copy-in login-controls/user:/etc/sudoers.d/user \
  --copy-in pycomms/pycomms_server.py:/opt/pycomms/pycomms_server.py \
  --copy-in pycomms/pycomms-server.service:/etc/systemd/system/pycomms-server.service \
  --run-command 'systemctl enable pycomms-server.service' \
  --run-command 'echo done'


