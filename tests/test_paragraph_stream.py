"""Grouping segments into paragraphs as they arrive.

The live review pane shows paragraphs before the run has finished. That
is only honest if what you watch appear is what you would have got by
waiting - so the governing test here is that streaming and batch agree,
on every input we can think of.
"""

import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness  # noqa: F401

try:
    import transcribr as T
    from transcribr.paragraphs import ParagraphStream, _gaps_are_informative
except ImportError as e:
    raise unittest.SkipTest(f"cannot import transcribr: {e}")

GAP = 1.5


def _stream(segments, gaps_informative, chunk=1):
    """Run segments through the stream in chunks, as the engine would."""
    s = ParagraphStream(GAP, gaps_informative=gaps_informative)
    out = []
    for i in range(0, len(segments), chunk):
        out.extend(s.push(segments[i:i + chunk]))
    out.extend(s.finish())
    return out


def _batch(segments, gaps_informative):
    s = ParagraphStream(GAP, gaps_informative=gaps_informative)
    out = s.push(segments)
    out.extend(s.finish())
    return out


def _speechlike(rng, n):
    """Segments with the shapes that drive the heuristics: real pauses,
    sentence endings, questions, and mid-sentence continuations."""
    enders = [".", "?", "!", ",", "", " -", "..."]
    words = ["the witness", "said", "on the fourteenth", "yes",
             "I arrived", "and then", "no", "about seven"]
    segs, t = [], 0.0
    for _ in range(n):
        dur = rng.uniform(0.6, 4.0)
        text = " ".join(rng.choice(words) for _ in range(rng.randint(1, 6)))
        if rng.random() < 0.3:
            text = text.capitalize()
        segs.append((round(t, 3), round(t + dur, 3),
                     text + rng.choice(enders)))
        t += dur + rng.choice([0.0, 0.05, 0.4, 1.0, 2.0, 6.0])
    return segs


class TestStreamingMatchesBatch(unittest.TestCase):
    """Same segments and the same gaps judgement must give the same
    paragraphs, however the segments were delivered."""

    def test_on_many_random_transcripts(self):
        rng = random.Random(20260917)
        for trial in range(200):
            segs = _speechlike(rng, rng.randint(1, 60))
            informative = _gaps_are_informative(segs)
            want = _batch(segs, informative)
            for chunk in (1, 2, 5, 17):
                got = _stream(segs, informative, chunk=chunk)
                self.assertEqual(
                    got, want,
                    f"trial {trial}, chunk {chunk}: streaming diverged")

    def test_on_wall_to_wall_segments(self):
        """No word timestamps: every segment abuts the next, so the gap
        rules never fire and punctuation is the only signal."""
        segs, t = [], 0.0
        for i in range(40):
            segs.append((t, t + 2.0, f"Sentence number {i}."))
            t += 2.0
        self.assertFalse(_gaps_are_informative(segs))
        self.assertEqual(_stream(segs, False), _batch(segs, False))

    def test_across_the_sixty_second_cap(self):
        """A monologue with no cues is broken by the length cap, which
        is the one rule that depends on where the paragraph started."""
        segs = [(t, t + 3.0, "and then he continued speaking")
                for t in [i * 3.0 for i in range(60)]]
        want = _batch(segs, True)
        self.assertGreater(len(want), 1, "the cap should have fired")
        self.assertEqual(_stream(segs, True), want)

    def test_a_single_segment(self):
        segs = [(0.0, 2.0, "Just the one.")]
        self.assertEqual(_stream(segs, True), _batch(segs, True))

    def test_no_segments_at_all(self):
        self.assertEqual(_stream([], True), [])
        self.assertEqual(_batch([], True), [])


