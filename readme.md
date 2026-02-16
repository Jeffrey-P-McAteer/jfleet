
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

# Docs

 - https://libguestfs.org/virt-customize.1.html

# Notes

 - `boot-controls/*` is a custom made-up set of dracut scripts to mount the NBD root, written when we did not think dracut by default could do it. Turns out dracut can! So keep `netroot=nbd:{self.server_ip}:{NBD_PORT} ip=dhcp rd.neednet=1` in the network boot server GRUB config and someday remove the likely-useless `nbdroot={self.server_ip}:{NBD_PORT}` flag we invented.





