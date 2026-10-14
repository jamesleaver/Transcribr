"""THIRD-PARTY-NOTICES.txt for what a build actually ships.

    <bundled python> -m transcribr.notices THIRD-PARTY-NOTICES.txt

The build runs this with the interpreter being shipped, after its
packages are installed: it reads every installed distribution's own metadata and
licence files, so the notices can never drift from the bundle. Then it
adds what pip does not know about - the FFmpeg libraries inside PyAV,
the web interface's JavaScript, and the models the app downloads at
first use (fetched by the user's machine, not shipped, but credited).

Discharges the attribution duties of the permissive licences and the
LGPL's notice duty for FFmpeg.
"""

import importlib.metadata as md
import io
import sys
from pathlib import Path

NOT_SHIPPED = {"pip", "setuptools", "wheel"}

FFMPEG_GPL = """\
FFmpeg (inside the "av" package)
  Licence: GNU General Public License, version 2 or later - this build
  of FFmpeg includes GPL components (the x264/x265 video encoders).
  Source: https://ffmpeg.org/releases/ and https://github.com/PyAV-Org/PyAV.
"""

FFMPEG = """\
FFmpeg (inside the "av" package)
  Licence: GNU Lesser General Public License, version 3 or later
  (FFmpeg is configured with --enable-version3). Built by Transcribr's
  own scripts (macos/build-ffmpeg.sh; windows/build-av-wheel.sh on
  Windows) with every GPL component disabled; the configure summary is
  checked to read "LGPL". LAME (MP3 decoding and encoding) is included
  under the GNU Lesser General Public License, version 2.1 or later.
  The libraries are dynamically linked and sit as separate files inside
  the application, so they may be replaced with modified versions.
  Source: https://ffmpeg.org/releases/ (the version is in
  macos/build-ffmpeg.sh) and https://lame.sourceforge.io/.
"""

WEB = """\
The window's interface (webdist/) is built from:
  React and React DOM - MIT Licence - Copyright (c) Meta Platforms, Inc.
    and affiliates.
  Zustand - MIT Licence - Copyright (c) 2019 Paul Henschel.
"""

MODELS = """\
Models downloaded on first use (fetched by your computer from their
publishers; not included in this download):
  Whisper speech recognition - OpenAI - MIT Licence.
  faster-whisper conversions of Whisper - SYSTRAN, and Mobius Labs
    (large-v3-turbo) - MIT Licence.
  mlx-whisper conversions - the MLX Community - MIT Licence.
  Parakeet TDT 0.6B v2 speech recognition - NVIDIA - CC BY 4.0, run
    through sherpa-onnx's conversion (k2-fsa).
  Speaker detection, through sherpa-onnx (k2-fsa, Apache 2.0):
    pyannote segmentation-3.0 - pyannote - MIT Licence;
    TitaNet-S speaker embeddings - NVIDIA - CC BY 4.0;
    WeSpeaker ResNet34 speaker embeddings - WeSpeaker - CC BY 4.0.
    The speaker-embedding models were trained on VoxCeleb, a research
    dataset built from public videos.
"""


def ffmpeg_is_gpl():
    """True when the bundled FFmpeg carries GPL encoders (the stock
    PyAV wheel); False for Transcribr's own LGPL build."""
    try:
        import av
        return bool({"libx264", "libx265"} & set(av.codecs_available))
    except Exception:
        return False


def licence_of(dist):
    m = dist.metadata
    if m.get("License-Expression"):
        return m["License-Expression"]
    lic = (m.get("License") or "").strip()
    if lic and len(lic) < 100 and "\n" not in lic:
        return lic
    cls = [c.split("::")[-1].strip() for c in m.get_all("Classifier") or []
           if c.startswith("License ::")]
    return "; ".join(cls) or (lic.splitlines()[0] if lic else "see below")


def licence_texts(dist):
    out = []
    for f in dist.files or []:
        name = f.name.upper()
        if any(name.startswith(p) for p in ("LICENSE", "LICENCE", "COPYING",
                                            "NOTICE", "AUTHORS")) or \
                "/licenses/" in str(f).lower():
            try:
                text = Path(dist.locate_file(f)).read_text(
                    encoding="utf-8", errors="replace")
            except OSError:
                continue
            if text and text.strip() and text not in out:
                out.append(text.strip())
    return out


def bundled_notices_path():
    """Where the build puts the notices: beside the package."""
    return Path(__file__).resolve().parent.parent / "THIRD-PARTY-NOTICES.txt"


def notices_text():
    buf = io.StringIO()
    write(buf)
    return buf.getvalue()


def main(argv=None):
    """With a path, write the notices there as UTF-8. Without one, write
    them to stdout - which is only safe where the console encoding can
    take them; on Windows it cannot (the licence texts carry typographic
    quotes and accents), which is why the build passes a path."""
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        with open(argv[0], "w", encoding="utf-8", newline="\n") as fh:
            write(fh)
        return
    out = sys.stdout
    try:
        out.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    write(out)


def write(out):
    dists = sorted({d.metadata["Name"]: d for d in md.distributions()
                    if d.metadata["Name"]
                    and d.metadata["Name"].lower() not in NOT_SHIPPED}.items(),
                   key=lambda kv: kv[0].lower())
    w = out.write
    w("THIRD-PARTY NOTICES\n\nTranscribr includes the software listed "
      "below, each under its own licence.\n\n")
    w("Python " + sys.version.split()[0] + " - Python Software Foundation "
      "License. Built by python-build-standalone, whose bundled libraries' "
      "licences are included with the runtime.\n\n")
    w((FFMPEG_GPL if ffmpeg_is_gpl() else FFMPEG) + "\n" + WEB + "\n"
      + MODELS + "\n")
    w("=" * 72 + "\nPython packages\n" + "=" * 72 + "\n\n")
    for name, d in dists:
        home = d.metadata.get("Home-page") or next(
            (u.split(",", 1)[-1].strip() for u in
             d.metadata.get_all("Project-URL") or []), "")
        w(f"{name} {d.version} - {licence_of(d)}\n")
        if home:
            w(f"  {home}\n")
    missing, carrier = [], {}
    for name, d in dists:
        texts = licence_texts(d)
        if not texts:
            missing.append((name, d))
            continue
        for t in texts:
            if "Apache License" in t and "Version 2.0" in t:
                carrier.setdefault("apache", name)
            if "Permission is hereby granted, free of charge" in t:
                carrier.setdefault("mit", name)
        w("\n" + "=" * 72 + f"\n{name} {d.version}\n" + "=" * 72 + "\n\n")
        w("\n\n".join(texts) + "\n")
    if missing:
        # Packages that ship no licence file of their own: point to the
        # standard text, which another package above already carries.
        w("\n" + "=" * 72 + "\nPackages without their own licence file\n"
          + "=" * 72 + "\n\n")
        for name, d in missing:
            lic = licence_of(d)
            low = lic.lower()
            if "apache" in low and "apache" in carrier:
                where = (f"the Apache License 2.0; its full text appears above"
                         f" under {carrier['apache']}")
            elif "mit" in low.split() or low.startswith("mit") and \
                    "mit" in carrier:
                where = (f"the MIT Licence; its text appears above under "
                         f"{carrier.get('mit', 'the packages marked MIT')}, "
                         f"with the copyright held by the {name} authors")
            else:
                where = lic
            w(f"{name} {d.version} - {where}.\n")


if __name__ == "__main__":
    main()
