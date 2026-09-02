# Releasing Transcribr

How a release is cut, signed and published. Two audiences: the
**one-time setup** (things James does once, mostly buying and
installing certificates) and the **per-release steps** (things the
build scripts do).

---

## Part 1 — One-time setup (macOS signing)

This is the part that needs a human, a credit card and a keyboard.
Budget an hour, plus however long Apple takes to approve the account.

### 1. Enrol in the Apple Developer Program — $99/year

<https://developer.apple.com/programs/enroll/>

- Enrol as an **Individual** unless there's a reason to enrol the
  chambers as an organisation. Organisation enrolment needs a D-U-N-S
  number and takes noticeably longer.
- Individual enrolment is often approved same-day, but can take a few
  days. Nothing below works until it is.
- Note your **Team ID** once you're in: <https://developer.apple.com/account>
  → Membership details. It's a 10-character string like `A1B2C3D4E5`.

### 2. Install the Xcode Command Line Tools

```bash
xcode-select --install
```

This provides `codesign`, `pkgbuild`, `productbuild`, `notarytool` and
`stapler`. The full Xcode app is not required.

### 3. Create two certificates

Transcribr needs **both** — they are different certificates and signing
fails confusingly if you only make one:

| Certificate | Signs |
|---|---|
| **Developer ID Application** | `Transcribr.app` and every binary inside it |
| **Developer ID Installer** | the `.pkg` itself |

Easiest route is Xcode: **Xcode → Settings → Accounts →** add your Apple
ID **→ Manage Certificates → + →** create each one. They land in your
login keychain automatically.

Without Xcode: <https://developer.apple.com/account/resources/certificates>
→ **+**, choose each type, and upload a CSR generated from **Keychain
Access → Certificate Assistant → Request a Certificate From a Certificate
Authority** (choose "Saved to disk"). Download each `.cer` and
double-click to install.

Verify both are present:

```bash
security find-identity -v -p codesigning | grep "Developer ID"
```

You should see two lines — one `Developer ID Application: ...`, one
`Developer ID Installer: ...` — each ending with your Team ID in
parentheses. **Back these up** (Keychain Access → right-click → Export)
somewhere safe; replacing a lost Developer ID certificate is tedious.

### 4. Create an app-specific password for notarisation

Apple's notary service won't take your ordinary Apple ID password.

1. <https://appleid.apple.com> → **Sign-In and Security → App-Specific
   Passwords → +**
2. Call it something like `transcribr-notary`.
3. Copy the generated password (format `abcd-efgh-ijkl-mnop`).

Then store it in the keychain so the build script never sees it:

```bash
xcrun notarytool store-credentials "transcribr-notary" \
    --apple-id "you@example.com" \
    --team-id "YOURTEAMID" \
    --password "abcd-efgh-ijkl-mnop"
```

The build script refers to the profile by name (`transcribr-notary`)
from then on.

### 5. Tell the build script who you are

Set these once in your shell profile (`~/.zshrc`), or pass them each
time:

```bash
export TRANSCRIBR_TEAM_ID="YOURTEAMID"
export TRANSCRIBR_NOTARY_PROFILE="transcribr-notary"
```

The signing identities are found automatically from the Team ID.

---

## Part 2 — What goes in the package

The macOS `.pkg` is **self-contained**: it carries its own Python
runtime and the default engine, so installing needs no Homebrew, no
python.org download, no administrator password beyond the installer's
own, and no internet connection.

What is bundled:

