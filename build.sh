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

customize_step setup-nbd \
  --install dracut-network \
  --run-command 'echo "nbd" > /etc/modules-load.d/nbd.conf' \
  --run-command 'echo "options nbd max_part=16" > /etc/modprobe.d/nbd.conf' \
  --run-command 'echo "# NBD network boot configuration" > /etc/dracut.conf.d/90-nbd.conf' \
  --run-command 'echo "add_drivers+=\" nbd \"" >> /etc/dracut.conf.d/90-nbd.conf' \
  --run-command 'echo "add_dracutmodules+=\" network \"" >> /etc/dracut.conf.d/90-nbd.conf' \
  --run-command 'echo "kernel_cmdline+=\" rd.neednet=1 \"" >> /etc/dracut.conf.d/90-nbd.conf' \
  --run-command 'echo "hostonly=no" >> /etc/dracut.conf.d/90-nbd.conf' \
  --mkdir /usr/lib/dracut/modules.d/95nbd \
    --write '/usr/lib/dracut/modules.d/95nbd/module-setup.sh:#!/bin/bash
check() {
    return 0
}
depends() {
    echo network
}
install() {
    inst_hook cmdline 90 "$moddir/parse-nbd.sh"
    inst_hook pre-mount 90 "$moddir/mount-nbd.sh"
}
' \
    --write '/usr/lib/dracut/modules.d/95nbd/parse-nbd.sh:#!/bin/bash
# Parse nbdroot= kernel parameter
if [ -n "$nbdroot" ]; then
    info "NBD: nbdroot=$nbdroot"
    echo "$nbdroot" > /tmp/nbdroot
fi
' \
    --write '/usr/lib/dracut/modules.d/95nbd/mount-nbd.sh:#!/bin/bash
# Mount NBD device as root
if [ -f /tmp/nbdroot ]; then
    nbdroot=$(cat /tmp/nbdroot)
    info "NBD: Connecting to $nbdroot"

    # Load NBD module
    modprobe nbd max_part=16

    # Parse server:port
    server="${nbdroot%:*}"
    port="${nbdroot##*:}"

    # Connect using nbd-client if available, otherwise use kernel module directly
    if command -v nbd-client >/dev/null 2>&1; then
        nbd-client "$server" "$port" /dev/nbd0
    else
        # Use kernel module directly via sysfs
        echo "$server $port" > /sys/module/nbd/parameters/nbdserver 2>/dev/null || true
    fi

    # Wait for device
    sleep 2

    info "NBD: Connected /dev/nbd0"
fi
' \
    --chmod '0755:/usr/lib/dracut/modules.d/95nbd/module-setup.sh' \
    --chmod '0755:/usr/lib/dracut/modules.d/95nbd/parse-nbd.sh' \
    --chmod '0755:/usr/lib/dracut/modules.d/95nbd/mount-nbd.sh' \
    --run-command 'for kver in $(rpm -q kernel --qf "%{VERSION}-%{RELEASE}.%{ARCH}\n"); do echo "Building initramfs for kernel $kver..."; dracut --force --add "network nbd" --add-drivers "nbd" --no-hostonly /boot/initramfs-${kver}.img ${kver} && echo "  ✓ Success for $kver" || exit 1; done'



customize_step verify-nbd \
    --run-command 'kver=$(rpm -q kernel --qf "%{VERSION}-%{RELEASE}.%{ARCH}\n" | head -1); echo "Checking initramfs for kernel $kver:"; if lsinitrd /boot/initramfs-${kver}.img | grep -q "nbd.ko"; then echo "  ✓ NBD kernel module found"; else echo "  ✗ WARNING: NBD module not found"; fi; if lsinitrd /boot/initramfs-${kver}.img | grep -q "modules.d/95nbd"; then echo "  ✓ NBD dracut module found"; else echo "  ℹ NBD dracut module not found (may not be needed)"; fi' \


#for kver in $(rpm -q kernel --qf "%{VERSION}-%{RELEASE}.%{ARCH}\n"); do echo dracut --force --add "network nbd" /boot/initramfs-${kver}.img ${kver}; done
#dracut --force --add "network nbd" /boot/initramfs-5.14.0-71.el9.x86_64.img 5.14.0-71.el9.x86_64




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





