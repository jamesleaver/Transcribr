"""Speaker cues without voiceprints (transcribr/turns.py)."""

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness  # noqa: F401

try:
    import numpy as np
    from transcribr import turns as TU
except ImportError as e:
    raise unittest.SkipTest(f"cannot import: {e}")


def w(start, end, text):
    return (start, end, text, 0.9)


class TestSentences(unittest.TestCase):

    def test_a_segment_is_cut_at_each_sentence_end(self):
        """A speaker change inside one engine segment needs a boundary
        to land on: "I was at home. All night?" is two sentences."""
        segs = [(0.0, 3.0, "I was at home. All night?")]
        words = [w(0.0, 0.3, " I"), w(0.3, 0.5, " was"), w(0.5, 0.7, " at"),
                 w(0.7, 1.2, " home."), w(1.8, 2.2, " All"),
                 w(2.2, 2.9, " night?")]
        s = TU.sentences(segs, words)
        self.assertEqual([x[2] for x in s], ["I was at home.", "All night?"])
        self.assertEqual((s[1][0], s[1][1]), (1.8, 2.9))

    def test_segments_without_word_timings_stay_whole(self):
        s = TU.sentences([(0.0, 2.0, "Yes. No.")], [])
        self.assertEqual([x[2] for x in s], ["Yes. No."])

    def test_unfinished_sentence_is_kept(self):
        segs = [(0.0, 1.0, "and then")]
        s = TU.sentences(segs, [w(0.0, 0.4, " and"), w(0.4, 1.0, " then")])
        self.assertEqual([x[2] for x in s], ["and then"])


class TestTextCues(unittest.TestCase):

    def cue(self, prev, curr, name):
        return TU.text_cues(prev, curr)[name]

    def test_you_answered_in_i(self):
        self.assertEqual(self.cue("Did you go back?", "I went back after dinner.",
                                  "you_to_i"), 1)
        self.assertEqual(self.cue("We went back.", "I went back after dinner.",
                                  "you_to_i"), 0)

    def test_forms_of_address(self):
        self.assertEqual(self.cue("Thank you.", "Your Honour, may I?",
                                  "address_open"), 1)
        self.assertEqual(self.cue("That is so, Officer.", "Right.",
                                  "address_close"), 1)
        self.assertEqual(self.cue("It was dark.", "So I left.",
                                  "address_open"), 0)

    def test_tag_and_echo_questions(self):
        self.assertEqual(self.cue("You were there, weren't you?", "Yes.",
                                  "tag_question"), 1)
        self.assertEqual(self.cue("I saw him on Friday.", "Friday?", "echo"), 1)
        self.assertEqual(self.cue("I saw him on Friday.", "Why?", "echo"), 0)

    def test_subtitle_dash(self):
        self.assertEqual(self.cue("Yes.", "- No, it wasn't.", "dash_open"), 1)

    def test_interview_formula(self):
        self.assertEqual(self.cue("For the purposes of the recording, what is "
                                  "your name?", "John.", "formula_prev"), 1)

    def test_roles(self):
        self.assertGreater(TU.asks("Do you agree that you were there?"), 0)
        self.assertEqual(TU.asks("I was there."), 0)
        self.assertGreater(TU.answers("I don't remember."), 0)


class TestVoice(unittest.TestCase):

    def tone(self, hz, seconds, amp=0.3):
        t = np.arange(int(seconds * TU.SR)) / TU.SR
        # a voice-like tone: fundamental plus harmonics
        return (amp * (np.sin(2 * np.pi * hz * t)
                       + 0.5 * np.sin(4 * np.pi * hz * t)
                       + 0.25 * np.sin(6 * np.pi * hz * t))).astype(np.float32)

    def test_pitch_is_measured(self):
        track = TU.voice_track(self.tone(120, 1.0))
        hz, _db = TU.voice_of(track, 0.1, 0.9)
        self.assertAlmostEqual(hz, 120, delta=4)

    def test_pitch_jump_between_voices(self):
        """A man's voice then a child's: about ten semitones apart."""
        audio = np.concatenate([self.tone(120, 1.0), self.tone(220, 1.0)])
        track = TU.voice_track(audio)
        cues = TU.voice_cues(track, (0.1, 0.9, "one two", []),
                             (1.1, 1.9, "three four", []))
        self.assertAlmostEqual(cues["pitch_jump"],
                               12 * math.log2(220 / 120), delta=0.7)

    def test_silence_has_no_pitch(self):
        track = TU.voice_track(np.zeros(TU.SR, dtype=np.float32))
        self.assertIsNone(TU.voice_of(track, 0.1, 0.9)[0])


class TestTwoPartyRoles(unittest.TestCase):

    def test_plain_question_and_answer(self):
        s = [(0, 2, "Where were you on Friday night?", []),
             (2.5, 4, "I was at home.", []),
             (4.1, 5, "All night?", []),
             (5.3, 7, "Yes, all night.", []),
             (7.2, 9, "Did you go out at all?", []),
             (9.5, 12, "No. I watched TV.", [])]
        self.assertEqual([r for r, _ in TU.two_party_roles(s)],
                         [0, 1, 0, 1, 0, 1])

    def test_pitch_settles_what_text_cannot(self):
        """Two voices far apart in pitch: statements with no text cue
        at all are still given to the right voice."""
        tone = TestVoice.tone
        parts, sents, t = [], [], 0.0
        script = [(120, "Where did you go?"), (220, "To the shops."),
                  (120, "Okay."), (120, "Tell me about the car."),
                  (220, "It was red."), (120, "Right."),
                  (120, "And after that?"), (220, "Home."),
                  (120, "Thanks."), (220, "Fine.")]
        for hz, text in script:
            parts.append(tone(self, hz, 1.0))
            sents.append((t + 0.1, t + 0.9, text, []))
            t += 1.0
        truth = [0 if hz == 120 else 1 for hz, _ in script]
        track = TU.voice_track(np.concatenate(parts))
        roles = [r for r, _ in TU.two_party_roles(sents, track)]
        self.assertEqual(roles, truth)
        # and the words alone could not have done it
        self.assertNotEqual([r for r, _ in TU.two_party_roles(sents)], truth)

    def test_empty(self):
        self.assertEqual(TU.two_party_roles([]), [])


if __name__ == "__main__":
    unittest.main()
