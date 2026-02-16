#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "tftpy",
# ]
# ///

"""
PXE Boot Server with NBD Support
Boots physical machines from qcow2 disk images over the network

Usage:
    sudo uv run network-boot-server.py <interface> <qcow2_image_path>

Example:
    sudo uv run network-boot-server.py eth0 ./out/jfleet-node.qcow2
"""

import sys
import os
import time
import signal
import subprocess
import threading
import logging
import argparse
import socket
import struct
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler

# Try to import tftpy, provide helpful message if not available
try:
    import tftpy
except ImportError:
    print("Error: tftpy library not found. Install it with: pip install tftpy")
    sys.exit(1)

# Network configuration
NETWORK_SUBNET = "172.16.172.0/24"
SERVER_IP = "172.16.172.1"
DHCP_RANGE_START = "172.16.172.100"
DHCP_RANGE_END = "172.16.172.200"
NETMASK = "255.255.255.0"

# Service ports
TFTP_PORT = 69
HTTP_PORT = 80
NBD_PORT = 10809

# Directories
WORK_DIR = "/tmp/pxeboot"
TFTP_ROOT = f"{WORK_DIR}/tftp"
HTTP_ROOT = f"{WORK_DIR}/http"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('PXEBootServer')


class DHCPServer:
    """Simple DHCP server with PXE support"""
    
    DHCP_SERVER_PORT = 67
    DHCP_CLIENT_PORT = 68
    
    # DHCP Message Types
    DHCPDISCOVER = 1
    DHCPOFFER = 2
    DHCPREQUEST = 3
    DHCPACK = 5
    
    def __init__(self, interface, server_ip, range_start, range_end, netmask):
        self.interface = interface
        self.server_ip = server_ip
        self.range_start = range_start
        self.range_end = range_end
        self.netmask = netmask
        self.lease_pool = {}
        self.next_ip = self._ip_to_int(range_start)
        self.running = False
        self.sock = None
        
    def _ip_to_int(self, ip):
        """Convert IP address string to integer"""
        return struct.unpack("!I", socket.inet_aton(ip))[0]
    
    def _int_to_ip(self, num):
        """Convert integer to IP address string"""
        return socket.inet_ntoa(struct.pack("!I", num))
    
    def _allocate_ip(self, mac):
        """Allocate an IP address for a MAC address"""
        if mac in self.lease_pool:
            return self.lease_pool[mac]
        
        ip = self._int_to_ip(self.next_ip)
        self.lease_pool[mac] = ip
        self.next_ip += 1
        
        # Wrap around if we exceed the range
        if self.next_ip > self._ip_to_int(self.range_end):
            self.next_ip = self._ip_to_int(self.range_start)
        
        return ip
    
    def _get_arch_name(self, arch_code):
        """Get human-readable architecture name"""
        if arch_code is None:
            return "Not specified (likely BIOS)"
        arch_names = {
            0x0000: "BIOS/Legacy x86",
            0x0006: "EFI IA32",
            0x0007: "EFI BC x64",
            0x0009: "EFI x64",
            0x000a: "EFI ARM 32-bit",
            0x000b: "EFI ARM 64-bit",
        }
        return arch_names.get(arch_code, f"Unknown (0x{arch_code:04x})")

    def _get_bootfile_for_arch(self, arch_code):
        """Get appropriate bootloader file for client architecture"""

        if arch_code in (0x0007, 0x0009):  # EFI x64
            logger.info("  → UEFI x64 client - using GRUB EFI")
            return "grubx64.efi"
        elif arch_code == 0x0006:  # EFI IA32
            logger.info("  → UEFI IA32 client - using GRUB IA32")
            return "grubia32.efi"
        elif arch_code == 0x0000 or arch_code is None:  # BIOS
            logger.info("  → BIOS/Legacy client - using SYSLINUX")
            return "lpxelinux.0"
        else:
            logger.warning(f"  → Unknown architecture - defaulting to BIOS")
            return "lpxelinux.0"

    def _build_dhcp_packet(self, transaction_id, client_mac, client_ip, msg_type, bootfile='lpxelinux.0'):
        """Build a DHCP packet"""
        packet = bytearray(300)

        # BOOTP header
        packet[0] = 2  # Boot Reply
        packet[1] = 1  # Ethernet
        packet[2] = 6  # Hardware address length
        packet[3] = 0  # Hops

        # Transaction ID
        packet[4:8] = transaction_id

        # Seconds elapsed
        packet[8:10] = b'\x00\x00'

        # Flags - Don't force broadcast for UEFI, let client decide
        packet[10:12] = b'\x00\x00'

        # Client IP (ciaddr) - empty for DISCOVER
        packet[12:16] = b'\x00' * 4

        # Your IP (yiaddr) - the offered IP
        packet[16:20] = socket.inet_aton(client_ip)

        # Server IP (siaddr)
        packet[20:24] = socket.inet_aton(self.server_ip)

        # Gateway IP (giaddr)
        packet[24:28] = b'\x00' * 4

        # Client MAC address
        packet[28:34] = client_mac
        packet[34:44] = b'\x00' * 10  # Padding

        # Server hostname (sname field) - 64 bytes at offset 44
        # Leave empty, using siaddr instead
        packet[44:108] = b'\x00' * 64

        # Boot filename (file field) - 128 bytes at offset 108
        # This is critical for PXE - put the boot filename here
        boot_filename_bytes = bootfile.encode('ascii') + b'\x00'
        packet[108:108+len(boot_filename_bytes)] = boot_filename_bytes
        packet[108+len(boot_filename_bytes):236] = b'\x00' * (128 - len(boot_filename_bytes))

        # Magic cookie
        packet[236:240] = bytes([99, 130, 83, 99])

        # DHCP options
        options = bytearray()

        # Option 53: DHCP Message Type
        options.extend([53, 1, msg_type])

        # Option 54: DHCP Server Identifier
        options.extend([54, 4] + list(socket.inet_aton(self.server_ip)))

        # Option 51: IP Address Lease Time (1 hour)
        options.extend([51, 4, 0, 0, 14, 16])

        # Option 1: Subnet Mask
        options.extend([1, 4] + list(socket.inet_aton(self.netmask)))

        # Option 3: Router
        options.extend([3, 4] + list(socket.inet_aton(self.server_ip)))

        # Option 6: DNS Server
        options.extend([6, 4] + list(socket.inet_aton(self.server_ip)))

        # Don't send Option 60 - that's for clients to identify themselves
        # Server responding with it confuses some clients

        # Option 43: Vendor-Specific Information (PXE)
        # For UEFI, this should be minimal or match what client expects
        # PXE Discovery Control: bits mean different things
        # Bit 0: disable broadcast discovery
        # Bit 1: disable multicast discovery
        # Bit 2: only accept servers in boot server list
        # Bit 3: download boot file from server
        # 0x0A = bits 1 and 3 set
        pxe_vendor_options = bytearray([6, 1, 0x0A])
        options.extend([43, len(pxe_vendor_options)] + list(pxe_vendor_options))

        # Option 66: TFTP Server Name (use IP as string for compatibility)
        tftp_server = self.server_ip.encode('ascii')
        options.extend([66, len(tftp_server)] + list(tftp_server))

        # Option 67: Bootfile Name (also in BOOTP field above, but include for compatibility)
        bootfile_bytes = bootfile.encode('ascii')
        options.extend([67, len(bootfile_bytes)] + list(bootfile_bytes))

        # End option
        options.append(255)

        packet[240:240+len(options)] = options

        return bytes(packet[:240+len(options)])

    def _parse_dhcp_packet(self, data):
        """Parse incoming DHCP packet"""
        if len(data) < 240:
            return None

        # Check magic cookie
        if data[236:240] != bytes([99, 130, 83, 99]):
            return None

        transaction_id = data[4:8]
        client_mac = data[28:34]

        # Parse options to find message type and vendor class
        msg_type = None
        vendor_class = None
        requested_ip = None
        client_arch = None  # Option 93 - Client System Architecture

        i = 240
        while i < len(data):
            option = data[i]
            if option == 255:  # End option
                break
            if option == 0:  # Pad option
                i += 1
                continue

            option_len = data[i + 1]
            if option == 53:  # DHCP Message Type
                msg_type = data[i + 2]
            elif option == 60:  # Vendor Class Identifier
                vendor_class = data[i + 2:i + 2 + option_len]
            elif option == 50:  # Requested IP Address
                requested_ip = socket.inet_ntoa(data[i + 2:i + 6])
            elif option == 93:  # Client System Architecture
                # This is a 2-byte value
                if option_len >= 2:
                    client_arch = (data[i + 2] << 8) | data[i + 3]

            i += 2 + option_len

        return {
            'transaction_id': transaction_id,
            'client_mac': client_mac,
            'msg_type': msg_type,
            'vendor_class': vendor_class,
            'requested_ip': requested_ip,
            'client_arch': client_arch
        }

    def start(self):
        """Start the DHCP server"""
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        # Bind to specific interface to ensure we send/receive on correct network
        import struct
        self.sock.setsockopt(
            socket.SOL_SOCKET,
            25,  # SO_BINDTODEVICE
            struct.pack('256s', self.interface.encode('utf-8')[:15])
        )

        try:
            # Bind to INADDR_ANY to receive broadcasts
            self.sock.bind(('0.0.0.0', self.DHCP_SERVER_PORT))
        except PermissionError:
            logger.error("Permission denied binding to port 67. Run as root!")
            return

        logger.info(f"DHCP server started on port {self.DHCP_SERVER_PORT}")
        logger.info(f"Bound to interface: {self.interface}")

        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
                parsed = self._parse_dhcp_packet(data)

                if not parsed:
                    continue

                client_mac = parsed['client_mac']
                mac_str = ':'.join(f'{b:02x}' for b in client_mac)

                if parsed['msg_type'] == self.DHCPDISCOVER:
                    vendor_class_bytes = parsed.get('vendor_class')
                    vendor = vendor_class_bytes.decode('ascii', errors='ignore') if vendor_class_bytes else ''
                    client_arch = parsed.get('client_arch')

                    logger.info(f"DHCP DISCOVER from {mac_str}")
                    if vendor:
                        logger.info(f"  Vendor Class: {vendor}")

                    # Check if this is a PXE client
                    is_pxe = vendor and 'PXEClient' in vendor

                    if is_pxe:
                        logger.info(f"  → PXE boot request")
                        if client_arch is not None:
                            logger.info(f"  Client Architecture: {client_arch} ({self._get_arch_name(client_arch)})")

                        # Determine bootloader based on architecture
                        bootfile = self._get_bootfile_for_arch(client_arch)
                        logger.info(f"  Selected bootloader: {bootfile}")
                    else:
                        logger.info(f"  → Regular DHCP request (likely initramfs network setup)")
                        bootfile = None  # No bootfile for regular DHCP

                    # Check if client already has an IP (ProxyDHCP mode)
                    client_has_ip = data[12:16] != b'\x00\x00\x00\x00'  # ciaddr field

                    if client_has_ip:
                        # ProxyDHCP mode - client wants boot info only, not IP
                        logger.info(f"  ProxyDHCP mode detected - client already has IP")
                        client_ip = socket.inet_ntoa(data[12:16])
                        logger.info(f"  Client IP: {client_ip}")
                    else:
                        # Normal DHCP - allocate new IP
                        logger.debug(f"  Client address: {addr}")
                        client_ip = self._allocate_ip(client_mac)

                    response = self._build_dhcp_packet(
                        parsed['transaction_id'],
                        client_mac,
                        client_ip,
                        self.DHCPOFFER,
                        bootfile or ''  # Empty string for non-PXE DHCP
                    )

                    try:
                        bytes_sent = self.sock.sendto(response, ('<broadcast>', self.DHCP_CLIENT_PORT))
                        logger.info(f"DHCP OFFER sent to {mac_str}: {client_ip} ({bytes_sent} bytes)")
                        if bootfile:
                            logger.info(f"  Next server: {self.server_ip}")
                            logger.info(f"  Boot file: {bootfile}")
                        else:
                            logger.info(f"  (No boot file - regular DHCP)")
                    except Exception as send_error:
                        logger.error(f"Failed to send DHCP OFFER: {send_error}")
                        import traceback
                        traceback.print_exc()

                elif parsed['msg_type'] == self.DHCPREQUEST:
                    vendor_class_bytes = parsed.get('vendor_class')
                    vendor = vendor_class_bytes.decode('ascii', errors='ignore') if vendor_class_bytes else ''
                    is_pxe = vendor and 'PXEClient' in vendor

                    logger.info(f"DHCP REQUEST from {mac_str}")
                    client_ip = self._allocate_ip(client_mac)

                    # Get architecture for bootfile (only for PXE)
                    if is_pxe:
                        client_arch = parsed.get('client_arch')
                        bootfile = self._get_bootfile_for_arch(client_arch)
                        logger.info(f"  → PXE boot ACK")
                    else:
                        bootfile = ''
                        logger.info(f"  → Regular DHCP ACK")

                    response = self._build_dhcp_packet(
                        parsed['transaction_id'],
                        client_mac,
                        client_ip,
                        self.DHCPACK,
                        bootfile
                    )

                    try:
                        bytes_sent = self.sock.sendto(response, ('<broadcast>', self.DHCP_CLIENT_PORT))
                        logger.info(f"DHCP ACK sent to {mac_str}: {client_ip} ({bytes_sent} bytes)")
                    except Exception as send_error:
                        logger.error(f"Failed to send DHCP ACK: {send_error}")

            except Exception as e:
                if self.running:
                    logger.error(f"DHCP error: {e}")

    def stop(self):
        """Stop the DHCP server"""
        self.running = False
        if self.sock:
            self.sock.close()


