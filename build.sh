#!/bin/bash

VM_IMAGE="out/jfleet-node.qcow2"
VM_SIZE=20G

set -e

OUT_DIR=$(dirname "$VM_IMAGE")

mkdir -p "$OUT_DIR"
mkdir -p "$OUT_DIR"/completed

VM_IMG_EXT="${VM_IMAGE##*.}"

VM_IMG_FMT=raw
if [[ "$VM_IMG_EXT" = "qcow2" ]] ; then
  VM_IMG_FMT=qcow2
fi

if ! [[ -e "$VM_IMAGE" ]] ; then
  echo virt-builder centosstream-9 -o "$VM_IMAGE" --format "$VM_IMG_FMT" --size "$VM_SIZE"
  virt-builder centosstream-9 -o "$VM_IMAGE" --format "$VM_IMG_FMT" --size "$VM_SIZE"
else
  echo "$VM_IMAGE exists, skipping virt-builder"
fi

customize_step() {
  STEP_NAME="$1"
  FLAG_FILE="$OUT_DIR/completed/$STEP_NAME"
  if [[ -e "$FLAG_FILE" ]] ; then
    echo "Step $STEP_NAME completed, skipping."
  else
    virt-customize --format "$VM_IMG_FMT" -a "$VM_IMAGE" "${@:2}"
    touch "$FLAG_FILE"
  fi
}

customize_step rebuild-inits \
  --install kernel-core,kernel-modules,kernel-modules-extra \
  --run-command 'dnf -y install kernel-core kernel-modules kernel-modules-extra' \
  --run-command '
for kver in /lib/modules/*; do
    kvername=$(basename $kver)
    echo "Building initramfs for kernel $kvername"
    dracut --force /boot/initramfs-$kvername.img $kvername
done
' \
  --run-command 'grub2-mkconfig -o /boot/grub2/grub.cfg' \
  --run-command 'systemd-machine-id-setup' \
  --run-command 'sync'

customize_step systemd-fixes \
  --selinux-relabel \
  --run-command 'sync'

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

