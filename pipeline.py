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
    tg_log(f"\u274c <b>ERROR</b>\n<code>{text[:3000]}</code>")

# ─── Endpoints ───────────────────────────────────────────────────────────────

TRIGGER_URL        = "https://trigger-bot.s-dulaksha-com.workers.dev/get-movie"
GRAND_MOVIE_URL    = "https://trigger-bot.s-dulaksha-com.workers.dev/grand-movie"
UPLOADED_MOVIE_URL = "https://trigger-bot.s-dulaksha-com.workers.dev/uploaded-movie"
ABYSS_UPLOAD_URL   = "http://up.abyss.to"

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

TARGET_QUALITY = int(os.environ.get("QUALITY", "0"))
os.makedirs(WORK_DIR, exist_ok=True)

# ─── Fetch movie ──────────────────────────────────────────────────────────────

def fetch_movie():
    env_name = os.environ.get("MOVIE_NAME", "")
    env_imdb = os.environ.get("MOVIE_IMDB", "")
    env_dl   = os.environ.get("MOVIE_DL_LINK", "")
    env_sub  = os.environ.get("MOVIE_SUB_LINK", "")
    env_back = os.environ.get("MOVIE_BACKDROP", "")

    if env_name and env_imdb and env_dl and env_sub:
        tg_log(f"\u2705 Movie from env vars:\n\U0001f3ac <b>{env_name}</b>\n\U0001f194 <code>{env_imdb}</code>\n\U0001f3af Quality: <b>{TARGET_QUALITY}p</b>")
        return {"name": env_name, "imdb": env_imdb, "dl_link": env_dl, "subtitle_link": env_sub, "backdrop": env_back}

    tg_log("\U0001f50d Fetching movie from trigger URL...")
    try:
        r = requests.get(TRIGGER_URL, timeout=30)
    except Exception as e:
        tg_log_error(f"Network error: {e}"); sys.exit(1)
    if not r.ok:
        tg_log_error(f"Trigger fetch failed: HTTP {r.status_code} — {r.text[:200]}"); sys.exit(1)
    try:
        data = r.json()
    except Exception as e:
        tg_log_error(f"Invalid JSON: {e}"); sys.exit(1)
    movies = data.get("movies", [])
    if not movies:
        tg_log("\u26a0\ufe0f No movies available. Exiting."); sys.exit(0)
    movie = movies[0]
    tg_log(f"\u2705 Movie found!\n\U0001f3ac <b>{movie['name']}</b>\n\U0001f194 <code>{movie['imdb']}</code>")
    return movie

# ─── Notify endpoints ─────────────────────────────────────────────────────────

def notify_grand_movie(imdb):
    tg_log(f"\U0001f4e1 Notifying: download accepted (imdb={imdb})...")
    try:
        r = requests.get(f"{GRAND_MOVIE_URL}?imdb={imdb}", timeout=30)
        tg_log(f"\u2705 grand-movie \u2192 HTTP {r.status_code}")
    except Exception as e:
        tg_log_error(f"grand-movie notify failed: {e}")

def notify_uploaded_movie(imdb):
    tg_log(f"\U0001f4e1 Notifying: upload complete (imdb={imdb})...")
    try:
        r = requests.get(f"{UPLOADED_MOVIE_URL}?imdb={imdb}", timeout=30)
        tg_log(f"\u2705 uploaded-movie \u2192 HTTP {r.status_code}")
    except Exception as e:
        tg_log_error(f"uploaded-movie notify failed: {e}")

# ─── Download ─────────────────────────────────────────────────────────────────

def download_file(url, dest_path, label=""):
    tg_log(f"\u2b07\ufe0f Downloading <b>{label}</b>...", edit_key=f"dl_{label}")
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
                        tg_log(f"\u2b07\ufe0f <b>{label}</b>: {pct:.1f}%  {downloaded/1e6:.1f}/{total/1e6:.1f} MB  {speed/1e6:.2f} MB/s", edit_key=f"dl_{label}")
    size_mb = os.path.getsize(final_path) / 1e6
    tg_log(f"\u2705 <b>{label}</b> downloaded ({size_mb:.1f} MB)", edit_key=f"dl_{label}")
    print(f"\u2705 Saved: {final_path} ({size_mb:.1f} MB)")
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
    if src_h >= 2160: return [2160, 1080, 720, 480]
    elif src_h >= 1080: return [1080, 720, 480]
    elif src_h >= 720: return [720, 480]
    else: return [480]

