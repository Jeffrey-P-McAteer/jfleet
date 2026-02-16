#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "cryptography",
#   "netifaces",
# ]
# ///

import socket
import struct
import json
import os
import fcntl
import traceback
import sys
import subprocess

# We use "uv" on dev machine, and the server has python3-cryptography installed.
import cryptography
from cryptography.fernet import Fernet

#
# uv run pycomms/pycomms.py status
# uv run pycomms/pycomms.py cmd hostname
#
# Simple recieve client:
#    socat - UDP4-DATAGRAM:239.255.42.99:50000,bind=:50000,ip-add-membership=239.255.42.99:0.0.0.0,reuseaddr
# Simple send-commands client:
#    socat - UDP4-DATAGRAM:239.255.42.99:50000,ip-multicast-if=172.16.172.1,ip-multicast-ttl=1 <<<"status"


MCAST_GRP = "239.255.42.99"
MCAST_PORT = 50000
BUF_SIZE = 16 * 1024

def get_primary_interface():
    """Determine primary interface by routing table"""
    with open("/proc/net/route") as f:
        for line in f.readlines()[1:]:
            iface, dest, *_ = line.strip().split()
            if dest == "00000000":
                return iface
    return None


def get_ip_address(ifname):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return socket.inet_ntoa(
        fcntl.ioctl(
            s.fileno(),
            0x8915,  # SIOCGIFADDR
            struct.pack("256s", ifname[:15].encode()),
        )[20:24]
    )


def get_mac_address(ifname):
    with open(f"/sys/class/net/{ifname}/address") as f:
        return f.read().strip()


def collect_status():
    iface = get_primary_interface()
    if not iface:
        return {}

    return {
        "hostname": socket.gethostname(),
        "ip": get_ip_address(iface),
        "mac": get_mac_address(iface),
    }

def do_cmd(cmd):
    if isinstance(cmd, list) and len(cmd) > 0:
        if cmd[0].casefold() == "status".casefold():
            return collect_status()
        elif cmd[0].casefold() == "cmd".casefold():
            return run_cli_cmd(cmd[1:])
        else:
            return {
                'error': f'Unknown command {cmd}'
            }
    else:
        if cmd.casefold() == "status".casefold():
            return collect_status()
        else:
            return {
                'error': f'Unknown command {cmd}'
            }

def run_cli_cmd(args):
    return subprocess.check_output(args, text=True)

def main_server():
    # Receiver socket
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    rx.bind(("", MCAST_PORT))

    mreq = struct.pack(
        "4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY
    )
    rx.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    # Transmit socket
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)

    print(f"Listening on multicast {MCAST_GRP}:{MCAST_PORT}")

    NUM_OUTPUTS_TO_IGNORE = 2
    our_outputs_to_ignore = list()

    fernet = load_existing_pycomms_keyfile()

    while True:
        if len(our_outputs_to_ignore) > NUM_OUTPUTS_TO_IGNORE:
            our_outputs_to_ignore.pop(0)

        assumed_ciphertext, addr = rx.recvfrom(BUF_SIZE)
        if assumed_ciphertext in our_outputs_to_ignore:
            continue

        out_obj = None
        try:
            cmd = fernet.decrypt(assumed_ciphertext)
            try:
                cmd = cmd.decode(errors="ignore").strip()
            except:
                pass
            try:
                cmd = json.loads(cmd)
            except:
                pass
            # CMD may now either be a bare string or an array/dict of JSON data
            out_obj = do_cmd(cmd)
        except:
            out_obj = {
                'error': f'{traceback.format_exc()}'
            }

        if isinstance(out_obj, str):
            payload = out_obj.encode('utf-8')+b'\n'
        elif isinstance(out_obj, bytes):
            payload = out_obj
        else:
            payload = json.dumps(out_obj).encode('utf-8')+b'\n'

        # encrypt payload
        ciphertext = fernet.encrypt(payload)

        our_outputs_to_ignore.append(ciphertext)
        tx.sendto(ciphertext, (MCAST_GRP, MCAST_PORT))

