import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import json
import random
import subprocess
import wave
import tempfile
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

GAMEPLAY_DIR = os.path.join(PROJECT_ROOT, "assets", "gameplays")
MUSIC_DIR = os.path.join(PROJECT_ROOT, "assets", "music")
SFX_DIR = os.path.join(PROJECT_ROOT, "assets", "sfx")

_video_duration_cache = {}

SUBREDDIT_MOOD_MAP = {
    "NuclearRevenge": "suspense",
    "ProRevenge": "suspense",
    "confessions": "suspense",
    "AITAH": "suspense",
    "AmItheAsshole": "suspense",
    "relationship_advice": "emotional",
    "TrueOffMyChest": "emotional",
    "offmychest": "emotional",
    "Stories": "emotional",
    "pettyrevenge": "chill",
    "tifu": "chill",
    "entitledparents": "chill",
    "EntitledPeople": "chill",
    "maliciouscompliance": "chill",
    "AskReddit": "chill"
}

def detect_story_mood(subreddit="", text=""):
    """
    Detects the optimal background music mood ('suspense', 'emotional', or 'chill')
    based on story context and subreddit.
    """
    text_lower = text.lower() if text else ""
    
    # 1. High-priority keyword signals
    if any(w in text_lower for w in ["cheated", "cheating", "revenge", "caught", "police", "lawyer", "lawsuit", "arrested", "secret affair", "betrayed", "unfaithful"]):
        return "suspense"
    if any(w in text_lower for w in ["crying", "heartbroken", "divorce", "passed away", "passed on", "grief", "brokenhearted", "lost my"]):
        return "emotional"
    if any(w in text_lower for w in ["karen", "petty", "laughed", "boss", "coworker", "embarrassing", "stupid"]):
        return "chill"
        
    # 2. Subreddit default mood mapping
    if subreddit in SUBREDDIT_MOOD_MAP:
        return SUBREDDIT_MOOD_MAP[subreddit]
        
    return "chill"

def get_available_music_tracks():
    """Returns a list of all valid audio tracks in assets/music."""
    valid_exts = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}
    if not os.path.exists(MUSIC_DIR):
        os.makedirs(MUSIC_DIR, exist_ok=True)
    return [
        os.path.join(MUSIC_DIR, f)
        for f in os.listdir(MUSIC_DIR)
        if Path(f).suffix.lower() in valid_exts and not f.startswith(".")
    ]

def get_content_aware_music(subreddit="", text=""):
    """
    Selects the best matching music track from assets/music/ based on story mood.
    Falls back gracefully to any available track.
    """
    all_tracks = get_available_music_tracks()
    if not all_tracks:
        return None
        
    mood = detect_story_mood(subreddit, text)
    mood_tracks = [t for t in all_tracks if os.path.basename(t).lower().startswith(mood)]
    
    if mood_tracks:
        chosen = random.choice(mood_tracks)
        print(f"[DEBUG] [Content-Aware Audio] Matched mood [{mood.upper()}]: {os.path.basename(chosen)}")
        return chosen
        
    # Fallback to random choice from all tracks
    chosen = random.choice(all_tracks)
    print(f"[DEBUG] [Content-Aware Audio] Default track selected: {os.path.basename(chosen)}")
    return chosen


def get_audio_duration(audio_path):
    """Accurately gets audio duration in seconds using wave or ffprobe."""
    try:
        with wave.open(audio_path, 'rb') as f:
            frames = f.getnframes()
            rate = f.getframerate()
            return frames / float(rate)
    except Exception:
        try:
            cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                audio_path
            ]
            out = subprocess.check_output(cmd, text=True).strip()
            return float(out)
        except Exception:
            return 60.0  # Fallback duration estimate


def get_video_duration(video_path):
    """Gets video duration using ffprobe with in-memory caching."""
    video_path_str = str(video_path)
    if video_path_str in _video_duration_cache:
        return _video_duration_cache[video_path_str]

    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path_str
        ]
        out = subprocess.check_output(cmd, text=True).strip()
        dur = float(out)
        _video_duration_cache[video_path_str] = dur
        return dur
    except Exception:
        # Default fallback estimate if ffprobe fails on a clip
        return 7.0


def get_available_gameplay_clips():
    """Returns a list of all valid video clips in assets/gameplays."""
    valid_exts = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
    if not os.path.exists(GAMEPLAY_DIR):
        os.makedirs(GAMEPLAY_DIR, exist_ok=True)

    clips = [
        os.path.join(GAMEPLAY_DIR, f)
        for f in os.listdir(GAMEPLAY_DIR)
        if Path(f).suffix.lower() in valid_exts and not f.startswith(".")
    ]
    return clips


