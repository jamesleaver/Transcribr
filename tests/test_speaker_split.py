"""Paragraphs break wherever the recording says the speaker changed.

When each speaker has a channel, who is speaking is known before the
first word is transcribed, so a paragraph need never hold two people.
The live pane must still show exactly what waiting would have given.
"""

import queue
import random
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness  # noqa: F401

try:
    import transcribr.worker as W
    from transcribr.live import LiveTranscript
    from transcribr.paragraphs import paragraphify, split_at_speaker_changes
    from test_channels import _Signals, _write_wav
except ImportError as e:
    raise unittest.SkipTest(f"cannot import: {e}")

GAP = 1.0
TURNS = [(0.0, 4.0, 0), (4.3, 9.0, 1), (9.2, 12.0, 0)]


def words_for(t0, t1, text):
    ws = text.split()
    step = (t1 - t0) / len(ws)
    return [(t0 + i * step, t0 + (i + 1) * step, " " + w, 0.9)
            for i, w in enumerate(ws)]


class TestSplitSegments(unittest.TestCase):

    def test_a_segment_spanning_two_speakers_is_cut(self):
        """Whisper often runs a question and its answer into one
        segment: "Where were you? At home." - two people."""
        words = (words_for(2.0, 4.0, "Where were you?")
                 + words_for(4.3, 6.0, "At home, all night."))
        seg = (2.0, 6.0, "Where were you? At home, all night.")
        out = split_at_speaker_changes([seg], words, TURNS)
        self.assertEqual([s[2] for s in out],
                         ["Where were you?", "At home, all night."])
        self.assertEqual((out[1][0], out[1][1]), (4.3, 6.0))

    def test_a_backchannel_does_not_split(self):
        """A "mm" from the other side, shorter than a turn, stays put."""
        turns = [(0.0, 3.0, 0), (3.0, 3.25, 1), (3.25, 8.0, 0)]
        words = words_for(0.0, 8.0, "and then I went down the road "
                                    "to the shop and bought milk")
        seg = (0.0, 8.0, "and then I went down the road to the shop "
                         "and bought milk")
        self.assertEqual(split_at_speaker_changes([seg], words, turns),
                         [seg])

    def test_without_word_timings_nothing_changes(self):
        seg = (2.0, 6.0, "Where were you? At home.")
        self.assertEqual(split_at_speaker_changes([seg], [], TURNS), [seg])


class TestParagraphs(unittest.TestCase):

    def test_a_change_of_speaker_breaks_even_mid_sentence(self):
        """"I went to the" / "shop?" reads as one sentence to the text
        rules, which never break there - but it is two people."""
        segs = [(0.5, 3.8, "I went to the"), (4.4, 5.0, "shop?")]
        self.assertEqual(len(paragraphify(segs, GAP)), 1)
        self.assertEqual(len(paragraphify(segs, GAP, turns=TURNS)), 2)

    def test_same_speaker_is_left_to_the_usual_rules(self):
        segs = [(4.4, 5.5, "I stayed in"), (5.6, 8.0, "all night long")]
        self.assertEqual(len(paragraphify(segs, GAP, turns=TURNS)), 1)


def _dialogue(rng, n):
    """Segments and words over alternating speakers, where segments
    sometimes straddle a change."""
    segs, words, turns = [], [], []
    t, who = 0.0, 0
    for _ in range(n):
        dur = rng.uniform(1.0, 5.0)
        turns.append((t, t + dur, who))
        t += dur + rng.choice([0.0, 0.1, 0.4, 1.2])
        who ^= 1
    vocab = ["yes", "no", "the car", "I went", "home", "on Friday",
             "and then", "about seven"]
    cursor = 0.0
    end = turns[-1][1]
    while cursor < end - 0.5:
        dur = min(rng.uniform(0.8, 6.0), end - cursor)
        text = " ".join(rng.choice(vocab) for _ in range(rng.randint(2, 7)))
        text += rng.choice([".", "?", "", ","])
        ws = words_for(cursor, cursor + dur, text)
        segs.append((cursor, cursor + dur, text))
        words.extend(ws)
        cursor += dur + rng.choice([0.0, 0.05, 0.5, 1.5])
    return segs, words, turns


class TestLiveMatchesBatch(unittest.TestCase):

    def test_on_many_random_dialogues(self):
        """The one judgement the live path makes from a sample - whether
        pauses mean anything - is pinned to the batch answer, as in
        test_paragraph_stream; everything else must agree exactly."""
        from transcribr.paragraphs import (_gaps_are_informative,
                                           _refine_segment_times)
        rng = random.Random(7)
        for trial in range(200):
            segs, words, turns = _dialogue(rng, rng.randint(2, 25))
            batch = paragraphify(segs, GAP, words=words, turns=turns)
            gi = _gaps_are_informative(split_at_speaker_changes(
                _refine_segment_times(segs, words), words, turns))
            live = LiveTranscript(GAP, turns=turns, gaps_informative=gi)
            out = []
            for seg in segs:
                mine = [w for w in words if seg[0] <= w[0] < seg[1]]
                out.extend(live.push([seg], words=mine))
            out.extend(live.finish())
            self.assertEqual(out, batch, f"trial {trial}")


class TestWorker(unittest.TestCase):
    """End to end on a real two-channel file: the one-pass path splits,
    and the channel analysis runs once and is reused."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.sig = _Signals(seconds=30, seed=3)
        self.path = _write_wav(self.tmp / "two.wav",
                               [self.sig.a + self.sig.noise(),
                                self.sig.b + self.sig.noise()])

    def test_one_pass_paragraphs_follow_the_channels(self):
        truth = self.sig.truth[:4]
        # one engine segment per pair of turns, as if Whisper had run a
        # question and its answer together
        segs, words = [], []
        for (a0, a1, _), (b0, b1, _) in zip(truth[::2], truth[1::2]):
            wa = words_for(a0 + 0.1, a1 - 0.1, "did you see him there?")
            wb = words_for(b0 + 0.1, b1 - 0.1, "no I did not see anyone")
            segs.append((a0 + 0.1, b1 - 0.1,
                         "did you see him there? no I did not see anyone"))
            words += wa + wb
        result = {"segments": [
            {"start": s[0], "end": s[1], "text": s[2],
             "words": [{"start": w[0], "end": w[1], "word": w[2],
                        "probability": 0.9} for w in words
                       if s[0] <= w[0] < s[1]]} for s in segs]}
        params = {"input": str(self.path), "gap": GAP, "diarize": False}
        paras, _ = W._paragraphs_with_speakers(
            segs, result, params, queue.Queue(), threading.Event())
        texts = [" ".join(s[2] for s in p).strip() for p in paras
                 if not p[0][2].startswith("[No audio")]
        self.assertEqual(texts, ["did you see him there?",
                                 "no I did not see anyone"] * 2)
        self.assertIsNotNone(params["_channel_turns"])

    def test_labelling_reuses_the_channel_analysis(self):
        from unittest import mock
        params = {"input": str(self.path)}
        W._channel_turns(params, queue.Queue())
        with mock.patch("transcribr.diarize.separated_channel_turns",
                        side_effect=AssertionError("analysed twice")):
            from transcribr.diarize import speaker_turns
            turns = speaker_turns(params, None, queue.Queue())
        self.assertEqual(turns, params["_channel_turns"])

    def test_mono_files_are_untouched(self):
        mono = _write_wav(self.tmp / "mono.wav", [self.sig.a + self.sig.b])
        params = {"input": str(mono)}
        self.assertIsNone(W._channel_turns(params, queue.Queue()))


if __name__ == "__main__":
    unittest.main()
