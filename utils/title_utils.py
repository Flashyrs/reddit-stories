import random
import google.generativeai as genai
import os
import re

HOOKS = ["INSANE", "KARMA", "REVENGE", "EXPOSED", "UNBELIEVABLE", "HEARTBREAKING", "SHOCKING", "TWISTED"]

def local_title_enhancer(title):
    if any(h.lower() in title.lower() for h in HOOKS):
        return title
    return f"{title} | {random.choice(HOOKS)}"

def clean_title_for_ffmpeg(title):
    title = title.split('\n')[0]  # take only the first line
    title = re.sub(r'[^\x00-\x7F]+', '', title)  # remove non-ASCII chars (like emojis)
    title = re.sub(r'[\'":]', '', title)  # remove quotes and colons
    return title.strip()

GEMINI_FALLBACK_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
    "gemini-flash-latest"
]

def _get_gemini_model():
    """
    Returns a configured GenerativeModel trying the preferred model then fallbacks.
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None
    genai.configure(api_key=api_key)

    preferred = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash").strip()
    candidates = [preferred] + [m for m in GEMINI_FALLBACK_MODELS if m != preferred]

    for model_name in candidates:
        if not model_name.startswith("models/"):
            model_name = f"models/{model_name}"
        try:
            return genai.GenerativeModel(model_name=model_name)
        except Exception:
            continue
    return genai.GenerativeModel(model_name="models/gemini-3.6-flash")

def generate_title_with_gemini(text, fallback_title):
    try:
        model = _get_gemini_model()
        if not model:
            return fallback_title

        # Extract subreddit prefix (e.g., "[AskReddit]") from the fallback_title
        match = re.match(r"^\[(.*?)\]\s*(.*)", fallback_title)
        subreddit = match.group(1) if match else "Reddit"
        original_title = match.group(2) if match else fallback_title

        prompt = (
            f"You're creating a YouTube title for a viral story from r/{subreddit}.\n"
            "Make it under 60 characters, catchy and clickable.\n"
            "No emojis, lists, or suggestions. Respond with just the title:\n\n"
            f"Original Reddit title: {original_title}\n"
            f"Story snippet: {text[:800]}\n"
        )

        response = model.generate_content(prompt)
        gemini_title = response.candidates[0].content.parts[0].text.strip()
        cleaned = clean_title_for_ffmpeg(gemini_title)

        # Re-append the subreddit prefix
        return f"[{subreddit}] {cleaned or original_title}"
    except Exception as e:
        print(f"⚠️ Gemini failed, fallback title used: {e}")
        return fallback_title


def enhance_story_hook_with_gemini(text, subreddit="Reddit"):
    """
    Rewrites the opening 1-2 sentences of a Reddit story into an intense, punchy hook.
    Removes intro fluff ('Throwaway account', 'Sorry for formatting', 'My first time posting here')
    and ensures unique narrative phrasing to prevent duplicate transcript penalties.
    """
    if not text or len(text) < 80:
        return text

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return text

    try:
        model = _get_gemini_model()
        if not model:
            return text

        prompt = (
            f"You are a viral YouTube Shorts editor adapting a real story from r/{subreddit}.\n"
            "Task: Rewrite the opening 1-2 sentences of this story into an intense, punchy narrative hook.\n"
            "Rules:\n"
            "1. Remove boring intro filler like 'Throwaway account because...', 'Posting from mobile', 'Long time lurker'.\n"
            "2. Keep the narrator's authentic first-person perspective and exact facts.\n"
            "3. Output ONLY the complete revised story with your new opening hook seamlessly flowing into the remaining body.\n"
            "4. Do NOT add meta commentary, quotes, markdown formatting, or emojis.\n\n"
            f"Original Story:\n{text}"
        )

        response = model.generate_content(prompt)
        enhanced_text = response.candidates[0].content.parts[0].text.strip()
        if enhanced_text and len(enhanced_text) >= len(text) * 0.7:
            # Clean non-ascii artifacts
            enhanced_text = re.sub(r'[^\x00-\x7F]+', '', enhanced_text).strip()
            return enhanced_text
        return text
    except Exception as e:
        print(f"⚠️ Gemini hook enhancement fallback: {e}")
        return text

