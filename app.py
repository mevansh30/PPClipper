from flask import Flask, request, jsonify, render_template, g
import datetime
import requests
import os
from dotenv import load_dotenv
import pytz
import sqlite3 # Import sqlite3 for database operations

load_dotenv()

app = Flask(__name__)

# Environment variables
YOUTUBE_API_KEY = os.getenv("YT_API_KEY")
CHANNEL_ID = os.getenv("YT_CHANNEL_ID")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

# Database configuration
DATABASE = 'clips.db'

def get_db():
    """Establishes a database connection or returns the existing one."""
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row # This allows access to columns by name
    return db

@app.teardown_appcontext
def close_connection(exception):
    """Closes the database connection at the end of the request."""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    """Initializes the database by creating the clips table if it doesn't exist."""
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
                level TEXT DEFAULT 'unknown' -- Added for potential filtering
            )
        ''')
        db.commit()
    print("Database initialized successfully.")

# Route for the home page
@app.route("/")
def home():
    """Home route for the application."""
    return "ClipSync_V2 is alive! Go to /clips_grid to see the clips.", 200

# Route for health check
@app.route("/ping")
def ping():
    """Health check endpoint."""
    return "pong", 200

# Route to display the clips grid frontend
@app.route("/clips_grid")
def show_clip_grid():
    """Renders the HTML page for the clip grid."""
    return render_template("clip_grid.html")

# Route to create a new clip
@app.route("/clip")
def create_clip():
    """
    Creates a new clip, generates its URL and thumbnail,
    sends it to Discord, and saves it to the database.
    """
    user = request.args.get('user', 'someone')
    name = request.args.get('name', '').strip()

    # Step 1: Get current live video ID
    video_id = get_active_live_video_id()
    if not video_id:
        return "No live stream found.", 404

    # Step 2: Get stream start time
    stream_start = get_stream_start_time(video_id)
    if not stream_start:
        return "Couldn't fetch stream start time.", 500

    # Step 3: Calculate timestamp
    now = datetime.datetime.now(pytz.UTC)
    # Clip 35 seconds prior to 'now'
    clip_time = now - datetime.timedelta(seconds=35)

    try:
        stream_start = stream_start.astimezone(pytz.UTC)
        seconds_since_start = int((clip_time - stream_start).total_seconds())
        # Ensure seconds_since_start is not negative
        seconds_since_start = max(0, seconds_since_start)
    except Exception as e:
        print(f"Time math failed: {str(e)}")
        return f"Time calculation failed: {str(e)}", 500

    # Step 4: Generate YouTube timestamped link and thumbnail URL
    # The format 'https://www.youtube.com/watch?v={video_id}&t={seconds_since_start}s'
    # is non-standard for direct YouTube embeds. A standard YouTube link is 'https://www.youtube.com/watch?v={video_id}&t={seconds_since_start}s'
    # For thumbnail, use the standard YouTube thumbnail URL format.
    clip_url = f"https://www.youtube.com/watch?v={video_id}&t={seconds_since_start}s"
    # YouTube provides various thumbnail sizes. 'mqdefault.jpg' is medium quality.
    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"

    # Step 5: Format and send to Discord
    clip_title_display = f" [{name}]" if name else ""
    message = f"🎬 New Clip by **{user}**{clip_title_display}: {clip_url}"

    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message})
        print(f"Sent to Discord: {message}")
    except Exception as e:
        print(f"Failed to send to Discord: {str(e)}")
        # Continue even if Discord fails, as the clip should still be saved
        # return f"Failed to send to Discord: {str(e)}", 500

    # Step 6: Save clip to database
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO clips (title, user, clip_url, thumbnail_url, video_id, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (name, user, clip_url, thumbnail_url, video_id, now.isoformat())
        )
        db.commit()
        print(f"Clip saved to DB: {name} by {user}")
    except sqlite3.Error as e:
        print(f"Failed to save clip to database: {e}")
        return "Failed to save clip to database.", 500

    return f"✅ Clip created{clip_title_display}: {clip_url}", 200

# New API endpoint for fetching clips
@app.route("/api/clips")
def get_clips():
    """
    API endpoint to fetch clips with pagination, search, and filtering.
    Query parameters:
    - page: current page number (default: 1)
    - limit: items per page (default: 12)
    - search: search term for clip title or user (optional)
    - level: filter by level (optional, currently not used in DB but can be extended)
    """
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 12, type=int)
    search_query = request.args.get('search', '').strip()
    level_filter = request.args.get('level', 'all').strip() # Placeholder for future use

    offset = (page - 1) * limit
    db = get_db()
    cursor = db.cursor()

    # Base query
    query = "SELECT id, title, user, clip_url, thumbnail_url, video_id, timestamp FROM clips"
    count_query = "SELECT COUNT(*) FROM clips"
    conditions = []
    params = []

    # Add search condition
    if search_query:
        conditions.append("(title LIKE ? OR user LIKE ?)")
        params.extend([f"%{search_query}%", f"%{search_query}%"])

    # Add level filter condition (if 'level' column is populated in DB)
    # if level_filter != 'all':
    #     conditions.append("level = ?")
    #     params.append(level_filter)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
        count_query += " WHERE " + " AND ".join(conditions)

    # Order by timestamp in descending order (latest clips first)
    query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    # Execute count query
    cursor.execute(count_query, params[:-2]) # Exclude limit and offset for count
    total_clips = cursor.fetchone()[0]

    # Execute main query
    cursor.execute(query, params)
    clips_data = cursor.fetchall()

    # Convert Row objects to dictionaries for JSON serialization
    clips_list = []
    for clip in clips_data:
        clip_dict = dict(clip)
        # Add a placeholder channelIcon if not stored in DB
        clip_dict['channelIcon'] = f"https://via.placeholder.com/24.png?text={clip_dict['user'][0].upper()}"
        clips_list.append(clip_dict)

    total_pages = (total_clips + limit - 1) // limit # Ceiling division
    total_pages = max(1, total_pages) # Ensure at least 1 page

    response = {
        "clips": clips_list,
        "pagination": {
            "currentPage": page,
            "itemsPerPage": limit,
            "totalItems": total_clips,
            "totalPages": total_pages
        }
    }
    return jsonify(response)

# Helper functions (unchanged)
def get_active_live_video_id():
    """Fetches the ID of the current live YouTube video for the channel."""
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
        print("No live stream found in YouTube API response.")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Error getting live video (request failed): {e}")
        return None
    except KeyError:
        print("Error: 'items' or 'id' or 'videoId' key not found in YouTube API response.")
        print(f"API Response: {data}")
        return None
    except Exception as e:
        print(f"Unexpected error getting live video: {e}")
        return None

def get_stream_start_time(video_id):
    """Fetches the actual start time of a YouTube live stream."""
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
    except requests.exceptions.RequestException as e:
        print(f"Error getting stream start time (request failed): {e}")
        return None
    except KeyError:
        print("Error: 'liveStreamingDetails' or 'actualStartTime' key not found in YouTube API response.")
        print(f"API Response: {data}")
        return None
    except Exception as e:
        print(f"Unexpected error getting start time: {e}")
        return None

# Ensure database is initialized when the app starts
with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True) # Set debug=True for development
