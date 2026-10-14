"""What a person does during a run must survive the run.

Every test here is a bug that shipped on develop and was found by
reading the code rather than by driving it: the paths a run exercises
were not the ones these were on. They are grouped by what they protect rather than by
which method they call, because that is the property that matters -
"the reviewer's work is not lost" - and it broke in six unrelated
places.
"""

import tempfile
import unittest
from pathlib import Path

import _harness  # noqa: F401  - isolates the config/log directories

from transcribr.errors import ApiFail
from transcribr.review import ReviewSession
from transcribr.transcript import TranscriptModel


def para(text, start=0.0):
    return [(start, start + 1.0, text)]


def bodies(m):
    return [m.body(i) for i in range(len(m.paragraphs))]


def live_model(n=3):
    """A model mid-run: `n` sealed paragraphs and a provisional tail."""
    m = TranscriptModel([para(f"p{i}", i) for i in range(n)])
    m.sealed_count = n
    m.extend([], tail=para("TAIL", n))
    return m


class SealedBoundary(unittest.TestCase):
    """sealed_count must track structural edits.

    A person only ever edits before the boundary and the engine only
    ever appends after it, so the two never collide - but only while the
    boundary is where it says it is. Every structural edit moves it, and
    none of them used to.
    """

    def test_split_does_not_feed_the_next_paragraph_to_the_engine(self):
        m = live_model()
        m.paragraphs[1] = [(1.0, 2.0, "one sentence. and another")]
        self.assertEqual(m.split(1, 14), 2)
        # "p2" has been pushed to index 3. If the boundary stayed at 3,
        # the next grow would overwrite it.
        m.extend([para("p3", 4)], tail=para("TAIL2", 5))
        self.assertIn("p2", bodies(m))

    def test_delete_does_not_strand_the_provisional_tail(self):
        m = live_model()
        m.delete_paragraph(1)
        m.extend([para("p3", 4)], tail=para("TAIL2", 5))
        # Exactly one tail, and it is the current one: the old one must
        # not have been absorbed into the sealed body.
        self.assertEqual(bodies(m).count("TAIL"), 0)
        self.assertEqual(bodies(m), ["p0", "p2", "p3", "TAIL2"])

    def test_merge_keeps_the_boundary(self):
        m = live_model()
        m.merge_with_previous(1)
        m.extend([para("p3", 4)], tail=para("TAIL2", 5))
        self.assertEqual(bodies(m), ["p0 p1", "p2", "p3", "TAIL2"])

    def test_boundary_never_passes_the_end(self):
        m = live_model()
        for i in range(3):
            m.delete_paragraph(0)
        self.assertLessEqual(m.sealed_count, len(m.paragraphs))


class UndoDuringARun(unittest.TestCase):
    """Undo winds back the reviewer's work, not the engine's.

    Snapshots used to copy the whole document, so an undo taken after
    the run had moved on restored a transcript from before it moved -
    silently discarding every paragraph streamed in since, with no way
    to get them back.
    """

    def test_undo_keeps_everything_that_streamed_in_since(self):
        m = live_model()
        m.commit_edit(1, "CORRECTED BY ME")
        for k in range(3, 8):
            m.extend([para(f"p{k}", k)], tail=para("TAIL", k + 1))
        grown = bodies(m)
        self.assertTrue(m.undo())
        self.assertEqual(m.body(1), "p1")           # the edit is undone
        for k in range(3, 8):                       # the run is not
            self.assertIn(f"p{k}", bodies(m))
        self.assertEqual(len(bodies(m)), len(grown))

    def test_undo_leaves_the_boundary_consistent(self):
        m = live_model()
        m.commit_edit(1, "CORRECTED BY ME")
        m.extend([para("p3", 3)], tail=para("TAIL", 4))
        m.undo()
        self.assertLessEqual(m.sealed_count, len(m.paragraphs))
        # And the run goes on landing in the right place.
        m.extend([para("p4", 4)], tail=para("TAIL2", 5))
        self.assertEqual(bodies(m), ["p0", "p1", "p2", "p3", "p4", "TAIL2"])

    def test_undo_after_a_structural_edit_and_growth(self):
        m = live_model()
        m.delete_paragraph(1)
        m.extend([para("p3", 3), para("p4", 4)], tail=para("TAIL", 5))
        self.assertTrue(m.undo())
        self.assertEqual(bodies(m),
                         ["p0", "p1", "p2", "p3", "p4", "TAIL"])

    def test_redo_too(self):
        m = live_model()
        m.commit_edit(1, "CORRECTED BY ME")
        m.extend([para("p3", 3)], tail=para("TAIL", 4))
        m.undo()
        self.assertTrue(m.redo())
        self.assertEqual(m.body(1), "CORRECTED BY ME")
        self.assertIn("p3", bodies(m))

    def test_undo_on_a_finished_transcript_is_unchanged(self):
        # The prefix is the whole document when nothing is live, so the
        # old whole-document behaviour must be exactly preserved.
        m = TranscriptModel([para("a", 0), para("b", 1)])
        m.commit_edit(0, "edited")
        m.delete_paragraph(1)
        m.undo()
        self.assertEqual(bodies(m), ["edited", "b"])
        m.undo()
        self.assertEqual(bodies(m), ["a", "b"])