def prepare_gameplay_input(audio_duration, specific_clip_path=None):
    """
    Prepares gameplay video inputs using either:
    - Method A (single offset): Random start timestamp from a long video
    - Method B (montage): Dynamic multi-clip slices stitched seamlessly via concat filter (for YPP)
    """
    all_clips = get_available_gameplay_clips()

    if not all_clips and (not specific_clip_path or not os.path.exists(specific_clip_path)):
        raise FileNotFoundError(f"[ERROR] No gameplay video clips found in {GAMEPLAY_DIR}")

    # Boolean toggle: ENABLE_MONTAGE (defaults to true for YPP compliance)
    enable_montage = os.getenv("ENABLE_MONTAGE", "true").strip().lower() in ("true", "1", "yes")
    if not enable_montage and os.getenv("GAMEPLAY_MODE", "").strip().lower() == "montage":
        enable_montage = True

    target_duration = audio_duration + 5.0  # 5 second buffer for safety

    # ----------------------------------------------------
    # METHOD A: Single Clip Random Offset (when montage disabled or specific clip forced)
    # ----------------------------------------------------
    if not enable_montage or (specific_clip_path and os.path.exists(specific_clip_path)):
        candidate_clip = specific_clip_path if specific_clip_path else random.choice(all_clips)
        clip_dur = get_video_duration(candidate_clip)
        max_start = max(0.0, clip_dur - target_duration)
        start_offset = random.uniform(0.0, max_start)
        print(f"[DEBUG] [Method A - Single Clip] Chosen: {os.path.basename(candidate_clip)} (Length: {clip_dur:.1f}s) starting at {start_offset:.1f}s, duration: {target_duration:.1f}s")
        return [
            "-ss", f"{start_offset:.2f}",
            "-t", f"{target_duration:.2f}",
            "-avoid_negative_ts", "make_zero",
            "-i", candidate_clip.replace("\\", "/")
        ], 1

    # ----------------------------------------------------
    # METHOD B: Multi-Input Filter-Graph Montage (100% Stable, No Black Screens)
    # ----------------------------------------------------
    print(f"[DEBUG] [Method B - YPP Montage] Slicing dynamic 5-8s scenes across gameplay clips...")
    selected_slices = []
    accumulated_duration = 0.0
    pool = list(all_clips)
    random.shuffle(pool)

    while accumulated_duration < target_duration and pool:
        clip = pool.pop(0)
        dur = get_video_duration(clip)

        slice_len = min(dur, random.uniform(5.0, 8.0))
        max_start = max(0.0, dur - slice_len)
        start_pt = random.uniform(0.0, max_start)

        selected_slices.append((clip, start_pt, slice_len))
        accumulated_duration += slice_len

        if not pool:
            pool = list(all_clips)
            random.shuffle(pool)

    print(f"[DEBUG] Assembled {len(selected_slices)} dynamic video cuts (total ~{accumulated_duration:.1f}s)")

    input_args = []
    for clip_path, in_pt, slice_len in selected_slices:
        input_args.extend([
            "-ss", f"{in_pt:.2f}",
            "-t", f"{slice_len:.2f}",
            "-avoid_negative_ts", "make_zero",
            "-i", clip_path.replace("\\", "/")
        ])

    return input_args, len(selected_slices)


_detected_encoder = None
def get_best_video_encoder():
    global _detected_encoder
    if _detected_encoder is not None:
        return _detected_encoder

    custom = os.getenv("VIDEO_ENCODER")
    if custom:
        _detected_encoder = custom
        return _detected_encoder

    # Check if NVIDIA hardware acceleration (h264_nvenc) is available
    try:
        res = subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "lavfi", "-i", "nullsrc=s=64x64:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        if res.returncode == 0:
            _detected_encoder = "h264_nvenc"
            return _detected_encoder
    except Exception:
        pass

    _detected_encoder = "libx264"
    return _detected_encoder


