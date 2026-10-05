"""Unit tests for the stdlib core: AES, Tuya framing against a fake device,
profiles and pins, wallpaper colour policy. Run: tools/check"""

import glob
import json
import os
import socket
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))

from omamood import aes, profiles, tuya, wallpaper  # noqa: E402


def _read_json(path):
    with open(path) as f:
        return json.load(f)

KEY = b"0123456789abcdef"


class AesTest(unittest.TestCase):
    def test_fips197(self):
        k = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        pt = bytes.fromhex("00112233445566778899aabbccddeeff")
        a = aes.AES(k)
        self.assertEqual(a.encrypt_block(pt).hex(), "69c4e0d86a7b0430d8cdb78070b4c55a")
        self.assertEqual(a.decrypt_block(bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")), pt)

    def test_ecb_roundtrip(self):
        for n in (0, 1, 15, 16, 17, 300):
            data = bytes(range(256))[:n] * 1
            self.assertEqual(aes.ecb_decrypt(KEY, aes.ecb_encrypt(KEY, data)), data)

    def test_ecb_wrong_key(self):
        with self.assertRaises(ValueError):
            aes.ecb_decrypt(b"fedcba9876543210", aes.ecb_encrypt(KEY, b'{"dps":{"20":true}}'))

    def test_gcm_nist(self):
        self.assertEqual(aes.gcm_encrypt(bytes(16), bytes(12), b"").hex(), "58e2fccefa7e3061367f1d57a4e7455a")
        ct = aes.gcm_encrypt(bytes(16), bytes(12), bytes(16))
        self.assertEqual(ct.hex(), "0388dace60b6a392f328c2b971b2fe78ab6e47d42cec13bdf53a67b21257bddf")
        self.assertEqual(aes.gcm_decrypt(bytes(16), bytes(12), ct), bytes(16))
        with self.assertRaises(ValueError):
            aes.gcm_decrypt(bytes(16), bytes(12), ct[:-1] + b"\0")


class FakeLamp(threading.Thread):
    """A Tuya 3.3 device on localhost, enough to answer queries and controls."""

    def __init__(self, dps, key=KEY, version="3.3", reject_query=False):
        super().__init__(daemon=True)
        self.dps = dict(dps)
        self.codec = tuya.Codec(key, version)
        self.reject_query = reject_query
        self.received = []
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]

    def run(self):
        conn, _ = self.srv.accept()
        self.srv.close()
        buf = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                conn.close()
                return
            buf += chunk
            while True:
                frame = tuya.unpack_frame(buf, has_retcode=False)
                if not frame:
                    break
                seq, cmd, _, body, buf = frame
                data = self.codec.decode(body)
                self.received.append((cmd, data))
                if cmd == tuya.DP_QUERY:
                    if self.reject_query:
                        reply = tuya.pack_frame(seq, cmd, b"json obj data unvalid", retcode=1)
                    else:
                        reply = tuya.pack_frame(seq, cmd, self.codec.encode(tuya.DP_QUERY, {"dps": self.dps}), retcode=0)
                    conn.sendall(reply)
                elif cmd == tuya.CONTROL_NEW:
                    conn.sendall(tuya.pack_frame(seq, cmd, self.codec.encode(tuya.STATUS, {"dps": self.dps}), retcode=0))
                elif cmd == tuya.CONTROL:
                    self.dps.update(data["dps"])
                    conn.sendall(tuya.pack_frame(seq, cmd, b"", retcode=0))
                    conn.sendall(tuya.pack_frame(0, tuya.STATUS, self.codec.encode(tuya.STATUS, {"dps": data["dps"], "t": 1}), retcode=0))
                elif cmd == tuya.HEART_BEAT:
                    conn.sendall(tuya.pack_frame(seq, cmd, b"", retcode=0))


class TuyaTest(unittest.TestCase):
    def test_frame_roundtrip(self):
        f = tuya.pack_frame(7, tuya.CONTROL, b"abc", retcode=0)
        seq, cmd, ret, body, rest = tuya.unpack_frame(f + b"tail")
        self.assertEqual((seq, cmd, ret, body, rest), (7, tuya.CONTROL, 0, b"abc", b"tail"))
        self.assertIsNone(tuya.unpack_frame(f[:-1]))
        with self.assertRaises(tuya.TuyaError):
            tuya.unpack_frame(f[:-5] + b"\0" + f[-4:])

    def test_unsupported_version(self):
        with self.assertRaises(tuya.UnsupportedProtocol):
            tuya.Codec(KEY, "3.4")

    def test_query_and_control(self):
        lamp = FakeLamp({"20": True, "21": "white", "22": 500})
        lamp.start()
        d = tuya.Device("dev1", "127.0.0.1", KEY, port=lamp.port, timeout=2)
        d.connect()
        self.assertEqual(d.query(), {"20": True, "21": "white", "22": 500})
        d.set_dps({"20": False})
        msgs = [d.recv(2), d.recv(2)]
        self.assertEqual(msgs[0].cmd, tuya.CONTROL)
        self.assertEqual(msgs[1].dps, {"20": False})
        self.assertFalse(lamp.dps["20"])
        # The CONTROL carried the version header; the query did not.
        self.assertEqual([c for c, _ in lamp.received], [tuya.DP_QUERY, tuya.CONTROL])
        d.close()

    def test_device22_fallback(self):
        lamp = FakeLamp({"1": True}, reject_query=True)
        lamp.start()
        d = tuya.Device("dev22", "127.0.0.1", KEY, port=lamp.port, timeout=2)
        d.query_dps = ["1"]
        d.connect()
        self.assertEqual(d.query(), {"1": True})
        self.assertEqual(d.query_cmd, tuya.CONTROL_NEW)
        d.close()


class ProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.all = profiles.load_all()

    def test_all_profiles_load(self):
        self.assertIn("tuya-light-v2", self.all)
        self.assertIn("lsc-moodlight", self.all)

    def test_pick(self):
        moodlight_status = {"20": True, "21": "white", "22": 764, "24": "000003e802bc"}
        self.assertEqual(profiles.pick(self.all, moodlight_status)["id"], "tuya-light-v2")
        self.assertEqual(profiles.pick(self.all, moodlight_status, ["LSC Moodlight"])["id"], "lsc-moodlight")
        self.assertEqual(profiles.pick(self.all, {"1": True, "2": "white", "3": 255, "5": "ff0000000003e8ff"})["id"], "tuya-light-v1")
        self.assertIsNone(profiles.pick(self.all, {"1": True}))   # a plug

    def test_inheritance(self):
        p = self.all["lsc-moodlight"]
        self.assertNotIn("temperature", p["dps"])
        self.assertEqual(p["dps"]["colour"]["dp"], "24")
        self.assertEqual(p["match"], {"product": ["LSC Moodlight", "LSC Smart Mood Light RGB+WW"]})

    def test_colour_codecs(self):
        self.assertEqual(profiles.encode_colour("hsv16", 207, 0.99, 0.76), "00cf03de02f8")
        self.assertEqual(profiles.decode_colour("hsv16", "00cf03de02f8"), (207, 0.99, 0.76))
        raw = profiles.encode_colour("rgbhsv", 0, 1, 1)
        self.assertEqual(raw, "ff00000000ffff")
        self.assertEqual(profiles.decode_colour("rgbhsv", raw), (0, 1.0, 1.0))
        self.assertEqual(profiles.hsv_to_hex(0, 1, 1), "#ff0000")


class PinTest(unittest.TestCase):
    """Every pin in tests/pins/ must keep decoding and planning exactly as recorded."""

    def test_pins(self):
        allp = profiles.load_all()
        pins = sorted(glob.glob(os.path.join(HERE, "pins", "*.json")))
        self.assertTrue(pins)
        for path in pins:
            pin = _read_json(path)
            with self.subTest(pin=os.path.basename(path)):
                light = profiles.Light(allp[pin["profile"]])
                for s in pin["statuses"]:
                    got = light.decode(s["dps"])
                    for k, v in s["state"].items():
                        self.assertEqual(got[k], v, "%s: %s" % (k, s["dps"]))
                for w in pin["writes"]:
                    name, *args = w["call"]
                    self.assertEqual(getattr(light, name)(*args), w["expect"], name)

    def test_tested_entries_point_at_pins(self):
        root = os.path.join(HERE, "..")
        for prof in profiles.load_all().values():
            for t in prof["tested"]:
                with self.subTest(profile=prof["id"]):
                    for k in ("owner", "model", "protocol", "pin"):
                        self.assertIn(k, t)
                    self.assertTrue(os.path.exists(os.path.join(root, t["pin"])), t["pin"])
                    self.assertEqual(_read_json(os.path.join(root, t["pin"]))["profile"], prof["id"])


class WallpaperTest(unittest.TestCase):
    def test_histogram_and_pick(self):
        text = ("      2428: (1.2,55.5,107.2) #01386B srgb(0%,21%,42%)\n"
                "      9000: (128,128,128) #808080 gray(50%)\n"
                "       300: (10,10,10) #0A0A0A srgb(4%,4%,4%)\n")
        clusters = wallpaper.parse_histogram(text)
        self.assertEqual(len(clusters), 3)
        self.assertEqual(wallpaper.pick_colour(clusters), (0x01, 0x38, 0x6B))

    def test_lamp_policy(self):
        self.assertEqual(wallpaper.lamp_hsv("#c1c1c1"), (0.0, 0.0))     # grey -> white
        h, s = wallpaper.lamp_hsv("#b59790")                            # muted -> boosted
        self.assertAlmostEqual(h, 11.4, places=0)
        self.assertEqual(s, 0.7)
        self.assertEqual(wallpaper.lamp_hsv("#ff0000"), (0.0, 1.0))


if __name__ == "__main__":
    unittest.main()
