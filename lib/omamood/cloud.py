"""Smart Life QR login: fetch each device's local key with no developer account.

The Tuya device-sharing API that Home Assistant's official Tuya integration
uses (tuya-device-sharing-sdk). The user types their Smart Life "User Code"
(Me > Settings > Account and Security > User Code), scans a QR code in the app
and confirms; we read the device list, then log the session out. Nothing from
the cloud is kept except what lands in devices.json.

The Smart Life app shows the login as "Home Assistant": this is that
integration's public app registration (client id below). If Tuya ever revokes
it, `omamood setup manual` still works.

Requests after login are signed (HMAC-SHA256) and their query/body/result are
AES-GCM encrypted with a per-request key; see _signed().
"""

import base64
import hashlib
import hmac
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import aes

CLIENT_ID = "HA_3y9q4ak7g4ephrvke"
SCHEMA = "haauthorize"
LOGIN_HOST = "https://apigw.iotbing.com"
QR_PREFIX = "tuyaSmart--qrLogin?token="
# Device categories that are lights (Tuya category codes).
LIGHT_CATEGORIES = {"dj", "dd", "fwd", "xdd", "dc", "fsd", "tgq", "tyndj", "sxd", "gyd", "fsd"}

_NONCE_ALPHABET = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"


class CloudError(Exception):
    pass


def _http(method, url, headers=None, body=None, timeout=15):
    data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
    h = dict(headers or {})
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.URLError as e:
        raise CloudError("network error talking to Tuya: %s" % e)


def request_qr(user_code):
    """Start a login. Returns the token; the QR content is QR_PREFIX + token."""
    q = urllib.parse.urlencode({"clientid": CLIENT_ID, "usercode": user_code, "schema": SCHEMA})
    r = _http("POST", "%s/v1.0/m/life/home-assistant/qrcode/tokens?%s" % (LOGIN_HOST, q))
    if not r.get("success"):
        raise CloudError("%s: %s" % (r.get("code"), r.get("msg")))
    return r["result"]["qrcode"]


def poll_login(token, user_code):
    """One poll. Returns the login dict once the user has confirmed, else None."""
    q = urllib.parse.urlencode({"clientid": CLIENT_ID, "usercode": user_code})
    r = _http("GET", "%s/v1.0/m/life/home-assistant/qrcode/tokens/%s?%s" % (LOGIN_HOST, token, q))
    return r["result"] if r.get("success") else None


class Session:
    def __init__(self, login):
        self.endpoint = login["endpoint"].rstrip("/")
        self.access = login["access_token"]
        self.refresh = login["refresh_token"]
        self.terminal = login.get("terminal_id", "")

    def _signed(self, method, path, params=None, body=None):
        rid = str(uuid.uuid4())
        hash_key = hashlib.md5((rid + self.refresh).encode()).hexdigest()
        secret = hmac.new(rid.encode(), hash_key.encode(), hashlib.sha256).digest().hex()[:16].encode()

        def seal(obj):
            nonce = "".join(random.choice(_NONCE_ALPHABET) for _ in range(12)).encode()
            ct = aes.gcm_encrypt(secret, nonce, json.dumps(obj, separators=(",", ":")).encode())
            return (base64.b64encode(nonce) + base64.b64encode(ct)).decode()

        q = seal(params) if params else ""
        b = seal(body) if body else ""
        headers = {"X-appKey": CLIENT_ID, "X-requestId": rid, "X-sid": "",
                   "X-time": str(int(time.time() * 1000)), "X-token": self.access}
        sign_str = "||".join("%s=%s" % (k, headers[k])
                             for k in ("X-appKey", "X-requestId", "X-sid", "X-time", "X-token") if headers[k]) + q + b
        headers["X-sign"] = hmac.new(hash_key.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
        url = self.endpoint + path + ("?" + urllib.parse.urlencode({"encdata": q}) if q else "")
        r = _http(method, url, headers, {"encdata": b} if b else None)
        if not r.get("success"):
            raise CloudError("%s: %s %s" % (path, r.get("code"), r.get("msg")))
        raw = base64.b64decode(r["result"])
        out = aes.gcm_decrypt(secret, raw[:12], raw[12:]).decode()
        try:
            return json.loads(out)
        except ValueError:
            return out

    def devices(self):
        """Every device in every home: dicts with id, name, local_key, category, product_name, ..."""
        out = []
        for home in self._signed("GET", "/v1.0/m/life/users/homes") or []:
            out += self._signed("GET", "/v1.0/m/life/ha/home/devices", {"homeId": str(home["ownerId"])}) or []
        return out

    def logout(self):
        try:
            self._signed("POST", "/v1.0/m/token/terminal/expire", None,
                         {"accessToken": self.access, "terminalId": self.terminal})
            return True
        except CloudError:
            return False


def to_device_records(cloud_devices):
    """Cloud device dicts -> devices.json records (no IP yet: discovery finds it)."""
    out = []
    for d in cloud_devices:
        if not d.get("local_key"):
            continue
        out.append({
            "id": d["id"],
            "name": d.get("name") or d["id"],
            "key": d["local_key"],
            "category": d.get("category"),
            "product": [p for p in (d.get("product_name"), d.get("product_id")) if p],
            "light": d.get("category") in LIGHT_CATEGORIES,
        })
    return out
