"""Find devices' IP addresses on the LAN.

Two ways, because a common firewall setup (ufw, default deny incoming) drops
Tuya's UDP broadcasts:

1. Listen on UDP 6666/6667 for the broadcasts every device sends every few
   seconds (gwId, ip, version). Free when it works.
2. Probe TCP 6668 on every address of the local /24 networks, then ask each
   open host for its status with each candidate device's key: the key that
   decrypts the reply identifies the device. Works through ufw, since these
   are outgoing connections.
"""

import concurrent.futures as cf
import ipaddress
import json
import socket
import subprocess
import time

from . import tuya


def local_networks():
    """IPv4 networks of the machine's up interfaces, capped at /24."""
    try:
        out = subprocess.run(["ip", "-j", "-4", "addr", "show", "up"], capture_output=True, text=True, timeout=5).stdout
        ifaces = json.loads(out or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    nets = []
    for iface in ifaces:
        name = iface.get("ifname", "")
        if name == "lo" or name.startswith(("docker", "br-", "veth", "virbr", "tailscale", "tun", "wg")):
            continue
        for a in iface.get("addr_info", []):
            if a.get("family") != "inet":
                continue
            net = ipaddress.ip_network("%s/%d" % (a["local"], max(24, a.get("prefixlen", 24))), strict=False)
            if net not in nets:
                nets.append(net)
    return nets


def listen_broadcasts(seconds=6.0):
    """Return {gwId: {"ip":..., "version":...}} heard on UDP 6666/6667."""
    socks = []
    for port in (6666, 6667):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", port))
        except OSError:
            s.close()
            continue
        s.settimeout(0.2)
        socks.append(s)
    found = {}
    end = time.monotonic() + seconds
    while socks and time.monotonic() < end:
        for s in socks:
            try:
                data, _ = s.recvfrom(4096)
            except socket.timeout:
                continue
            info = tuya.decode_broadcast(data)
            if info and info.get("gwId"):
                found[info["gwId"]] = {"ip": info.get("ip"), "version": info.get("version")}
    for s in socks:
        s.close()
    return found


def open_hosts(networks=None, port=tuya.PORT, timeout=0.6):
    """Addresses with TCP `port` open."""
    hosts = [str(h) for net in (networks or local_networks()) for h in net.hosts()]

    def probe(ip):
        s = socket.socket()
        s.settimeout(timeout)
        try:
            return ip if s.connect_ex((ip, port)) == 0 else None
        except OSError:
            return None
        finally:
            s.close()

    with cf.ThreadPoolExecutor(128) as ex:
        return [ip for ip in ex.map(probe, hosts) if ip]


def identify(ip, devices, timeout=2.0):
    """Return (device, dps) for the first device in `devices` whose key
    decrypts this host's status reply, else (None, None)."""
    for dev in devices:
        try:
            d = tuya.Device(dev["id"], ip, dev["key"], dev.get("version", "3.3"), timeout=timeout)
            d.connect()
            try:
                return dev, d.query(timeout)
            finally:
                d.close()
        except (OSError, tuya.TuyaError, TimeoutError, ValueError):
            continue
    return None, None


def locate(devices, use_broadcasts=True, log=lambda *_: None):
    """Fill in "ip" (and "version") for every device that can be found.
    Returns {id: dps} for devices reached over TCP (their live status)."""
    statuses = {}
    pending = [d for d in devices]
    if use_broadcasts:
        heard = listen_broadcasts()
        log("broadcasts heard: %d" % len(heard))
        for d in pending:
            if d["id"] in heard:
                d["ip"] = heard[d["id"]]["ip"] or d.get("ip")
                d["version"] = heard[d["id"]]["version"] or d.get("version")
    # Confirm known IPs, then sweep for the rest.
    unresolved = []
    for d in pending:
        if d.get("ip"):
            dev, dps = identify(d["ip"], [d])
            if dev:
                statuses[d["id"]] = dps
                continue
        unresolved.append(d)
    if unresolved:
        hosts = open_hosts()
        log("hosts with TCP %d open: %s" % (tuya.PORT, ", ".join(hosts) or "none"))
        taken = {d.get("ip") for d in devices if d["id"] in statuses}
        with cf.ThreadPoolExecutor(16) as ex:
            results = ex.map(lambda ip: (ip, identify(ip, unresolved)), [h for h in hosts if h not in taken])
            for ip, (dev, dps) in results:
                if dev and dev["id"] not in statuses:
                    dev["ip"] = ip
                    statuses[dev["id"]] = dps
    return statuses
