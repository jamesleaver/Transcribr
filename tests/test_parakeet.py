"""NVIDIA Parakeet through sherpa-onnx (transcribr/parakeet.py). A
stand-in recogniser keeps these tests offline and fast."""

import io
import queue
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness  # noqa: F401

try:
    import numpy as np
    import transcribr as T
    import transcribr.worker as W
    from transcribr import parakeet as P
    from transcribr.engines import MODEL_TIERS, PARAKEET_MODEL
except ImportError as e:
    raise unittest.SkipTest(f"cannot import: {e}")

SR = 16000


class FakeResult:
    def __init__(self, tokens, times, durations=None, logps=None):
        self.tokens, self.timestamps = tokens, times
        self.durations = durations or [0.1] * len(tokens)
        self.ys_log_probs = logps or [-0.01] * len(tokens)


class FakeRecognizer:
    """Says the same two sentences in every window it is given."""

    def __init__(self):
        self.windows = []

    def create_stream(self):
        rec = self

        class Stream:
            def accept_waveform(self, sr, samples):
                rec.windows.append(len(samples) / sr)
                self.result = FakeResult(
                    [" Did", " you", " go", "?", " I", " did", "."],
                    [0.1, 0.3, 0.5, 0.6, 1.2, 1.4, 1.6])
        return Stream()

    def decode_stream(self, stream):
        pass


class TestWindows(unittest.TestCase):

    def test_windows_cover_the_audio_without_gaps(self):
        rng = np.random.default_rng(0)
        audio = rng.standard_normal(SR * 95).astype(np.float32) * 0.1
        audio[SR * 27:SR * 28] = 0          # a pause to cut at
        w = P.quiet_windows(audio)
        self.assertEqual(w[0][0], 0)
        self.assertEqual(w[-1][1], len(audio))
        for (a, b), (c, _) in zip(w, w[1:]):
            self.assertEqual(b, c)
        self.assertTrue(all(b - a <= 30 * SR for a, b in w))
        self.assertTrue(27 * SR <= w[0][1] <= 28 * SR,
                        "the first cut should land in the pause")


class TestWords(unittest.TestCase):

    def test_tokens_become_words_with_timings_and_confidence(self):
        r = FakeResult([" It", "'", "s", " fine", "."],
                       [0.2, 0.4, 0.5, 0.7, 0.9],
                       [0.1, 0.05, 0.1, 0.2, 0.05],
                       [-0.1, -2.0, -0.1, -0.05, -0.01])
        ws = P.words_from(r, offset=10.0)
        self.assertEqual([w[2] for w in ws], ["It's", "fine."])
        self.assertAlmostEqual(ws[0][0], 10.2)
        self.assertAlmostEqual(ws[0][1], 10.6)
        # the least confident token decides the word's confidence
        self.assertAlmostEqual(ws[0][3], np.exp(-2.0), places=5)


class TestRun(unittest.TestCase):

    def run_it(self, seconds=70, cancel=False):
        fake = FakeRecognizer()
        q, ev = queue.Queue(), threading.Event()
        if cancel:
            ev.set()
        seen = []
        params = {"_audio": np.zeros(SR * seconds, dtype=np.float32),
                  "input": "x.wav"}
        with mock.patch.dict(sys.modules,
                             {"sherpa_onnx": types.ModuleType("sherpa_onnx")}), \
                mock.patch.object(P, "ensure_model"), \
                mock.patch.object(P, "_load", return_value=fake):
            segs, result, partial = P.run(
                params, q, ev, on_segment=lambda s, w: seen.append((s, w)))
        return fake, segs, result, partial, seen

    def test_one_segment_per_sentence_with_word_timings(self):
        fake, segs, result, partial, seen = self.run_it()
        self.assertEqual(len(fake.windows), 3)          # 70 s in 30 s windows
        self.assertEqual([s[2] for s in segs[:2]], ["Did you go?", "I did."])
        self.assertFalse(partial)
        words = W._extract_word_conf(result)
        self.assertEqual(len(words), 5 * 3)     # Did you go? I did.
        self.assertAlmostEqual(segs[2][0], fake.windows[0] + 0.1, places=3)

    def test_segments_are_handed_over_as_they_come(self):
        _, segs, _, _, seen = self.run_it()
        self.assertEqual([s for s, _ in seen], segs)

    def test_a_stop_is_honoured(self):
        _, segs, _, partial, _ = self.run_it(cancel=True)
        self.assertEqual(segs, [])
        self.assertTrue(partial)


