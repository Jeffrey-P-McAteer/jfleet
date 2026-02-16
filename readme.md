
# Arch Linux Dev Env Setup

```bash
yay -S guestfs-tools

virt-builder --list
```

# Building the Images

```bash
./build.sh
```

# Testing the Images

```bash
./run.sh ./out/jfleet-node.qcow2

```

# PXE boot the Images

```bash
sudo uv run network-boot-server.py eth0 ./out/jfleet-node.qcow2

# Once system boots
IFACE_IP=172.16.172.1 uv run pycomms/pycomms.py status

clear ; sudo tcpdump -i eth0 -s0 -vv -A host 239.255.42.99 and udp

```

# Docs

 - https://libguestfs.org/virt-customize.1.html

# Notes

 - `boot-controls/*` is a custom made-up set of dracut scripts to mount the NBD root, written when we did not think dracut by default could do it. Turns out dracut can! So keep `netroot=nbd:{self.server_ip}:{NBD_PORT} ip=dhcp rd.neednet=1` in the network boot server GRUB config and someday remove the likely-useless `nbdroot={self.server_ip}:{NBD_PORT}` flag we invented.