class TestSealing(unittest.TestCase):
    def test_the_open_paragraph_is_never_sealed_early(self):
        """A paragraph is only final once the next segment has started
        a new one - that is what makes it safe to edit."""
        s = ParagraphStream(GAP, gaps_informative=True)
        self.assertEqual(s.push([(0.0, 1.0, "First sentence.")]), [])
        self.assertIsNotNone(s.tail)
        # A long silence starts a new paragraph, sealing the first.
        sealed = s.push([(9.0, 10.0, "Second sentence.")])
        self.assertEqual(len(sealed), 1)
        self.assertEqual(sealed[0][0][2], "First sentence.")

    def test_the_tail_grows_until_it_breaks(self):
        s = ParagraphStream(GAP, gaps_informative=True)
        s.push([(0.0, 1.0, "One")])
        s.push([(1.0, 2.0, "two")])
        self.assertEqual(len(s.tail), 2)

    def test_finish_seals_what_is_open(self):
        s = ParagraphStream(GAP, gaps_informative=True)
        s.push([(0.0, 1.0, "Only paragraph.")])
        self.assertEqual(len(s.finish()), 1)
        self.assertIsNone(s.tail)

    def test_nothing_is_emitted_twice(self):
        rng = random.Random(7)
        segs = _speechlike(rng, 40)
        s = ParagraphStream(GAP, gaps_informative=True)
        seen = []
        for seg in segs:
            seen.extend(s.push([seg]))
        seen.extend(s.finish())
        flat = [tuple(x) for para in seen for x in para]
        self.assertEqual(len(flat), len(set(flat)), "a segment was emitted twice")
        self.assertEqual(len(flat), len(segs), "a segment went missing")


class TestTheGapsSample(unittest.TestCase):
    """When the caller has not already decided, the stream holds its
    first segments back until it can settle the one judgement that is
    not local."""

    def test_nothing_is_emitted_before_the_sample_is_complete(self):
        s = ParagraphStream(GAP)          # no decision supplied
        for i in range(5):
            self.assertEqual(s.push([(i * 9.0, i * 9.0 + 1.0, f"Line {i}.")]),
                             [], "emitted before it could know")
        self.assertIsNone(s.gaps_informative)

    def test_the_sample_settles_it_and_the_paragraphs_arrive(self):
        s = ParagraphStream(GAP, sample_size=4)
        out = []
        for i in range(4):
            out.extend(s.push([(i * 9.0, i * 9.0 + 1.0, f"Line {i}.")]))
        self.assertIsNotNone(s.gaps_informative)
        self.assertTrue(out, "held segments were not released")

    def test_the_held_segments_are_still_shown(self):
        """Holding them back from sealing is not a reason to show a
        blank pane for the first eight segments of a run."""
        s = ParagraphStream(GAP)
        s.push([(0.0, 1.0, "First words.")])
        self.assertIsNotNone(s.tail)
        self.assertEqual(s.tail[0][2], "First words.")
        s.push([(9.0, 10.0, "More words.")])
        self.assertEqual(len(s.tail), 2)

    def test_the_tail_moves_to_the_open_paragraph_once_settled(self):
        s = ParagraphStream(GAP, sample_size=2)
        s.push([(0.0, 1.0, "One."), (9.0, 10.0, "Two.")])
        self.assertIsNotNone(s.gaps_informative)
        self.assertEqual(s.tail[0][2], "Two.")

    def test_a_short_recording_still_comes_out(self):
        """Fewer segments than the sample: finish() has to settle it."""
        s = ParagraphStream(GAP, sample_size=50)
        s.push([(0.0, 1.0, "Short.")])
        self.assertEqual(len(s.finish()), 1)

    def test_the_sample_agrees_with_the_whole_on_real_shapes(self):
        """The judgement is about the engine's output format, not the
        content, so a sample should reach the same answer."""
        rng = random.Random(99)
        agreed = 0
        for _ in range(60):
            segs = _speechlike(rng, 40)
            whole = _gaps_are_informative(segs)
            sample = _gaps_are_informative(segs[:12])
            agreed += (whole == sample)
        self.assertGreaterEqual(agreed, 54, "sampling is too unreliable")


class TestParagraphifyIsUnchanged(unittest.TestCase):
    """paragraphify() is a wrapper over the stream now; its callers must
    not be able to tell."""

    def test_it_still_groups_the_obvious_case(self):
        segs = [(0.0, 1.0, "First thing."), (8.0, 9.0, "Second thing.")]
        paras = T.paragraphify(segs, GAP)
        self.assertEqual(len(paras), 2)

    def test_it_uses_the_whole_set_for_the_gaps_judgement(self):
        """Not a sample - the whole transcript is in hand here."""
        segs, t = [], 0.0
        for i in range(30):
            segs.append((t, t + 2.0, f"Sentence {i}."))
            t += 2.0
        self.assertEqual(T.paragraphify(segs, GAP),
                         _batch(segs, _gaps_are_informative(segs)))
