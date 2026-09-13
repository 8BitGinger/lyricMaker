"""
lyric_video_app.py — Windows pygame GUI for lyric video creation.

Browse for audio, background video, and an optional LRC lyrics file.
If no lyrics file is selected, audio is auto-transcribed with Whisper.
Output is saved automatically to the Desktop.

Requirements:
    pip install pygame moviepy pillow numpy faster-whisper proglog
"""

import os
import re
import sys
import tempfile
import threading
import subprocess
from datetime import datetime
from pathlib import Path

import pygame
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# MoviePy 1.0.3 references the removed PIL.Image.ANTIALIAS (Pillow 10+ renamed it)
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.LANCZOS

from moviepy.editor import (
    VideoFileClip,
    AudioFileClip,
    VideoClip,
)
import proglog


# ── UI theme ──────────────────────────────────────────────────────────────────

WIN_W, WIN_H = 740, 560
FPS          = 30

BG        = ( 18,  18,  30)
PANEL     = ( 28,  28,  45)
BORDER    = ( 60,  62,  90)
ACCENT    = ( 90, 120, 220)
ACCENT_H  = (120, 150, 255)
ACCENT_ON = (130, 160, 255)
BTN_OK    = ( 55, 155,  75)
BTN_OK_H  = ( 70, 185,  90)
BTN_DIS   = ( 50,  50,  70)
TEXT_PRI  = (230, 230, 240)
TEXT_SEC  = (150, 150, 170)
TEXT_DIM  = ( 90,  90, 110)
BAR_BG    = ( 38,  38,  58)
BAR_PHASE = ( 80, 160, 230)
BAR_TOTAL = ( 60, 200, 120)
DIVIDER   = ( 48,  52,  78)
RED       = (220,  70,  70)
GREEN_OK  = ( 70, 200, 100)

DEFAULT_VIDEO_FONT = r"C:\Windows\Fonts\arialbd.ttf"
FALLBACK_FONT      = r"C:\Windows\Fonts\arial.ttf"

DD_VISIBLE = 8    # items shown in open dropdown
DD_ITEM_H  = 26   # height of each dropdown item

REVIEW_ROW_H = 26   # height of each lyric row in the review screen


# ── System font discovery ─────────────────────────────────────────────────────

def _scan_fonts():
    """Return sorted list of (display_name, full_path) for all Windows fonts."""
    font_dir = Path(r"C:\Windows\Fonts")
    entries = []
    for ext in ("*.ttf", "*.otf", "*.TTF", "*.OTF"):
        for f in font_dir.glob(ext):
            try:
                name = ImageFont.truetype(str(f), 12).getname()
                display = f"{name[0]}  {name[1]}".strip() if name[1] else name[0]
            except Exception:
                display = f.stem
            entries.append((display, str(f)))
    entries.sort(key=lambda x: x[0].lower())
    return entries


# ── Shared state (worker writes, main thread reads) ───────────────────────────

class _State:
    def __init__(self):
        self._lock    = threading.Lock()
        self._phase   = 0.0
        self._overall = 0.0
        self._status  = "Ready"
        self._done    = False
        self._error   = None
        self._lyrics  = None

    def set(self, **kw):
        with self._lock:
            for k, v in kw.items():
                object.__setattr__(self, f"_{k}", v)

    def snapshot(self):
        with self._lock:
            return dict(
                phase   = self._phase,
                overall = self._overall,
                status  = self._status,
                done    = self._done,
                error   = self._error,
                lyrics  = self._lyrics,
            )


# ── LRC parser ────────────────────────────────────────────────────────────────

def parse_lrc(lrc_path, last_line_duration=5.0):
    ts_re   = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
    entries = []
    with open(lrc_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            matches = ts_re.findall(line)
            if not matches:
                continue
            text = ts_re.sub("", line).strip()
            for m, s in matches:
                entries.append([int(m) * 60 + float(s), None, text])
    entries.sort(key=lambda e: e[0])
    for i in range(len(entries) - 1):
        entries[i][1] = entries[i + 1][0]
    if entries:
        entries[-1][1] = entries[-1][0] + last_line_duration
    return entries


# ── Video geometry ────────────────────────────────────────────────────────────

def get_resolution(ratio):
    return (1920, 1080) if ratio == "16:9" else (1080, 1920)


def fit_video(clip, tw, th):
    cr = clip.w / clip.h
    tr = tw / th
    if cr > tr:
        nh = th
        nw = int(round(clip.w * (th / clip.h)))
    else:
        nw = tw
        nh = int(round(clip.h * (tw / clip.w)))
    return clip.resize((nw, nh)).crop(
        x_center=nw / 2, y_center=nh / 2, width=tw, height=th
    )


# ── PIL text rendering (replaces ImageMagick TextClip) ───────────────────────

_COLOR_MAP = {
    "white":   (255, 255, 255),
    "black":   (  0,   0,   0),
    "yellow":  (255, 225,  35),
    "red":     (245,  60,  60),
    "blue":    ( 55, 130, 255),
    "green":   ( 60, 215,  85),
    "cyan":    (  0, 230, 230),
    "magenta": (230,   0, 230),
    "gray":    (160, 160, 160),
    "orange":  (255, 165,   0),
}

TEXT_COLORS = [
    ("White",  "white",  (255, 255, 255)),
    ("Black",  "black",  ( 20,  20,  25)),
    ("Blue",   "blue",   ( 55, 130, 255)),
    ("Green",  "green",  ( 60, 215,  85)),
    ("Yellow", "yellow", (255, 225,  35)),
    ("Red",    "red",    (245,  60,  60)),
]


def _parse_color(c):
    if isinstance(c, (list, tuple)):
        return tuple(int(x) for x in c[:3])
    return _COLOR_MAP.get(str(c).lower(), (255, 255, 255))


def _text_width(font, text):
    """Measure text width, compatible with Pillow < 9.2 and >= 9.2."""
    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]
    except AttributeError:
        return font.getsize(text)[0]


def _wrap_text(text, font, max_w):
    words = text.split()
    lines, cur = [], []
    for word in words:
        probe = " ".join(cur + [word])
        if _text_width(font, probe) > max_w and cur:
            lines.append(" ".join(cur))
            cur = [word]
        else:
            cur.append(word)
    if cur:
        lines.append(" ".join(cur))
    return lines or [""]