class EngineWritesRespectTheReviewer(unittest.TestCase):
    """The repair pass offers where a person has been, and applies only
    where nobody has."""

    def test_a_speaker_the_reviewer_set_is_not_overwritten(self):
        m = TranscriptModel([para("the witness said yes", 0)])
        m.set_speaker(0, "3")
        m.set_ts_mark(0, "hidden")
        outcome = m.offer_engine_run(
            0, 0, [para("the witness said yes indeed", 0)], source="repair")
        self.assertEqual(outcome, "offered")
        self.assertEqual(m.body(0), "the witness said yes")
        self.assertEqual(m.speakers[0], "3")
        self.assertEqual(m.speaker_origin[0], "user")
        self.assertEqual(m.ts_marks[0], "hidden")

    def test_a_name_corrected_everywhere_is_not_reverted(self):
        m = TranscriptModel([para("Mr Cavil arrived", 0),
                             para("Cavil then left", 1)])
        self.assertEqual(m.replace_term(["Cavil"], "Cavill"), 2)
        self.assertEqual(m.text_origin, ["user", "user"])
        outcome = m.offer_engine_run(
            0, 1, [para("Mr Cavil arrived", 0), para("Cavil then left", 1)],
            source="repair")
        self.assertEqual(outcome, "offered")
        self.assertEqual(bodies(m), ["Mr Cavill arrived", "Cavill then left"])

    def test_replace_all_marks_only_what_it_changed(self):
        m = TranscriptModel([para("the defendant Smyth", 0),
                             para("unrelated", 1)])
        m.replace_all("Smyth", "Smith")
        self.assertEqual(m.text_origin, ["user", "engine"])

    def test_untouched_text_is_still_applied_outright(self):
        m = TranscriptModel([para("the the the the", 0)])
        outcome = m.offer_engine_run(0, 0, [para("the witness", 0)],
                                     source="repair")
        self.assertEqual(outcome, "applied")
        self.assertEqual(m.body(0), "the witness")

    def test_the_provisional_tail_is_not_the_repair_pass_to_rewrite(self):
        m = live_model()
        outcome = m.offer_engine_run(3, 3, [para("rewritten", 3)],
                                     source="repair")
        self.assertEqual(outcome, "stale")
        self.assertEqual(m.body(3), "TAIL")


class RevisionsVersusGrowth(unittest.TestCase):
    """A rev that moved only because the engine added paragraphs must
    not invalidate an edit composed against it."""

    def setUp(self):
        class _Broker:
            def publish(self, *a, **k):
                pass

        self.session = ReviewSession.open_live(
            {"out_path": "/tmp/x.txt", "paragraphs": []}, _Broker())
        for k in range(4):
            self.session.grow([para(f"p{k}", k)], para("TAIL", 9))

    def test_an_edit_survives_the_engine_growing_underneath_it(self):
        rev = self.session.model.rev
        self.session.grow([para("p9", 9)], para("TAIL", 10))
        self.session.mutate(rev, "edit", {"index": 1, "text": "MINE"})
        self.assertEqual(self.session.model.body(1), "MINE")

    def test_a_genuinely_stale_edit_is_still_refused(self):
        rev = self.session.model.rev
        self.session.mutate(rev, "edit", {"index": 1, "text": "FIRST"})
        with self.assertRaises(ApiFail) as caught:
            self.session.mutate(rev, "edit", {"index": 1, "text": "SECOND"})
        self.assertEqual(caught.exception.code, "stale_rev")
        self.assertEqual(self.session.model.body(1), "FIRST")


