import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import json
import random
import requests
import re
import html
import praw
from dotenv import load_dotenv
from datetime import datetime, timedelta
from utils.youtube_utils import is_title_already_uploaded
from utils.thumbnail_utils import create_reddit_thumbnail
from utils.title_utils import generate_title_with_gemini, enhance_story_hook_with_gemini

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv()

def get_current_time():
    tz_name = os.getenv("TIMEZONE", "Asia/Kolkata")
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now()

# Curated list of most engaging, viral, and entertaining story subreddits
DEFAULT_SUBREDDITS = [
    "relationship_advice",
    "TrueOffMyChest",
    "tifu",
    "AmItheAsshole",
    "AITAH",
    "confessions",
    "NuclearRevenge",
    "ProRevenge",
    "pettyrevenge",
    "entitledparents",
    "EntitledPeople",
    "maliciouscompliance",
    "AskReddit",
    "offmychest",
    "Stories"
]

env_subreddits = os.getenv("SUBREDDITS")
if env_subreddits:
    SUBREDDITS = [s.strip() for s in env_subreddits.split(",") if s.strip()]
else:
    SUBREDDITS = DEFAULT_SUBREDDITS

CENSOR_WORDS = ["fuck", "shit", "bitch", "asshole", "dick", "bastard", "crap", "cunt", "fag", "nigger"]

WORDS_PER_MINUTE = 150
MAX_VIDEO_WORDS = 1500
MIN_SHORT_WORDS = 100
MAX_SHORT_WORDS = 550

