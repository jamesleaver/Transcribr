"""Applying a repair to a transcript somebody has been editing.

The repair works on a copy and reports spans as indices into the
transcript as it stood when it began. Indices move: deleting a
paragraph renumbers every one after it. Applying those numbers blindly
to the live transcript would corrupt it silently, so they are resolved
through paragraph ids - and this is where that is proved.
"""

import sys
import tempfile
import types
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


class TestRepairIntoALiveSession(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        audio = Path(self.tmp.name) / "recording.wav"
        audio.write_bytes(b"\x00" * 64)

        self.broker = T.EventBroker()
        self.backend = object.__new__(T.WebBackend)
        self.backend.broker = self.broker
        self.logged = []
        self.backend.controller = types.SimpleNamespace(
            _append_log=self.logged.append, progress=None)

        self.session = T.ReviewSession({
            "paragraphs": [para(i * 3, f"Paragraph {i}.") for i in range(5)],
            "out_path": str(Path(self.tmp.name) / "t.txt"),
            "show_timestamp": True, "title": None, "output_format": "txt",
            "audio_path": str(audio),
        }, self.broker)
        self.addCleanup(self.session.model.close)
        self.backend.review = self.session

        self._orig = T.server.auto_retranscribe
        self.addCleanup(setattr, T.server, "auto_retranscribe", self._orig)

    def stub_repair(self, runs, during=None):
        """Make the repair pass report `runs` as accepted.

        `during` runs inside it - which is the whole point: the repair
        takes minutes, and what it has to survive is somebody editing
        the transcript while it works. Editing beforehand would prove
        nothing, because the pass captures the ids as it starts."""
        def fake(paragraphs, words, audio, settings, **kw):
            if during is not None:
                during()
            return paragraphs, words, len(runs), runs
        T.server.auto_retranscribe = fake

    def repair(self, tier=2):
        return types.MethodType(T.WebBackend._run_repair, self.backend)(
            self.session, tier)

    def bodies(self):
        return [p["body"] for p in self.session.payload()["paragraphs"]]

    # ----- the ordinary cases -----------------------------------------

    def test_an_untouched_span_is_replaced(self):
        self.stub_repair([(1, 1, [para(3, "Re-read version.")])])
        self.assertTrue(self.repair())
        self.assertEqual(self.bodies()[1], "Re-read version.")

    def test_a_span_the_reviewer_edited_is_offered_instead(self):
        self.session.mutate(self.session.model.rev, "edit",
                            {"index": 1, "text": "My correction."})
        self.stub_repair([(1, 1, [para(3, "Re-read version.")])])
        self.assertTrue(self.repair())
        payload = self.session.payload()
        self.assertEqual(payload["paragraphs"][1]["body"], "My correction.")
        self.assertEqual(payload["paragraphs"][1]["offer"]["text"],
                         "Re-read version.")

    # ----- the case the id resolution exists for -----------------------

    def test_a_span_lands_on_its_paragraph_after_a_delete_mid_pass(self):
        """Somebody deletes paragraph 0 while the pass is running, which
        shifts everything down one. The repair's (2, 2) must still reach
        the same text, now at index 1."""
        target = self.session.model.body(2)
        self.stub_repair(
            [(2, 2, [para(6, "Re-read version.")])],
            during=lambda: self.session.mutate(
                self.session.model.rev, "delete", {"index": 0}))
        self.assertTrue(self.repair())
        self.assertEqual(self.bodies()[1], "Re-read version.")
        self.assertNotIn(target, self.bodies())

    def test_a_span_deleted_mid_pass_is_dropped_not_misapplied(self):
        """The paragraph the repair was about is deleted while it runs.
        Applying (3, 3) blindly would overwrite whatever has moved into
        that slot."""
        self.stub_repair(
            [(3, 3, [para(9, "Re-read version.")])],
            during=lambda: self.session.mutate(
                self.session.model.rev, "delete", {"index": 3}))
        self.assertFalse(self.repair())
        self.assertNotIn("Re-read version.", self.bodies())

    def test_several_spans_all_land_where_they_belong(self):
        first = self.session.model.body(1)
        last = self.session.model.body(4)
        self.stub_repair([(1, 1, [para(3, "First re-read.")]),
                          (4, 4, [para(12, "Last re-read.")])])
        self.assertTrue(self.repair())
        bodies = self.bodies()
        self.assertEqual(bodies[1], "First re-read.")
        self.assertEqual(bodies[4], "Last re-read.")
        self.assertNotIn(first, bodies)
        self.assertNotIn(last, bodies)

    def test_nothing_flagged_changes_nothing(self):
        self.stub_repair([])
        before = self.bodies()
        self.assertFalse(self.repair())
        self.assertEqual(self.bodies(), before)

    def test_a_closed_session_is_left_alone(self):
        self.stub_repair([(1, 1, [para(3, "Re-read version.")])])
        self.session.closed = True
        self.assertFalse(self.repair())

    def test_the_first_tier_is_skipped_for_other_languages(self):
        """small.en has nothing to offer a French recording."""
        self.stub_repair([(1, 1, [para(3, "Re-read version.")])])
        orig = T.server.current_settings
        T.server.current_settings = lambda: {**T.default_settings(),
                                             "language": "French"}
        try:
            self.assertFalse(self.repair(tier=1))
        finally:
            T.server.current_settings = orig
        self.assertEqual(self.bodies()[1], "Paragraph 1.")
