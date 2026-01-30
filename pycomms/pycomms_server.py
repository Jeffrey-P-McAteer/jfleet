#!/usr/bin/env python3

import socket
import struct
import json
import os
import fcntl
import traceback

# Simple client:
#    socat - UDP4-DATAGRAM:239.255.42.99:50000,bind=:50000,ip-add-membership=239.255.42.99:0.0.0.0,reuseaddr

MCAST_GRP = "239.255.42.99"
MCAST_PORT = 50000
BUF_SIZE = 1024

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
    if cmd.casefold() == "status".casefold():
        return collect_status()
    else:
        return {
            'error': f'Unknown command {cmd}'
        }

def main():
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

    while True:
        if len(our_outputs_to_ignore) > NUM_OUTPUTS_TO_IGNORE:
            our_outputs_to_ignore.pop(0)

        data, addr = rx.recvfrom(BUF_SIZE)
        if data in our_outputs_to_ignore:
            continue

        cmd = data.decode(errors="ignore").strip()

        out_obj = None
        try:
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

        our_outputs_to_ignore.append(payload)
        tx.sendto(payload, (MCAST_GRP, MCAST_PORT))


if __name__ == "__main__":
    main()

