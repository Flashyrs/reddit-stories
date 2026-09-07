import os
import sys
import json

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv()

from datetime import datetime
from utils.thumbnail_utils import create_reddit_thumbnail
from scripts.render_video import render_video

def regenerate_thumbnails(date_str):
    print(f"🖼️ Re-generating pure PIL cards and composite thumbnails for {date_str}...")
    for i in [1, 2, 3]:
        p = os.path.join(PROJECT_ROOT, f"reddit_stories/{date_str}/story_{i}.json")
        thumb_p = os.path.join(PROJECT_ROOT, f"reddit_stories/{date_str}/thumb_{i}.png")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            create_reddit_thumbnail(
                title_text=d.get("title", ""),
                subreddit=d.get("subreddit", "relationship_advice"),
                body_text=d.get("text", ""),
                output_path=thumb_p,
                format="short"
            )
    print(f"✅ All thumbnails regenerated for {date_str}!")

def rerender(date_str=None, thumbs_only=False):
    if not date_str:
        date_str = datetime.now().strftime("%Y%m%d")

    regenerate_thumbnails(date_str)

    if thumbs_only:
        return

    out_dir = os.path.join(PROJECT_ROOT, f"output/{date_str}")
    if os.path.exists(out_dir):
        for f in os.listdir(out_dir):
            if f.endswith(".mp4"):
                try:
                    os.remove(os.path.join(out_dir, f))
                except Exception:
                    pass

    for i in [1, 2, 3]:
        print(f"\n🎬 === RENDERING STORY {i} WITH LIVE MOVING GAMEPLAY BACKGROUND ===")
        render_video(date_str, story_name=str(i), format="short")

    print("\n🎉 === ALL 3 VIDEOS RENDERED WITH LIVE GAMEPLAY BACKGROUND! ===")

if __name__ == "__main__":
    thumbs_only = "--thumbs-only" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--thumbs-only"]
    d = args[0] if args else datetime.now().strftime("%Y%m%d")
    rerender(d, thumbs_only=thumbs_only)

