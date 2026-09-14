# Lyric Video Toolkit

Python tools for turning a song, timed lyrics, and a background video into a
finished lyric video. The renderers support landscape `16:9` output for
YouTube and vertical `9:16` output for TikTok, Reels, and Shorts.

## Contents

| File | Purpose |
|---|---|
| `requirements.txt` | Python dependencies for the GUI, sync tools, and renderer |
| `install_windows.bat` | Windows setup script; installs packages and checks `ffmpeg` |
| `install_unix.sh` | macOS/Linux setup script; installs packages and checks `ffmpeg` |
| `lyric_video_app.py` | Windows pygame GUI with lyric review and automatic Desktop output |
| `auto_lyric_sync.py` | CLI that creates an LRC file from known lyrics or transcription |
| `lyric_video_creator.py` | CLI that composites an LRC file over a background video |

## Installation

Install **Python 3.9 or newer** from [python.org](https://www.python.org/downloads/).
On Windows, enable **Add Python to PATH** during installation.

Run the installer from this directory:

**Windows**: double-click `install_windows.bat` or run it from Command Prompt.

**macOS/Linux**:

```bash
chmod +x install_unix.sh
./install_unix.sh
```

The scripts upgrade `pip`, install the packages in `requirements.txt`, and
check whether `ffmpeg` is on `PATH`. They do not create a virtual environment
or install `ffmpeg` automatically. On Debian/Ubuntu use `sudo apt install
ffmpeg`; on Fedora use `sudo dnf install ffmpeg`; on macOS use `brew install
ffmpeg`. Windows users can use `winget install ffmpeg` from an elevated
Command Prompt.

The first use of Whisper, WhisperX, or Demucs may download model files and
can require an internet connection. CPU processing is supported. An NVIDIA
GPU can speed up inference if a CUDA-enabled PyTorch installation is used.

The CLI renderer uses MoviePy `TextClip`, which requires ImageMagick in
addition to `ffmpeg`. Install ImageMagick separately and ensure its executable
is available to MoviePy. The GUI does not use ImageMagick for lyric overlays;
it renders text with Pillow.

## Inputs and output

You need:

1. An audio file such as `.mp3`, `.wav`, `.flac`, `.ogg`, or `.m4a`.
2. A background video. If it is shorter than the audio, it is looped; if it
   is longer, it is trimmed to the audio duration.
3. Either a plain text lyrics file, an LRC file, or no lyrics file.

Plain text lyrics should contain one sung line per line. Blank lines are
ignored. Keep section labels such as `[Verse]` out of the file unless they
should appear as onscreen lyrics.

An LRC file uses timestamps such as:

```text
[00:12.50]First line of lyrics
[00:16.30]Second line of lyrics
```

The renderers show each line until the next timestamp. The final line gets a
default five-second duration. Metadata tags without a timestamped lyric line
are ignored.

## CLI workflow

### 1. Create timed lyrics

For the best word accuracy, provide the original lyrics. This uses WhisperX
forced alignment to detect timing while preserving the supplied text and line
breaks:

```powershell
python auto_lyric_sync.py ^
  --audio "song.mp3" ^
  --lyrics-text "lyrics.txt" ^
  --output "song.lrc" ^
  --isolate-vocals
```

On macOS/Linux, replace each `^` with `\`.

If no lyrics file is available, omit `--lyrics-text`. The tool then uses
`faster-whisper` to transcribe the audio, preserving word timestamps and
grouping up to seven words per output line by default:

```bash
python auto_lyric_sync.py --audio song.mp3 --output song.lrc
```

Useful sync options:

| Option | Purpose | Default |
|---|---|---|
| `--lyrics-text` | Use supplied text for forced alignment | None |
| `--isolate-vocals` | Run Demucs before timing detection | Off |
| `--whisper-model` | Transcription model in blind mode | `medium` |
| `--language` | WhisperX language code | `en` |
| `--max-line-words` | Maximum words per blind-transcription line | `7` |

Review and correct the generated `.lrc` file before rendering. Text changes
do not change timestamps; edit the timestamp itself when timing is wrong.

### 2. Render the video

List fonts known to the renderer:

```bash
python lyric_video_creator.py --list-fonts
```

Then render with a font path from that list:

```powershell
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

The CLI renderer center-crops the background to exactly `1920x1080` or
`1080x1920`, overlays each timed line with a short fade, and encodes H.264
video with AAC audio. The output path is controlled by `--output`.

| Option | Values | Description |
|---|---|---|
| `--ratio` | `16:9`, `9:16` | Output aspect ratio |
| `--font` | `.ttf` or `.otf` path | Lyric font; required for rendering |
| `--font-size` | Integer | Text size in pixels; default `70` |
| `--color` | Color name or hex value | Text color; default `white` |
| `--stroke-color` | Color name or hex value | Text outline; default `black` |
| `--stroke-width` | Integer | Outline width; default `2` |
| `--position` | `top`, `center`, `bottom` | Lyric placement; default `bottom` |

## Windows GUI workflow

Launch the pygame application with:

```powershell
python lyric_video_app.py
```

The GUI is Windows-specific: it uses Windows file dialogs and scans
`C:\Windows\Fonts`. Choose an audio file and background video, then optionally
choose an `.lrc` or `.txt` lyrics file. Leaving lyrics unselected enables
automatic transcription.

When the app prepares lyrics:

- An `.lrc` file is parsed directly.
- A plain text file is first sent through WhisperX forced alignment. If that
  fails, the app falls back to `faster-whisper` word transcription and matches
  the detected words back to the original lines.
- With no lyrics file, `faster-whisper` transcribes the audio. The GUI offers
  `base` and `large-v3-turbo` model choices.
- Optional Demucs vocal isolation can be enabled for any of these paths.

The app then opens **Review Lyrics**. You can edit line text, remove lines,
or cancel and return to setup. Approving starts the render. The GUI includes:

- **Font selector**: Scans system fonts and renders font names in their actual typeface preview.
- **Text color palette**: Clickable color swatches (`White`, `Black`, `Blue`, `Green`, `Yellow`, `Red`) with adaptive contrasting outlines.
- **Atmospheric overlays**: Procedural looping effects placed behind the lyrics:
  - `None`: Clean background video.
  - `Fire`: Floating/drifting embers and sparks with radiant floor glow.
  - `Disco`: Color washes and rhythmic strobe flashes.
  - `Old Film`: Vintage grain, vignette shading, and vertical film scratches.
  - `Wind`: Atmospheric drifting mist and fog wisps.
- **Aspect ratio & position**: Configurable `16:9` / `9:16` and `Top`, `Center`, `Bottom` positioning with safe-zone padding.

The completed MP4 is saved to the Windows Desktop as
`lyric_video_YYYYMMDD_HHMMSS.mp4`.

## Backend process

Both entry points use MoviePy and `ffmpeg` for media I/O and H.264/AAC
encoding, but their lyric-rendering paths differ:

1. **Acquire timing**: WhisperX aligns supplied lyrics, or faster-whisper
   transcribes audio when text is unavailable. Demucs is an optional
   pre-processing step that creates a temporary vocal stem.
2. **Preserve lines**: supplied lyric text remains the displayed text. The
   sync tools map detected word timestamps back to the original line breaks.
3. **Fit media**: the background is resized and center-cropped to the selected
   aspect ratio, then looped or trimmed to the audio length.
4. **Build overlays**: the GUI pre-renders unique lyric lines as transparent
   Pillow images and alpha-blends the active line into each video frame. The
   CLI creates MoviePy `TextClip` overlays and applies short crossfades.
5. **Encode**: rendering uses available CPU threads. The GUI uses a higher
   quality social-video profile with `yuv420p`, `+faststart`, and a 16 Mbps
   video bitrate; the CLI uses MoviePy's `veryfast` x264 preset.

Temporary Demucs output is removed after timing completes. Model caches are
managed by the underlying Whisper, WhisperX, and Demucs packages.

## Troubleshooting

**`ffmpeg not found` or media will not load**

Install `ffmpeg` and make sure it is on `PATH`. Restart the terminal after
changing `PATH`, then run `ffmpeg -version` to verify it.

**CLI text rendering fails or MoviePy cannot create `TextClip`**

Install ImageMagick separately and make sure MoviePy can locate it. This
requirement applies to `lyric_video_creator.py`; the GUI uses Pillow instead.

**Timing is early, late, or incomplete**

Use the exact lyrics with `--lyrics-text` or the GUI `.txt` workflow, enable
vocal isolation, and review the resulting timestamps. Editing displayed text
does not move a line in time.

**WhisperX, faster-whisper, or PyTorch installation fails**

Upgrade `pip` with `python -m pip install --upgrade pip` and retry. These
packages are large and version-sensitive. CUDA support is optional; start with
the CPU installation if GPU setup is causing the failure.

**Rendering is slow**

Model inference and Demucs vocal isolation are usually the expensive stages.
The GUI and CLI use multiple CPU threads for encoding, and inference uses a
CUDA device automatically when the installed backend reports one.

**Text wraps or is hidden by a social platform's controls**

Reduce `--font-size`, choose a different position, or use the GUI's vertical
safe-zone layout. The GUI keeps extra top and bottom padding for `9:16` output.

## Glossary

- **LRC**: Plain text lyrics with timestamps, for example `[00:12.50]Line`.
- **Forced alignment**: Timing known lyrics against audio without replacing
  the supplied words.
- **Blind transcription**: Generating both lyric text and timing from audio.
- **Vocal isolation**: Separating vocals from the instrumental mix before
  timing detection.