def _load_font(font_path, size):
    for path in (font_path, DEFAULT_VIDEO_FONT, FALLBACK_FONT):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def make_text_image(text, cw, ch, font_path, font_size,
                    color, stroke_color, stroke_w, position, ratio="16:9"):
    """Return an RGBA numpy array (ch, cw, 4) with text drawn on transparency."""
    img  = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _load_font(font_path, font_size)

    # Safe margin wrapping: 9:16 vertical videos need tighter width to avoid TikTok right-side buttons
    wrap_ratio = 0.82 if ratio == "9:16" else 0.88
    lines   = _wrap_text(text, font, int(cw * wrap_ratio))
    lh      = int(font_size * 1.25)
    total_h = len(lines) * lh

    # Safe zone padding: TikTok/Reels/Shorts bottom overlay covers 20-25% of height!
    if ratio == "9:16":
        pad_bottom = int(ch * 0.22)   # 22% safe zone for vertical social platforms
        pad_top    = int(ch * 0.12)   # 12% top safe zone
    else:
        pad_bottom = int(ch * 0.08)
        pad_top    = int(ch * 0.08)

    y0 = {"bottom": ch - total_h - pad_bottom,
          "top":    pad_top,
          "center": (ch - total_h) // 2}.get(position, ch - total_h - pad_bottom)

    tc = _parse_color(color)        + (255,)
    sc = _parse_color(stroke_color) + (220,)

    # Multi-pass outline + drop shadow to ensure legibility when compressed on social platforms
    effective_stroke = max(stroke_w, 3)
    for i, line in enumerate(lines):
        lw = _text_width(font, line)
        x  = (cw - lw) // 2
        y  = y0 + i * lh

        # Soft shadow behind text
        shadow_col = (0, 0, 0, 160)
        for dx in range(-effective_stroke - 1, effective_stroke + 2):
            for dy in range(1, effective_stroke + 3):
                draw.text((x + dx, y + dy), line, font=font, fill=shadow_col)

        # Stroke / outline
        for dx in range(-effective_stroke, effective_stroke + 1):
            for dy in range(-effective_stroke, effective_stroke + 1):
                if dx or dy:
                    draw.text((x + dx, y + dy), line, font=font, fill=sc)
        draw.text((x, y), line, font=font, fill=tc)

    return np.array(img)


def build_lyric_overlays(lyrics, tw, th, font_path, font_size,
                          color, stroke_color, stroke_w, position, ratio, state):
    """Pre-render each unique lyric line as an RGBA numpy array (h, w, 4)."""
    unique = list(dict.fromkeys(t for _, _, t in lyrics if t.strip()))
    overlays = {}
    total = len(unique) or 1
    for i, text in enumerate(unique):
        overlays[text] = make_text_image(
            text, tw, th, font_path, font_size,
            color, stroke_color, stroke_w, position, ratio,
        )
        state.set(phase=(i + 1) / total)
    return overlays


# ── Transcription & Alignment ────────────────────────────────────────────────

def _cuda_available():
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def isolate_vocals_demucs(audio_path, state):
    """Separate vocals from music mix using Demucs."""
    state.set(status="Isolating vocals with Demucs (reduces background noise)…", phase=0.05)
    temp_dir = tempfile.mkdtemp(prefix="lyric_demucs_")
    cmd = [
        sys.executable, "-m", "demucs",
        "--two-stems", "vocals",
        "-o", temp_dir,
        audio_path,
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            return None, temp_dir
        base_name = os.path.splitext(os.path.basename(audio_path))[0]
        for root, _, files in os.walk(temp_dir):
            if "vocals.wav" in files and base_name in root:
                return os.path.join(root, "vocals.wav"), temp_dir
    except Exception:
        pass
    return None, temp_dir


def align_with_whisperx(audio_path, lyrics_text_path, state, language="en"):
    """Forced alignment using WhisperX (guarantees 100% correct words)."""
    state.set(status="Loading WhisperX forced alignment…", phase=0.1)
    import whisperx

    device = "cuda" if _cuda_available() else "cpu"
    with open(lyrics_text_path, "r", encoding="utf-8", errors="replace") as f:
        raw_lines = [ln.strip() for ln in f.readlines()]
    lyric_lines = [ln for ln in raw_lines if ln]
    if not lyric_lines:
        raise ValueError("Lyrics text file is empty.")

    full_text = " ".join(lyric_lines)
    state.set(status=f"Loading audio into WhisperX ({device})…", phase=0.2)
    audio = whisperx.load_audio(audio_path)

    state.set(status="Loading alignment model…", phase=0.4)
    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)

    duration = len(audio) / whisperx.audio.SAMPLE_RATE
    segments = [{"text": full_text, "start": 0.0, "end": duration}]

    state.set(status="Aligning lyrics to audio timestamps…", phase=0.6)
    result = whisperx.align(
        segments, align_model, metadata, audio, device, return_char_alignments=False
    )

    words = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []):
            if "start" in w:
                words.append((w["start"], w.get("end", w["start"] + 0.5), w["word"].strip()))

    if not words:
        raise RuntimeError("WhisperX alignment found no matching words in audio.")

    state.set(phase=0.9)
    return regroup_aligned_lines(words, lyric_lines)


def _clean_word(w):
    """Normalize word for fuzzy matching (lowercase, alphanumeric only)."""
    return re.sub(r"[^\w]", "", w.lower())


