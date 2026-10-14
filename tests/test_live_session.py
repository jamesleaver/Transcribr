"""A review session that opens empty and fills in as the engine works.

Covers the session's side of it: growing without disturbing what is
already there, sealing and labelling at the end, and the offers the
repair pass leaves against text somebody has corrected.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness  # noqa: F401

try:
    import transcribr as T
except ImportError as e:
    raise unittest.SkipTest(f"cannot import transcribr: {e}")


def para(start, text):
    return [(float(start), float(start) + 1.5, text)]


class _Case(unittest.TestCase):
    def setUp(self):
        self.broker = T.EventBroker()
        self.session = T.ReviewSession.open_live({
            "out_path": str(Path(_harness.CONFIG_DIR) / "x.txt"),
            "show_timestamp": True,
            "title": "Live",
            "output_format": "txt",
            "audio_path": None,
        }, self.broker)
        self.addCleanup(self.session.model.close)


class TestGrowing(_Case):
    def test_it_opens_empty_and_live(self):
        p = self.session.payload()
        self.assertTrue(p["live"])
        self.assertEqual(p["total"], 0)
        self.assertEqual(p["sealed_count"], 0)

    def test_the_delta_says_what_changed(self):
        d = self.session.grow([para(0, "First.")], para(2, "Second so far"))
        self.assertEqual(d["from"], 0)
        self.assertEqual(d["sealed_count"], 1)
        self.assertEqual(d["total"], 2)
        self.assertEqual(len(d["paragraphs"]), 2)

    def test_the_tail_is_marked_unsealed(self):
        self.session.grow([para(0, "First.")], para(2, "Still going"))
        payload = self.session.payload()
        self.assertTrue(payload["paragraphs"][0]["sealed"])
        self.assertFalse(payload["paragraphs"][1]["sealed"])

    def test_the_next_delta_starts_where_the_last_sealed(self):
        self.session.grow([para(0, "First.")], para(2, "tail"))
        d = self.session.grow([para(2, "Second.")], para(4, "tail two"))
        self.assertEqual(d["from"], 1, "it re-sent settled paragraphs")

    def test_an_edit_survives_the_transcript_growing_around_it(self):
        """The point of the exercise."""
        self.session.grow([para(0, "Engine text.")], para(2, "tail"))
        self.session.mutate(self.session.model.rev, "edit",
                            {"index": 0, "text": "My correction."})
        self.session.grow([para(2, "More engine text.")], para(4, "tail"))
        payload = self.session.payload()
        self.assertEqual(payload["paragraphs"][0]["body"], "My correction.")
        self.assertTrue(payload["paragraphs"][0]["mine"])
        self.assertEqual(payload["paragraphs"][1]["body"], "More engine text.")

    def test_a_speaker_set_during_the_run_survives_it(self):
        self.session.grow([para(0, "First."), para(2, "Second.")],
                          para(4, "tail"))
        self.session.mutate(self.session.model.rev, "speaker",
                            {"index": 0, "slot": "3"})
        self.session.grow([para(4, "Third.")], None)
        payload = self.session.payload()
        self.assertEqual(payload["paragraphs"][0]["speaker"], "3")
        self.assertTrue(payload["paragraphs"][0]["speaker_mine"])


class TestFinishing(_Case):
    def test_everything_seals(self):
        self.session.grow([para(0, "First.")], para(2, "tail"))
        payload = self.session.finish_live({"result": None})
        self.assertFalse(payload["live"])
        self.assertEqual(payload["sealed_count"], payload["total"])
        self.assertTrue(all(p["sealed"] for p in payload["paragraphs"]))

    def test_detected_speakers_fill_the_blanks(self):
        self.session.grow([para(0, "A."), para(2, "B.")], None)
        payload = self.session.finish_live(
            {"result": None, "preset_speakers": ["1", "2"]})
        self.assertEqual([p["speaker"] for p in payload["paragraphs"]],
                         ["1", "2"])

    def test_they_do_not_override_one_you_set(self):
        self.session.grow([para(0, "A."), para(2, "B.")], None)
        self.session.mutate(self.session.model.rev, "speaker",
                            {"index": 0, "slot": "5"})
        payload = self.session.finish_live(
            {"result": None, "preset_speakers": ["1", "2"]})
        self.assertEqual([p["speaker"] for p in payload["paragraphs"]],
                         ["5", "2"])

    def test_the_tail_becomes_editable(self):
        self.session.grow([], para(0, "Only paragraph"))
        self.session.finish_live({"result": None})
        rev = self.session.model.rev
        self.session.mutate(rev, "edit", {"index": 0, "text": "Edited."})
        self.assertEqual(self.session.payload()["paragraphs"][0]["body"],
                         "Edited.")


class TestSavingIsHeldBack(_Case):
    """save() ends the session - it writes the file, marks itself closed
    and clears the autosave. Doing that mid-run threw away the live
    transcript and every correction in it, and the run then opened a
    fresh session with the engine's own text."""

    def test_saving_while_the_engine_is_working_is_refused(self):
        self.session.grow([para(0, "First.")], para(2, "tail"))
        self.session.mutate(self.session.model.rev, "edit",
                            {"index": 0, "text": "My correction."})
        with self.assertRaises(T.ApiFail) as caught:
            self.session.save(self.session.model.rev, "labels")
        self.assertEqual(caught.exception.code, "still_running")

    def test_the_session_is_still_open_and_the_edit_still_there(self):
        self.session.grow([para(0, "First.")], para(2, "tail"))
        self.session.mutate(self.session.model.rev, "edit",
                            {"index": 0, "text": "My correction."})
        try:
            self.session.save(self.session.model.rev, "labels")
        except T.ApiFail:
            pass
        self.assertFalse(self.session.closed)
        self.assertEqual(self.session.payload()["paragraphs"][0]["body"],
                         "My correction.")

    def test_it_is_allowed_once_the_run_has_finished(self):
        self.session.grow([para(0, "First.")], None)
        self.session.finish_live({"result": None})
        out = self.session.save(self.session.model.rev, "no_labels")
        self.assertTrue(Path(out).exists())


