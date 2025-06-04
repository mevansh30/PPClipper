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

# Environment Variables
YOUTUBE_API_KEY = os.getenv("YT_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")  # New admin key

# Database Config
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
                level TEXT DEFAULT 'unknown'
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL UNIQUE,
                name TEXT
            )
        ''')
        cursor.execute("SELECT COUNT(*) FROM channels")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO channels (channel_id, name) VALUES (?, ?)", ("UC4rnJFlsO1TC9FJMTWMPNdw", "Default Channel"))
        db.commit()
        print("Database initialized.")

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
    
    video_id = get_active_live_video_id()
    if not video_id:
        return "No live stream found.", 404

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
    message = f"\ud83c\udfaC New Clip by **{user}**{clip_title_display}: {clip_url}"

    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message})
    except Exception as e:
        print(f"Failed to send to Discord: {str(e)}")

    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO clips (title, user, clip_url, thumbnail_url, video_id, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (name, user, clip_url, thumbnail_url, video_id, now.isoformat())
        )
        db.commit()
    except sqlite3.Error as e:
        return "Failed to save clip to database.", 500

    return f"\u2705 Clip created{clip_title_display}: {clip_url}", 200

@app.route("/api/clips")
def get_clips():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 12, type=int)
    search_query = request.args.get('search', '').strip()
    level_filter = request.args.get('level', 'all').strip()

    offset = (page - 1) * limit
    db = get_db()
    cursor = db.cursor()

    query = "SELECT id, title, user, clip_url, thumbnail_url, video_id, timestamp FROM clips"
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

@app.route("/admin/add_channel", methods=["POST"])
def add_channel():
    if request.headers.get("x-api-key") != ADMIN_API_KEY:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json
    channel_id = data.get("channel_id")
    name = data.get("name", "Unnamed Channel")

    if not channel_id:
        return jsonify({"error": "channel_id is required"}), 400

    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute("INSERT INTO channels (channel_id, name) VALUES (?, ?)", (channel_id, name))
        db.commit()
        return jsonify({"status": "added", "channel_id": channel_id, "name": name}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "Channel ID already exists"}), 409
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/list_channels")
def list_channels():
    if request.headers.get("x-api-key") != ADMIN_API_KEY:
        return jsonify({"error": "Unauthorized"}), 403

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM channels")
    rows = cursor.fetchall()
    return jsonify([dict(row) for row in rows])

def get_active_live_video_id():
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT channel_id FROM channels")
        channels = cursor.fetchall()
        for row in channels:
            channel_id = row["channel_id"]
            url = (
                f"https://www.googleapis.com/youtube/v3/search?part=snippet"
                f"&channelId={channel_id}&eventType=live&type=video&key={YOUTUBE_API_KEY}"
            )
            r = requests.get(url, timeout=5)
            r.raise_for_status()
            items = r.json().get('items', [])
            if items:
                return items[0]['id']['videoId']
        return None
    except Exception as e:
        print(f"Error getting live video: {e}")
        return None

def get_stream_start_time(video_id):
    try:
        url = (
            f"https://www.googleapis.com/youtube/v3/videos?part=liveStreamingDetails&id={video_id}&key={YOUTUBE_API_KEY}"
        )
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        start_time_str = r.json()["items"][0]["liveStreamingDetails"]["actualStartTime"]
        return datetime.datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
    except Exception as e:
        print(f"Error getting stream start time: {e}")
        return None

with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
