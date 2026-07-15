"""Automated test suite for Transcribr.

Run from the project root with:

    python3 -m unittest discover -s tests -v

or on macOS double-click tests/run_tests.command (uses the app's venv,
which has the optional dependencies installed).

Uses only the standard library (unittest). Tests that need an optional
package (python-docx, reportlab) or a working Tk display skip themselves
when it isn't available.

All config-directory access (settings.json, recent.json, autosave.json)
is redirected to a temporary directory for the duration of the run, so
the suite never touches the real user configuration.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import transcribr as T
except ImportError as e:  # e.g. no tkinter in this interpreter
    raise unittest.SkipTest(
        f"cannot import transcribr in this interpreter: {e}")

_real_config_dir = T._config_dir
_tmp_config = None


def setUpModule():
    """Redirect the config dir to a throwaway temp directory."""
    global _tmp_config
    _tmp_config = tempfile.TemporaryDirectory(prefix="transcribr-tests-")
    T._config_dir = lambda: Path(_tmp_config.name)


def tearDownModule():
    T._config_dir = _real_config_dir
    if _tmp_config is not None:
        _tmp_config.cleanup()


# =====================================================================
# Pure-logic helpers
# =====================================================================

class TestFormatTimestamp(unittest.TestCase):
    def test_minutes_seconds(self):
        self.assertEqual(T.format_timestamp(0), "[00:00]")
        self.assertEqual(T.format_timestamp(75.9), "[01:15]")

    def test_hours(self):
        self.assertEqual(T.format_timestamp(3600), "[1:00:00]")
        self.assertEqual(T.format_timestamp(3725), "[1:02:05]")


class TestParagraphify(unittest.TestCase):
    # Texts chosen to avoid every break trigger except the one under
    # test: no sentence-ending punctuation, not short responses.
    A = "and then we walked along"
    B = "toward the river for a while"

    def test_no_break_when_continuous(self):
        segs = [(0.0, 1.0, self.A), (1.2, 2.0, self.B)]
        self.assertEqual(len(T.paragraphify(segs, 1.5)), 1)

    def test_gap_breaks(self):
        segs = [(0.0, 1.0, self.A), (5.0, 6.0, self.B)]
        self.assertEqual(len(T.paragraphify(segs, 1.5)), 2)

    def test_sentence_end_breaks(self):
        segs = [(0.0, 1.0, "It was finished."), (1.1, 2.0, self.B)]
        self.assertEqual(len(T.paragraphify(segs, 1.5)), 2)

    def test_short_response_breaks(self):
        segs = [(0.0, 1.0, self.A), (1.1, 1.5, "Yes"), (1.6, 2.5, self.B)]
        paras = T.paragraphify(segs, 5.0)
        self.assertEqual(len(paras), 3)

    def test_sixty_second_cap(self):
        # 13 segments, 10s apart, no other break trigger; the cap should
        # split them into three paragraphs (0-50s, 60-110s, 120s).
        segs = [(i * 10.0, i * 10.0 + 9.0, self.A) for i in range(13)]
        paras = T.paragraphify(segs, 100.0)
        self.assertEqual(len(paras), 3)

    def test_empty(self):
        self.assertEqual(T.paragraphify([], 1.5), [])


class TestRevisionPath(unittest.TestCase):
    def test_first_revision_and_rev_stripping(self):
        with tempfile.TemporaryDirectory() as d:
            original = Path(d) / "case.transcript.docx"
            original.write_text("x")
            rev1 = T._next_revision_path(original)
            self.assertEqual(rev1.name, "case.transcript.rev1.docx")
            rev1.write_text("x")
            # Next revision skips the existing rev1.
            rev2 = T._next_revision_path(original)
            self.assertEqual(rev2.name, "case.transcript.rev2.docx")
            # A revision of a revision doesn't nest suffixes.
            rev2b = T._next_revision_path(rev1)
            self.assertEqual(rev2b.name, "case.transcript.rev2.docx")


class TestGuessAudio(unittest.TestCase):
    def test_finds_sibling_media(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "interview.mp3").write_bytes(b"")
            transcript = d / "interview.transcript.docx"
            transcript.write_bytes(b"")
            self.assertEqual(
                T._guess_audio_for_transcript(transcript),
                str(d / "interview.mp3"))

    def test_revision_strips_rev_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "interview.m4a").write_bytes(b"")
            rev = d / "interview.transcript.rev3.docx"
            rev.write_bytes(b"")
            self.assertEqual(
                T._guess_audio_for_transcript(rev),
                str(d / "interview.m4a"))

    def test_no_media_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "interview.transcript.txt"
            t.write_text("")
            self.assertIsNone(T._guess_audio_for_transcript(t))


class TestExtractWordConf(unittest.TestCase):
    def test_extracts_words(self):
        res = {"segments": [
            {"start": 0, "end": 2, "text": "hi there",
             "words": [
                 {"word": "hi", "start": 0, "end": 1, "probability": 0.9},
                 {"word": " there", "start": 1, "end": 2,
                  "probability": 0.3},
             ]},
            {"start": 2, "end": 3, "text": "no words"},
        ]}
        wc = T._extract_word_conf(res)
        self.assertEqual(len(wc), 2)
        self.assertEqual(wc[1][2].strip(), "there")
        self.assertAlmostEqual(wc[1][3], 0.3)

    def test_handles_missing_probability(self):
        res = {"segments": [
            {"words": [{"word": "x", "start": 0, "end": 1}]}]}
        wc = T._extract_word_conf(res)
        self.assertEqual(len(wc), 1)
        self.assertIsNone(wc[0][3])

    def test_empty_inputs(self):
        self.assertEqual(T._extract_word_conf(None), [])
        self.assertEqual(T._extract_word_conf({}), [])


# =====================================================================
# Config-dir persistence (settings / recent / autosave)
# =====================================================================

class TestTheme(unittest.TestCase):
    def test_resolve_explicit_settings(self):
        self.assertEqual(T._resolve_theme("light"), "light")
        self.assertEqual(T._resolve_theme("dark"), "dark")

    def test_resolve_auto_returns_valid_palette_key(self):
        self.assertIn(T._resolve_theme("auto"), T._PALETTES)

    def test_palette_follows_active_theme(self):
        old = T._ACTIVE_THEME
        try:
            T._ACTIVE_THEME = "dark"
            self.assertEqual(T._palette(), T._PALETTES["dark"])
            T._ACTIVE_THEME = "light"
            self.assertEqual(T._palette(), T._PALETTES["light"])
        finally:
            T._ACTIVE_THEME = old

    def test_palettes_have_matching_keys(self):
        light, dark = T._PALETTES["light"], T._PALETTES["dark"]
        self.assertEqual(set(light.keys()), set(dark.keys()))
        self.assertEqual(set(light["speaker_colours"]),
                         set(dark["speaker_colours"]))


class TestSettingsPersistence(unittest.TestCase):
    def test_round_trip(self):
        T._settings_save({"model": "tiny", "gap": 2.5, "review": True})
        loaded = T._settings_load()
        self.assertEqual(loaded["model"], "tiny")
        self.assertEqual(loaded["gap"], 2.5)
        self.assertIs(loaded["review"], True)

    def test_corrupt_file_returns_empty(self):
        T._settings_file().write_text("{not json", encoding="utf-8")
        self.assertEqual(T._settings_load(), {})


class TestRecentList(unittest.TestCase):
    def setUp(self):
        T._recent_save([])

    def test_add_moves_to_front_and_dedupes(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "a.docx"; a.write_text("")
            b = Path(d) / "b.docx"; b.write_text("")
            T._recent_add(a)
            T._recent_add(b)
            T._recent_add(a)  # again: moves to front, no duplicate
            items = T._recent_load()
            self.assertEqual(items[0], str(a.resolve()))
            self.assertEqual(len(items), 2)

    def test_capped_at_max(self):
        for i in range(T._RECENT_MAX + 5):
            T._recent_add(f"/tmp/file{i}.txt")
        self.assertEqual(len(T._recent_load()), T._RECENT_MAX)


class TestAutosave(unittest.TestCase):
    def test_round_trip_and_clear(self):
        data = {
            "out_path": "/tmp/x.docx",
            "paragraphs": [[[0.0, 1.0, "hello"]]],
            "speakers": ["1"],
            "speaker_names": {"1": "Witness"},
        }
        T._autosave_save(data)
        loaded = T._autosave_load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["speakers"], ["1"])
        T._autosave_clear()
        self.assertIsNone(T._autosave_load())

    def test_rejects_empty_paragraphs(self):
        T._autosave_save({"out_path": "/tmp/x.docx", "paragraphs": []})
        self.assertIsNone(T._autosave_load())
        T._autosave_clear()


# =====================================================================
# Writers and round trips
# =====================================================================

def _sample_paragraphs():
    return [
        [(0.0, 2.0, "Good morning, your Honour.")],
        [(2.0, 4.0, "Good morning, counsel.")],
        [(4.0, 6.0, "May it please the court.")],
    ]


class TestTxtRoundTrip(unittest.TestCase):
    def test_speakers_and_title_survive(self):
        speakers = ["MR SMITH", "HIS HONOUR", "MR SMITH"]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "t.transcript.txt"
            T.write_paragraphs_to_file(
                _sample_paragraphs(), out, show_timestamp=True,
                title="R v Example", output_format="txt",
                speakers=speakers)
            parsed = T.read_paragraphs_from_file(out)
        self.assertEqual(parsed["title"], "R v Example")
        self.assertTrue(parsed["show_timestamp"])
        self.assertEqual(len(parsed["paragraphs"]), 3)
        self.assertEqual(parsed["speakers"], speakers)

    def test_unattributed_marker_blocks_carryover(self):
        speakers = ["MR SMITH", None, None]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "t.transcript.txt"
            T.write_paragraphs_to_file(
                _sample_paragraphs(), out, output_format="txt",
                speakers=speakers)
            parsed = T.read_paragraphs_from_file(out)
        self.assertEqual(parsed["speakers"], speakers)

    def test_loaded_spans_stretch_to_next_paragraph(self):
        # Files only record paragraph start times. Parsed paragraphs must
        # come back with each end stretched to the next paragraph's start,
        # so audio playback covers the full paragraph (the bug where only
        # ~1.3s snippets played). Timestamps round to whole seconds in the
        # file, so use whole-second starts.
        paras = [
            [(0.0, 2.0, "First paragraph of the morning.")],
            [(45.0, 47.0, "Second paragraph after a long stretch.")],
            [(90.0, 92.0, "Third and final paragraph.")],
        ]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "t.transcript.txt"
            T.write_paragraphs_to_file(paras, out, output_format="txt")
            parsed = T.read_paragraphs_from_file(out)
        spans = [(p[0][0], p[-1][1]) for p in parsed["paragraphs"]]
        self.assertEqual(spans[0], (0.0, 45.0))
        self.assertEqual(spans[1], (45.0, 90.0))
        # Last paragraph keeps the placeholder span (real end unknowable);
        # playback treats it as open-ended.
        self.assertEqual(spans[2], (90.0, 91.0))


@unittest.skipUnless(
    __import__("importlib.util", fromlist=["util"]).find_spec("docx"),
    "python-docx not installed")
class TestDocxRoundTrip(unittest.TestCase):
    def test_round_trip_with_six_speakers(self):
        paras = [[(float(i), float(i) + 1, f"Statement number {i}.")]
                 for i in range(6)]
        speakers = [f"Witness {i + 1}" for i in range(6)]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "t.transcript.docx"
            T.write_paragraphs_to_file(
                paras, out, show_timestamp=True, title="Test",
                output_format="docx", speakers=speakers)
            self.assertGreater(out.stat().st_size, 1000)
            parsed = T.read_paragraphs_from_file(out)
        self.assertEqual(len(parsed["paragraphs"]), 6)
        self.assertEqual(parsed["speakers"], speakers)

    def test_a4_page_size(self):
        from docx import Document
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "t.docx"
            T.write_paragraphs_to_file(
                _sample_paragraphs(), out, output_format="docx")
            sec = Document(str(out)).sections[0]
            self.assertAlmostEqual(sec.page_width.cm, 21.0, places=1)
            self.assertAlmostEqual(sec.page_height.cm, 29.7, places=1)


@unittest.skipUnless(
    __import__("importlib.util", fromlist=["util"]).find_spec("reportlab"),
    "reportlab not installed")
class TestPdfWriter(unittest.TestCase):
    def test_writes_valid_pdf(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "t.transcript.pdf"
            T.write_paragraphs_to_file(
                _sample_paragraphs(), out, show_timestamp=True,
                title="R v Example", output_format="pdf",
                speakers=["MR SMITH", "HIS HONOUR", None])
            blob = out.read_bytes()
        self.assertTrue(blob.startswith(b"%PDF"), "not a PDF file")
        self.assertGreater(len(blob), 1000)

    def test_xml_specials_in_body_dont_crash(self):
        paras = [[(0.0, 1.0, "Smith & Jones <Pty> Ltd \"quoted\"")]]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "t.pdf"
            T.write_paragraphs_to_file(paras, out, output_format="pdf")
            self.assertTrue(out.read_bytes().startswith(b"%PDF"))


class TestUnknownFormatFallsBackToTxt(unittest.TestCase):
    def test_unknown_format_writes_text(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "t.weird"
            T.write_paragraphs_to_file(
                _sample_paragraphs(), out, output_format="weird")
            self.assertIn("Good morning", out.read_text(encoding="utf-8"))



if __name__ == "__main__":
    unittest.main(verbosity=2)
