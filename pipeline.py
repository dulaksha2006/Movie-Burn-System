#!/usr/bin/env python3
"""
MoviePluz Subtitle Burn Pipeline
Fetches video/subtitle links, burns subtitles, uploads to Abyss.to & Telegram
"""

import os
import sys
import re
import json
import time
import glob
import asyncio
import subprocess
import urllib.request
import requests
import config as _cfg

# ─── Telegram Progress Reporter ───────────────────────────────────────────────

BOT_TOKEN   = _cfg.BOT_TOKEN
LOG_CHAT_ID = _cfg.LOG_CHAT_ID

_last_msg_id = {}

def tg_log(text, edit_key=None):
    """Send or edit a Telegram message for logging/progress."""
    if not BOT_TOKEN or not LOG_CHAT_ID:
        print(text)
        return None
    url_base = f"https://api.telegram.org/bot{BOT_TOKEN}"
    payload = {"chat_id": LOG_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        if edit_key and edit_key in _last_msg_id:
            r = requests.post(
                f"{url_base}/editMessageText",
                json={**payload, "message_id": _last_msg_id[edit_key]},
                timeout=10
            )
            if r.ok:
                return _last_msg_id[edit_key]
        r = requests.post(f"{url_base}/sendMessage", json=payload, timeout=10)
        if r.ok:
            mid = r.json().get("result", {}).get("message_id")
            if edit_key:
                _last_msg_id[edit_key] = mid
            return mid
    except Exception as e:
        print(f"[TG LOG ERROR] {e}")
    return None


def tg_log_error(text):
    tg_log(f"❌ <b>ERROR</b>\n<code>{text[:3000]}</code>")


# ─── Config ───────────────────────────────────────────────────────────────────

TRIGGER_URL    = "https://movie-trigger.s-dulaksha-com.workers.dev/get"
DELETE_URL     = "https://movie-trigger.s-dulaksha-com.workers.dev/delete"
PIPELINE_SECRET = _cfg.PIPELINE_SECRET

def cf_params():
    """Query params for Cloudflare Worker requests."""
    return {"token": PIPELINE_SECRET} if PIPELINE_SECRET else {}
ABYSS_UPLOAD_URL = "http://up.abyss.to"   # POST to up.abyss.to/{apiKey}

API_ID        = _cfg.API_ID
API_HASH      = _cfg.API_HASH
SESSION_CODE  = _cfg.SESSION_CODE
ABYSS_API_KEY = _cfg.ABYSS_API_KEY
CHAT_ID       = _cfg.CHAT_ID

WORK_DIR  = os.environ.get("WORK_DIR", "/tmp/moviepluz")
FONT_PATH = f"{WORK_DIR}/NotoSansSinhala.ttf"
FONT_URL  = "https://raw.githubusercontent.com/Super-Chama/Noto-Sans-Sinhala/refs/heads/master/TTF/NotoSansSinhala-CondensedBold.ttf"

RES_MAP = {2160: (3840, 2160), 1080: (1920, 1080), 720: (1280, 720), 480: (854, 480)}
CRF_MAP = {2160: 20, 1080: 20, 720: 23, 480: 26}

os.makedirs(WORK_DIR, exist_ok=True)


# ─── Step 1: Fetch trigger data ───────────────────────────────────────────────

def fetch_trigger():
    tg_log("🔍 Checking trigger URL for new links...")
    try:
        r = requests.get(TRIGGER_URL, timeout=30, params=cf_params())
    except Exception as e:
        tg_log_error(f"Network error fetching trigger: {e}")
        sys.exit(1)

    if r.status_code == 401:
        tg_log_error("❌ PIPELINE_SECRET token වැරදියි — Worker Settings → SECRET_TOKEN check කරන්න.")
        sys.exit(1)
    if r.status_code == 403:
        tg_log_error(f"❌ 403 Forbidden: {r.text[:200]}")
        sys.exit(1)
    if not r.ok:
        tg_log_error(f"❌ Trigger fetch failed: HTTP {r.status_code} — {r.text[:200]}")
        sys.exit(1)

    try:
        data = r.json()
    except Exception as e:
        tg_log_error(f"❌ Invalid JSON from trigger URL: {e}\nResponse: {r.text[:200]}")
        sys.exit(1)

    if not data.get("links_available", False):
        tg_log("⚠️ <b>No links available.</b> Exiting.")
        sys.exit(0)

    tg_log(
        f"✅ Links found!\n"
        f"🎬 <b>{data['movie_name']} ({data['year']})</b>\n"
        f"🔗 Video: <code>{data['video_link'][:60]}...</code>"
    )
    return data


# ─── Step 2: Download file with progress ─────────────────────────────────────

def download_file(url, dest_path, label=""):
    tg_log(f"⬇️ Downloading <b>{label}</b>...", edit_key=f"dl_{label}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=3600) as r:
        r.raise_for_status()
        cd = r.headers.get("content-disposition", "")
        if "filename=" in cd:
            fname = cd.split("filename=")[-1].strip().strip('"\'')
        else:
            fname = url.split("/")[-1].split("?")[0] or os.path.basename(dest_path)
        final_path = os.path.join(os.path.dirname(dest_path), fname) if fname else dest_path
        total      = int(r.headers.get("content-length", 0))
        downloaded = 0
        start      = time.time()
        last_tg    = 0
        with open(final_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                elapsed = time.time() - start
                speed   = downloaded / elapsed if elapsed > 0 else 0
                if total:
                    pct = downloaded / total * 100
                    if time.time() - last_tg > 10:
                        last_tg = time.time()
                        tg_log(
                            f"⬇️ <b>{label}</b>: {pct:.1f}%  "
                            f"{downloaded/1e6:.1f}/{total/1e6:.1f} MB  "
                            f"{speed/1e6:.2f} MB/s",
                            edit_key=f"dl_{label}"
                        )
    size_mb = os.path.getsize(final_path) / 1e6
    tg_log(f"✅ <b>{label}</b> downloaded ({size_mb:.1f} MB)", edit_key=f"dl_{label}")
    print(f"✅ Saved: {final_path} ({size_mb:.1f} MB)")
    return final_path


def get_video_resolution(video_path):
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", video_path],
        capture_output=True, text=True
    )
    info  = json.loads(probe.stdout)
    w = info["streams"][0]["width"]
    h = info["streams"][0]["height"]
    return w, h


def get_duration(path):
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True
    )
    try:
        return float(p.stdout.strip())
    except:
        return 0


def build_quality_ladder(src_h):
    if src_h >= 2160:
        return [2160, 1080, 720, 480]
    elif src_h >= 1080:
        return [1080, 720, 480]
    elif src_h >= 720:
        return [720, 480]
    else:
        return [480]


# ─── Step 3: Download font ────────────────────────────────────────────────────

def download_font():
    tg_log("🔤 Downloading Sinhala font...")
    urllib.request.urlretrieve(FONT_URL, FONT_PATH)
    os.makedirs(os.path.expanduser("~/.fonts"), exist_ok=True)
    subprocess.run(["cp", FONT_PATH, os.path.expanduser("~/.fonts/")], check=True)
    subprocess.run(["fc-cache", "-fv"], capture_output=True)
    tg_log("✅ Sinhala font ready.")


# ─── Step 4: SRT → ASS ───────────────────────────────────────────────────────

def html_color_to_ass(hex_color):
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 6:
        r, g, b = hex_color[0:2], hex_color[2:4], hex_color[4:6]
        return f"&H00{b}{g}{r}".upper()
    return "&H00FFFFFF"


def convert_srt_line_to_ass(text):
    result, color_stack, size_stack = [], [], []
    pos = 0
    tag_pattern = re.compile(r'<(/?)(\w+)([^>]*)>', re.IGNORECASE)
    for match in tag_pattern.finditer(text):
        result.append(text[pos:match.start()])
        pos = match.end()
        closing  = match.group(1) == "/"
        tag_name = match.group(2).lower()
        attrs    = match.group(3)
        if not closing:
            if tag_name == "font":
                tags = ""
                cm = re.search(r'color=["\']?([#\w]+)["\']?', attrs, re.I)
                sm = re.search(r'size=["\']?(\d+)', attrs, re.I)
                if cm:
                    ac = html_color_to_ass(cm.group(1))
                    color_stack.append(ac)
                    tags += f"\\c{ac}"
                if sm:
                    px = int(sm.group(1))
                    size_stack.append(px)
                    tags += f"\\fs{px}"
                if tags:
                    result.append("{" + tags + "}")
            elif tag_name == "b": result.append("{\\b1}")
            elif tag_name == "i": result.append("{\\i1}")
            elif tag_name == "u": result.append("{\\u1}")
        else:
            if tag_name == "font":
                restore = ""
                if color_stack:
                    color_stack.pop()
                    restore += f"\\c{color_stack[-1]}" if color_stack else "\\c&H00FFFFFF"
                if size_stack:
                    size_stack.pop()
                    restore += f"\\fs{size_stack[-1]}" if size_stack else "\\fs42"
                if restore:
                    result.append("{" + restore + "}")
            elif tag_name == "b": result.append("{\\b0}")
            elif tag_name == "i": result.append("{\\i0}")
            elif tag_name == "u": result.append("{\\u0}")
    result.append(text[pos:])
    return "".join(result)


def srt_time_to_ass(t):
    t = t.replace(",", ".")
    h, m, rest = t.split(":")
    s, ms = rest.split(".")
    return f"{int(h)}:{m}:{s}.{ms[:2]}"


def make_ass(srt_path, ass_path, play_w, play_h):
    font_size = max(28, int(play_h * 0.058))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_w}