def if_git_above_cd_to_it():
    try:
        script_dir = os.path.abspath(__file__)
        for _ in range(0, 4):
            script_dir = os.path.abspath(os.path.join(script_dir, '..'))
            if os.path.exists(os.path.join(script_dir, '.git')):
                os.chdir(script_dir)
                break
    except:
        traceback.print_exc()

def get_existing_pycomms_keyfile():
    canidates = [
        'pycomms-key',
        'crypto/pycomms-key',
        '/opt/pycomms/pycomms-key',
    ]
    for c in canidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    raise Exception(f'Cannot find pycomms-key!')

def load_existing_pycomms_keyfile():
    key_file = get_existing_pycomms_keyfile()
    with open(key_file, 'rb') as key_file:
        loaded_key = key_file.read()
    return Fernet(loaded_key)

def main_client(args):
    if_git_above_cd_to_it() # Now we can assume developer file-paths begin at jfleet git repo root.

    if args[0] == 'init-crypto':
        os.makedirs('crypto', exist_ok=True)
        key_file = os.path.abspath('crypto/pycomms-key')
        if not os.path.exists(key_file):
            key = Fernet.generate_key()
            with open(key_file, 'wb') as fd:
                fd.write(key)
            print(f'Generated: {key_file}')
        else:
            print(f'Already exists: {key_file}')

    else:
        import netifaces

        fernet = load_existing_pycomms_keyfile()
        message = json.dumps(args)
        message = message.encode('utf-8')
        ciphertext = fernet.encrypt(message)

        # Send all args to multicast, print replies for 2s
        explicit_iface_ip = os.environ.get('IFACE_IP', None)

        # Receiver socket
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        rx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        rx.bind(("", MCAST_PORT))

        if explicit_iface_ip:
            mreq = struct.pack(
                "4s4s", socket.inet_aton(MCAST_GRP), socket.inet_aton(explicit_iface_ip)
            )
        else:
            mreq = struct.pack(
                "4s4s", socket.inet_aton(MCAST_GRP), socket.inet_aton("0.0.0.0")
            )
        rx.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        rx.settimeout(int(os.environ.get('TIMEOUT_S', '6')))  # 6 second timeout

        # Transmit socket
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)

        # Enumerate all interfaces & transmit ciphertext
        for iface in netifaces.interfaces():
            if str(iface) == 'lo':
                continue

            addrs = netifaces.ifaddresses(iface)
            if netifaces.AF_INET in addrs:
                for addr_info in addrs[netifaces.AF_INET]:
                    iface_ip = addr_info['addr']
                    if explicit_iface_ip:
                        if not explicit_iface_ip.casefold() == iface_ip.casefold():
                            continue
                    try:
                        # Set outgoing interface for multicast
                        tx.setsockopt(
                            socket.IPPROTO_IP,
                            socket.IP_MULTICAST_IF,
                            socket.inet_aton(iface_ip)
                        )
                        # Send packet
                        tx.sendto(ciphertext, (MCAST_GRP, MCAST_PORT))
                        print(f"Sent on {iface} ({iface_ip})")
                    except Exception as e:
                        print(f"Failed on {iface} ({iface_ip}): {e}")

        # Listen for replies
        try:
            our_outputs_to_ignore = [ciphertext]
            while True:
                assumed_ciphertext, addr = rx.recvfrom(BUF_SIZE)
                if assumed_ciphertext in our_outputs_to_ignore:
                    continue
                try:
                    reply = fernet.decrypt(assumed_ciphertext)
                    try:
                        reply = reply.decode(errors="ignore").strip()
                    except:
                        pass
                    try:
                        reply = json.loads(reply)
                    except:
                        pass

                    if isinstance(reply, str):
                        print(f'{addr[0]}:{addr[1]} > {reply}')
                    else:
                        print(f'{addr[0]}:{addr[1]} > {json.dumps(reply, indent=2)}')
                except:
                    traceback.print_exc() # Likely bad encryption
        except:
            if 'TimeoutError' in traceback.format_exc():
                print(f'Timed Out')
            else:
                traceback.print_exc()

def main():
    if len(sys.argv) > 1:
        main_client(sys.argv[1:])
    else:
        main_server()

if __name__ == "__main__":
    main()

