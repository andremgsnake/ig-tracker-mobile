import os
import re
import json
import html as html_lib
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from collections import defaultdict, deque
from time import time

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import requests
import yt_dlp

app = Flask(__name__)

APP_VERSION = "2026.09.19.4"
ALLOWED_ORIGIN = "https://andremgsnake.github.io"
CORS(
    app,
    resources={r"/download": {"origins": [ALLOWED_ORIGIN]}},
    expose_headers=["X-Filename", "Content-Disposition"],
)

RATE_WINDOW = 3600
RATE_LIMIT = 30
MAX_BYTES = 200 * 1024 * 1024
_hits = defaultdict(deque)

UA = (
    "Mozilla/5.0 (Linux; Android 14; Mobile) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/153.0.0.0 Mobile Safari/537.36"
)

def valid_instagram_url(url: str) -> bool:
    try:
        u = urlparse(url)
        if u.scheme != "https":
            return False
        host = (u.hostname or "").lower()
        return host == "instagram.com" or host.endswith(".instagram.com")
    except Exception:
        return False

def valid_media_url(url: str) -> bool:
    try:
        u = urlparse(url)
        if u.scheme != "https":
            return False
        host = (u.hostname or "").lower()
        return (
            host == "cdninstagram.com"
            or host.endswith(".cdninstagram.com")
            or host == "fbcdn.net"
            or host.endswith(".fbcdn.net")
            or host == "instagram.com"
            or host.endswith(".instagram.com")
        )
    except Exception:
        return False

def safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" ._")
    if not name.lower().endswith(".mp4"):
        name += ".mp4"
    return name[:160] or "instagram_reel.mp4"

def client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"

def rate_ok(ip: str) -> bool:
    now = time()
    q = _hits[ip]
    while q and now - q[0] > RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        return False
    q.append(now)
    return True

def extract_shortcode(url: str) -> str:
    m = re.search(r"/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else ""

def canonical_url(url: str) -> str:
    code = extract_shortcode(url)
    if code:
        return f"https://www.instagram.com/reel/{code}/"
    return url

def _extract_json_array(text: str, marker_pos: int):
    start = text.find("[", marker_pos)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, min(len(text), start + 25000)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except Exception:
                    return None
    return None

def extract_progressive_url(page_text: str, shortcode: str) -> str:
    text = html_lib.unescape(page_text)

    search_from = 0
    if shortcode:
        code_marker = f'"code":"{shortcode}"'
        pos = text.find(code_marker)
        if pos >= 0:
            search_from = pos

    for marker in ['"video_versions":', '\"video_versions\":']:
        pos = text.find(marker, search_from)
        if pos < 0 and search_from:
            pos = text.find(marker)
        if pos >= 0:
            arr = _extract_json_array(text, pos)
            if isinstance(arr, list):
                candidates = []
                for v in arr:
                    if isinstance(v, dict):
                        u = str(v.get("url") or "")
                        if valid_media_url(u):
                            w = int(v.get("width") or 0)
                            h = int(v.get("height") or 0)
                            candidates.append((w * h, u))
                if candidates:
                    candidates.sort(reverse=True)
                    return candidates[0][1]

    patterns = [
        r'<meta[^>]+property=["\']og:video(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:video(?::secure_url)?["\']',
        r'"video_url":"([^"]+)"',
    ]
    for p in patterns:
        m = re.search(p, text, flags=re.I)
        if not m:
            continue
        u = html_lib.unescape(m.group(1)).replace("\\u0026", "&").replace("\\/", "/")
        if valid_media_url(u):
            return u
    return ""

def fetch_instagram_media(insta_url: str):
    canon = canonical_url(insta_url)
    code = extract_shortcode(canon)
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.7,en;q=0.6",
        "Referer": "https://www.instagram.com/",
    }

    session = requests.Session()
    page_urls = [canon]
    if code:
        page_urls.append(f"https://www.instagram.com/reel/{code}/embed/captioned/")

    media_url = ""
    for page_url in page_urls:
        try:
            r = session.get(page_url, headers=headers, timeout=25, allow_redirects=True)
            if r.ok:
                media_url = extract_progressive_url(r.text, code)
                if media_url:
                    break
        except requests.RequestException:
            pass

    if not media_url:
        return None

    media_headers = {
        "User-Agent": UA,
        "Referer": canon,
        "Accept": "*/*",
    }
    r = session.get(media_url, headers=media_headers, stream=True, timeout=40, allow_redirects=True)
    r.raise_for_status()

    content_length = int(r.headers.get("Content-Length") or 0)
    if content_length and content_length > MAX_BYTES:
        r.close()
        raise ValueError("too_large")

    tmp = tempfile.TemporaryDirectory(prefix="igdl_direct_")
    path = Path(tmp.name) / f"instagram_{code or 'reel'}.mp4"
    total = 0
    with open(path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 512):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_BYTES:
                r.close()
                tmp.cleanup()
                raise ValueError("too_large")
            f.write(chunk)
    r.close()

    if total < 1024:
        tmp.cleanup()
        return None
    return tmp, path

