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
import threading
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

WIN_W, WIN_H = 720, 560
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

DD_VISIBLE = 9    # items shown in open dropdown
DD_ITEM_H  = 22   # height of each dropdown item

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
    "yellow":  (255, 255,   0),
    "red":     (255,  60,  60),
    "blue":    ( 60, 100, 255),
    "green":   ( 60, 200,  60),
    "cyan":    (  0, 230, 230),
    "magenta": (230,   0, 230),
    "gray":    (160, 160, 160),
    "orange":  (255, 165,   0),
}


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
                    color, stroke_color, stroke_w, position):
    """Return an RGBA numpy array (ch, cw, 4) with text drawn on transparency."""
    img  = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _load_font(font_path, font_size)

    lines   = _wrap_text(text, font, int(cw * 0.88))
    lh      = int(font_size * 1.25)
    total_h = len(lines) * lh
    pad     = int(ch * 0.08)

    y0 = {"bottom": ch - total_h - pad,
          "top":    pad,
          "center": (ch - total_h) // 2}.get(position, ch - total_h - pad)

    tc = _parse_color(color)        + (255,)
    sc = _parse_color(stroke_color) + (210,)

    for i, line in enumerate(lines):
        lw = _text_width(font, line)
        x  = (cw - lw) // 2
        y    = y0 + i * lh
        if stroke_w > 0:
            for dx in range(-stroke_w, stroke_w + 1):
                for dy in range(-stroke_w, stroke_w + 1):
                    if dx or dy:
                        draw.text((x + dx, y + dy), line, font=font, fill=sc)
        draw.text((x, y), line, font=font, fill=tc)

    return np.array(img)


def build_lyric_overlays(lyrics, tw, th, font_path, font_size,
                          color, stroke_color, stroke_w, position, state):
    """Pre-render each unique lyric line as an RGBA numpy array (h, w, 4)."""
    unique = list(dict.fromkeys(t for _, _, t in lyrics if t.strip()))
    overlays = {}
    total = len(unique) or 1
    for i, text in enumerate(unique):
        overlays[text] = make_text_image(
            text, tw, th, font_path, font_size,
            color, stroke_color, stroke_w, position,
        )
        state.set(phase=(i + 1) / total)
    return overlays


# ── Transcription ─────────────────────────────────────────────────────────────

def transcribe_audio(audio_path, state, hint=None):
    """Transcribe audio with faster-whisper.

    hint: optional plain-text lyrics passed as initial_prompt so Whisper
          knows the vocabulary and is less likely to mishear words.
    """
    state.set(status="Loading Whisper model  (first run downloads ~150 MB)…")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "faster-whisper is not installed.\n"
            "Run:  pip install faster-whisper"
        )

    model = WhisperModel("base", device="auto", compute_type="auto")
    label = "Transcribing with lyrics hint…" if hint else "Transcribing audio…"
    state.set(status=label, phase=0.05)

    kwargs = dict(word_timestamps=False)
    if hint:
        kwargs["initial_prompt"] = hint[:900]   # Whisper prompt limit ≈ 224 tokens

    segments, info = model.transcribe(audio_path, **kwargs)
    total_dur = max(getattr(info, "duration", 0) or 1.0, 1.0)

    lyrics = []
    for seg in segments:
        lyrics.append([seg.start, seg.end, seg.text.strip()])
        state.set(phase=min(seg.end / total_dur, 0.99))

    if not lyrics:
        raise RuntimeError(
            "Transcription produced no output.\n"
            "Verify the audio file contains vocals."
        )
    return lyrics


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
    """Parse or transcribe the lyrics for cfg, returning a list of
    [start, end, text] entries. Runs before the review/approve step."""
    lyric_file = cfg["lyrics"]
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
        # Plain text lyrics — read and pass to Whisper as a vocabulary hint
        state.set(status="Reading lyrics text file…", phase=0.0, overall=0.05)
        with open(lyric_file, encoding="utf-8", errors="replace") as fh:
            hint = fh.read()
        state.set(status="Transcribing with lyrics hint…", phase=0.0, overall=0.1)
        lyrics = transcribe_audio(cfg["audio"], state, hint=hint)
        state.set(overall=1.0, phase=1.0,
                  status=f"Transcribed {len(lyrics)} segments (lyrics-guided).")

    else:
        # No lyrics file — blind transcription
        state.set(status="Starting transcription…", phase=0.0, overall=0.05)
        lyrics = transcribe_audio(cfg["audio"], state)
        state.set(overall=1.0, phase=1.0,
                  status=f"Transcribed {len(lyrics)} lyric segments.")

    return lyrics


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

        # ── Encode
        enc_logger = _EncodeLogger(state, o_start=0.5, o_end=1.0)
        final.write_videofile(
            output,
            fps         = video.fps or 30,
            codec       = "libx264",
            audio_codec = "aac",
            threads     = os.cpu_count() or 4,
            preset      = "veryfast",
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
        self.audio_path  = ""
        self.video_path  = ""
        self.lyrics_path = ""
        self.ratio       = "16:9"
        self.position    = "bottom"

        # Font dropdown
        self.font_list   = _scan_fonts()
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

        # Button hit-rects  (font dropdown row added at y=306; everything below shifted +48)
        self._rects = {
            "audio_browse":  pygame.Rect(602, 100, 88, 28),
            "video_browse":  pygame.Rect(602, 148, 88, 28),
            "lyrics_browse": pygame.Rect(602, 196, 88, 28),
            "ratio_169":     pygame.Rect( 92, 268, 68, 26),
            "ratio_916":     pygame.Rect(170, 268, 68, 26),
            "pos_top":       pygame.Rect(338, 268, 60, 26),
            "pos_center":    pygame.Rect(408, 268, 70, 26),
            "pos_bottom":    pygame.Rect(488, 268, 70, 26),
            "font_dd":       pygame.Rect(104, 306, 558, 26),
            "create":        pygame.Rect(160, 366, 400, 44),
            "review_list":     pygame.Rect(20, 92, 680, 312),
            "review_back":     pygame.Rect(20, 418, 150, 42),
            "review_approve":  pygame.Rect(190, 418, 510, 42),
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
                "Select LRC File  (cancel to auto-transcribe)",
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
        visible = list_r.height // REVIEW_ROW_H

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
        self.cfg = {
            "audio":        self.audio_path,
            "video":        self.video_path,
            "lyrics":       self.lyrics_path,
            "ratio":        self.ratio,
            "position":     self.position,
            "font":         selected_font,
            "font_size":    70,
            "color":        "white",
            "stroke_color": "black",
            "stroke_width": 2,
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
        s.blit(title, title.get_rect(centerx=WIN_W // 2, y=16))
        pygame.draw.line(s, DIVIDER, (20, 50), (WIN_W - 20, 50), 1)

        # File input rows
        self._draw_file_row(s, mp, 100, "Audio:", self.audio_path,
                            "audio_browse", "Select an MP3, WAV, FLAC …")
        self._draw_file_row(s, mp, 148, "Video:", self.video_path,
                            "video_browse", "Select a background MP4, MKV …")
        self._draw_file_row(s, mp, 196, "Lyrics:", self.lyrics_path,
                            "lyrics_browse", "Optional — leave empty to auto-transcribe")

        pygame.draw.line(s, DIVIDER, (20, 238), (WIN_W - 20, 238), 1)

        # Aspect ratio
        s.blit(self.f_lbl.render("Aspect:", True, TEXT_SEC), (20, 274))
        self._draw_toggle(s, mp, "ratio_169", "16:9", self.ratio == "16:9")
        self._draw_toggle(s, mp, "ratio_916", "9:16", self.ratio == "9:16")

        # Position
        s.blit(self.f_lbl.render("Position:", True, TEXT_SEC), (262, 274))
        self._draw_toggle(s, mp, "pos_top",    "Top",    self.position == "top")
        self._draw_toggle(s, mp, "pos_center", "Center", self.position == "center")
        self._draw_toggle(s, mp, "pos_bottom", "Bottom", self.position == "bottom")

        # Font row label (dropdown drawn last so it overlays everything)
        pygame.draw.line(s, DIVIDER, (20, 296), (WIN_W - 20, 296), 1)
        s.blit(self.f_lbl.render("Font:", True, TEXT_SEC), (20, 312))

        pygame.draw.line(s, DIVIDER, (20, 348), (WIN_W - 20, 348), 1)

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

        pygame.draw.line(s, DIVIDER, (20, 422), (WIN_W - 20, 422), 1)

        # Progress bars
        st = self.state.snapshot()

        s.blit(self.f_lbl.render("Phase:",   True, TEXT_SEC), (20, 442))
        _draw_bar(s, (106, 441, 502, 17), st["phase"],   BAR_PHASE)
        s.blit(self.f_sm.render(f"{int(st['phase'] * 100)}%", True, TEXT_SEC),
               (616, 442))

        s.blit(self.f_lbl.render("Overall:", True, TEXT_SEC), (20, 472))
        _draw_bar(s, (106, 471, 502, 17), st["overall"], BAR_TOTAL)
        s.blit(self.f_sm.render(f"{int(st['overall'] * 100)}%", True, TEXT_SEC),
               (616, 472))

        # Status text
        if st["error"] and st["error"] not in ("no_audio", "no_video"):
            scol = RED
        elif st["done"]:
            scol = GREEN_OK
        else:
            scol = TEXT_SEC

        s.blit(self.f_sm.render(st["status"][:94], True, scol), (20, 510))

        # Font dropdown drawn last so its open panel sits on top of other widgets
        self._draw_dropdown(s, mp)

        pygame.display.flip()

    def _draw_review(self, s, mp):
        title = self.f_title.render("REVIEW  LYRICS", True, TEXT_PRI)
        s.blit(title, title.get_rect(centerx=WIN_W // 2, y=16))
        pygame.draw.line(s, DIVIDER, (20, 50), (WIN_W - 20, 50), 1)

        hint = self.f_sm.render(
            "Click a line to edit its text (Enter to confirm). Click × to remove a line.",
            True, TEXT_SEC,
        )
        s.blit(hint, (20, 64))

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


    def _draw_dropdown(self, s, mp):
        r   = self._rects["font_dd"]
        dis = self.processing

        # Header box
        border_col = ACCENT if self.dd_open else (BTN_DIS if dis else BORDER)
        pygame.draw.rect(s, PANEL, r, border_radius=4)
        pygame.draw.rect(s, border_col, r, width=1, border_radius=4)

        name = self.font_list[self.font_idx][0] if self.font_list else "No fonts found"
        s.blit(self.f_sm.render(_trunc_path(name, self.f_sm, r.width - 30), True,
                                BTN_DIS if dis else TEXT_PRI), (r.x + 8, r.y + 5))
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
            label = self.font_list[idx][0]
            s.blit(self.f_sm.render(label[:55], True, col), (item_r.x + 8, item_r.y + 4))

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
        box = pygame.Rect(104, y, 488, 28)
        pygame.draw.rect(s, PANEL,  box, border_radius=4)
        pygame.draw.rect(s, BORDER, box, width=1, border_radius=4)

        disp = _trunc_path(path, self.f_sm, 458) if path else hint
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
