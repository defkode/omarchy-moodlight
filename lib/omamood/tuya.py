"""Tuya local (LAN) protocol, version 3.3.

A Tuya Wi-Fi device listens on TCP 6668. Every message is a frame:

    000055AA | seq u32 | cmd u32 | len u32 | payload | crc32 u32 | 0000AA55

`len` counts payload + crc + suffix. Frames from the device start their payload
with a u32 return code. Payloads are JSON encrypted with AES-128-ECB under the
device's 16-character *local key*; most commands prefix the ciphertext with the
version header b"3.3" + 12 zero bytes (queries and heartbeats do not).

Only 3.3 is implemented because it is the only version tested against real
hardware so far (an LSC Smart Mood Light). 3.1 / 3.4 / 3.5 raise
UnsupportedProtocol with a pointer to docs/ADDING_DEVICES.md. Adding one means
extending `Codec` (3.4/3.5 negotiate a session key and add HMAC/GCM) and a pin
from a real device.
"""

import binascii
import hashlib
import json
import socket
import struct
import time

from . import aes

PORT = 6668
PREFIX = 0x000055AA
SUFFIX = 0x0000AA55

CONTROL = 0x07
STATUS = 0x08          # pushed by the device when a DP changes
HEART_BEAT = 0x09
DP_QUERY = 0x0A
CONTROL_NEW = 0x0D
DP_QUERY_NEW = 0x10

NO_HEADER = {DP_QUERY, DP_QUERY_NEW, HEART_BEAT}
SUPPORTED_VERSIONS = ("3.3",)

# Broadcasts on UDP 6667 (3.3) are encrypted with this well-known key.
UDP_KEY = hashlib.md5(b"yGAdlopoPVldABfn").digest()


class TuyaError(Exception):
    pass


class UnsupportedProtocol(TuyaError):
    pass


class Message:
    __slots__ = ("seq", "cmd", "retcode", "data")

    def __init__(self, seq, cmd, retcode, data):
        self.seq, self.cmd, self.retcode, self.data = seq, cmd, retcode, data

    @property
    def dps(self):
        return self.data.get("dps") if isinstance(self.data, dict) else None

    def __repr__(self):
        return "Message(seq=%d, cmd=0x%02x, ret=%s, data=%r)" % (self.seq, self.cmd, self.retcode, self.data)


def pack_frame(seq, cmd, payload, retcode=None):
    if retcode is not None:
        payload = struct.pack(">I", retcode) + payload
    head = struct.pack(">IIII", PREFIX, seq, cmd, len(payload) + 8) + payload
    return head + struct.pack(">II", binascii.crc32(head) & 0xFFFFFFFF, SUFFIX)


def unpack_frame(buf, has_retcode=True):
    """Parse one frame from the start of buf. Returns (seq, cmd, retcode, payload, rest)
    or None if buf does not yet hold a whole frame."""
    if len(buf) < 16:
        return None
    prefix, seq, cmd, length = struct.unpack(">IIII", buf[:16])
    if prefix != PREFIX:
        raise TuyaError("bad frame prefix")
    if length > 64 * 1024:
        raise TuyaError("frame too long: %d" % length)
    end = 16 + length
    if len(buf) < end:
        return None
    body = buf[16:end - 8]
    crc, suffix = struct.unpack(">II", buf[end - 8:end])
    if suffix != SUFFIX:
        raise TuyaError("bad frame suffix")
    if crc != binascii.crc32(buf[:end - 8]) & 0xFFFFFFFF:
        raise TuyaError("bad frame crc")
    retcode = None
    if has_retcode and len(body) >= 4:
        retcode = struct.unpack(">I", body[:4])[0]
        body = body[4:]
    return seq, cmd, retcode, body, buf[end:]


