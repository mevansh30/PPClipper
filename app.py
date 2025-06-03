from flask import Flask, request
import datetime
import requests
import os
from dotenv import load_dotenv
import pytz  # for timezone-aware datetime

load_dotenv()

app = Flask(__name__)

YOUTUBE_API_KEY = os.getenv("YT_API_KEY")
CHANNEL_ID = os.getenv("YT_CHANNEL_ID")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

@app.route("/")
def home():
    return "ClipSync_V1 is alive!", 200

@app.route("/ping")
def ping():
    return "pong", 200

@app.route("/clip")
def create_clip():
    user = request.args.get('user', 'someone')
    name = request.args.get('name', '').strip()

    # Step 1: Get current live video ID
    video_id = get_active_live_video_id()
    if not video_id:
        return "No live stream found."

    # Step 2: Get stream start time
    stream_start = get_stream_start_time(video_id)
    if not stream_start:
        return "Couldn't fetch stream start time."

    # Step 3: Calculate timestamp
    now = datetime.datetime.now(pytz.UTC)
    clip_time = now - datetime.timedelta(seconds=35)

    try:
        stream_start = stream_start.astimezone(pytz.UTC)
        seconds_since_start = int((clip_time - stream_start).total_seconds())
        seconds_since_start = max(0, seconds_since_start)
    except Exception as e:
        return f"Time math failed: {str(e)}"

    # Step 4: Generate YouTube timestamped link
    clip_url = f"https://www.youtube.com/watch?v={video_id}&t={seconds_since_start}s"

    # Step 5: Format and send to Discord
    clip_title = f" [{name}]" if name else ""
    message = f"🎬 New Clip by **{user}**{clip_title}: {clip_url}"

    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message})
    except Exception as e:
        return f"Failed to send to Discord: {str(e)}"

    return f"✅ Clip created{clip_title}: {clip_url}"


def get_active_live_video_id():
    try:
        url = (
            f"https://www.googleapis.com/youtube/v3/search?part=snippet"
            f"&channelId={CHANNEL_ID}&eventType=live&type=video&key={YOUTUBE_API_KEY}"
        )
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        data = r.json()
        items = data.get('items', [])
        if items:
            return items[0]['id']['videoId']
        return None
    except Exception as e:
        print("Error getting live video:", e)
        return None

def get_stream_start_time(video_id):
    try:
        url = (
            f"https://www.googleapis.com/youtube/v3/videos?"
            f"part=liveStreamingDetails&id={video_id}&key={YOUTUBE_API_KEY}"
        )
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        data = r.json()
        start_time_str = data["items"][0]["liveStreamingDetails"]["actualStartTime"]
        return datetime.datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
    except Exception as e:
        print("Error getting start time:", e)
        return None

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
