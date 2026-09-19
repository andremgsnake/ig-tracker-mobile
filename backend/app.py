import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from collections import defaultdict, deque
from time import time

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
ALLOWED_ORIGIN = "https://andremgsnake.github.io"
CORS(app, resources={r"/download": {"origins": [ALLOWED_ORIGIN]}}, expose_headers=["X-Filename"])

RATE_WINDOW = 3600
RATE_LIMIT = 20
_hits = defaultdict(deque)

def valid_instagram_url(url: str) -> bool:
    try:
        u = urlparse(url)
        if u.scheme != "https":
            return False
        host = (u.hostname or "").lower()
        return host == "instagram.com" or host == "www.instagram.com" or host.endswith(".instagram.com")
    except Exception:
        return False

def safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" ._")
    return (name[:140] or "instagram_video.mp4")

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

@app.get("/health")
def health():
    return jsonify({"ok": True})

@app.post("/download")
def download():
    ip = client_ip()
    if not rate_ok(ip):
        return jsonify({"error": "Troppi download in un'ora. Riprova più tardi."}), 429

    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    if not valid_instagram_url(url):
        return jsonify({"error": "Link Instagram non valido."}), 400

    tmp = tempfile.TemporaryDirectory(prefix="igdl_")
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
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

        files = [p for p in folder.iterdir() if p.is_file()]
        if not files:
            tmp.cleanup()
            return jsonify({"error": "Nessun video trovato."}), 422

        video = max(files, key=lambda p: p.stat().st_size)
        size = video.stat().st_size
        if size > 200 * 1024 * 1024:
            tmp.cleanup()
            return jsonify({"error": "Video troppo grande per il downloader."}), 413

        filename = safe_name(video.name)
        response = send_file(
            video,
            as_attachment=True,
            download_name=filename,
            mimetype="video/mp4",
            conditional=True,
        )
        response.headers["X-Filename"] = filename
        response.call_on_close(tmp.cleanup)
        return response

    except yt_dlp.utils.DownloadError as e:
        tmp.cleanup()
        msg = str(e).lower()
        if "login" in msg or "cookie" in msg:
            return jsonify({"error": "Questo Reel richiede login oppure Instagram ne limita l'accesso."}), 422
        return jsonify({"error": "Download non riuscito. Il Reel potrebbe essere privato, rimosso o temporaneamente bloccato da Instagram."}), 422
    except Exception:
        tmp.cleanup()
        return jsonify({"error": "Errore temporaneo del downloader."}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
