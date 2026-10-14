"""However a run ends, the transcript must end up saveable.

Both of these are regressions found by driving a real run: stopping
one left the session live for good, and a live session refuses to save
- so the partial transcript somebody asked to keep was trapped in the
pane with no way out.
"""

import sys
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


class TestSealingOnRunEnd(unittest.TestCase):
    def setUp(self):
        self.broker = T.EventBroker()
        self.backend = object.__new__(T.WebBackend)
        self.backend.broker = self.broker
        self.backend.controller = types.SimpleNamespace(
            _append_log=lambda _t: None)
        self.session = T.ReviewSession.open_live({
            "out_path": str(Path(_harness.CONFIG_DIR) / "stopped.txt"),
            "show_timestamp": True, "title": None,
            "output_format": "txt", "audio_path": None,
        }, self.broker)
        self.addCleanup(self.session.model.close)
        self.backend.review = self.session
        self.seal = types.MethodType(T.WebBackend._seal_live_review,
                                     self.backend)

    def test_a_stopped_run_leaves_a_saveable_transcript(self):
        self.session.grow([para(0, "Kept.")], para(2, "half a sentence"))
        self.assertTrue(self.session.live)
        self.seal()
        self.assertFalse(self.session.live)
        self.assertTrue(all(p["sealed"]
                            for p in self.session.payload()["paragraphs"]))
        out = self.session.save(self.session.model.rev, "no_labels")
        self.assertTrue(Path(out).exists())

    def test_corrections_made_during_the_run_are_kept(self):
        self.session.grow([para(0, "Engine text.")], para(2, "tail"))
        self.session.mutate(self.session.model.rev, "edit",
                            {"index": 0, "text": "My correction."})
        self.seal()
        self.assertEqual(self.session.payload()["paragraphs"][0]["body"],
                         "My correction.")

    def test_sealing_a_finished_session_again_does_nothing(self):
        self.session.grow([para(0, "Done.")], None)
        self.session.finish_live({"result": None})
        rev = self.session.model.rev
        self.seal()
        self.assertEqual(self.session.model.rev, rev)

    def test_it_copes_with_no_session_at_all(self):
        self.backend.review = None
        self.seal()             # must not raise

    def test_it_copes_with_a_closed_session(self):
        self.session.grow([para(0, "Done.")], None)
        self.session.finish_live({"result": None})
        self.session.save(self.session.model.rev, "no_labels")
        self.seal()             # must not raise


class TestTheHookIsActuallyWired(unittest.TestCase):
    """The controller has to call it, or none of the above happens."""

    def controller(self):
        c = T.RunController(T.EventBroker())
        self.fired = []
        c.on_run_ended = lambda: self.fired.append(True)
        return c

    def test_a_stopped_run_fires_it(self):
        c = self.controller()
        c.handle_message("cancelled", None)
        self.assertEqual(len(self.fired), 1)

    def test_a_failed_run_fires_it(self):
        c = self.controller()
        c.handle_message("error", "the engine fell over")
        self.assertEqual(len(self.fired), 1)

    def test_a_hook_that_throws_does_not_break_the_run(self):
        c = T.RunController(T.EventBroker())
        c.on_run_ended = lambda: 1 / 0
        c.handle_message("cancelled", None)      # must not raise
        self.assertEqual(c.phase, "cancelled")
