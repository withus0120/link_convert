from flask import Flask, request, jsonify
from flask_cors import CORS
from googleapiclient.discovery import build
import os
import re
import requests
from urllib.parse import urlparse, parse_qs
from urllib.parse import quote

app = Flask(__name__)
CORS(app)

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"


def get_youtube():
    if not YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY 환경변수가 설정되지 않았습니다.")
    return build(
        YOUTUBE_API_SERVICE_NAME,
        YOUTUBE_API_VERSION,
        developerKey=YOUTUBE_API_KEY
    )


def clean_input(value: str) -> str:
    return (value or "").strip()


def extract_video_id(value: str):
    value = clean_input(value)

    if "youtu.be/" in value:
        return value.split("youtu.be/")[-1].split("?")[0].split("&")[0].strip("/")

    if "watch?v=" in value:
        parsed = urlparse(value)
        qs = parse_qs(parsed.query)
        return qs.get("v", [None])[0]

    if "/live/" in value:
        return value.split("/live/")[-1].split("?")[0].split("&")[0].strip("/")

    if "/shorts/" in value:
        return value.split("/shorts/")[-1].split("?")[0].split("&")[0].strip("/")

    return None


def extract_channel_id(value: str):
    value = clean_input(value)
    match = re.search(r"youtube\.com/channel/(UC[\w-]+)", value)
    if match:
        return match.group(1)
    if value.startswith("UC") and len(value) >= 20:
        return value
    return None


def extract_handle(value: str):
    value = clean_input(value)

    if value.startswith("@"):
        return value

    match = re.search(r"youtube\.com/@([^/?&\s]+)", value)
    if match:
        return "@" + match.group(1)

    return None


def detect_input_type(value: str):
    value = clean_input(value)

    if extract_handle(value):
        return "handle"

    if extract_channel_id(value):
        return "channel_id"

    if "watch?v=" in value:
        return "video_url"

    if "youtu.be/" in value:
        return "short_video_url"

    if "/shorts/" in value:
        return "shorts_url"

    if "/live/" in value:
        return "live_url"

    return "unknown"


def fetch_html(url: str):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    res = requests.get(url, headers=headers, timeout=10)
    res.raise_for_status()
    return res.text


def handle_to_channel_id(handle: str):
    handle = handle.strip()

    if not handle.startswith("@"):
        handle = "@" + handle

    raw_handle = handle
    encoded_handle = "@" + quote(handle[1:], safe="")

    urls = [
        f"https://www.youtube.com/{raw_handle}",
        f"https://www.youtube.com/{encoded_handle}",
    ]

    for url in urls:
        try:
            html = fetch_html(url)

            patterns = [
                r'"channelId":"(UC[\w-]+)"',
                r'"externalId":"(UC[\w-]+)"',
                r'<meta itemprop="channelId" content="(UC[\w-]+)">',
                r'"browseId":"(UC[\w-]+)"'
            ]

            for pattern in patterns:
                match = re.search(pattern, html)
                if match:
                    return match.group(1)

        except Exception:
            continue

    return None


def channel_id_to_info(channel_id: str):
    youtube = get_youtube()

    res = youtube.channels().list(
        part="snippet,statistics",
        id=channel_id
    ).execute()

    items = res.get("items", [])
    if not items:
        return None

    item = items[0]
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})

    custom_url = snippet.get("customUrl")
    handle = custom_url if custom_url and custom_url.startswith("@") else None

    return {
        "channel_id": channel_id,
        "channel_url": f"https://www.youtube.com/channel/{channel_id}",
        "title": snippet.get("title"),
        "handle": handle,
        "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url"),
        "subscriber_count": stats.get("subscriberCount"),
        "total_views": stats.get("viewCount"),
        "total_videos": stats.get("videoCount"),
    }


def video_to_channel_info(video_id: str):
    youtube = get_youtube()

    res = youtube.videos().list(
        part="snippet,liveStreamingDetails",
        id=video_id
    ).execute()

    items = res.get("items", [])
    if not items:
        return None

    item = items[0]
    snippet = item.get("snippet", {})
    live_details = item.get("liveStreamingDetails", {})

    channel_id = snippet.get("channelId")
    channel_info = channel_id_to_info(channel_id) if channel_id else {}

    return {
        "video_id": video_id,
        "video_url": f"https://www.youtube.com/watch?v={video_id}",
        "video_title": snippet.get("title"),
        "channel_id": channel_id,
        "channel_url": f"https://www.youtube.com/channel/{channel_id}" if channel_id else None,
        "handle": channel_info.get("handle") if channel_info else None,
        "channel_title": snippet.get("channelTitle"),
        "is_live_video": bool(live_details.get("actualStartTime") and not live_details.get("actualEndTime")),
    }


