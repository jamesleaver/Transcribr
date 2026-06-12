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

try:
    import tkinter as tk
except ImportError:
    tk = None


def _have_tk_display():
    """True if a Tk root can actually be created (not just imported)."""
    if tk is None:
        return False
    try:
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except tk.TclError:
        return False


_TK_OK = _have_tk_display()

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


# =====================================================================
# Review pane (needs Tk)
# =====================================================================

@unittest.skipUnless(_TK_OK, "no Tk display available")
class TestReviewPaneText(unittest.TestCase):
    WORD_CONF = [
        (0.0, 0.5, "the", 0.92), (0.5, 1.5, " quick", 0.20),
        (1.5, 2.5, " brown", 0.95), (2.5, 4.0, " fox", 0.50),
        (4.0, 4.5, " jumps", 0.9), (4.5, 5.0, " over", 0.9),
        (5.0, 6.0, " everything", 0.9),
    ]

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.autosaves = []
        self.pane = T.ReviewPaneText(
            self.root,
            [
                [(0.0, 4.0, "the quick brown fox")],
                [(4.0, 6.0, "jumps over everything")],
                [(6.0, 8.0, "and keeps on going")],
            ],
            on_save=lambda *a: None,
            on_cancel=lambda: None,
            word_conf=self.WORD_CONF,
            on_autosave=lambda p, s, n: self.autosaves.append((p, s, n)),
        )
        self.root.update_idletasks()

    def tearDown(self):
        self.root.destroy()

    def test_undo_redo_speaker_change(self):
        self.pane.selected_idx = 0
        self.pane._kb_set_speaker("1")
        self.assertEqual(self.pane.speakers[0], "1")
        self.pane._undo()
        self.assertIsNone(self.pane.speakers[0])
        self.pane._redo()
        self.assertEqual(self.pane.speakers[0], "1")

    def test_undo_merge_restores_paragraphs(self):
        self.pane.selected_idx = 1
        self.pane._kb_merge()
        self.assertEqual(len(self.pane.paragraphs), 2)
        self.pane._undo()
        self.assertEqual(len(self.pane.paragraphs), 3)

    def test_split_and_undo(self):
        body = "the quick brown fox"
        self.pane._do_split(0, body.index("brown"), body)
        self.assertEqual(len(self.pane.paragraphs), 4)
        self.assertEqual(self.pane.paragraphs[1][0][2], "brown fox")
        self.pane._undo()
        self.assertEqual(len(self.pane.paragraphs), 3)

    def test_replace_all_and_undo(self):
        self.pane.find_var.set("quick")
        self.pane.replace_var.set("QUICK")
        self.pane._replace_all()
        self.assertIn("QUICK", self.pane.paragraphs[0][0][2])
        self.assertIn("Replaced 1", self.pane.find_status_var.get())
        self.pane._undo()
        self.assertIn("quick", self.pane.paragraphs[0][0][2])

    def test_replace_all_no_matches(self):
        self.pane.find_var.set("zzznotfound")
        self.pane.replace_var.set("x")
        depth = len(self.pane._undo_stack)
        self.pane._replace_all()
        # No undo entry for a no-op.
        self.assertEqual(len(self.pane._undo_stack), depth)

    def test_find_next_highlights(self):
        self.pane.find_var.set("fox")
        self.pane._reset_search()
        self.pane._find_next()
        rng = self.pane.text.tag_ranges("search")
        self.assertTrue(rng)
        self.assertEqual(self.pane.text.get(rng[0], rng[1]), "fox")

    def test_confidence_shading_aligns(self):
        self.pane.show_confidence = True
        self.pane._render_all()
        low = self.pane.text.tag_ranges("conf_low")
        med = self.pane.text.tag_ranges("conf_med")
        self.assertEqual(self.pane.text.get(low[0], low[1]), "quick")
        self.assertEqual(self.pane.text.get(med[0], med[1]), "fox")

    def test_shading_skips_mismatched_paragraph(self):
        self.pane.show_confidence = True
        self.pane.paragraphs[0] = [(0.0, 4.0, "edited beyond recognition")]
        self.pane._render_all()  # must not raise
        self.assertFalse(self.pane.text.tag_ranges("conf_low"))

    def test_jump_next_attention_unattributed(self):
        self.pane.show_confidence = False
        self.pane.speakers = ["1", None, "2"]
        self.pane.selected_idx = 0
        self.pane._jump_next_attention()
        self.assertEqual(self.pane.selected_idx, 1)

    def test_jump_wraps_around(self):
        self.pane.show_confidence = False
        self.pane.speakers = [None, "1", "1"]
        self.pane.selected_idx = 2
        self.pane._jump_next_attention()
        self.assertEqual(self.pane.selected_idx, 0)

    def test_jump_low_confidence_when_shading_on(self):
        self.pane.show_confidence = True
        # All paragraphs labelled; only paragraph 0 has low-conf words.
        self.pane.speakers = ["1", "1", "1"]
        self.pane.selected_idx = 1
        self.pane._jump_next_attention()
        self.assertEqual(self.pane.selected_idx, 0)

    def test_autosave_fires_after_mutation(self):
        self.pane.selected_idx = 0
        self.pane._kb_set_speaker("3")
        # The debounce timer can't fire without a mainloop; invoke the
        # deadline callback directly.
        self.pane._do_autosave()
        self.assertEqual(len(self.autosaves), 1)
        paragraphs, speakers, names = self.autosaves[0]
        self.assertEqual(speakers[0], "3")
        self.assertEqual(len(paragraphs), 3)

    def test_hotkey_seven_reveals_speaker_field(self):
        self.pane.selected_idx = 0
        self.pane._kb_set_speaker("7")
        self.assertEqual(self.pane.speakers[0], "7")
        self.assertGreaterEqual(self.pane.visible_speakers, 7)
        self.assertIn("7", self.pane.name_vars)

    def test_can_play_requires_audio_and_ffplay(self):
        self.assertFalse(self.pane._can_play())  # no audio_path given
        self.pane.audio_path = "/tmp/anything.mp3"
        self.pane._ffplay = "/usr/bin/true"
        self.assertTrue(self.pane._can_play())


