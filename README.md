# Transcribr

(c) James Leaver, 2026.

An experimental GUI for transcribing audio and video files on macOS and Windows. It uses OpenAI's Whisper to do the transcription, then a separate script groups the result into likely paragraphs ready for speaker assignment and writes a .txt file. Everything runs locally on your computer — no audio, video, or transcripts are uploaded to the internet.

When a particular model is run for the first time, that model will be
downloaded to your computer and stored locally. The `medium.en` or
`large-v3-turbo` models are recommended.

Use at your own risk.

Questions: [jleaver@sgchambers.com.au](mailto:jleaver@sgchambers.com.au)

## What this folder contains

```
Transcribr-Installer/
├── INSTALL.txt              ← Quick-start instructions
├── README.md                ← This file
├── transcribr.py   ← The cross-platform GUI itself
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

You do **not** need Python, ffmpeg, or Whisper pre-installed.
The installer handles all of that.

## How to install

### macOS

1. Copy this folder to the target Mac.
2. Open the `macos` folder.
3. **Right-click `install.command`** -> Open -> Open.
   (Right-click is needed only the first time, to get past the
   "unidentified developer" warning. After that, double-click works.)
4. Read what it tells you and confirm prompts. It will:
   - Ask before installing Homebrew (only if missing)
   - Install Python 3.12, ffmpeg, and python-tk@3.12 via Homebrew
   - Create a venv at `~/Library/Application Support/Transcribr/`
   - Install openai-whisper into that venv (downloads ~1.5 GB)
   - Create `/Applications/Transcribr.app`
5. Launch from Spotlight, Launchpad, or the Applications folder.

### Windows

1. Copy this folder to the target PC.
2. Open the `windows` folder.
3. **Double-click `install.bat`.**
   - If Windows SmartScreen warns, click "More info" -> "Run anyway".
   - The installer runs in a console window; PowerShell does the work.
4. Read what it tells you and confirm prompts. It will:
   - Use winget to install Python 3.12 and ffmpeg (Gyan.FFmpeg)
   - Create a venv at `%LOCALAPPDATA%\Transcribr\venv`
   - Install openai-whisper into that venv (downloads ~2 GB)
   - Place a Desktop shortcut and a Start Menu entry
5. Launch from your Desktop or Start Menu (search "whisper").

## Using the application

When you launch the app you get a single window with all of the
options laid out in groups. The defaults are sensible for most jobs;
you can change settings as needed and click **Run Transcription**.

### File

**Input.** The audio or video file to transcribe. Click *Browse...*
and pick it, or paste / type a path. Anything ffmpeg can read works:
`.mp3`, `.wav`, `.m4a`, `.mp4`, `.mov`, `.aac`, `.flac`, `.ogg`,
`.opus`, `.webm`, etc. You can also drag a file onto the window if
your install supports it.

**Output.** Where the paragraph-grouped output file goes. Auto-fills
to `<input>.transcript.<ext>` next to the input file whenever you
change the input or the format. Override it if you want it somewhere
else.

**Format.** Choose how the main output is delivered:

- **`.txt`** — plain text with one paragraph per line block, each
  starting with a timestamp in square brackets. Easiest to edit in
  any text editor; perfect when you want to paste excerpts somewhere
  else.
- **`.docx`** — Microsoft Word format in a monospaced font, with a
  hanging indent so the timestamp sits in the left margin and the
  body text wraps further right. This makes it easy to type a speaker
  name before each timestamp and press Tab to align cleanly. Includes
  a "Page X of Y" footer and an italic disclaimer at the end of the
  document.

### Model

This is the most important choice and the main quality / speed
trade-off. The English-only models (`.en` suffix) are slightly more
accurate on English than their multilingual counterparts of the same
size and ignore the *Language* dropdown.

| Model | Download | Speed (relative) | Notes |
|---|---|---|---|
| `tiny.en`, `tiny` | ~75 MB | very fast | Often inaccurate; useful for quick dry runs |
| `base.en`, `base` | ~150 MB | fast | Acceptable for clear, simple speech |
| `small.en`, `small` | ~500 MB | moderate | Good balance for casual jobs |
| `medium.en`, `medium` | ~1.5 GB | slow | The default. Recommended for legal / interview / DVEC work |
| `large-v1`, `large-v2`, `large-v3`, `large` | ~3 GB | very slow | Best raw accuracy; runtime can be painful on a CPU |
| `turbo`, `large-v3-turbo` | ~1.6 GB | fast (despite the size) | Faster than `large-v3` with similar accuracy. No `.en` variant |

Models download once on first use and are cached at
`~/.cache/whisper/` (Mac) or `%USERPROFILE%\.cache\whisper\`
(Windows). Subsequent runs use the cached version.

For DVEC-style recordings — Australian accents, distressed speech,
proper nouns that matter — `medium.en` or `large-v3-turbo` are the
two to pick from. Try both on a representative file once and use
whichever you prefer.

### Language

Tells Whisper what language the audio is in. *Auto-detect* works but
costs a small amount of time and accuracy. If you know the language,
set it explicitly. The `.en` models always treat input as English
regardless of this setting.

### Task

- **Transcribe** (default): output is in the source language.
- **Translate**: output is translated to English. Only useful for
  non-English audio. Ignored by `.en` models.

### Initial prompt

A free-text box that primes the model with context before
transcription begins. This is one of the highest-leverage settings
for accuracy on names and jargon.

Whisper does not "know" the names of the people in your recording.
If you want it to spell *Joannah Bloggs* correctly rather than
guessing *Jo-Anna Blaggs / Joanne Abloggs / Goanna Frogs*, tell it
ahead of time:

> *DVEC interview between Constable Macklebum and Joannah Bloggs at
> Mount Druitt Police Station.*

Worth including:
- Names of speakers and people referred to
- Place names that might be ambiguous
- Acronyms or jargon (DVEC, AVO, ICAC, etc.)
- A short note on the format ("interview", "phone call", "court
  hearing")

Keep it under about 200 words; longer prompts get truncated.

### Paragraph grouping

**Pause that triggers a new paragraph** (default 1.5 seconds). The
script breaks paragraphs whenever it sees a pause of at least this
length, when a question mark ends one segment, or when one side
gives a short acknowledgment ("Yes", "Yeah", "Okay").

- **Lower** (e.g. 1.0): more breaks. Useful for rapid-fire dialogue
  where speakers cut each other off.
- **Higher** (e.g. 2.5): fewer breaks. Useful for monologue-heavy
  audio where one person speaks at length.

Tweak after looking at a result; you do not need to get it right
first time. The text itself never changes — only paragraph breaks
and timestamps move around.

**Show timestamps in output** (default ON). When ticked, each
paragraph in the output starts with a `[MM:SS]` timestamp. Untick to
get plain prose with no timestamps; useful when you want to share
the transcript without time markers.

### Advanced

These usually do not need touching. Defaults are tuned by Whisper's
authors for typical use. The settings worth knowing:

**Temperature** (default 0.0). 0 means deterministic decoding; the
same audio produces the same transcript every time. Higher values
let the model take more risks. Whisper internally bumps this up if
it suspects the output is garbage, so leaving it at 0 is normally
correct.

**Beam size** and **Best of** (default 5 each). Higher = better
quality, slower. The defaults are a good balance. Setting them
higher for very important short clips can occasionally help.

**Compression-ratio threshold** (default 2.4). Whisper rejects
output that is suspiciously repetitive (a sign of a hallucination
loop). Raise this if you legitimately have repeating content being
flagged; lower it if hallucinations are slipping through.

**Logprob threshold** (default -1.0). Rejects very low-confidence
output. Default is fine.

**No-speech threshold** (default 0.6). How aggressively Whisper
treats silent passages as "no speech". Raise it (e.g. 0.7) if
Whisper is hallucinating words during silent stretches; lower it if
real speech is being skipped.

**Condition on previous text** (default ON). Each segment uses the
previous transcript as context, which improves consistency. Turn
off if Whisper gets stuck in a repetition loop or copies an early
mistake forward through the rest of the file.

**Word-level timestamps** (default OFF). Records a timestamp per
word rather than per segment. Slows transcription and is not used by
the paragraph script, so leave off unless you want the JSON for
something else.

### Run / Stop

**Run Transcription** kicks off the job. Progress streams into the
*Progress* pane: model loading, then segment-by-segment transcripts
as they complete. Do not be alarmed by long pauses early on — model
loading can take 30+ seconds, especially on first launch.

**Stop** appears beside Run while a job is in progress. Clicking it
saves whatever has been transcribed up to that point as a partial
transcript, with the same paragraph grouping. Stop takes effect at
the next chunk boundary, usually within a few seconds. Whisper does
not support a true pause; only stop-and-save.

### After transcription

When the run finishes, three buttons become available at the bottom:

- **Open Output** opens the `.txt` file in your default text editor.
- **Show in Folder** (called "Reveal in Finder" on Mac, "Show in
  Explorer" on Windows) opens the containing folder with the file
  selected.
- **About** shows version, copyright, and contact info.

Reading the result, you may notice the occasional paragraph that
mixes two speakers or an answer that did not get its own break.
That is expected — the paragraph script's job is to give you a
useful starting point, not a perfect speaker-attributed transcript.
A quick read-through with the audio open is the second pass; the
script saves the bulk of the manual labour.

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
| The application | `/Applications/Transcribr.app` |
| Launch logs | `~/Library/Logs/Transcribr/launch.log` |
| Whisper model cache | `~/.cache/whisper/` |

### Windows

| What | Where |
|---|---|
| Python virtualenv | `%LOCALAPPDATA%\Transcribr\venv\` |
| The GUI script | `%LOCALAPPDATA%\Transcribr\transcribr.py` |
| Desktop shortcut | `%USERPROFILE%\Desktop\Transcribr.lnk` |
| Start Menu shortcut | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Transcribr.lnk` |
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

## What this does NOT install

The Whisper model weights themselves. The first time you run a
particular model from the GUI, Whisper downloads it (~150 MB for
small.en, ~1.5 GB for medium.en, ~3 GB for large-v3) and caches it
locally. Subsequent runs use the cached version.
