"""The engine supervisor: what happens when the engine dies badly.

The whole point of moving the engine into a child process is that the
app survives a native crash, so these tests hand the supervisor
children that crash on purpose. The child functions are module level
because multiprocessing's spawn has to import them by name.
"""

import os
import multiprocessing
import queue
import signal
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import transcribr as T
except ImportError as e:
    raise unittest.SkipTest(f"cannot import transcribr: {e}")

from transcribr import workerproc


# --------------------------------------------------------------------
# Children. Each takes the same arguments _child_main does.
# --------------------------------------------------------------------

def child_well_behaved(params, conn, cancel_flag):
    conn.send(("log", f"started with {params['marker']}\n"))
    conn.send(("done", "/tmp/out.docx"))
    conn.send((workerproc._CHILD_FINISHED, None))
    conn.close()


def child_segfaults(params, conn, cancel_flag):
    """Really crash, rather than exit with a chosen code.

    On Unix, dereferencing a null pointer through ctypes really does
    fault the process, which is what the supervisor has to survive.

    Windows will not co-operate: it catches the access violation inside
    the ctypes call and raises a Python OSError, so the process would
    exit tidily with code 1 and prove nothing. os.kill is no better -
    it understands only SIGTERM and the console events, and passes
    anything else to TerminateProcess as a plain exit code. So on
    Windows the child exits with the status code the operating system
    would itself have reported for an access violation, which is what
    the supervisor actually reads."""
    import ctypes
    conn.send(("log", "about to die\n"))
    if sys.platform == "win32":
        # STATUS_ACCESS_VIOLATION, as a signed C int: os._exit takes an
        # int, and 0xC0000005 is past INT_MAX. Windows reads the exit
        # code back as the unsigned value, which is what the supervisor
        # matches on.
        os._exit(ctypes.c_int(0xC0000005).value)
    ctypes.string_at(0)


def child_exits_nonzero(params, conn, cancel_flag):
    conn.send(("log", "about to give up\n"))
    os._exit(3)


def child_dies_silently(params, conn, cancel_flag):
    """Never says anything - the shape of a process that could not
    really start."""
    os._exit(1)


def child_ignores_cancel(params, conn, cancel_flag):
    conn.send(("log", "not listening\n"))
    time.sleep(120)


def child_obeys_cancel(params, conn, cancel_flag):
    for _ in range(600):
        if cancel_flag.is_set():
            conn.send(("cancelled", None))
            conn.send((workerproc._CHILD_FINISHED, None))
            conn.close()
            return
        time.sleep(0.05)


def child_sends_unpicklable(params, conn, cancel_flag):
    class Local:            # not importable in the parent
        pass
    conn.send(("log", "here comes trouble\n"))
    workerproc._PipeQueue(conn).put(
        ("paragraphs_ready", {"paragraphs": [], "result": Local()}))
    conn.send((workerproc._CHILD_FINISHED, None))
    conn.close()


# --------------------------------------------------------------------

