"""
ESP32 Music Server v2 - Hỗ trợ tiếng Việt tốt hơn
- Thêm "vietnamese" vào query khi tìm nhạc Việt
- Dùng Invidious + SoundCloud full (không phải preview)
"""

from flask import Flask, jsonify, request, Response
import yt_dlp
import requests
import threading
import time
import unicodedata
import re

app = Flask(__name__)

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

def is_vietnamese(text):
    vn_chars = "áàảãạăắằẳẵặâấầẩẫậđéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợúùủũụưứừửữựýỳỷỹỵ"
    return any(c in text.lower() for c in vn_chars)

def build_query(song, artist):
    """Tạo query tìm kiếm thông minh cho nhạc Việt"""
    query = f"{song} {artist}".strip()
    # Nếu là tiếng Việt, thêm từ khóa để tìm đúng hơn
    if is_vietnamese(song) or is_vietnamese(artist):
        query = f"{song} {artist} vietnamese".strip()
    return query

# ============================================================
# Invidious — mirror YouTube
# ============================================================
INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://invidious.privacyredirect.com",
    "https://yt.cdaut.de",
    "https://invidious.io.lol",
]

def search_invidious(song, artist=""):
    query = build_query(song, artist)
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

            # Ưu tiên audio-only opus/webm
            audio_url = None
            for fmt in data.get("adaptiveFormats", []):
                t = fmt.get("type", "")
                if ("audio/webm" in t or "audio/mp4" in t) and fmt.get("url"):
                    audio_url = fmt["url"]
                    break

            # Fallback format có video+audio
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
            print(f"[INVIDIOUS] {title} ({instance})")
            return result

        except Exception as e:
            print(f"[INVIDIOUS ERROR] {instance}: {e}")
            continue

    return None

# ============================================================
# SoundCloud — full track (không phải preview)
# ============================================================
def search_soundcloud(song, artist=""):
    query = build_query(song, artist)
    key = f"sc:{query}"
    cached = cache_get(key)
    if cached:
        return cached

    ydl_opts = {
        "format": "bestaudio",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        # Chỉ lấy bài hát đầy đủ, bỏ qua preview
        "match_filter": lambda info: None if info.get("duration", 0) > 60 else "Bỏ qua preview ngắn",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"scsearch3:{query}", download=False)
            if not info or "entries" not in info:
                return None

            # Lấy bài đầu tiên có thời lượng > 60 giây
            for entry in info["entries"]:
                if not entry:
                    continue
                duration = entry.get("duration", 0)
                audio_url = entry.get("url")
                if audio_url and duration > 60:
                    result = {
                        "title":     entry.get("title", query),
                        "audio_url": audio_url,
                        "source":    "soundcloud",
                        "duration":  duration,
                    }
                    cache_set(key, result)
                    print(f"[SOUNDCLOUD] {result['title']} ({duration}s)")
                    return result

    except Exception as e:
        print(f"[SOUNDCLOUD ERROR] {e}")

    return None

# ============================================================
# Endpoint chính
# ============================================================
@app.route("/stream_pcm")
def stream_pcm():
    song   = request.args.get("song", "").strip()
    artist = request.args.get("artist", "").strip()

    if not song:
        return jsonify({"error": "Thiếu tên bài hát"}), 400

    print(f"\n[REQUEST] song='{song}' artist='{artist}'")
    print(f"[LANG] Vietnamese: {is_vietnamese(song)}")

    # Thử Invidious trước
    result = search_invidious(song, artist)

    # Fallback SoundCloud
    if not result:
        print("[FALLBACK] Thử SoundCloud...")
        result = search_soundcloud(song, artist)

    if not result:
        return jsonify({"error": f"Không tìm thấy: {song}"}), 404

    proxy_url = f"/proxy?url={requests.utils.quote(result['audio_url'], safe='')}"
    language  = "vietnamese" if is_vietnamese(song) else "unknown"

    print(f"[OK] {result['title']} ({result['source']})")

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

@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "message": "ESP32 Music Server đang chạy"})

if __name__ == "__main__":
    print("=" * 50)
    print("  ESP32 Music Server v2")
    print("  http://localhost:5005")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5005, debug=False)


# ============================================================
# Keep-alive: tự ping chính mình mỗi 10 phút
# để Render không ngủ
# ============================================================
import threading

def keep_alive():
    while True:
        time.sleep(600)  # 10 phút
        try:
            requests.get("http://localhost:10000/ping", timeout=5)
            print("[KEEP-ALIVE] ping ok")
        except:
            pass

threading.Thread(target=keep_alive, daemon=True).start()