def align_lyrics_via_transcription(audio_path, lyrics_text_path, state, model_size="large-v3-turbo"):
    """Map the exact uploaded lyrics lines onto timestamps detected by Whisper.
    
    This guarantees 100% of YOUR exact words and line structure are preserved,
    while using Whisper purely to detect when each line begins and ends.
    """
    import difflib

    with open(lyrics_text_path, "r", encoding="utf-8", errors="replace") as f:
        raw_lines = [ln.strip() for ln in f.readlines()]
    user_lines = [ln for ln in raw_lines if ln]
    if not user_lines:
        raise ValueError("Uploaded lyrics file is empty.")

    hint = " ".join(user_lines)[:900]
    segments, raw_words = transcribe_audio_words(audio_path, state, hint=hint, model_size=model_size)

    if not raw_words and not segments:
        raise RuntimeError("No speech detected in audio to align lyrics.")

    # Flatten transcribed words and user words
    # user_words_map: list of (user_word, line_index)
    user_words_map = []
    for line_idx, line in enumerate(user_lines):
        words = line.split()
        for w in words:
            user_words_map.append((_clean_word(w), line_idx))

    user_clean = [w for w, _ in user_words_map]
    trans_clean = [_clean_word(w[2]) for w in raw_words]

    # Use SequenceMatcher to find matching blocks between user lyrics and Whisper audio
    matcher = difflib.SequenceMatcher(None, user_clean, trans_clean)
    matching_blocks = matcher.get_matching_blocks()

    # Track start/end timestamps assigned to each user line
    line_starts = [None] * len(user_lines)
    line_ends   = [None] * len(user_lines)

    for u_start, t_start, length in matching_blocks:
        for i in range(length):
            u_idx = u_start + i
            t_idx = t_start + i
            line_idx = user_words_map[u_idx][1]
            t_word = raw_words[t_idx]
            w_start, w_end = t_word[0], t_word[1]

            if line_starts[line_idx] is None or w_start < line_starts[line_idx]:
                line_starts[line_idx] = w_start
            if line_ends[line_idx] is None or w_end > line_ends[line_idx]:
                line_ends[line_idx] = w_end

    # Interpolate missing timestamps smoothly
    last_known = 0.0
    for idx in range(len(user_lines)):
        if line_starts[idx] is None:
            # Look ahead for next known timestamp
            next_known = None
            for ahead in range(idx + 1, len(user_lines)):
                if line_starts[ahead] is not None:
                    next_known = line_starts[ahead]
                    break
            if next_known is not None:
                step = (next_known - last_known) / (ahead - idx + 1)
                line_starts[idx] = last_known + step
                line_ends[idx]   = line_starts[idx] + max(step * 0.9, 0.5)
            else:
                line_starts[idx] = last_known + 2.5
                line_ends[idx]   = line_starts[idx] + 3.0
        last_known = line_starts[idx]

    # Build final [start, end, exact_user_text] list
    entries = []
    for idx, text in enumerate(user_lines):
        start = line_starts[idx]
        end = line_ends[idx] if line_ends[idx] and line_ends[idx] > start else start + 3.0
        entries.append([round(start, 2), round(end, 2), text])

    # Ensure monotonic timing
    for i in range(len(entries) - 1):
        if entries[i][1] < entries[i + 1][0]:
            entries[i][1] = entries[i + 1][0]
    if entries:
        entries[-1][1] = entries[-1][0] + 5.0

    return entries


def regroup_aligned_lines(words, original_lines, last_line_duration=5.0):
    """Map aligned words back onto original line breaks."""
    entries = []
    word_idx = 0
    for line_text in original_lines:
        words_in_line = [w for w in line_text.split() if w]
        if not words_in_line:
            continue
        count = len(words_in_line)
        chunk = words[word_idx:word_idx + count]
        word_idx += count

        if chunk:
            start = chunk[0][0]
            end = chunk[-1][1] if len(chunk[-1]) > 1 else start + 3.0
            entries.append([start, end, line_text])
        else:
            prev_end = entries[-1][1] if entries else 0.0
            entries.append([prev_end, prev_end + 3.0, line_text])

    for i in range(len(entries) - 1):
        if entries[i][1] < entries[i + 1][0]:
            entries[i][1] = entries[i + 1][0]
    if entries:
        entries[-1][1] = entries[-1][0] + last_line_duration
    return entries


def transcribe_audio_words(audio_path, state, hint=None, model_size="large-v3-turbo"):
    """Transcribe audio returning (segments, [(start, end, word), ...])."""
    state.set(status=f"Loading Whisper model ({model_size})…")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "faster-whisper is not installed.\n"
            "Run:  pip install faster-whisper"
        )

    try:
        model = WhisperModel(model_size, device="auto", compute_type="auto")
    except Exception:
        try:
            model = WhisperModel("medium", device="auto", compute_type="auto")
        except Exception:
            model = WhisperModel("base", device="auto", compute_type="auto")

    label = "Transcribing audio to align lyrics…" if hint else "Transcribing audio (VAD filtered)…"
    state.set(status=label, phase=0.05)

    kwargs = {
        "word_timestamps": True,
        "vad_filter": True,
        "vad_parameters": dict(min_silence_duration_ms=400),
        "condition_on_previous_text": False,
    }
    if hint:
        kwargs["initial_prompt"] = hint[:900]

    segments_gen, info = model.transcribe(audio_path, **kwargs)
    total_dur = max(getattr(info, "duration", 0) or 1.0, 1.0)

    segments = []
    raw_words = []
    for seg in segments_gen:
        text = seg.text.strip()
        if text:
            segments.append([seg.start, seg.end, text])
        if seg.words:
            for w in seg.words:
                raw_words.append((w.start, w.end, w.word.strip()))
        state.set(phase=min(seg.end / total_dur, 0.99))

    return segments, raw_words


def transcribe_audio(audio_path, state, hint=None, model_size="large-v3-turbo"):
    """Transcribe audio with faster-whisper with VAD filtering to avoid hallucinations."""
    segments, _ = transcribe_audio_words(audio_path, state, hint=hint, model_size=model_size)
    if not segments:
        raise RuntimeError(
            "Transcription produced no output.\n"
            "Verify the audio file contains vocals."
        )
    return segments


# ── MoviePy encoding progress bridge ─────────────────────────────────────────

class _EncodeLogger(proglog.ProgressBarLogger):
    """Pipes MoviePy frame progress into the shared State object."""

    def __init__(self, state, o_start, o_end):
        super().__init__(logged_bars="all", min_time_interval=0.25)
        self._state   = state
        self._o_start = o_start
        self._o_end   = o_end

    def bars_callback(self, bar, attr, value, old_value=None):
        if attr == "index":
            total = (self.bars.get(bar) or {}).get("total") or 1
            frac  = min(value / total, 1.0)
            ov    = self._o_start + frac * (self._o_end - self._o_start)
            self._state.set(
                phase   = frac,
                overall = ov,
                status  = f"Encoding video…  {int(frac * 100)}%",
            )


# ── Background workers ────────────────────────────────────────────────────────

