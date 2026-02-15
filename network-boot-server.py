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

    def _build_dhcp_packet(self, transaction_id, client_mac, client_ip, msg_type):
        """Build a DHCP packet"""
        packet = bytearray(300)

        # BOOTP header
        packet[0] = 2  # Boot Reply
        packet[1] = 1  # Ethernet
        packet[2] = 6  # Hardware address length
        packet[3] = 0  # Hops

        # Transaction ID
        packet[4:8] = transaction_id

        # Seconds and flags
        packet[8:12] = b'\x00' * 4

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

        # Server name and boot file
        packet[44:108] = b'\x00' * 64
        packet[108:236] = b'\x00' * 128

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

        # PXE Options
        # Option 66: TFTP Server Name
        options.extend([66, 4] + list(socket.inet_aton(self.server_ip)))

        # Option 67: Bootfile Name
        bootfile = b"lpxelinux.0"
        options.extend([67, len(bootfile)] + list(bootfile))

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

        # Parse options to find message type
        msg_type = None
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

            i += 2 + option_len

        return {
            'transaction_id': transaction_id,
            'client_mac': client_mac,
            'msg_type': msg_type
        }

    def start(self):
        """Start the DHCP server"""
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        try:
            self.sock.bind(('', self.DHCP_SERVER_PORT))
        except PermissionError:
            logger.error("Permission denied binding to port 67. Run as root!")
            return

        logger.info(f"DHCP server started on port {self.DHCP_SERVER_PORT}")

        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
                parsed = self._parse_dhcp_packet(data)

                if not parsed:
                    continue

                client_mac = parsed['client_mac']
                mac_str = ':'.join(f'{b:02x}' for b in client_mac)

                if parsed['msg_type'] == self.DHCPDISCOVER:
                    logger.info(f"DHCP DISCOVER from {mac_str}")
                    client_ip = self._allocate_ip(client_mac)

                    response = self._build_dhcp_packet(
                        parsed['transaction_id'],
                        client_mac,
                        client_ip,
                        self.DHCPOFFER
                    )

                    self.sock.sendto(response, ('<broadcast>', self.DHCP_CLIENT_PORT))
                    logger.info(f"DHCP OFFER sent to {mac_str}: {client_ip}")

                elif parsed['msg_type'] == self.DHCPREQUEST:
                    logger.info(f"DHCP REQUEST from {mac_str}")
                    client_ip = self._allocate_ip(client_mac)

                    response = self._build_dhcp_packet(
                        parsed['transaction_id'],
                        client_mac,
                        client_ip,
                        self.DHCPACK
                    )

                    self.sock.sendto(response, ('<broadcast>', self.DHCP_CLIENT_PORT))
                    logger.info(f"DHCP ACK sent to {mac_str}: {client_ip}")

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
    APPEND initrd=initrd.img root=/dev/nbd0 ip=dhcp nbdroot={self.server_ip}:{NBD_PORT} rd.neednet=1 console=tty0 console=ttyS0,115200n8
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

            # Create mount point
            mount_point.mkdir(parents=True, exist_ok=True)

            # Try mounting each partition until we find /boot
            mounted_partition = None
            for partition in partitions:
                try:
                    logger.info(f"Trying to mount {partition}...")
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
            subprocess.run(['qemu-nbd', '--disconnect', nbd_device], check=True)
            mount_point.rmdir()

            logger.info("Kernel and initrd extracted successfully")

        except Exception as e:
            logger.error(f"Failed to extract kernel/initrd: {e}")
            logger.error("Attempting cleanup...")
            subprocess.run(['umount', str(mount_point)], check=False)
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
            '--shared=8',
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
            self.copy_bootloader_files()
            self.extract_kernel_initrd()
            self.create_pxe_config()

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
    required_commands = ['qemu-nbd', 'ip', 'modprobe', 'fdisk']

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

