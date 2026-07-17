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


class TestParagraphifySpeakers(unittest.TestCase):
    # Punctuation-free, non-short-response texts so nothing but the
    # signal under test can force a break (same trick as above).
    A = "and then we walked along"
    B = "toward the river for a while"

    def test_speaker_change_forces_break(self):
        segs = [(0.0, 1.0, self.A), (1.1, 2.0, self.B)]
        paras, spk, conf = T.paragraphify_speakers(segs, 5.0, [0, 1])
        self.assertEqual(len(paras), 2)
        self.assertEqual(spk, [0, 1])
        self.assertEqual(conf, [1.0, 1.0])

    def test_none_speaker_never_breaks(self):
        segs = [(0.0, 1.0, self.A), (1.1, 2.0, self.B),
                (2.1, 3.0, self.A)]
        paras, spk, conf = T.paragraphify_speakers(
            segs, 5.0, [0, None, 0])
        self.assertEqual(len(paras), 1)
        self.assertEqual(spk, [0])

    def test_confidence_is_labelled_share(self):
        # 1s of speaker 0 in a 2s paragraph -> share 0.5.
        segs = [(0.0, 1.0, self.A), (1.1, 2.1, self.B)]
        _, spk, conf = T.paragraphify_speakers(segs, 5.0, [0, None])
        self.assertEqual(spk, [0])
        self.assertAlmostEqual(conf[0], 0.5, places=3)

    def test_all_unattributed(self):
        segs = [(0.0, 1.0, self.A)]
        paras, spk, conf = T.paragraphify_speakers(segs, 5.0, [None])
        self.assertEqual(spk, [None])
        self.assertEqual(conf, [0.0])

    def test_same_break_rules_still_apply(self):
        segs = [(0.0, 1.0, "It was finished."), (1.1, 2.0, self.B)]
        paras, spk, _ = T.paragraphify_speakers(segs, 5.0, [0, 0])
        self.assertEqual(len(paras), 2)


class TestAssignWordSpeakers(unittest.TestCase):
    TURNS = [(0.0, 5.0, 0), (5.5, 10.0, 1)]

    def _word(self, start, end):
        return (start, end, " word", None)

    def test_inside_turn(self):
        out = T.assign_word_speakers(
            [self._word(1.0, 1.4), self._word(6.0, 6.4)], self.TURNS)
        self.assertEqual(out, [0, 1])

    def test_straddling_word_takes_larger_overlap(self):
        out = T.assign_word_speakers([self._word(4.8, 5.9)], self.TURNS)
        # 0.2s with speaker 0, 0.4s with speaker 1.
        self.assertEqual(out, [1])

    def test_word_near_turn_adopts_nearest(self):
        # 0.1s after turn 0 ends vs 0.2s before turn 1 starts -> turn 0.
        out = T.assign_word_speakers([self._word(5.1, 5.3)], self.TURNS)
        self.assertEqual(out, [0])
        # 0.35s after turn 0 vs 0.05s before turn 1 -> turn 1.
        out = T.assign_word_speakers([self._word(5.35, 5.45)], self.TURNS)
        self.assertEqual(out, [1])

    def test_word_far_from_any_turn_is_none(self):
        out = T.assign_word_speakers([self._word(20.0, 20.4)], self.TURNS)
        self.assertEqual(out, [None])


