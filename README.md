# Transcribr

(c) James Leaver, 2026. Version 0.8.1.

An experimental GUI for transcribing audio and video files on macOS and
Windows. A Whisper engine (openai-whisper, faster-whisper, or
mlx-whisper) does the transcription, the result is grouped into likely
paragraphs, and a built-in review pane lets you label speakers, edit
text, search and replace, and specifically play each paragraph from 
the source audio before saving as Word (`.docx`), PDF, or plain text.
Several files can be queued and transcribed in one unattended batch.

**Everything runs locally on your computer — no audio, video, or
transcripts are uploaded to the internet.** This may be particularly
important for lawyers who need to create transcriptions of material
that it may not be appropriate to uploaded to an external website or
AI service (for example, material that is subject to non-publication or
suppression orders, the implied (_Harman_) undertaking, material
produced on subpoena, or any material that is the subject of a statutory
prohibition upon publication).

The quality of the transcription will depend on the quality of the audio
fed into it, as well as various settings that can be configured by the
user. Good quality audio will usually produce good quality transcript.
**Advanced decoding** can be adjusted that will improve the quality of poor
transcripts (any even nonsense ones). It can be worth playing around 
with them to work out what might work best for paticular types of
recordings. See the heading **Decoding options** below for a further
description of what various settings do.

If words from different speakers are being transcribed into the same
paragraph, adjust the **paragraph gap** setting down. If the same speaker's
words are being transcribed into multiple paragraphs, then adjust the same
setting up.

The **Review pane** is designed to allow easy editing of the transcription
created by the software. You can navigate between paragraphs of text with
the up/down arrows on your keyboard. To edit the text in a paragraph, press
the `enter` key (or `return` on a Mac). Press `enter` again to save the edit.
When not in 'edit' mode, pressing a number (eg `1`, `2`, `3`, etc) will
assign a speaker to the paragraph that is in focus. To split at paragraph at
a particular word (which may be necessary if there is a change in the speaker),
just `double-click` that word. To merge a paragraph with the paragraph above
it, press the `M` key. To listen to the audio segment that relates to the
paragraph that is in focus, press `P`.

When a particular model is run for the first time, that model will be
downloaded to your computer and stored locally. The `small.en` and `medium.en`
models may perform adequately on clear, crisp audio. On trickier audio, the
`large-v3-turbo` model may do better, but it will be slower.

Use at your own risk.

Questions: [jleaver@sgchambers.com.au](mailto:jleaver@sgchambers.com.au)

## Screenshots

Main pane:

<img width="1312" height="912" alt="Screenshot 2026-07-16 at 2 28 28 pm" src="https://github.com/user-attachments/assets/c91c6f47-ba0c-4806-b54c-36510e1f786e" />


Review pane:

<img width="1312" height="912" alt="Screenshot 2026-07-16 at 2 33 29 pm" src="https://github.com/user-attachments/assets/9900d430-8574-4682-8788-8feb9f2abab2" />


Result:

<img width="781" height="1101" alt="Screenshot 2026-07-16 at 2 34 14 pm" src="https://github.com/user-attachments/assets/1e5c4b31-a93b-4e28-8e78-747da2b2605b" />


## What this folder contains

```
Transcribr-Installer/
├── INSTALL.txt              ← Quick-start instructions
├── README.md                ← This file
├── transcribr.py            ← The cross-platform GUI itself
├── tests/                   ← Automated test suite
│   ├── test_transcribr.py
│   └── run_tests.command    ← Mac users: double-click to run the tests
├── macos/
│   ├── install.command      ← Mac users: double-click this
│   └── app_template/        ← Files used by the installer
└── windows/
    ├── install.bat          ← Windows users: double-click this
    ├── install.ps1          ← The actual installer (run by install.bat)
    └── icon.ico
```

## Requirements

**macOS:**
- macOS 11 (Big Sur) or later
- ~5 GB free disk space
- An admin password (Homebrew may need it during install)

**Windows:**
- Windows 10 (1809+) or Windows 11
- ~5 GB free disk space
- `winget` (built into modern Windows; install "App Installer" from the
  Microsoft Store if missing)
