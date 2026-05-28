#!/usr/bin/env python3
"""
MoviePluz Subtitle Burn Pipeline
Fetches video/subtitle links, burns subtitles, uploads to Abyss.to & Telegram
"""

import os, sys, re, json, time, glob, asyncio, subprocess, urllib.request
import requests
import config as _cfg

# ─── Telegram Logger ──────────────────────────────────────────────────────────

BOT_TOKEN   = _cfg.BOT_TOKEN
LOG_CHAT_ID = _cfg.LOG_CHAT_ID
_last_msg_id = {}

def tg_log(text, edit_key=None):
    if not BOT_TOKEN or not LOG_CHAT_ID:
        print(text); return None
    url_base = f"https://api.telegram.org/bot{BOT_TOKEN}"
    payload  = {"chat_id": LOG_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        if edit_key and edit_key in _last_msg_id:
            r = requests.post(f"{url_base}/editMessageText",
                              json={**payload, "message_id": _last_msg_id[edit_key]}, timeout=10)
            if r.ok: return _last_msg_id[edit_key]
        r = requests.post(f"{url_base}/sendMessage", json=payload, timeout=10)
        if r.ok:
            mid = r.json().get("result", {}).get("message_id")
            if edit_key: _last_msg_id[edit_key] = mid
            return mid
    except Exception as e:
        print(f"[TG LOG ERROR] {e}")
    return None

def tg_log_error(text):
    tg_log(f"❌ <b>ERROR</b>\n<code>{text[:3000]}</code>")

# ─── Progress bar helper ──────────────────────────────────────────────────────

def make_progress_bar(pct, width=20):
    """Returns a text progress bar like ▓▓▓▓▓▓░░░░ 60%"""
    filled = int(width * pct / 100)
    bar = "▓" * filled + "░" * (width - filled)
    return f"[{bar}] {pct:.1f}%"

def format_eta(seconds):
    if seconds <= 0: return "—"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0: return f"{h}h {m}m {s}s"
    if m > 0: return f"{m}m {s}s"
    return f"{s}s"

def format_size(b):
    if b >= 1e9: return f"{b/1e9:.2f} GB"
    return f"{b/1e6:.1f} MB"

# ─── Endpoints ───────────────────────────────────────────────────────────────

TRIGGER_URL        = "https://trigger-bot.s-dulaksha-com.workers.dev/get-movie"
GRAND_MOVIE_URL    = "https://trigger-bot.s-dulaksha-com.workers.dev/grand-movie"
UPLOADED_MOVIE_URL = "https://trigger-bot.s-dulaksha-com.workers.dev/uploaded-movie"
ABYSS_UPLOAD_URL   = "http://up.abyss.to"

API_ID        = _cfg.API_ID
API_HASH      = _cfg.API_HASH
SESSION_CODE  = _cfg.SESSION_CODE
ABYSS_API_KEY = _cfg.ABYSS_API_KEY

# ── Fix CHAT_ID: Pyrogram needs -100XXXXXXXXXX format or @username ──────────
_raw_chat_id = _cfg.CHAT_ID
if isinstance(_raw_chat_id, str):
    _raw_chat_id = _raw_chat_id.strip()

# If it's a username (starts with @) or non-numeric string, use as-is
if isinstance(_raw_chat_id, str) and (
    _raw_chat_id.startswith("@") or not _raw_chat_id.lstrip("-").isdigit()
):
    CHAT_ID = _raw_chat_id
else:
    _raw_chat_id = int(_raw_chat_id)
    _chat_id_str = str(_raw_chat_id)
    if _chat_id_str.startswith("-100"):
        CHAT_ID = _raw_chat_id
    elif _chat_id_str.startswith("-"):
        inner = _chat_id_str[1:]
        if len(inner) >= 10:
            CHAT_ID = int(f"-100{inner}")
        else:
            CHAT_ID = _raw_chat_id
    else:
        CHAT_ID = _raw_chat_id

WORK_DIR  = os.environ.get("WORK_DIR", "/tmp/moviepluz")
FONT_PATH = f"{WORK_DIR}/NotoSansSinhala.ttf"
FONT_URL  = "https://raw.githubusercontent.com/Super-Chama/Noto-Sans-Sinhala/refs/heads/master/TTF/NotoSansSinhala-CondensedBold.ttf"

RES_MAP = {2160: (3840, 2160), 1080: (1920, 1080), 720: (1280, 720), 480: (854, 480)}
CRF_MAP = {2160: 20, 1080: 20, 720: 23, 480: 26}

TARGET_QUALITY = int(os.environ.get("QUALITY", "0"))
os.makedirs(WORK_DIR, exist_ok=True)

# ─── Fetch movie ──────────────────────────────────────────────────────────────

def fetch_movie():
    env_name = os.environ.get("MOVIE_NAME", "")
    env_imdb = os.environ.get("MOVIE_IMDB", "")
    env_dl   = os.environ.get("MOVIE_DL_LINK", "")
    env_sub  = os.environ.get("MOVIE_SUB_LINK", "")
    env_back = os.environ.get("MOVIE_BACKDROP", "")

    if not (env_name and env_imdb and env_sub):
        tg_log_error("Missing required env vars: MOVIE_NAME, MOVIE_IMDB or MOVIE_SUB_LINK not set.")
        sys.exit(1)

    tg_log(f"✅ Movie from env vars:\n🎬 <b>{env_name}</b>\n🆔 <code>{env_imdb}</code>\n🎯 Quality: <b>{TARGET_QUALITY}p</b>")
    return {"name": env_name, "imdb": env_imdb, "dl_link": env_dl, "subtitle_link": env_sub, "backdrop": env_back}

# ─── Notify endpoints ─────────────────────────────────────────────────────────

def notify_grand_movie(imdb):
    tg_log(f"📡 Notifying: download accepted (imdb={imdb})...")
    try:
        r = requests.get(f"{GRAND_MOVIE_URL}?imdb={imdb}", timeout=30)
        tg_log(f"✅ grand-movie → HTTP {r.status_code}")
    except Exception as e:
        tg_log_error(f"grand-movie notify failed: {e}")

def notify_uploaded_movie(imdb):
    tg_log(f"📡 Notifying: upload complete (imdb={imdb})...")
    try:
        r = requests.get(f"{UPLOADED_MOVIE_URL}?imdb={imdb}", timeout=30)
        tg_log(f"✅ uploaded-movie → HTTP {r.status_code}")
    except Exception as e:
        tg_log_error(f"uploaded-movie notify failed: {e}")

# ─── Download ─────────────────────────────────────────────────────────────────

def download_file(url, dest_path, label=""):
    tg_log(f"⬇️ Downloading <b>{label}</b>...", edit_key=f"dl_{label}")
    with requests.get(url, stream=True, allow_redirects=True, timeout=3600) as r:
        r.raise_for_status()
        cd = r.headers.get("content-disposition", "")
        fname = cd.split("filename=")[-1].strip().strip('"\'') if "filename=" in cd else url.split("/")[-1].split("?")[0] or os.path.basename(dest_path)
        final_path = os.path.join(os.path.dirname(dest_path), fname) if fname else dest_path
        total = int(r.headers.get("content-length", 0))
        downloaded, start, last_tg = 0, time.time(), 0
        with open(final_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk); downloaded += len(chunk)
                elapsed = time.time() - start
                speed   = downloaded / elapsed if elapsed > 0 else 0
                if total:
                    pct = downloaded / total * 100
                    if time.time() - last_tg > 10:
                        last_tg = time.time()
                        bar = make_progress_bar(pct)
                        tg_log(
                            f"⬇️ <b>{label}</b>\n"
                            f"{bar}\n"
                            f"📦 {format_size(downloaded)} / {format_size(total)}\n"
                            f"⚡ {speed/1e6:.2f} MB/s",
                            edit_key=f"dl_{label}"
                        )
    size_mb = os.path.getsize(final_path) / 1e6
    tg_log(f"✅ <b>{label}</b> downloaded ({size_mb:.1f} MB)", edit_key=f"dl_{label}")
    print(f"✅ Saved: {final_path} ({size_mb:.1f} MB)")
    return final_path

def get_video_resolution(video_path):
    probe = subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=width,height","-of","json",video_path], capture_output=True, text=True)
    info = json.loads(probe.stdout)
    return info["streams"][0]["width"], info["streams"][0]["height"]

def get_duration(path):
    p = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",path], capture_output=True, text=True)
    try: return float(p.stdout.strip())
    except: return 0

def build_quality_ladder(src_h):
    if src_h >= 1920:  return [2160, 1080, 720, 480]
    elif src_h >= 960: return [1080, 720, 480]
    elif src_h >= 600: return [720, 480]
    else:              return [480]

# ─── Font ─────────────────────────────────────────────────────────────────────

def download_font():
    tg_log("🔤 Downloading Sinhala font...")
    urllib.request.urlretrieve(FONT_URL, FONT_PATH)
    os.makedirs(os.path.expanduser("~/.fonts"), exist_ok=True)
    subprocess.run(["cp", FONT_PATH, os.path.expanduser("~/.fonts/")], check=True)
    subprocess.run(["fc-cache", "-fv"], capture_output=True)
    tg_log("✅ Sinhala font ready.")

# ─── SRT → ASS ────────────────────────────────────────────────────────────────

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
                cm = re.search(r"""color=["']?([#\w]+)["']?""", attrs, re.I)
                sm = re.search(r"""size=["']?(\d+)""", attrs, re.I)
                if cm:
                    ac = html_color_to_ass(cm.group(1)); color_stack.append(ac); tags += f"\\c{ac}"
                if sm:
                    px = int(sm.group(1)); size_stack.append(px); tags += f"\\fs{px}"
                if tags: result.append("{" + tags + "}")
            elif tag_name == "b": result.append("{\\b1}")
            elif tag_name == "i": result.append("{\\i1}")
            elif tag_name == "u": result.append("{\\u1}")
        else:
            if tag_name == "font":
                restore = ""
                if color_stack: color_stack.pop(); restore += f"\\c{color_stack[-1]}" if color_stack else "\\c&H00FFFFFF"
                if size_stack:  size_stack.pop();  restore += f"\\fs{size_stack[-1]}" if size_stack else "\\fs42"
                if restore: result.append("{" + restore + "}")
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
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {play_w}\nPlayResY: {play_h}\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Noto Sans Sinhala,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,10,10,40,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    with open(srt_path, "r", encoding="utf-8") as f: content = f.read()
    events = []
    for block in re.split(r"\n{2,}", content.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3: continue
        times = lines[1].split(" --> ")
        start = srt_time_to_ass(times[0].strip())
        end   = srt_time_to_ass(times[1].strip())
        raw_text = "\\N".join(lines[2:])
        ass_text = convert_srt_line_to_ass(raw_text)
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{ass_text}")
    with open(ass_path, "w", encoding="utf-8") as f: f.write(header + "\n".join(events) + "\n")
    print(f"  ✅ ASS ({play_w}x{play_h}): {ass_path}  [{len(events)} lines]")
    return ass_path

# ─── FFmpeg ───────────────────────────────────────────────────────────────────

def run_ffmpeg(cmd, total_secs, label):
    tg_log(f"⚙️ <b>{label}</b>: Starting...", edit_key=f"ffmpeg_{label}")
    start, last_tg, out_time = time.time(), 0, 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    while True:
        line = proc.stdout.readline()
        if not line and proc.poll() is not None: break
        line = line.strip()
        if line.startswith("out_time_ms="):
            try: out_time = int(line.split("=")[1]) / 1e6
            except: pass
        if line in ("progress=continue", "progress=end"):
            elapsed = time.time() - start
            pct = (out_time / total_secs * 100) if total_secs else 0
            spd = out_time / elapsed if elapsed > 0 else 0
            eta = (total_secs - out_time) / spd if spd > 0 else 0
            print(f"\r  {pct:.1f}%  {out_time/60:.1f}/{total_secs/60:.1f} min  speed {spd:.1f}x  ETA {eta:.0f}s", end="")
            if time.time() - last_tg > 15:
                last_tg = time.time()
                bar = make_progress_bar(pct)
                tg_log(
                    f"⚙️ <b>{label}</b>\n"
                    f"{bar}\n"
                    f"⏱ {out_time/60:.1f} / {total_secs/60:.1f} min\n"
                    f"🚀 Speed: {spd:.1f}x  |  ⏳ ETA: {format_eta(eta)}",
                    edit_key=f"ffmpeg_{label}"
                )
    proc.wait(); print()
    if proc.returncode != 0:
        err = proc.stderr.read()[-2000:]
        tg_log_error(f"{label} failed:\n{err}")
        return False
    elapsed = time.time() - start
    tg_log(f"✅ <b>{label}</b>: Done in {elapsed:.0f}s", edit_key=f"ffmpeg_{label}")
    return True

def make_filename(movie_name, quality, file_type, burned=True):
    name = re.sub(r'[^\w\s.-]', '', movie_name).strip().replace(" ", ".")
    q = f"{quality}p"
    return f"{name}.{q}.{file_type}.x264.[MoviePluz.COM.LK].Sinhala.Subtitiles.mp4" if burned else f"{name}.{q}.{file_type}.x264.[MoviePluz.COM.LK].mp4"

def detect_file_type(video_url):
    u = video_url.lower()
    if "bluray" in u or "blu-ray" in u: return "BluRay"
    elif "webrip" in u: return "WEBRip"
    elif "web-dl" in u or "webdl" in u or "web.dl" in u: return "WebDL"
    elif "hdtv" in u: return "HDTV"
    else: return "WebDL"

# ─── Abyss upload — highest quality burned only ───────────────────────────────

def upload_to_abyss(file_path, api_key):
    fname = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    tg_log(
        f"☁️ <b>Abyss.to Upload</b>\n"
        f"📄 {fname}\n"
        f"📦 {format_size(file_size)}\n"
        f"⏳ Status: Uploading...",
        edit_key="abyss"
    )
    with open(file_path, "rb") as f:
        resp = requests.post(
            f"{ABYSS_UPLOAD_URL}/{api_key}",
            headers={"content-type": "multipart/related"},
            files={"file": (fname, f, "video/mp4")},
            timeout=3600
        )
    if resp.status_code == 200:
        data = resp.json()
        slug = data.get("slug") or data.get("id") or str(data)
        link = f"https://abyss.to/{slug}"
        tg_log(
            f"✅ <b>Abyss.to Upload Done!</b>\n"
            f"📄 {fname}\n"
            f"🔗 {link}",
            edit_key="abyss"
        )
        return link
    else:
        tg_log_error(f"Abyss upload failed: {resp.status_code} {resp.text[:500]}")
        return None

# ─── Telegram upload ──────────────────────────────────────────────────────────
# Logic:
#   1. Upload highest-quality burned file to Abyss first (caller handles this)
#   2. While a quality's burned file is encoding, upload its plain version first
#   3. After encoding done, upload burned version for that quality
#   4. Per-quality: 1 message for burned (1080p/720p/480p), updated with live progress
#   5. Separate message per quality — total 3 messages (one per quality)

async def tg_upload_all(burned_files, plain_files, thumbnail_path, abyss_link, movie_name):
    import cv2
    from pyrogram import Client

    # Build upload queue: for each quality → plain first, then burned
    qualities_sorted = sorted(set(list(burned_files.keys()) + list(plain_files.keys())), reverse=True)

    # Quality label icons
    q_icons = {1080: "🔵", 720: "🟢", 480: "🟡", 2160: "🔴"}

    tg_log(f"📤 Starting Telegram upload: {len(qualities_sorted)} qualities...")

    async with Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN) as app:

        for quality in qualities_sorted:
            icon = q_icons.get(quality, "⚪")

            # ── Upload PLAIN first (no sub) ──────────────────────────────────
            if quality in plain_files:
                file_path = plain_files[quality]
                fname = os.path.basename(file_path)
                file_size = os.path.getsize(file_path)
                edit_key = f"tg_plain_{quality}"

                duration, width, height = _get_video_meta(file_path)
                thumb = thumbnail_path if (thumbnail_path and os.path.exists(thumbnail_path)) else None

                caption = (
                    f"{icon} <b>{movie_name}</b>\n"
                    f"📺 Quality: <b>{quality}p</b>\n"
                    f"📝 Type: <b>No Subtitles</b>\n"
                    f"📦 Size: {format_size(file_size)}"
                )

                tg_log(
                    f"{icon} <b>{quality}p | No Subtitles</b>\n"
                    f"{make_progress_bar(0)}\n"
                    f"📦 0 / {format_size(file_size)}\n"
                    f"⚡ Speed: — | ⏳ ETA: —\n"
                    f"⏫ Status: <b>Uploading...</b>",
                    edit_key=edit_key
                )

                upload_start = [time.time()]
                last_tg_t = [0]

                def make_plain_progress(fsize, eq_key, eq, ei, us, lt):
                    def cb(cur, tot):
                        elapsed = time.time() - us[0]
                        speed = cur / elapsed if elapsed > 0 else 0
                        eta = (fsize - cur) / speed if speed > 0 else 0
                        pct = cur / fsize * 100 if fsize else 0
                        if time.time() - lt[0] > 8:
                            lt[0] = time.time()
                            tg_log(
                                f"{ei} <b>{eq}p | No Subtitles</b>\n"
                                f"{make_progress_bar(pct)}\n"
                                f"📦 {format_size(cur)} / {format_size(fsize)}\n"
                                f"⚡ Speed: {speed/1e6:.2f} MB/s | ⏳ ETA: {format_eta(eta)}\n"
                                f"⏫ Status: <b>Uploading...</b>",
                                edit_key=eq_key
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
                    progress=make_plain_progress(file_size, edit_key, quality, icon, upload_start, last_tg_t)
                )

                tg_log(
                    f"✅ {icon} <b>{quality}p | No Subtitles</b> — Done!\n"
                    f"{make_progress_bar(100)}\n"
                    f"📦 {format_size(file_size)}",
                    edit_key=edit_key
                )

            # ── Upload BURNED (with Sinhala subs) ───────────────────────────
            if quality in burned_files:
                file_path = burned_files[quality]
                fname = os.path.basename(file_path)
                file_size = os.path.getsize(file_path)
                edit_key = f"tg_burned_{quality}"

                duration, width, height = _get_video_meta(file_path)
                thumb = thumbnail_path if (thumbnail_path and os.path.exists(thumbnail_path)) else None

                caption_parts = [
                    f"{icon} <b>{movie_name}</b>",
                    f"📺 Quality: <b>{quality}p</b>",
                    f"📝 Type: <b>Sinhala Subtitles</b>",
                    f"📦 Size: {format_size(file_size)}",
                ]
                if abyss_link and quality == max(burned_files.keys()):
                    caption_parts.append(f"☁️ Abyss: {abyss_link}")
                caption = "\n".join(caption_parts)

                tg_log(
                    f"{icon} <b>{quality}p | 🔥 Sinhala Subtitles</b>\n"
                    f"{make_progress_bar(0)}\n"
                    f"📦 0 / {format_size(file_size)}\n"
                    f"⚡ Speed: — | ⏳ ETA: —\n"
                    f"⏫ Status: <b>Uploading...</b>",
                    edit_key=edit_key
                )

                upload_start_b = [time.time()]
                last_tg_b = [0]

                def make_burned_progress(fsize, eq_key, eq, ei, us, lt):
                    def cb(cur, tot):
                        elapsed = time.time() - us[0]
                        speed = cur / elapsed if elapsed > 0 else 0
                        eta = (fsize - cur) / speed if speed > 0 else 0
                        pct = cur / fsize * 100 if fsize else 0
                        if time.time() - lt[0] > 8:
                            lt[0] = time.time()
                            tg_log(
                                f"{ei} <b>{eq}p | 🔥 Sinhala Subtitles</b>\n"
                                f"{make_progress_bar(pct)}\n"
                                f"📦 {format_size(cur)} / {format_size(fsize)}\n"
                                f"⚡ Speed: {speed/1e6:.2f} MB/s | ⏳ ETA: {format_eta(eta)}\n"
                                f"⏫ Status: <b>Uploading...</b>",
                                edit_key=eq_key
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
                    progress=make_burned_progress(file_size, edit_key, quality, icon, upload_start_b, last_tg_b)
                )

                tg_log(
                    f"✅ {icon} <b>{quality}p | 🔥 Sinhala Subtitles</b> — Done!\n"
                    f"{make_progress_bar(100)}\n"
                    f"📦 {format_size(file_size)}",
                    edit_key=edit_key
                )


def _get_video_meta(file_path):
    """Returns (duration, width, height) using cv2 if available."""
    try:
        import cv2
        cap = cv2.VideoCapture(file_path)
        fps    = cap.get(cv2.CAP_PROP_FPS) or 1
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        dur    = int(frames / fps)
        w      = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h      = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        return dur, w, h
    except Exception as e:
        print(f"⚠️ Metadata error: {e}")
        return 0, 0, 0


# ─── Disable workflow ─────────────────────────────────────────────────────────

def disable_workflow():
    token = os.environ.get("GH_TOKEN", "") or getattr(_cfg, "GH_TOKEN", "")
    repo  = os.environ.get("GH_REPO", "")  or getattr(_cfg, "GH_REPO", "")
    if not token or not repo:
        tg_log("⚠️ GH_TOKEN/GH_REPO not set — skipping disable."); return
    tg_log("🔒 Disabling pipeline workflow...")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        r = requests.put(f"https://api.github.com/repos/{repo}/actions/workflows/main.yml/disable", headers=headers, timeout=15)
        if r.status_code == 204: tg_log("✅ Workflow disabled.")
        else: tg_log_error(f"Disable failed: HTTP {r.status_code}")
    except Exception as e:
        tg_log_error(f"GitHub API error: {e}")

# ─── Cleanup ──────────────────────────────────────────────────────────────────

def cleanup(paths):
    tg_log("🧹 Cleaning up temp files...")
    for p in paths:
        try: os.remove(p); print(f"  🗑️  {os.path.basename(p)}")
        except Exception as e: print(f"  ⚠️  {p}: {e}")
    for ass in glob.glob(f"{WORK_DIR}/subtitles_*.ass"):
        try: os.remove(ass)
        except: pass

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    tg_log(f"🚀 <b>MoviePluz Pipeline Started</b> — quality: <b>{TARGET_QUALITY}p</b>")

    movie         = fetch_movie()
    imdb          = movie["imdb"]
    movie_name    = movie["name"]
    subtitle_url  = movie["subtitle_link"]
    thumbnail_url = movie.get("backdrop", "")

    file_type = os.environ.get("MOVIE_FILE_TYPE", "") or detect_file_type(movie.get("dl_link", ""))

    input_video = os.environ.get("VIDEO_PATH", f"{WORK_DIR}/input_video.mp4")
    if not os.path.exists(input_video):
        tg_log_error(f"Video not found at {input_video} — artifact download may have failed.")
        sys.exit(1)
    size_mb = os.path.getsize(input_video) / 1e6
    tg_log(f"✅ Video ready from artifact: <code>{input_video}</code> ({size_mb:.1f} MB)")

    src_w, src_h = get_video_resolution(input_video)
    tg_log(f"🎬 Source: {src_w}x{src_h}")
    all_qualities = build_quality_ladder(src_h)

    # notify_grand_movie only from highest quality job to avoid granded conflict
    if not TARGET_QUALITY or TARGET_QUALITY == max(all_qualities):
        notify_grand_movie(imdb)

    srt_path = download_file(subtitle_url, f"{WORK_DIR}/subtitles.srt", "Subtitle")
    thumbnail_path = None
    if thumbnail_url:
        try: thumbnail_path = download_file(thumbnail_url, f"{WORK_DIR}/thumbnail.jpg", "Thumbnail")
        except Exception as e: tg_log_error(f"Thumbnail download failed (non-fatal): {e}")

    download_font()

    if TARGET_QUALITY and TARGET_QUALITY in all_qualities:
        qualities = [TARGET_QUALITY]
    else:
        qualities = all_qualities
        tg_log(f"⚠️ TARGET_QUALITY={TARGET_QUALITY} not in ladder — running all: {qualities}")

    tg_log(f"📐 Encoding: {qualities}")

    tg_log("🔠 Building ASS subtitle files...")
    ass_files = {}
    for q in qualities:
        w, h = RES_MAP[q]; ap = f"{WORK_DIR}/subtitles_{q}p.ass"
        make_ass(srt_path, ap, w, h); ass_files[q] = ap

    total_secs = get_duration(input_video)
    burned_files, plain_files = {}, {}

    for q in qualities:
        w, h = RES_MAP[q]; crf = CRF_MAP[q]; scale = f"scale=-2:{h}"
        burned_path = os.path.join(WORK_DIR, make_filename(movie_name, q, file_type, burned=True))
        plain_path  = os.path.join(WORK_DIR, make_filename(movie_name, q, file_type, burned=False))
        ass_esc = ass_files[q].replace("\\", "/").replace(":", "\\:")
        vf_burn = f"{scale},ass={ass_esc}:fontsdir={WORK_DIR}"
        if run_ffmpeg(["ffmpeg","-y","-i",input_video,"-vf",vf_burn,"-c:v","libx264","-pix_fmt","yuv420p","-crf",str(crf),"-c:a","copy","-progress","pipe:1","-nostats",burned_path], total_secs, f"Burn {q}p"):
            burned_files[q] = burned_path
        cmd_plain = ["ffmpeg","-y","-i",input_video,"-c","copy","-progress","pipe:1","-nostats",plain_path] if h == src_h else \
                    ["ffmpeg","-y","-i",input_video,"-vf",scale,"-c:v","libx264","-pix_fmt","yuv420p","-crf",str(crf),"-c:a","copy","-progress","pipe:1","-nostats",plain_path]
        if run_ffmpeg(cmd_plain, total_secs, f"Plain {q}p"):
            plain_files[q] = plain_path

    tg_log(f"🎉 Encodes done!\n✅ Burned: {list(burned_files.keys())}\n✅ Plain: {list(plain_files.keys())}")

    # ── Abyss: upload ONLY 1080p burned file ──────────────────────────────────
    abyss_link = None
    if burned_files and ABYSS_API_KEY and TARGET_QUALITY == 1080:
        tg_log(f"☁️ Abyss upload: <b>1080p</b> burned only")
        abyss_link = upload_to_abyss(burned_files[1080], ABYSS_API_KEY)
    elif burned_files and ABYSS_API_KEY and TARGET_QUALITY != 1080:
        tg_log(f"ℹ️ Skipping Abyss upload — only 1080p job uploads to Abyss (current: {TARGET_QUALITY}p)")

    # ── Telegram: upload plain while next burned is encoding (sequential) ─────
    import nest_asyncio; nest_asyncio.apply()
    asyncio.run(tg_upload_all(burned_files, plain_files, thumbnail_path, abyss_link, movie_name))

    # notify_uploaded_movie only from highest quality job
    if not TARGET_QUALITY or TARGET_QUALITY == max(all_qualities):
        notify_uploaded_movie(imdb)

    cleanup_files = [srt_path] + ([thumbnail_path] if thumbnail_path else []) + list(burned_files.values()) + list(plain_files.values())
    cleanup(cleanup_files)

    tg_log("✅ <b>Pipeline complete.</b>")
    print("✅ Pipeline complete.")

    if not TARGET_QUALITY or TARGET_QUALITY == max(all_qualities):
        disable_workflow()


if __name__ == "__main__":
    main()