# =====================================================================
# Main GUI (needs Tk)
# =====================================================================

@unittest.skipUnless(_TK_OK, "no Tk display available")
class TestWhisperGUI(unittest.TestCase):
    def setUp(self):
        T._autosave_clear()
        self.root = tk.Tk()
        self.root.withdraw()
        self.gui = T.WhisperGUI(self.root)
        self.root.update_idletasks()

    def tearDown(self):
        self.root.destroy()
        T._autosave_clear()

    def test_settings_collect_apply_round_trip(self):
        self.gui.model_var.set("medium.en")
        self.gui.output_format_var.set("pdf")
        self.gui.prompt_text.delete("1.0", "end")
        self.gui.prompt_text.insert("1.0", "R v Example")
        snap = self.gui._collect_settings()
        self.gui.model_var.set("tiny")
        self.gui.output_format_var.set("txt")
        self.gui.prompt_text.delete("1.0", "end")
        self.gui._apply_settings(snap)
        self.assertEqual(self.gui.model_var.get(), "medium.en")
        self.assertEqual(self.gui.output_format_var.get(), "pdf")
        self.assertEqual(
            self.gui.prompt_text.get("1.0", "end-1c"), "R v Example")

    def test_apply_settings_ignores_junk(self):
        self.gui.model_var.set("medium.en")
        self.gui._apply_settings(
            {"model": "no-such-model", "output_format": "exe",
             "gap": "not-a-number"})
        self.assertEqual(self.gui.model_var.get(), "medium.en")

    def test_batch_queue_operations(self):
        self.gui._batch_add_paths(["/tmp/a.mp3", "/tmp/b.mp3", "/tmp/a.mp3"])
        self.assertEqual(self.gui._batch_files(),
                         ["/tmp/a.mp3", "/tmp/b.mp3"])
        self.gui.batch_listbox.selection_set(0)
        self.gui._batch_remove_selected()
        self.assertEqual(self.gui._batch_files(), ["/tmp/b.mp3"])
        self.gui._batch_clear()
        self.assertEqual(self.gui._batch_files(), [])

    @unittest.skipUnless(T.AVAILABLE_ENGINES, "no whisper engine installed")
    def test_build_params_confidence_enables_word_timestamps(self):
        self.gui.confidence_var.set(True)
        self.gui.word_ts_var.set(False)
        p = self.gui._build_params("/tmp/x.mp3", "/tmp/x.txt",
                                   review_before_save=True)
        self.assertTrue(p["word_timestamps"])
        self.assertTrue(p["highlight_confidence"])
        self.gui.confidence_var.set(False)
        p = self.gui._build_params("/tmp/x.mp3", "/tmp/x.txt",
                                   review_before_save=True)
        self.assertFalse(p["word_timestamps"])

    def test_review_autosave_written_and_cleared_on_exit(self):
        self.gui._pending_review_info = {
            "out_path": "/tmp/case.transcript.docx",
            "show_timestamp": True,
            "title": None,
            "output_format": "docx",
            "loaded": False,
            "audio_path": None,
        }
        self.gui._on_review_autosave(
            [[(0.0, 1.0, "hello there")]], ["2"],
            dict(T.ReviewPaneText.DEFAULT_NAMES))
        saved = T._autosave_load()
        self.assertIsNotNone(saved)
        self.assertEqual(saved["speakers"], ["2"])
        # Only the in-use speaker's name is kept.
        self.assertEqual(set(saved["speaker_names"].keys()), {"2"})
        self.gui._exit_review_mode()
        self.assertIsNone(T._autosave_load())


if __name__ == "__main__":
    unittest.main(verbosity=2)
