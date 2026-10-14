"""What actually reaches the engine from the Settings page.

A setting the engine never receives is worse than no setting: the
Settings page promises something the transcript does not deliver.
"""

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
    import transcribr.worker as W
except ImportError as e:
    raise unittest.SkipTest(f"cannot import transcribr: {e}")


def _options_passed(**params):
    """Run the faster-whisper runner against a stand-in model and return
    the options its transcribe() was called with."""
    seen = {}

    class Info:
        duration = 1.0
        language = "en"

    class Model:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, audio, **options):
            seen.update(options)
            return iter([]), Info()

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = Model
    base = dict(model="tiny.en", input="x.wav", _audio=[0.0] * 16000,
                audio_duration=1.0, beam_size=5, best_of=5,
                temperature=0.0)
    with mock.patch.dict(sys.modules, {"faster_whisper": fake}):
        W._run_faster_whisper({**base, **params}, queue.Queue(),
                              threading.Event())
    return seen


class TestFasterWhisperBeam(unittest.TestCase):

    def test_a_beam_of_one_reaches_the_engine(self):
        """faster-whisper's own default is a beam of 5. Leaving the
        option out, as the runner used to for a beam of 1, silently
        turned "greedy" into beam search, so the two settings produced
        identical transcripts."""
        self.assertEqual(_options_passed(beam_size=1)["beam_size"], 1)

    def test_a_wider_beam_reaches_the_engine(self):
        self.assertEqual(_options_passed(beam_size=8)["beam_size"], 8)


class TestPromptReachesEveryWindow(unittest.TestCase):
    """Unconditioned, faster-whisper's initial_prompt reaches only the
    first 30-second window. The context / vocabulary hint must help in
    every window, so it goes as hotwords instead."""

    def test_unconditioned_prompt_goes_as_hotwords(self):
        seen = _options_passed(initial_prompt="Macklebum, AVO",
                               condition_on_previous_text=False)
        self.assertEqual(seen.get("hotwords"), "Macklebum, AVO")
        self.assertNotIn("initial_prompt", seen)

    def test_conditioned_prompt_stays_an_initial_prompt(self):
        """Conditioned, the prompt carries forward through the running
        text as it always did; sending hotwords too would say it twice."""
        seen = _options_passed(initial_prompt="Macklebum, AVO",
                               condition_on_previous_text=True)
        self.assertEqual(seen.get("initial_prompt"), "Macklebum, AVO")
        self.assertNotIn("hotwords", seen)

    def test_no_prompt_sends_neither(self):
        seen = _options_passed(condition_on_previous_text=False)
        self.assertNotIn("hotwords", seen)
        self.assertNotIn("initial_prompt", seen)

    def test_unconditioned_is_the_default(self):
        self.assertIs(_options_passed()["condition_on_previous_text"],
                      False)


class TestAcceptsOption(unittest.TestCase):
    """openai-whisper gained carry_initial_prompt in 20250625; the
    option is sent only to versions that take it."""

    def test_new_signature(self):
        def transcribe(model, audio, *, carry_initial_prompt=False,
                       **decode_options):
            pass
        self.assertTrue(W._accepts_option(transcribe,
                                          "carry_initial_prompt"))

    def test_old_signature(self):
        def transcribe(model, audio, *, initial_prompt=None,
                       **decode_options):
            pass
        self.assertFalse(W._accepts_option(transcribe,
                                           "carry_initial_prompt"))

    def test_uninspectable(self):
        """No signature to read - not an error, just no option."""
        self.assertFalse(W._accepts_option(object(), "anything"))


class TestMlxInstallSkipsTorch(unittest.TestCase):
    """mlx-whisper declares PyTorch but only needs it to convert models:
    its runtime dependencies go in first, then mlx-whisper with
    --no-deps, and nothing names torch."""

    def test_two_pip_steps_and_no_torch(self):
        import subprocess
        import transcribr.models as M
        calls = []
        ok = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(M, "_pip_stream",
                               lambda args, *a: calls.append(args)), \
                mock.patch("subprocess.run", return_value=ok):
            M._run_engine_op("install", "mlx", lambda _l: None,
                             lambda: False)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("mlx-whisper", calls[0])
        self.assertIn("--no-deps", calls[1])
        self.assertIn("mlx-whisper", calls[1])
        self.assertFalse(any("torch" in a for c in calls for a in c))


if __name__ == "__main__":
    unittest.main()
