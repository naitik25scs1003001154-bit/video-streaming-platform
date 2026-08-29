import os
import sqlite3
import subprocess
import uuid

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_from_directory
)

from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename


# --------------------------------------------------
# Configuration
# --------------------------------------------------

load_dotenv()

app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "change-this-secret-key"
)

DATABASE = "videos.db"
UPLOAD_FOLDER = "uploads"

ALLOWED_EXTENSIONS = {
    "mp4",
    "mov",
    "avi",
    "mkv",
    "webm"
}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# --------------------------------------------------
# AWS S3 Configuration
# --------------------------------------------------

AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

s3 = None

if (
    os.getenv("AWS_ACCESS_KEY_ID")
    and os.getenv("AWS_SECRET_ACCESS_KEY")
    and AWS_REGION
    and S3_BUCKET_NAME
):
    s3 = boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
    )


# --------------------------------------------------
# Database
# --------------------------------------------------

def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():

    connection = get_db()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            filename TEXT NOT NULL,
            s3_key TEXT,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    connection.commit()
    connection.close()


init_db()


# --------------------------------------------------
# Helper Functions
# --------------------------------------------------

def allowed_file(filename):

    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_EXTENSIONS
    )


def get_current_user():

    user_id = session.get("user_id")

    if not user_id:
        return None

    connection = get_db()

    user = connection.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    connection.close()

    return user


def process_video(input_file, output_file):

    """
    Convert uploaded video to MP4 using FFmpeg.
    """

    command = [
        "ffmpeg",
        "-i",
        input_file,
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        "-y",
        output_file
    ]

    subprocess.run(
        command,
        check=True
    )


def upload_to_s3(file_path, s3_key):

    if s3 is None:
        return False

    try:

        s3.upload_file(
            file_path,
            S3_BUCKET_NAME,
            s3_key,
            ExtraArgs={
                "ContentType": "video/mp4"
            }
        )

        return True

    except (BotoCoreError, ClientError) as error:

        print("S3 upload error:", error)

        return False


def create_s3_url(s3_key):

    if s3 is None:
        return None

    try:

        return s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": S3_BUCKET_NAME,
                "Key": s3_key
            },
            ExpiresIn=3600
        )

    except (BotoCoreError, ClientError) as error:

        print("S3 URL error:", error)

        return None


# --------------------------------------------------
# Home Page
# --------------------------------------------------

@app.route("/")
def home():

    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    return redirect(url_for("login"))


# --------------------------------------------------
# Register
# --------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:

            flash("Username and password are required.")

            return redirect(url_for("register"))

        hashed_password = generate_password_hash(password)

        connection = get_db()

        try:

            connection.execute(
                """
                INSERT INTO users (username, password)
                VALUES (?, ?)
                """,
                (username, hashed_password)
            )

            connection.commit()

            flash("Registration successful. Please login.")

            return redirect(url_for("login"))

        except sqlite3.IntegrityError:

            flash("Username already exists.")

            return redirect(url_for("register"))

        finally:

            connection.close()

    return render_template("register.html")


# --------------------------------------------------
# Login
# --------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        connection = get_db()

        user = connection.execute(
            """
            SELECT * FROM users
            WHERE username = ?
            """,
            (username,)
        ).fetchone()

        connection.close()

        if user and check_password_hash(
            user["password"],
            password
        ):

            session["user_id"] = user["id"]

            flash("Login successful.")

            return redirect(url_for("dashboard"))

        flash("Invalid username or password.")

    return render_template("login.html")


# --------------------------------------------------
# Logout
# --------------------------------------------------

@app.route("/logout")
def logout():

    session.clear()

    flash("You have been logged out.")

    return redirect(url_for("login"))


# --------------------------------------------------
# Dashboard
# --------------------------------------------------

