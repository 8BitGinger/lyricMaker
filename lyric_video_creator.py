#!/usr/bin/env python3
"""
Lyric Video Creator
====================

Combines an audio file, a background video, and a timed lyrics (LRC) file
into a finished lyric video, in either 16:9 (landscape) or 9:16 (vertical/
TikTok/Reels/Shorts) format.

Requirements:
    pip install moviepy pillow

moviepy's TextClip requires ImageMagick to be installed on the system
(on Windows, download from imagemagick.org and make sure "Install legacy
utilities" is checked during setup so the 'convert' command is available).

LRC FORMAT
----------
A simple text file where each line of lyrics is prefixed with a timestamp:

    [00:12.50]First line of lyrics
    [00:16.30]Second line of lyrics
    [00:20.00]Third line of lyrics

Each line's duration runs until the next timestamp begins. The final line
gets a default 5-second duration (configurable in code if needed).

USAGE
-----
List available system fonts:
    python lyric_video_creator.py --list-fonts

Create a vertical (9:16) lyric video:
    python lyric_video_creator.py \\
        --audio song.mp3 \\
        --video background.mp4 \\
        --lyrics song.lrc \\
        --output lyric_video.mp4 \\
        --ratio 9:16 \\
        --font "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" \\
        --font-size 80 \\
        --color white \\
        --stroke-color black \\
        --stroke-width 3
"""

import argparse
import os
import re
import sys
from pathlib import Path

from moviepy.editor import (
    VideoFileClip,
    AudioFileClip,
    CompositeVideoClip,
    TextClip,
)


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
# LRC parsing
# ---------------------------------------------------------------------------

def parse_lrc(lrc_path, last_line_duration=5.0):
    """Parse an LRC file into a list of [start_time, end_time, text] entries."""
    timestamp_re = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
    entries = []

    with open(lrc_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            matches = timestamp_re.findall(line)
            if not matches:
                continue  # skip metadata tags like [ar:Artist], [ti:Title]

            text = timestamp_re.sub("", line).strip()
            for minutes, seconds in matches:
                start = int(minutes) * 60 + float(seconds)
                entries.append([start, None, text])

    entries.sort(key=lambda e: e[0])
    for i in range(len(entries) - 1):
        entries[i][1] = entries[i + 1][0]
    if entries:
        entries[-1][1] = entries[-1][0] + last_line_duration

    return entries


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
        # source is relatively wider than target -> match height, crop width
        new_h = target_h
        new_w = int(round(clip.w * (target_h / clip.h)))
    else:
        # source is relatively taller than target -> match width, crop height
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
# Lyric overlay generation
# ---------------------------------------------------------------------------

def create_lyric_clips(lyrics, target_w, target_h, font_path, font_size,
                        color, stroke_color, stroke_width, position):
    clips = []
    for start, end, text in lyrics:
        if not text:
            continue

        duration = max(end - start, 0.1)

        txt_clip = TextClip(
            text,
            fontsize=font_size,
            font=font_path,
            color=color,
            stroke_color=stroke_color,
            stroke_width=stroke_width,
            method="caption",
            size=(int(target_w * 0.9), None),
            align="center",
        )

        txt_clip = txt_clip.set_start(start).set_duration(duration)

        if position == "bottom":
            txt_clip = txt_clip.set_position(("center", "bottom")).margin(
                bottom=int(target_h * 0.08), opacity=0
            )
        elif position == "top":
            txt_clip = txt_clip.set_position(("center", "top")).margin(
                top=int(target_h * 0.08), opacity=0
            )
        else:  # center
            txt_clip = txt_clip.set_position("center")

        # Simple fade in/out so lines don't pop harshly
        fade = min(0.25, duration / 4)
        txt_clip = txt_clip.crossfadein(fade).crossfadeout(fade)

        clips.append(txt_clip)

    return clips


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Create a lyric video from an audio file, a background video, and an LRC lyrics file."
    )
    parser.add_argument("--audio", help="Path to audio file (mp3, wav, etc.)")
    parser.add_argument("--video", help="Path to background video file")
    parser.add_argument("--lyrics", help="Path to LRC lyrics file")
    parser.add_argument("--output", help="Output video file path")
    parser.add_argument("--ratio", choices=["9:16", "16:9"], default="16:9",
                         help="Output aspect ratio (default: 16:9)")
    parser.add_argument("--font", help="Path to a .ttf/.otf font file")
    parser.add_argument("--font-size", type=int, default=70, help="Lyric font size (default: 70)")
    parser.add_argument("--color", default="white", help="Lyric text color (default: white)")
    parser.add_argument("--stroke-color", default="black", help="Lyric text outline color (default: black)")
    parser.add_argument("--stroke-width", type=int, default=2, help="Lyric text outline width (default: 2)")
    parser.add_argument("--position", choices=["top", "center", "bottom"], default="bottom",
                         help="Where lyrics appear on screen (default: bottom)")
    parser.add_argument("--list-fonts", action="store_true", help="List available system fonts and exit")

    args = parser.parse_args()

    if args.list_fonts:
        fonts = list_system_fonts()
        if not fonts:
            print("No fonts found in standard system font directories.")
        for f in fonts:
            print(f)
        return

    # Validate required args for actual rendering
    missing = [name for name, val in [
        ("--audio", args.audio), ("--video", args.video),
        ("--lyrics", args.lyrics), ("--output", args.output),
        ("--font", args.font),
    ] if not val]
    if missing:
        parser.error(f"the following arguments are required (unless using --list-fonts): {', '.join(missing)}")

    target_w, target_h = get_target_resolution(args.ratio)

    print("Loading video...")
    video = VideoFileClip(args.video)

    print("Loading audio...")
    audio = AudioFileClip(args.audio)

    # Match video length to audio length
    if video.duration < audio.duration:
        print("Background video is shorter than the song — looping it.")
        video = video.loop(duration=audio.duration)
    else:
        video = video.subclip(0, audio.duration)

    video = video.set_audio(audio)

    print(f"Resizing/cropping to {args.ratio} ({target_w}x{target_h})...")
    video = fit_video_to_aspect(video, target_w, target_h)

    print("Parsing lyrics...")
    lyrics = parse_lrc(args.lyrics)
    print(f"Found {len(lyrics)} lyric lines.")

    print("Building lyric overlays...")
    lyric_clips = create_lyric_clips(
        lyrics, target_w, target_h, args.font, args.font_size,
        args.color, args.stroke_color, args.stroke_width, args.position
    )

    print("Compositing final video...")
    final = CompositeVideoClip([video] + lyric_clips, size=(target_w, target_h))
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
