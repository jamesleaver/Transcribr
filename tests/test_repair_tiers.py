"""The two-tier repair: a cheap attempt first, the full model after."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness  # noqa: F401

try:
    import transcribr as T
    from transcribr.audioprep import REPAIR_FIRST_MODEL, repair_settings
except ImportError as e:
    raise unittest.SkipTest(f"cannot import transcribr: {e}")


class TestRepairSettings(unittest.TestCase):
    def base(self, **kw):
        return {**T.default_settings(), **kw}

    def test_the_first_attempt_drops_to_a_small_model(self):
        """It runs while the main engine still holds its memory, and a
        second copy of a large model is what gets the app killed."""
        s = repair_settings(self.base(model="large-v3-turbo"), tier=1)
        self.assertEqual(s["model"], REPAIR_FIRST_MODEL)

    def test_the_second_keeps_the_model_the_run_used(self):
        s = repair_settings(self.base(model="large-v3-turbo"), tier=2)
        self.assertEqual(s["model"], "large-v3-turbo")

    def test_both_tiers_stop_conditioning(self):
        """Conditioning is what turns one bad guess into a runaway
        repetition, so it is exactly what to drop on a retry."""
        for tier in (1, 2):
            s = repair_settings(
                self.base(condition_on_previous_text=True), tier=tier)
            self.assertFalse(s["condition_on_previous_text"], f"tier {tier}")

    def test_the_first_pass_is_skipped_for_other_languages(self):
        """small.en is English-only; there is nothing to gain from
        running it over French."""
        self.assertIsNone(repair_settings(self.base(language="French"),
                                          tier=1))
        self.assertIsNotNone(repair_settings(self.base(language="French"),
                                             tier=2))

    def test_auto_detect_still_gets_the_cheap_attempt(self):
        s = repair_settings(self.base(language=""), tier=1)
        self.assertIsNotNone(s)

    def test_the_caller_s_settings_are_not_modified(self):
        original = self.base(model="large-v3-turbo")
        repair_settings(original, tier=1)
        self.assertEqual(original["model"], "large-v3-turbo")


class TestEscalation(unittest.TestCase):
    """The order of the two attempts, with the engine stubbed out."""

    def setUp(self):
        # A bare backend: _repair_escalating touches only the broker and
        # _run_repair, so there is nothing to start up.
        self.backend = object.__new__(T.WebBackend)
        self.backend.broker = T.EventBroker()
        self.tiers = []
        self.backend._run_repair = lambda session, tier: (
            self.tiers.append(tier) or False)

    def escalate(self, *, enabled=True):
        settings = {**T.default_settings(), "auto_retranscribe": enabled}
        original = T.server.current_settings
        T.server.current_settings = lambda: settings
        try:
            T.WebBackend._repair_escalating(self.backend, None)
        finally:
            T.server.current_settings = original

    def test_the_cheap_attempt_comes_first(self):
        self.escalate()
        self.assertEqual(self.tiers, [1, 2])

    def test_nothing_runs_when_the_setting_is_off(self):
        self.escalate(enabled=False)
        self.assertEqual(self.tiers, [])


class TheRepairPassMustNotMakeItWorse(unittest.TestCase):
    """auto_retranscribe accepts a re-read only when it is an
    improvement, and 'improvement' has to mean what the transcript will
    look like afterwards.

    detect_hallucinations flags a paragraph that repeats the one next to
    it, so a candidate judged on its own cannot see the duplicate it is
    about to become. It was judged on its own, and the pass could
    therefore replace one flagged paragraph with a clean-looking copy of
    the paragraph above it - ending with two flagged paragraphs where
    there had been one, and the same sentence printed twice.
    """

    NEIGHBOUR = "The witness arrived at the depot on Harbour Road that evening."

    def _transcript(self):
        def para(text, start):
            return [(start, start + 4.0, text)]
        out = [
            para(self.NEIGHBOUR, 0),
            para("the the the the the the the the the the the the", 5),
            para("He was wearing a dark jacket and left by the side "
                 "entrance.", 10),
        ]
        # Long enough that the 25% re-read budget allows the span.
        out += [para(f"Filler paragraph number {k} of the hearing.",
                     15 + k * 5) for k in range(20)]
        return out

    def _run_with(self, replacement):
        import transcribr.audioprep as ap
        original = ap.retranscribe_slice
        ap.retranscribe_slice = lambda *a, **k: [(5.0, 9.0, replacement)]
        try:
            return ap.auto_retranscribe(
                self._transcript(), [], "/nonexistent.wav",
                {"gap": 1.5, "model": "tiny"})
        finally:
            ap.retranscribe_slice = original

    def test_a_re_read_that_duplicates_its_neighbour_is_refused(self):
        from transcribr.worker import detect_hallucinations
        before = self._transcript()
        self.assertEqual(detect_hallucinations(before), [1])
        after, _w, attempted, runs = self._run_with(self.NEIGHBOUR)
        self.assertEqual(attempted, 1)
        self.assertEqual(runs, [])                  # not accepted
        self.assertEqual(detect_hallucinations(after), [1])   # not worse

    def test_a_genuine_improvement_is_still_taken(self):
        from transcribr.worker import detect_hallucinations
        after, _w, attempted, runs = self._run_with(
            "I stayed on to lock up the back gate after my shift.")
        self.assertEqual(attempted, 1)
        self.assertEqual(len(runs), 1)
        self.assertEqual(detect_hallucinations(after), [])
        self.assertIn("lock up the back gate", after[1][0][2])

    def test_the_count_of_flagged_paragraphs_never_rises(self):
        from transcribr.worker import detect_hallucinations
        for replacement in (self.NEIGHBOUR,
                            "He was wearing a dark jacket and left by the "
                            "side entrance.",
                            "the the the the the the the the the",
                            "I stayed on to lock up the back gate."):
            with self.subTest(replacement=replacement[:40]):
                before = self._transcript()
                after, _w, _a, _r = self._run_with(replacement)
                self.assertLessEqual(len(detect_hallucinations(after)),
                                     len(detect_hallucinations(before)))