@app.route("/dashboard")
def dashboard():

    user = get_current_user()

    if not user:

        return redirect(url_for("login"))

    connection = get_db()

    videos = connection.execute(
        """
        SELECT *
        FROM videos
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        (user["id"],)
    ).fetchall()

    connection.close()

    return render_template(
        "dashboard.html",
        user=user,
        videos=videos
    )


# --------------------------------------------------
# Upload Video
# --------------------------------------------------

@app.route("/upload", methods=["GET", "POST"])
def upload():

    user = get_current_user()

    if not user:

        return redirect(url_for("login"))

    if request.method == "POST":

        title = request.form.get("title", "").strip()
        file = request.files.get("video")

        if not title:

            flash("Please enter a video title.")

            return redirect(url_for("upload"))

        if not file or file.filename == "":

            flash("Please select a video.")

            return redirect(url_for("upload"))

        if not allowed_file(file.filename):

            flash(
                "Unsupported video format. "
                "Use MP4, MOV, AVI, MKV, or WEBM."
            )

            return redirect(url_for("upload"))

        original_name = secure_filename(file.filename)

        unique_id = uuid.uuid4().hex

        input_filename = (
            unique_id + "_" + original_name
        )

        input_path = os.path.join(
            app.config["UPLOAD_FOLDER"],
            input_filename
        )

        file.save(input_path)

        output_filename = unique_id + "_processed.mp4"

        output_path = os.path.join(
            app.config["UPLOAD_FOLDER"],
            output_filename
        )

        # ------------------------------------------
        # Process video with FFmpeg
        # ------------------------------------------

        try:

            process_video(
                input_path,
                output_path
            )

        except FileNotFoundError:

            flash(
                "FFmpeg is not installed or not available."
            )

            return redirect(url_for("upload"))

        except subprocess.CalledProcessError:

            flash(
                "Video processing failed."
            )

            return redirect(url_for("upload"))

        # ------------------------------------------
        # Upload processed video to S3
        # ------------------------------------------

        s3_key = None

        if s3 is not None:

            s3_key = (
                f"videos/{user['id']}/"
                f"{output_filename}"
            )

            success = upload_to_s3(
                output_path,
                s3_key
            )

            if not success:

                flash(
                    "Video processed, but S3 upload failed."
                )

        # ------------------------------------------
        # Save video information in SQLite
        # ------------------------------------------

        connection = get_db()

        connection.execute(
            """
            INSERT INTO videos
            (title, filename, s3_key, user_id)
            VALUES (?, ?, ?, ?)
            """,
            (
                title,
                output_filename,
                s3_key,
                user["id"]
            )
        )

        connection.commit()
        connection.close()

        # ------------------------------------------
        # Remove temporary original file
        # ------------------------------------------

        if os.path.exists(input_path):

            os.remove(input_path)

        flash("Video uploaded successfully.")

        return redirect(url_for("dashboard"))

    return render_template("upload.html")


# --------------------------------------------------
# Watch Video
# --------------------------------------------------

@app.route("/watch/<int:video_id>")
def watch(video_id):

    user = get_current_user()

    if not user:

        return redirect(url_for("login"))

    connection = get_db()

    video = connection.execute(
        """
        SELECT *
        FROM videos
        WHERE id = ?
        AND user_id = ?
        """,
        (video_id, user["id"])
    ).fetchone()

    connection.close()

    if not video:

        flash("Video not found.")

        return redirect(url_for("dashboard"))

    video_url = None

    # Use AWS S3 when available
    if video["s3_key"]:

        video_url = create_s3_url(
            video["s3_key"]
        )

    return render_template(
        "watch.html",
        video=video,
        video_url=video_url
    )


# --------------------------------------------------
# Stream Local Video
# --------------------------------------------------

@app.route("/video/<filename>")
def local_video(filename):

    user = get_current_user()

    if not user:

        return redirect(url_for("login"))

    return send_from_directory(
        app.config["UPLOAD_FOLDER"],
        filename
    )


# --------------------------------------------------
# Delete Video
# --------------------------------------------------

@app.route(
    "/delete/<int:video_id>",
    methods=["POST"]
)
def delete_video(video_id):

    user = get_current_user()

    if not user:

        return redirect(url_for("login"))

    connection = get_db()

    video = connection.execute(
        """
        SELECT *
        FROM videos
        WHERE id = ?
        AND user_id = ?
        """,
        (video_id, user["id"])
    ).fetchone()

    if not video:

        connection.close()

        flash("Video not found.")

        return redirect(url_for("dashboard"))

    # Delete from S3
    if s3 is not None and video["s3_key"]:

        try:

            s3.delete_object(
                Bucket=S3_BUCKET_NAME,
                Key=video["s3_key"]
            )

        except (BotoCoreError, ClientError) as error:

            print(
                "S3 delete error:",
                error
            )

    # Delete local file
    local_path = os.path.join(
        app.config["UPLOAD_FOLDER"],
        video["filename"]
    )

    if os.path.exists(local_path):

        os.remove(local_path)

    # Delete database record
    connection.execute(
        "DELETE FROM videos WHERE id = ?",
        (video_id,)
    )

    connection.commit()
    connection.close()

    flash("Video deleted successfully.")

    return redirect(url_for("dashboard"))


# --------------------------------------------------
# Run Application
# --------------------------------------------------

if __name__ == "__main__":

    app.run(
        debug=True,
        host="127.0.0.1",
        port=5000
    )