# ─── Font ─────────────────────────────────────────────────────────────────────

def download_font():
    tg_log("\U0001f524 Downloading Sinhala font...")
    urllib.request.urlretrieve(FONT_URL, FONT_PATH)
    os.makedirs(os.path.expanduser("~/.fonts"), exist_ok=True)
    subprocess.run(["cp", FONT_PATH, os.path.expanduser("~/.fonts/")], check=True)
    subprocess.run(["fc-cache", "-fv"], capture_output=True)
    tg_log("\u2705 Sinhala font ready.")

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
                cm = re.search(r'color=["\'\']?([#\w]+)["\'\']?', attrs, re.I)
                sm = re.search(r'size=["\'\']?(\d+)', attrs, re.I)
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
    print(f"  \u2705 ASS ({play_w}x{play_h}): {ass_path}  [{len(events)} lines]")
    return ass_path

# ─── FFmpeg ───────────────────────────────────────────────────────────────────

def run_ffmpeg(cmd, total_secs, label):
    tg_log(f"\u2699\ufe0f <b>{label}</b>: Starting...", edit_key=f"ffmpeg_{label}")
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
                tg_log(f"\u2699\ufe0f <b>{label}</b>: {pct:.1f}%\n\u23f1 {out_time/60:.1f}/{total_secs/60:.1f} min | Speed: {spd:.1f}x | ETA: {eta:.0f}s", edit_key=f"ffmpeg_{label}")
    proc.wait(); print()
    if proc.returncode != 0:
        err = proc.stderr.read()[-2000:]
        tg_log_error(f"{label} failed:\n{err}")
        return False
    elapsed = time.time() - start
    tg_log(f"\u2705 <b>{label}</b>: Done in {elapsed:.0f}s", edit_key=f"ffmpeg_{label}")
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

# ─── Abyss upload ─────────────────────────────────────────────────────────────

def upload_to_abyss(file_path, api_key):
    fname = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    tg_log(f"\u2601\ufe0f Uploading to <b>Abyss.to</b>: {fname} ({file_size/1e6:.1f} MB)...", edit_key="abyss")
    with open(file_path, "rb") as f:
        resp = requests.post(f"{ABYSS_UPLOAD_URL}/{api_key}", headers={"content-type": "multipart/related"}, files={"file": (fname, f, "video/mp4")}, timeout=3600)
    if resp.status_code == 200:
        data = resp.json()
        slug = data.get("slug") or data.get("id") or str(data)
        link = f"https://abyss.to/{slug}"
        tg_log(f"\u2705 <b>Abyss.to</b> done!\n\U0001f517 {link}", edit_key="abyss")
        return link
    else:
        tg_log_error(f"Abyss upload failed: {resp.status_code} {resp.text[:500]}")
        return None

# ─── Telegram upload ──────────────────────────────────────────────────────────