class TestRefusalsAreReported(_Case):
    """Refusing by doing nothing and reporting success is
    indistinguishable from applying the change."""

    def setUp(self):
        super().setUp()
        self.session.grow([para(0, "Sealed.")], para(2, "Still going"))

    def test_editing_the_tail_says_so(self):
        with self.assertRaises(T.ApiFail) as caught:
            self.session.mutate(self.session.model.rev, "edit",
                                {"index": 1, "text": "nope"})
        self.assertEqual(caught.exception.code, "not_editable")

    def test_splitting_the_tail_says_so(self):
        with self.assertRaises(T.ApiFail) as caught:
            self.session.mutate(self.session.model.rev, "split",
                                {"index": 1, "offset": 4})
        self.assertEqual(caught.exception.code, "not_editable")

    def test_a_real_edit_still_works(self):
        self.session.mutate(self.session.model.rev, "edit",
                            {"index": 0, "text": "Fine."})
        self.assertEqual(self.session.payload()["paragraphs"][0]["body"],
                         "Fine.")


class TestOffers(_Case):
    def _with_offer(self):
        self.session.grow([para(0, "Engine text."), para(2, "Second.")], None)
        self.session.finish_live({"result": None})
        m = self.session.model
        self.session.mutate(m.rev, "edit",
                            {"index": 0, "text": "My correction."})
        m.offer_engine_run(0, 0, [para(0, "A better re-read.")],
                           source="repair")
        return m

    def test_the_offer_reaches_the_pane(self):
        self._with_offer()
        first = self.session.payload()["paragraphs"][0]
        self.assertEqual(first["body"], "My correction.")
        self.assertEqual(first["offer"]["text"], "A better re-read.")
        self.assertEqual(first["offer"]["source"], "repair")

    def test_accepting_it(self):
        m = self._with_offer()
        self.session.mutate(m.rev, "accept-offer", {"index": 0})
        first = self.session.payload()["paragraphs"][0]
        self.assertEqual(first["body"], "A better re-read.")
        self.assertIsNone(first["offer"])

    def test_keeping_yours(self):
        m = self._with_offer()
        self.session.mutate(m.rev, "dismiss-offer", {"index": 0})
        first = self.session.payload()["paragraphs"][0]
        self.assertEqual(first["body"], "My correction.")
        self.assertIsNone(first["offer"])

    def test_dismissing_nothing_is_refused(self):
        self.session.grow([para(0, "A.")], None)
        self.session.finish_live({"result": None})
        with self.assertRaises(T.ApiFail):
            self.session.mutate(self.session.model.rev, "dismiss-offer",
                                {"index": 0})

    def test_a_stale_offer_is_refused_rather_than_misapplied(self):
        m = self._with_offer()
        m.suggestions[m.ids[0]]["end_id"] = 99999      # span edited away
        with self.assertRaises(T.ApiFail):
            self.session.mutate(m.rev, "accept-offer", {"index": 0})
