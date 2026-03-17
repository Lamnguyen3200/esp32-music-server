"""
ESP32 Music Server - Hỗ trợ tiếng Việt
Dùng SoundCloud + Invidious (mirror YouTube không cần đăng nhập)

Cài đặt:
    pip install flask yt-dlp requests gunicorn

Chạy:
    python music_server.py
"""

from flask import Flask, jsonify, request, Response
import yt_dlp
import requests
import threading
import time

app = Flask(__name__)

# ============================================================
# Cache
# ============================================================
_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 3600

def cache_get(key):
    with _cache_lock:
        item = _cache.get(key)
        if item and (time.time() - item["ts"]) < CACHE_TTL:
            return item["data"]
    return None

def cache_set(key, data):
    with _cache_lock:
        _cache[key] = {"data": data, "ts": time.time()}


# ============================================================
# Invidious — mirror YouTube, không bị chặn bot
# ============================================================
INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://invidious.privacyredirect.com",
    "https://yt.cdaut.de",
    "https://invidious.io.lol",
]

def search_invidious(song, artist=""):
    query = f"{song} {artist}".strip()
    key = f"inv:{query}"
    cached = cache_get(key)
    if cached:
        print(f"[CACHE] {query}")
        return cached

    for instance in INVIDIOUS_INSTANCES:
        try:
            search_url = f"{instance}/api/v1/search?q={requests.utils.quote(query)}&type=video&fields=videoId,title,author&page=1"
            r = requests.get(search_url, timeout=8)
            if not r.ok:
                continue

            results = r.json()
            if not results:
                continue

            video_id = results[0]["videoId"]
            title    = results[0].get("title", query)

            info_url = f"{instance}/api/v1/videos/{video_id}?fields=adaptiveFormats,formatStreams"
            r2 = requests.get(info_url, timeout=8)
            if not r2.ok:
                continue

            data = r2.json()

            audio_url = None
            for fmt in data.get("adaptiveFormats", []):
                if "audio" in fmt.get("type", "") and fmt.get("url"):
                    audio_url = fmt["url"]
                    break

            if not audio_url:
                for fmt in data.get("formatStreams", []):
                    if fmt.get("url"):
                        audio_url = fmt["url"]
                        break

            if not audio_url:
                continue

            result = {
                "title":     title,
                "audio_url": audio_url,
                "source":    "invidious",
            }
            cache_set(key, result)
            print(f"[INVIDIOUS] Tìm thấy: {title} ({instance})")
            return result

        except Exception as e:
            print(f"[INVIDIOUS ERROR] {instance}: {e}")
            continue

    return None


# ============================================================
# SoundCloud — backup nếu Invidious thất bại
# ============================================================
def search_soundcloud(song, artist=""):
    query = f"{song} {artist}".strip()
    key = f"sc:{query}"
    cached = cache_get(key)
    if cached:
        return cached

    ydl_opts = {
        "format": "bestaudio",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"scsearch1:{query}", download=False)
            if not info or "entries" not in info or not info["entries"]:
                return None

            video = info["entries"][0]
            audio_url = video.get("url")
            if not audio_url:
                return None

            result = {
                "title":     video.get("title", query),
                "audio_url": audio_url,
                "source":    "soundcloud",
            }
            cache_set(key, result)
            print(f"[SOUNDCLOUD] Tìm thấy: {result['title']}")
            return result

    except Exception as e:
        print(f"[SOUNDCLOUD ERROR] {e}")
        return None


# ============================================================
# Endpoint chính
# GET /stream_pcm?song=...&artist=...
# ============================================================
@app.route("/stream_pcm")
def stream_pcm():
    song   = request.args.get("song", "").strip()
    artist = request.args.get("artist", "").strip()

    if not song:
        return jsonify({"error": "Thiếu tên bài hát"}), 400

    print(f"\n[REQUEST] song='{song}' artist='{artist}'")

    # Thử Invidious trước
    result = search_invidious(song, artist)

    # Fallback SoundCloud
    if not result:
        print("[FALLBACK] Thử SoundCloud...")
        result = search_soundcloud(song, artist)

    if not result:
        return jsonify({"error": f"Không tìm thấy: {song}"}), 404

    proxy_url = f"/proxy?url={requests.utils.quote(result['audio_url'], safe='')}"
    language  = "vietnamese" if _is_vietnamese(song) else "unknown"

    return jsonify({
        "audio_url": proxy_url,
        "lyric_url": "",
        "language":  language,
        "title":     result["title"],
        "source":    result["source"],
    })


# ============================================================
# Proxy stream
# ============================================================
@app.route("/proxy")
def proxy():
    url = request.args.get("url", "")
    if not url:
        return jsonify({"error": "Thiếu url"}), 400

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Range": request.headers.get("Range", "bytes=0-"),
    }

    try:
        r = requests.get(url, headers=headers, stream=True, timeout=15)

        def generate():
            for chunk in r.iter_content(chunk_size=4096):
                if chunk:
                    yield chunk

        resp_headers = {
            "Content-Type": r.headers.get("Content-Type", "audio/mpeg"),
            "Accept-Ranges": "bytes",
        }
        if "Content-Length" in r.headers:
            resp_headers["Content-Length"] = r.headers["Content-Length"]
        if "Content-Range" in r.headers:
            resp_headers["Content-Range"] = r.headers["Content-Range"]

        return Response(generate(), status=r.status_code, headers=resp_headers)

    except Exception as e:
        print(f"[PROXY ERROR] {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================
# Ping
# ============================================================
@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "message": "ESP32 Music Server đang chạy"})


def _is_vietnamese(text):
    vn_chars = "áàảãạăắằẳẵặâấầẩẫậđéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợúùủũụưứừửữựýỳỷỹỵ"
    return any(c in text.lower() for c in vn_chars)


if __name__ == "__main__":
    print("=" * 50)
    print("  ESP32 Music Server (Invidious + SoundCloud)")
    print("  http://localhost:5005")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5005, debug=False)