async def tg_upload_all(burned_files, plain_files, thumbnail_path, abyss_link, movie_name):
    import cv2
    from pyrogram import Client
    from tqdm import tqdm

    upload_queue = [(burned_files[q], q, True) for q in sorted(burned_files.keys(), reverse=True)]
    upload_queue += [(plain_files[q], q, False) for q in sorted(plain_files.keys(), reverse=True)]
    tg_log(f"\U0001f4e4 Starting Telegram upload: {len(upload_queue)} files...")

    async with Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN) as app:
        for file_path, quality, is_burned in upload_queue:
            fname = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)
            file_type = "Sinhala Subtitles" if is_burned else "No Subtitles"
            label = f"{'🔥' if is_burned else '🎬'} {quality}p | {file_type}"
            tg_log(f"\u2b06\ufe0f Uploading: <b>{label}</b> ({file_size/1e6:.1f} MB)...", edit_key=f"tg_{quality}_{is_burned}")
            caption_parts = [f"<b>{movie_name}</b>", f"\U0001f4fa Quality: <b>{quality}p</b>", f"\U0001f4dd Type: <b>{file_type}</b>", f"\U0001f4e6 Size: {file_size/1e6:.1f} MB"]
            if abyss_link and is_burned: caption_parts.append(f"\u2601\ufe0f Abyss: {abyss_link}")
            caption = "\n".join(caption_parts)
            duration, width, height = 0, 0, 0
            try:
                cap = cv2.VideoCapture(file_path)
                fps = cap.get(cv2.CAP_PROP_FPS) or 1
                frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                duration = int(frames / fps); width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                cap.release()
            except Exception as e:
                print(f"\u26a0\ufe0f Metadata error: {e}")
            thumb = thumbnail_path if (thumbnail_path and os.path.exists(thumbnail_path)) else None
            pbar = tqdm(total=file_size, unit="B", unit_scale=True, unit_divisor=1024, desc=fname[:40], ncols=80)
            last_tg_progress = [0]
            def make_progress(pb, lbl, key):
                def cb(cur, tot):
                    delta = cur - pb.n
                    if delta > 0: pb.update(delta)
                    if time.time() - last_tg_progress[0] > 20:
                        last_tg_progress[0] = time.time()
                        pct = cur / tot * 100 if tot else 0
                        tg_log(f"\u2b06\ufe0f <b>{lbl}</b>: {pct:.1f}%\n{cur/1e6:.1f}/{tot/1e6:.1f} MB", edit_key=key)
                return cb
            await app.send_video(chat_id=CHAT_ID, video=file_path, file_name=fname, caption=caption, duration=duration, width=width, height=height, thumb=thumb, supports_streaming=True, progress=make_progress(pbar, label, f"tg_{quality}_{is_burned}"))
            pbar.close()
            tg_log(f"\u2705 Sent: <b>{label}</b>", edit_key=f"tg_{quality}_{is_burned}")

# ─── Disable workflow ─────────────────────────────────────────────────────────

def disable_workflow():
    token = os.environ.get("GH_TOKEN", "") or getattr(_cfg, "GH_TOKEN", "")
    repo  = os.environ.get("GH_REPO", "")  or getattr(_cfg, "GH_REPO", "")
    if not token or not repo:
        tg_log("\u26a0\ufe0f GH_TOKEN/GH_REPO not set — skipping disable."); return
    tg_log("\U0001f512 Disabling pipeline workflow...")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        r = requests.put(f"https://api.github.com/repos/{repo}/actions/workflows/main.yml/disable", headers=headers, timeout=15)
        if r.status_code == 204: tg_log("\u2705 Workflow disabled.")
        else: tg_log_error(f"Disable failed: HTTP {r.status_code}")
    except Exception as e:
        tg_log_error(f"GitHub API error: {e}")

# ─── Cleanup ──────────────────────────────────────────────────────────────────

