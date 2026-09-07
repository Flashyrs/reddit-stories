import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import json
import re
import time
import asyncio
import io
import edge_tts
from pydub import AudioSegment

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from scripts.telegram_notify import log

# ====================================================================
# SUBREDDIT-AWARE VOICE MAPPING (Boosted for high viral engagement)
# ====================================================================
SUBREDDIT_VOICES = {
    # Casual, funny, thought-provoking
    "askreddit": {
        "male": "en-US-GuyNeural",
        "female": "en-US-EmmaNeural",
        "rate": "+30%"
    },
    # Emotional, serious relationship drama
    "relationship_advice": {
        "male": "en-US-ChristopherNeural",
        "female": "en-US-JennyNeural",
        "rate": "+28%"
    },
    # Dramatic, fast-paced, humorous fuckups
    "tifu": {
        "male": "en-US-BrianNeural",
        "female": "en-US-AriaNeural",
        "rate": "+30%"
    },
    # Pop culture & music
    "askredditkpop": {
        "male": "en-US-EricNeural",
        "female": "en-US-AriaNeural",
        "rate": "+30%"
    },
    # Moral conflicts, debates
    "amitheasshole": {
        "male": "en-US-GuyNeural",
        "female": "en-US-AriaNeural",
        "rate": "+30%"
    },
    "aitah": {
        "male": "en-US-GuyNeural",
        "female": "en-US-AriaNeural",
        "rate": "+30%"
    },
    # Petty / Pro / Nuclear Revenge
    "pettyrevenge": {
        "male": "en-US-EricNeural",
        "female": "en-US-JennyNeural",
        "rate": "+30%"
    },
    "prorevenge": {
        "male": "en-US-EricNeural",
        "female": "en-US-JennyNeural",
        "rate": "+30%"
    },
    "nuclearrevenge": {
        "male": "en-US-EricNeural",
        "female": "en-US-JennyNeural",
        "rate": "+30%"
    },
    # Intimate, reflective confessions
    "confessions": {
        "male": "en-US-RogerNeural",
        "female": "en-US-JennyNeural",
        "rate": "+28%"
    },
    # Raw feelings & unfiltered stories
    "trueoffmychest": {
        "male": "en-US-ChristopherNeural",
        "female": "en-US-JennyNeural",
        "rate": "+30%"
    },
    "stories": {
        "male": "en-US-AndrewNeural",
        "female": "en-US-AvaNeural",
        "rate": "+30%"
    }
}

DEFAULT_VOICE = {
    "male": "en-US-ChristopherNeural",
    "female": "en-US-JennyNeural",
    "rate": "+30%"
}


def get_voice_for_subreddit(subreddit, gender):
    """
    Selects the optimal narrator voice and pacing based on the subreddit and gender.
    Can be overridden if custom values exist in .env.
    """
    sub_key = re.sub(r"^r/", "", subreddit.lower().strip()) if subreddit else ""
    config = SUBREDDIT_VOICES.get(sub_key, DEFAULT_VOICE)

    # Subreddit voice
    voice = config.get(gender, DEFAULT_VOICE.get(gender, "en-US-ChristopherNeural"))
    rate = config.get("rate", "+30%")

    # Optional manual override via .env if specified
    if gender == "male" and os.getenv("EDGE_VOICE_MALE"):
        voice = os.getenv("EDGE_VOICE_MALE")
    elif gender == "female" and os.getenv("EDGE_VOICE_FEMALE"):
        voice = os.getenv("EDGE_VOICE_FEMALE")

    if os.getenv("EDGE_TTS_RATE"):
        rate = os.getenv("EDGE_TTS_RATE")

    return voice, rate


