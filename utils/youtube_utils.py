import os
import pickle
import re
from pathlib import Path
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google_auth_oauthlib.flow import InstalledAppFlow


SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]

TOKEN_PATH = Path("token.pickle").resolve()
CLIENT_SECRET = Path(os.getenv("YOUTUBE_CLIENT_SECRET", "client_secret.json")).resolve()

_cached_titles = None

def strip_part_suffix(title):
    return re.sub(r"\s*\[Part \d+ of \d+\]$", "", title, flags=re.IGNORECASE).strip().lower()

def get_authenticated_service():
    creds = None

    # Load existing token
    if TOKEN_PATH.exists():
        with open(TOKEN_PATH, "rb") as token_file:
            creds = pickle.load(token_file)

    # Refresh token if expired
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(TOKEN_PATH, "wb") as token_file:
                pickle.dump(creds, token_file)
            print("🔁 Token refreshed successfully.")
        except RefreshError as e:
            print(f"⚠️ Refresh token failed: {e}. Reauthenticating...")
            creds = None  # Force reauth

    # If no valid creds, do full auth flow
    if not creds or not creds.valid:
        if not CLIENT_SECRET.exists():
            raise FileNotFoundError(f"❌ client_secret.json not found at {CLIENT_SECRET}")
        print("🔐 Starting new OAuth flow...")
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "wb") as token_file:
            pickle.dump(creds, token_file)
        print("✅ Token saved to disk.")

    return build("youtube", "v3", credentials=creds)

def get_recent_video_titles(max_results=200):
    global _cached_titles
    try:
        youtube = get_authenticated_service()
        titles = []
        next_page_token = None

        while len(titles) < max_results:
            request = youtube.search().list(
                part="snippet",
                forMine=True,
                type="video",
                maxResults=min(50, max_results - len(titles)),
                pageToken=next_page_token
            )
            response = request.execute()
            for item in response.get("items", []):
                snippet = item.get("snippet")
                if isinstance(snippet, dict):
                    raw_title = snippet.get("title", "")
                    if raw_title:
                        titles.append(strip_part_suffix(raw_title))
            next_page_token = response.get("nextPageToken")
            if not next_page_token:
                break

        _cached_titles = titles
        return titles
    except Exception as e:
        print(f"⚠️ Warning fetching YouTube titles (using fallback): {e}")
        _cached_titles = []
        return []

def is_title_already_uploaded(target_title):
    global _cached_titles
    if not target_title:
        return False
    try:
        if _cached_titles is None:
            print("🔄 Fetching recent uploaded titles from YouTube...")
            _cached_titles = get_recent_video_titles()
        return strip_part_suffix(target_title) in (_cached_titles or [])
    except Exception:
        return False
