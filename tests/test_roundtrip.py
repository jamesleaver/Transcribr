"""A transcript saved and reopened must be the same transcript.

The saved file is the work product - the thing that gets filed, served
and relied on - so anything lost between write_paragraphs_to_file and
read_paragraphs_from_file is lost for good, and lost silently: the
reviewer reopens their transcript and a paragraph simply is not there.

These are all cases where it was. Every one comes down to the same
thing: the plain-text format is ambiguous, and the parser was guessing
where the writer could simply have said.
"""

import itertools
import random
import tempfile
import unittest
from pathlib import Path

import _harness  # noqa: F401  - isolates the config/log directories

from transcribr.writers import write_paragraphs_to_file
from transcribr.readers import read_paragraphs_from_file


def have(module):
    try:
        __import__(module)
        return True
    except ImportError:
        return False


FORMATS = [("txt", True)]
if have("docx"):
    FORMATS.append(("docx", True))


class RoundTripCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)

    def trip(self, paragraphs, *, fmt="txt", speakers=None, title=None,
             show_timestamp=True, hide_ts=None):
        path = Path(self.tmp.name) / f"t.{fmt}"
        write_paragraphs_to_file(
            paragraphs, path, show_timestamp=show_timestamp, title=title,
            output_format=fmt, speakers=speakers, hide_ts=hide_ts)
        return read_paragraphs_from_file(str(path))

    def bodies(self, parsed):
        return [" ".join(s[2] for s in p) for p in parsed["paragraphs"]]


class FirstParagraphVersusTitle(RoundTripCase):
    """A bare first line is a title or a paragraph, and the file used to
    say which only by accident.

    With no title, no speaker and no timestamp, the first paragraph
    looked exactly like a title - so it was read back as one and the
    paragraph was gone. Both "hide this stamp" and "no timestamps at
    all" are ordinary review-pane choices, and an unattributed opening
    paragraph is the norm rather than the exception.
    """

    PARAS = [[(0.0, 5.0, "First paragraph of the interview")],
             [(6.0, 9.0, "Second paragraph of the interview")]]

    def test_every_combination_survives(self):
        for fmt, _ in FORMATS:
            for title, show_ts, hide0, sp0 in itertools.product(
                    [None, "A title"], [True, False], [False, True],
                    [None, "THE COURT"]):
                with self.subTest(fmt=fmt, title=title, show_ts=show_ts,
                                  hide_first=hide0, speaker=sp0):
                    got = self.trip(
                        self.PARAS, fmt=fmt, title=title,
                        show_timestamp=show_ts,
                        speakers=[sp0, "WITNESS"],
                        hide_ts=[hide0, False])
                    self.assertEqual(
                        self.bodies(got),
                        ["First paragraph of the interview",
                         "Second paragraph of the interview"])
                    self.assertEqual(got.get("title"), title)

    def test_a_real_title_is_still_read_as_one(self):
        got = self.trip(self.PARAS, title="R v CAVILL", speakers=[None, None])
        self.assertEqual(got["title"], "R v CAVILL")
        self.assertEqual(len(got["paragraphs"]), 2)


class BodiesThatLookLikeSyntax(RoundTripCase):
    """Transcript text that reads like the format's own punctuation.

    None of these is exotic in spoken evidence, and each used to cost
    either a whole paragraph or its opening words.
    """

    CASES = {
        "ends with a colon":
            "And then he turned to me and said:",
        "is itself a speaker-looking line":
            "THE COURT:",
        "opens with something shaped like a stamp":
            "[00:12] was the time on the clock in the hallway.",
        "opens with a bracketed aside":
            "[Witness indicates] It was over there, by the gate.",
        "quotes the app's own disclaimer":
            "Transcribed using Transcribr "
            "[https://github.com/jamesleaver/Transcribr].",
        "contains a blank line":
            "First line.\n\nSecond line after a blank.",
        "is only punctuation": "...",
        "is a bare number": "42",
        "looks like a time of day": "9:45 was when it happened.",
    }

    def test_each_body_survives_every_document_shape(self):
        for fmt, _ in FORMATS:
            for name, body in self.CASES.items():
                for title, show_ts, sp in itertools.product(
                        [None, "A title"], [True, False],
                        [None, "THE COURT"]):
                    with self.subTest(fmt=fmt, case=name, title=title,
                                      show_ts=show_ts, speaker=sp):
                        got = self.trip(
                            [[(0.0, 5.0, body)],
                             [(6.0, 9.0, "A following paragraph.")]],
                            fmt=fmt, title=title, show_timestamp=show_ts,
                            speakers=[sp, "WITNESS"])
                        # A body is one paragraph: its internal
                        # whitespace is collapsed, its words are not.
                        self.assertEqual(
                            self.bodies(got),
                            [" ".join(body.split()), "A following paragraph."])

    def test_the_trailing_disclaimer_is_still_dropped(self):
        got = self.trip([[(0.0, 5.0, "The only paragraph.")]],
                        speakers=[None])
        self.assertEqual(self.bodies(got), ["The only paragraph."])


class Fuzz(RoundTripCase):
    """Randomised transcripts, because the cases above were all found by
    a fuzz rather than by thinking of them."""

    WORDS = ["the", "witness", "said", "Mr", "Cavill", "arrived", "at", "9",
             "o'clock", "and", "left", "—", "again", "…", "naïve", "café",
             "Müller", "O'Brien", "Smith-Jones", "12th", "March", "2026"]
    NAMES = ["CONSTABLE MACKLEBUM", "DETECTIVE NGUYEN", "MS O'BRIEN",
             "THE COURT", "WITNESS", None]

    def case(self, rng):
        paragraphs, speakers, hide = [], [], []
        t = 0.0
        for _ in range(rng.randint(1, 10)):
            dur = rng.uniform(1.0, 9.0)
            n = rng.randint(3, 18)
            text = " ".join(rng.choice(self.WORDS) for _ in range(n))
            text = text[0].upper() + text[1:] + rng.choice(
                [".", "?", "!", "", "...", ":"])
            paragraphs.append([(t, t + dur, text)])
            speakers.append(rng.choice(self.NAMES))
            hide.append(rng.random() < 0.15)
            t += dur + rng.uniform(0.0, 3.0)
        return paragraphs, speakers, hide

    def test_two_hundred_transcripts_per_format(self):
        for fmt, _ in FORMATS:
            rng = random.Random(20260917)
            for i in range(200):
                paragraphs, speakers, hide = self.case(rng)
                with self.subTest(fmt=fmt, i=i):
                    got = self.trip(
                        paragraphs, fmt=fmt, speakers=speakers,
                        hide_ts=hide,
                        title=rng.choice([None, "Interview 3"]),
                        show_timestamp=rng.random() < 0.85)
                    self.assertEqual(
                        self.bodies(got),
                        [" ".join(s[2] for s in p) for p in paragraphs])
                    self.assertEqual(list(got["speakers"]), speakers)


if __name__ == "__main__":
    unittest.main()