def detect_gender(text):
    """
    Detects likely narrator gender using contextual NLP analysis:
    - Direct self-identification: 'I (24F)', 'I [28F]', 'as a woman', 'I am a woman/girl/female/wife/mother/mom'
    - Relationship partner context: 'my husband', 'my boyfriend', 'my fiancé', 'my baby daddy' (narrator is female)
    - Inverse male self-identification & partner context: 'I (28M)', 'my wife', 'my girlfriend', 'my fiancée'
    - Age/gender tag parsing with narrator subject association.
    """
    if not text:
        return "male"
    
    text_lower = text.lower()
    
    # 1. Direct female narrator self-identification and female-specific situations
    female_self_patterns = [
        r"\bi\s*[\(\[]\s*\d{1,2}\s*f\s*[\)\]]",
        r"\b\d{1,2}\s*f\b",
        r"\b(i am|i'm|im)\s+(a\s+)?(\d{1,2}\s*(yo|year\s*old)\s+)?(woman|girl|female|wife|mother|mom|bride)\b",
        r"\bas\s+a\s+(\d{1,2}\s*(yo|year\s*old)\s+)?(woman|girl|female|wife|mother|mom)\b",
        r"\b(my\s+husband|my\s+ex-husband|my\s+ex\s+husband|my\s+boyfriend|my\s+ex-boyfriend|my\s+ex\s+bf|my\s+fianc[eé]|my\s+bf|my\s+hubby|my\s+baby\s+daddy)\b",
        r"\bhe\s+called\s+me\s+(his\s+wife|his\s+girl|a\s+bitch|his\s+woman)\b",
        r"\b(pregnant|giving\s+birth|my\s+pregnancy|my\s+period)\b"
    ]
    
    # 2. Direct male narrator self-identification
    male_self_patterns = [
        r"\bi\s*[\(\[]\s*\d{1,2}\s*m\s*[\)\]]",
        r"\b\d{1,2}\s*m\b",
        r"\b(i am|i'm|im)\s+(a\s+)?(\d{1,2}\s*(yo|year\s*old)\s+)?(man|guy|male|husband|father|dad|groom)\b",
        r"\bas\s+a\s+(\d{1,2}\s*(yo|year\s*old)\s+)?(man|guy|male|husband|father|dad)\b",
        r"\b(my\s+wife|my\s+ex-wife|my\s+ex\s+wife|my\s+girlfriend|my\s+ex-girlfriend|my\s+ex\s+gf|my\s+fianc[eé]e|my\s+gf|my\s+baby\s+mama)\b",
        r"\bshe\s+called\s+me\s+(her\s+husband|her\s+man)\b"
    ]
    
    female_score = 0
    male_score = 0
    
    for pat in female_self_patterns:
        female_score += len(re.findall(pat, text_lower)) * 2
        
    for pat in male_self_patterns:
        male_score += len(re.findall(pat, text_lower)) * 2

    # 3. Check standalone age/gender tags: e.g. "husband (41M)" -> husband is male, so speaker is female
    partner_male_tags = re.findall(r"\b(husband|boyfriend|bf|fianc[eé]|ex)\s*[\(\[]\s*\d{1,2}\s*m\s*[\)\]]", text_lower)
    partner_female_tags = re.findall(r"\b(wife|girlfriend|gf|fianc[eé]e|ex)\s*[\(\[]\s*\d{1,2}\s*f\s*[\)\]]", text_lower)
    
    female_score += len(partner_male_tags) * 3
    male_score += len(partner_female_tags) * 3

    if female_score > male_score:
        return "female"
    elif male_score > female_score:
        return "male"

    # Fallback to general isolated gender tags if no contextual relationship match
    general_tags = re.findall(r"\b\d{1,2}([MF])\b", text.upper())
    if general_tags:
        return "female" if general_tags.count("F") > general_tags.count("M") else "male"
        
    return "male"


