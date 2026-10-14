"""Who last wrote each paragraph, and what the engine may do about it.

A transcription that keeps running while somebody edits the transcript
needs one rule enforced everywhere: an automated writer may fill in a
blank, but it may never silently replace a correction a person made on
purpose. These tests are that rule.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness  # noqa: F401  (redirects the config dir; must precede transcribr)

try:
    import transcribr as T
except ImportError as e:
    raise unittest.SkipTest(f"cannot import transcribr: {e}")


def _para(start, text):
    return [(float(start), float(start) + 1.0, text)]


def _model(n=4, **kw):
    return T.TranscriptModel(
        [_para(i * 2, f"Paragraph {i}.") for i in range(n)], **kw)


class TestParallelListsStayInStep(unittest.TestCase):
    """Six lists are kept parallel to paragraphs. Any mutation that
    forgets one corrupts the document silently, so every mutation is
    checked rather than trusted."""

    LISTS = ("speakers", "ids", "ts_marks", "text_origin", "speaker_origin")

    def assertParallel(self, m, where):
        n = len(m.paragraphs)
        for name in self.LISTS:
            self.assertEqual(len(getattr(m, name)), n,
                             f"{name} out of step after {where}")
        self.assertEqual(len(set(m.ids)), n, f"duplicate ids after {where}")

    def test_every_mutation_keeps_them_in_step(self):
        m = _model(6)
        self.assertParallel(m, "construction")
        m.commit_edit(1, "Edited.")
        self.assertParallel(m, "commit_edit")
        m.set_speaker(2, "1")
        self.assertParallel(m, "set_speaker")
        m.split(3, 5)
        self.assertParallel(m, "split")
        m.merge_with_previous(2)
        self.assertParallel(m, "merge_with_previous")
        m.delete_paragraph(0)
        self.assertParallel(m, "delete_paragraph")
        m.replace_runs([(0, 1, [_para(0, "Replaced.")])])
        self.assertParallel(m, "replace_runs")
        m.undo()
        self.assertParallel(m, "undo")
        m.redo()
        self.assertParallel(m, "redo")

    def test_growing_keeps_them_in_step(self):
        m = T.TranscriptModel([_para(0, "First.")])
        m.sealed_count = 0
        m.extend([_para(0, "First."), _para(2, "Second.")],
                 _para(4, "Third (still going)."))
        self.assertParallel(m, "extend")
        self.assertEqual(len(m.paragraphs), 3)


class TestProvenance(unittest.TestCase):
    def test_paragraphs_start_as_the_engine_s(self):
        m = _model()
        self.assertEqual(m.text_origin, ["engine"] * 4)
        self.assertEqual(m.speaker_origin, [None] * 4)

    def test_editing_text_claims_it(self):
        m = _model()
        m.commit_edit(2, "My correction.")
        self.assertEqual(m.text_origin[2], "user")
        self.assertEqual(m.text_origin[1], "engine")

    def test_assigning_a_speaker_claims_it(self):
        m = _model()
        m.set_speaker(1, "2")
        self.assertEqual(m.speaker_origin, [None, "user", None, None])

    def test_splitting_claims_both_halves(self):
        m = _model()
        m.set_speaker(1, "3")
        new_idx = m.split(1, 5)
        self.assertEqual(m.text_origin[1], "user")
        self.assertEqual(m.text_origin[new_idx], "user")
        # the speaker carries across, and stays the user's
        self.assertEqual(m.speakers[new_idx], "3")
        self.assertEqual(m.speaker_origin[new_idx], "user")

    def test_merging_claims_the_survivor(self):
        m = _model()
        m.merge_with_previous(2)
        self.assertEqual(m.text_origin[1], "user")

    def test_undo_restores_provenance_too(self):
        m = _model()
        m.commit_edit(0, "Mine.")
        self.assertEqual(m.text_origin[0], "user")
        m.undo()
        self.assertEqual(m.text_origin[0], "engine")


class TestEngineSpeakers(unittest.TestCase):
    def test_detected_speakers_fill_the_blanks(self):
        m = _model()
        filled = m.apply_engine_speakers(["1", "2", "1", None])
        self.assertEqual(filled, 3)
        self.assertEqual(m.speakers, ["1", "2", "1", None])
        self.assertEqual(m.speaker_origin[:3], ["engine"] * 3)

    def test_they_never_override_a_person(self):
        """The whole point: detection runs while somebody is labelling,
        and must not undo their work."""
        m = _model()
        m.set_speaker(1, "5")
        m.apply_engine_speakers(["1", "2", "3", "4"])
        self.assertEqual(m.speakers[1], "5")
        self.assertEqual(m.speaker_origin[1], "user")
        self.assertEqual(m.speakers[0], "1")

    def test_filling_nothing_records_no_undo_step(self):
        m = _model()
        before = m.can_undo()
        m.apply_engine_speakers([None, None, None, None])
        self.assertEqual(m.can_undo(), before)


class TestRepairOffers(unittest.TestCase):
    REPAIR = [_para(2, "The re-read version.")]

    def test_an_untouched_span_is_simply_replaced(self):
        m = _model()
        self.assertEqual(
            m.offer_engine_run(1, 1, self.REPAIR, source="repair-small"),
            "applied")
        self.assertEqual(m.body(1), "The re-read version.")
        self.assertEqual(m.suggestions, {})

    def test_an_edited_span_keeps_the_person_s_text(self):
        m = _model()
        m.commit_edit(1, "What I typed.")
        self.assertEqual(
            m.offer_engine_run(1, 1, self.REPAIR, source="repair-small"),
            "offered")
        self.assertEqual(m.body(1), "What I typed.")

    def test_the_alternative_is_there_to_look_at(self):
        m = _model()
        m.commit_edit(1, "What I typed.")
        m.offer_engine_run(1, 1, self.REPAIR, source="repair-small")
        offer = m.suggestion_for(1)
        self.assertEqual(offer["text"], "The re-read version.")
        self.assertEqual(offer["source"], "repair-small")

    def test_accepting_takes_the_engine_s_version(self):
        m = _model()
        m.commit_edit(1, "What I typed.")
        m.offer_engine_run(1, 1, self.REPAIR, source="repair-small")
        self.assertTrue(m.accept_suggestion(1))
        self.assertEqual(m.body(1), "The re-read version.")
        self.assertIsNone(m.suggestion_for(1))

    def test_dismissing_keeps_what_is_there(self):
        m = _model()
        m.commit_edit(1, "What I typed.")
        m.offer_engine_run(1, 1, self.REPAIR, source="repair-small")
        self.assertTrue(m.dismiss_suggestion(1))
        self.assertEqual(m.body(1), "What I typed.")
        self.assertIsNone(m.suggestion_for(1))

    def test_an_offer_follows_its_paragraph_when_indices_shift(self):
        """Offers are keyed by paragraph id, because deleting something
        earlier renumbers every index after it."""
        m = _model()
        m.commit_edit(2, "What I typed.")
        m.offer_engine_run(2, 2, self.REPAIR, source="repair-small")
        m.delete_paragraph(0)
        self.assertEqual(m.body(1), "What I typed.")
        self.assertIsNotNone(m.suggestion_for(1))
        self.assertIsNone(m.suggestion_for(2))

    def test_an_offer_whose_span_was_merged_away_goes_stale(self):
        m = _model()
        m.commit_edit(1, "Mine.")
        m.offer_engine_run(1, 2, self.REPAIR, source="repair-small")
        m.delete_paragraph(2)                 # the far end of the span
        self.assertFalse(m.accept_suggestion(1))
        self.assertIsNone(m.suggestion_for(1))

    def test_editing_a_paragraph_drops_its_stale_offer(self):
        m = _model()
        m.commit_edit(1, "Mine.")
        m.offer_engine_run(1, 1, self.REPAIR, source="repair-small")
        m.split(1, 3)
        self.assertIsNone(m.suggestion_for(1))

    def test_a_span_is_offered_when_any_of_it_was_edited(self):
        m = _model()
        m.commit_edit(2, "Mine.")
        self.assertEqual(
            m.offer_engine_run(1, 3, self.REPAIR, source="repair-full"),
            "offered")
        self.assertEqual(m.body(2), "Mine.")


class TestTheProvisionalTail(unittest.TestCase):
    """While the engine is still running, the last paragraph can still
    change shape as segments arrive. Editing it would be editing
    something about to be thrown away."""

    def _live(self):
        m = T.TranscriptModel([_para(0, "Sealed."), _para(2, "Still going")])
        m.sealed_count = 1
        return m

    def test_sealed_paragraphs_are_editable(self):
        m = self._live()
        self.assertTrue(m.is_sealed(0))
        self.assertTrue(m.commit_edit(0, "Edited."))

    def test_the_tail_is_not(self):
        m = self._live()
        self.assertFalse(m.is_sealed(1))
        self.assertFalse(m.commit_edit(1, "Nope."))
        self.assertFalse(m.set_speaker(1, "1"))
        self.assertIsNone(m.split(1, 3))
        self.assertFalse(m.delete_paragraph(1))

    def test_merging_across_the_boundary_is_refused(self):
        m = self._live()
        self.assertFalse(m.merge_with_previous(1))

    def test_growing_replaces_the_tail_and_leaves_sealed_text_alone(self):
        m = self._live()
        m.commit_edit(0, "My correction.")
        m.extend([_para(2, "Now sealed.")], _para(4, "New tail"))
        self.assertEqual(m.body(0), "My correction.")
        self.assertEqual(m.text_origin[0], "user")
        self.assertEqual(m.body(1), "Now sealed.")
        self.assertEqual(m.sealed_count, 2)
        self.assertFalse(m.is_sealed(2))

    def test_sealing_makes_everything_editable(self):
        m = self._live()
        m.seal_all()
        self.assertTrue(m.commit_edit(1, "Now I can."))

    def test_a_finished_transcript_is_sealed_throughout(self):
        m = _model()
        self.assertEqual(m.sealed_count, len(m.paragraphs))
        self.assertTrue(all(m.is_sealed(i) for i in range(4)))

    def test_growing_does_not_pile_up_undo_steps(self):
        """A long run would otherwise fill the stack with steps nobody
        wants to undo."""
        m = self._live()
        for i in range(5):
            m.extend([_para(i, f"Line {i}.")], _para(i + 1, "tail"))
        self.assertFalse(m.can_undo())
