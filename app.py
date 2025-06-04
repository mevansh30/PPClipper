from flask import Flask, request, jsonify, render_template, g
import datetime
import requests
import os
from dotenv import load_dotenv
import pytz
import sqlite3
from flask_cors import CORS

load_dotenv()

app = Flask(__name__)
CORS(app)

YOUTUBE_API_KEY = os.getenv("YT_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

# Use DB to manage multiple channels
DATABASE = 'clips.db'

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                user TEXT NOT NULL,
                clip_url TEXT NOT NULL,
                thumbnail_url TEXT NOT NULL,
                video_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                channel_id TEXT DEFAULT NULL,
                level TEXT DEFAULT 'unknown'
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL UNIQUE,
                name TEXT DEFAULT NULL
            )
        ''')
        db.commit()
        print("Database initialized successfully.")

def get_all_channel_ids():
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT channel_id FROM channels")
    return [row['channel_id'] for row in cursor.fetchall()]

@app.route("/")
def home():
    return "ClipSync_V2 is alive! Go to /clips_grid to see the clips.", 200

@app.route("/ping")
def ping():
    return "pong", 200

@app.route("/clips_grid")
def show_clip_grid():
    return render_template("clip_grid.html")

@app.route("/clip")
def create_clip():
    user = request.args.get('user', 'someone')
    name = request.args.get('name', '').strip()

    result = get_active_live_video_id()
    if not result:
        return "No live stream found.", 404

    video_id, channel_id_used = result
    stream_start = get_stream_start_time(video_id)
    if not stream_start:
        return "Couldn't fetch stream start time.", 500

    now = datetime.datetime.now(pytz.UTC)
    clip_time = now - datetime.timedelta(seconds=35)

    try:
        stream_start = stream_start.astimezone(pytz.UTC)
        seconds_since_start = int((clip_time - stream_start).total_seconds())
        seconds_since_start = max(0, seconds_since_start)
    except Exception as e:
        return f"Time calculation failed: {str(e)}", 500

    clip_url = f"https://www.youtube.com/watch?v={video_id}&t={seconds_since_start}s"
    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"
    clip_title_display = f" [{name}]" if name else ""
    message = f"\U0001F3AC New Clip by **{user}**{clip_title_display}: {clip_url}"

    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message})
    except Exception as e:
        print(f"Failed to send to Discord: {str(e)}")

    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO clips (title, user, clip_url, thumbnail_url, video_id, timestamp, channel_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, user, clip_url, thumbnail_url, video_id, now.isoformat(), channel_id_used)
        )
        db.commit()
    except sqlite3.Error as e:
        print(f"DB Error: {e}")
        return "Failed to save clip to database.", 500

    return f"\u2705 Clip created{clip_title_display}: {clip_url}", 200

@app.route("/api/clips")
def get_clips():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 12, type=int)
    search_query = request.args.get('search', '').strip()
    offset = (page - 1) * limit

    db = get_db()
    cursor = db.cursor()

    query = "SELECT id, title, user, clip_url, thumbnail_url, video_id, timestamp, channel_id FROM clips"
    count_query = "SELECT COUNT(*) FROM clips"
    conditions = []
    params = []

    if search_query:
        conditions.append("(title LIKE ? OR user LIKE ?)")
        params.extend([f"%{search_query}%", f"%{search_query}%"])

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
        count_query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(count_query, params[:-2])
    total_clips = cursor.fetchone()[0]

    cursor.execute(query, params)
    clips_data = cursor.fetchall()

    clips_list = []
    for clip in clips_data:
        clip_dict = dict(clip)
        clip_dict['channelIcon'] = f"https://via.placeholder.com/24.png?text={clip_dict['user'][0].upper()}"
        clips_list.append(clip_dict)

    total_pages = max(1, (total_clips + limit - 1) // limit)

    return jsonify({
        "clips": clips_list,
        "pagination": {
            "currentPage": page,
            "itemsPerPage": limit,
            "totalItems": total_clips,
            "totalPages": total_pages
        }
    })

def get_active_live_video_id():
    for channel_id in get_all_channel_ids():
        try:
            url = (
                f"https://www.googleapis.com/youtube/v3/search?part=snippet"
                f"&channelId={channel_id}&eventType=live&type=video&key={YOUTUBE_API_KEY}"
            )
            r = requests.get(url, timeout=5)
            r.raise_for_status()
            data = r.json()
            items = data.get('items', [])
            if items:
                return items[0]['id']['videoId'], channel_id
        except Exception as e:
            print(f"Error fetching live video for {channel_id}: {e}")
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
        print(f"Error fetching stream start time: {e}")
        return None

with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
