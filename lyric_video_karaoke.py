#!/usr/bin/env python3
"""
Lyric Video Karaoke Renderer
==============================

Builds a lyric video where each word lights up in sync with the vocals,
similar to TikTok/CapCut auto-captions.

INPUT
-----
Takes the "karaoke JSON" produced by auto_lyric_sync.py (--karaoke-json),
which looks like:

    [
      {
        "text": "First line of lyrics",
        "start": 12.5,
        "end": 13.9,
        "words": [
          {"word": "First", "start": 12.5, "end": 12.8},
          {"word": "line", "start": 12.8, "end": 13.0},
          ...
        ]
      },
      ...
    ]

If you don't have word-level data (e.g. you're using a plain .lrc file
instead), this script will still work -- it just displays each full line
without per-word highlighting (pass --lrc instead of --karaoke-json).

Text is rendered with Pillow (no ImageMagick dependency).

REQUIREMENTS
------------
    pip install moviepy pillow numpy

USAGE
-----
    python lyric_video_karaoke.py \\
        --audio song.mp3 \\
        --video background.mp4 \\
        --karaoke-json song_karaoke.json \\
        --output lyric_video.mp4 \\
        --ratio 9:16 \\
        --font "/path/to/font.ttf" \\
        --font-size 80 \\
        --base-color white \\
        --highlight-color "#FFD700" \\
        --stroke-color black \\
        --stroke-width 3
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip, ImageClip


# ---------------------------------------------------------------------------
# Font discovery
# ---------------------------------------------------------------------------

def list_system_fonts():
    """Find available .ttf/.otf font files on the system."""
    font_dirs = []
    if sys.platform.startswith("linux"):
        font_dirs = [
            "/usr/share/fonts",
            "/usr/local/share/fonts",
            os.path.expanduser("~/.fonts"),
            os.path.expanduser("~/.local/share/fonts"),
        ]
    elif sys.platform == "darwin":
        font_dirs = [
            "/Library/Fonts",
            "/System/Library/Fonts",
            os.path.expanduser("~/Library/Fonts"),
        ]
    elif sys.platform == "win32":
        font_dirs = [r"C:\Windows\Fonts"]

    fonts = []
    for d in font_dirs:
        p = Path(d)
        if p.exists():
            for ext in ("*.ttf", "*.otf", "*.TTF", "*.OTF"):
                fonts.extend(str(f) for f in p.rglob(ext))

    return sorted(set(fonts))


# ---------------------------------------------------------------------------
# Input parsing: karaoke JSON or plain LRC
# ---------------------------------------------------------------------------

def load_karaoke_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_lrc_as_lines(path, last_line_duration=5.0):
    """Fallback: parse a plain .lrc file into the same line structure, but
    without per-word timing (so the whole line highlights at once)."""
    timestamp_re = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
    raw = []

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            matches = timestamp_re.findall(line)
            if not matches:
                continue
            text = timestamp_re.sub("", line).strip()
            for m, s in matches:
                start = int(m) * 60 + float(s)
                raw.append([start, text])

    raw.sort(key=lambda e: e[0])
    lines = []
    for i, (start, text) in enumerate(raw):
        end = raw[i + 1][0] if i + 1 < len(raw) else start + last_line_duration
        words = text.split()
        if not words:
            continue
        # Distribute the line's duration evenly across its words so there's
        # still a (rough) per-word highlight progression.
        span = max(end - start, 0.1)
        per_word = span / len(words)
        word_entries = []
        for j, w in enumerate(words):
            w_start = start + j * per_word
            w_end = w_start + per_word
            word_entries.append({"word": w, "start": w_start, "end": w_end})
        lines.append({"text": text, "start": start, "end": end, "words": word_entries})

    return lines


# ---------------------------------------------------------------------------
# Video sizing
# ---------------------------------------------------------------------------

def get_target_resolution(aspect_ratio):
    if aspect_ratio == "9:16":
        return (1080, 1920)
    elif aspect_ratio == "16:9":
        return (1920, 1080)
    else:
        raise ValueError("Aspect ratio must be '9:16' or '16:9'")


def fit_video_to_aspect(clip, target_w, target_h):
    """Scale and center-crop a video clip to exactly fill target_w x target_h."""
    target_ratio = target_w / target_h
    clip_ratio = clip.w / clip.h

    if clip_ratio > target_ratio:
        new_h = target_h
        new_w = int(round(clip.w * (target_h / clip.h)))
    else:
        new_w = target_w
        new_h = int(round(clip.h * (target_w / clip.w)))

    resized = clip.resize((new_w, new_h))
    cropped = resized.crop(
        x_center=new_w / 2,
        y_center=new_h / 2,
        width=target_w,
        height=target_h,
    )
    return cropped


# ---------------------------------------------------------------------------
# Text layout: wrap words into sub-lines that fit within max_width
# ---------------------------------------------------------------------------

def wrap_words(words, font, max_width, draw, space_px):
    """words: list of word dicts (with 'word','start','end').
    Returns a list of sub-lines, each a list of word dicts, annotated in
    place with 'width' (pixel width of the word)."""
    for w in words:
        bbox = draw.textbbox((0, 0), w["word"], font=font)
        w["width"] = bbox[2] - bbox[0]

    sub_lines = []
    current = []
    current_width = 0

    for w in words:
        added = w["width"] if not current else w["width"] + space_px
        if current and current_width + added > max_width:
            sub_lines.append(current)
            current = []
            current_width = 0
            added = w["width"]
        current.append(w)
        current_width += added

    if current:
        sub_lines.append(current)

    return sub_lines


# ---------------------------------------------------------------------------
# Frame rendering
# ---------------------------------------------------------------------------

def render_line_image(sub_lines, font, line_height, canvas_width, space_px,
                       base_color, highlight_color, stroke_color, stroke_width,
                       highlight_word_id=None):
    """Render one transparent RGBA image containing all sub-lines of a lyric
    line, center-aligned, with the word matching highlight_word_id drawn in
    highlight_color and all others in base_color."""
    height = line_height * len(sub_lines)
    img = Image.new("RGBA", (canvas_width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    for row, sub_line in enumerate(sub_lines):
        total_width = sum(w["width"] for w in sub_line) + space_px * (len(sub_line) - 1)
        x = (canvas_width - total_width) / 2
        y = row * line_height

        for w in sub_line:
            color = highlight_color if w.get("id") == highlight_word_id else base_color
            draw.text(
                (x, y),
                w["word"],
                font=font,
                fill=color,
                stroke_width=stroke_width,
                stroke_fill=stroke_color,
            )
            x += w["width"] + space_px

    return img


def pil_to_clip(img, start, duration, position, canvas_height, target_h):
    arr = np.array(img)
    clip = ImageClip(arr, transparent=True)
    clip = clip.set_start(start).set_duration(duration)

    if position == "bottom":
        clip = clip.set_position(("center", target_h - canvas_height - int(target_h * 0.08)))
    elif position == "top":
        clip = clip.set_position(("center", int(target_h * 0.08)))
    else:
        clip = clip.set_position("center")

    return clip


# ---------------------------------------------------------------------------
# Building all overlay clips
# ---------------------------------------------------------------------------

def build_overlay_clips(lyric_lines, target_w, target_h, font_path, font_size,
                         base_color, highlight_color, stroke_color, stroke_width,
                         position, no_highlight=False):
    font = ImageFont.truetype(font_path, font_size)

    # Use a dummy image/draw just for text measurement
    dummy_img = Image.new("RGBA", (10, 10))
    dummy_draw = ImageDraw.Draw(dummy_img)

    space_bbox = dummy_draw.textbbox((0, 0), "  ", font=font)
    space_px = space_bbox[2] - space_bbox[0]

    ascent, descent = font.getmetrics()
    line_height = int((ascent + descent) * 1.3)

    max_width = int(target_w * 0.9)

    clips = []

    for line_idx, line in enumerate(lyric_lines):
        words = line["words"]
        if not words:
            continue

        # Assign a stable id to each word for highlight matching
        for i, w in enumerate(words):
            w["id"] = i

        sub_lines = wrap_words(words, font, max_width, dummy_draw, space_px)
        canvas_height = line_height * len(sub_lines)

        if no_highlight:
            # Single static image for the whole line
            img = render_line_image(
                sub_lines, font, line_height, max_width, space_px,
                base_color, highlight_color, stroke_color, stroke_width,
                highlight_word_id=None,
            )
            duration = max(line["end"] - line["start"], 0.1)
            clip = pil_to_clip(img, line["start"], duration, position, canvas_height, target_h)
            clips.append(clip)
            continue

        # One image per word -> shown only while that word is active
        for w in words:
            img = render_line_image(
                sub_lines, font, line_height, max_width, space_px,
                base_color, highlight_color, stroke_color, stroke_width,
                highlight_word_id=w["id"],
            )
            duration = max(w["end"] - w["start"], 0.05)
            clip = pil_to_clip(img, w["start"], duration, position, canvas_height, target_h)
            clips.append(clip)

    return clips


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_color(value):
    """Accept either a named color, '#RRGGBB', or 'R,G,B' and return a tuple
    or string PIL understands."""
    if value.startswith("#"):
        return value
    if "," in value:
        parts = [int(p.strip()) for p in value.split(",")]
        return tuple(parts) + (255,) if len(parts) == 3 else tuple(parts)
    return value


def main():
    parser = argparse.ArgumentParser(
        description="Create a karaoke-style lyric video with word-by-word highlighting."
    )
    parser.add_argument("--audio", help="Path to audio file (mp3, wav, etc.)")
    parser.add_argument("--video", help="Path to background video file")
    parser.add_argument("--output", help="Output video file path")
    parser.add_argument("--karaoke-json", help="Path to karaoke JSON from auto_lyric_sync.py")
    parser.add_argument("--lrc", help="Path to a plain .lrc file (fallback, no true word timing)")
    parser.add_argument("--ratio", choices=["9:16", "16:9"], default="16:9",
                         help="Output aspect ratio (default: 16:9)")
    parser.add_argument("--font", help="Path to a .ttf/.otf font file")
    parser.add_argument("--font-size", type=int, default=70, help="Lyric font size (default: 70)")
    parser.add_argument("--base-color", default="white",
                         help="Color for non-highlighted words (default: white)")
    parser.add_argument("--highlight-color", default="#FFD700",
                         help="Color for the currently-sung word (default: gold)")
    parser.add_argument("--stroke-color", default="black", help="Text outline color (default: black)")
    parser.add_argument("--stroke-width", type=int, default=2, help="Text outline width (default: 2)")
    parser.add_argument("--position", choices=["top", "center", "bottom"], default="bottom",
                         help="Where lyrics appear on screen (default: bottom)")
    parser.add_argument("--no-highlight", action="store_true",
                         help="Display full lines without word-by-word highlighting")
    parser.add_argument("--list-fonts", action="store_true", help="List available system fonts and exit")

    args = parser.parse_args()

    if args.list_fonts:
        fonts = list_system_fonts()
        if not fonts:
            print("No fonts found in standard system font directories.")
        for f in fonts:
            print(f)
        return

    missing = [name for name, val in [
        ("--audio", args.audio), ("--video", args.video),
        ("--output", args.output), ("--font", args.font),
    ] if not val]
    if missing:
        parser.error(f"the following arguments are required (unless using --list-fonts): {', '.join(missing)}")

    if not args.karaoke_json and not args.lrc:
        parser.error("provide either --karaoke-json or --lrc")

    target_w, target_h = get_target_resolution(args.ratio)

    print("Loading video...")
    video = VideoFileClip(args.video)

    print("Loading audio...")
    audio = AudioFileClip(args.audio)

    if video.duration < audio.duration:
        print("Background video is shorter than the song — looping it.")
        video = video.loop(duration=audio.duration)
    else:
        video = video.subclip(0, audio.duration)

    video = video.set_audio(audio)

    print(f"Resizing/cropping to {args.ratio} ({target_w}x{target_h})...")
    video = fit_video_to_aspect(video, target_w, target_h)

    print("Loading lyric timing...")
    if args.karaoke_json:
        lyric_lines = load_karaoke_json(args.karaoke_json)
    else:
        lyric_lines = load_lrc_as_lines(args.lrc)

    base_color = parse_color(args.base_color)
    highlight_color = parse_color(args.highlight_color)
    stroke_color = parse_color(args.stroke_color)

    print("Rendering lyric overlay frames (this is the slow part)...")
    overlay_clips = build_overlay_clips(
        lyric_lines, target_w, target_h, args.font, args.font_size,
        base_color, highlight_color, stroke_color, args.stroke_width,
        args.position, no_highlight=args.no_highlight,
    )
    print(f"Created {len(overlay_clips)} overlay frame(s).")

    print("Compositing final video...")
    final = CompositeVideoClip([video] + overlay_clips, size=(target_w, target_h))
    final = final.set_duration(audio.duration)

    print(f"Rendering to {args.output} (this can take a while)...")
    final.write_videofile(
        args.output,
        fps=video.fps or 30,
        codec="libx264",
        audio_codec="aac",
        threads=4,
    )

    print("Done!")


if __name__ == "__main__":
    main()
