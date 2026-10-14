"""A paragraph break where the voice changes (transcribr.paragraphs.
VoiceBreak): after a finished sentence, a sharp change
of pitch is usually a different person."""

import queue
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness  # noqa: F401

try:
    import numpy as np
    import transcribr as T
    import transcribr.worker as W
    from transcribr.live import LiveTranscript
    from transcribr.paragraphs import VoiceBreak, paragraphify
    from transcribr.turns import VoiceMeter
except ImportError as e:
    raise unittest.SkipTest(f"cannot import: {e}")

SR = 16000
GAP = 1.0


def tone(hz, seconds):
    t = np.arange(int(seconds * SR)) / SR
    return (0.3 * (np.sin(2 * np.pi * hz * t)
                   + 0.5 * np.sin(4 * np.pi * hz * t))).astype(np.float32)


def voices(*parts):
    """Audio of back-to-back voices: parts are (hz, seconds)."""
    return np.concatenate([tone(hz, s) for hz, s in parts])


class TestTheRule(unittest.TestCase):

    def setUp(self):
        # a man's voice for 4 s, then a child's for 4 s
        self.meter = VoiceMeter(voices((120, 4.0), (220, 4.0)))
        self.vb = VoiceBreak(self.meter, min_jump=4.0)

    def test_a_new_voice_after_a_sentence_breaks(self):
        self.assertTrue(self.vb((0.2, 3.9, "I went home."),
                                (4.05, 7.8, "Did you stay there?")))

    def test_never_mid_sentence(self):
        self.assertFalse(self.vb((0.2, 3.9, "I went home and"),
                                 (4.05, 7.8, "stayed there")))

    def test_the_same_voice_does_not_break(self):
        meter = VoiceMeter(voices((120, 4.0), (126, 4.0)))
        self.assertFalse(VoiceBreak(meter, min_jump=4.0)(
            (0.2, 3.9, "I went home."), (4.05, 7.8, "Then I slept.")))

    def test_silence_says_nothing(self):
        meter = VoiceMeter(np.zeros(SR * 8, dtype=np.float32))
        self.assertFalse(VoiceBreak(meter)((0.2, 3.9, "Yes."),
                                           (4.05, 7.8, "Right.")))

    def test_a_meter_can_work_from_a_saved_track(self):
        audio = voices((120, 4.0), (220, 4.0))
        a = VoiceMeter(audio)
        b = VoiceMeter(track=T.turns.voice_track(audio))
        self.assertAlmostEqual(a.pitch(4.5, 7.5), b.pitch(4.5, 7.5),
                               delta=3)

    def test_paragraphify_uses_it(self):
        segs = [(0.2, 3.9, "I went home."), (4.05, 7.8, "Then what?")]
        # today's rules keep these together: a full stop, no pause
        self.assertEqual(len(paragraphify(segs, GAP)), 1)
        self.assertEqual(len(paragraphify(segs, GAP, voice=self.vb)), 2)


class TestLiveMatchesBatch(unittest.TestCase):

    def test_on_random_conversations(self):
        """The rule reads only the audio near each boundary, so a
        paragraph sealed live is the one waiting would have made."""
        from transcribr.paragraphs import _gaps_are_informative
        rng = random.Random(3)
        for trial in range(40):
            parts, segs, t = [], [], 0.0
            for _ in range(rng.randint(2, 12)):
                d = rng.uniform(1.0, 4.0)
                parts.append((rng.choice([110, 125, 200, 230]), d))
                segs.append((round(t + 0.05, 3), round(t + d - 0.05, 3),
                             rng.choice(["Yes.", "and then", "No it wasn't.",
                                         "Where?", "about seven"])))
                t += d
            vb = VoiceBreak(VoiceMeter(voices(*parts)), min_jump=4.0)
            batch = paragraphify(segs, GAP, voice=vb)
            live = LiveTranscript(GAP, voice=vb,
                                  gaps_informative=_gaps_are_informative(segs))
            out = []
            for s in segs:
                out.extend(live.push([s]))
            out.extend(live.finish())
            self.assertEqual(out, batch, f"trial {trial}")


class TestWorker(unittest.TestCase):

    def test_on_by_default_and_off_when_asked(self):
        self.assertTrue(T.default_settings()["voice_breaks"])
        audio = voices((120, 2.0))
        self.assertIsNotNone(W._voice_break({"_audio": audio}))
        self.assertIsNone(W._voice_break({"_audio": audio,
                                          "voice_breaks": False}))
        self.assertIsNone(W._voice_break({"_audio": None}))

    def test_one_pass_paragraphs_break_at_the_new_voice(self):
        import threading
        audio = voices((120, 4.0), (220, 4.0))
        segs = [(0.2, 3.9, "I went home."), (4.05, 7.8, "Then what?")]
        params = {"_audio": audio, "input": "none.wav", "gap": GAP}
        paras, _ = W._paragraphs_with_speakers(
            segs, None, params, queue.Queue(), threading.Event())
        self.assertEqual(len([p for p in paras
                              if not p[0][2].startswith("[No audio")]), 2)


if __name__ == "__main__":
    unittest.main()