def _acquire_lyrics(cfg, state):
    """Parse, align, or transcribe lyrics for cfg."""
    lyric_file = cfg["lyrics"]
    audio_path = cfg["audio"]
    isolate_vocal = cfg.get("isolate_vocals", False)
    model_size = cfg.get("model_size", "large-v3-turbo")

    work_audio = audio_path
    temp_dir = None

    if isolate_vocal:
        vocal_audio, temp_dir = isolate_vocals_demucs(audio_path, state)
        if vocal_audio and os.path.exists(vocal_audio):
            work_audio = vocal_audio
            state.set(status="Vocals isolated. Proceeding with timing…")
        else:
            state.set(status="Demucs vocal isolation not available; using original audio…")

    try:
        if lyric_file and lyric_file.lower().endswith(".lrc"):
            # Timed LRC file — parse timestamps directly
            state.set(status="Parsing LRC file…", phase=0.0, overall=0.05)
            lyrics = parse_lrc(lyric_file)
            if not lyrics:
                raise RuntimeError(
                    "LRC file contained no timed lines.\n"
                    "Each line must start with a timestamp like [00:12.50]."
                )
            state.set(status=f"Parsed {len(lyrics)} lyric lines.", phase=1.0, overall=1.0)

        elif lyric_file:
            # Plain text lyrics: preserve 100% of the user's text and line structure,
            # using Whisper/WhisperX purely to detect timestamps and placement.
            success = False
            try:
                state.set(status="Aligning uploaded lyrics with WhisperX…", phase=0.0, overall=0.05)
                lyrics = align_with_whisperx(work_audio, lyric_file, state)
                if lyrics:
                    success = True
                    state.set(status=f"Aligned {len(lyrics)} lyric lines with WhisperX.", phase=1.0, overall=1.0)
            except Exception:
                success = False

            if not success:
                state.set(status="Aligning uploaded lyrics lines to audio timing…", phase=0.0, overall=0.05)
                lyrics = align_lyrics_via_transcription(work_audio, lyric_file, state, model_size=model_size)
                state.set(overall=1.0, phase=1.0,
                          status=f"Matched {len(lyrics)} uploaded lyric lines to music timing.")

        else:
            # Blind transcription
            state.set(status="Starting transcription…", phase=0.0, overall=0.05)
            lyrics = transcribe_audio(work_audio, state, model_size=model_size)
            state.set(overall=1.0, phase=1.0,
                      status=f"Transcribed {len(lyrics)} lyric segments.")

        return lyrics
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try:
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass


def _run_lyrics_phase(cfg, state):
    """Worker: acquire lyrics only, then hand off to the review/approve screen."""
    try:
        lyrics = _acquire_lyrics(cfg, state)
        state.set(lyrics=lyrics, done=True, error=None,
                  status="Lyrics ready — review below, then approve to render.")
    except Exception as exc:
        state.set(error=str(exc), done=True, status=f"Error: {exc}")


def _run_render(cfg, lyrics, state):
    """Worker: render the final video from already-approved lyrics."""
    try:
        desktop = Path(os.path.expandvars("%USERPROFILE%")) / "Desktop"
        ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
        output  = str(desktop / f"lyric_video_{ts}.mp4")

        # ── Load clips
        state.set(status="Loading background video…", phase=0.0, overall=0.0)
        video = VideoFileClip(cfg["video"])

        state.set(status="Loading audio…", phase=0.5, overall=0.05)
        audio = AudioFileClip(cfg["audio"])

        if video.duration < audio.duration:
            state.set(status="Looping background video to match song length…")
            video = video.loop(duration=audio.duration)
        else:
            video = video.subclip(0, audio.duration)
        video = video.set_audio(audio)

        tw, th = get_resolution(cfg["ratio"])
        state.set(status=f"Fitting video to {cfg['ratio']} ({tw}×{th})…",
                  phase=1.0, overall=0.1)
        video = fit_video(video, tw, th)

        # ── Pre-render lyric overlays (PIL — no ImageMagick)
        state.set(status="Rendering lyric overlays…", phase=0.0, overall=0.1)
        overlays = build_lyric_overlays(
            lyrics, tw, th,
            cfg["font"],
            cfg["font_size"],
            cfg["color"],
            cfg["stroke_color"],
            cfg["stroke_width"],
            cfg["position"],
            cfg["ratio"],
            state,
        )

        # Sort lyrics once for fast per-frame lookup
        timed = sorted((s, e, t) for s, e, t in lyrics if t.strip())

        def make_frame(t):
            base = video.get_frame(t)
            for start, end, text in timed:
                if start <= t < end:
                    ov = overlays.get(text)
                    if ov is not None:
                        alpha   = ov[:, :, 3:4].astype(np.float32) / 255.0
                        rgb     = ov[:, :, :3].astype(np.float32)
                        blended = base.astype(np.float32) * (1.0 - alpha) + rgb * alpha
                        return blended.astype(np.uint8)
                    break
            return base

        state.set(status="Building final video…", phase=1.0, overall=0.5)
        final = VideoClip(make_frame, duration=audio.duration).set_audio(audio)

        # ── Encode with high-quality settings to resist social platform compression
        enc_logger = _EncodeLogger(state, o_start=0.5, o_end=1.0)
        final.write_videofile(
            output,
            fps         = video.fps or 30,
            codec       = "libx264",
            audio_codec = "aac",
            bitrate     = "16M",
            audio_bitrate = "320k",
            audio_fps   = 48000,
            preset      = "medium",
            ffmpeg_params = [
                "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-profile:v", "high",
                "-level", "4.2",
                "-movflags", "+faststart",
            ],
            threads     = os.cpu_count() or 4,
            logger      = enc_logger,
        )

        state.set(
            status  = f"Done!   Saved → {output}",
            phase   = 1.0,
            overall = 1.0,
            done    = True,
            error   = None,
        )

    except Exception as exc:
        state.set(error=str(exc), done=True,
                  status=f"Error: {exc}")


# ── pygame helpers ────────────────────────────────────────────────────────────

def _browse_file(title, filetypes):
    """Open a Windows file dialog (hidden Tk root so no blank window appears)."""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(title=title, filetypes=filetypes)
    root.destroy()
    return path or ""


def _draw_bar(surf, rect, frac, color):
    r = pygame.Rect(rect)
    pygame.draw.rect(surf, BAR_BG, r, border_radius=4)
    if frac > 0:
        fill = r.copy()
        fill.width = max(8, int(r.width * min(frac, 1.0)))
        pygame.draw.rect(surf, color, fill, border_radius=4)
    pygame.draw.rect(surf, BORDER, r, width=1, border_radius=4)


def _trunc_path(path, font, max_w):
    """Trim a long path from the left so it fits within max_w pixels."""
    if not path or font.size(path)[0] <= max_w:
        return path
    while path and font.size("…" + path)[0] > max_w:
        path = path[1:]
    return "…" + path


def _trunc_right(text, font, max_w):
    """Trim lyric text from the right so it fits within max_w pixels."""
    if not text or font.size(text)[0] <= max_w:
        return text
    while text and font.size(text + "…")[0] > max_w:
        text = text[:-1]
    return text + "…"


# ── Main application ──────────────────────────────────────────────────────────

