"""Model loops of whole clauses, anywhere in a paragraph.

Every positive case below has the shape of a real engine loop, and
every negative is ordinary human repetition - the detector must tell
them apart, because a flagged paragraph is
re-transcribed and highlighted for review."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness  # noqa: F401

try:
    import transcribr as T
    from transcribr.worker import _has_repetition_loop
except ImportError as e:
    raise unittest.SkipTest(f"cannot import transcribr: {e}")


class TestLoopsTheOldRuleMissed(unittest.TestCase):
    """Each of these reached a saved transcript because the old rule
    only knew 1-4 word phrases that dominate a paragraph."""

    LOOPS = [
        # a seven-word clause, looped
        "So I went round and there was a car in the drive there was a "
        "car in the drive there was a car in the drive there was a car "
        "in the drive there was a car in the drive and then we left.",
        # a looped clause carried across sentence ends
        "He was quite out of breath. Silence, you knave, cried out of breath "
        "silence you knave cried out of breath silence you knave cried out "
        "of breath silence you knave cried Montfichet.",
        # a whole sentence, looped
        "They're buying something custom. This is not off a rack. This is "
        "not off a rack. This is not off a rack. This is not off a rack.",
        # a three-word loop, repeated enough to be one
        "Okay, let me just let me just let me just let me just let me just "
        "let me just find the page.",
    ]

    def test_each_is_flagged(self):
        for text in self.LOOPS:
            with self.subTest(text=text[:40]):
                self.assertTrue(_has_repetition_loop(text))

    def test_flagged_paragraphs_reach_detection(self):
        paras = [[(0.0, 5.0, "An ordinary opening paragraph of evidence.")],
                 [(5.0, 20.0, self.LOOPS[0])],
                 [(20.0, 25.0, "And an ordinary closing one.")]]
        self.assertEqual(T.detect_hallucinations(paras), [1])


class TestRealRepetitionIsLeftAlone(unittest.TestCase):
    """People repeat themselves. None of these may be flagged."""

    GENUINE = [
        # a stammer
        "And the question is whether it is a it is a it is a it is a "
        "message that she is being compelled to create.",
        "No, no, no. That is not what I said.",
        "Thank you. Thank you, Your Honour.",
        "I object. I object, Your Honour, to the form of the question.",
        "Yes. Yes. Yes, I did. I did see him that night.",
        # a clause said twice for emphasis is emphasis, not a loop
        "It was not me. It was not me. I was at home the whole evening.",
    ]

    def test_none_is_flagged(self):
        for text in self.GENUINE:
            with self.subTest(text=text[:40]):
                self.assertFalse(_has_repetition_loop(text))


if __name__ == "__main__":
    unittest.main()