PlayResY: {play_h}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans Sinhala,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,10,10,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()
    blocks = re.split(r"\n{2,}", content.strip())
    events = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        times    = lines[1].split(" --> ")
        start    = srt_time_to_ass(times[0].strip())
        end      = srt_time_to_ass(times[1].strip())
        raw_text = r"\N".join(lines[2:])
        ass_text = convert_srt_line_to_ass(raw_text)
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{ass_text}")
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")
    print(f"  ✅ ASS ({play_w}x{play_h}): {ass_path}  [{len(events)} lines]")
    return ass_path


# ─── Step 5: FFmpeg encode ────────────────────────────────────────────────────

def run_ffmpeg(cmd, total_secs, label):
    tg_log(f"⚙️ <b>{label}</b>: Starting...", edit_key=f"ffmpeg_{label}")
    start    = time.time()
    last_tg  = 0
    proc     = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out_time = 0
    while True:
        line = proc.stdout.readline()
        if not line and proc.poll() is not None:
            break
        line = line.strip()
        if line.startswith("out_time_ms="):
            try:
                out_time = int(line.split("=")[1]) / 1e6
            except:
                pass
        if line in ("progress=continue", "progress=end"):
            elapsed = time.time() - start
            pct     = (out_time / total_secs * 100) if total_secs else 0
            spd     = out_time / elapsed if elapsed > 0 else 0
            eta     = (total_secs - out_time) / spd if spd > 0 else 0
            print(f"\r  {pct:.1f}%  {out_time/60:.1f}/{total_secs/60:.1f} min  speed {spd:.1f}x  ETA {eta:.0f}s", end="")
            if time.time() - last_tg > 15:
                last_tg = time.time()
                tg_log(
                    f"⚙️ <b>{label}</b>: {pct:.1f}%\n"
                    f"⏱ {out_time/60:.1f}/{total_secs/60:.1f} min | Speed: {spd:.1f}x | ETA: {eta:.0f}s",
                    edit_key=f"ffmpeg_{label}"
                )
    proc.wait()
    print()
    if proc.returncode != 0:
        err = proc.stderr.read()[-2000:]
        tg_log_error(f"{label} failed:\n{err}")
        return False
    elapsed = time.time() - start
    tg_log(f"✅ <b>{label}</b>: Done in {elapsed:.0f}s", edit_key=f"ffmpeg_{label}")
    return True