class Codec:
    """Encrypt/decrypt payloads for one protocol version."""

    def __init__(self, key, version="3.3"):
        version = str(version)
        if version not in SUPPORTED_VERSIONS:
            raise UnsupportedProtocol(
                "Tuya protocol %s is not supported yet (have: %s). See docs/ADDING_DEVICES.md."
                % (version, ", ".join(SUPPORTED_VERSIONS)))
        key = key.encode() if isinstance(key, str) else bytes(key)
        if len(key) != 16:
            raise TuyaError("local key must be 16 characters, got %d" % len(key))
        self.key = key
        self.version = version
        self.header = version.encode() + b"\0" * 12

    def encode(self, cmd, obj):
        raw = json.dumps(obj, separators=(",", ":")).encode()
        enc = aes.ecb_encrypt(self.key, raw)
        return enc if cmd in NO_HEADER else self.header + enc

    def decode(self, body):
        if not body:
            return None
        if body[:3] == self.version.encode():
            body = body[15:]
        try:
            raw = aes.ecb_decrypt(self.key, body)
        except ValueError:
            # Some replies are plaintext error strings.
            raw = body
        text = raw.decode("utf-8", "replace")
        try:
            return json.loads(text)
        except ValueError:
            return text


class Device:
    """One TCP connection to one device. Not thread-safe: the bridge gives each
    lamp its own thread and serialises access."""

    def __init__(self, dev_id, host, key, version="3.3", timeout=3.0, port=PORT):
        self.id = dev_id
        self.host = host
        self.port = port
        self.timeout = timeout
        self.codec = Codec(key, version)
        self.sock = None
        self.seq = 0
        self.buf = b""
        self.query_cmd = DP_QUERY
        self.query_dps = None   # list of dp ids, for devices that need CONTROL_NEW queries

    # ---- connection ----

    def connect(self):
        self.close()
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buf = b""

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    @property
    def connected(self):
        return self.sock is not None

    def fileno(self):
        return self.sock.fileno() if self.sock else -1

    # ---- framing ----

    def send(self, cmd, obj):
        if self.sock is None:
            self.connect()
        self.seq += 1
        self.sock.sendall(pack_frame(self.seq, cmd, self.codec.encode(cmd, obj)))
        return self.seq

    def recv(self, timeout=None):
        """Return the next Message, or None on timeout."""
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while True:
            try:
                frame = unpack_frame(self.buf)
            except TuyaError:
                # Drop garbage up to the next frame start and carry on.
                i = self.buf.find(struct.pack(">I", PREFIX), 1)
                self.buf = self.buf[i:] if i > 0 else b""
                continue
            if frame:
                seq, cmd, ret, body, self.buf = frame
                return Message(seq, cmd, ret, self.codec.decode(body))
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                return None
            if not chunk:
                self.close()
                raise ConnectionError("device closed the connection")
            self.buf += chunk

    # ---- commands ----

    def _base(self):
        return {"devId": self.id, "uid": self.id, "t": str(int(time.time()))}

    def query(self, timeout=None):
        """Ask for every DP; return the dps dict."""
        if self.query_cmd == CONTROL_NEW:
            obj = dict(self._base(), dps={str(d): None for d in (self.query_dps or [])})
        else:
            obj = dict(self._base(), gwId=self.id)
        self.send(self.query_cmd, obj)
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            msg = self.recv(deadline - time.monotonic())
            if msg is None:
                break
            if isinstance(msg.data, str) and "data unvalid" in msg.data and self.query_cmd == DP_QUERY:
                # "device22" firmware: queries must use CONTROL_NEW with explicit dps.
                self.query_cmd = CONTROL_NEW
                return self.query(timeout)
            if msg.dps is not None:
                return msg.dps
        raise TimeoutError("no status reply from %s" % self.host)

    def set_dps(self, dps):
        """Send a CONTROL with the given {dp: value}; the device acks and later
        pushes STATUS frames, which the caller reads with recv()."""
        return self.send(CONTROL, dict(self._base(), dps={str(k): v for k, v in dps.items()}))

    def heartbeat(self):
        return self.send(HEART_BEAT, {"gwId": self.id, "devId": self.id})


def decode_broadcast(data):
    """Decode a UDP discovery broadcast (port 6667, 3.3+). Returns a dict with
    gwId / ip / version / productKey, or None."""
    try:
        frame = unpack_frame(data, has_retcode=False)
    except TuyaError:
        return None
    if not frame:
        return None
    body = frame[3]
    if len(body) >= 4 and body[:4] == b"\0\0\0\0":
        body = body[4:]
    for candidate in (body, body[4:]):
        try:
            return json.loads(aes.ecb_decrypt(UDP_KEY, candidate))
        except (ValueError, UnicodeDecodeError):
            continue
    try:
        return json.loads(body)
    except ValueError:
        return None