class AutosaveDuringARun(unittest.TestCase):
    """The crash-recovery snapshot has to happen while the run is going,
    which is when there is most to lose."""

    def _model(self, paragraphs, fired):
        """A model whose autosave timers are recorded, not started."""
        made = []
        model = TranscriptModel(
            paragraphs,
            on_autosave=lambda *a: fired.append(1),
            timer_factory=lambda delay, fn: _record(made, delay, fn))
        return model, made

    def test_growth_does_not_push_the_save_back_for_ever(self):
        fired = []
        m, made = self._model([para("p0", 0)], fired)
        m.sealed_count = 1
        for k in range(50):
            m.extend([], tail=para("TAIL", k))
        # One timer for the lot, at the growth interval. Before, each
        # segment cancelled the last one and started another, so the
        # deadline was pushed past the end of the run and the autosave
        # never happened at all.
        self.assertEqual([t.delay for t in made],
                         [TranscriptModel.AUTOSAVE_GROWTH])
        made[0].fire()
        self.assertEqual(len(fired), 1)

    def test_a_human_edit_still_takes_the_short_debounce(self):
        fired = []
        m, made = self._model([para("p0", 0), para("p1", 1)], fired)
        m.commit_edit(0, "mine")
        self.assertEqual([t.delay for t in made],
                         [TranscriptModel.AUTOSAVE_DEBOUNCE])

    def test_an_edit_mid_run_brings_the_save_forward(self):
        fired = []
        m, made = self._model([para("p0", 0)], fired)
        m.sealed_count = 1
        m.extend([], tail=para("TAIL", 1))          # growth: long timer
        m.commit_edit(0, "mine")                    # edit: short one
        self.assertEqual([t.delay for t in made],
                         [TranscriptModel.AUTOSAVE_GROWTH,
                          TranscriptModel.AUTOSAVE_DEBOUNCE])
        self.assertTrue(made[0].cancelled)


class _FakeTimer:
    """A threading.Timer stand-in that fires only when told to."""

    def __init__(self, delay, fn):
        self.delay = delay
        self.fn = fn
        self.cancelled = False
        self.daemon = False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.fn()


def _record(made, delay, fn):
    timer = _FakeTimer(delay, fn)
    made.append(timer)
    return timer


if __name__ == "__main__":
    unittest.main()


class ExportWhileTheRunIsGoing(unittest.TestCase):
    """A transcript that is a quarter finished must not leave the
    building looking finished - let alone certified."""

    def setUp(self):
        class _Broker:
            def publish(self, *a, **k):
                pass

        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.session = ReviewSession.open_live(
            {"out_path": str(Path(self.tmp.name) / "hearing.txt"),
             "paragraphs": [], "output_format": "txt"}, _Broker())
        for k in range(4):
            self.session.grow([para(f"p{k}", k)], para("TAIL", 9))

    def test_export_is_refused_while_live(self):
        with self.assertRaises(ApiFail) as caught:
            self.session.export(self.session.model.rev, fmt="txt",
                                verified_by="A. Reviewer")
        self.assertEqual(caught.exception.code, "still_running")
        self.assertFalse(list(Path(self.tmp.name).glob("*.export.*")))

    def test_export_works_once_the_run_has_finished(self):
        self.session.finish_live({"result": None})
        path = self.session.export(self.session.model.rev, fmt="txt")
        self.assertTrue(Path(path).exists())


class LockedSectionsAreLeftAlone(unittest.TestCase):
    """While a span is being re-transcribed, the actions that cannot say
    which paragraph they mean have to wait."""

    def setUp(self):
        class _Broker:
            def publish(self, *a, **k):
                pass

        self.session = ReviewSession(
            {"out_path": "/tmp/x.txt",
             "paragraphs": [para("Mr Cavil arrived", 0),
                            para("Cavil then left", 1)]}, _Broker())
        self.session.lock_paragraphs(0, 0)

    def _refused(self, action, body):
        with self.assertRaises(ApiFail) as caught:
            self.session.mutate(self.session.model.rev, action, body)
        self.assertEqual(caught.exception.code, "locked")

    def test_whole_document_actions_wait(self):
        self._refused("term", {"variants": ["Cavil"], "to": "Cavill"})
        self._refused("replace-all", {"find": "Cavil", "replace": "Cavill"})
        self._refused("undo", {})
        self._refused("redo", {})
        self._refused("accept-offer", {"index": 0})
        # Nothing was changed on the way to being refused.
        self.assertEqual(self.session.model.body(0), "Mr Cavil arrived")

    def test_editing_elsewhere_is_still_free(self):
        self.session.mutate(self.session.model.rev, "edit",
                            {"index": 1, "text": "MINE"})
        self.assertEqual(self.session.model.body(1), "MINE")

    def test_they_are_allowed_again_once_the_lock_clears(self):
        self.session.clear_lock()
        self.session.mutate(self.session.model.rev, "term",
                            {"variants": ["Cavil"], "to": "Cavill"})
        self.assertEqual(self.session.model.body(0), "Mr Cavill arrived")


