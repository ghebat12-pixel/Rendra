import os
import re
import uuid
import shutil
import zipfile
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp

app = FastAPI(title="Rendra YouTube Downloader")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TEMP_DIR = "/tmp/yt_downloads"
os.makedirs(TEMP_DIR, exist_ok=True)


class UrlRequest(BaseModel):
    url: str


def get_cookie_file():
    """Mendukung cookies YouTube opsional dari Environment Variable agar anti-blokir IP cloud."""
    cookies_env = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if cookies_env:
        cookie_path = "/tmp/cookies.txt"
        with open(cookie_path, "w", encoding="utf-8") as f:
            f.write(cookies_env)
        return cookie_path
    return None


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    return cleaned[:120] if cleaned else "video_youtube"


def cleanup_path(path: str):
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def format_duration(seconds):
    if not seconds:
        return "00:00"
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.post("/api/info")
async def get_video_info(req: UrlRequest):
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL tidak boleh kosong.")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "geo_bypass": True,
    }
    cookie_file = get_cookie_file()
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if "entries" in info:
            entries = []
            for idx, entry in enumerate(info.get("entries", []), start=1):
                if not entry:
                    continue
                vid_url = entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id')}"
                if not vid_url.startswith("http"):
                    vid_url = f"https://www.youtube.com/watch?v={vid_url}"
                thumbs = entry.get("thumbnails") or []
                thumb = thumbs[-1]["url"] if thumbs else ""
                entries.append({
                    "index": idx,
                    "title": entry.get("title") or f"Video #{idx}",
                    "url": vid_url,
                    "duration": format_duration(entry.get("duration")),
                    "thumbnail": thumb,
                })

            return {
                "type": "playlist",
                "title": info.get("title") or "YouTube Playlist",
                "uploader": info.get("uploader") or info.get("channel") or "YouTube",
                "count": len(entries),
                "entries": entries,
            }

        # Single Video
        return {
            "type": "video",
            "title": info.get("title") or "Video YouTube",
            "uploader": info.get("uploader") or info.get("channel") or "YouTube",
            "duration": format_duration(info.get("duration")),
            "thumbnail": info.get("thumbnail") or "",
            "url": info.get("webpage_url") or url,
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Gagal mengambil data: {str(e)}")


@app.get("/api/download")
async def download_media(
    background_tasks: BackgroundTasks,
    url: str = Query(...),
    format: str = Query("mp4"),
    quality: str = Query("720"),
):
    job_id = str(uuid.uuid4())
    job_dir = os.path.join(TEMP_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    ydl_opts = {
        "outtmpl": os.path.join(job_dir, "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "geo_bypass": True,
        "restrictfilenames": False,
    }

    cookie_file = get_cookie_file()
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file

    if format == "mp3":
        bitrate = quality if quality in ["320", "256", "192", "128"] else "192"
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": bitrate,
            }
        ]
    else:
        if quality == "best":
            ydl_opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        else:
            height = quality if quality in ["1080", "720", "480", "360", "240"] else "720"
            ydl_opts["format"] = (
                f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
                f"best[height<={height}][ext=mp4]/best[height<={height}]/best"
            )
        ydl_opts["merge_output_format"] = "mp4"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            raw_title = sanitize_filename(info.get("title", "download"))

        files = os.listdir(job_dir)
        if not files:
            cleanup_path(job_dir)
            raise HTTPException(status_code=500, detail="File gagal dibuat oleh server.")

        downloaded_file = os.path.join(job_dir, files[0])
        ext = os.path.splitext(files[0])[1] or (".mp3" if format == "mp3" else ".mp4")
        final_name = f"{raw_title}_{quality}{'kbps' if format == 'mp3' else 'p'}{ext}"
        encoded_name = quote(final_name)

        background_tasks.add_task(cleanup_path, job_dir)

        return FileResponse(
            path=downloaded_file,
            media_type="audio/mpeg" if format == "mp3" else "video/mp4",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"
            },
        )
    except Exception as e:
        cleanup_path(job_dir)
        raise HTTPException(status_code=400, detail=f"Gagal mengunduh: {str(e)}")


@app.get("/api/download-playlist")
async def download_playlist_zip(
    background_tasks: BackgroundTasks,
    url: str = Query(...),
    format: str = Query("mp3"),
    quality: str = Query("192"),
    limit: int = Query(15),
):
    job_id = str(uuid.uuid4())
    job_dir = os.path.join(TEMP_DIR, job_id)
    files_dir = os.path.join(job_dir, "items")
    os.makedirs(files_dir, exist_ok=True)

    max_items = min(max(limit, 1), 25)  # Maksimal 25 video per ZIP agar stabil

    ydl_opts = {
        "outtmpl": os.path.join(files_dir, "%(playlist_index)02d - %(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "playlistend": max_items,
        "geo_bypass": True,
    }

    cookie_file = get_cookie_file()
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file

    if format == "mp3":
        bitrate = quality if quality in ["320", "192", "128"] else "192"
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": bitrate,
            }
        ]
    else:
        height = quality if quality in ["720", "480", "360"] else "480"
        ydl_opts["format"] = (
            f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"best[height<={height}][ext=mp4]/best"
        )
        ydl_opts["merge_output_format"] = "mp4"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            pl_title = sanitize_filename(info.get("title", "YouTube_Playlist"))

        downloaded_items = os.listdir(files_dir)
        if not downloaded_items:
            cleanup_path(job_dir)
            raise HTTPException(status_code=500, detail="Tidak ada video playlist yang berhasil diunduh.")

        zip_path = os.path.join(job_dir, f"{pl_title}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname in sorted(downloaded_items):
                full_p = os.path.join(files_dir, fname)
                if os.path.isfile(full_p):
                    zf.write(full_p, arcname=fname)

        encoded_zip = quote(f"{pl_title}_{format.upper()}.zip")
        background_tasks.add_task(cleanup_path, job_dir)

        return FileResponse(
            path=zip_path,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_zip}"
            },
        )
    except Exception as e:
        cleanup_path(job_dir)
        raise HTTPException(status_code=400, detail=f"Gagal memproses playlist: {str(e)}")
