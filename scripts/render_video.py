import os
import json
import random
import subprocess
import wave
import tempfile
from pathlib import Path

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GAMEPLAY_DIR = os.path.join(PROJECT_ROOT, "assets", "gameplays")
MUSIC_DIR = os.path.join(PROJECT_ROOT, "assets", "music")
SFX_DIR = os.path.join(PROJECT_ROOT, "assets", "sfx")

_video_duration_cache = {}

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
    Prepares gameplay video input using either:
    - Method A (offset): Random start timestamp from a long video (Default)
    - Method B (montage): Rapid 5-10s dynamic cuts stitched across clips
    """
    all_clips = get_available_gameplay_clips()

    if not all_clips and (not specific_clip_path or not os.path.exists(specific_clip_path)):
        raise FileNotFoundError(f"[ERROR] No gameplay video clips found in {GAMEPLAY_DIR}")

    # Boolean toggle: ENABLE_MONTAGE=false (Method A: Offset) or true (Method B: Montage)
    enable_montage = os.getenv("ENABLE_MONTAGE", "false").strip().lower() in ("true", "1", "yes")
    if not enable_montage and os.getenv("GAMEPLAY_MODE", "").strip().lower() == "montage":
        enable_montage = True

    target_duration = audio_duration + 5.0  # 5 second buffer for safety

    # Determine candidate clip for Method A
    candidate_clip = None
    if specific_clip_path and os.path.exists(specific_clip_path):
        candidate_clip = specific_clip_path
    elif all_clips:
        candidate_clip = random.choice(all_clips)

    if not enable_montage and candidate_clip:
        clip_dur = get_video_duration(candidate_clip)
        if clip_dur >= target_duration:
            max_start = max(0.0, clip_dur - target_duration)
            start_offset = random.uniform(0.0, max_start)
            print(f"[DEBUG] [Method A - Offset] Chosen clip: {os.path.basename(candidate_clip)} (Length: {clip_dur:.1f}s) starting at random offset: {start_offset:.1f}s, duration: {target_duration:.1f}s")
            return [
                "-ss", f"{start_offset:.2f}",
                "-t", f"{target_duration:.2f}",
                "-avoid_negative_ts", "make_zero",
                "-i", candidate_clip.replace("\\", "/")
            ], None

    # ----------------------------------------------------
    # METHOD B: Dynamic Random Montage (or Fallback if clip too short)
    # ----------------------------------------------------
    print(f"[DEBUG] [Method B - Montage] Slicing random 5-10s scenes across clips...")
    selected_slices = []
    accumulated_duration = 0.0
    pool = list(all_clips)
    random.shuffle(pool)

    while accumulated_duration < target_duration and pool:
        clip = pool.pop(0)
        dur = get_video_duration(clip)

        slice_len = min(dur, random.uniform(5.0, 10.0))
        max_start = max(0.0, dur - slice_len)
        start_pt = random.uniform(0.0, max_start)
        end_pt = start_pt + slice_len

        selected_slices.append((clip, start_pt, end_pt))
        accumulated_duration += slice_len

        if not pool:
            pool = list(all_clips)
            random.shuffle(pool)

    print(f"[DEBUG] Stitched {len(selected_slices)} random scenes for total ~{accumulated_duration:.1f}s")

    # Create temporary concat list for FFmpeg using inpoint & outpoint
    temp_concat = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    for clip_path, in_pt, out_pt in selected_slices:
        formatted_path = clip_path.replace("\\", "/")
        temp_concat.write(f"file '{formatted_path}'\n")
        temp_concat.write(f"inpoint {in_pt:.2f}\n")
        temp_concat.write(f"outpoint {out_pt:.2f}\n")
    temp_concat.close()

    input_args = ["-f", "concat", "-safe", "0", "-i", temp_concat.name.replace("\\", "/")]
    return input_args, temp_concat.name


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

    # Prepare gameplay video input (Input 0)
    gameplay_input_args, temp_concat_file = prepare_gameplay_input(audio_duration, specific_clip_path=gameplay_path)

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

    def ffmpeg_path(path):
        return path.replace("\\", "/").replace(":", "\\:")

    audio_path_ffmpeg = audio_path.replace("\\", "/")
    subtitle_path_ffmpeg = ffmpeg_path(subtitle_path)

    w, h = (1080, 1920) if format == "short" else (1920, 1080)

    encoder = get_best_video_encoder()
    print(f"[DEBUG] Using video encoder: {encoder}")

    # Build FFmpeg inputs:
    # Input 0: gameplay video
    # Input 1: voice audio
    input_args = list(gameplay_input_args) + ["-i", audio_path_ffmpeg]
    current_input_idx = 2

    # Input 2 (optional): Card overlay image
    card_idx = None
    if overlay_img_path:
        card_idx = current_input_idx
        current_input_idx += 1
        overlay_path_ffmpeg = overlay_img_path.replace("\\", "/")
        input_args += ["-loop", "1", "-i", overlay_path_ffmpeg]

    # Input (optional): Background music track
    music_tracks = get_available_music_tracks()
    music_idx = None
    if music_tracks:
        chosen_music = random.choice(music_tracks)
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
    # Video Filter Graph Construction
    # ----------------------------------------------------
    if card_idx is not None:
        fade_d = 0.4
        fade_st = max(0.1, title_end_time - fade_d)
        v_filter = (
            f"[{card_idx}:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},format=yuva420p,fade=t=out:st={fade_st:.2f}:d={fade_d:.2f}:alpha=1[card];"
            f"[0:v]fps=30,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1[gameplay];"
            f"[gameplay][card]overlay=0:0:enable='between(t,0,{title_end_time:.2f})':eof_action=pass[v_merged];"
            f"[v_merged]subtitles='{subtitle_path_ffmpeg}'[v_out]"
        )
    else:
        v_filter = (
            f"[0:v]fps=30,"
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},"
            f"setsar=1,"
            f"subtitles='{subtitle_path_ffmpeg}'[v_out]"
        )

    # ----------------------------------------------------
    # Audio Filter Graph: Dynamic Ducking & SFX
    # ----------------------------------------------------
    a_filters = []
    if music_idx is not None:
        # Music base volume + sidechain compression keyed to voiceover [1:a]
        # Music ducks down to ~12% volume while voice is speaking, smoothly swelling to ~22% during pauses/outro
        a_filters.append(
            f"[{music_idx}:a]volume=0.22[m_vol];"
            f"[m_vol][1:a]sidechaincompress=threshold=0.035:ratio=6:attack=150:release=700:makeup=1[m_ducked]"
        )

    if sfx_idx is not None:
        sfx_delay_ms = max(0, int((title_end_time - 0.25) * 1000))
        a_filters.append(
            f"[{sfx_idx}:a]volume=0.45,adelay={sfx_delay_ms}|{sfx_delay_ms}[sfx_del]"
        )

    # Combine audio streams
    if music_idx is not None and sfx_idx is not None:
        a_filters.append(
            f"[1:a][m_ducked][sfx_del]amix=inputs=3:duration=first:dropout_transition=2[a_out]"
        )
    elif music_idx is not None:
        a_filters.append(
            f"[1:a][m_ducked]amix=inputs=2:duration=first:dropout_transition=2[a_out]"
        )
    elif sfx_idx is not None:
        a_filters.append(
            f"[1:a][sfx_del]amix=inputs=2:duration=first:dropout_transition=2[a_out]"
        )
    else:
        a_filters.append(
            f"[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a_out]"
        )

    full_filter_complex = v_filter + ";" + ";".join(a_filters)
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
    finally:
        # Clean up temporary concat file if created
        if temp_concat_file and os.path.exists(temp_concat_file):
            try:
                os.remove(temp_concat_file)
            except Exception:
                pass

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

