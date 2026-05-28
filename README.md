# MoviePluz Subtitle Burn Pipeline 🎬

GitHub Actions pipeline එකක් — Telegram bot command එකෙන් trigger කරලා, subtitle burn කරලා upload කරලා, automatically disable වෙනවා.

---

## 🔧 GitHub Secrets

Repo → **Settings → Secrets and variables → Actions**

| Secret | Description |
|--------|-------------|
| `API_ID` | Telegram API ID |
| `API_HASH` | Telegram API Hash |
| `SESSION_CODE` | Telethon StringSession |
| `CHAT_ID` | Videos upload වෙන channel/chat ID |
| `BOT_TOKEN` | Telegram bot token (progress logs) |
| `LOG_CHAT_ID` | Progress logs chat ID |
| `ABYSS_API_KEY` | Abyss.to API key |
| `GH_PAT` | GitHub Personal Access Token (`actions: write` permission) |

---

## 🔑 GH_PAT හදන විදිහ

1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens**
2. Repository access: **Only select repositories** → ඔයාගේ repo
3. Permissions → **Actions: Read and Write**
4. Generate → copy token → `GH_PAT` secret ලෙස save කරන්න

---

## 🚀 How it works

```
Bot /start command
      ↓
GitHub API → Enable pipeline.yml workflow
      ↓
GitHub API → Trigger workflow_dispatch
      ↓
Pipeline runs:
  • Fetch links from trigger URL
  • Download video + subtitle + thumbnail
  • SRT → ASS (per resolution)
  • FFmpeg burn (multi quality)
  • Upload best → Abyss.to
  • Upload all → Telegram
  • Call /delete endpoint
  • Cleanup
      ↓
Pipeline disables itself (pipeline.yml disabled)
      ↓
Next /start re-enables it again
```

---

## 📁 Output Filenames

```
The.Mummy.2026.720p.WebDL.x264.[MoviePluz.COM.LK].Sinhala.Subtitiles.mp4
The.Mummy.2026.720p.WebDL.x264.[MoviePluz.COM.LK].mp4
```

---

## 📊 Video Settings

| Quality | CRF |
|---------|-----|
| 2160p | 20 |
| 1080p | 20 |
| 720p | 23 |
| 480p | 26 |

- Codec: `libx264` | Pixel: `yuv420p` | Audio: `-c:a copy`

---

## 🤖 ඔයාගේ Bot එකේ /start handler

Bot `/start` command receive කරද්දි මේ දෙකම call කරන්න:

```python
import requests

GH_PAT  = "your_gh_pat"
GH_REPO = "username/moviepluz-pipeline"   # ඔයාගේ repo
HEADERS = {
    "Authorization": f"Bearer {GH_PAT}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
}
BASE = f"https://api.github.com/repos/{GH_REPO}/actions/workflows/pipeline.yml"

def start_pipeline():
    # 1. Enable workflow
    requests.put(f"{BASE}/enable", headers=HEADERS, timeout=15)
    # 2. Trigger it
    requests.post(
        f"{BASE}/dispatches",
        headers=HEADERS,
        json={"ref": "main"},
        timeout=15
    )
```