class TestSplitSegmentsBySpeaker(unittest.TestCase):
    def test_single_speaker_segment_keeps_engine_text(self):
        segs = [(0.0, 2.0, "Exactly as Whisper wrote it.")]
        words = [(0.1, 0.5, " Exactly", None), (0.6, 1.0, " as", None),
                 (1.1, 1.5, " Whisper", None), (1.6, 2.0, " wrote it.", None)]
        out_segs, out_spk = T.split_segments_by_speaker(
            segs, words, [0, 0, 0, 0])
        self.assertEqual(out_segs, segs)
        self.assertEqual(out_spk, [0])

    def test_mixed_segment_splits_at_speaker_boundary(self):
        segs = [(0.0, 4.0, "Hello there and welcome back everyone today")]
        words = [(0.0, 0.5, " Hello", None), (0.5, 1.0, " there", None),
                 (1.0, 1.5, " and", None),
                 (2.0, 2.5, " welcome", None), (2.5, 3.0, " back", None),
                 (3.0, 3.5, " everyone", None), (3.5, 4.0, " today", None)]
        out_segs, out_spk = T.split_segments_by_speaker(
            segs, words, [0, 0, 0, 1, 1, 1, 1])
        self.assertEqual(len(out_segs), 2)
        self.assertEqual(out_spk, [0, 1])
        self.assertEqual(out_segs[0][2], "Hello there and")
        self.assertEqual(out_segs[1][2], "welcome back everyone today")
        self.assertAlmostEqual(out_segs[0][0], 0.0)
        self.assertAlmostEqual(out_segs[1][0], 2.0)

    def test_jitter_run_folds_into_predecessor(self):
        # A lone quick word attributed to speaker 1 mid-flow should not
        # split the segment.
        segs = [(0.0, 3.0, "one two three four five six")]
        words = [(i * 0.5, i * 0.5 + 0.4, f" w{i}", None)
                 for i in range(6)]
        out_segs, out_spk = T.split_segments_by_speaker(
            segs, words, [0, 0, 1, 0, 0, 0])
        self.assertEqual(len(out_segs), 1)
        self.assertEqual(out_spk, [0])

    def test_segment_without_words_passes_through(self):
        segs = [(0.0, 2.0, "No word data here")]
        out_segs, out_spk = T.split_segments_by_speaker(segs, [], [])
        self.assertEqual(out_segs, segs)
        self.assertEqual(out_spk, [None])


class TestBuildSpeakerParagraphs(unittest.TestCase):
    A = "and then we walked along"
    B = "toward the river for a while"

    def test_two_speaker_conversation_no_words(self):
        # Segment-overlap fallback path (no word timestamps).
        segs = [(0.0, 4.0, self.A), (4.2, 8.0, self.B),
                (10.0, 14.0, self.A), (14.2, 18.0, self.B)]
        turns = [(0.0, 8.0, 0), (10.0, 18.0, 1)]
        paras, letters = T.build_speaker_paragraphs(segs, [], turns, 1.5)
        self.assertEqual(len(paras), 2)
        self.assertEqual(letters, ["1", "2"])

    def test_first_voice_becomes_speaker_one(self):
        segs = [(0.0, 4.0, self.A), (5.0, 9.0, self.B)]
        # Diarizer ids in reverse order: id 7 speaks first.
        turns = [(0.0, 4.0, 7), (5.0, 9.0, 2)]
        _, letters = T.build_speaker_paragraphs(segs, [], turns, 1.5)
        self.assertEqual(letters, ["1", "2"])

    def test_low_confidence_left_unlabelled(self):
        # Only the last fifth of the paragraph is attributed.
        segs = [(0.0, 4.0, self.A), (4.1, 8.0, self.B),
                (8.1, 10.0, self.A)]
        turns = [(8.1, 10.0, 0)]
        paras, letters = T.build_speaker_paragraphs(segs, [], turns, 5.0)
        self.assertEqual(len(paras), 1)
        self.assertEqual(letters, [None])

    def test_more_than_nine_speakers_keeps_busiest(self):
        # 10 speakers with decreasing airtime; the quietest one (the
        # last) must be left unlabelled, the rest numbered 1..9.
        segs, turns = [], []
        t = 0.0
        for spk in range(10):
            dur = 10.0 - spk
            segs.append((t, t + dur, "It stops here."))
            turns.append((t, t + dur, spk))
            t += dur + 3.0
        paras, letters = T.build_speaker_paragraphs(segs, [], turns, 1.5)
        self.assertEqual(len(paras), 10)
        self.assertEqual(letters[:9],
                         [str(i) for i in range(1, 10)])
        self.assertIsNone(letters[9])