class PXEBootServer:
    """Main PXE Boot Server with NBD support"""

    def __init__(self, interface, qcow2_path):
        self.interface = interface
        self.qcow2_path = Path(qcow2_path)
        self.server_ip = SERVER_IP

        # Process handles
        self.nbd_process = None
        self.dhcp_server = None
        self.tftp_server = None
        self.http_server = None
        self.http_thread = None

        # Verify qcow2 file exists
        if not self.qcow2_path.exists():
            raise FileNotFoundError(f"QCOW2 image not found: {qcow2_path}")

        logger.info(f"Initializing PXE Boot Server on {interface}")
        logger.info(f"QCOW2 image: {qcow2_path}")

    def setup_network(self):
        """Configure network interface"""
        logger.info(f"Configuring network on {self.interface}")

        # Bring interface up
        subprocess.run(['ip', 'link', 'set', self.interface, 'up'], check=True)

        # Flush existing addresses
        subprocess.run(['ip', 'addr', 'flush', 'dev', self.interface], check=False)

        # Add IP address
        subprocess.run(
            ['ip', 'addr', 'add', f'{self.server_ip}/24', 'dev', self.interface],
            check=True
        )

        logger.info(f"Network configured: {self.server_ip}/24 on {self.interface}")

    def setup_directories(self):
        """Create necessary directories"""
        for directory in [WORK_DIR, TFTP_ROOT, HTTP_ROOT]:
            Path(directory).mkdir(parents=True, exist_ok=True)

        # Create pxelinux.cfg directory
        Path(f"{TFTP_ROOT}/pxelinux.cfg").mkdir(exist_ok=True)

        logger.info(f"Directories created in {WORK_DIR}")

    def copy_bootloader_files(self):
        """Copy PXE bootloader files to TFTP root"""
        logger.info("Setting up bootloader files")

        # Common locations for syslinux files (ordered by priority)
        syslinux_paths = [
            '/usr/lib/syslinux/bios',           # Arch Linux, modern systems
            '/usr/lib/syslinux/modules/bios',   # Debian/Ubuntu
            '/usr/lib/syslinux',                # Older systems
            '/usr/share/syslinux',              # Fedora/RHEL
            '/usr/lib/PXELINUX',                # Some Debian variants
        ]

        # Required files for BIOS PXE boot
        required_files = ['lpxelinux.0', 'ldlinux.c32', 'menu.c32', 'libutil.c32']

        missing_files = []

        for filename in required_files:
            dest = Path(f"{TFTP_ROOT}/{filename}")
            if dest.exists():
                logger.info(f"File already exists: {filename}")
                continue

            # Search for file in known locations
            found = False
            for search_path in syslinux_paths:
                source = Path(search_path) / filename
                if source.exists():
                    subprocess.run(['cp', str(source), str(dest)], check=True)
                    logger.info(f"Copied {filename} from {search_path}")
                    found = True
                    break

            if not found:
                missing_files.append(filename)

        # Report any missing files
        if missing_files:
            logger.error("=" * 60)
            logger.error("CRITICAL: Missing required bootloader files!")
            logger.error("=" * 60)
            logger.error(f"Could not find: {', '.join(missing_files)}")
            logger.error("")
            logger.error("Searched locations:")
            for path in syslinux_paths:
                logger.error(f"  - {path}")
            logger.error("")
            logger.error("Install syslinux package for your distribution:")
            logger.error("  Debian/Ubuntu: sudo apt install syslinux-common pxelinux")
            logger.error("  Arch Linux:    sudo pacman -S syslinux")
            logger.error("  Fedora/RHEL:   sudo dnf install syslinux")
            logger.error("  Alpine:        apk add syslinux")
            logger.error("")
            logger.error("Or manually copy files to: " + TFTP_ROOT)
            logger.error("=" * 60)
            raise FileNotFoundError(f"Missing bootloader files: {', '.join(missing_files)}")

        logger.info("All bootloader files ready")

    def copy_uefi_bootloader_files(self):
        """Copy UEFI GRUB bootloader files to TFTP root"""
        logger.info("Setting up UEFI bootloader files")

        # Common locations for pre-built GRUB EFI files
        grub_paths = [
            '/usr/lib/grub/x86_64-efi/monolithic/grubnetx64.efi.signed',
            '/usr/lib/grub/x86_64-efi/monolithic/grubnetx64.efi',
            '/boot/efi/EFI/centos/grubx64.efi',
            '/boot/efi/EFI/fedora/grubx64.efi',
            '/boot/efi/EFI/arch/grubx64.efi',
            '/boot/efi/EFI/ubuntu/grubx64.efi',
            '/boot/efi/EFI/debian/grubx64.efi',
            '/usr/lib/grub/x86_64-efi/grubnetx64.efi',
        ]

        dest = Path(f"{TFTP_ROOT}/grubx64.efi")

        if dest.exists():
            logger.info("✓ UEFI bootloader already exists: grubx64.efi")
            return

        # Search for pre-built GRUB EFI file
        for grub_path in grub_paths:
            source = Path(grub_path)
            if source.exists():
                subprocess.run(['cp', str(source), str(dest)], check=True)
                logger.info(f"✓ Copied GRUB EFI from {grub_path}")
                return

        # No pre-built file found - try to build it
        logger.info("Pre-built grubx64.efi not found - attempting to build...")

        if not self._build_grub_efi(dest):
            logger.warning("=" * 60)
            logger.warning("UEFI bootloader (grubx64.efi) not available")
            logger.warning("UEFI clients will not be able to boot!")
            logger.warning("")
            logger.warning("Install GRUB EFI bootloader:")
            logger.warning("  Debian/Ubuntu: sudo apt install grub-efi-amd64-bin grub-common")
            logger.warning("  Arch Linux:    sudo pacman -S grub")
            logger.warning("  Fedora/RHEL:   sudo dnf install grub2-efi-x64 grub2-tools")
            logger.warning("")
            logger.warning("Or manually run: ./build_grub_efi.sh")
            logger.warning("=" * 60)

    def _build_grub_efi(self, output_path):
        """Build grubx64.efi from GRUB modules using grub-mkstandalone"""
        # Check if grub-mkstandalone is available
        if subprocess.run(['which', 'grub-mkstandalone'], capture_output=True).returncode != 0:
            logger.warning("grub-mkstandalone not found - cannot build GRUB EFI")
            return False

        try:
            logger.info("Building GRUB EFI bootloader from modules...")

            # Create embedded config that loads main config from TFTP
            embedded_cfg = Path('/tmp/grub-embedded.cfg')
            embedded_cfg.write_text("""# Embedded GRUB config
set prefix=(tftp)/
configfile (tftp)/grub.cfg
""")

            # Build the standalone EFI image
            cmd = [
                'grub-mkstandalone',
                '--format=x86_64-efi',
                '--output=' + str(output_path),
                '--compress=xz',
                '--modules=tftp net efinet http configfile normal linux loopback part_gpt part_msdos fat ext2 iso9660 echo boot',
                '/boot/grub/grub.cfg=' + str(embedded_cfg)
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)
            embedded_cfg.unlink()  # Clean up temp file

            if result.returncode == 0 and output_path.exists():
                size = output_path.stat().st_size
                logger.info(f"✓ Built grubx64.efi successfully ({size / 1024:.1f} KB)")
                return True
            else:
                logger.warning(f"Failed to build GRUB EFI: {result.stderr}")
                return False

        except Exception as e:
            logger.warning(f"Error building GRUB EFI: {e}")
            return False

    def create_grub_config(self):
        """Create GRUB configuration for UEFI boot"""
        grub_config = f"""set timeout=5
set default=0

menuentry "Boot from Network (NBD)" {{
    echo "Loading kernel..."
    linux (tftp)/vmlinuz root=/dev/nbd0 rw nbdroot={self.server_ip}:{NBD_PORT} netroot=nbd:{self.server_ip}:{NBD_PORT} ip=dhcp rd.neednet=1 rd.debug
    echo "Loading initramfs..."
    initrd (tftp)/initrd.img
    echo "Booting..."
}}
"""

        grub_cfg_path = Path(f"{TFTP_ROOT}/grub.cfg")
        grub_cfg_path.write_text(grub_config)
        logger.info("✓ GRUB configuration created")

    def create_pxe_config(self):
        """Create PXE boot configuration"""
        config = f"""DEFAULT menu.c32
PROMPT 0
TIMEOUT 100

MENU TITLE Network Boot Menu

LABEL centos-nbd
    MENU LABEL Boot CentOS Stream 9 from NBD
    MENU DEFAULT
    KERNEL vmlinuz
    APPEND initrd=initrd.img root=/dev/nbd0 ip=dhcp nbdroot={self.server_ip}:{NBD_PORT} rd.neednet=1 rd.debug
    TEXT HELP
    Boot CentOS Stream 9 from network block device
    ENDTEXT

LABEL local
    MENU LABEL Boot from local disk
    LOCALBOOT 0
"""

        config_path = Path(f"{TFTP_ROOT}/pxelinux.cfg/default")
        config_path.write_text(config)
        logger.info("PXE configuration created")

    def extract_kernel_initrd(self):
        """Extract kernel and initrd from qcow2 image"""
        logger.info("Extracting kernel and initrd from qcow2 image")

        nbd_device = '/dev/nbd15'
        mount_point = Path('/mnt/pxeboot_temp')

        try:
            # Load NBD kernel module
            subprocess.run(['modprobe', 'nbd', 'max_part=16'], check=True)
            logger.info("NBD module loaded")

            # Connect qcow2 to NBD device
            subprocess.run(
                ['qemu-nbd', '--connect=' + nbd_device, '-f', 'qcow2', str(self.qcow2_path)],
                check=True
            )
            logger.info(f"QCOW2 connected to {nbd_device}")

            # Wait for kernel to detect partitions
            time.sleep(3)

            # Use fdisk to find partitions
            result = subprocess.run(
                ['fdisk', '-l', nbd_device],
                capture_output=True,
                text=True
            )
            logger.info(f"Partition table:\n{result.stdout}")

            # Parse partition table to find boot partition
            partitions = []
            for line in result.stdout.split('\n'):
                if nbd_device in line and ('Linux' in line or '*' in line):
                    parts = line.split()
                    if parts:
                        partition = parts[0]
                        partitions.append(partition)

            if not partitions:
                # Try to find any partition
                partitions = [f"{nbd_device}p{i}" for i in range(1, 4)
                             if Path(f"{nbd_device}p{i}").exists()]

            if not partitions:
                raise Exception(f"No partitions found on {nbd_device}")

            logger.info(f"Found partitions: {partitions}")

            # Always check for LVM - don't rely on blkid detection
            logger.info("Checking for LVM volumes...")

            # Scan for volume groups
            subprocess.run(['vgscan', '--mknodes'], check=False, capture_output=True)
            subprocess.run(['vgchange', '-ay'], check=False, capture_output=True)

            # Give LVM time to create device nodes
            time.sleep(2)

            # List logical volumes
            lv_result = subprocess.run(
                ['lvs', '--noheadings', '-o', 'lv_path'],
                capture_output=True,
                text=True
            )

            lvm_detected = False
            if lv_result.returncode == 0 and lv_result.stdout.strip():
                lv_paths = [lv.strip() for lv in lv_result.stdout.strip().split('\n') if lv.strip()]
                if lv_paths:
                    lvm_detected = True
                    logger.info(f"✓ Found LVM logical volumes: {lv_paths}")
                    # Prepend LVM volumes to try them first
                    partitions = lv_paths + partitions
                else:
                    logger.info("No LVM logical volumes found")
            else:
                logger.info("No LVM detected (this is fine for non-LVM images)")

            # Create mount point
            mount_point.mkdir(parents=True, exist_ok=True)

            # Try mounting each partition until we find /boot
            mounted_partition = None
            for partition in partitions:
                if not partition or not Path(partition).exists():
                    continue

                try:
                    logger.info(f"Trying to mount {partition}...")

                    # Try to get filesystem type first
                    fs_result = subprocess.run(
                        ['blkid', '-s', 'TYPE', '-o', 'value', partition],
                        capture_output=True,
                        text=True
                    )
                    fs_type = fs_result.stdout.strip()
                    if fs_type:
                        logger.info(f"  Filesystem type: {fs_type}")

                    # Skip swap partitions
                    if fs_type == 'swap':
                        logger.info(f"  Skipping swap partition")
                        continue

                    subprocess.run(
                        ['mount', '-o', 'ro', partition, str(mount_point)],
                        check=True,
                        capture_output=True
                    )

                    # Check if /boot exists or if we're at root with /boot
                    boot_dir = mount_point / 'boot'
                    if boot_dir.exists() or list(mount_point.glob('vmlinuz-*')):
                        mounted_partition = partition
                        logger.info(f"Successfully mounted {partition}")
                        break
                    else:
                        # Not the right partition, unmount and try next
                        subprocess.run(['umount', str(mount_point)], check=False)

                except subprocess.CalledProcessError as e:
                    logger.warning(f"Failed to mount {partition}: {e}")
                    continue

            if not mounted_partition:
                raise Exception("Could not find partition with kernel files")

            # Find kernel and initrd
            boot_dir = mount_point / 'boot'
            if not boot_dir.exists():
                boot_dir = mount_point

            logger.info(f"Looking for kernel in {boot_dir}")

            # Copy kernel
            kernel_files = list(boot_dir.glob('vmlinuz-*')) or list(boot_dir.glob('vmlinuz'))
            if kernel_files:
                # Use the newest kernel if multiple exist
                kernel_src = sorted(kernel_files)[-1]
                kernel_dst = Path(f"{TFTP_ROOT}/vmlinuz")
                subprocess.run(['cp', str(kernel_src), str(kernel_dst)], check=True)
                logger.info(f"Kernel copied: {kernel_src.name}")
            else:
                raise FileNotFoundError(f"Kernel (vmlinuz) not found in {boot_dir}")

            # Copy initrd
            initrd_patterns = ['initramfs-*.img', 'initrd.img-*', 'initrd-*']
            initrd_files = []
            for pattern in initrd_patterns:
                initrd_files.extend(list(boot_dir.glob(pattern)))

            if initrd_files:
                # Use the newest initrd if multiple exist
                initrd_src = sorted(initrd_files)[-1]
                initrd_dst = Path(f"{TFTP_ROOT}/initrd.img")
                subprocess.run(['cp', str(initrd_src), str(initrd_dst)], check=True)
                logger.info(f"Initrd copied: {initrd_src.name}")
            else:
                raise FileNotFoundError(f"Initrd (initramfs) not found in {boot_dir}")

            # Unmount and disconnect
            subprocess.run(['umount', str(mount_point)], check=True)

            # Deactivate LVM if it was used
            if lvm_detected:
                subprocess.run(['vgchange', '-an'], check=False, capture_output=True)

            subprocess.run(['qemu-nbd', '--disconnect', nbd_device], check=True)
            mount_point.rmdir()

            logger.info("Kernel and initrd extracted successfully")

        except Exception as e:
            logger.error(f"Failed to extract kernel/initrd: {e}")
            logger.error("Attempting cleanup...")
            subprocess.run(['umount', str(mount_point)], check=False)
            subprocess.run(['vgchange', '-an'], check=False, capture_output=True)
            subprocess.run(['qemu-nbd', '--disconnect', nbd_device], check=False)
            if mount_point.exists():
                try:
                    mount_point.rmdir()
                except:
                    pass
            raise

    def start_nbd_server(self):
        """Start qemu-nbd server to export qcow2 image"""
        logger.info(f"Starting NBD server on port {NBD_PORT}")

        cmd = [
            'qemu-nbd',
            '--persistent',
            '-f', 'qcow2',
            str(self.qcow2_path),
            '--bind', self.server_ip,
            '--port', str(NBD_PORT),
            '--fork'
        ]

        try:
            subprocess.run(cmd, check=True)
            logger.info(f"NBD server started: {self.server_ip}:{NBD_PORT}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to start NBD server: {e}")
            raise

    def stop_nbd_server(self):
        """Stop qemu-nbd server"""
        logger.info("Stopping NBD server")
        # Kill all qemu-nbd processes
        subprocess.run(['pkill', '-9', 'qemu-nbd'], check=False)

    def start_tftp_server(self):
        """Start TFTP server"""
        logger.info(f"Starting TFTP server on port {TFTP_PORT}")

        try:
            self.tftp_server = tftpy.TftpServer(TFTP_ROOT)
            # Run in a thread
            tftp_thread = threading.Thread(
                target=self.tftp_server.listen,
                args=(self.server_ip, TFTP_PORT),
                daemon=True
            )
            tftp_thread.start()
            logger.info(f"TFTP server started: {self.server_ip}:{TFTP_PORT}")
        except Exception as e:
            logger.error(f"Failed to start TFTP server: {e}")
            raise

    def start_http_server(self):
        """Start HTTP server"""
        logger.info(f"Starting HTTP server on port {HTTP_PORT}")

        class QuietHTTPHandler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=HTTP_ROOT, **kwargs)

            def log_message(self, format, *args):
                # Only log errors
                if args[1][0] != '2':
                    logger.info(f"HTTP: {format % args}")

        try:
            self.http_server = HTTPServer((self.server_ip, HTTP_PORT), QuietHTTPHandler)
            self.http_thread = threading.Thread(
                target=self.http_server.serve_forever,
                daemon=True
            )
            self.http_thread.start()
            logger.info(f"HTTP server started: http://{self.server_ip}:{HTTP_PORT}")
        except Exception as e:
            logger.error(f"Failed to start HTTP server: {e}")
            raise

    def start_dhcp_server(self):
        """Start DHCP server"""
        logger.info("Starting DHCP server")

        self.dhcp_server = DHCPServer(
            self.interface,
            SERVER_IP,
            DHCP_RANGE_START,
            DHCP_RANGE_END,
            NETMASK
        )

        dhcp_thread = threading.Thread(
            target=self.dhcp_server.start,
            daemon=True
        )
        dhcp_thread.start()
        logger.info(f"DHCP server started: {DHCP_RANGE_START} - {DHCP_RANGE_END}")

    def start(self):
        """Start all PXE boot services"""
        logger.info("=" * 60)
        logger.info("Starting PXE Boot Server")
        logger.info("=" * 60)

        try:
            # Setup
            self.setup_network()
            self.setup_directories()
            self.copy_bootloader_files()  # BIOS/Legacy bootloader
            self.copy_uefi_bootloader_files()  # UEFI bootloader
            self.extract_kernel_initrd()
            self.create_pxe_config()  # BIOS config
            self.create_grub_config()  # UEFI config

            # Start services
            self.start_nbd_server()
            time.sleep(1)  # Give NBD time to start

            self.start_tftp_server()
            self.start_http_server()
            self.start_dhcp_server()

            logger.info("=" * 60)
            logger.info("PXE Boot Server is READY")
            logger.info("=" * 60)
            logger.info(f"Server IP: {self.server_ip}")
            logger.info(f"Network: {NETWORK_SUBNET}")
            logger.info(f"DHCP Range: {DHCP_RANGE_START} - {DHCP_RANGE_END}")
            logger.info(f"NBD Export: {self.server_ip}:{NBD_PORT}")
            logger.info("")
            logger.info("Boot Modes Supported:")
            logger.info("  ✓ BIOS/Legacy (lpxelinux.0)")
            logger.info("  ✓ UEFI x64 (grubx64.efi)")
            logger.info("")
            logger.info("Connect a client via ethernet and PXE boot it")
            logger.info("Press Ctrl+C to stop")
            logger.info("=" * 60)

            # Keep running
            while True:
                time.sleep(1)

        except KeyboardInterrupt:
            logger.info("\nShutdown requested...")
        except Exception as e:
            logger.error(f"Error: {e}")
            raise
        finally:
            self.stop()

    def stop(self):
        """Stop all services and cleanup"""
        logger.info("Stopping all services...")

        # Stop DHCP server
        if self.dhcp_server:
            self.dhcp_server.stop()

        # Stop HTTP server
        if self.http_server:
            self.http_server.shutdown()

        # Stop NBD server
        self.stop_nbd_server()

        logger.info("All services stopped")


