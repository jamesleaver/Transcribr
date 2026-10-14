"""THIRD-PARTY-NOTICES: generated from what is actually installed."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness  # noqa: F401

from transcribr import notices as N


class TestNotices(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.text = N.notices_text()

    def test_every_installed_package_is_listed(self):
        import importlib.metadata as md
        for d in md.distributions():
            name = d.metadata["Name"]
            if name and name.lower() not in N.NOT_SHIPPED:
                self.assertIn(f"\n{name} {d.version} - ", self.text, name)

    def test_licence_texts_are_included(self):
        self.assertIn("Permission is hereby granted", self.text)

    def test_ffmpeg_licence_matches_the_build(self):
        """The stock PyAV wheel carries GPL encoders, the LGPL build does
        not; FFmpeg's own entry must say which was shipped. Looked for in
        that entry alone: other packages' licence texts name these
        licences too."""
        entry = self.text.split('FFmpeg (inside the "av" package)')[1]
        entry = entry.split("\n\n")[0]
        if N.ffmpeg_is_gpl():
            self.assertIn("GNU General Public License, version 2", entry)
            self.assertNotIn("Lesser", entry)
        else:
            self.assertIn("GNU Lesser General Public License", entry)

    def test_written_to_a_path_as_utf8(self):
        """The build passes a path: piping through the console fails on
        Windows, where the default encoding cannot take the typographic
        characters in some licence texts."""
        import tempfile
        from pathlib import Path as _P
        with tempfile.TemporaryDirectory() as td:
            out = _P(td) / "NOTICES.txt"
            N.main([str(out)])
            raw = out.read_bytes()
        self.assertEqual(raw.decode("utf-8"), self.text)
        self.assertFalse(all(b < 128 for b in raw),
                         "the licence texts are not all ASCII")

    def test_downloaded_models_are_credited(self):
        for who in ("OpenAI", "NVIDIA", "WeSpeaker", "pyannote"):
            self.assertIn(who, self.text)


if __name__ == "__main__":
    unittest.main()
