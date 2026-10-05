"""AES-128 in pure Python: ECB (Tuya LAN protocol 3.1/3.3) and GCM (Tuya cloud).

Omarchy ships no AES library for Python, and a plugin may not install one, so
this is the whole cipher. Packets to a lamp are a few hundred bytes; speed is a
non-issue. Verified against FIPS-197 and NIST GCM vectors in tests/test_aes.py.
"""

def _build_sbox():
    sbox = [0] * 256
    p = q = 1
    for _ in range(255):
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        rot = lambda v, n: ((v << n) | (v >> (8 - n))) & 0xFF
        sbox[p] = q ^ rot(q, 1) ^ rot(q, 2) ^ rot(q, 3) ^ rot(q, 4) ^ 0x63
    sbox[0] = 0x63
    inv = [0] * 256
    for i, v in enumerate(sbox):
        inv[v] = i
    return sbox, inv


SBOX, INV_SBOX = _build_sbox()


def _xtime(a):
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _mul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


# Precomputed GF(2^8) products for MixColumns and its inverse.
_M = {k: [_mul(i, k) for i in range(256)] for k in (2, 3, 9, 11, 13, 14)}


def _expand(key):
    if len(key) != 16:
        raise ValueError("AES-128 needs a 16-byte key, got %d" % len(key))
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = [SBOX[b] for b in t[1:] + t[:1]]
            t[0] ^= rcon
            rcon = _xtime(rcon)
        w.append([a ^ b for a, b in zip(w[i - 4], t)])
    return [sum(w[r * 4:r * 4 + 4], []) for r in range(11)]


def _shift(s):
    return [s[((c + r) % 4) * 4 + r] for c in range(4) for r in range(4)]


def _inv_shift(s):
    return [s[((c - r) % 4) * 4 + r] for c in range(4) for r in range(4)]


def _mix(s):
    m2, m3 = _M[2], _M[3]
    out = []
    for c in range(0, 16, 4):
        a0, a1, a2, a3 = s[c:c + 4]
        out += [m2[a0] ^ m3[a1] ^ a2 ^ a3, a0 ^ m2[a1] ^ m3[a2] ^ a3,
                a0 ^ a1 ^ m2[a2] ^ m3[a3], m3[a0] ^ a1 ^ a2 ^ m2[a3]]
    return out


def _inv_mix(s):
    m9, m11, m13, m14 = _M[9], _M[11], _M[13], _M[14]
    out = []
    for c in range(0, 16, 4):
        a0, a1, a2, a3 = s[c:c + 4]
        out += [m14[a0] ^ m11[a1] ^ m13[a2] ^ m9[a3], m9[a0] ^ m14[a1] ^ m11[a2] ^ m13[a3],
                m13[a0] ^ m9[a1] ^ m14[a2] ^ m11[a3], m11[a0] ^ m13[a1] ^ m9[a2] ^ m14[a3]]
    return out


def _xor(a, b):
    return [x ^ y for x, y in zip(a, b)]


class AES:
    def __init__(self, key: bytes):
        self._ks = _expand(bytes(key))

    def encrypt_block(self, block: bytes) -> bytes:
        ks = self._ks
        s = _xor(block, ks[0])
        for r in range(1, 10):
            s = _xor(_mix(_shift([SBOX[x] for x in s])), ks[r])
        return bytes(_xor(_shift([SBOX[x] for x in s]), ks[10]))

    def decrypt_block(self, block: bytes) -> bytes:
        ks = self._ks
        s = _xor(block, ks[10])
        for r in range(9, 0, -1):
            s = _inv_mix(_xor([INV_SBOX[x] for x in _inv_shift(s)], ks[r]))
        return bytes(_xor([INV_SBOX[x] for x in _inv_shift(s)], ks[0]))


def ecb_encrypt(key: bytes, data: bytes, pad: bool = True) -> bytes:
    if pad:
        n = 16 - len(data) % 16
        data += bytes([n]) * n
    if len(data) % 16:
        raise ValueError("ECB input must be a multiple of 16 bytes")
    aes = AES(key)
    return b"".join(aes.encrypt_block(data[i:i + 16]) for i in range(0, len(data), 16))


def ecb_decrypt(key: bytes, data: bytes, unpad: bool = True) -> bytes:
    if len(data) % 16:
        raise ValueError("ECB ciphertext must be a multiple of 16 bytes")
    aes = AES(key)
    out = b"".join(aes.decrypt_block(data[i:i + 16]) for i in range(0, len(data), 16))
    if unpad and out:
        n = out[-1]
        if not 1 <= n <= 16 or out[-n:] != bytes([n]) * n:
            raise ValueError("bad padding (wrong key?)")
        out = out[:-n]
    return out


# ---- GCM (no additional authenticated data; 96-bit nonces) ----

def _gmul(x, y):
    r = 0xE1 << 120
    z = 0
    for i in range(127, -1, -1):
        if (x >> i) & 1:
            z ^= y
        y = (y >> 1) ^ r if y & 1 else y >> 1
    return z


def _ghash(h, data):
    y = 0
    for i in range(0, len(data), 16):
        y = _gmul(y ^ int.from_bytes(data[i:i + 16].ljust(16, b"\0"), "big"), h)
    return _gmul(y ^ (len(data) * 8), h)


def _ctr(aes, j0, data):
    out = bytearray()
    n = int.from_bytes(j0[12:], "big")
    for i in range(0, len(data), 16):
        n = (n + 1) & 0xFFFFFFFF
        k = aes.encrypt_block(j0[:12] + n.to_bytes(4, "big"))
        out += bytes(a ^ b for a, b in zip(data[i:i + 16], k))
    return bytes(out)


def gcm_encrypt(key: bytes, nonce: bytes, plaintext: bytes) -> bytes:
    """Return ciphertext || 16-byte tag."""
    if len(nonce) != 12:
        raise ValueError("GCM nonce must be 12 bytes")
    aes = AES(key)
    h = int.from_bytes(aes.encrypt_block(bytes(16)), "big")
    j0 = nonce + b"\0\0\0\1"
    c = _ctr(aes, j0, plaintext)
    tag = _ghash(h, c) ^ int.from_bytes(aes.encrypt_block(j0), "big")
    return c + tag.to_bytes(16, "big")


def gcm_decrypt(key: bytes, nonce: bytes, data: bytes) -> bytes:
    """Decrypt ciphertext || tag; raise ValueError if the tag does not verify."""
    if len(nonce) != 12 or len(data) < 16:
        raise ValueError("bad GCM input")
    aes = AES(key)
    h = int.from_bytes(aes.encrypt_block(bytes(16)), "big")
    j0 = nonce + b"\0\0\0\1"
    c, tag = data[:-16], data[-16:]
    expect = _ghash(h, c) ^ int.from_bytes(aes.encrypt_block(j0), "big")
    if expect.to_bytes(16, "big") != tag:
        raise ValueError("GCM tag mismatch")
    return _ctr(aes, j0, c)