class App:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((WIN_W, WIN_H))
        pygame.display.set_caption("Lyric Video Creator")
        self.clock  = pygame.time.Clock()

        # UI fonts (Windows system fonts)
        self.f_title = pygame.font.SysFont("segoeui,arial", 21, bold=True)
        self.f_lbl   = pygame.font.SysFont("segoeui,arial", 14)
        self.f_sm    = pygame.font.SysFont("segoeui,arial", 13)
        self.f_btn   = pygame.font.SysFont("segoeui,arial", 14, bold=True)
        self.f_create= pygame.font.SysFont("segoeui,arial", 16, bold=True)

        # User selections
        self.audio_path      = ""
        self.video_path      = ""
        self.lyrics_path     = ""
        self.ratio           = "16:9"
        self.position        = "bottom"
        self.text_color      = "white"
        self.model_size      = "large-v3-turbo"
        self.isolate_vocals  = False

        # Font dropdown
        self.font_list   = _scan_fonts()
        self._font_cache = {}
        # Default to arialbd if present, else index 0
        default_path     = DEFAULT_VIDEO_FONT.lower()
        self.font_idx    = next(
            (i for i, (_, p) in enumerate(self.font_list) if p.lower() == default_path),
            0,
        )
        self.dd_open     = False
        self.dd_scroll   = max(0, self.font_idx - DD_VISIBLE // 2)

        # Processing state
        self.state       = _State()
        self.processing  = False
        self.phase       = "idle"    # "idle" | "lyrics" | "render"
        self.cfg         = None

        # Lyrics review/edit state
        self.mode              = "setup"   # "setup" | "review"
        self.review_lines       = []
        self.review_scroll      = 0
        self.review_edit_idx    = None
        self.review_edit_text   = ""
        self.review_edit_cursor = 0

        # Button hit-rects
        self._rects = {
            "audio_browse":   pygame.Rect(620,  58,  96, 28),
            "video_browse":   pygame.Rect(620,  94,  96, 28),
            "lyrics_browse":  pygame.Rect(620, 130,  96, 28),
            "ratio_169":      pygame.Rect( 84, 178,  64, 26),
            "ratio_916":      pygame.Rect(154, 178,  64, 26),
            "pos_top":        pygame.Rect(314, 178,  56, 26),
            "pos_center":     pygame.Rect(376, 178,  66, 26),
            "pos_bottom":     pygame.Rect(448, 178,  68, 26),
            "model_fast":     pygame.Rect( 84, 212,  76, 26),
            "model_turbo":    pygame.Rect(166, 212, 126, 26),
            "isolate_vocals": pygame.Rect(448, 212, 180, 26),
            "font_dd":        pygame.Rect(104, 254, 612, 26),
            "color_white":    pygame.Rect(104, 288,  44, 26),
            "color_black":    pygame.Rect(156, 288,  44, 26),
            "color_blue":     pygame.Rect(208, 288,  44, 26),
            "color_green":    pygame.Rect(260, 288,  44, 26),
            "color_yellow":   pygame.Rect(312, 288,  44, 26),
            "color_red":      pygame.Rect(364, 288,  44, 26),
            "create":         pygame.Rect(170, 332, 400, 42),
            "review_list":    pygame.Rect( 20,  78, 700, 410),
            "review_back":    pygame.Rect( 20, 502, 150, 42),
            "review_approve": pygame.Rect(190, 502, 530, 42),
        }

    # ── event loop ────────────────────────────────────────────────────────

    def run(self):
        while True:
            mp = pygame.mouse.get_pos()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self._on_click(mp)
                if event.type == pygame.MOUSEWHEEL and self.dd_open:
                    max_scroll = max(0, len(self.font_list) - DD_VISIBLE)
                    self.dd_scroll = max(0, min(max_scroll, self.dd_scroll - event.y))
                elif event.type == pygame.MOUSEWHEEL and self.mode == "review":
                    list_r  = self._rects["review_list"]
                    visible = list_r.height // REVIEW_ROW_H
                    max_scroll = max(0, len(self.review_lines) - visible)
                    self.review_scroll = max(0, min(max_scroll, self.review_scroll - event.y))
                if self.mode == "review" and self.review_edit_idx is not None:
                    self._handle_review_key_event(event)

            self._poll_phase()
            self._draw(mp)
            self.clock.tick(FPS)

    def _poll_phase(self):
        """Check background worker state and advance lyrics -> review -> render."""
        if self.phase == "lyrics" and self.processing:
            st = self.state.snapshot()
            if st["done"]:
                self.processing = False
                self.phase = "idle"
                if not st["error"]:
                    self.review_lines = [
                        {"start": s, "end": e, "text": t} for s, e, t in st["lyrics"]
                    ]
                    self.review_scroll      = 0
                    self.review_edit_idx    = None
                    self.review_edit_text   = ""
                    self.review_edit_cursor = 0
                    self.mode = "review"
        elif self.phase == "render" and self.processing:
            st = self.state.snapshot()
            if st["done"]:
                self.processing = False
                self.phase = "idle"

    def _handle_review_key_event(self, event):
        if event.type == pygame.TEXTINPUT:
            t, c = self.review_edit_text, self.review_edit_cursor
            self.review_edit_text = t[:c] + event.text + t[c:]
            self.review_edit_cursor += len(event.text)
        elif event.type == pygame.KEYDOWN:
            t, c = self.review_edit_text, self.review_edit_cursor
            if event.key == pygame.K_BACKSPACE and c > 0:
                self.review_edit_text = t[:c - 1] + t[c:]
                self.review_edit_cursor -= 1
            elif event.key == pygame.K_DELETE:
                self.review_edit_text = t[:c] + t[c + 1:]
            elif event.key == pygame.K_LEFT:
                self.review_edit_cursor = max(0, c - 1)
            elif event.key == pygame.K_RIGHT:
                self.review_edit_cursor = min(len(t), c + 1)
            elif event.key == pygame.K_HOME:
                self.review_edit_cursor = 0
            elif event.key == pygame.K_END:
                self.review_edit_cursor = len(t)
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
                self._commit_review_edit()
            elif event.key == pygame.K_ESCAPE:
                self.review_edit_idx    = None
                self.review_edit_text   = ""
                self.review_edit_cursor = 0

    def _select_review_line(self, idx):
        self._commit_review_edit()
        self.review_edit_idx    = idx
        self.review_edit_text   = self.review_lines[idx]["text"]
        self.review_edit_cursor = len(self.review_edit_text)

    def _commit_review_edit(self):
        if self.review_edit_idx is not None and 0 <= self.review_edit_idx < len(self.review_lines):
            self.review_lines[self.review_edit_idx]["text"] = self.review_edit_text
        self.review_edit_idx    = None
        self.review_edit_text   = ""
        self.review_edit_cursor = 0

    def _on_click(self, mp):
        if self.mode == "review":
            self._on_click_review(mp)
            return

        if self.processing:
            return

        # Dropdown takes priority: check open panel first, then header
        dd_r = self._rects["font_dd"]
        if self.dd_open:
            panel = pygame.Rect(dd_r.x, dd_r.bottom + 2, dd_r.width, DD_VISIBLE * DD_ITEM_H)
            if panel.collidepoint(mp):
                i = (mp[1] - panel.y) // DD_ITEM_H
                idx = self.dd_scroll + i
                if 0 <= idx < len(self.font_list):
                    self.font_idx = idx
                self.dd_open = False
                return
            # Click outside panel closes it (header re-opens below)
            self.dd_open = False
            if not dd_r.collidepoint(mp):
                return

        if dd_r.collidepoint(mp):
            self.dd_open = not self.dd_open
            return

        tag = next((t for t, r in self._rects.items() if r.collidepoint(mp)), None)
        if tag is None:
            return

        if tag == "audio_browse":
            p = _browse_file(
                "Select Audio File",
                [("Audio", "*.mp3 *.wav *.flac *.ogg *.m4a *.aac"), ("All", "*.*")],
            )
            if p:
                self.audio_path = p

        elif tag == "video_browse":
            p = _browse_file(
                "Select Background Video",
                [("Video", "*.mp4 *.mov *.avi *.mkv *.wmv"), ("All", "*.*")],
            )
            if p:
                self.video_path = p

        elif tag == "lyrics_browse":
            # Cancel = empty string = auto-transcribe
            p = _browse_file(
                "Select Lyrics File  (LRC or TXT)",
                [("LRC / Text", "*.lrc *.txt"), ("All", "*.*")],
            )
            self.lyrics_path = p

        elif tag == "ratio_169":
            self.ratio = "16:9"
        elif tag == "ratio_916":
            self.ratio = "9:16"
        elif tag == "pos_top":
            self.position = "top"
        elif tag == "pos_center":
            self.position = "center"
        elif tag == "pos_bottom":
            self.position = "bottom"
        elif tag == "model_fast":
            self.model_size = "base"
        elif tag == "model_turbo":
            self.model_size = "large-v3-turbo"
        elif tag == "isolate_vocals":
            self.isolate_vocals = not self.isolate_vocals
        elif tag.startswith("color_"):
            self.text_color = tag[6:]

        elif tag == "create":
            if not self.audio_path:
                self.state.set(status="Please select an audio file first.",
                               error="no_audio")
            elif not self.video_path:
                self.state.set(status="Please select a background video first.",
                               error="no_video")
            else:
                self._start_lyrics_phase()

    def _on_click_review(self, mp):
        list_r  = self._rects["review_list"]

        if list_r.collidepoint(mp):
            i   = (mp[1] - list_r.y - 1) // REVIEW_ROW_H
            idx = self.review_scroll + i
            if 0 <= idx < len(self.review_lines):
                row = pygame.Rect(list_r.x + 1, list_r.y + 1 + i * REVIEW_ROW_H,
                                  list_r.width - 2, REVIEW_ROW_H)
                del_r = pygame.Rect(row.right - 22, row.y + 3, 18, REVIEW_ROW_H - 6)
                if del_r.collidepoint(mp):
                    if self.review_edit_idx == idx:
                        self.review_edit_idx = None
                    del self.review_lines[idx]
                    return
                self._select_review_line(idx)
            return

        if self._rects["review_back"].collidepoint(mp):
            self._commit_review_edit()
            self.mode = "setup"
        elif self._rects["review_approve"].collidepoint(mp):
            self._commit_review_edit()
            lyrics = [
                (e["start"], e["end"], e["text"])
                for e in self.review_lines if e["text"].strip()
            ]
            self.mode = "setup"
            self._start_render_phase(lyrics)

    def _start_lyrics_phase(self):
        self.state      = _State()
        self.processing = True
        self.phase      = "lyrics"
        selected_font = (
            self.font_list[self.font_idx][1]
            if self.font_list else DEFAULT_VIDEO_FONT
        )
        stroke_color = "white" if self.text_color == "black" else "black"
        self.cfg = {
            "audio":          self.audio_path,
            "video":          self.video_path,
            "lyrics":         self.lyrics_path,
            "ratio":          self.ratio,
            "position":       self.position,
            "model_size":     self.model_size,
            "isolate_vocals": self.isolate_vocals,
            "font":           selected_font,
            "font_size":      70,
            "color":          self.text_color,
            "stroke_color":   stroke_color,
            "stroke_width":   3,
        }
        threading.Thread(target=_run_lyrics_phase, args=(self.cfg, self.state), daemon=True).start()

    def _start_render_phase(self, lyrics):
        self.state      = _State()
        self.processing = True
        self.phase      = "render"
        threading.Thread(target=_run_render, args=(self.cfg, lyrics, self.state), daemon=True).start()

    # ── drawing ───────────────────────────────────────────────────────────

    def _draw(self, mp):
        s = self.screen
        s.fill(BG)

        if self.mode == "review":
            self._draw_review(s, mp)
            pygame.display.flip()
            return

        # Title
        title = self.f_title.render("LYRIC  VIDEO  CREATOR", True, TEXT_PRI)
        s.blit(title, title.get_rect(centerx=WIN_W // 2, y=14))
        pygame.draw.line(s, DIVIDER, (20, 46), (WIN_W - 20, 46), 1)

        # File input rows
        self._draw_file_row(s, mp, 58,  "Audio:", self.audio_path,
                            "audio_browse", "Select an MP3, WAV, FLAC …")
        self._draw_file_row(s, mp, 94,  "Video:", self.video_path,
                            "video_browse", "Select a background MP4, MKV …")
        self._draw_file_row(s, mp, 130, "Lyrics:", self.lyrics_path,
                            "lyrics_browse", "Optional — select .txt (WhisperX align) or .lrc")

        pygame.draw.line(s, DIVIDER, (20, 168), (WIN_W - 20, 168), 1)

        # Row 1: Aspect & Position
        s.blit(self.f_lbl.render("Aspect:", True, TEXT_SEC), (20, 183))
        self._draw_toggle(s, mp, "ratio_169", "16:9", self.ratio == "16:9")
        self._draw_toggle(s, mp, "ratio_916", "9:16", self.ratio == "9:16")

        s.blit(self.f_lbl.render("Position:", True, TEXT_SEC), (248, 183))
        self._draw_toggle(s, mp, "pos_top",    "Top",    self.position == "top")
        self._draw_toggle(s, mp, "pos_center", "Center", self.position == "center")
        self._draw_toggle(s, mp, "pos_bottom", "Bottom", self.position == "bottom")

        # Row 2: AI Model & Vocal Isolation
        s.blit(self.f_lbl.render("Model:", True, TEXT_SEC), (20, 217))
        self._draw_toggle(s, mp, "model_fast",  "Base",          self.model_size == "base")
        self._draw_toggle(s, mp, "model_turbo", "Turbo / Large", self.model_size == "large-v3-turbo")

        s.blit(self.f_lbl.render("Audio:", True, TEXT_SEC), (388, 217))
        self._draw_toggle(s, mp, "isolate_vocals", "Isolate Vocals (Demucs)", self.isolate_vocals)

        # Font row
        pygame.draw.line(s, DIVIDER, (20, 246), (WIN_W - 20, 246), 1)
        s.blit(self.f_lbl.render("Font:", True, TEXT_SEC), (20, 258))

        # Color row
        s.blit(self.f_lbl.render("Color:", True, TEXT_SEC), (20, 292))
        for _, key, rgb in TEXT_COLORS:
            r = self._rects[f"color_{key}"]
            active = (self.text_color == key)
            hov = r.collidepoint(mp) and not self.processing
            # Box fill
            pygame.draw.rect(s, rgb, r, border_radius=4)
            # Border / highlight
            if active:
                pygame.draw.rect(s, ACCENT_ON, r.inflate(4, 4), width=2, border_radius=6)
                contrast_col = (0, 0, 0) if key in ("white", "yellow", "green") else (255, 255, 255)
                chk = self.f_sm.render("✓", True, contrast_col)
                s.blit(chk, chk.get_rect(center=r.center))
            elif hov:
                pygame.draw.rect(s, ACCENT_H, r.inflate(2, 2), width=2, border_radius=5)
            else:
                pygame.draw.rect(s, (80, 85, 110), r, width=1, border_radius=4)

        col_name = self.text_color.capitalize()
        s.blit(self.f_sm.render(f"Text Color: {col_name}", True, TEXT_PRI), (424, 293))

        pygame.draw.line(s, DIVIDER, (20, 322), (WIN_W - 20, 322), 1)

        # Create button
        cr  = self._rects["create"]
        hov = cr.collidepoint(mp) and not self.processing
        c   = BTN_DIS if self.processing else (BTN_OK_H if hov else BTN_OK)
        pygame.draw.rect(s, c, cr, border_radius=8)
        if self.phase == "lyrics":
            create_label = "Preparing lyrics…"
        elif self.phase == "render":
            create_label = "Creating video…"
        else:
            create_label = "CREATE  LYRIC  VIDEO"
        lbl = self.f_create.render(create_label, True, TEXT_PRI)
        s.blit(lbl, lbl.get_rect(center=cr.center))

        pygame.draw.line(s, DIVIDER, (20, 384), (WIN_W - 20, 384), 1)

        # Progress bars
        st = self.state.snapshot()

        s.blit(self.f_lbl.render("Phase:",   True, TEXT_SEC), (20, 396))
        _draw_bar(s, (104, 395, 520, 16), st["phase"],   BAR_PHASE)
        s.blit(self.f_sm.render(f"{int(st['phase'] * 100)}%", True, TEXT_SEC),
               (636, 396))

        s.blit(self.f_lbl.render("Overall:", True, TEXT_SEC), (20, 424))
        _draw_bar(s, (104, 423, 520, 16), st["overall"], BAR_TOTAL)
        s.blit(self.f_sm.render(f"{int(st['overall'] * 100)}%", True, TEXT_SEC),
               (636, 424))

        # Status text
        if st["error"] and st["error"] not in ("no_audio", "no_video"):
            scol = RED
        elif st["done"]:
            scol = GREEN_OK
        else:
            scol = TEXT_SEC

        s.blit(self.f_sm.render(st["status"][:96], True, scol), (20, 452))

        # Font dropdown drawn last so its open panel sits on top of other widgets
        self._draw_dropdown(s, mp)

        pygame.display.flip()

    def _draw_review(self, s, mp):
        title = self.f_title.render("REVIEW  LYRICS", True, TEXT_PRI)
        s.blit(title, title.get_rect(centerx=WIN_W // 2, y=14))
        pygame.draw.line(s, DIVIDER, (20, 44), (WIN_W - 20, 44), 1)

        hint = self.f_sm.render(
            "Click a line to edit its text (Enter to confirm). Click × to remove a line.",
            True, TEXT_SEC,
        )
        s.blit(hint, (20, 56))

        list_r = self._rects["review_list"]
        pygame.draw.rect(s, PANEL, list_r, border_radius=4)
        pygame.draw.rect(s, BORDER, list_r, width=1, border_radius=4)

        visible = list_r.height // REVIEW_ROW_H
        max_scroll = max(0, len(self.review_lines) - visible)
        self.review_scroll = max(0, min(self.review_scroll, max_scroll))

        for i in range(visible):
            idx = self.review_scroll + i
            if idx >= len(self.review_lines):
                break
            row = pygame.Rect(list_r.x + 1, list_r.y + 1 + i * REVIEW_ROW_H,
                              list_r.width - 2, REVIEW_ROW_H)
            editing = (idx == self.review_edit_idx)
            if editing:
                pygame.draw.rect(s, (45, 55, 90), row)
            elif row.collidepoint(mp):
                pygame.draw.rect(s, (35, 38, 60), row)

            entry = self.review_lines[idx]
            ts = self.f_sm.render(
                f"[{int(entry['start']) // 60:02d}:{entry['start'] % 60:05.2f}]",
                True, TEXT_SEC,
            )
            s.blit(ts, (row.x + 6, row.y + 5))

            text_x = row.x + 92
            text_w = row.width - 92 - 26
            if editing:
                s.blit(self.f_sm.render(self.review_edit_text, True, TEXT_PRI),
                      (text_x, row.y + 5))
                if (pygame.time.get_ticks() // 500) % 2 == 0:
                    cursor_str = self.review_edit_text[:self.review_edit_cursor]
                    cx = text_x + self.f_sm.size(cursor_str)[0]
                    pygame.draw.line(s, TEXT_PRI, (cx, row.y + 3), (cx, row.y + REVIEW_ROW_H - 4))
            else:
                shown = entry["text"] or "(empty — will not appear)"
                col   = TEXT_PRI if entry["text"] else TEXT_DIM
                s.blit(self.f_sm.render(_trunc_right(shown, self.f_sm, text_w), True, col),
                      (text_x, row.y + 5))

            del_r = pygame.Rect(row.right - 22, row.y + 3, 18, REVIEW_ROW_H - 6)
            del_col = (120, 50, 50) if del_r.collidepoint(mp) else (70, 40, 40)
            pygame.draw.rect(s, del_col, del_r, border_radius=3)
            xlbl = self.f_sm.render("×", True, TEXT_PRI)
            s.blit(xlbl, xlbl.get_rect(center=del_r.center))

        if len(self.review_lines) > visible:
            sb_track = pygame.Rect(list_r.right - 8, list_r.y + 2, 5, list_r.height - 4)
            pygame.draw.rect(s, BAR_BG, sb_track, border_radius=2)
            sb_h = max(20, int(sb_track.height * visible / len(self.review_lines)))
            sb_y = sb_track.y + int(sb_track.height * self.review_scroll / len(self.review_lines))
            pygame.draw.rect(s, ACCENT, (sb_track.x, sb_y, sb_track.width, sb_h), border_radius=2)

        back_r = self._rects["review_back"]
        hov = back_r.collidepoint(mp)
        pygame.draw.rect(s, ACCENT_H if hov else ACCENT, back_r, border_radius=8)
        lbl = self.f_btn.render("Cancel", True, TEXT_PRI)
        s.blit(lbl, lbl.get_rect(center=back_r.center))

        appr_r = self._rects["review_approve"]
        hov = appr_r.collidepoint(mp)
        pygame.draw.rect(s, BTN_OK_H if hov else BTN_OK, appr_r, border_radius=8)
        lbl = self.f_create.render("APPROVE  &  CREATE  VIDEO", True, TEXT_PRI)
        s.blit(lbl, lbl.get_rect(center=appr_r.center))

    def _get_preview_font(self, font_path, size=15):
        if not font_path:
            return self.f_sm
        key = (font_path, size)
        if key not in self._font_cache:
            try:
                self._font_cache[key] = pygame.font.Font(font_path, size)
            except Exception:
                self._font_cache[key] = self.f_sm
        return self._font_cache[key]

    def _render_font_sample(self, font_path, text, color, max_w, size=15):
        font = self._get_preview_font(font_path, size)
        try:
            txt = text
            while txt and font.size(txt)[0] > max_w:
                txt = txt[:-1]
            if len(txt) < len(text) and txt:
                txt = txt[:-1] + "…"
            surf = font.render(txt or text, True, color)
            if surf.get_width() > 0:
                return surf
        except Exception:
            pass
        txt = _trunc_right(text, self.f_sm, max_w)
        return self.f_sm.render(txt, True, color)

    def _draw_dropdown(self, s, mp):
        r   = self._rects["font_dd"]
        dis = self.processing

        # Header box
        border_col = ACCENT if self.dd_open else (BTN_DIS if dis else BORDER)
        pygame.draw.rect(s, PANEL, r, border_radius=4)
        pygame.draw.rect(s, border_col, r, width=1, border_radius=4)

        if self.font_list:
            name, path = self.font_list[self.font_idx]
        else:
            name, path = "No fonts found", ""
        f_surf = self._render_font_sample(
            path, name, BTN_DIS if dis else TEXT_PRI, r.width - 34, size=14
        )
        s.blit(f_surf, (r.x + 8, r.y + (r.height - f_surf.get_height()) // 2))

        arrow = self.f_sm.render("▲" if self.dd_open else "▼", True, TEXT_SEC)
        s.blit(arrow, (r.right - 18, r.y + 5))

        if not self.dd_open or dis:
            return

        # Open panel
        panel = pygame.Rect(r.x, r.bottom + 2, r.width, DD_VISIBLE * DD_ITEM_H)
        pygame.draw.rect(s, (22, 22, 38), panel, border_radius=4)
        pygame.draw.rect(s, ACCENT, panel, width=1, border_radius=4)

        for i in range(DD_VISIBLE):
            idx = self.dd_scroll + i
            if idx >= len(self.font_list):
                break
            item_r = pygame.Rect(panel.x + 1, panel.y + i * DD_ITEM_H,
                                 panel.width - 2, DD_ITEM_H)
            hov = item_r.collidepoint(mp)
            sel = (idx == self.font_idx)
            if sel:
                pygame.draw.rect(s, ACCENT, item_r)
            elif hov:
                pygame.draw.rect(s, (45, 50, 80), item_r)
            col = TEXT_PRI if (sel or hov) else TEXT_SEC
            name, path = self.font_list[idx]
            f_surf = self._render_font_sample(path, name, col, item_r.width - 24, size=14)
            s.blit(f_surf, (item_r.x + 8, item_r.y + (item_r.height - f_surf.get_height()) // 2))

        # Scrollbar
        total = len(self.font_list)
        if total > DD_VISIBLE:
            sb_track = pygame.Rect(panel.right - 7, panel.y + 2, 5, panel.height - 4)
            pygame.draw.rect(s, BAR_BG, sb_track, border_radius=2)
            sb_h = max(20, int(sb_track.height * DD_VISIBLE / total))
            sb_y = sb_track.y + int(sb_track.height * self.dd_scroll / total)
            pygame.draw.rect(s, ACCENT, (sb_track.x, sb_y, sb_track.width, sb_h),
                             border_radius=2)

    def _draw_file_row(self, s, mp, y, label, path, btn_tag, hint):
        s.blit(self.f_lbl.render(label, True, TEXT_SEC), (20, y + 7))
        box = pygame.Rect(104, y, 506, 28)
        pygame.draw.rect(s, PANEL,  box, border_radius=4)
        pygame.draw.rect(s, BORDER, box, width=1, border_radius=4)

        disp = _trunc_path(path, self.f_sm, 480) if path else hint
        col  = TEXT_PRI if path else TEXT_DIM
        s.blit(self.f_sm.render(disp, True, col), (112, y + 7))

        self._draw_btn(s, mp, btn_tag, "Browse",
                       disabled=self.processing)

    def _draw_toggle(self, s, mp, tag, label, active):
        r   = self._rects[tag]
        dis = self.processing
        hov = r.collidepoint(mp) and not dis
        if dis:
            c = BTN_DIS
        elif active:
            c = ACCENT_ON
        elif hov:
            c = ACCENT_H
        else:
            c = ACCENT
        pygame.draw.rect(s, c, r, border_radius=5)
        pygame.draw.rect(s, (90, 100, 140), r, width=1, border_radius=5)
        t = self.f_sm.render(label, True, TEXT_PRI)
        s.blit(t, t.get_rect(center=r.center))

    def _draw_btn(self, s, mp, tag, label, disabled=False):
        r   = self._rects[tag]
        hov = r.collidepoint(mp) and not disabled
        c   = BTN_DIS if disabled else (ACCENT_H if hov else ACCENT)
        pygame.draw.rect(s, c, r, border_radius=5)
        pygame.draw.rect(s, (90, 100, 140), r, width=1, border_radius=5)
        t = self.f_btn.render(label, True, TEXT_PRI)
        s.blit(t, t.get_rect(center=r.center))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    App().run()