def make_filename(movie_name, year, quality, file_type, burned=True):
    """Generate proper filename."""
    name = movie_name.replace(" ", ".")
    year = str(year)
    q    = f"{quality}p"
    ft   = file_type  # e.g. WebDL
    if burned:
        return f"{name}.{year}.{q}.{ft}.x264.[MoviePluz.COM.LK].Sinhala.Subtitiles.mp4"
    else:
        return f"{name}.{year}.{q}.{ft}.x264.[MoviePluz.COM.LK].mp4"


def detect_file_type(video_url):
    """Detect file type from URL (BluRay, WebDL, WEBRip, etc.)"""
    url_lower = video_url.lower()
    if "bluray" in url_lower or "blu-ray" in url_lower:
        return "BluRay"
    elif "webrip" in url_lower:
        return "WEBRip"
    elif "web-dl" in url_lower or "webdl" in url_lower or "web.dl" in url_lower:
        return "WebDL"
    elif "hdtv" in url_lower:
        return "HDTV"
    else:
        return "WebDL"


# ─── Step 6: Abyss.to upload ─────────────────────────────────────────────────

def upload_to_abyss(file_path, api_key):
    fname     = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    tg_log(f"☁️ Uploading to <b>Abyss.to</b>: {fname} ({file_size/1e6:.1f} MB)...", edit_key="abyss")
    upload_url = f"{ABYSS_UPLOAD_URL}/{api_key}"
    with open(file_path, "rb") as f:
        resp = requests.post(
            upload_url,
            headers={"content-type": "multipart/related"},
            files={"file": (fname, f, "video/mp4")},
            timeout=3600
        )
    if resp.status_code == 200:
        data = resp.json()
        slug = data.get("slug") or data.get("id") or str(data)
        link = f"https://abyss.to/{slug}"
        tg_log(f"✅ <b>Abyss.to</b> upload done!\n🔗 {link}", edit_key="abyss")
        return link
    else:
        tg_log_error(f"Abyss upload failed: {resp.status_code} {resp.text[:500]}")
        return None


