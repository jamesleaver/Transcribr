"""The worker reporting paragraphs while the run is still going.

These drive transcribe_worker with a stand-in engine, so they exercise
the real assembly, the real message protocol and the real end-of-run
path without loading a model.
"""

import queue
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness  # noqa: F401

try:
    import transcribr as T
    from transcribr import worker as W
except ImportError as e:
    raise unittest.SkipTest(f"cannot import transcribr: {e}")

SEGMENTS = [
    (0.0, 4.0, "Now, you were present on the evening of the fourteenth?"),
    (5.8, 9.0, "I was, yes."),
    (9.2, 12.0, "I arrived a little after seven."),
    (18.0, 22.0, "And who else was in the room at that time?"),
    (23.5, 28.0, "My colleague Ms Nguyen, and the two contractors."),
]


def fake_engine(streaming):
    """A stand-in runner. `streaming` decides whether it reports as it
    goes (faster-whisper) or only at the end (openai-whisper, mlx)."""
    def run(params, q, cancel_event, on_segment=None):
        result = {"segments": [{"start": s, "end": e, "text": t, "words": []}
                               for s, e, t in SEGMENTS],
                  "language": "en"}
        if streaming and on_segment is not None:
            for seg in SEGMENTS:
                on_segment(seg, [])
        return list(SEGMENTS), result, False
    return run


class _WorkerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(_harness.CONFIG_DIR)
        # No real audio: keeps the dead-air scan and diarization out of
        # the way, which are covered on their own.
        self._patch(W, "decode_audio_16k", lambda _p: None)
        self._patch(W, "_have_pyav", lambda: False)

    def _patch(self, module, name, value):
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)

    def run_worker(self, *, streaming=True, review=True, **extra):
        self._patch(W, "_run_faster_whisper", fake_engine(streaming))
        params = {
            "engine": "faster",
            "input": str(self.tmp / "in.mp3"),
            "output": str(self.tmp / "out.txt"),
            "gap": 1.5,
            "output_format": "txt",
            "title": "A title",
            "show_timestamp": True,
            "review_before_save": review,
            **extra,
        }
        q = queue.Queue()
        W.transcribe_worker(params, q, threading.Event())
        out = []
        while True:
            try:
                out.append(q.get_nowait())
            except queue.Empty:
                return out

    @staticmethod
    def of_kind(messages, kind):
        return [data for k, data in messages if k == kind]


class TestLiveReporting(_WorkerCase):
    def test_the_run_announces_itself_before_any_text(self):
        """The pane needs the document's identity to open at all."""
        msgs = self.run_worker()
        kinds = [k for k, _ in msgs]
        started = self.of_kind(msgs, "transcript_started")
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]["title"], "A title")
        self.assertTrue(started[0]["out_path"].endswith("out.txt"))
        self.assertLess(kinds.index("transcript_started"),
                        kinds.index("paragraphs_grew"))

    def test_paragraphs_are_handed_over_during_the_run(self):
        msgs = self.run_worker()
        grew = self.of_kind(msgs, "paragraphs_grew")
        self.assertGreater(len(grew), 1,
                           "everything arrived in one message")
        self.assertTrue(any(g["sealed"] for g in grew))

    def test_the_last_message_closes_the_tail(self):
        grew = self.of_kind(self.run_worker(), "paragraphs_grew")
        self.assertIsNone(grew[-1]["tail"])

    def test_a_paragraph_is_only_sent_once(self):
        grew = self.of_kind(self.run_worker(), "paragraphs_grew")
        sent = [tuple(seg) for g in grew for para in g["sealed"]
                for seg in para]
        self.assertEqual(len(sent), len(set(sent)))

    def test_the_streamed_transcript_matches_the_finished_one(self):
        """The governing property, through the real worker."""
        msgs = self.run_worker()
        streamed = [para for g in self.of_kind(msgs, "paragraphs_grew")
                    for para in g["sealed"]]
        ready = self.of_kind(msgs, "paragraphs_ready")
        self.assertEqual(len(ready), 1)
        self.assertEqual(streamed, ready[0]["paragraphs"])

    def test_it_matches_what_paragraphify_would_have_produced(self):
        msgs = self.run_worker()
        ready = self.of_kind(msgs, "paragraphs_ready")[0]
        self.assertEqual(ready["paragraphs"],
                         T.paragraphify(list(SEGMENTS), 1.5))


class TestEnginesThatCannotStream(_WorkerCase):
    """openai-whisper and mlx hand everything back at the end. The
    transcript must be identical - it just arrives in one go."""

    def test_the_transcript_is_the_same(self):
        live = self.of_kind(self.run_worker(streaming=True),
                            "paragraphs_ready")[0]["paragraphs"]
        batched = self.of_kind(self.run_worker(streaming=False),
                               "paragraphs_ready")[0]["paragraphs"]
        self.assertEqual(live, batched)

    def test_nothing_is_lost_or_duplicated(self):
        msgs = self.run_worker(streaming=False)
        paragraphs = self.of_kind(msgs, "paragraphs_ready")[0]["paragraphs"]
        segs = [tuple(s) for p in paragraphs for s in p]
        self.assertEqual(len(segs), len(SEGMENTS))


class TestUnattendedRunsAreUnchanged(_WorkerCase):
    """A run that saves straight to a file never opened a review pane,
    and must not start reporting to one now."""

    def test_no_live_messages(self):
        msgs = self.run_worker(review=False)
        self.assertEqual(self.of_kind(msgs, "transcript_started"), [])
        self.assertEqual(self.of_kind(msgs, "paragraphs_grew"), [])

    def test_it_still_writes_the_file_and_reports_done(self):
        msgs = self.run_worker(review=False)
        done = self.of_kind(msgs, "done")
        self.assertEqual(len(done), 1)
        self.assertTrue(Path(done[0]).exists())
        self.assertIn("fourteenth",
                      Path(done[0]).read_text(encoding="utf-8"))