class SpeakerLabelsLandOnTheRightParagraph(unittest.TestCase):
    """Detection labels the engine's paragraphs, one letter each, and
    hands the list over when the run finishes. By then the reviewer may
    have deleted, split or merged some of them.

    Applied by position, a single deletion early in a run shifted every
    label after it - handing the witness's answers to the person asking
    the questions. Fluent, plausible, and wrong about who spoke, which
    in a transcript of evidence is worse than garbled text. The non-live
    path already knew this hazard; this one did not.
    """

    WHO = ["1", "1", "2", "2", "1", "3", "3", "1"]
    TEXT = ["Q: state your name", "A: Eleanor Cavill",
            "Q: where were you", "A: at the depot",
            "Q: who else was there", "A: a man in a dark jacket",
            "A: he left by the side gate", "Q: thank you"]

    def setUp(self):
        class _Broker:
            def publish(self, *a, **k):
                pass

        self.session = ReviewSession.open_live(
            {"out_path": "/tmp/x.txt", "paragraphs": []}, _Broker())
        for i, text in enumerate(self.TEXT):
            self.session.grow([para(text, i * 5)], para("TAIL", 999))
        self.session.model.seal_all()
        self.engine = [para(t, i * 5) for i, t in enumerate(self.TEXT)]

    def _finish(self):
        self.session.live = True
        self.session.finish_live({
            "result": None, "preset_speakers": self.WHO,
            "paragraphs": self.engine})

    def _speaker_of(self, snippet):
        m = self.session.model
        for i in range(len(m.paragraphs)):
            if snippet in m.body(i):
                return m.speakers[i]
        self.fail(f"{snippet!r} is not in the transcript")

    def test_labels_are_right_when_nothing_was_edited(self):
        self._finish()
        self.assertEqual(self._speaker_of("state your name"), "1")
        self.assertEqual(self._speaker_of("a man in a dark jacket"), "3")

    def test_a_deletion_mid_run_does_not_shift_every_label_after_it(self):
        self.session.model.delete_paragraph(2)      # "Q: where were you"
        self._finish()
        self.assertEqual(self._speaker_of("at the depot"), "2")
        self.assertEqual(self._speaker_of("who else was there"), "1")
        self.assertEqual(self._speaker_of("a man in a dark jacket"), "3")
        self.assertEqual(self._speaker_of("thank you"), "1")

    def test_a_merge_mid_run_keeps_the_rest_aligned(self):
        self.session.model.merge_with_previous(3)
        self._finish()
        self.assertEqual(self._speaker_of("a man in a dark jacket"), "3")
        self.assertEqual(self._speaker_of("thank you"), "1")

    def test_a_split_mid_run_keeps_the_rest_aligned(self):
        m = self.session.model
        m.paragraphs[1] = [(5.0, 9.0, "A: Eleanor Cavill. I work there.")]
        self.assertIsNotNone(m.split(1, 20))
        self._finish()
        self.assertEqual(self._speaker_of("a man in a dark jacket"), "3")
        self.assertEqual(self._speaker_of("thank you"), "1")

    def test_a_speaker_the_reviewer_set_is_still_left_alone(self):
        self.session.model.set_speaker(5, "9")
        self.session.model.delete_paragraph(2)
        self._finish()
        self.assertEqual(self._speaker_of("a man in a dark jacket"), "9")

    def test_it_falls_back_to_position_when_the_engine_paragraphs_are_absent(self):
        # A loaded transcript or an older payload carries letters only.
        self.session.live = True
        self.session.finish_live({"result": None,
                                  "preset_speakers": self.WHO})
        self.assertEqual(self._speaker_of("state your name"), "1")