# ─── Step 7: Telegram upload ─────────────────────────────────────────────────

async def tg_upload_all(burned_files, plain_files, thumbnail_path, abyss_link, movie_name, year):
    import cv2
    from pyrogram import Client
    from tqdm import tqdm

    upload_queue = []
    for q in sorted(burned_files.keys(), reverse=True):
        upload_queue.append((burned_files[q], q, True))
    for q in sorted(plain_files.keys(), reverse=True):
        upload_queue.append((plain_files[q], q, False))

    tg_log(f"📤 Starting Telegram upload: {len(upload_queue)} files...")

    async with Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN) as app:
        for file_path, quality, is_burned in upload_queue:
            fname     = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)
            file_type = "Sinhala Subtitles" if is_burned else "No Subtitles"
            emoji     = "🔥" if is_burned else "🎬"
            label     = f"{emoji} {quality}p | {file_type}"

            tg_log(f"⬆️ Uploading: <b>{label}</b> ({file_size/1e6:.1f} MB)...", edit_key=f"tg_{quality}_{is_burned}")

            caption_parts = [
                f"<b>{movie_name} ({year})</b>",
                f"📺 Quality: <b>{quality}p</b>",
                f"📝 Type: <b>{file_type}</b>",
                f"📦 Size: {file_size/1e6:.1f} MB",
            ]
            if abyss_link and is_burned:
                caption_parts.append(f"☁️ Abyss: {abyss_link}")
            caption = "\n".join(caption_parts)

            # Video metadata (duration / resolution)
            duration, width, height = 0, 0, 0
            try:
                cap = cv2.VideoCapture(file_path)
                fps = cap.get(cv2.CAP_PROP_FPS) or 1
                frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                duration = int(frames / fps)
                width    = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                cap.release()
            except Exception as e:
                print(f"⚠️ Metadata ගන්න බැරි වුණා: {e}")

            thumb = thumbnail_path if (thumbnail_path and os.path.exists(thumbnail_path)) else None

            pbar = tqdm(total=file_size, unit="B", unit_scale=True,
                        unit_divisor=1024, desc=fname[:40], ncols=80)
            last_tg_progress = [0]

            def make_progress(pb, lbl, key):
                def cb(cur, tot):
                    delta = cur - pb.n
                    if delta > 0:
                        pb.update(delta)
                    if time.time() - last_tg_progress[0] > 20:
                        last_tg_progress[0] = time.time()
                        pct = cur / tot * 100 if tot else 0
                        tg_log(
                            f"⬆️ <b>{lbl}</b>: {pct:.1f}%\n"
                            f"{cur/1e6:.1f}/{tot/1e6:.1f} MB",
                            edit_key=key
                        )
                return cb

            await app.send_video(
                chat_id=CHAT_ID,
                video=file_path,
                file_name=fname,
                caption=caption,
                duration=duration,
                width=width,
                height=height,
                thumb=thumb,
                supports_streaming=True,
                progress=make_progress(pbar, label, f"tg_{quality}_{is_burned}")
            )
            pbar.close()
            tg_log(f"✅ Sent: <b>{label}</b>", edit_key=f"tg_{quality}_{is_burned}")


