from flask import Flask, request
import datetime
import requests
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

YOUTUBE_API_KEY = os.getenv("YT_API_KEY")
CHANNEL_ID = os.getenv("YT_CHANNEL_ID")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

@app.route("/clip")
def create_clip():
    user = request.args.get('user', 'someone')

    # 1. Get current live video ID from channel
    video_id = get_active_live_video_id()
    if not video_id:
        return "No live stream found."

    # 2. Get stream start time
    stream_start = get_stream_start_time(video_id)
    if not stream_start:
        return "Couldn't fetch stream start time."

    # 3. Calculate timestamp for 35 seconds ago
    now = datetime.datetime.utcnow()
    clip_time = now - datetime.timedelta(seconds=35)
    seconds_since_start = int((clip_time - stream_start).total_seconds())
    seconds_since_start = max(0, seconds_since_start)

    # 4. Generate YouTube clip link
    clip_url = f"https://www.youtube.com/watch?v={video_id}&t={seconds_since_start}s"

    # 5. Send to Discord
    message = f"🎬 New Clip by **{user}**: {clip_url}"
    requests.post(DISCORD_WEBHOOK_URL, json={"content": message})

    return f"Clip created: {clip_url}"

def get_active_live_video_id():
    url = (
        f"https://www.googleapis.com/youtube/v3/search?part=snippet"
        f"&channelId={CHANNEL_ID}&eventType=live&type=video&key={YOUTUBE_API_KEY}"
    )
    r = requests.get(url)
    data = r.json()
    items = data.get('items', [])
    if items:
        return items[0]['id']['videoId']
    return None

def get_stream_start_time(video_id):
    url = f"https://www.googleapis.com/youtube/v3/videos?part=liveStreamingDetails&id={video_id}&key={YOUTUBE_API_KEY}"
    r = requests.get(url)
    data = r.json()
    try:
        start_time = data["items"][0]["liveStreamingDetails"]["actualStartTime"]
        return datetime.datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    except Exception:
        return None

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)