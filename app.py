from flask import Flask, request, jsonify, render_template, g
import datetime
import requests
import os
from dotenv import load_dotenv
import pytz
import sqlite3
from flask_cors import CORS
from functools import wraps # For decorator

load_dotenv()

app = Flask(__name__)
CORS(app) # Enable CORS for all routes

# Environment Variables
YOUTUBE_API_KEY = os.getenv("YT_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")

# Database Config
DATABASE = 'clips.db'

# --- Database Helper Functions ---
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row # Access columns by name
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
        # Create clips table
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
        # Create channels table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL UNIQUE,
                name TEXT
            )
        ''')
        # Add a default channel if none exist
        cursor.execute("SELECT COUNT(*) FROM channels")
        if cursor.fetchone()[0] == 0:
            try:
                cursor.execute("INSERT INTO channels (channel_id, name) VALUES (?, ?)", 
                               ("UC4rnJFlsO1TC9FJMTWMPNdw", "Default Example Channel"))
                print("Inserted default channel.")
            except sqlite3.IntegrityError:
                print("Default channel already exists or another issue.") # Should not happen with COUNT(*) check
        db.commit()
        print("Database initialized.")

# --- Admin Auth Decorator ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not ADMIN_API_KEY:
            print("ADMIN_API_KEY is not set in the environment.")
            return jsonify({"error": "Server configuration error: Admin API key not set."}), 500
        
        api_key = request.headers.get("x-api-key")
        if api_key == ADMIN_API_KEY:
            return f(*args, **kwargs)
        else:
            print(f"Unauthorized admin access attempt. Provided key: {api_key}")
            return jsonify({"error": "Unauthorized: Invalid or missing API key."}), 403
    return decorated_function

# --- YouTube API Helper Functions ---
def get_active_live_video_id():
    if not YOUTUBE_API_KEY:
        print("Error: YOUTUBE_API_KEY is not configured.")
        return None
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT channel_id FROM channels")
        channels = cursor.fetchall()
        if not channels:
            print("No channels configured in the database to check for live streams.")
            return None

        for row in channels:
            channel_id = row["channel_id"]
            url = (
                f"https://www.googleapis.com/youtube/v3/search?part=snippet"
                f"&channelId={channel_id}&eventType=live&type=video&key={YOUTUBE_API_KEY}"
            )
            print(f"Checking for live video on channel: {channel_id}")
            r = requests.get(url, timeout=10) # Increased timeout
            r.raise_for_status() # Will raise an HTTPError for bad responses (4xx or 5xx)
            data = r.json()
            items = data.get('items', [])
            if items:
                video_id = items[0]['id']['videoId']
                print(f"Found live video: {video_id} on channel {channel_id}")
                return video_id
        print("No active live streams found on configured channels.")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Error fetching live video from YouTube API: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred in get_active_live_video_id: {e}")
        return None

def get_stream_start_time(video_id):
    if not YOUTUBE_API_KEY:
        print("Error: YOUTUBE_API_KEY is not configured.")
        return None
    if not video_id:
        print("Error: video_id is required to get stream start time.")
        return None
    try:
        url = (
            f"https://www.googleapis.com/youtube/v3/videos?part=liveStreamingDetails&id={video_id}&key={YOUTUBE_API_KEY}"
        )
        print(f"Fetching stream start time for video: {video_id}")
        r = requests.get(url, timeout=10) # Increased timeout
        r.raise_for_status()
        data = r.json()
        items = data.get("items", [])
        if items and "liveStreamingDetails" in items[0] and "actualStartTime" in items[0]["liveStreamingDetails"]:
            start_time_str = items[0]["liveStreamingDetails"]["actualStartTime"]
            # Convert ISO 8601 string to datetime object
            start_time = datetime.datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            print(f"Stream start time: {start_time}")
            return start_time
        else:
            print(f"Could not find live streaming details or actualStartTime for video {video_id}. Response: {data}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error fetching stream start time from YouTube API: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred in get_stream_start_time: {e}")
        return None

# --- Application Routes ---
@app.route("/")
def home():
    return "ClipSync_V2 is alive! API endpoints are available.", 200

@app.route("/ping")
def ping():
    return "pong", 200

# This endpoint is for user-triggered clip creation (e.g., via a bookmarklet or direct call)
# The frontend button for this has been removed as per request.
@app.route("/clip")
def create_clip():
    user = request.args.get('user', 'someone')
    name = request.args.get('name', '').strip() # Clip title part
    
    if not YOUTUBE_API_KEY:
        return jsonify({"error": "Server configuration error: YouTube API key not set."}), 500

    video_id = get_active_live_video_id()
    if not video_id:
        return jsonify({"error": "No active live stream found on configured channels."}), 404

    stream_start_utc = get_stream_start_time(video_id)
    if not stream_start_utc:
        return jsonify({"error": "Could not fetch stream start time for the live video."}), 500

    now_utc = datetime.datetime.now(pytz.utc)
    # Ensure stream_start_utc is timezone-aware (it should be from fromisoformat)
    if stream_start_utc.tzinfo is None:
         stream_start_utc = pytz.utc.localize(stream_start_utc)


    # Calculate time for the clip (e.g., 35 seconds before "now")
    # This logic might need adjustment based on how "clipping" is intended to work
    clip_time_utc = now_utc - datetime.timedelta(seconds=35) 

    if clip_time_utc < stream_start_utc:
        print(f"Clip time ({clip_time_utc}) is before stream start time ({stream_start_utc}). Clipping from start.")
        seconds_since_start = 0
    else:
        seconds_since_start = int((clip_time_utc - stream_start_utc).total_seconds())
    
    # Standard YouTube URL and Thumbnail
    clip_url = f"https://www.youtube.com/watch?v={video_id}&t={seconds_since_start}s"
    thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg" # mqdefault or hqdefault
    
    actual_clip_title = name if name else f"Clip by {user}"

    # Send to Discord if URL is configured
    if DISCORD_WEBHOOK_URL:
        discord_message_title = f" [{name}]" if name else ""
        message = f"\ud83c\udfaC New Clip by **{user}**{discord_message_title}: {clip_url}"
        try:
            print(f"Sending to Discord: {message}")
            requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=5)
        except requests.exceptions.RequestException as e:
            print(f"Failed to send clip notification to Discord: {str(e)}")
        except Exception as e:
            print(f"An unexpected error occurred while sending to Discord: {str(e)}")


    # Save to database
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO clips (title, user, clip_url, thumbnail_url, video_id, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (actual_clip_title, user, clip_url, thumbnail_url, video_id, now_utc.isoformat())
        )
        db.commit()
        print(f"Clip saved to DB: {actual_clip_title}")
        return jsonify({
            "message": "Clip created successfully",
            "clip_url": clip_url,
            "title": actual_clip_title,
            "user": user
        }), 200
    except sqlite3.Error as e:
        print(f"Database error saving clip: {e}")
        db.rollback() # Rollback on error
        return jsonify({"error": "Failed to save clip to database."}), 500
    except Exception as e:
        print(f"An unexpected error occurred while saving clip to DB: {e}")
        db.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500


@app.route("/api/clips")
def get_clips_api():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 12, type=int)
    search_query = request.args.get('search', '').strip()
    # level_filter = request.args.get('level', 'all').strip() # Level filter not fully implemented in this version

    offset = (page - 1) * limit
    
    db = get_db()
    cursor = db.cursor()

    base_query = "FROM clips"
    conditions = []
    params = []

    if search_query:
        conditions.append("(title LIKE ? OR user LIKE ?)")
        # For SQLite, parameters for LIKE should include %
        params.extend([f"%{search_query}%", f"%{search_query}%"])

    # if level_filter != 'all': # Example for future filter
    #     conditions.append("level = ?")
    #     params.append(level_filter)

    where_clause = ""
    if conditions:
        where_clause = " WHERE " + " AND ".join(conditions)

    count_query = "SELECT COUNT(*) " + base_query + where_clause
    
    # Parameters for count query are same as for data query, excluding limit/offset
    count_params = tuple(params) 
    
    cursor.execute(count_query, count_params)
    total_clips = cursor.fetchone()[0]

    data_query = "SELECT id, title, user, clip_url, thumbnail_url, video_id, timestamp " + \
                 base_query + where_clause + " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    
    # Add limit and offset to params for data query
    params.extend([limit, offset])
    
    cursor.execute(data_query, tuple(params))
    clips_data = cursor.fetchall()

    clips_list = [dict(clip) for clip in clips_data] # Convert rows to dicts

    total_pages = max(1, (total_clips + limit - 1) // limit) # Ensure at least 1 page

    return jsonify({
        "clips": clips_list,
        "pagination": {
            "currentPage": page,
            "itemsPerPage": limit,
            "totalItems": total_clips,
            "totalPages": total_pages
        }
    })

# --- Admin API Routes for Channels ---
@app.route("/admin/channels", methods=["GET"])
@admin_required
def list_channels_api():
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("SELECT id, channel_id, name FROM channels ORDER BY name")
        channels = cursor.fetchall()
        return jsonify([dict(row) for row in channels]), 200
    except sqlite3.Error as e:
        print(f"Database error listing channels: {e}")
        return jsonify({"error": "Failed to retrieve channels from database."}), 500

@app.route("/admin/channels", methods=["POST"])
@admin_required
def add_channel_api():
    data = request.json
    if not data:
        return jsonify({"error": "Request body must be JSON."}), 400
        
    channel_id = data.get("channel_id")
    name = data.get("name") # Optional, can be None or empty

    if not channel_id:
        return jsonify({"error": "channel_id is required."}), 400
    
    # Basic validation for YouTube channel ID format (UC followed by 22 chars)
    if not (channel_id.startswith("UC") and len(channel_id) == 24):
        print(f"Invalid YouTube Channel ID format: {channel_id}")
        # return jsonify({"error": "Invalid YouTube Channel ID format. Must start with 'UC' and be 24 characters long."}), 400

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("INSERT INTO channels (channel_id, name) VALUES (?, ?)", (channel_id, name))
        db.commit()
        new_channel_id = cursor.lastrowid
        print(f"Channel added: ID={new_channel_id}, YT_ID={channel_id}, Name={name}")
        return jsonify({
            "message": "Channel added successfully.",
            "id": new_channel_id, # Return the database ID
            "channel_id": channel_id,
            "name": name
        }), 201
    except sqlite3.IntegrityError: # Handles UNIQUE constraint violation for channel_id
        db.rollback()
        print(f"Attempt to add duplicate channel_id: {channel_id}")
        return jsonify({"error": f"Channel with YouTube ID '{channel_id}' already exists."}), 409
    except sqlite3.Error as e:
        db.rollback()
        print(f"Database error adding channel: {e}")
        return jsonify({"error": "Failed to add channel to database."}), 500

@app.route("/admin/channels/<int:channel_db_id>", methods=["DELETE"])
@admin_required
def remove_channel_api(channel_db_id):
    db = get_db()
    cursor = db.cursor()
    try:
        # Check if channel exists before deleting
        cursor.execute("SELECT id FROM channels WHERE id = ?", (channel_db_id,))
        channel = cursor.fetchone()
        if channel is None:
            return jsonify({"error": f"Channel with database ID {channel_db_id} not found."}), 404
            
        cursor.execute("DELETE FROM channels WHERE id = ?", (channel_db_id,))
        db.commit()
        if cursor.rowcount > 0:
            print(f"Channel removed: DB_ID={channel_db_id}")
            return jsonify({"message": f"Channel (DB ID: {channel_db_id}) removed successfully."}), 200
        else:
            # This case should be caught by the check above, but as a fallback
            print(f"Channel not found for deletion: DB_ID={channel_db_id}")
            return jsonify({"error": f"Channel with database ID {channel_db_id} not found or already deleted."}), 404
    except sqlite3.Error as e:
        db.rollback()
        print(f"Database error removing channel: {e}")
        return jsonify({"error": "Failed to remove channel from database."}), 500

# Initialize DB on first request if not already done
with app.app_context():
    init_db()

if __name__ == "__main__":
    # Make sure ADMIN_API_KEY is set before running for admin features to work
    if not ADMIN_API_KEY:
        print("WARNING: ADMIN_API_KEY is not set. Admin functionalities will be locked.")
    if not YOUTUBE_API_KEY:
        print("WARNING: YT_API_KEY is not set. YouTube related functionalities (creating clips, fetching live status) will fail.")
    
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)), debug=os.getenv("FLASK_DEBUG", "False").lower() == "true")