def strip_links_and_urls(text):
    """
    Completely removes all URLs, web links, markdown links, and link headers
    so they are never spoken by TTS or displayed in subtitles/thumbnails.
    """
    if not text:
        return ""
    # 1. Strip markdown links: [text](http://...) -> keep text
    text = re.sub(r'\[([^\]]+)\]\(https?://[^\)]+\)', r'\1', text)
    # 2. Strip standard URLs: http://..., https://...
    text = re.sub(r'https?://\S+', '', text)
    # 3. Strip www.... links
    text = re.sub(r'\bwww\.[a-zA-Z0-9\-\._~:/?#\[\]@!$&\'()*+,;=]+', '', text)
    # 4. Strip common reddit link headers like "Original post:", "Source:", "Link:"
    text = re.sub(r'(?i)\b(original\s+post|source\s+link|source|post\s+link|reddit\s+link|link|update)\s*:\s*', '', text)
    # 5. Strip "submitted by...", "posted by...", and "[link] [comments]" metadata
    text = re.sub(r'(?i)\b(submitted\s+by|posted\s+by)\b.*', '', text)
    text = re.sub(r'(?i)\[link\]\s*\[comments\].*', '', text)
    text = re.sub(r'(?i)\b/?u/\w+\b', '', text)
    text = text.replace(r'\_', '_')
    # 6. Normalize whitespace
    text = re.sub(r'\s+([,.:;?!])', r'\1', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def censor(text):
    text = strip_links_and_urls(text)
    def replace_word(word):
        return re.sub(rf"\b{re.escape(word)}\b", word[0] + "*" * (len(word) - 1), flags=re.IGNORECASE, string=text)
    for word in CENSOR_WORDS:
        text = replace_word(word)
    return text

CTA_ENDINGS = [
    "What would you do in this situation? Let me know in the comments below, and subscribe for daily stories!",
    "Who do you think was in the wrong here? Drop your thoughts in the comments and subscribe for more stories!",
    "Would you have handled this differently? Let me know in the comments below, and subscribe for daily Reddit stories!"
]

def append_engagement_cta(text):
    """
    Appends an engaging question and subscribe call-to-action if not already present.
    This spikes comment-to-view ratios and boosts YouTube Shorts distribution.
    """
    if not text:
        return text
    clean = text.strip()
    # Check if the story already ends with a question or CTA
    if any(q in clean.lower()[-120:] for q in ["what would you do", "aita", "what do you think", "thoughts?", "let me know", "subscribe"]):
        return clean
    cta = random.choice(CTA_ENDINGS)
    return f"{clean} {cta}"

def trim_story_to_short(text, min_words=100, max_words=550):
    """
    Trims a story cleanly at a sentence boundary if needed to fit YouTube Shorts (up to 3 minutes, ~550 words).
    Guarantees no sentence is cut off mid-word.
    """
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    accumulated = []
    current_word_count = 0

    for s in sentences:
        s = s.strip()
        if not s:
            continue
        words_in_s = len(s.split())
        if current_word_count + words_in_s <= max_words:
            accumulated.append(s)
            current_word_count += words_in_s
        else:
            if current_word_count >= min_words:
                break
            if current_word_count + words_in_s <= max_words + 30:
                accumulated.append(s)
                current_word_count += words_in_s
            break

    result = " ".join(accumulated).strip()
    return result, len(result.split())

def get_or_create_thumbnail(post_url, title_text, body_text, save_path, subreddit="relationship_advice", format="short"):
    create_reddit_thumbnail(
        title_text=title_text,
        subreddit=subreddit,
        body_text=body_text,
        output_path=save_path,
        format=format,
        post_url=post_url
    )

import hashlib

USED_POSTS_FILE = os.path.join(PROJECT_ROOT, "reddit_stories", "used_posts_history.json")

def load_used_posts_db():
    """
    Loads persistent database of all previously fetched and rendered Reddit posts.
    Automatically scans existing story JSON files in reddit_stories/ to populate historical entries.
    """
    db = {
        "post_ids": [],
        "permalinks": [],
        "text_hashes": [],
        "records": []
    }
    
    if os.path.exists(USED_POSTS_FILE):
        try:
            with open(USED_POSTS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    db["post_ids"] = loaded.get("post_ids", [])
                    db["permalinks"] = loaded.get("permalinks", [])
                    db["text_hashes"] = loaded.get("text_hashes", [])
                    db["records"] = loaded.get("records", [])
        except Exception as e:
            print(f"⚠️ Warning loading used posts db: {e}")

    # Auto-scan existing directories in reddit_stories to backfill history
    reddit_stories_dir = os.path.join(PROJECT_ROOT, "reddit_stories")
    if os.path.exists(reddit_stories_dir):
        for entry in os.listdir(reddit_stories_dir):
            day_dir = os.path.join(reddit_stories_dir, entry)
            if os.path.isdir(day_dir) and re.match(r"^\d{8}$", entry):
                for sf in os.listdir(day_dir):
                    if sf.startswith("story_") and sf.endswith(".json"):
                        try:
                            with open(os.path.join(day_dir, sf), "r", encoding="utf-8") as jf:
                                sdata = json.load(jf)
                                stext = sdata.get("text", "")
                                if stext:
                                    thash = hashlib.md5(stext[:120].strip().encode("utf-8")).hexdigest()
                                    if thash not in db["text_hashes"]:
                                        db["text_hashes"].append(thash)
                                        db["records"].append({
                                            "date": entry,
                                            "title": sdata.get("title", ""),
                                            "text_hash": thash
                                        })
                        except Exception:
                            pass

    return db


def save_used_posts_db(db):
    """Saves updated used posts database to disk."""
    os.makedirs(os.path.dirname(USED_POSTS_FILE), exist_ok=True)
    try:
        with open(USED_POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Warning saving used posts db: {e}")


def is_post_duplicate(post, used_db):
    """
    Checks if a candidate Reddit post was already used in ANY previous date.
    Deduplicates by post ID, permalink, text content hash, and YouTube upload history.
    """
    permalink = post.get("permalink", "").strip()
    post_id = post.get("id", "")
    if not post_id and permalink:
        # Extract post ID from permalink (e.g. /r/subreddit/comments/POST_ID/title/)
        m = re.search(r"/comments/([a-z0-9]+)/", permalink)
        if m:
            post_id = m.group(1)

    if post_id and post_id in used_db.get("post_ids", []):
        return True

    if permalink and permalink in used_db.get("permalinks", []):
        return True

    raw_text = post.get("text", "")
    if raw_text:
        thash = hashlib.md5(raw_text[:120].strip().encode("utf-8")).hexdigest()
        if thash in used_db.get("text_hashes", []):
            return True

    raw_title = post.get("title", "")
    if raw_title and is_title_already_uploaded(raw_title):
        return True

    return False


def record_used_post(post, date_str, final_title, used_db):
    """Registers a chosen post into the persistent used posts database."""
    permalink = post.get("permalink", "").strip()
    post_id = post.get("id", "")
    if not post_id and permalink:
        m = re.search(r"/comments/([a-z0-9]+)/", permalink)
        if m:
            post_id = m.group(1)

    raw_text = post.get("text", "")
    thash = hashlib.md5(raw_text[:120].strip().encode("utf-8")).hexdigest() if raw_text else ""

    if post_id and post_id not in used_db["post_ids"]:
        used_db["post_ids"].append(post_id)
    if permalink and permalink not in used_db["permalinks"]:
        used_db["permalinks"].append(permalink)
    if thash and thash not in used_db["text_hashes"]:
        used_db["text_hashes"].append(thash)

    used_db["records"].append({
        "date": date_str,
        "post_id": post_id,
        "permalink": permalink,
        "subreddit": post.get("subreddit", ""),
        "title": final_title,
        "text_hash": thash
    })
    save_used_posts_db(used_db)


def fetch_reddit_posts(target_date=None, replace_story_idx=None):
    posts_collected = []
    used_db = load_used_posts_db()
    print(f"📚 Loaded {len(used_db.get('records', []))} previously used posts from history db.")

    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT", "android:com.narrateloop.shorts:v1.0 (by /u/Flashyrs)")

    # Shuffle subreddits for variety across runs
    subreddits_pool = list(SUBREDDITS)
    random.shuffle(subreddits_pool)

    # Method 1: Official PRAW API (Fast & Block-Free)
    if client_id and client_secret:
        praw_configs = []
        username = os.getenv("REDDIT_USERNAME")
        password = os.getenv("REDDIT_PASSWORD")
        if username and password:
            praw_configs.append({
                "client_id": client_id,
                "client_secret": client_secret,
                "user_agent": user_agent,
                "username": username,
                "password": password
            })
        praw_configs.append({
            "client_id": client_id,
            "client_secret": client_secret,
            "user_agent": user_agent
        })

        for cfg in praw_configs:
            if posts_collected:
                break
            try:
                print("🔑 Fetching Reddit posts via PRAW API...")
                reddit = praw.Reddit(**cfg)
                for subreddit_name in subreddits_pool:
                    try:
                        sub = reddit.subreddit(subreddit_name)
                        # Check top daily, hot, and top weekly posts
                        for post in sub.top(time_filter="day", limit=30):
                            if not post.selftext or len(post.selftext) < 100 or post.score < 50:
                                continue
                            candidate = {
                                "id": getattr(post, "id", ""),
                                "title": censor(post.title.strip()),
                                "text": censor(post.selftext.strip()),
                                "score": post.score,
                                "subreddit": subreddit_name,
                                "permalink": post.permalink
                            }
                            if not is_post_duplicate(candidate, used_db):
                                posts_collected.append(candidate)
                            else:
                                print(f"🔁 [Deduplication] Skipped already-used post: {candidate['title'][:40]}...")

                        # Also fetch hot posts for fresh content
                        if len(posts_collected) < 10:
                            for post in sub.hot(limit=20):
                                if not post.selftext or len(post.selftext) < 100 or post.score < 50:
                                    continue
                                candidate = {
                                    "id": getattr(post, "id", ""),
                                    "title": censor(post.title.strip()),
                                    "text": censor(post.selftext.strip()),
                                    "score": post.score,
                                    "subreddit": subreddit_name,
                                    "permalink": post.permalink
                                }
                                if not is_post_duplicate(candidate, used_db) and not any(p.get("id") == candidate["id"] for p in posts_collected):
                                    posts_collected.append(candidate)
                    except Exception as sub_e:
                        print(f"⚠️ Failed to fetch r/{subreddit_name} via PRAW: {sub_e}")
                        continue
            except Exception as e:
                print(f"⚠️ PRAW attempt failed: {e}")

    # Method 1.5: Direct Reddit OAuth API Fallback
    if not posts_collected and client_id and client_secret:
        proxies_list = [None, {"http": "socks5h://127.0.0.1:40000", "https": "socks5h://127.0.0.1:40000"}]
        for proxies in proxies_list:
            if posts_collected:
                break
            try:
                auth = requests.auth.HTTPBasicAuth(client_id, client_secret)
                token_data = {"grant_type": "client_credentials"}
                username = os.getenv("REDDIT_USERNAME")
                password = os.getenv("REDDIT_PASSWORD")
                if username and password:
                    token_data = {
                        "grant_type": "password",
                        "username": username,
                        "password": password
                    }

                token_resp = requests.post(
                    "https://www.reddit.com/api/v1/access_token",
                    auth=auth,
                    data=token_data,
                    headers={"User-Agent": user_agent},
                    proxies=proxies,
                    timeout=10
                )
                if token_resp.status_code == 200:
                    access_token = token_resp.json().get("access_token")
                    oauth_headers = {
                        "Authorization": f"Bearer {access_token}",
                        "User-Agent": user_agent
                    }
                    for subreddit_name in subreddits_pool:
                        url = f"https://oauth.reddit.com/r/{subreddit_name}/top.json?limit=25&t=day&raw_json=1"
                        try:
                            res = requests.get(url, headers=oauth_headers, proxies=proxies, timeout=10)
                            if res.status_code == 200:
                                data_children = res.json().get("data", {}).get("children", [])
                                for child in data_children:
                                    pdata = child.get("data", {})
                                    selftext = pdata.get("selftext", "")
                                    score = pdata.get("score", 0)
                                    if not selftext or len(selftext) < 100 or score < 50:
                                        continue
                                    candidate = {
                                        "id": pdata.get("id", ""),
                                        "title": censor(pdata.get("title", "").strip()),
                                        "text": censor(selftext.strip()),
                                        "score": score,
                                        "subreddit": subreddit_name,
                                        "permalink": pdata.get("permalink", "")
                                    }
                                    if not is_post_duplicate(candidate, used_db):
                                        posts_collected.append(candidate)
                        except Exception as sub_e:
                            print(f"⚠️ Direct OAuth fetch failed for r/{subreddit_name}: {sub_e}")
            except Exception:
                pass

    # Method 2: High-Speed RSS2JSON Fallback
    if not posts_collected:
        print("🌐 Fetching top stories via RSS2JSON feed parser...")
        for subreddit in subreddits_pool:
            try:
                rss_url = f"https://www.reddit.com/r/{subreddit}/top/.rss?t=day"
                api_url = f"https://api.rss2json.com/v1/api.json?rss_url={requests.utils.quote(rss_url)}"
                res = requests.get(api_url, timeout=10)
                if res.status_code == 200:
                    feed_data = res.json()
                    for item in feed_data.get("items", []):
                        raw_desc = item.get("description", "") or item.get("content", "")
                        clean_text = re.sub(r"<[^>]+>", " ", raw_desc)
                        clean_text = re.sub(r"(?i)\b(submitted\s+by|posted\s+by)\b.*", "", clean_text)
                        clean_text = re.sub(r"(?i)\[link\]\s*\[comments\].*", "", clean_text)
                        clean_text = html.unescape(clean_text).strip()
                        clean_text = re.sub(r"\s+", " ", clean_text)

                        if not clean_text or len(clean_text) < 100:
                            continue

                        candidate = {
                            "id": "",
                            "title": censor(item.get("title", "").strip()),
                            "text": censor(clean_text),
                            "score": 500,
                            "subreddit": subreddit,
                            "permalink": item.get("link", "")
                        }
                        if not is_post_duplicate(candidate, used_db):
                            posts_collected.append(candidate)
            except Exception as rss_e:
                print(f"⚠️ RSS2JSON fetch failed for r/{subreddit}: {rss_e}")

    # Method 3: Public JSON Fallback
    if not posts_collected:
        print("🌐 Falling back to public JSON scraping...")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        }
        for subreddit in subreddits_pool:
            url = f'https://www.reddit.com/r/{subreddit}/top.json?limit=25&t=day'
            try:
                res = requests.get(url, headers=headers, timeout=10)
                if res.status_code != 200:
                    continue
                posts = res.json().get('data', {}).get('children', [])
                for post in posts:
                    data = post["data"]
                    if not data.get("selftext") or len(data["selftext"]) < 100:
                        continue
                    if data.get("score", 0) < 50:
                        continue
                    candidate = {
                        "id": data.get("id", ""),
                        "title": censor(data["title"].strip()),
                        "text": censor(data["selftext"].strip()),
                        "score": data.get("score", 0),
                        "subreddit": subreddit,
                        "permalink": data.get("permalink")
                    }
                    if not is_post_duplicate(candidate, used_db):
                        posts_collected.append(candidate)
            except Exception as e:
                print(f"⚠️ Failed to fetch from r/{subreddit}: {e}")
                continue

    if not posts_collected:
        raise Exception("❌ No fresh, unseen posts found across all subreddits.")

    # Sort candidates by score
    posts_collected.sort(key=lambda x: x["score"], reverse=True)
    print(f"✨ Found {len(posts_collected)} fresh, unseen candidate posts!")

    # Subreddit Diversity Strategy: Select max 1 post per subreddit to guarantee rich variety
    selected_posts = []
    used_subreddits_today = set()

    for post in posts_collected:
        sub = post["subreddit"]
        if sub not in used_subreddits_today:
            selected_posts.append(post)
            used_subreddits_today.add(sub)
            if len(selected_posts) >= 3:
                break

    # If fewer than 3 unique subreddits, fill remaining from highest score
    if len(selected_posts) < 3:
        for post in posts_collected:
            if post not in selected_posts:
                selected_posts.append(post)
                if len(selected_posts) >= 3:
                    break

    only_shorts = os.getenv("ONLY_SHORTS", "true").lower() in ("true", "1", "yes")
    target_shorts = int(os.getenv("TARGET_SHORTS_PER_DAY", "3" if only_shorts else "2"))
    target_videos = 0 if only_shorts else int(os.getenv("TARGET_VIDEOS_PER_DAY", "1"))

    shorts_collected = 0
    videos_collected = 0

    date_today = get_current_time()
    date_str_today = target_date if target_date else date_today.strftime("%Y%m%d")
    out_dir_today = os.path.join(PROJECT_ROOT, "reddit_stories", date_str_today)
    os.makedirs(out_dir_today, exist_ok=True)

    # Determine which story index to save
    if replace_story_idx:
        indices_to_populate = [int(replace_story_idx)]
    else:
        indices_to_populate = [1, 2, 3]

    for post in selected_posts:
        if not indices_to_populate:
            break

        idx_today = indices_to_populate.pop(0)
        raw_text = post["text"]
        subreddit = post["subreddit"]

        # AI Hook Transformation: rewrite opening 1-2 sentences for uniqueness & high CTR
        text = enhance_story_hook_with_gemini(raw_text, subreddit=subreddit)
        word_count = len(text.split())
        raw_title = post["title"]
        title_with_subreddit = f"[{subreddit}] {raw_title}"
        gemini_title = generate_title_with_gemini(text, title_with_subreddit)

        raw_permalink = post.get("permalink", "")
        post_url = raw_permalink if raw_permalink.startswith("http") else f"https://www.reddit.com{raw_permalink}"

        # Preserve complete stories that naturally fit within YouTube Shorts
        if 90 <= word_count <= 550:
            story_content = text
            word_cnt = word_count
        elif word_count > 550:
            story_content, word_cnt = trim_story_to_short(text, min_words=200, max_words=550)
        else:
            story_content = text
            word_cnt = word_count

        # Add viral engagement CTA question
        story_content = append_engagement_cta(story_content)
        word_cnt = len(story_content.split())

        story = {
            "title": gemini_title,
            "text": story_content,
            "part": 1,
            "total_parts": 1,
            "format": "short",
            "subreddit": subreddit
        }

        story_path = os.path.join(out_dir_today, f"story_{idx_today}.json")
        with open(story_path, "w", encoding="utf-8") as f:
            json.dump(story, f, indent=4, ensure_ascii=False)

        screenshot_path = os.path.join(out_dir_today, f"thumb_{idx_today}.png")
        try:
            get_or_create_thumbnail(post_url, gemini_title, story_content, screenshot_path, subreddit=subreddit, format="short")
        except Exception as thumb_err:
            print(f"⚠️ Thumbnail generation warning for story {idx_today}: {thumb_err}")

        # Record in persistent history database
        record_used_post(post, date_str_today, gemini_title, used_db)
        print(f"🎯 Saved fresh story_{idx_today}.json ({word_cnt} words, r/{subreddit}): {story_path}")
        shorts_collected += 1

    print(f"✅ Saved {shorts_collected} fresh stories for {date_str_today}")
    return date_str_today, shorts_collected

if __name__ == "__main__":
    target_d = None
    rep_idx = None
    for arg in sys.argv[1:]:
        if arg.isdigit() and len(arg) == 8:
            target_d = arg
        elif arg.startswith("--story="):
            rep_idx = int(arg.split("=")[1])
        elif arg.isdigit() and len(arg) == 1:
            rep_idx = int(arg)
    fetch_reddit_posts(target_date=target_d, replace_story_idx=rep_idx)


