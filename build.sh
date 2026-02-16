#!/bin/bash

VM_IMAGE="out/jfleet-node.qcow2"
VM_SIZE=20G
BASE_IMG_NAME=centosstream-9

set -e

fmt_size() {
  local kb="${1:-0}"
  if (( kb >= 1000000 )); then
    printf "%dg %dmb" $(( kb / 1000000 )) $(( (kb / 1000) % 1000 ))
  else
    if (( kb >= 1000 )); then
      printf "%dmb" $(( kb / 1000 ))
    else
      printf "%dkb" "$kb"
    fi
  fi
}
fmt_secs() {
  local s="${1:-0}"

  if (( s < 60 )); then
    printf "%ds" "$s"
  else
    printf "%dm %ds" $(( s / 60 )) $(( s % 60 ))
  fi
}


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
  SECONDS=0
  print_and_run virt-builder $BASE_IMG_NAME \
    -o "$VM_IMAGE" \
    --format "$VM_IMG_FMT" \
    --hostname "jfleet-node" \
    --root-password disabled \
    --cache "$OUT_DIR"/cache \
    --size "$VM_SIZE"
  RUNTIME_TOTAL_S=${SECONDS}
  VM_IMAGE_SIZE_KB=$(du -s "$VM_IMAGE" | cut -f1)
  echo "virt-builder took $(fmt_secs $RUNTIME_TOTAL_S) to build $(fmt_size $VM_IMAGE_SIZE_KB) base image"
  cat > "$OUT_DIR/completed/initial-virt-builder" <<EOF
RUNTIME_TOTAL_S=$RUNTIME_TOTAL_S
VM_IMAGE_SIZE_KB=$VM_IMAGE_SIZE_KB
EOF
else
  source "$OUT_DIR/completed/initial-virt-builder"
  echo "$VM_IMAGE exists, skipping virt-builder (task took $(fmt_secs $RUNTIME_TOTAL_S), original size $(fmt_size $VM_IMAGE_SIZE_KB))"
fi

customize_step() {
  STEP_NAME="$1"
  FLAG_FILE="$OUT_DIR/completed/$STEP_NAME"
  if [[ -e "$FLAG_FILE" ]] ; then
    source "$FLAG_FILE"
    echo "Step $(printf "%-26.26s" $STEP_NAME) completed with a $(printf "%-7.7s" $(fmt_size $STEP_SIZE_INCREASE_KB)) storage size increase, skipping (task took $(fmt_secs $RUNTIME_TOTAL_S))"
  else
    BEFORE_SIZE_KB=$(du -s "$VM_IMAGE" | cut -f1)
    SECONDS=0

    print_and_run virt-customize --format "$VM_IMG_FMT" -a "$VM_IMAGE" "${@:2}"

    RUNTIME_TOTAL_S=${SECONDS}
    AFTER_SIZE_KB=$(du -s "$VM_IMAGE" | cut -f1)
    STEP_SIZE_INCREASE_KB=$(( $AFTER_SIZE_KB - $BEFORE_SIZE_KB ))

    cat > "$FLAG_FILE" <<EOF
BEFORE_SIZE_KB=$BEFORE_SIZE_KB
AFTER_SIZE_KB=$AFTER_SIZE_KB
STEP_SIZE_INCREASE_KB=$STEP_SIZE_INCREASE_KB
RUNTIME_TOTAL_S=$RUNTIME_TOTAL_S
EOF
    echo "$(printf "%-26.26s" $STEP_NAME) finished with a $(printf "%-7.7s" $(fmt_size $STEP_SIZE_INCREASE_KB)) storage size increase in $(fmt_secs $RUNTIME_TOTAL_S)"

  fi
}

customize_step systemd-adjustments \
  --run-command 'systemd-machine-id-setup' \
  --run-command 'dnf --noplugins remove -y -q subscription-manager dnf-plugin-subscription-manager' \
  --run-command 'printf "SELINUX=disabled\nSELINUXTYPE=targeted\n" > /etc/selinux/config' \
  --run-command 'rm -f /.autorelabel /etc/selinux/.autorelabel || true' \
  --run-command 'sync'

customize_step install-packages --install vim,git,bash-completion,python,socat

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

customize_step setup-nbd \
  --install dracut-network,iproute,iputils \
  --install tar,bison,gcc,make,glib2-devel,libnl3-devel \
    --run-command 'cd /tmp && curl -L https://github.com/NetworkBlockDevice/nbd/releases/download/nbd-3.25/nbd-3.25.tar.gz -o nbd.tar.gz' \
    --run-command 'cd /tmp && tar xzf nbd.tar.gz' \
    --run-command 'cd /tmp/nbd-3.25 && ./configure --prefix=/usr' \
    --run-command 'cd /tmp/nbd-3.25 && make' \
    --run-command 'cd /tmp/nbd-3.25 && make install' \
    --run-command 'rm -rf /tmp/nbd-3.25 /tmp/nbd.tar.gz' \
  --mkdir /usr/lib/dracut/modules.d/95nbdroot \
  --upload ./boot-controls/95nbdroot_module-setup.sh:/usr/lib/dracut/modules.d/95nbdroot/module-setup.sh \
  --upload ./boot-controls/95nbdroot_parse-nbdroot.sh:/usr/lib/dracut/modules.d/95nbdroot/parse-nbdroot.sh \
  --upload ./boot-controls/95nbdroot_mount-nbdroot.sh:/usr/lib/dracut/modules.d/95nbdroot/mount-nbdroot.sh \
  --upload ./boot-controls/95nbdroot_nbdroot.sh:/usr/lib/dracut/modules.d/95nbdroot/nbdroot.sh \
  --chmod '0755:/usr/lib/dracut/modules.d/95nbdroot/module-setup.sh' \
  --chmod '0755:/usr/lib/dracut/modules.d/95nbdroot/parse-nbdroot.sh' \
  --chmod '0755:/usr/lib/dracut/modules.d/95nbdroot/mount-nbdroot.sh' \
  --chmod '0755:/usr/lib/dracut/modules.d/95nbdroot/nbdroot.sh' \
    --run-command 'echo "add_dracutmodules+=\" nbdroot network \"" > /etc/dracut.conf.d/90-nbd.conf' \
    --run-command 'echo "add_drivers+=\" nbd \"" >> /etc/dracut.conf.d/90-nbd.conf' \
    --run-command 'echo "hostonly=no" >> /etc/dracut.conf.d/90-nbd.conf' \
  --run-command 'for kver in $(rpm -q kernel --qf "%{VERSION}-%{RELEASE}.%{ARCH}\n"); do dracut -f /boot/initramfs-${kver}.img ${kver} || exit 1; done' \
  --run-command 'kver=$(rpm -q kernel --qf "%{VERSION}-%{RELEASE}.%{ARCH}\n" | head -1); if lsinitrd /boot/initramfs-${kver}.img | grep -q nbd-client; then echo "✓ nbd-client found in initramfs"; else echo "✗ nbd-client NOT in initramfs"; exit 1; fi' \






# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

VM_IMAGE_SIZE_KB=$(du -s "$VM_IMAGE" | cut -f1)
echo "Final size of $VM_IMAGE is $(fmt_size $VM_IMAGE_SIZE_KB)"

if [[ "$1" = "run" ]] || [[ "$2" = "run" ]] || [[ "$3" = "run" ]] ; then
  ./run.sh "$VM_IMAGE"
fi