def find_live_by_handle(handle: str):
    """
    YouTube Data API search.list를 쓰지 않고,
    https://www.youtube.com/@handle/streams HTML에서 라이브 여부를 확인한다.
    비용: 0 YouTube API units
    """
    empty = {
        "is_live": False,
        "live_url": None,
        "live_video_id": None,
        "live_title": None
    }

    if not handle:
        return empty

    handle = handle.strip()
    if not handle.startswith("@"):
        handle = "@" + handle

    raw_handle = handle
    encoded_handle = "@" + quote(handle[1:], safe="")

    urls = [
        f"https://www.youtube.com/{raw_handle}/streams",
        f"https://www.youtube.com/{encoded_handle}/streams",
    ]

    for url in urls:
        try:
            html = fetch_html(url)

            # 사용자가 요청한 라이브 뱃지 클래스 기준
            live_badge_found = (
                "ytSpecAvatarShapeLiveBadgeText" in html
                or "ytSpecAvatarShapeLiveBadge" in html
                or '"style":"LIVE"' in html
                or '"text":"LIVE"' in html
                or '"label":"LIVE"' in html
            )

            if not live_badge_found:
                continue

            video_id = None

            # /streams 페이지의 첫 번째 videoId가 라이브일 가능성이 높음
            video_matches = re.findall(r'"videoId":"([^"]+)"', html)
            if video_matches:
                # 중복 제거하면서 첫 번째 값 사용
                seen = []
                for v in video_matches:
                    if v not in seen:
                        seen.append(v)
                video_id = seen[0] if seen else None

            live_title = None
            title_match = re.search(r'"title":\{"runs":\[\{"text":"([^"]+)"', html)
            if title_match:
                live_title = title_match.group(1)

            return {
                "is_live": True,
                "live_url": f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
                "live_video_id": video_id,
                "live_title": live_title
            }

        except Exception:
            continue

    return empty


def resolve_to_channel_id(value: str):
    input_type = detect_input_type(value)

    if input_type == "handle":
        handle = extract_handle(value)
        channel_id = handle_to_channel_id(handle)
        return channel_id, handle

    if input_type == "channel_id":
        channel_id = extract_channel_id(value)
        return channel_id, None

    if input_type in ["video_url", "short_video_url", "shorts_url", "live_url"]:
        video_id = extract_video_id(value)
        info = video_to_channel_info(video_id)
        if not info:
            return None, None
        return info.get("channel_id"), info.get("handle")

    return None, None


@app.route("/")
def home():
    return jsonify({
        "status": "server running",
        "service": "youtube link converter"
    })


@app.route("/api/test")
def test():
    return jsonify({
        "message": "API working",
        "youtube_api_key_loaded": bool(YOUTUBE_API_KEY)
    })


@app.route("/api/convert", methods=["POST"])
def convert():
    try:
        data = request.get_json(silent=True) or {}
        value = clean_input(data.get("input"))
        mode = data.get("mode", "all")

        if not value:
            return jsonify({"error": "input missing"}), 400

        input_type = detect_input_type(value)

        result = {
            "input": value,
            "mode": mode,
            "detected_type": input_type,
            "handle": None,
            "channel_id": None,
            "channel_url": None,
            "channel_title": None,
            "video_id": None,
            "video_url": None,
            "video_title": None,
            "is_live": False,
            "live_url": None,
            "live_video_id": None,
            "live_title": None,
            "message": None
        }

        if input_type == "unknown":
            result["message"] = "지원하지 않는 입력 형식입니다."
            return jsonify(result), 400

        # 영상 링크인 경우
        if input_type in ["video_url", "short_video_url", "shorts_url", "live_url"]:
            video_id = extract_video_id(value)
            video_info = video_to_channel_info(video_id)

            if not video_info:
                result["message"] = "영상을 찾을 수 없습니다."
                return jsonify(result), 404

            result.update({
                "video_id": video_info.get("video_id"),
                "video_url": video_info.get("video_url"),
                "video_title": video_info.get("video_title"),
                "channel_id": video_info.get("channel_id"),
                "channel_url": video_info.get("channel_url"),
                "handle": video_info.get("handle"),
                "channel_title": video_info.get("channel_title"),
            })

            if mode in ["all", "live"]:
                live_info = find_live_by_handle(video_info.get("handle"))
                result.update(live_info)
                if not live_info.get("is_live"):
                    result["message"] = "현재 방송 중인 라이브가 없습니다."

            return jsonify(result)

        # 핸들 또는 채널 링크인 경우
        channel_id, handle = resolve_to_channel_id(value)

        if not channel_id:
            result["message"] = "채널 정보를 찾을 수 없습니다."
            return jsonify(result), 404

        channel_info = channel_id_to_info(channel_id)

        if not channel_info:
            result["message"] = "채널 정보를 조회하지 못했습니다."
            return jsonify(result), 404

        result.update({
            "channel_id": channel_info.get("channel_id"),
            "channel_url": channel_info.get("channel_url"),
            "channel_title": channel_info.get("title"),
            "handle": handle or channel_info.get("handle"),
            "subscriber_count": channel_info.get("subscriber_count"),
            "total_views": channel_info.get("total_views"),
            "total_videos": channel_info.get("total_videos"),
        })

        if mode in ["all", "live"]:
            live_info = find_live_by_handle(result.get("handle"))
            result.update(live_info)
            if not live_info.get("is_live"):
                result["message"] = "현재 방송 중인 라이브가 없습니다."

        return jsonify(result)

    except Exception as e:
        return jsonify({
            "error": "server_error",
            "message": str(e)
        }), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