- CPython 3.12 (relocatable, from python-build-standalone)
- **faster-whisper** (CTranslate2) — the default engine
- PyAV (bundles FFmpeg's libraries), python-docx, reportlab, pywebview,
  bottle, and the built web interface

What is **not** bundled, and why:

| Left out | Size it would add | How users get it |
|---|---|---|
| `mlx-whisper` (Apple Silicon GPU engine) | **~1.1 GB** — it hard-requires PyTorch, plus numba, llvmlite and scipy | Models tab, one click |
| `openai-whisper` (reference engine) | ~900 MB — PyTorch again | Models tab, one click |
| Whisper model weights | 150 MB – 3 GB each | Downloaded on first use, as now |

Bundling mlx-whisper would take the download from roughly 330 MB to
roughly 1.4 GB. Since it is one click away in the app, it stays out.

Optional engines install into a **per-user virtual environment** under
`~/Library/Application Support/Transcribr/`, created on first launch
from the bundled runtime (`--system-site-packages`, so everything in
the bundle stays importable). The app bundle in `/Applications` stays
read-only and signed; nothing ever writes into it, which is what keeps
the signature valid. Verified: `sys.executable` inside the running app
is the user's venv, so the Models tab's pip installs land there.

Measured sizes:

| | Staged | Shipped |
|---|---|---|
| macOS `.pkg` (arm64) | 335 MB | **108 MB** |
| Windows `.exe` | 371 MB | **82 MB** |

---

## Part 3 — Cutting a release

1. Bump `__version__` in `transcribr.py` and the version line in
   `README.md`.
2. Rebuild the web interface if it changed:
   ```bash
   cd web && npm ci && npm run build
   ```
3. Run the tests:
   ```bash
   python3 -m unittest discover -s tests
   ```
4. Build and sign the macOS package:
   ```bash
   ./macos/build-pkg.sh
   ```
   This downloads a relocatable CPython, installs the engine, assembles
   the app, signs every binary inside it (169 of them in the current
   build), builds the `.pkg`, signs that, uploads it for notarisation,
   waits for Apple, and staples the ticket. Notarisation usually takes
   2–15 minutes.

   Useful variants:

   ```bash
   ./macos/build-pkg.sh --adhoc         # no certificates; test the build
   ./macos/build-pkg.sh --no-notarize   # sign properly, skip Apple
   ./macos/build-pkg.sh --arch x86_64   # Intel Macs (default is arm64)
   ```

   The default build is **arm64 only**. Run it twice, once per arch, if
   Intel Macs need supporting.
5. Build the Windows installer (see Part 4).
6. Commit, tag and publish. Attach both installers; GitHub generates
   the source archive for the tag by itself, and that is what
   `macos/bootstrap.sh` falls back to on an Intel Mac, so no extra
   asset is needed.

   ```bash
   git tag v0.9.14 && git push origin main --tags
   gh release create v0.9.14 dist/*.pkg dist/*.exe \
       --title "Transcribr v0.9.14" --generate-notes
   ```

Publishing the release is what makes the in-app updater offer it to
existing users, so publish last.

The updater picks the asset that suits the machine it is running on:
`.pkg` on macOS, `.exe` on Windows, and the source archive if neither is
present (which is how releases before 0.9.14 still work). A `.pkg` or
`.exe` opens directly; the source archive is unpacked and its platform
installer script is run.

### Verifying a signed package

```bash
spctl --assess --type install -vv dist/Transcribr-0.9.14-arm64.pkg
pkgutil --check-signature dist/Transcribr-0.9.14-arm64.pkg
stapler validate dist/Transcribr-0.9.14-arm64.pkg
```

`spctl` should say **accepted** with source *Notarized Developer ID*.
The real test is downloading it in a browser on another Mac and
double-clicking: no warning at all is the goal.

---

## Part 4 — Windows

The Windows installer is currently **unsigned**. It works, but
SmartScreen will show a "Windows protected your PC" warning; users
click **More info → Run anyway**. Removing that warning needs a code
signing certificate, which is a separate purchase from Apple's $99:

| Option | Rough cost | Notes |
|---|---|---|
| OV certificate | $200–400/yr | Since June 2023 the key must live on a hardware token or cloud HSM, so signing is a manual step. SmartScreen still warns until download reputation accumulates. |
| EV certificate | $400–700/yr | Instant SmartScreen reputation. Also token-based. |
| Azure Trusted Signing | ~$10/month | Much cheaper and automatable, but has business-eligibility requirements worth checking before committing. |

Nothing in the build pipeline needs to change when a certificate is
eventually added — it is one extra signing step.

### Building it

Building a Windows installer needs Windows, so it runs on GitHub
Actions rather than locally:

```bash
gh workflow run build-installers.yml --ref main
gh run watch
```

Pushing a `v*` tag runs it automatically and attaches the `.exe` to
that release. Either way the installer is downloadable from the run's
Artifacts section.

The Windows installer is **per-user**: it installs into
`%LOCALAPPDATA%\Programs\Transcribr`, so there is no UAC prompt and no
administrator password, and the tree stays writable for the Models tab.

The macOS `.pkg` is deliberately not built in CI — notarising it needs
the Developer ID certificates, which belong in your keychain rather
than in repository secrets.

---

## Troubleshooting

**`errSecInternalComponent` when signing.** The keychain is locked, or
the script is running over SSH without keychain access. Unlock it:
`security unlock-keychain login.keychain`.

**Notarisation rejected.** Read the log — it names the exact file:

```bash
xcrun notarytool log <submission-id> --keychain-profile "transcribr-notary"
```

Almost always one of: a binary missing the hardened runtime, a binary
signed without a secure timestamp, or an unsigned `.so` that the signing
sweep missed.

**"Transcribr is damaged and can't be opened."** The signature does not
match the contents — something modified the bundle after signing. Never
edit files inside `/Applications/Transcribr.app`; rebuild instead.

**The app launches but the engine is missing.** The per-user environment
under `~/Library/Application Support/Transcribr/` did not get built on
first launch. Check `~/Library/Logs/Transcribr/launch.log`.
