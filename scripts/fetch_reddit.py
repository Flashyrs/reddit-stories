import os
import sys
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
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

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

def fetch_reddit_posts():
    posts_collected = []

    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT", "android:com.narrateloop.shorts:v1.0 (by /u/Flashyrs)")

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
        # App-only read-only instance (no user login needed)
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
                for subreddit_name in SUBREDDITS:
                    try:
                        sub = reddit.subreddit(subreddit_name)
                        for post in sub.top(time_filter="day", limit=20):
                            if not post.selftext or len(post.selftext) < 100 or post.score < 50:
                                continue
                            posts_collected.append({
                                "title": censor(post.title.strip()),
                                "text": censor(post.selftext.strip()),
                                "score": post.score,
                                "subreddit": subreddit_name,
                                "permalink": post.permalink
                            })
                    except Exception as sub_e:
                        print(f"⚠️ Failed to fetch r/{subreddit_name} via PRAW: {sub_e}")
                        continue
            except Exception as e:
                print(f"⚠️ PRAW attempt failed: {e}")


    # Method 1.5: Direct Reddit OAuth API Fallback (for Cloud Datacenter IPs)
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
                    for subreddit_name in SUBREDDITS:
                        url = f"https://oauth.reddit.com/r/{subreddit_name}/top.json?limit=20&t=day&raw_json=1"
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
                                    posts_collected.append({
                                        "title": censor(pdata.get("title", "").strip()),
                                        "text": censor(selftext.strip()),
                                        "score": score,
                                        "subreddit": subreddit_name,
                                        "permalink": pdata.get("permalink", "")
                                    })
                            else:
                                print(f"⚠️ Direct OAuth r/{subreddit_name} returned {res.status_code}")
                        except Exception as sub_e:
                            print(f"⚠️ Direct OAuth fetch failed for r/{subreddit_name}: {sub_e}")
                else:
                    print(f"⚠️ Access token request returned {token_resp.status_code}")
            except Exception as oauth_e:
                pass

    # Method 2: High-Speed RSS2JSON Fallback (100% Reliable on Cloud Servers)
    if not posts_collected:
        print("🌐 Fetching top stories via RSS2JSON feed parser...")
        for subreddit in SUBREDDITS:
            try:
                rss_url = f"https://www.reddit.com/r/{subreddit}/top/.rss?t=day"
                api_url = f"https://api.rss2json.com/v1/api.json?rss_url={requests.utils.quote(rss_url)}"
                res = requests.get(api_url, timeout=10)
                if res.status_code == 200:
                    feed_data = res.json()
                    for item in feed_data.get("items", []):
                        raw_desc = item.get("description", "") or item.get("content", "")
                        # Remove HTML markup and unescape HTML entities
                        clean_text = re.sub(r"<[^>]+>", " ", raw_desc)
                        clean_text = re.sub(r"(?i)\b(submitted\s+by|posted\s+by)\b.*", "", clean_text)
                        clean_text = re.sub(r"(?i)\[link\]\s*\[comments\].*", "", clean_text)
                        clean_text = html.unescape(clean_text).strip()
                        clean_text = re.sub(r"\s+", " ", clean_text)

                        if not clean_text or len(clean_text) < 100:
                            continue

                        posts_collected.append({
                            "title": censor(item.get("title", "").strip()),
                            "text": censor(clean_text),
                            "score": 500,  # Top daily posts
                            "subreddit": subreddit,
                            "permalink": item.get("link", "")
                        })
            except Exception as rss_e:
                print(f"⚠️ RSS2JSON fetch failed for r/{subreddit}: {rss_e}")

    # Method 3: Public JSON Fallback (if PRAW and RSS not available)
    if not posts_collected:
        print("🌐 Falling back to public JSON scraping...")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        }
        for subreddit in SUBREDDITS:
            url = f'https://www.reddit.com/r/{subreddit}/top.json?limit=15&t=day'
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
                    posts_collected.append({
                        "title": censor(data["title"].strip()),
                        "text": censor(data["selftext"].strip()),
                        "score": data.get("score", 0),
                        "subreddit": subreddit,
                        "permalink": data.get("permalink")
                    })
            except Exception as e:
                print(f"⚠️ Failed to fetch from r/{subreddit}: {e}")
                continue

    if not posts_collected:
        raise Exception("❌ No suitable posts found.")

    posts_collected.sort(key=lambda x: x["score"], reverse=True)

    # Configuration toggles from environment
    only_shorts = os.getenv("ONLY_SHORTS", "true").lower() in ("true", "1", "yes")
    target_shorts = int(os.getenv("TARGET_SHORTS_PER_DAY", "3" if only_shorts else "2"))
    target_videos = 0 if only_shorts else int(os.getenv("TARGET_VIDEOS_PER_DAY", "1"))

    shorts_collected = 0
    videos_collected = 0

    date_today = get_current_time()
    date_str_today = date_today.strftime("%Y%m%d")
    out_dir_today = os.path.join(PROJECT_ROOT, "reddit_stories", date_str_today)
    os.makedirs(out_dir_today, exist_ok=True)
    idx_today = 1

    for post in posts_collected:
        raw_text = post["text"]
        # AI Hook Transformation: rewrite opening 1-2 sentences for uniqueness & high CTR
        text = enhance_story_hook_with_gemini(raw_text, subreddit=subreddit)
        word_count = len(text.split())
        raw_title = post["title"]
        subreddit = post["subreddit"]
        title_with_subreddit = f"[{subreddit}] {raw_title}"
        gemini_title = generate_title_with_gemini(text, title_with_subreddit)

        raw_permalink = post.get("permalink", "")
        post_url = raw_permalink if raw_permalink.startswith("http") else f"https://www.reddit.com{raw_permalink}"

        # ----- LONG VIDEO STORIES (Only if not in ONLY_SHORTS mode) -----
        if not only_shorts and word_count > 350 and videos_collected < target_videos:
            story = {
                "title": gemini_title,
                "text": text,
                "part": 1,
                "total_parts": 1,
                "format": "video",
                "subreddit": subreddit
            }

            story_path = os.path.join(out_dir_today, f"story_{idx_today}.json")
            with open(story_path, "w", encoding="utf-8") as f:
                json.dump(story, f, indent=4, ensure_ascii=False)

            screenshot_path = os.path.join(out_dir_today, f"thumb_{idx_today}.png")
            try:
                get_or_create_thumbnail(post_url, gemini_title, text, screenshot_path, subreddit=subreddit, format="video")
            except Exception as thumb_err:
                print(f"⚠️ Thumbnail generation warning for story {idx_today}: {thumb_err}")

            print(f"🎬 Saved video story: {story_path}")
            idx_today += 1
            videos_collected += 1

        # ----- SHORT STORIES (Complete standalone stories without truncation) -----
        elif shorts_collected < target_shorts:
            # Preserve 100% of complete stories that naturally fit within YouTube Shorts (up to ~3 minutes)
            if 90 <= word_count <= 550:
                story_content = text
                word_cnt = word_count
            elif word_count > 550:
                story_content, word_cnt = trim_story_to_short(text, min_words=200, max_words=550)
                if word_cnt < 90:
                    continue
            else:
                continue

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

            print(f"🎯 Saved short story ({word_cnt} words, ~{round(word_cnt/175*60)}s): {story_path}")
            idx_today += 1
            shorts_collected += 1

        # ----- Exit condition -----
        if shorts_collected >= target_shorts and videos_collected >= target_videos:
            break

    if shorts_collected < target_shorts:
        raise Exception(f"❌ Not enough short stories collected (collected {shorts_collected}/{target_shorts}).")

    print(f"✅ Saved {idx_today - 1} stories for {date_str_today} (Shorts: {shorts_collected}, Videos: {videos_collected})")
    return date_str_today, idx_today - 1

if __name__ == "__main__":
    fetch_reddit_posts()

