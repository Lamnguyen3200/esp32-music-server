"""
ESP32 Music Server - Hỗ trợ tiếng Việt
Dùng YouTube (yt-dlp) làm nguồn nhạc chính

Cài đặt:
    pip install flask yt-dlp requests

Chạy:
    python music_server.py

Sau đó dùng ngrok để tạo URL public (xem hướng dẫn bên dưới)
"""

from flask import Flask, jsonify, request, Response
import yt_dlp
import requests
import threading
import time
import re

app = Flask(__name__)

# ============================================================
# Cache đơn giản để tránh tìm lại bài vừa phát
# ============================================================
_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 3600  # 1 giờ

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
# Tìm nhạc YouTube bằng yt-dlp
# ============================================================
def search_youtube(song, artist=""):
    query = f"{song} {artist}".strip()
    key = f"yt:{query}"
    cached = cache_get(key)
    if cached:
        print(f"[CACHE] {query}")
        return cached

    ydl_opts = {
        "format": "bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio",
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "noplaylist": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
            if not info or "entries" not in info or not info["entries"]:
                return None

            video = info["entries"][0]
            # Lấy URL audio tốt nhất
            audio_url = None
            if "url" in video:
                audio_url = video["url"]
            elif "formats" in video:
                for fmt in reversed(video["formats"]):
                    if fmt.get("acodec") != "none" and fmt.get("url"):
                        audio_url = fmt["url"]
                        break

            if not audio_url:
                return None

            result = {
                "title": video.get("title", query),
                "audio_url": audio_url,
                "webpage_url": video.get("webpage_url", ""),
                "duration": video.get("duration", 0),
                "source": "youtube",
            }
            cache_set(key, result)
            print(f"[YT] Tìm thấy: {result['title']}")
            return result

    except Exception as e:
        print(f"[YT ERROR] {e}")
        return None


# ============================================================
# Endpoint chính — ESP32 gọi vào đây
# GET /stream_pcm?song=tên+bài&artist=nghệ+sĩ&source=youtube
#
# Trả về JSON:
# {
#   "audio_url": "/proxy?url=...",   <- ESP32 dùng để stream
#   "lyric_url": "",
#   "language": "vietnamese",
#   "title": "Tên bài hát"
# }
# ============================================================
@app.route("/stream_pcm")
def stream_pcm():
    song   = request.args.get("song", "").strip()
    artist = request.args.get("artist", "").strip()
    source = request.args.get("source", "youtube").lower()

    if not song:
        return jsonify({"error": "Thiếu tên bài hát"}), 400

    print(f"\n[REQUEST] song='{song}' artist='{artist}' source={source}")

    result = search_youtube(song, artist)

    if not result:
        return jsonify({"error": f"Không tìm thấy: {song}"}), 404

    # Wrap URL qua /proxy để ESP32 không cần xử lý redirect
    proxy_url = f"/proxy?url={requests.utils.quote(result['audio_url'], safe='')}"

    language = "vietnamese" if _is_vietnamese(song) else "unknown"

    return jsonify({
        "audio_url": proxy_url,
        "lyric_url": "",          # Chưa hỗ trợ lời bài hát
        "language":  language,
        "title":     result["title"],
        "source":    result["source"],
    })


# ============================================================
# Proxy stream — ESP32 tải nhạc qua đây
# GET /proxy?url=<encoded_audio_url>
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
# Kiểm tra server còn sống
# ============================================================
@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "message": "ESP32 Music Server đang chạy"})


# ============================================================
# Tiện ích
# ============================================================
def _is_vietnamese(text):
    vn_chars = "áàảãạăắằẳẵặâấầẩẫậđéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợúùủũụưứừửữựýỳỷỹỵ"
    return any(c in text.lower() for c in vn_chars)


# ============================================================
if __name__ == "__main__":
    print("=" * 50)
    print("  ESP32 Music Server")
    print("  http://localhost:5005")
    print("=" * 50)
    print()
    print("  Test: http://localhost:5005/ping")
    print("  Test: http://localhost:5005/stream_pcm?song=see+tinh")
    print()
    app.run(host="0.0.0.0", port=5005, debug=False)