def check_requirements():
    """Check if required tools are installed"""
    required_commands = ['qemu-nbd', 'ip', 'modprobe', 'fdisk', 'blkid']
    optional_commands = ['vgscan', 'vgchange', 'lvs']  # LVM tools

    missing = []
    for cmd in required_commands:
        if subprocess.run(['which', cmd], capture_output=True).returncode != 0:
            missing.append(cmd)

    if missing:
        logger.error(f"Missing required commands: {', '.join(missing)}")
        logger.error("Install with:")
        logger.error("  Debian/Ubuntu: sudo apt install qemu-utils iproute2 kmod util-linux")
        logger.error("  Arch Linux:    sudo pacman -S qemu-base iproute2 kmod util-linux")
        logger.error("  Fedora/RHEL:   sudo dnf install qemu-img iproute kmod util-linux")
        return False

    # Check for LVM tools (optional but recommended)
    lvm_missing = []
    for cmd in optional_commands:
        if subprocess.run(['which', cmd], capture_output=True).returncode != 0:
            lvm_missing.append(cmd)

    if lvm_missing:
        logger.warning(f"Optional LVM tools not found: {', '.join(lvm_missing)}")
        logger.warning("LVM support will be limited. Install with:")
        logger.warning("  Debian/Ubuntu: sudo apt install lvm2")
        logger.warning("  Arch Linux:    sudo pacman -S lvm2")
        logger.warning("  Fedora/RHEL:   sudo dnf install lvm2")
        logger.warning("")

    # Check for syslinux files
    syslinux_paths = [
        '/usr/lib/syslinux/bios',
        '/usr/lib/syslinux/modules/bios',
        '/usr/lib/syslinux',
        '/usr/share/syslinux',
        '/usr/lib/PXELINUX',
    ]

    required_files = ['lpxelinux.0', 'ldlinux.c32', 'menu.c32', 'libutil.c32']
    found_files = []

    for filename in required_files:
        for search_path in syslinux_paths:
            if Path(search_path, filename).exists():
                found_files.append(filename)
                break

    if len(found_files) < len(required_files):
        missing_syslinux = set(required_files) - set(found_files)
        logger.warning(f"Missing syslinux files: {', '.join(missing_syslinux)}")
        logger.warning("Install syslinux package:")
        logger.warning("  Debian/Ubuntu: sudo apt install syslinux-common pxelinux")
        logger.warning("  Arch Linux:    sudo pacman -S syslinux")
        logger.warning("  Fedora/RHEL:   sudo dnf install syslinux")
        logger.warning("")
        logger.warning("Available syslinux files found:")
        for f in found_files:
            logger.warning(f"  ✓ {f}")
        logger.warning("Missing syslinux files:")
        for f in missing_syslinux:
            logger.warning(f"  ✗ {f}")
        return False

    logger.info(f"✓ All {len(found_files)} syslinux files found")

    # Check for GRUB EFI files (optional for UEFI support)
    grub_paths = [
        '/usr/lib/grub/x86_64-efi/monolithic/grubnetx64.efi.signed',
        '/usr/lib/grub/x86_64-efi/monolithic/grubnetx64.efi',
        '/boot/efi/EFI/centos/grubx64.efi',
        '/boot/efi/EFI/fedora/grubx64.efi',
        '/boot/efi/EFI/arch/grubx64.efi',
        '/boot/efi/EFI/ubuntu/grubx64.efi',
        '/usr/lib/grub/x86_64-efi/grubnetx64.efi',
    ]

    grub_found = False
    for grub_path in grub_paths:
        if Path(grub_path).exists():
            grub_found = True
            logger.info(f"✓ GRUB EFI bootloader found: {grub_path}")
            break

    if not grub_found:
        logger.warning("GRUB EFI bootloader not found - UEFI boot will not work")
        logger.warning("Install GRUB EFI for UEFI support:")
        logger.warning("  Debian/Ubuntu: sudo apt install grub-efi-amd64-bin")
        logger.warning("  Arch Linux:    sudo pacman -S grub")
        logger.warning("  Fedora/RHEL:   sudo dnf install grub2-efi-x64-modules")
        logger.warning("Note: BIOS/Legacy boot will still work")
        logger.warning("")

    return True


def main():
    parser = argparse.ArgumentParser(
        description='PXE Boot Server with NBD support for booting from qcow2 images'
    )
    parser.add_argument(
        'interface',
        help='Network interface to use (e.g., eth0, enp0s3)'
    )
    parser.add_argument(
        'qcow2_image',
        help='Path to qcow2 disk image'
    )

    args = parser.parse_args()

    # Check if running as root
    if os.geteuid() != 0:
        logger.error("This script must be run as root (sudo)")
        sys.exit(1)

    # Check requirements
    if not check_requirements():
        sys.exit(1)

    # Create and start server
    server = PXEBootServer(args.interface, args.qcow2_image)
    server.start()


if __name__ == '__main__':
    main()