class TestRouting(unittest.TestCase):

    def test_parakeet_has_its_own_runner(self):
        params = {"model": PARAKEET_MODEL, "engine": "faster",
                  "language": "en"}
        self.assertIs(W.runner_for(params), W._run_parakeet)

    def test_other_languages_fall_back_to_whisper(self):
        q = queue.Queue()
        params = {"model": PARAKEET_MODEL, "engine": "faster",
                  "language": "fr"}
        self.assertIs(W.runner_for(params, q), W._run_faster_whisper)
        self.assertEqual(params["model"], "small")

    def test_whisper_models_are_unaffected(self):
        params = {"model": "large-v3-turbo", "engine": "mlx"}
        self.assertIs(W.runner_for(params), W._run_mlx_whisper)

    def test_quick_draft_is_parakeet_in_english(self):
        draft = next(t for t in MODEL_TIERS if t["id"] == "draft")
        self.assertEqual(draft["model_en"], PARAKEET_MODEL)
        self.assertEqual(draft["model"], "small")

    def test_settings_accept_the_model(self):
        self.assertEqual(T.validate_settings({"model": PARAKEET_MODEL})
                         ["model"], PARAKEET_MODEL)


class TestDownload(unittest.TestCase):

    def test_a_tampered_download_is_refused(self):
        class Resp(io.BytesIO):
            headers = {"Content-Length": "5"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch.object(P, "model_ready", return_value=False), \
                mock.patch("urllib.request.urlopen",
                           return_value=Resp(b"hello")):
            with self.assertRaises(P.ParakeetUnavailable) as cm:
                P.ensure_model(queue.Queue())
        self.assertIn("integrity", str(cm.exception))
        self.assertFalse(P.model_ready())


class TestModelsPage(unittest.TestCase):
    """Parakeet can be fetched before a run and deleted afterwards, like
    any other model - it is 480 MB and should not arrive by surprise."""

    def setUp(self):
        import transcribr.models as M
        self.M = M
        if not M._parakeet_available():
            # sherpa-onnx is in every build, but not in the bare
            # environment the CI suite runs in.
            self.skipTest("needs sherpa-onnx")
        d = P.models_dir()
        d.mkdir(parents=True, exist_ok=True)
        self.addCleanup(P.remove_model)

    def install_fake(self):
        for i, name in enumerate(P._SPEC["members"]):
            (P.models_dir() / name).write_bytes(b"x" * (i + 1))

    def entry(self):
        payload = self.M.ModelStore(engines=[]).payload()
        return next((e for e in payload["engines"]
                     if e["key"] == "parakeet"), None)

    def test_listed_with_its_size_when_installed(self):
        self.assertFalse(self.entry()["models"][0]["installed"])
        self.install_fake()
        e = self.entry()
        self.assertTrue(e["models"][0]["installed"])
        self.assertEqual(e["total"], 1 + 2 + 3 + 4)
        self.assertEqual(e["models"][0]["model"], PARAKEET_MODEL)

    def test_download_goes_to_parakeets_own_downloader(self):
        seen = {}

        def fake(q, cancel_event=None, on_progress=None):
            seen["progress"] = on_progress
            if on_progress:
                on_progress({"downloaded": 5, "total": 10, "speed": 1.0})

        with mock.patch.object(P, "ensure_model", fake):
            self.M._prefetch_model("parakeet", PARAKEET_MODEL,
                                   on_progress=lambda d: seen.setdefault(
                                       "reported", d))
        self.assertEqual(seen["reported"]["total"], 10)

    def test_delete_frees_the_space(self):
        self.install_fake()
        self.assertEqual(self.M._uninstall_model("parakeet", PARAKEET_MODEL),
                         1 + 2 + 3 + 4)
        self.assertFalse(P.model_ready())

    def test_deleting_what_is_not_there_says_so(self):
        from transcribr.errors import ApiFail
        with self.assertRaises(ApiFail):
            self.M._uninstall_model("parakeet", PARAKEET_MODEL)

    def test_not_offered_where_sherpa_onnx_is_missing(self):
        with mock.patch.object(self.M, "_parakeet_available",
                               return_value=False):
            payload = self.M.ModelStore(engines=[]).payload()
        self.assertEqual([e for e in payload["engines"]
                          if e["key"] == "parakeet"], [])

    def test_half_an_install_does_not_count_as_installed(self):
        (P.models_dir() / P._SPEC["members"][0]).write_bytes(b"x")
        self.assertFalse(P.model_ready())
        self.assertEqual(P.model_size(), 0)


if __name__ == "__main__":
    unittest.main()
