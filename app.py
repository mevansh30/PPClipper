import os
import datetime
import requests
import pytz
from functools import wraps
from dotenv import load_dotenv

from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError

# --- Initialization ---
load_dotenv()

app = Flask(__name__)
CORS(app)

# --- Environment Variables ---
YOUTUBE_API_KEY = os.getenv("YT_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

# --- Database Configuration (PostgreSQL with SQLAlchemy) ---
if not DATABASE_URL:
    raise RuntimeError("FATAL: DATABASE_URL is not set. The app cannot connect to the database.")

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- Database Models ---
# These classes define the structure of your database tables.

class Clip(db.Model):
    __tablename__ = 'clips'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    user = db.Column(db.String(100), nullable=False)
    clip_url = db.Column(db.String(500), nullable=False)
    thumbnail_url = db.Column(db.String(500), nullable=False)
    video_id = db.Column(db.String(100), nullable=False)
    timestamp = db.Column(db.String(100), nullable=False)
    level = db.Column(db.String(50), default='unknown')

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

class Channel(db.Model):
    __tablename__ = 'channels'
    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(255), nullable=True)
    
    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

# --- Database Initialization Function ---
def init_db():
    with app.app_context():
        print("Initializing database schema...")
        db.create_all() # Creates tables from the models above if they don't exist
        
        # Add a default channel if the table is empty
        if not Channel.query.first():
            print("No channels found, adding default channel.")
            default_channel = Channel(channel_id="UC4rnJFlsO1TC9FJMTWMPNdw", name="Default Example Channel")
            db.session.add(default_channel)
            db.session.commit()
            print("Default channel added.")
        else:
            print("Database already contains channels.")
        print("Database initialization complete.")

# --- Admin Auth Decorator (Unchanged) ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not ADMIN_API_KEY:
            print("CRITICAL: ADMIN_API_KEY is not set. All admin routes are blocked.")
            return jsonify({"error": "Server configuration error: Admin API key not set."}), 500
        
        api_key = request.headers.get("x-api-key")
        if api_key == ADMIN_API_KEY:
            return f(*args, **kwargs)
        else:
            print(f"Unauthorized admin access attempt. Provided key: {api_key}")
            return jsonify({"error": "Unauthorized: Invalid or missing API key."}), 403
    return decorated_function

# --- YouTube API Helper Functions (Updated to use SQLAlchemy) ---
def get_active_live_video_id():
    if not YOUTUBE_API_KEY:
        print("Error: YOUTUBE_API_KEY is not configured.")
        return None
    try:
        # Use SQLAlchemy to get all channels from the database
        channels = Channel.query.all()
        if not channels:
            print("No channels configured in the database to check for live streams.")
            return None

        for channel in channels:
            url = (
                f"https://www.googleapis.com/youtube/v3/search?part=snippet"
                f"&channelId={channel.channel_id}&eventType=live&type=video&key={YOUTUBE_API_KEY}"
            )
            print(f"Checking for live video on channel: {channel.channel_id}")
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            data = r.json()
            items = data.get('items', [])
            if items:
                video_id = items[0]['id']['videoId']
                print(f"Found live video: {video_id} on channel {channel.channel_id}")
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
    # This function did not interact with our database, so it remains unchanged.
    if not YOUTUBE_API_KEY or not video_id:
        print(f"Error: YOUTUBE_API_KEY configured: {bool(YOUTUBE_API_KEY)}, video_id provided: {bool(video_id)}")
        return None
    try:
        url = f"https://www.googleapis.com/youtube/v3/videos?part=liveStreamingDetails&id={video_id}&key={YOUTUBE_API_KEY}"
        print(f"Fetching stream start time for video: {video_id}")
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        items = data.get("items", [])
        if items and "liveStreamingDetails" in items[0] and "actualStartTime" in items[0]["liveStreamingDetails"]:
            start_time_str = items[0]["liveStreamingDetails"]["actualStartTime"]
            return datetime.datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
        else:
            print(f"Could not find live streaming details for video {video_id}. Response: {data}")
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