def render_video(date_str, gameplay_path=None, story_name=1, format="short"):
    print(f"[DEBUG] Starting render_video for story: {story_name} on date: {date_str}, format: {format}")

    audio_path = os.path.abspath(os.path.join(PROJECT_ROOT, f"audio/{date_str}/voice_{story_name}.wav"))
    subtitle_path = os.path.abspath(os.path.join(PROJECT_ROOT, f"subtitles/{date_str}_{story_name}_{format}.ass"))
    output_dir = os.path.join(PROJECT_ROOT, f"output/{date_str}")
    output_path = os.path.abspath(os.path.join(output_dir, f"final_{story_name}.mp4"))

    os.makedirs(output_dir, exist_ok=True)

    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"[ERROR] Audio file not found: {audio_path}")
    if not os.path.exists(subtitle_path):
        raise FileNotFoundError(f"[ERROR] Subtitle file not found: {subtitle_path}")

    audio_duration = get_audio_duration(audio_path)
    print(f"[DEBUG] Audio duration: {audio_duration:.2f}s")

    # Prepare gameplay video inputs (Method A single or Method B montage)
    gameplay_input_args, num_gameplay_inputs = prepare_gameplay_input(audio_duration, specific_clip_path=gameplay_path)

    # Check if transparent card overlay exists for live gameplay video intro
    card_path = os.path.abspath(os.path.join(PROJECT_ROOT, f"reddit_stories/{date_str}/card_{story_name}.png"))
    thumb_path = os.path.abspath(os.path.join(PROJECT_ROOT, f"reddit_stories/{date_str}/thumb_{story_name}.png"))
    
    overlay_img_path = None
    if os.path.exists(card_path):
        overlay_img_path = card_path
    elif os.path.exists(thumb_path):
        overlay_img_path = thumb_path

    # Read title duration from timing JSON
    timing_path = os.path.abspath(os.path.join(PROJECT_ROOT, f"audio/{date_str}/voice_{story_name}_timing.json"))
    title_end_time = 3.0
    if os.path.exists(timing_path):
        try:
            with open(timing_path, "r", encoding="utf-8") as f:
                tdata = json.load(f)
                if isinstance(tdata, dict):
                    title_end_time = float(tdata.get("title_end_time", 3.0))
        except Exception:
            pass

    # Read story JSON for content-aware audio and subtitle matching
    story_json_path = os.path.abspath(os.path.join(PROJECT_ROOT, f"reddit_stories/{date_str}/story_{story_name}.json"))
    story_subreddit = ""
    story_text = ""
    if os.path.exists(story_json_path):
        try:
            with open(story_json_path, "r", encoding="utf-8") as f:
                sdata = json.load(f)
                if isinstance(sdata, dict):
                    story_subreddit = sdata.get("subreddit", "")
                    story_text = sdata.get("text", "")
        except Exception:
            pass

    def ffmpeg_path(path):
        return path.replace("\\", "/").replace(":", "\\:")

    audio_path_ffmpeg = audio_path.replace("\\", "/")
    subtitle_path_ffmpeg = ffmpeg_path(subtitle_path)

    w, h = (1080, 1920) if format == "short" else (1920, 1080)

    encoder = get_best_video_encoder()
    print(f"[DEBUG] Using video encoder: {encoder}")

    # Build FFmpeg inputs:
    # Inputs 0..num_gameplay_inputs-1: gameplay video slices
    # Input num_gameplay_inputs: voice audio
    input_args = list(gameplay_input_args) + ["-i", audio_path_ffmpeg]
    voice_idx = num_gameplay_inputs
    current_input_idx = num_gameplay_inputs + 1

    # Input (optional): Card overlay image
    card_idx = None
    if overlay_img_path:
        card_idx = current_input_idx
        current_input_idx += 1
        overlay_path_ffmpeg = overlay_img_path.replace("\\", "/")
        input_args += ["-loop", "1", "-i", overlay_path_ffmpeg]

    # Input (optional): Background music track (Content-Aware Selection)
    chosen_music = get_content_aware_music(subreddit=story_subreddit, text=story_text)
    music_idx = None
    if chosen_music:
        music_idx = current_input_idx
        current_input_idx += 1
        input_args += ["-stream_loop", "-1", "-i", chosen_music.replace("\\", "/")]
        print(f"[DEBUG] Layering background music: {os.path.basename(chosen_music)}")

    # Input (optional): Whoosh transition SFX
    whoosh_path = os.path.join(SFX_DIR, "whoosh.wav")
    sfx_idx = None
    if card_idx is not None and os.path.exists(whoosh_path):
        sfx_idx = current_input_idx
        current_input_idx += 1
        input_args += ["-i", whoosh_path.replace("\\", "/")]
        print(f"[DEBUG] Layering card transition SFX at t={title_end_time:.2f}s")

    # ----------------------------------------------------
    # Video Filter Graph Construction (Multi-Input Concat + Overlay + Subs)
    # ----------------------------------------------------
    v_filters = []
    if num_gameplay_inputs == 1:
        v_filters.append(f"[0:v]fps=30,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,setpts=PTS-STARTPTS[gameplay]")
    else:
        # Pre-format each slice to identical 1080x1920 30fps with normalized timestamps and concatenate in memory
        slice_labels = []
        for k in range(num_gameplay_inputs):
            v_filters.append(f"[{k}:v]fps=30,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,setpts=PTS-STARTPTS[v_sl_{k}]")
            slice_labels.append(f"[v_sl_{k}]")
        v_filters.append(f"{''.join(slice_labels)}concat=n={num_gameplay_inputs}:v=1:a=0[gameplay]")

    if card_idx is not None:
        fade_d = 0.4
        fade_st = max(0.1, title_end_time - fade_d)
        v_filters.append(
            f"[{card_idx}:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},format=yuva420p,fade=t=out:st={fade_st:.2f}:d={fade_d:.2f}:alpha=1[card]"
        )
        v_filters.append(
            f"[gameplay][card]overlay=0:0:enable='between(t,0,{title_end_time:.2f})':eof_action=pass[v_merged]"
        )
        v_filters.append(
            f"[v_merged]subtitles='{subtitle_path_ffmpeg}'[v_out]"
        )
    else:
        v_filters.append(
            f"[gameplay]subtitles='{subtitle_path_ffmpeg}'[v_out]"
        )

    # ----------------------------------------------------
    # Audio Filter Graph: Dynamic Ducking & SFX
    # ----------------------------------------------------
    a_filters = []
    if music_idx is not None:
        # Split voice audio into main mix and sidechain trigger
        music_base_vol = float(os.getenv("MUSIC_BASE_VOLUME", "0.12"))
        a_filters.append(
            f"[{voice_idx}:a]asplit=2[v_main][v_sc];"
            f"[{music_idx}:a]volume={music_base_vol:.2f}[m_vol];"
            f"[m_vol][v_sc]sidechaincompress=threshold=0.030:ratio=8:attack=150:release=650:makeup=1[m_ducked]"
        )
        voice_stream_label = "[v_main]"
    else:
        voice_stream_label = f"[{voice_idx}:a]"

    if sfx_idx is not None:
        sfx_delay_ms = max(0, int((title_end_time - 0.25) * 1000))
        a_filters.append(
            f"[{sfx_idx}:a]volume=0.30,adelay={sfx_delay_ms}|{sfx_delay_ms}[sfx_del]"
        )

    # Combine audio streams
    if music_idx is not None and sfx_idx is not None:
        a_filters.append(
            f"{voice_stream_label}[m_ducked][sfx_del]amix=inputs=3:duration=first:dropout_transition=2[a_out]"
        )
    elif music_idx is not None:
        a_filters.append(
            f"{voice_stream_label}[m_ducked]amix=inputs=2:duration=first:dropout_transition=2[a_out]"
        )
    elif sfx_idx is not None:
        a_filters.append(
            f"{voice_stream_label}[sfx_del]amix=inputs=2:duration=first:dropout_transition=2[a_out]"
        )
    else:
        a_filters.append(
            f"{voice_stream_label}aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a_out]"
        )

    full_filter_complex = ";".join(v_filters) + ";" + ";".join(a_filters)
    map_args = ["-filter_complex", full_filter_complex, "-map", "[v_out]", "-map", "[a_out]"]

    threads_count = "2" if encoder == "libx264" else "0"

    cmd = [
        "ffmpeg",
        "-y"
    ] + input_args + [
        "-t", f"{audio_duration:.2f}",
        "-c:v", encoder,
        "-preset", "ultrafast",
        "-crf", "24",
        "-threads", threads_count,
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart"
    ] + map_args + [
        "-shortest",
        "-map_metadata", "-1",       # Strip all source metadata
        "-metadata", "title=",        # Remove container title
        "-metadata:s:v:0", "title=",  # Remove video stream title
        "-metadata:s:a:0", "title=",  # Remove audio stream title
        output_path
    ]

    print(f"[DEBUG] Running FFmpeg command:\n{' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        print(f"[DEBUG] FFmpeg STDOUT:\n{result.stdout}")
        print(f"[DEBUG] FFmpeg STDERR:\n{result.stderr}")
    except subprocess.CalledProcessError as e:
        error_msg = f"[ERROR] FFmpeg failed with exit code {e.returncode}:\n{e.stderr}"
        print(error_msg)
        raise RuntimeError(error_msg)

    if not os.path.exists(output_path):
        raise FileNotFoundError(f"[ERROR] Output video not created at: {output_path}")

    # Ensure thumbnail exists for YouTube upload (preserve pristine PIL card composite)
    extracted_thumb_path = os.path.abspath(os.path.join(PROJECT_ROOT, f"reddit_stories/{date_str}/thumb_{story_name}.png"))
    if not os.path.exists(extracted_thumb_path):
        try:
            # Fallback extraction from video intro during title display
            extract_time = f"{max(0.2, min(1.0, title_end_time * 0.4)):.2f}"
            extract_cmd = [
                "ffmpeg", "-y",
                "-i", output_path,
                "-ss", extract_time,
                "-frames:v", "1",
                "-update", "1",
                extracted_thumb_path
            ]
            subprocess.run(extract_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            print(f"[SUCCESS] Extracted video frame thumbnail: {extracted_thumb_path}")
        except Exception as e:
            print(f"⚠️ Thumbnail extraction warning: {e}")
    else:
        print(f"[SUCCESS] Verified composite thumbnail: {extracted_thumb_path}")

    print(f"[SUCCESS] Video rendered successfully at: {output_path}")
    return output_path