def _run(child, *, cancel_after=None, timeout=60):
    q = queue.Queue()
    cancel = threading.Event()
    if cancel_after is not None:
        threading.Timer(cancel_after, cancel.set).start()
    t = threading.Thread(
        target=workerproc.run_worker_subprocess,
        args=({"marker": "hello"}, q, cancel),
        kwargs={"child_target": child}, daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "the supervisor never returned"
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            break
    return out


class TestEngineSupervisor(unittest.TestCase):
    def kinds(self, msgs):
        return [k for k, _ in msgs]

    def test_a_normal_run_is_passed_straight_through(self):
        msgs = _run(child_well_behaved)
        self.assertIn("log", self.kinds(msgs))
        self.assertIn(("done", "/tmp/out.docx"), msgs)
        self.assertNotIn(workerproc._CHILD_FINISHED, self.kinds(msgs))

    def test_the_child_really_is_a_separate_process(self):
        msgs = _run(child_well_behaved)
        text = "".join(d for k, d in msgs if k == "log")
        self.assertIn("hello", text)

    def test_a_native_crash_becomes_an_error_not_a_dead_app(self):
        msgs = _run(child_segfaults)
        errors = [d for k, d in msgs if k == "error"]
        self.assertEqual(len(errors), 1, msgs)
        # The same fault is a segmentation fault on Unix and an access
        # violation on Windows; either way it must be named.
        self.assertTrue(
            "segmentation fault" in errors[0]
            or "access violation" in errors[0],
            errors[0])
        # And it says what it means for the user.
        self.assertIn("review pane is untouched", errors[0])

    def test_a_nonzero_exit_becomes_an_error_naming_the_code(self):
        msgs = _run(child_exits_nonzero)
        errors = [d for k, d in msgs if k == "error"]
        self.assertEqual(len(errors), 1, msgs)
        self.assertIn("exit code 3", errors[0])

    def test_output_before_a_crash_is_not_lost(self):
        msgs = _run(child_segfaults)
        self.assertIn("about to die\n",
                      [d for k, d in msgs if k == "log"])

    def test_a_cancelled_child_that_cooperates_reports_cancelled(self):
        msgs = _run(child_obeys_cancel, cancel_after=0.5)
        self.assertIn("cancelled", self.kinds(msgs))
        self.assertNotIn("error", self.kinds(msgs))

    def test_a_child_that_ignores_stop_is_killed_and_still_reports(self):
        """Cancellation must not depend on the engine's goodwill."""
        original = workerproc.CANCEL_GRACE_SECONDS
        workerproc.CANCEL_GRACE_SECONDS = 1
        try:
            started = time.monotonic()
            msgs = _run(child_ignores_cancel, cancel_after=0.3)
        finally:
            workerproc.CANCEL_GRACE_SECONDS = original
        self.assertLess(time.monotonic() - started, 40)
        self.assertIn("cancelled", self.kinds(msgs))
        self.assertNotIn("error", self.kinds(msgs))

    def test_an_unpicklable_result_costs_the_result_not_the_run(self):
        msgs = _run(child_sends_unpicklable)
        ready = [d for k, d in msgs if k == "paragraphs_ready"]
        self.assertEqual(len(ready), 1, msgs)
        self.assertIsNone(ready[0]["result"])
        self.assertEqual(ready[0]["paragraphs"], [])


class TestSilentChildFallsBackInProcess(unittest.TestCase):
    """The Windows app runs under pythonw.exe, which has no console and
    no standard streams - the configuration least like the one CI
    exercises. If spawning fails there, the run should still happen."""

    def test_a_child_that_dies_without_speaking_is_retried_in_process(self):
        calls = []

        def fake_worker(params, q, cancel_event):
            calls.append(params)
            q.put(("done", "/tmp/inproc.docx"))

        original = workerproc.transcribe_worker
        workerproc.transcribe_worker = fake_worker
        try:
            msgs = _run(child_dies_silently)
        finally:
            workerproc.transcribe_worker = original
        self.assertEqual(len(calls), 1, "did not fall back")
        self.assertIn(("done", "/tmp/inproc.docx"), msgs)
        self.assertNotIn("error", [k for k, _ in msgs])

    def test_a_child_that_spoke_first_is_reported_as_a_crash(self):
        """Silence is the signal. A child that got as far as logging
        did start, so its death is a real failure and must be said so
        rather than quietly retried."""
        calls = []
        original = workerproc.transcribe_worker
        workerproc.transcribe_worker = lambda *a: calls.append(a)
        try:
            msgs = _run(child_exits_nonzero)
        finally:
            workerproc.transcribe_worker = original
        self.assertEqual(calls, [], "should not have retried")
        self.assertIn("exit code 3",
                      "".join(d for k, d in msgs if k == "error"))


class TestExitDescription(unittest.TestCase):
    def test_signals_are_named(self):
        text = workerproc._describe_exit(-int(signal.SIGSEGV),
                                         cancelled=False)
        self.assertIn("segmentation fault", text)

    @unittest.skipUnless(hasattr(signal, "SIGKILL"),
                         "Windows has no SIGKILL")
    def test_a_kill_mentions_memory(self):
        text = workerproc._describe_exit(-int(signal.SIGKILL),
                                         cancelled=False)
        self.assertIn("memory", text)

    def test_a_plain_exit_code_is_reported(self):
        self.assertIn("exit code 9",
                      workerproc._describe_exit(9, cancelled=False))

    def test_a_cancelled_child_needs_no_explanation(self):
        self.assertIsNone(workerproc._describe_exit(-15, cancelled=True))


class TestWindowsCompatibility(unittest.TestCase):
    """The engine supervisor has to import and behave on Windows, which
    the Mac cannot demonstrate on its own - so these check the places
    that would differ."""

    def test_the_signal_table_only_names_signals_that_exist(self):
        """A module-level signal.SIGKILL raises AttributeError on
        Windows, and this module is imported on the way to starting the
        app - so it would not start at all."""
        for code in workerproc._SIGNAL_NOTES:
            self.assertIsInstance(code, int)
            self.assertIsNotNone(signal.Signals(code))

    def test_the_table_is_built_without_the_signals_windows_lacks(self):
        import types
        stub = types.SimpleNamespace(
            SIGSEGV=signal.SIGSEGV, SIGABRT=signal.SIGABRT,
            SIGILL=signal.SIGILL, SIGFPE=signal.SIGFPE)
        original = workerproc.signal
        workerproc.signal = stub          # no SIGKILL, no SIGBUS
        try:
            notes = workerproc._signal_notes()
        finally:
            workerproc.signal = original
        self.assertIn(int(signal.SIGSEGV), notes)
        self.assertEqual(len(notes), 4)

    def test_a_windows_access_violation_is_explained(self):
        """Windows reports a fault as the NTSTATUS exit code, not as a
        negative signal number."""
        text = workerproc._describe_exit(0xC0000005, cancelled=False)
        self.assertIn("access violation", text)

    def test_other_windows_faults_are_named(self):
        for code, word in ((0xC00000FD, "stack overflow"),
                           (0xC0000017, "memory"),
                           (0xC0000409, "buffer overrun")):
            self.assertIn(word,
                          workerproc._describe_exit(code, cancelled=False))

    def test_an_unknown_code_still_says_something_useful(self):
        text = workerproc._describe_exit(12345, cancelled=False)
        self.assertIn("12345", text)


def child_that_cannot_start(params, conn, cancel_flag):
    raise AssertionError("never runs - the Process is refused first")


class TestFallbackWhenTheChildWillNotStart(unittest.TestCase):
    def test_the_run_falls_back_to_the_app_process(self):
        """Whatever the platform quirk, a transcription without the
        crash isolation beats no transcription."""
        calls = []

        def fake_worker(params, q, cancel_event):
            calls.append(params)
            q.put(("done", "/tmp/fallback.docx"))

        import multiprocessing

        real = multiprocessing.get_context("spawn")   # before patching

        class RefusingContext:
            """A context that hands out real pipes but refuses to make
            a process, the way a platform quirk or an exhausted process
            table would."""

            def Pipe(self, duplex=False):
                return real.Pipe(duplex)

            def Event(self):
                return real.Event()

            def Process(self, **kw):
                raise OSError("no processes available")

        original_get = multiprocessing.get_context
        original_worker = workerproc.transcribe_worker
        multiprocessing.get_context = lambda name=None: RefusingContext()
        workerproc.transcribe_worker = fake_worker
        try:
            q = queue.Queue()
            workerproc.run_worker_subprocess(
                {"marker": "x"}, q, threading.Event())
        finally:
            multiprocessing.get_context = original_get
            workerproc.transcribe_worker = original_worker

        msgs = []
        while True:
            try:
                msgs.append(q.get_nowait())
            except queue.Empty:
                break
        self.assertEqual(len(calls), 1, "the fallback did not run")
        self.assertIn(("done", "/tmp/fallback.docx"), msgs)
        self.assertTrue(any("in the app instead" in d
                            for k, d in msgs if k == "log"))


class TestWorkerSelection(unittest.TestCase):
    def test_the_subprocess_worker_is_the_default(self):
        os.environ.pop("TRANSCRIBR_INPROCESS_ENGINE", None)
        self.assertIs(workerproc.default_worker_fn(),
                      workerproc.run_worker_subprocess)

    def test_the_escape_hatch_puts_it_back_on_a_thread(self):
        os.environ["TRANSCRIBR_INPROCESS_ENGINE"] = "1"
        self.addCleanup(os.environ.pop, "TRANSCRIBR_INPROCESS_ENGINE", None)
        self.assertIs(workerproc.default_worker_fn(), T.transcribe_worker)


class TestParamsCrossTheProcessBoundary(unittest.TestCase):
    def test_build_worker_params_output_is_picklable(self):
        """Whatever the settings page produces has to reach the child."""
        import pickle
        import tempfile
        # The suite deliberately installs no Whisper engine, and
        # build_worker_params refuses to build anything without one.
        saved = T.engines._AVAILABLE_ENGINES
        T.engines._AVAILABLE_ENGINES = [("faster", "faster-whisper")]
        self.addCleanup(setattr, T.engines, "_AVAILABLE_ENGINES", saved)
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "a.mp3"
            src.write_bytes(b"\x00" * 16)
            params = T.build_worker_params(
                T.default_settings(), str(src), str(Path(d) / "a.docx"),
                review_before_save=True)
        restored = pickle.loads(pickle.dumps(params))
        self.assertEqual(restored["input"], params["input"])


class CrashesAreNotReRunInTheApp(unittest.TestCase):
    """The engine runs in a child so that a crash costs the run and not
    the application. Falling back in-process after a crash gives that
    away twice over: the same fault then happens in the process holding
    the run, the log, and the review session somebody has been
    labelling.

    The in-process fallback exists for an environment that will not
    spawn at all - the Windows app runs under pythonw.exe, with no
    console and no standard streams - and that is all it is for.
    """

    def _run(self, target):
        """Run the supervisor against `target`, recording whether the
        in-process path was taken."""
        called = []
        real = workerproc.transcribe_worker
        workerproc.transcribe_worker = lambda p, q, e: called.append(1)
        try:
            q = queue.Queue()
            workerproc.run_worker_subprocess(
                {}, q, multiprocessing.Event(), child_target=target)
        finally:
            workerproc.transcribe_worker = real
        msgs = []
        while not q.empty():
            msgs.append(q.get_nowait())
        return bool(called), msgs

    @unittest.skipIf(sys.platform == "win32", "no SIGKILL on Windows")
    def test_a_child_killed_by_the_os_is_reported_not_repeated(self):
        ran_in_app, msgs = self._run(_child_killed_by_the_os)
        self.assertFalse(ran_in_app)
        errors = [m[1] for m in msgs if m[0] == "error"]
        self.assertTrue(errors)
        # And it says what actually happened, which for a large model on
        # a small machine is the thing worth knowing.
        self.assertIn("out of memory", errors[0])

    def test_a_segfaulting_child_is_reported_not_repeated(self):
        ran_in_app, msgs = self._run(_child_segfaults)
        self.assertFalse(ran_in_app)
        self.assertTrue([m for m in msgs if m[0] == "error"])

    def test_a_child_that_never_started_still_falls_back(self):
        # A plain silent non-zero exit is the shape of an environment
        # refusing to spawn, and the fallback must survive the fix.
        ran_in_app, msgs = self._run(_child_exits_silently)
        self.assertTrue(ran_in_app)


def _child_killed_by_the_os(params, conn, cancel_flag):
    import os
    import signal
    os.kill(os.getpid(), signal.SIGKILL)


def _child_segfaults(params, conn, cancel_flag):
    import ctypes
    import os
    try:
        ctypes.string_at(0)
    except OSError:
        # Windows raises rather than faulting; exit with the NTSTATUS
        # the fault would have produced.
        os._exit(ctypes.c_int(0xC0000005).value)


def _child_exits_silently(params, conn, cancel_flag):
    import os
    os._exit(3)