@app.route("/clip")
def create_clip():
    user = request.args.get('user', 'someone')
    name = request.args.get('name', '').strip()
    
    if not YOUTUBE_API_KEY:
        return jsonify({"error": "Server configuration error: YouTube API key not set."}), 500

    video_id = get_active_live_video_id()
    if not video_id:
        return jsonify({"error": "No active live stream found on configured channels."}), 404

    stream_start_utc = get_stream_start_time(video_id)
    if not stream_start_utc:
        return jsonify({"error": "Could not fetch stream start time for the live video."}), 500

    now_utc = datetime.datetime.now(pytz.utc)
    clip_time_utc = now_utc - datetime.timedelta(seconds=35) 

    seconds_since_start = 0
    if clip_time_utc > stream_start_utc:
        seconds_since_start = int((clip_time_utc - stream_start_utc).total_seconds())
    
    clip_url = f"https://www.youtube.com/watch?v={video_id}&t={seconds_since_start}s"
    thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
    actual_clip_title = name if name else f"Clip by {user}"

    # --- Discord Notification Logic (Unchanged) ---
    if DISCORD_WEBHOOK_URL:
        discord_message_title = f" [{name}]" if name else ""
        message = f"🎬 New Clip by **{user}**{discord_message_title}: {clip_url}"
        try:
            print("Attempting to send notification to Discord...")
            response = requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=5)
            response.raise_for_status()
            print("Successfully sent notification to Discord.")
        except requests.exceptions.RequestException as e:
            print(f"ERROR sending to Discord. Status: {e.response.status_code if e.response else 'N/A'}. Error: {e}")
        except Exception as e:
            print(f"ERROR: An unexpected error occurred while sending to Discord: {e}")
    else:
        print("INFO: DISCORD_WEBHOOK_URL not set. Skipping Discord notification.")

    # --- Save to PostgreSQL Database ---
    try:
        new_clip = Clip(
            title=actual_clip_title,
            user=user,
            clip_url=clip_url,
            thumbnail_url=thumbnail_url,
            video_id=video_id,
            timestamp=now_utc.isoformat()
        )
        db.session.add(new_clip)
        db.session.commit()
        print(f"Clip saved to DB: {actual_clip_title}")
        return jsonify({
            "message": "Clip created successfully",
            "clip_url": clip_url,
            "title": actual_clip_title,
            "user": user
        }), 200
    except Exception as e:
        db.session.rollback()
        print(f"Database error saving clip: {e}")
        return jsonify({"error": "Failed to save clip to database."}), 500

@app.route("/api/clips")
def get_clips_api():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 12, type=int)
    search_query = request.args.get('search', '').strip()
    
    # Base query
    query = Clip.query

    # Apply search filter if provided
    if search_query:
        search_term = f"%{search_query}%"
        query = query.filter(db.or_(Clip.title.ilike(search_term), Clip.user.ilike(search_term)))

    # Order by most recent
    query = query.order_by(Clip.timestamp.desc())

    # Paginate the results
    pagination = query.paginate(page=page, per_page=limit, error_out=False)
    clips_data = [clip.to_dict() for clip in pagination.items]

    return jsonify({
        "clips": clips_data,
        "pagination": {
            "currentPage": pagination.page,
            "itemsPerPage": pagination.per_page,
            "totalItems": pagination.total,
            "totalPages": pagination.pages
        }
    })

# --- Admin API Routes for Channels (Updated for SQLAlchemy) ---
@app.route("/admin/channels", methods=["GET"])
@admin_required
def list_channels_api():
    try:
        channels = Channel.query.order_by(Channel.name).all()
        return jsonify([ch.to_dict() for ch in channels]), 200
    except Exception as e:
        print(f"Database error listing channels: {e}")
        return jsonify({"error": "Failed to retrieve channels from database."}), 500

@app.route("/admin/channels", methods=["POST"])
@admin_required
def add_channel_api():
    data = request.json
    if not data or 'channel_id' not in data:
        return jsonify({"error": "channel_id is required."}), 400
    
    channel_id = data.get("channel_id").strip()
    name = data.get("name", "").strip() or None

    try:
        new_channel = Channel(channel_id=channel_id, name=name)
        db.session.add(new_channel)
        db.session.commit()
        print(f"Channel added: ID={new_channel.id}, YT_ID={channel_id}")
        return jsonify(new_channel.to_dict()), 201
    except IntegrityError:
        db.session.rollback()
        print(f"Attempt to add duplicate channel_id: {channel_id}")
        return jsonify({"error": f"Channel with YouTube ID '{channel_id}' already exists."}), 409
    except Exception as e:
        db.session.rollback()
        print(f"Database error adding channel: {e}")
        return jsonify({"error": "Failed to add channel to database."}), 500

@app.route("/admin/channels/<int:channel_db_id>", methods=["DELETE"])
@admin_required
def remove_channel_api(channel_db_id):
    try:
        channel_to_delete = Channel.query.get(channel_db_id)
        if channel_to_delete is None:
            return jsonify({"error": f"Channel with database ID {channel_db_id} not found."}), 404
        
        db.session.delete(channel_to_delete)
        db.session.commit()
        print(f"Channel removed: DB_ID={channel_db_id}")
        return jsonify({"message": f"Channel (DB ID: {channel_db_id}) removed successfully."}), 200
    except Exception as e:
        db.session.rollback()
        print(f"Database error removing channel: {e}")
        return jsonify({"error": "Failed to remove channel from database."}), 500

# --- Main Execution ---
if __name__ == "__main__":
    init_db() # Initialize the database schema on startup
    
    print("--- Initializing Server ---")
    if not ADMIN_API_KEY:
        print("WARNING: ADMIN_API_KEY is not set. Admin features will be locked.")
    if not YOUTUBE_API_KEY:
        print("WARNING: YT_API_KEY is not set. YouTube features will fail.")
    if not DISCORD_WEBHOOK_URL:
        print("WARNING: DISCORD_WEBHOOK_URL is not set. Discord notifications will be disabled.")
    print("-------------------------")

    port = int(os.getenv("PORT", 8080))
    debug = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
