"""Assembling a transcript while the engine is still running.

Same governing test as the paragraph stream: what the pane shows during
a run has to be what the file would have contained if you had waited.
So every case here builds a transcript both ways and demands they
match.
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
    from transcribr.live import LiveTranscript
    from transcribr.paragraphs import _gaps_are_informative
except ImportError as e:
    raise unittest.SkipTest(f"cannot import transcribr: {e}")

GAP = 1.5


def batch(segments, spans):
    """What 0.9.15 produced: one pass over everything at the end."""
    kept, _dropped = T._drop_segments_in_silence(segments, spans)
    paragraphs = T.paragraphify(kept, GAP)
    merged, _letters = T._splice_no_audio_markers(paragraphs, None, spans)
    return merged


def live(segments, spans, chunk=1):
    lt = LiveTranscript(GAP, silence_spans=spans,
                        gaps_informative=_gaps_are_informative(
                            [s for s in segments
                             if not T.live.segment_is_phantom(s, spans)]))
    out = []
    for i in range(0, len(segments), chunk):
        out.extend(lt.push(segments[i:i + chunk]))
    out.extend(lt.finish())
    return out


def speechlike(rng, n, start=0.0):
    enders = [".", "?", "!", ",", "", " -"]
    words = ["the witness", "said", "on the fourteenth", "yes",
             "I arrived", "and then", "no"]
    segs, t = [], start
    for _ in range(n):
        dur = rng.uniform(0.6, 3.0)
        text = " ".join(rng.choice(words) for _ in range(rng.randint(1, 5)))
        segs.append((round(t, 3), round(t + dur, 3),
                     text + rng.choice(enders)))
        t += dur + rng.choice([0.0, 0.3, 1.0, 4.0])
    return segs


class TestLiveMatchesBatch(unittest.TestCase):
    def test_with_no_dead_air(self):
        rng = random.Random(1)
        for _ in range(60):
            segs = speechlike(rng, rng.randint(1, 40))
            for chunk in (1, 3, 11):
                self.assertEqual(live(segs, [], chunk), batch(segs, []))

    def test_with_dead_air_in_the_middle(self):
        rng = random.Random(2)
        for _ in range(60):
            first = speechlike(rng, 12)
            end = first[-1][1]
            spans = [(end + 1.0, end + 20.0)]
            second = speechlike(rng, 12, start=end + 21.0)
            segs = first + second
            for chunk in (1, 4):
                self.assertEqual(live(segs, spans, chunk),
                                 batch(segs, spans))

    def test_with_dead_air_at_the_very_end(self):
        """A marker after the last paragraph only gets emitted by
        finish(), which is the case a pointer walk is easiest to get
        wrong."""
        segs = speechlike(random.Random(3), 8)
        end = segs[-1][1]
        spans = [(end + 2.0, end + 30.0)]
        self.assertEqual(live(segs, spans), batch(segs, spans))

    def test_with_dead_air_before_any_speech(self):
        segs = speechlike(random.Random(4), 6, start=40.0)
        spans = [(0.0, 35.0)]
        self.assertEqual(live(segs, spans), batch(segs, spans))

    def test_with_several_spans(self):
        rng = random.Random(5)
        segs = speechlike(rng, 30)
        spans = [(20.0, 26.0), (55.0, 61.0), (90.0, 97.0)]
        for chunk in (1, 7):
            self.assertEqual(live(segs, spans, chunk), batch(segs, spans))

    def test_a_recording_that_is_all_dead_air(self):
        segs = [(5.0, 6.0, "phantom text")]
        spans = [(0.0, 60.0)]
        self.assertEqual(live(segs, spans), batch(segs, spans))


class TestPhantomText(unittest.TestCase):
    def test_text_invented_inside_dead_air_is_dropped(self):
        segs = [(0.0, 2.0, "Real speech."),
                (10.0, 12.0, "Thank you for watching!"),
                (30.0, 32.0, "More real speech.")]
        spans = [(9.0, 25.0)]
        lt = LiveTranscript(GAP, silence_spans=spans, gaps_informative=True)
        out = lt.push(segs)
        out.extend(lt.finish())
        bodies = [" ".join(s[2] for s in p) for p in out]
        self.assertNotIn("Thank you for watching!", bodies)
        self.assertEqual(lt.dropped, 1)

    def test_speech_brushing_the_edge_of_a_span_is_kept(self):
        segs = [(8.0, 12.0, "Speech that runs into the gap.")]
        spans = [(10.0, 30.0)]
        lt = LiveTranscript(GAP, silence_spans=spans, gaps_informative=True)
        lt.push(segs)
        self.assertEqual(lt.dropped, 0)


class TestSealing(unittest.TestCase):
    def test_the_open_paragraph_is_withheld(self):
        lt = LiveTranscript(GAP, gaps_informative=True)
        self.assertEqual(lt.push([(0.0, 1.0, "First.")]), [])
        self.assertIsNotNone(lt.tail)

    def test_it_is_handed_over_once_something_seals_it(self):
        lt = LiveTranscript(GAP, gaps_informative=True)
        lt.push([(0.0, 1.0, "First.")])
        sealed = lt.push([(9.0, 10.0, "Second.")])
        self.assertEqual(len(sealed), 1)
        self.assertIsNotNone(lt.tail)

    def test_the_count_matches_what_was_handed_over(self):
        rng = random.Random(6)
        segs = speechlike(rng, 25)
        spans = [(200.0, 210.0)]
        lt = LiveTranscript(GAP, silence_spans=spans, gaps_informative=True)
        total = 0
        for seg in segs:
            total += len(lt.push([seg]))
        total += len(lt.finish())
        self.assertEqual(total, lt.paragraph_count)
