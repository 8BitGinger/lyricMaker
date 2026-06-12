#!/usr/bin/env python3
"""
Auto Lyric Sync
================

Automatically generates a timed LRC lyrics file from a song, similar to how
TikTok/CapCut auto-caption features work.

TWO MODES
---------
1. TRANSCRIBE MODE (no lyrics provided)
   Whisper listens to the audio and both transcribes and times the words.
   Fast and fully automatic, but can mis-hear lyrics in busy mixes.

       python auto_lyric_sync.py --audio song.mp3 --output song.lrc

2. ALIGN MODE (you provide the exact lyrics)  -- RECOMMENDED for music
   You supply a plain text file with the correct lyrics (one line per lyric
   line, blank lines allowed between sections). WhisperX aligns each word to
   the audio using forced alignment, so the *words* are guaranteed correct
   and only the *timing* is detected. Much better for singing/rap.

       python auto_lyric_sync.py --audio song.mp3 --lyrics-text lyrics.txt --output song.lrc

OPTIONAL: VOCAL ISOLATION (strongly recommended for either mode)
------------------------------------------------------------------
Instrumentals/beats badly confuse timing models. If Demucs is installed,
pass --isolate-vocals to separate vocals before running alignment.

INSTALL
-------
    pip install faster-whisper          # transcribe mode
    pip install whisperx                # align mode (pulls in torch)
    pip install demucs                  # optional vocal isolation

OUTPUT
------
A .lrc file in the same format used by lyric_video_creator.py:

    [00:12.50]First line of lyrics
    [00:16.30]Second line of lyrics

With --karaoke-json, also writes a JSON file containing each lyric line
along with per-word start/end times within that line -- this is the format
consumed by lyric_video_karaoke.py for word-by-word highlight animation.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile


# ---------------------------------------------------------------------------
# Vocal isolation (optional)
# ---------------------------------------------------------------------------

def isolate_vocals(audio_path, work_dir):
    """Run Demucs to separate vocals from the rest of the mix.

    Returns the path to the isolated vocal stem (wav).
    """
    print("Isolating vocals with Demucs (this can take a few minutes)...")
    cmd = [
        sys.executable, "-m", "demucs",
        "--two-stems", "vocals",
        "-o", work_dir,
        audio_path,
    ]
    subprocess.run(cmd, check=True)

    # Demucs writes to <out>/<model_name>/<track_name>/vocals.wav
    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    for root, _, files in os.walk(work_dir):
        if "vocals.wav" in files and base_name in root:
            return os.path.join(root, "vocals.wav")

    raise RuntimeError("Could not locate Demucs vocal output.")


# ---------------------------------------------------------------------------
# LRC writing helpers
# ---------------------------------------------------------------------------

def seconds_to_lrc_timestamp(seconds):
    minutes = int(seconds // 60)
    secs = seconds - minutes * 60
    return f"[{minutes:02d}:{secs:05.2f}]"


def write_lrc(lines, output_path):
    """lines: list of (start_seconds, text)"""
    with open(output_path, "w", encoding="utf-8") as f:
        for start, text in lines:
            f.write(f"{seconds_to_lrc_timestamp(start)}{text}\n")


# ---------------------------------------------------------------------------
# Mode 1: Transcribe (blind ASR with word timestamps)
# ---------------------------------------------------------------------------

def transcribe_mode(audio_path, model_size="medium", max_line_words=7):
    from faster_whisper import WhisperModel

    print(f"Loading Whisper model '{model_size}'...")
    model = WhisperModel(model_size, device="auto", compute_type="auto")

    print("Transcribing (word-level timestamps)...")
    segments, _ = model.transcribe(audio_path, word_timestamps=True)

    words = []
    for segment in segments:
        for w in segment.words:
            words.append((w.start, w.word.strip()))

    return group_words_into_lines(words, max_line_words), words


# ---------------------------------------------------------------------------
# Mode 2: Align (forced alignment to known lyrics)
# ---------------------------------------------------------------------------

def align_mode(audio_path, lyrics_text_path, language="en"):
    import whisperx

    device = "cuda" if _cuda_available() else "cpu"
    print(f"Running WhisperX forced alignment on {device}...")

    with open(lyrics_text_path, "r", encoding="utf-8") as f:
        raw_lines = [ln.strip() for ln in f.readlines()]
    lyric_lines = [ln for ln in raw_lines if ln]  # drop blank lines

    full_text = " ".join(lyric_lines)

    audio = whisperx.load_audio(audio_path)

    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)

    # WhisperX alignment expects "segments" with text + rough time bounds.
    # We give it the whole song as one segment spanning its full duration;
    # the aligner finds precise word-level timing within it.
    duration = len(audio) / whisperx.audio.SAMPLE_RATE
    segments = [{"text": full_text, "start": 0.0, "end": duration}]

    result = whisperx.align(segments, align_model, metadata, audio, device,
                             return_char_alignments=False)

    words = []
    for seg in result["segments"]:
        for w in seg.get("words", []):
            if "start" in w:
                words.append((w["start"], w["word"].strip()))

    return regroup_to_original_lines(words, lyric_lines), words


def _cuda_available():
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Grouping helpers
# ---------------------------------------------------------------------------

def group_words_into_lines(words, max_line_words):
    """Group a flat list of (start_time, word) into lyric lines of up to
    max_line_words words each. Used for transcribe mode where we don't know
    the original line breaks."""
    lines = []
    current_words = []
    current_start = None

    for start, word in words:
        if not word:
            continue
        if current_start is None:
            current_start = start
        current_words.append(word)
        if len(current_words) >= max_line_words:
            lines.append((current_start, " ".join(current_words)))
            current_words = []
            current_start = None

    if current_words:
        lines.append((current_start, " ".join(current_words)))

    return lines


def regroup_to_original_lines(words, original_lines):
    """Map aligned words back onto the user's original line breaks, so the
    output LRC matches the lyrics file they provided line-for-line."""
    lines = []
    word_idx = 0

    for line_text in original_lines:
        line_word_count = len(line_text.split())
        if line_word_count == 0:
            continue

        chunk = words[word_idx:word_idx + line_word_count]
        if not chunk:
            # ran out of aligned words (shouldn't normally happen)
            start_time = lines[-1][0] + 2 if lines else 0.0
        else:
            start_time = chunk[0][0]

        lines.append((start_time, line_text))
        word_idx += line_word_count

    return lines


# ---------------------------------------------------------------------------
# Karaoke JSON export (line + per-word timing, for word-by-word highlighting)
# ---------------------------------------------------------------------------

def build_karaoke_json(lines, flat_words, default_last_duration=0.4):
    """Combine the line groupings with the flat word timing list to produce
    a structure of:

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

    `lines` is the list of (start_time, text) tuples used for the LRC file.
    `flat_words` is the ordered list of (start_time, word) that the line
    groupings were derived from -- so concatenating each line's word count
    walks through flat_words in the same order.
    """
    # Filter out empty words and compute an end time for each word
    cleaned = [(s, w) for s, w in flat_words if w]
    timed_words = []
    for i, (start, word) in enumerate(cleaned):
        if i + 1 < len(cleaned):
            end = cleaned[i + 1][0]
        else:
            end = start + default_last_duration
        # guard against zero/negative durations from noisy timestamps
        if end <= start:
            end = start + 0.05
        timed_words.append({"word": word, "start": start, "end": end})

    result = []
    idx = 0
    for line_start, line_text in lines:
        n = len(line_text.split())
        chunk = timed_words[idx:idx + n]
        idx += n
        if not chunk:
            continue
        result.append({
            "text": line_text,
            "start": chunk[0]["start"],
            "end": chunk[-1]["end"],
            "words": chunk,
        })

    return result


def write_karaoke_json(karaoke_lines, output_path):
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(karaoke_lines, f, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Automatically generate timed LRC lyrics from a song."
    )
    parser.add_argument("--audio", required=True, help="Path to the song audio file")
    parser.add_argument("--output", required=True, help="Path to write the .lrc file")
    parser.add_argument("--lyrics-text", help=(
        "Path to a plain text file with the correct lyrics (one line per "
        "lyric line). If provided, uses ALIGN MODE (recommended)."
    ))
    parser.add_argument("--isolate-vocals", action="store_true",
                         help="Use Demucs to isolate vocals before processing")
    parser.add_argument("--whisper-model", default="medium",
                         help="Whisper model size for transcribe mode "
                              "(tiny/base/small/medium/large-v3). Default: medium")
    parser.add_argument("--language", default="en", help="Language code (default: en)")
    parser.add_argument("--max-line-words", type=int, default=7,
                         help="Words per line in transcribe mode (default: 7)")
    parser.add_argument("--karaoke-json",
                         help="Optional path to also write line+word-level timestamps as "
                              "JSON, for word-by-word karaoke highlighting")
    parser.add_argument("--default-word-duration", type=float, default=0.4,
                         help="Fallback duration (seconds) for the very last word "
                              "when no following word timestamp exists (default: 0.4)")

    args = parser.parse_args()

    audio_path = args.audio
    temp_dir = None

    if args.isolate_vocals:
        temp_dir = tempfile.mkdtemp(prefix="lyric_sync_")
        audio_path = isolate_vocals(args.audio, temp_dir)
        print(f"Using isolated vocal stem: {audio_path}")

    try:
        if args.lyrics_text:
            print("Mode: forced alignment (using your provided lyrics)")
            lines, flat_words = align_mode(audio_path, args.lyrics_text, args.language)
        else:
            print("Mode: blind transcription")
            lines, flat_words = transcribe_mode(audio_path, args.whisper_model, args.max_line_words)

        write_lrc(lines, args.output)
        print(f"Wrote {len(lines)} lyric lines to {args.output}")

        if args.karaoke_json:
            karaoke_lines = build_karaoke_json(lines, flat_words, args.default_word_duration)
            write_karaoke_json(karaoke_lines, args.karaoke_json)
            print(f"Wrote karaoke word-timing data to {args.karaoke_json}")

    finally:
        if temp_dir:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
