#!/bin/bash

VM_IMAGE="out/jfleet-node.qcow2"
VM_SIZE=20G
BASE_IMG_NAME=centosstream-9

set -e

OUT_DIR=$(dirname "$VM_IMAGE")

if [[ "$1" = "clean" ]] || [[ "$2" = "clean" ]] || [[ "$3" = "clean" ]] ; then
  if [[ -e "$OUT_DIR"/completed ]] ; then
    rm -rf "$OUT_DIR"/completed
  fi
  if [[ -e "$VM_IMAGE" ]] ; then
    rm "$VM_IMAGE"
  fi
fi

mkdir -p "$OUT_DIR"
mkdir -p "$OUT_DIR"/completed
mkdir -p "$OUT_DIR"/cache

VM_IMG_EXT="${VM_IMAGE##*.}"

VM_IMG_FMT=raw
if [[ "$VM_IMG_EXT" = "qcow2" ]] ; then
  VM_IMG_FMT=qcow2
fi

print_and_run() {
  echo "${@:1}"
  "${@:1}"
}

if ! [[ -e "$VM_IMAGE" ]] ; then
  print_and_run virt-builder $BASE_IMG_NAME \
    -o "$VM_IMAGE" \
    --format "$VM_IMG_FMT" \
    --hostname "jfleet-node" \
    --root-password disabled \
    --cache "$OUT_DIR"/cache \
    --size "$VM_SIZE"
else
  echo "$VM_IMAGE exists, skipping virt-builder"
fi

customize_step() {
  STEP_NAME="$1"
  FLAG_FILE="$OUT_DIR/completed/$STEP_NAME"
  if [[ -e "$FLAG_FILE" ]] ; then
    echo "Step $STEP_NAME completed, skipping."
  else
    print_and_run virt-customize --format "$VM_IMG_FMT" -a "$VM_IMAGE" "${@:2}"
    touch "$FLAG_FILE"
  fi
}

customize_step systemd-adjustments \
  --run-command 'systemd-machine-id-setup' \
  --run-command 'dnf --noplugins remove -y -q subscription-manager dnf-plugin-subscription-manager' \
  --run-command 'printf "SELINUX=disabled\nSELINUXTYPE=targeted\n" > /etc/selinux/config' \
  --run-command 'rm -f /.autorelabel /etc/selinux/.autorelabel || true' \
  --run-command 'sync'

customize_step install-packages --install vim,git,bash-completion,python

customize_step create-user \
  --run-command 'useradd -m -G wheel -s /bin/bash user' \
  --run-command "echo 'user:Passw0rd!' | chpasswd" \
  \
  --run-command 'mkdir -p /etc/systemd/system/getty@tty1.service.d/' \
  --copy-in login-controls/autologin.conf:'/etc/systemd/system/getty@tty1.service.d/' \
  --run-command 'chown -R root:root /etc/systemd/system/getty@tty1.service.d/' \
  --run-command 'chmod 644 /etc/systemd/system/getty@tty1.service.d/autologin.conf' \
  \
  --run-command 'mkdir -p /etc/systemd/system/serial-getty@ttyS0.service.d/' \
  --copy-in login-controls/autologin.conf:'/etc/systemd/system/serial-getty@ttyS0.service.d/' \
  --run-command 'chown -R root:root /etc/systemd/system/serial-getty@ttyS0.service.d/' \
  --run-command 'chmod 644 /etc/systemd/system/serial-getty@ttyS0.service.d/autologin.conf' \
  \
  --run-command 'systemctl daemon-reload' \
  --run-command 'mkdir -p /etc/sudoers.d/' \
  --copy-in login-controls/user:/etc/sudoers.d/ \
  --run-command 'chown -R root:root /etc/sudoers.d/' \

customize_step setup-pycomms \
  --run-command 'mkdir -p /opt/pycomms/' \
  --copy-in pycomms/pycomms_server.py:/opt/pycomms/ \
  --copy-in pycomms/pycomms-server.service:/etc/systemd/system/ \
  --run-command 'chown -R root:root /etc/systemd/system/' \
  --run-command 'systemctl enable pycomms-server.service' \



# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #


if [[ "$1" = "run" ]] || [[ "$2" = "run" ]] || [[ "$3" = "run" ]] ; then
  ./run.sh "$VM_IMAGE"
fi





