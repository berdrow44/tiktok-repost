"""Détecte les nouveaux TikTok de plusieurs comptes, les télécharge en meilleure
qualité et les reposte chacun sur sa chaîne YouTube (Shorts). Lancé par GitHub Actions.

Les paires TikTok -> YouTube sont dans comptes.json. Pour chaque compte :
  - "tiktok"      : le @ du compte TikTok
  - "jeton"       : le nom du secret GitHub qui contient le jeton YouTube de la chaîne
  - "memoire"     : le fichier où sont notées les vidéos déjà traitées
"""

import json
import os
import sys
from pathlib import Path

import yt_dlp
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

PRIVACY = os.environ.get("YT_PRIVACY", "public")  # public / unlisted / private
MAX_PER_RUN = int(os.environ.get("MAX_PER_RUN", "3"))
SECRETS = json.loads(os.environ.get("SECRETS_JSON") or "{}")
COMPTES_FILE = Path("comptes.json")
DL_DIR = Path("downloads")


def secret(name):
    return SECRETS.get(name) or os.environ.get(name)


def load_seen(path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_seen(path, seen):
    path.write_text(json.dumps(sorted(seen), indent=1), encoding="utf-8")


def list_recent_videos(username):
    opts = {"extract_flat": True, "playlistend": 15, "quiet": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.tiktok.com/@{username}", download=False)
    return [e for e in info.get("entries", []) if e and e.get("id")]


def download(username, video_id):
    DL_DIR.mkdir(exist_ok=True)
    opts = {
        # Meilleure image + meilleur son disponibles (sans filigrane), fusionnés en MP4
        "format": "bv*+ba/b",
        "format_sort": ["res", "fps", "br"],
        "merge_output_format": "mp4",
        "outtmpl": str(DL_DIR / "%(id)s.%(ext)s"),
        "quiet": True,
    }
    url = f"https://www.tiktok.com/@{username}/video/{video_id}"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    path = DL_DIR / f"{video_id}.mp4"
    print(f"  téléchargé : {info.get('width')}x{info.get('height')}")
    return path, info.get("description") or info.get("title") or ""


def youtube_client(refresh_token):
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=secret("YT_CLIENT_ID"),
        client_secret=secret("YT_CLIENT_SECRET"),
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


def traiter(compte):
    """Renvoie True si tout s'est bien passé pour ce compte."""
    username = compte["tiktok"].lstrip("@")
    seen_file = Path(compte.get("memoire") or f"seen_{username}.json")
    token = secret(compte["jeton"])
    print(f"\n=== @{username} ===")
    if not token:
        print(f"  ÉCHEC : secret GitHub {compte['jeton']} introuvable")
        return False

    videos = list_recent_videos(username)
    print(f"{len(videos)} vidéos récentes trouvées")
    seen = load_seen(seen_file)

    # Premier lancement du compte : on mémorise l'existant sans rien reposter
    if seen is None:
        save_seen(seen_file, {v["id"] for v in videos})
        print("Premier lancement : vidéos existantes mémorisées, rien reposté.")
        return True

    seen = set(seen)
    new = [v for v in reversed(videos) if v["id"] not in seen][:MAX_PER_RUN]
    if not new:
        print("Aucune nouvelle vidéo.")
        return True

    youtube = youtube_client(token)
    ok = True
    for v in new:
        print(f"Nouvelle vidéo {v['id']}")
        try:
            path, caption = download(username, v["id"])
            upload(youtube, path, caption)
            seen.add(v["id"])
            save_seen(seen_file, seen)
            path.unlink(missing_ok=True)
        except Exception as e:  # on réessaiera au prochain passage
            print(f"  ÉCHEC : {e}")
            ok = False
    return ok


def main():
    comptes = json.loads(COMPTES_FILE.read_text(encoding="utf-8"))
    tout_ok = True
    for compte in comptes:
        try:
            if not traiter(compte):
                tout_ok = False
        except Exception as e:  # un compte en panne ne bloque pas les autres
            print(f"  ÉCHEC @{compte.get('tiktok')} : {e}")
            tout_ok = False
    if not tout_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