def cleanup(paths):
    tg_log("\U0001f9f9 Cleaning up temp files...")
    for p in paths:
        try: os.remove(p); print(f"  \U0001f5d1\ufe0f  {os.path.basename(p)}")
        except Exception as e: print(f"  \u26a0\ufe0f  {p}: {e}")
    for ass in glob.glob(f"{WORK_DIR}/subtitles_*.ass"):
        try: os.remove(ass)
        except: pass

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    tg_log(f"\U0001f680 <b>MoviePluz Pipeline Started</b> — quality: <b>{TARGET_QUALITY}p</b>")

    movie         = fetch_movie()
    imdb          = movie["imdb"]
    movie_name    = movie["name"]
    video_url     = movie["dl_link"]
    subtitle_url  = movie["subtitle_link"]
    thumbnail_url = movie.get("backdrop", "")
    file_type     = detect_file_type(video_url)

    # ── Shared video download (matrix-safe) ──────────────────────────────────
    # All matrix jobs share the same WORK_DIR. Only the first job to arrive
    # downloads the video; the others wait for it to appear on disk.
    input_video   = f"{WORK_DIR}/input_video.mp4"
    download_lock = f"{WORK_DIR}/.video_downloading"

    if os.path.exists(input_video):
        size_mb = os.path.getsize(input_video) / 1e6
        tg_log(f"\u23e9 Video already on disk ({size_mb:.1f} MB) \u2014 skipping download.")
    elif os.path.exists(download_lock):
        tg_log("\u23f3 Another job is downloading the video \u2014 waiting...")
        for _ in range(360):          # wait up to 60 minutes
            time.sleep(10)
            if os.path.exists(input_video) and not os.path.exists(download_lock):
                break
        if not os.path.exists(input_video):
            tg_log_error("Timed out waiting for shared video download."); sys.exit(1)
        tg_log("\u2705 Shared video is ready.")
    else:
        # This job is the first \u2014 download and hold the lock
        open(download_lock, "w").close()
        try:
            input_video = download_file(video_url, input_video, "Video")
        finally:
            try: os.remove(download_lock)
            except: pass

    # Notify: download accepted
    notify_grand_movie(imdb)

    # Download subtitle & thumbnail (lightweight \u2014 each job can do this)
    srt_path = download_file(subtitle_url, f"{WORK_DIR}/subtitles.srt", "Subtitle")
    thumbnail_path = None
    if thumbnail_url:
        try: thumbnail_path = download_file(thumbnail_url, f"{WORK_DIR}/thumbnail.jpg", "Thumbnail")
        except Exception as e: tg_log_error(f"Thumbnail download failed (non-fatal): {e}")

    download_font()

    src_w, src_h = get_video_resolution(input_video)
    tg_log(f"\U0001f3ac Source: {src_w}x{src_h}")
    all_qualities = build_quality_ladder(src_h)

    # Use only this matrix job's quality
    if TARGET_QUALITY and TARGET_QUALITY in all_qualities:
        qualities = [TARGET_QUALITY]
    else:
        qualities = all_qualities
        tg_log(f"\u26a0\ufe0f TARGET_QUALITY={TARGET_QUALITY} not in ladder — running all: {qualities}")

    tg_log(f"\U0001f4d0 Encoding: {qualities}")

    # Build ASS subtitles
    tg_log("\U0001f520 Building ASS subtitle files...")
    ass_files = {}
    for q in qualities:
        w, h = RES_MAP[q]; ap = f"{WORK_DIR}/subtitles_{q}p.ass"
        make_ass(srt_path, ap, w, h); ass_files[q] = ap

    # FFmpeg encode
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
        cmd_plain = ["ffmpeg","-y","-i",input_video,"-c","copy","-progress","pipe:1","-nostats",plain_path] if h == src_h else                     ["ffmpeg","-y","-i",input_video,"-vf",scale,"-c:v","libx264","-pix_fmt","yuv420p","-crf",str(crf),"-c:a","copy","-progress","pipe:1","-nostats",plain_path]
        if run_ffmpeg(cmd_plain, total_secs, f"Plain {q}p"):
            plain_files[q] = plain_path

    tg_log(f"\U0001f389 Encodes done!\n\u2705 Burned: {list(burned_files.keys())}\n\u2705 Plain: {list(plain_files.keys())}")

    # Abyss upload
    abyss_link = None
    best_q = max(burned_files.keys()) if burned_files else None
    if best_q and ABYSS_API_KEY: abyss_link = upload_to_abyss(burned_files[best_q], ABYSS_API_KEY)

    # Telegram upload
    import nest_asyncio; nest_asyncio.apply()
    asyncio.run(tg_upload_all(burned_files, plain_files, thumbnail_path, abyss_link, movie_name))

    # Notify: upload complete
    notify_uploaded_movie(imdb)

    # Cleanup — shared video is only removed by the last (lowest-quality) job
    # so other matrix jobs can still read it while encoding.
    shared_files = ([thumbnail_path] if thumbnail_path else [])
    encoded_files = list(burned_files.values()) + list(plain_files.values())
    if not TARGET_QUALITY or TARGET_QUALITY == min(all_qualities):
        shared_files += [input_video, srt_path]
    else:
        shared_files += [srt_path]
    cleanup(shared_files + encoded_files)

    tg_log("\u2705 <b>Pipeline complete.</b>")
    print("\u2705 Pipeline complete.")

    # Disable workflow only from the highest-quality job to avoid race condition
    if not TARGET_QUALITY or TARGET_QUALITY == max(all_qualities):
        disable_workflow()


if __name__ == "__main__":
    main()