def fetch_with_ytdlp(insta_url: str):
    tmp = tempfile.TemporaryDirectory(prefix="igdl_ytdlp_")
    folder = Path(tmp.name)
    outtmpl = str(folder / "instagram_%(id)s.%(ext)s")

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "restrictfilenames": True,
        "format": "best[ext=mp4]/best",
        "outtmpl": outtmpl,
        "socket_timeout": 30,
        "retries": 2,
        "fragment_retries": 2,
        "http_headers": {
            "User-Agent": UA,
            "Referer": "https://www.instagram.com/",
        },
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(insta_url, download=True)

    files = [p for p in folder.iterdir() if p.is_file()]
    if not files:
        tmp.cleanup()
        return None

    video = max(files, key=lambda p: p.stat().st_size)
    if video.stat().st_size > MAX_BYTES:
        tmp.cleanup()
        raise ValueError("too_large")

    return tmp, video

@app.get("/health")
def health():
    return jsonify({"ok": True, "version": APP_VERSION})

@app.route("/download", methods=["GET", "POST"])
def download():
    ip = client_ip()
    if not rate_ok(ip):
        return jsonify({"error": "Troppi download in un'ora. Riprova più tardi."}), 429

    data = request.get_json(silent=True) or {}
    url = str(
        data.get("url")
        or request.args.get("url")
        or request.form.get("url")
        or ""
    ).strip()

    if not valid_instagram_url(url):
        return jsonify({"error": "Link Instagram non valido."}), 400

    result = None
    direct_error = None

    try:
        result = fetch_instagram_media(url)
    except ValueError as e:
        if str(e) == "too_large":
            return jsonify({"error": "Video troppo grande per il downloader."}), 413
        direct_error = str(e)
    except Exception as e:
        direct_error = str(e)

    if result is None:
        try:
            result = fetch_with_ytdlp(url)
        except ValueError as e:
            if str(e) == "too_large":
                return jsonify({"error": "Video troppo grande per il downloader."}), 413
        except yt_dlp.utils.DownloadError as e:
            msg = str(e).lower()
            if "login" in msg or "cookie" in msg:
                return jsonify({"error": "Questo Reel richiede login oppure Instagram ne limita l'accesso."}), 422
        except Exception:
            pass

    if result is None:
        return jsonify({
            "error": "Download non riuscito. Il Reel deve essere pubblico e accessibile senza login.",
            "version": APP_VERSION,
            "direct": bool(direct_error),
        }), 422

    tmp, video = result
    filename = safe_name(video.name)

    try:
        response = send_file(
            video,
            as_attachment=True,
            download_name=filename,
            mimetype="video/mp4",
            conditional=False,
            max_age=0,
        )
        response.headers["X-Filename"] = filename
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.call_on_close(tmp.cleanup)
        return response
    except Exception:
        tmp.cleanup()
        raise

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