# ─── Step 8: Call delete endpoint ────────────────────────────────────────────

def call_delete():
    tg_log("🗑️ Calling delete endpoint...")
    try:
        r = requests.get(DELETE_URL, timeout=30, params=cf_params())
        tg_log(f"✅ Delete endpoint response: {r.status_code}")
    except Exception as e:
        tg_log_error(f"Delete endpoint failed: {e}")


# ─── GitHub Actions workflow control ─────────────────────────────────────────

GH_HEADERS = lambda token: {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
}

def _gh_request(method, path, token, repo):
    url = f"https://api.github.com/repos/{repo}/actions/workflows/pipeline.yml/{path}"
    try:
        r = requests.request(method, url, headers=GH_HEADERS(token), timeout=15)
        return r.status_code
    except Exception as e:
        tg_log_error(f"GitHub API error: {e}")
        return None

def disable_workflow():
    """Disable pipeline.yml so it cannot be triggered until re-enabled."""
    token = os.environ.get("GH_TOKEN", "") or getattr(_cfg, "GH_TOKEN", "")
    repo  = os.environ.get("GH_REPO", "")  or getattr(_cfg, "GH_REPO", "")
    if not token or not repo:
        tg_log("⚠️ GH_TOKEN/GH_REPO not set — skipping workflow disable.")
        return
    tg_log("🔒 Disabling pipeline workflow...")
    code = _gh_request("PUT", "disable", token, repo)
    if code == 204:
        tg_log("✅ Workflow disabled. Bot re-enables it on next /start.")
    else:
        tg_log_error(f"Disable failed: HTTP {code}")


# ─── Step 9: Cleanup ─────────────────────────────────────────────────────────

