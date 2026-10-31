"""Détecte les nouveaux TikTok d'un compte, les télécharge en meilleure qualité
et les reposte sur YouTube (Shorts). Lancé par GitHub Actions."""

import json
import os
import sys
from pathlib import Path

import yt_dlp
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

USERNAME = os.environ["TIKTOK_USERNAME"].lstrip("@")
PRIVACY = os.environ.get("YT_PRIVACY", "public")  # public / unlisted / private
MAX_PER_RUN = int(os.environ.get("MAX_PER_RUN", "3"))
SEEN_FILE = Path("seen.json")
DL_DIR = Path("downloads")


def load_seen():
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    return None


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=1), encoding="utf-8")


def list_recent_videos():
    opts = {"extract_flat": True, "playlistend": 15, "quiet": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.tiktok.com/@{USERNAME}", download=False)
    return [e for e in info.get("entries", []) if e and e.get("id")]


def download(video_id):
    DL_DIR.mkdir(exist_ok=True)
    opts = {
        # Meilleure image + meilleur son disponibles (sans filigrane), fusionnés en MP4
        "format": "bv*+ba/b",
        "format_sort": ["res", "fps", "br"],
        "merge_output_format": "mp4",
        "outtmpl": str(DL_DIR / "%(id)s.%(ext)s"),
        "quiet": True,
    }
    url = f"https://www.tiktok.com/@{USERNAME}/video/{video_id}"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    path = DL_DIR / f"{video_id}.mp4"
    print(f"  téléchargé : {info.get('width')}x{info.get('height')}")
    return path, info.get("description") or info.get("title") or ""


def youtube_client():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def upload(youtube, path, caption):
    title = caption.split("\n")[0].strip() or "Nouvelle vidéo"
    if "#shorts" not in title.lower():
        title = f"{title[:90]} #Shorts"
    title = title[:100].replace("<", "").replace(">", "")
    tags = [w.lstrip("#") for w in caption.split() if w.startswith("#")][:15]
    body = {
        "snippet": {
            "title": title,
            "description": caption.replace("<", "").replace(">", "")[:4900],
            "tags": tags,
            "categoryId": "24",  # Divertissement
        },
        "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(path), mimetype="video/mp4", resumable=True)
    req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    print(f"  posté sur YouTube : https://youtube.com/shorts/{resp['id']}")


def main():
    videos = list_recent_videos()
    print(f"{len(videos)} vidéos récentes trouvées sur @{USERNAME}")
    seen = load_seen()

    # Premier lancement : on mémorise l'existant sans rien reposter
    if seen is None:
        save_seen({v["id"] for v in videos})
        print("Premier lancement : vidéos existantes mémorisées, rien reposté.")
        return

    seen = set(seen)
    new = [v for v in reversed(videos) if v["id"] not in seen][:MAX_PER_RUN]
    if not new:
        print("Aucune nouvelle vidéo.")
        return

    youtube = youtube_client()
    failed = False
    for v in new:
        print(f"Nouvelle vidéo {v['id']}")
        try:
            path, caption = download(v["id"])
            upload(youtube, path, caption)
            seen.add(v["id"])
            save_seen(seen)
        except Exception as e:  # on réessaiera au prochain passage
            print(f"  ÉCHEC : {e}")
            failed = True
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
