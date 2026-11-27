#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KSA MOFA - applicant data check (online / hosted helper)

server.py-র মতোই কাজ, শুধু host/port online deployment-এর জন্য:

  - 127.0.0.1:8777-এর বদলে 0.0.0.0:$PORT-এ শোনে
  - started-এ ব্রাউজার খোলে না
  - /api/health দিয়ে platform health-check করে

গুরুত্বপূর্ণ: token ও captcha key memory-তে থাকে। তাই শুধু একটি instance/worker
রাখুন। Restart বা scale হলে session reset হবে — পেজ reload করে নতুন captcha নিন।

চালানো: python server-online.py      (Render/Railway/VPS-এ start command)
"""

import http.cookiejar
import http.server
import json
import mimetypes
import os
import posixpath
import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

# Windows কনসোলে বাংলা প্রিন্ট করতে গিয়ে ক্র্যাশ না করার জন্য
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

BASE = "https://visa.mofa.gov.sa"
PAGE = BASE + "/visaperson/getapplicantdata"
HOME = BASE + "/"
SEARCH_URL = BASE + "/Home/SearchVisa"
ROOT = os.path.dirname(os.path.abspath(__file__))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_token_re = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')
_captcha_re = re.compile(r"GetRandomCaptchaImage/(\d+)")
_rem_re = re.compile(r"(-?\d*\.?\d+)rem\b", re.I)


def portal_rem(text):
    """পোর্টালের 1rem = 10px (html font-size 62.5%) shadow DOM-এও বজায় রাখি।"""
    def sub(match):
        value = float(match.group(1)) * 10
        if value.is_integer():
            return "%dpx" % int(value)
        return ("%.3f" % value).rstrip("0").rstrip(".") + "px"
    return _rem_re.sub(sub, text)

_jar = http.cookiejar.CookieJar()
# পোর্টালের ভাষা cookie দিয়ে ঠিক হয় — শুধু Accept-Language দিলে Arabic-এই থাকে।
# English cookie না দিলে ফলাফল পেজের declaration/text Arabic-এ আসে, যা লোকাল UI-তে মেলানো যায় না।
_jar.set_cookie(http.cookiejar.Cookie(
    0, "VisaPortalUICulture", "en", None, False,
    "visa.mofa.gov.sa", True, False, "/", True,
    False, None, False, None, None, {},
))
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))
_lock = threading.Lock()
_state = {"token": None, "key": None}
# KSA visa check (homepage "Query" form) রাখে আলাদা state — মূল applicant form-এর token-এর সাথে মিশবে না
_ksa_state = {"key": None}


def _request(url, data=None, referer=None, content_type=None):
    headers = {
        "User-Agent": UA,
        "Accept-Language": "en,ar;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/json,image/webp,image/png,*/*;q=0.8",
        "Referer": referer or PAGE,
    }
    if data is not None and content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers)
    return _opener.open(req, timeout=30)


def refresh_form():
    """GET ফর্ম পেজ → নতুন antiforgery token + ক্যাপচা key।"""
    with _lock:
        with _request(PAGE) as resp:
            html = resp.read().decode("utf-8", "replace")
        m_token = _token_re.search(html)
        m_key = _captcha_re.search(html)
        if not m_token or not m_key:
            raise RuntimeError("token/captcha not found - the site HTML probably changed")
        _state["token"] = m_token.group(1)
        _state["key"] = m_key.group(1)
        return {"token": _state["token"], "captchaKey": _state["key"]}


def refresh_ksa_form():
    """Homepage GET → নতুন captcha key (Query form-এর জন্য, token লাগে না)।"""
    with _lock:
        with _request(HOME, referer=HOME) as resp:
            html = resp.read().decode("utf-8", "replace")
        m_key = _captcha_re.search(html)
        if not m_key:
            raise RuntimeError("homepage captcha key not found - the site HTML probably changed")
        _ksa_state["key"] = m_key.group(1)
        return {"captchaKey": _ksa_state["key"]}


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "ksa-mofa-local/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    # ---------- helpers ----------
    def _send(self, code, ctype, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, "application/json; charset=utf-8", body)

    def _file(self, name, ctype):
        path = os.path.join(ROOT, name)
        if not os.path.isfile(path):
            return self._json({"error": "file not found: " + name}, 404)
        with open(path, "rb") as fh:
            self._send(200, ctype, fh.read())

    # ---------- GET ----------
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)

        if url.path in ("/", "/index.html"):
            return self._file("index.html", "text/html; charset=utf-8")
        if url.path == "/data.js":
            return self._file("data.js", "application/javascript; charset=utf-8")

        if url.path == "/api/health":
            return self._json({"status": "ok", "ready": bool(_state["token"])})

        if url.path == "/api/form":
            try:
                return self._json(refresh_form())
            except Exception as exc:  # noqa: BLE001
                return self._json({"error": str(exc)}, 502)

        if url.path == "/api/ksa/form":
            try:
                return self._json(refresh_ksa_form())
            except Exception as exc:  # noqa: BLE001
                return self._json({"error": str(exc)}, 502)

        if url.path == "/api/captcha":
            query = urllib.parse.parse_qs(url.query)
            key = (query.get("k") or [""])[0]
            if not re.fullmatch(r"\d+", key):
                return self._json({"error": "bad captcha key"}, 400)
            try:
                with _request(f"{BASE}/Base/GetRandomCaptchaImage/{key}") as resp:
                    body = resp.read()
                    ctype = resp.headers.get("Content-Type", "image/png")
                return self._send(200, ctype, body)
            except Exception as exc:  # noqa: BLE001
                return self._json({"error": str(exc)}, 502)

        if url.path == "/api/image":
            # ফলাফল পেজের ছবি ( applicant photo, barcode, logo ) একই session দিয়ে এনে দিই
            query = urllib.parse.parse_qs(url.query)
            kind = (query.get("type") or [""])[0]
            key = (query.get("key") or [""])[0]
            paths = {
                "photo": f"/VisaPerson/GetPersonPhoto?key={key}",
                "barcode": f"/Base/GenerateBarCode?key={key}",
                "logo": "/assets/common/images/MOFA_logo.png",
            }
            if kind not in paths:
                return self._json({"error": "bad image type"}, 400)
            if kind != "logo" and not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", key):
                return self._json({"error": "bad image key"}, 400)
            try:
                with _request(BASE + paths[kind]) as resp:
                    body = resp.read()
                    ctype = resp.headers.get("Content-Type", "") or ""
                if not ctype.lower().startswith("image/"):
                    ctype = ("image/png" if body[:8] == b"\x89PNG\r\n\x1a\n"
                             else "image/jpeg" if body[:2] == b"\xff\xd8"
                             else "image/gif" if body[:6] in (b"GIF87a", b"GIF89a")
                             else "image/png")
                return self._send(200, ctype, body)
            except Exception as exc:  # noqa: BLE001
                return self._json({"error": str(exc)}, 502)

        if url.path.startswith("/assets/"):
            # পোর্টালের CSS/font/image লোকাল ডোমেইনেই দিই, যাতে অফিশিয়াল লুক আসে
            target = url.path + (("?" + url.query) if url.query else "")
            try:
                with _request(BASE + target) as resp:
                    body = resp.read()
                    ctype = resp.headers.get("Content-Type", "") or ""
                if not ctype:
                    ctype = mimetypes.guess_type(url.path)[0] or "application/octet-stream"
                if ctype.split(";")[0].strip().lower() == "text/css":
                    text = body.decode("utf-8", "replace")

                    def fix_css_url(match):
                        quote = match.group(1)
                        value = match.group(2).strip().strip("\"'")
                        if value.startswith(("data:", "http://", "https://", "//")):
                            return match.group(0)
                        fixed = posixpath.normpath(posixpath.join(posixpath.dirname(url.path), value))
                        return "url(" + quote + fixed + quote + ")"

                    text = re.sub(r"url\((['\"]?)([^)]+?)\1\)", fix_css_url, text)
                    text = portal_rem(text)
                    body = text.encode("utf-8")
                return self._send(200, ctype, body)
            except Exception as exc:  # noqa: BLE001
                return self._json({"error": str(exc)}, 502)

        return self._json({"error": "not found"}, 404)

    # ---------- POST ----------
    def _ksa_search(self):
        """Homepage Query form: SearchingType=3 → JSON POST → result page HTML."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": "bad json: %s" % exc}, 400)

        model = {
            "SearchingType": "3",
            "ApplicationNumber": str(payload.get("ApplicationNumber", "")).strip(),
            "SponserID": str(payload.get("SponserID", "")).strip(),
            "Captcha": str(payload.get("Captcha", "")).strip(),
        }
        missing = [k for k in ("ApplicationNumber", "SponserID", "Captcha") if not model[k]]
        if missing:
            return self._json({"error": "missing fields: " + ", ".join(missing)}, 400)

        try:
            body = json.dumps({"model": model}).encode("utf-8")
            with _request(SEARCH_URL, data=body, referer=HOME,
                          content_type="application/json; charset=utf-8") as resp:
                raw = resp.read().decode("utf-8", "replace")
            data = json.loads(raw)
        except urllib.error.HTTPError as exc:
            return self._json({"error": "MOFA HTTP %s" % exc.code}, 502)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": str(exc)}, 502)

        # ক্যাপচা একবারই ব্যবহারযোগ্য — ফলাফলের সাথে আর পাঠানো হয় না, ডিস্কে লেখা হয় না
        _ksa_state["key"] = None
        if not data.get("Result"):
            return self._json({"ok": False, "message": str(data.get("Message") or "No result")})

        target = str(data.get("Url") or "/Home/PrintVisa")
        if target.startswith(BASE):
            target = target[len(BASE):]
        if not target.startswith("/"):
            target = "/" + target
        try:
            with _request(BASE + target, referer=HOME) as resp:
                html = resp.read().decode("utf-8", "replace")
                status = resp.status
        except urllib.error.HTTPError as exc:
            return self._json({"error": "MOFA HTTP %s" % exc.code}, 502)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": str(exc)}, 502)
        return self._json({"ok": True, "status": status, "html": html, "url": target})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api/ksa/search":
            return self._ksa_search()
        if url.path != "/api/search":
            return self._json({"error": "not found"}, 404)

        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": "bad json: %s" % exc}, 400)

        if not _state["token"]:
            try:
                refresh_form()
            except Exception as exc:  # noqa: BLE001
                return self._json({"error": str(exc)}, 502)

        form = {
            "PassportNumber": str(payload.get("PassportNumber", "")).strip(),
            "Nationality": str(payload.get("Nationality", "")).strip(),
            "VisaType": str(payload.get("VisaType", "")).strip(),
            "Embassy": str(payload.get("Embassy", "")).strip(),
            "Captcha": str(payload.get("Captcha", "")).strip(),
            "__RequestVerificationToken": _state["token"],
        }
        missing = [k for k in ("PassportNumber", "Nationality", "VisaType", "Embassy", "Captcha")
                   if not form[k]]
        if missing:
            return self._json({"error": "missing fields: " + ", ".join(missing)}, 400)

        data = urllib.parse.urlencode(form).encode("utf-8")
        try:
            with _request(PAGE, data=data) as resp:
                html = resp.read().decode("utf-8", "replace")
                status = resp.status
        except urllib.error.HTTPError as exc:
            return self._json({"error": "MOFA HTTP %s" % exc.code}, 502)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": str(exc)}, 502)

        # ক্যাপচা/টোকেন এখন খরচ হয়ে গেছে — ফলাফল সরাসরি ব্রাউজারে পাঠানো হয়, ডিস্কে কিছু লেখা হয় না
        _state["token"] = None
        return self._json({"status": status, "html": html})


def make_server():
    # Render/Railway/etc. PORT env দেয়; local-এ 8777 fallback
    port = int(os.environ.get("PORT", "8777"))
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    return httpd, port


def main():
    httpd, port = make_server()

    print("=" * 52)
    print(" KSA MOFA applicant check - online")
    print(f" listening on 0.0.0.0:{port}")
    print(" single worker only (token/captcha state is in memory)")
    print("=" * 52)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