def cleanup(paths):
    tg_log("🧹 Cleaning up temp files...")
    for p in paths:
        try:
            os.remove(p)
            print(f"  🗑️  {os.path.basename(p)}")
        except Exception as e:
            print(f"  ⚠️  {p}: {e}")
    for ass in glob.glob(f"{WORK_DIR}/subtitles_*.ass"):
        try:
            os.remove(ass)
        except:
            pass


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    tg_log("🚀 <b>MoviePluz Pipeline Started</b>")

    # 1. Fetch trigger
    data = fetch_trigger()
    movie_name    = data["movie_name"]
    year          = data["year"]
    video_url     = data["video_link"]
    subtitle_url  = data["subtitle_link"]
    thumbnail_url = data.get("thumbnail_link", "")

    file_type = detect_file_type(video_url)

    # 2. Download
    input_video    = download_file(video_url,    f"{WORK_DIR}/input_video.mp4",    "Video")
    srt_path       = download_file(subtitle_url, f"{WORK_DIR}/subtitles.srt",       "Subtitle")
    thumbnail_path = None
    if thumbnail_url:
        try:
            thumbnail_path = download_file(thumbnail_url, f"{WORK_DIR}/thumbnail.jpg", "Thumbnail")
        except Exception as e:
            tg_log_error(f"Thumbnail download failed (non-fatal): {e}")

    # 3. Font
    download_font()

    # 4. Resolution & quality ladder
    src_w, src_h = get_video_resolution(input_video)
    tg_log(f"🎬 Source: {src_w}x{src_h}")
    qualities = build_quality_ladder(src_h)
    tg_log(f"📐 Quality ladder: {qualities}")

    # 5. Build ASS files
    tg_log("🔠 Building ASS subtitle files...")
    ass_files = {}
    for q in qualities:
        w, h = RES_MAP[q]
        ap   = f"{WORK_DIR}/subtitles_{q}p.ass"
        make_ass(srt_path, ap, w, h)
        ass_files[q] = ap

    # 6. FFmpeg encode
    total_secs   = get_duration(input_video)
    burned_files = {}
    plain_files  = {}

    for q in qualities:
        w, h  = RES_MAP[q]
        crf   = CRF_MAP[q]
        scale = f"scale=-2:{h}"

        # Burned filename
        burned_name = make_filename(movie_name, year, q, file_type, burned=True)
        plain_name  = make_filename(movie_name, year, q, file_type, burned=False)
        burned_path = os.path.join(WORK_DIR, burned_name)
        plain_path  = os.path.join(WORK_DIR, plain_name)

        ass_esc = ass_files[q].replace("\\", "/").replace(":", "\\:")
        vf_burn = f"{scale},ass={ass_esc}:fontsdir={WORK_DIR}"

        cmd_burn = [
            "ffmpeg", "-y", "-i", input_video,
            "-vf", vf_burn,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf),
            "-c:a", "copy",
            "-progress", "pipe:1", "-nostats",
            burned_path
        ]
        if run_ffmpeg(cmd_burn, total_secs, f"Burn {q}p subtitles"):
            burned_files[q] = burned_path

        if h == src_h:
            cmd_plain = [
                "ffmpeg", "-y", "-i", input_video,
                "-c", "copy",
                "-progress", "pipe:1", "-nostats",
                plain_path
            ]
        else:
            cmd_plain = [
                "ffmpeg", "-y", "-i", input_video,
                "-vf", scale,
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf),
                "-c:a", "copy",
                "-progress", "pipe:1", "-nostats",
                plain_path
            ]
        if run_ffmpeg(cmd_plain, total_secs, f"Plain {q}p"):
            plain_files[q] = plain_path

    tg_log(f"🎉 All encodes done!\n✅ Burned: {list(burned_files.keys())}\n✅ Plain: {list(plain_files.keys())}")

    # 7. Upload best quality to Abyss.to
    abyss_link = None
    best_q = max(burned_files.keys()) if burned_files else None
    if best_q and ABYSS_API_KEY:
        abyss_link = upload_to_abyss(burned_files[best_q], ABYSS_API_KEY)

    # 8. Upload all to Telegram
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(tg_upload_all(burned_files, plain_files, thumbnail_path, abyss_link, movie_name, year))

    # 9. Call delete endpoint
    call_delete()

    # 10. Cleanup
    all_files = [input_video, srt_path]
    if thumbnail_path:
        all_files.append(thumbnail_path)
    all_files += list(burned_files.values()) + list(plain_files.values())
    cleanup(all_files)

    tg_log("✅ <b>Pipeline complete. Shutting down.</b>")
    print("✅ Pipeline complete.")

    # 11. Disable this workflow — bot will re-enable when needed
    disable_workflow()


if __name__ == "__main__":
    main()