def clean_text_for_tts(text):
    """Cleans up typographical punctuation, non-ascii characters, URLs, and web links for clean TTS."""
    if not text:
        return ""
    # 1. Strip markdown links: [text](http://...) -> keep text, drop link
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

    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = text.replace("\u2026", "...")
    text = text.replace("\xa0", " ")
    text = re.sub(r"[^\x00-\x7F]+", "", text)
    text = re.sub(r'\s+([,.:;?!])', r'\1', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# ====================================================================
# MICROSOFT EDGE-TTS GENERATION WITH WORD-LEVEL TIMESTAMPS
# ====================================================================

async def _tts_edge_async(text, out_wav_path, timing_json_path, voice_name, rate, clean_title=""):
    comm = edge_tts.Communicate(text, voice=voice_name, rate=rate, boundary="WordBoundary")
    audio_buffer = bytearray()
    words = []

    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            audio_buffer.extend(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            words.append({
                "word": chunk["text"],
                "start": round(chunk["offset"] / 10_000_000, 3),
                "end": round((chunk["offset"] + chunk["duration"]) / 10_000_000, 3)
            })

    # Convert audio stream to clean standard WAV
    seg = AudioSegment.from_file(io.BytesIO(audio_buffer), format="mp3")
    seg.export(out_wav_path, format="wav")

    # Calculate exact timestamp when title finishes speaking
    title_words = clean_title.split() if clean_title else []
    title_word_count = len(title_words)
    title_end_time = 0.0
    if title_word_count > 0 and len(words) >= title_word_count:
        title_end_time = words[title_word_count - 1]["end"] + 0.15
    elif words:
        title_end_time = min(3.0, words[-1]["end"])

    timing_payload = {
        "title_end_time": round(title_end_time, 3),
        "clean_title": clean_title,
        "words": words
    }

    # Save word-level timestamps & title metadata
    with open(timing_json_path, "w", encoding="utf-8") as f:
        json.dump(timing_payload, f, ensure_ascii=False, indent=2)

    return seg.duration_seconds, len(words)


def generate_tts(date_str, story_name):
    """
    Main TTS entrypoint:
    - Reads story JSON
    - Cleans spoken title (strips [subreddit] and [Part X of Y])
    - Speaks title first, then body text
    - Detects gender and selects voice based on subreddit
    - Generates voiceover and word-level timestamps in seconds
    """
    story_folder = os.path.join(PROJECT_ROOT, "reddit_stories", date_str)
    audio_dir = os.path.join(PROJECT_ROOT, "audio", date_str)
    os.makedirs(audio_dir, exist_ok=True)

    story_path = os.path.join(story_folder, f"story_{story_name}.json")
    out_path = os.path.join(audio_dir, f"voice_{story_name}.wav")
    timing_path = out_path.replace(".wav", "_timing.json")

    if os.path.exists(out_path) and os.path.exists(timing_path):
        print(f"✅ TTS and timings already exist for story {story_name}, skipping...")
        return out_path

    with open(story_path, "r", encoding="utf-8") as f:
        story = json.load(f)

    # 1. Clean spoken title (strip [subreddit] tags so narrator only speaks the title)
    raw_title = story.get("title", "")
    clean_title = re.sub(r"^\[.*?\]\s*", "", raw_title)
    clean_title = re.sub(r"\[Part \d+ of \d+\]", "", clean_title).strip()
    clean_title = clean_text_for_tts(clean_title)

    # 2. Clean body text
    raw_body = story.get("text", "").strip().replace("\n", " ")
    clean_body = clean_text_for_tts(raw_body)

    # 3. Combine: Title spoken first, then body
    if clean_title:
        full_text = f"{clean_title}. {clean_body}"
    else:
        full_text = clean_body

    # Detect author gender
    voice_gender = story.get("voice")
    if voice_gender not in ["male", "female"]:
        combined_text = clean_title + " " + clean_body
        voice_gender = detect_gender(combined_text)
        story["voice"] = voice_gender
        with open(story_path, "w", encoding="utf-8") as f:
            json.dump(story, f, indent=4, ensure_ascii=False)

    # Select voice based on subreddit & gender
    subreddit = story.get("subreddit", "")
    if not subreddit:
        match = re.match(r"^\[(.*?)\]", story.get("title", ""))
        if match:
            subreddit = match.group(1).strip()

    voice_name, rate = get_voice_for_subreddit(subreddit, voice_gender)

    log(f"🎙️ [Story {story_name}] Subreddit: r/{subreddit} | Gender: {voice_gender} ➔ Voice: {voice_name} (rate: {rate})", telegram=True)

    start_t = time.time()
    duration, word_count = asyncio.run(_tts_edge_async(full_text, out_path, timing_path, voice_name, rate, clean_title=clean_title))
    elapsed = time.time() - start_t

    log(f"✅ [Story {story_name}] Voiceover generated: {duration:.1f}s audio ({word_count} words) in {elapsed:.2f}s!", telegram=True)
    return out_path


if __name__ == "__main__":
    from datetime import datetime
    date_str = datetime.now().strftime("%Y%m%d")
    for name in ["1", "2", "3"]:
        generate_tts(date_str, story_name=name)