class TestDiarizeModelDownload(unittest.TestCase):
    def _spec(self, url, sha, member=None, target="model.onnx"):
        return {"url": url, "sha256": sha, "archive_member": member,
                "target": target, "label": "test model"}

    def _sha(self, path):
        import hashlib
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def test_plain_download_and_verify(self):
        import queue
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "weights.bin"
            src.write_bytes(b"onnx" * 1000)
            spec = self._spec(src.as_uri(), self._sha(src),
                              target="embed.onnx")
            q = queue.Queue()
            T._download_one_model(spec, q, None)
            got = T._diarize_models_dir() / "embed.onnx"
            self.assertTrue(got.exists())
            self.assertEqual(got.read_bytes(), src.read_bytes())
            got.unlink()

    def test_bad_hash_rejected_and_not_installed(self):
        import queue
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "weights.bin"
            src.write_bytes(b"evil")
            spec = self._spec(src.as_uri(), "0" * 64, target="bad.onnx")
            with self.assertRaises(T.DiarizationUnavailable):
                T._download_one_model(spec, queue.Queue(), None)
            self.assertFalse(
                (T._diarize_models_dir() / "bad.onnx").exists())

    def test_tarball_member_extracted(self):
        import queue
        import tarfile
        with tempfile.TemporaryDirectory() as d:
            payload = Path(d) / "model.onnx"
            payload.write_bytes(b"segmentation-weights")
            tar_path = Path(d) / "pack.tar.bz2"
            with tarfile.open(tar_path, "w:bz2") as tar:
                tar.add(payload, arcname="pack/model.onnx")
            spec = self._spec(tar_path.as_uri(), self._sha(tar_path),
                              member="model.onnx", target="seg.onnx")
            T._download_one_model(spec, queue.Queue(), None)
            got = T._diarize_models_dir() / "seg.onnx"
            self.assertEqual(got.read_bytes(), b"segmentation-weights")
            got.unlink()

    def test_existing_target_skips_download(self):
        import queue
        target = T._diarize_models_dir() / "have.onnx"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"already here")
        spec = self._spec("file:///nonexistent/nowhere.onnx", "0" * 64,
                          target="have.onnx")
        T._download_one_model(spec, queue.Queue(), None)   # must not raise
        self.assertEqual(target.read_bytes(), b"already here")
        target.unlink()


class TestDescribeNonAudioFile(unittest.TestCase):
    def _probe(self, data, suffix=".mp3"):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / f"fake{suffix}"
            p.write_bytes(data)
            return T.describe_non_audio_file(str(p))

    def test_office_document_named_mp3(self):
        reason = self._probe(b"PK\x03\x04" + b"\x00" * 64)
        self.assertIn("Microsoft Office", reason)

    def test_pdf_named_mp3(self):
        reason = self._probe(b"%PDF-1.7\n" + b"\x00" * 64)
        self.assertIn("PDF", reason)

    def test_empty_file(self):
        self.assertIn("empty", self._probe(b""))

    def test_missing_file(self):
        reason = T.describe_non_audio_file("/nonexistent/audio.mp3")
        self.assertIn("could not be read", reason)

    def test_real_audio_gets_no_complaint(self):
        try:
            import av  # noqa: F401
        except ImportError:
            self.skipTest("needs the av (PyAV) package")
        import math
        import struct
        import wave
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tone.wav"
            with wave.open(str(p), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"".join(
                    struct.pack("<h", int(8000 * math.sin(i / 20)))
                    for i in range(16000)))
            self.assertIsNone(T.describe_non_audio_file(str(p)))


def _have_av():
    try:
        import av  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(_have_av(), "needs the av (PyAV) package")
class TestPyAvAudio(unittest.TestCase):
    def _make_wav(self, path, seconds=1.0, rate=44100):
        import math
        import struct
        import wave
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            n = int(seconds * rate)
            frames = b"".join(
                struct.pack("<h", int(12000 * math.sin(
                    2 * math.pi * 440 * i / rate)))
                for i in range(n))
            w.writeframes(frames)

    def test_decode_audio_16k(self):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "tone.wav"
            self._make_wav(wav, seconds=1.0)
            audio = T.decode_audio_16k(wav)
            self.assertIsNotNone(audio)
            self.assertEqual(str(audio.dtype), "float32")
            self.assertAlmostEqual(
                len(audio) / T.AUDIO_SAMPLE_RATE, 1.0, delta=0.05)
            self.assertLessEqual(float(abs(audio).max()), 1.0)

    def test_get_audio_duration(self):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "tone.wav"
            self._make_wav(wav, seconds=2.0)
            duration = T.get_audio_duration(str(wav))
            self.assertIsNotNone(duration)
            self.assertAlmostEqual(duration, 2.0, delta=0.1)

    def test_decode_missing_file_returns_none(self):
        self.assertIsNone(T.decode_audio_16k("/nonexistent/audio.mp3"))


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