- The Microsoft Edge WebView2 runtime, for the 0.7.0 interface —
  already present on Windows 11 and most Windows 10 machines; the
  installer checks and installs it if missing

You do **not** need Python, ffmpeg, or Whisper pre-installed.
The installer handles all of that.

## How to install

### macOS

1. Download and unzip the latest release.
2. Open the `macos` folder.
3. **Right-click `install.command`** -> Open -> Open.
   (Right-click is needed only the first time, to get past the
   "unidentified developer" warning. After that, double-click works.)
4. Read what it tells you and confirm prompts. It will:
   - Ask before installing Homebrew (only if missing)
   - Install Python 3.12 and ffmpeg via Homebrew
   - Create a venv at `~/Library/Application Support/Transcribr/`
   - Install the faster-whisper engine, plus mlx-whisper on Apple
     Silicon (macOS 13.5+), along with python-docx, reportlab, pywebview
     and bottle (a few hundred MB — no PyTorch)
   - Create `/Applications/Transcribr.app`
5. Launch from Spotlight, Launchpad, or the Applications folder.

   The reference **openai-whisper** engine isn't installed up front
   (it pulls in PyTorch, ~2 GB). Add it any time from the **Models** tab
   in the app if you want it — see [Models](#models) below.

### Windows

1. Download and unzip the latest release.
2. Open the `windows` folder.
3. **Double-click `install.bat`.**
   - If Windows SmartScreen warns, click "More info" -> "Run anyway".
   - The installer runs in a console window; PowerShell does the work.
4. Read what it tells you and confirm prompts. It will:
   - Use winget to install Python 3.12 and ffmpeg (Gyan.FFmpeg)
   - Create a venv at `%LOCALAPPDATA%\Transcribr\venv`
   - Install the faster-whisper engine plus python-docx, reportlab,
     pywebview and bottle (a few hundred MB — no PyTorch)
   - Place a Desktop shortcut and a Start Menu entry
5. Launch from your Desktop or Start Menu (search "Transcribr").

   The reference **openai-whisper** engine isn't installed up front
   (it pulls in PyTorch, ~2 GB). Add it any time from the **Models** tab
   in the app if you want it — see [Models](#models) below.

## Using the application

The window has four views, switched in the left sidebar:
**Transcribe** (choose files and options, run jobs), **Review** (label
speakers and edit a finished transcript), **Library** (recent
transcripts), and **Models** (see what's downloaded and free up space).
The defaults are sensible for most jobs; pick an input file and click
**Run Transcription**.

The app remembers your settings (engine, model, format, description,
decoding options, theme) between launches. The moon button at the
bottom of the sidebar cycles between following the system appearance,
light, and dark.

### Choosing files and output

**Drop zone.** Drag one or more audio/video files onto the dashed
panel (or anywhere on the window), or click it to browse. Dropping a
single file sets it as the Input; dropping several adds them all to
the batch queue.

**Input.** The audio or video file to transcribe. Anything ffmpeg can
read works: `.mp3`, `.wav`, `.m4a`, `.mp4`, `.mov`, `.aac`, `.flac`,
`.ogg`, `.opus`, `.webm`, etc.

**Output.** Where the transcript goes. Auto-fills to
`<input>.transcript.<ext>` next to the input file whenever you change
the input or the format. Override it if you want it somewhere else.

**Format.**

- **`.txt`** — plain text, one paragraph per block, each starting with
  a timestamp in square brackets. Easiest to edit anywhere.
- **`.docx`** — A4 Word document in a monospaced font with a hanging
  indent (timestamp in the left column, body wrapping cleanly), bold
  speaker labels, a "Page X of Y" footer, and an italic disclaimer.
- **`.pdf`** — an A4 PDF with the same layout as the Word output.
  (PDFs can't be re-opened for labelling later; use `.docx` or `.txt`
  if you'll want to revisit the speaker labels.)

**Document title.** The heading placed at the top of the transcript.
It is **not** sent to the engine — it only labels the document. Left
blank, the transcript is titled after the source file's name instead.

**Context / vocabulary hint** *(optional)*. Free text fed to the engine
as its `initial_prompt` to prime it with names and jargon it may not
know. It can help accuracy on proper nouns — but **priming is opt-in
and can backfire**: the prompt may bleed into the transcript or trigger
hallucinations, especially on unclear audio or long silences. Leave it
blank unless you need it, and when you do, keep it to **keywords rather
than sentences** — a prose description is the most likely to leak in:

> *Macklebum, Bloggs, Mount Druitt, AVO, ICAC, DVEC*

is safer than a full narrative sentence. Worth including: speaker and
referenced names, ambiguous place names, and acronyms. Keep it under
about 200 words; longer prompts get truncated. If a run comes out
garbled, try clearing this field, or turn off **Condition on previous
text** (Advanced decoding) so an early mistake can't propagate.

**Batch queue.** Add several files to transcribe them one after
another in a single unattended run. Each transcript is saved next to
its source file with no interactive review; failures are recorded and
the run carries on, with a summary at the end. Open each result from
the **Library** afterwards to label speakers. Stage a single file to
use the normal flow instead (which *does* pause for review).

### Engine and model

**Engine.** Which Whisper implementation does the work. Only engines
actually installed appear (install more from the **Models** tab):

- **faster-whisper** — CTranslate2-based; substantially faster on CPU
  with essentially identical output. Installed by default; no PyTorch.
- **mlx-whisper** — Apple-Silicon-only (macOS 13.5+), uses the Mac's
  GPU via MLX. Fastest option on M-series machines. No mid-run Stop.
  Installed by default on Apple Silicon.
- **openai-whisper** — the reference implementation. Most thoroughly
  tested, but pulls in PyTorch (~2 GB), so it's **optional**: install
  it from the **Models** tab when you want it.

**Model.** The main quality / speed trade-off. English-only models
(`.en` suffix) are slightly more accurate on English and ignore the
*Language* dropdown.

| Model | Download | Speed (relative) | Notes |
|---|---|---|---|
| `tiny.en`, `tiny` | ~75 MB | very fast | Often inaccurate; useful for quick dry runs |
| `base.en`, `base` | ~150 MB | fast | Acceptable for clear, simple speech |
| `small.en`, `small` | ~500 MB | moderate | Good balance for casual jobs |
| `medium.en`, `medium` | ~1.5 GB | slow | Recommended for legal / interview / DVEC work |
| `large-v1`, `large-v2`, `large-v3` | ~3 GB | very slow | Best raw accuracy; runtime can be painful on a CPU |
| `large-v3-turbo` | ~1.6 GB | fast (despite the size) | Faster than `large-v3` with similar accuracy. No `.en` variant |

Models download once on first use and are cached locally
(faster-whisper and mlx-whisper under `~/.cache/huggingface/`;
openai-whisper under `~/.cache/whisper/`). The **Models** tab shows
what's downloaded, how much space it uses, and lets you pre-download or
remove models — see [Models](#models) below.

**Language / Task.** Set the language explicitly if you know it
(auto-detect costs a little time and accuracy). *Translate* converts
non-English audio to English output; *Transcribe* keeps the source
language.

**Decoding options.** These usually do not need touching — the
defaults are tuned by Whisper's authors. Briefly: **Temperature** 0 =
deterministic; **Beam size / Best of** higher = better but slower;
**Compression-ratio threshold** catches hallucination loops;
**No-speech threshold** raises/lowers how readily silence is skipped;
**Condition on previous text** improves consistency but can propagate
an early mistake. **Highlight low-confidence words in review** records
per-word confidence during transcription (slightly slower) so the
review pane can shade words the engine was unsure about.

### Paragraphs and extra outputs

**Paragraph grouping.** *Pause that triggers a new paragraph* (default
1.5 s) controls how aggressively paragraphs break — lower for
rapid-fire dialogue, higher for monologues. Breaks also occur at
sentence-ending punctuation and short acknowledgments ("Yes", "Okay"),
and a 60-second cap stops run-on paragraphs. *Show timestamps in
output* prefixes each paragraph with `[MM:SS]`. *Review and label
speakers before saving* opens the review pane when transcription
finishes (untick to save straight to disk).

**Additional outputs.** Optional technical sidecar files: JSON (the
full Whisper result), SRT / VTT subtitles, and TSV.

### Library

The last ten transcripts you produced or opened, with their locations.
Click one (or its **Review** button) to re-open it for speaker
labelling and editing; **Open transcript…** browses for any other
`.docx`/`.txt` transcript.

### Models

Model weights are large (75 MB for `tiny` up to ~3 GB for `large-v3`)
and each engine keeps its **own** copy in its own cache, so the same
model can occupy disk two or three times over. Engine cards are
collapsible and summarise how many models are installed of the total,
plus the space used. The **Models** view lists every model grouped by
engine and lets you:

- **Download** a model ahead of time, so the first real run doesn't
  stall on a multi-gigabyte fetch. A progress bar with size and speed
  shows how it's going, and **Cancel** aborts it.
- **Uninstall** a model you no longer need to reclaim the space (it
  re-downloads automatically the next time you use it).
- **Download a newer model** that isn't in the built-in list — for the
  faster-whisper and mlx-whisper engines, type a model name or a full
  Hugging Face repo (e.g. `mlx-community/whisper-large-v3-turbo`) into
  the engine's download box. (openai-whisper only offers its fixed
  catalogue.)
- **Install an optional engine.** The reference **openai-whisper**
  engine isn't installed by default (it downloads PyTorch, ~2 GB). The
  **Add an engine** panel installs it on demand; once it's done it
  appears in the Transcribe **Engine** dropdown. You can **Remove
  engine** later to reclaim the space.

The `large` and `turbo` aliases (identical weights to `large-v3` and
`large-v3-turbo`) aren't listed separately — only the canonical names
appear. Downloading, installing and removing are paused while a
transcription is running, and a transcription won't start while one of
those is in progress. The
cache locations are shown at the bottom of the view.

### Run / Stop and progress

**Run Transcription** starts the job. The progress card shows the
file name, a progress bar with percentage, time remaining, and speed.
**Show details** expands the raw engine output underneath (it expands
automatically if something goes wrong). Model loading can take 30+
seconds on first launch — that's normal.

**Stop** saves whatever has been transcribed so far as a partial
transcript. It takes effect at the next chunk boundary (not supported
mid-run by mlx-whisper).

### The review workspace

After a transcription (or when opening an existing `.docx`/`.txt`
transcript), the Review view shows the paragraphs in three columns —
speaker, timestamp, text — with up to nine colour-coded speakers named
in the panel on the right.

| Action | How |
|---|---|
| Select a paragraph | Click it, or Up/Down arrows |
| Assign a speaker | Press `1`–`9` (auto-advances to the next paragraph) |
| Clear a speaker | Press `0` |
| Jump to the next unlabelled paragraph | Press `N` |
| Play that paragraph's audio | Press `P`, or the ▶ Play button |
| Merge with the previous paragraph | Press `M` |
| Split a paragraph | Double-click the word to split before |
| Edit the text | Enter (or F2), then Enter to commit / Esc to cancel |
| Undo / redo | Ctrl+Z / Ctrl+Shift+Z (Cmd on Mac), or the buttons |
| Find / replace | Ctrl+F (Cmd+F), then *Find next* / *Replace all* |

Speaker names typed into the name fields appear in the saved document
in place of the numbers. The header shows how many paragraphs are
labelled. If word-confidence highlighting was enabled for the run,
uncertain words are shaded red/amber so you know where to listen.

While you review, the work is auto-saved every few seconds; if the app
crashes or is force-quit, the next launch offers to restore the
session exactly where you left off. A safety copy of the un-labelled
transcript is also written to disk before review begins.

## Running the tests

```bash
python3 -m unittest discover -s tests -v
```

or double-click `tests/run_tests.command` on a Mac (uses the app's
venv, which has the optional dependencies). Tests that need an
optional package or a display skip themselves; the suite never touches
your real settings.

## Re-running the installer

Both installers are safe to re-run. They will:
- Skip dependencies that are already installed
- Offer to recreate the venv from scratch (say no for a quick refresh,
  yes if something is genuinely broken)
- Always rebuild the launcher / .app bundle / shortcuts

## Where things go

### macOS

| What | Where |
|---|---|
| Python virtualenv | `~/Library/Application Support/Transcribr/venv/` |
| The GUI script | `~/Library/Application Support/Transcribr/transcribr.py` |
| The web interface (pre-built) | `~/Library/Application Support/Transcribr/webdist/` |
| The application | `/Applications/Transcribr.app` |
| Settings / recent / autosave | `~/Library/Application Support/Transcribr/*.json` |
| Launch logs | `~/Library/Logs/Transcribr/launch.log` |
| Whisper model cache | `~/.cache/whisper/` |

### Windows

| What | Where |
|---|---|
| Python virtualenv | `%LOCALAPPDATA%\Transcribr\venv\` |
| The GUI script | `%LOCALAPPDATA%\Transcribr\transcribr.py` |
| The web interface (pre-built) | `%LOCALAPPDATA%\Transcribr\webdist\` |
| Desktop shortcut | `%USERPROFILE%\Desktop\Transcribr.lnk` |
| Start Menu shortcut | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Transcribr.lnk` |
| Settings / recent / autosave | `%APPDATA%\Transcribr\*.json` |
| Launch logs | `%LOCALAPPDATA%\Transcribr\launch.log` |
| Whisper model cache | `%USERPROFILE%\.cache\whisper\` |

## Uninstalling

### macOS

```bash
rm -rf "/Applications/Transcribr.app"
rm -rf "$HOME/Library/Application Support/Transcribr"
rm -rf "$HOME/Library/Logs/Transcribr"
rm -rf "$HOME/.cache/whisper"      # optional: frees the model cache
```

### Windows

In PowerShell:

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Transcribr"
Remove-Item -Force "$env:USERPROFILE\Desktop\Transcribr.lnk"
Remove-Item -Force "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Transcribr.lnk"
Remove-Item -Recurse -Force "$env:USERPROFILE\.cache\whisper"  # optional
```

## Troubleshooting

If the app does not launch:

1. **Check the log** at the path shown in the table above. The last
   few lines usually show the cause.
2. **Re-run the installer.** Eight times out of ten, this fixes it.
3. **Test the GUI directly from Terminal / PowerShell** to see live
   errors:

   macOS:
   ```bash
   source "$HOME/Library/Application Support/Transcribr/venv/bin/activate"
   python "$HOME/Library/Application Support/Transcribr/transcribr.py"
   ```

   Windows (PowerShell):
   ```powershell
   & "$env:LOCALAPPDATA\Transcribr\venv\Scripts\python.exe" `
     "$env:LOCALAPPDATA\Transcribr\transcribr.py"
   ```

4. **If the new (web) interface's window won't open**, run it without
   a window and use your browser instead — from the same venv python:

   ```bash
   python transcribr.py --serve
   ```

   then open the printed `http://127.0.0.1:…` URL. That URL only works
   on your own machine.

## Development

The web interface's source lives in `web/` (React + TypeScript,
built with Vite). Node.js (≥ 20) is needed **only for development** —
end users receive the pre-built files in `webdist/`, which are
committed to the repository and must be rebuilt and committed after
changing anything in `web/`:

```bash
cd web
npm install
npm run build     # type-checks, then writes ../webdist/
```

For live-reload development: `python3 transcribr.py --serve` in one
terminal (port 8737, token `dev`) and `npm run dev` in another, then
open the Vite URL. If the repository lives in Dropbox, mark
`web/node_modules` as ignored so it doesn't sync:
`xattr -w com.dropbox.ignored 1 web/node_modules` (macOS).

## What this does NOT install

The Whisper model weights themselves. The first time you run a
particular model from the GUI, the engine downloads it (~150 MB for
small, ~1.5 GB for medium, ~3 GB for large-v3) and caches it locally.
Subsequent runs use the cached version. You can also pre-download or
remove models from the **Models** tab.

The reference **openai-whisper** engine (and its ~2 GB PyTorch
dependency) is also not installed by default — add it from the
**Models** tab if you want it.
