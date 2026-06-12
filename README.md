# Lyric Video Toolkit

A set of Python scripts for turning a song and a background video into a
finished lyric video — either with full lines of text, or with TikTok-style
word-by-word highlighting. Supports both 16:9 (YouTube) and 9:16 (TikTok,
Reels, Shorts) output.

---

## What's in this folder

| File | Purpose |
|---|---|
| `requirements.txt` | List of Python packages needed |
| `install_windows.bat` | One-click setup for Windows |
| `install_unix.sh` | One-click setup for macOS/Linux |
| `auto_lyric_sync.py` | Listens to your song and generates timed lyrics |
| `lyric_video_creator.py` | Builds a lyric video with full lines of text |
| `lyric_video_karaoke.py` | Builds a lyric video with word-by-word highlighting |

---

## 1. Install everything

You'll need **Python 3.9 or newer** installed first
([python.org/downloads](https://www.python.org/downloads/) — on Windows,
make sure to check "Add Python to PATH" during install).

Then:

- **Windows:** double-click `install_windows.bat`
- **macOS/Linux:** open a terminal in this folder and run:
  ```
  chmod +x install_unix.sh
  ./install_unix.sh
  ```

This installs all required Python packages and checks that `ffmpeg` (needed
for reading/writing video) is available on your system. If it's missing, the
script tells you how to install it.

**One-time notes:**
- The alignment tool (`whisperx`) downloads a small model the first time you
  use it — this needs an internet connection and may take a few minutes.
- Everything runs on a normal CPU. If you have an NVIDIA GPU, processing can
  be sped up by installing a CUDA build of PyTorch
  ([pytorch.org/get-started/locally](https://pytorch.org/get-started/locally/)),
  but this is optional.

---

## 2. Prepare your files

For each song, gather:

1. **The audio file** — e.g. `song.mp3`
2. **A background video** — any `.mp4` you want playing behind the lyrics.
   It can be shorter or longer than the song; the script will loop or trim
   it to match automatically.
3. **The lyrics as plain text** — create a `.txt` file with one lyric line
   per line. Don't include section labels like `[Verse]` or `[Chorus]` —
   just the words that are actually sung. For example:

   ```
   Walking through the city lights tonight
   Everything feels different now
   I can finally breathe again
   ```

---

## 3. Generate timed lyrics

This step listens to the song and figures out exactly when each word is
sung, using your lyrics text as the source of truth (so the words are
guaranteed correct — only the timing is detected).

```
python auto_lyric_sync.py ^
  --audio "song.mp3" ^
  --lyrics-text "lyrics.txt" ^
  --output "song.lrc" ^
  --karaoke-json "song_karaoke.json" ^
  --isolate-vocals
```

*(On macOS/Linux, replace the `^` line-continuation characters with `\`.)*

**What this does:**
- `--isolate-vocals` separates the vocals from the instrumental first, which
  significantly improves timing accuracy — especially on tracks with heavy
  beats. This step adds a few minutes of processing.
- `--output song.lrc` writes a standard timed-lyrics file (useful on its own
  if you ever want to check the timing in a text editor).
- `--karaoke-json song_karaoke.json` writes the word-by-word timing data
  used for highlighting in the next step.

This is the slowest step in the whole process — let it run, you'll see
progress messages as it works.

> **No lyrics file?** You can omit `--lyrics-text` entirely and the script
> will transcribe the song from scratch. This is faster to set up but can
> misread words in busy mixes, so it's best used as a fallback.

---

## 4. Pick a font

Both video-rendering scripts can list the fonts available on your system:

```
python lyric_video_karaoke.py --list-fonts
```

This prints full file paths (e.g.
`C:\Windows\Fonts\arialbd.ttf` or
`/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf`).
Copy the path of the one you want — you'll use it in the next step.

---

## 5. Create the video

### Option A — Word-by-word highlighting (recommended)

```
python lyric_video_karaoke.py ^
  --audio "song.mp3" ^
  --video "background.mp4" ^
  --karaoke-json "song_karaoke.json" ^
  --output "lyric_video.mp4" ^
  --ratio 9:16 ^
  --font "C:\Windows\Fonts\arialbd.ttf" ^
  --font-size 80 ^
  --base-color white ^
  --highlight-color "#FFD700" ^
  --stroke-color black ^
  --stroke-width 3 ^
  --position bottom
```

Each word lights up in the highlight color as it's sung, while the rest of
the line stays in the base color — the classic TikTok caption look.

### Option B — Full lines of text (simpler, faster to render)

```
python lyric_video_creator.py ^
  --audio "song.mp3" ^
  --video "background.mp4" ^
  --lyrics "song.lrc" ^
  --output "lyric_video.mp4" ^
  --ratio 16:9 ^
  --font "C:\Windows\Fonts\arialbd.ttf" ^
  --font-size 70 ^
  --color white ^
  --stroke-color black ^
  --stroke-width 2 ^
  --position bottom
```

Each full line appears and disappears in sync with the timed lyrics, without
per-word highlighting.

---

## Settings reference

| Option | What it controls | Typical values |
|---|---|---|
| `--ratio` | Output video shape | `16:9` (YouTube/landscape) or `9:16` (TikTok/Reels/vertical) |
| `--font` | Path to a font file | Any `.ttf`/`.otf` from `--list-fonts` |
| `--font-size` | Lyric text size in pixels | `60`–`100` depending on ratio |
| `--base-color` / `--color` | Normal text color | `white`, `black`, `#RRGGBB` |
| `--highlight-color` | Active word color (karaoke only) | `#FFD700` (gold), `#FF4500` (orange-red), etc. |
| `--stroke-color` | Outline color around text | Usually `black` for contrast |
| `--stroke-width` | Outline thickness in pixels | `2`–`4` |
| `--position` | Where lyrics sit on screen | `top`, `center`, `bottom` |

Colors can be a name (`white`), a hex code (`#FFD700`), or comma-separated
RGB (`255,215,0`).

---

## Typical end-to-end run (copy/paste template)

```
python auto_lyric_sync.py --audio song.mp3 --lyrics-text lyrics.txt --output song.lrc --karaoke-json song_karaoke.json --isolate-vocals

python lyric_video_karaoke.py --audio song.mp3 --video background.mp4 --karaoke-json song_karaoke.json --output lyric_video.mp4 --ratio 9:16 --font "PATH_TO_FONT" --font-size 80
```

---

## Troubleshooting

**"ffmpeg not found" or video won't load**
Make sure ffmpeg is installed and on your system PATH (the install scripts
check this and tell you how to fix it).

**Alignment seems off / words highlight too early or late**
Try running `auto_lyric_sync.py` with `--isolate-vocals` if you haven't
already — instrumentals are the most common cause of timing drift.

**Rendering is slow**
The karaoke (word-by-word) version is slower than the full-line version
because it renders one image per word. For long songs, expect this step to
take a while — let it run in the background.

**Whisper/WhisperX install issues**
These packages depend on PyTorch, which is large and occasionally
version-sensitive. If installation fails, try upgrading pip first
(`python -m pip install --upgrade pip`) and re-running the install script.

**Text is too big/small or wraps awkwardly**
Adjust `--font-size`, or try a different `--position`. The text
auto-wraps to fit 90% of the video width.

---

## Quick glossary

- **LRC file** — a plain text file with timestamps, the standard format for
  synced lyrics (e.g. `[00:12.50]First line of lyrics`).
- **Forced alignment** — matching known lyrics text to the exact moments
  they're sung in the audio (used by `auto_lyric_sync.py` for accurate
  timing).
- **Vocal isolation** — separating the singing/rapping from the instrumental
  track to improve timing accuracy.
