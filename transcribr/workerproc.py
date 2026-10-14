"""Running the engine in a process of its own.

The transcription engines are native code - CTranslate2, ONNX Runtime,
and PyTorch behind the optional engines. Native code can segfault, and
a large model on a small machine can be killed outright by the
operating system for using too much memory. While the engine ran on a
thread inside the app, either of those took the whole application down
with it: the run, the log, the open review session and whatever the
user had labelled but not yet saved.

So the engine runs in a child process now. The seam was already there -
transcribe_worker only ever speaks to the app by putting tuples on a
queue and reading a cancel flag - so the child does exactly what the
thread used to do, and the messages come back over a pipe and go onto
the same queue. Nothing downstream of the queue changed.

What that buys:

  - An engine crash becomes an error message. The supervisor sees the
    child exit without having sent a result, works out what killed it,
    and reports it as a failed run. The app, and anything open in the
    review pane, carries on.
  - Stop becomes real. Cancelling still asks politely first, so a
    part-finished transcript can still be saved, but a child that
    ignores it is terminated and then killed.
  - Memory is actually returned. A large model's several gigabytes go
    back to the system when the child exits, instead of sitting in the
    app's heap for the rest of the session.
"""

import os
import signal
import time
import traceback

from .platformutil import _log
from .worker import transcribe_worker

# How long a cancelled child gets to save what it has before it is
# terminated, and then killed. Whisper checks the flag between
# segments, so this is generous rather than tight.
CANCEL_GRACE_SECONDS = 25
TERMINATE_GRACE_SECONDS = 5

# A child that dies this quickly without having said anything at all
# never got as far as transcribing, so it is treated as the process
# failing to start rather than as the engine crashing. See the fallback
# at the end of run_worker_subprocess.
SILENT_DEATH_SECONDS = 15

# Sent by the child as its last act, so the supervisor can tell an
# orderly finish from a crash.
_CHILD_FINISHED = "__child_finished__"

# Messages that mean the run reached an end of its own accord.
_TERMINAL = frozenset({"done", "error", "cancelled", "paragraphs_ready"})

def _signal_notes():
    """Signals worth naming, for the platforms that have them.

    Looked up rather than named directly: Windows has no SIGKILL and no
    SIGBUS, so a module-level signal.SIGKILL would raise AttributeError
    on import and stop the app starting there at all."""
    notes = {}
    for name, text in (
        ("SIGSEGV", "a segmentation fault"),
        ("SIGBUS", "a bus error"),
        ("SIGABRT", "the engine aborting"),
        ("SIGILL", "an illegal instruction"),
        ("SIGFPE", "an arithmetic error"),
        ("SIGKILL", "being killed by the operating system, which "
                    "usually means it ran out of memory"),
    ):
        sig = getattr(signal, name, None)
        if sig is not None:
            notes[int(sig)] = text
    return notes


_SIGNAL_NOTES = _signal_notes()

# Windows does not report a fault as a negative signal; the process
# exit code is the NTSTATUS that killed it.
_WINDOWS_FAULTS = {
    0xC0000005: "an access violation - what a segmentation fault is "
                "called on Windows",
    0xC000001D: "an illegal instruction",
    0xC0000094: "an integer division by zero",
    0xC00000FD: "a stack overflow",
    0xC0000374: "heap corruption",
    0xC0000409: "a stack buffer overrun",
    0xC0000017: "running out of memory",
}


class _PipeQueue:
    """The queue the worker thinks it is writing to. Every put() is a
    message down the pipe to the app.

    A message that will not pickle must not cost the user the run, so a
    failure is retried with the engine's raw result dropped - it feeds
    the extra output formats and nothing else - and only then given up
    on."""

    def __init__(self, conn):
        self.conn = conn

    def put(self, item):
        try:
            self.conn.send(item)
            return
        except Exception:
            pass
        kind, data = item
        if isinstance(data, dict) and data.get("result") is not None:
            try:
                self.conn.send((kind, {**data, "result": None}))
                return
            except Exception:
                pass
        try:
            self.conn.send((
                "log",
                f"\n[Internal: a '{kind}' message could not be passed "
                "back from the engine process.]\n"))
        except Exception:
            pass


def _child_main(params, conn, cancel_flag):
    """The child process. Runs the ordinary worker and reports back."""
    try:
        transcribe_worker(params, _PipeQueue(conn), cancel_flag)
    except BaseException:
        # Including KeyboardInterrupt and SystemExit: whatever ends the
        # run, the app is told rather than left waiting.
        try:
            conn.send(("error",
                       "The transcription engine failed:\n\n"
                       + traceback.format_exc()))
        except Exception:
            pass
    finally:
        try:
            conn.send((_CHILD_FINISHED, None))
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def _describe_exit(exitcode, cancelled):
    """A sentence for a child that stopped without saying why."""
    if cancelled:
        return None            # the user asked for this
    if exitcode is None:
        return ("The transcription engine stopped without reporting a "
                "result.")
    if exitcode < 0:
        note = _SIGNAL_NOTES.get(-exitcode)
        if note is None:
            try:
                note = f"signal {signal.Signals(-exitcode).name}"
            except ValueError:
                note = f"signal {-exitcode}"
        return f"The transcription engine stopped unexpectedly - {note}."
    fault = _WINDOWS_FAULTS.get(exitcode & 0xFFFFFFFF)
    if fault is not None:
        return f"The transcription engine stopped unexpectedly - {fault}."
    return (f"The transcription engine stopped unexpectedly "
            f"(exit code {exitcode}).")


_CRASH_ADVICE = (
    "\n\nThis is the engine failing rather than anything wrong with "
    "your recording. A smaller model, or a different engine from the "
    "Models page, is the usual way through; the log file has whatever "
    "the engine managed to print.\n\n"
    "Nothing else was lost - the app kept running, and any transcript "
    "open in the review pane is untouched."
)


def run_worker_subprocess(params, q, cancel_event, *, child_target=None):
    """Run one transcription in a child process.

    Drop-in for transcribe_worker as RunController's worker_fn: same
    arguments, same messages on the same queue. Runs on the controller's
    worker thread, which now supervises rather than transcribes.

    `child_target` replaces what the child runs, the way RunController
    takes a worker_fn. The supervision here is the part that has to
    behave when an engine dies badly, and the only way to test that is
    to hand it a child that dies badly on purpose.
    """
    import multiprocessing

    # spawn, not fork: the parent is a GUI process with Cocoa and a web
    # view already initialised, and forking that is not safe.
    ctx = multiprocessing.get_context("spawn")
    recv_conn, send_conn = ctx.Pipe(duplex=False)
    cancel_flag = ctx.Event()

    try:
        proc = ctx.Process(target=child_target or _child_main,
                           args=(params, send_conn, cancel_flag),
                           name="transcribr-engine", daemon=True)
        proc.start()
    except Exception as e:
        # Whatever stopped the child starting - a platform quirk, a
        # sandbox, an exhausted process table - a working transcription
        # without the crash isolation beats no transcription at all.
        _log("could not start the engine process; falling back to "
             "running it in-process:\n" + traceback.format_exc())
        send_conn.close()
        recv_conn.close()
        q.put(("log",
               f"[Could not start a separate engine process "
               f"({type(e).__name__}); running it in the app instead.]\n"))
        transcribe_worker(params, q, cancel_event)
        return
    finally:
        # The parent has to drop its copy of the sending end, or the
        # pipe never reaches end-of-file when the child exits.
        try:
            send_conn.close()
        except Exception:
            pass

    saw_terminal = False
    saw_anything = False
    cancelled_at = None
    started_at = time.monotonic()
    try:
        while True:
            if cancel_event.is_set() and cancelled_at is None:
                cancelled_at = time.monotonic()
                cancel_flag.set()

            if recv_conn.poll(0.2):
                try:
                    kind, data = recv_conn.recv()
                except (EOFError, OSError):
                    break
                if kind == _CHILD_FINISHED:
                    saw_terminal = saw_terminal or True
                    break
                if kind in _TERMINAL:
                    saw_terminal = True
                saw_anything = True
                q.put((kind, data))
                continue

            if not proc.is_alive():
                break
            if (cancelled_at is not None
                    and time.monotonic() - cancelled_at > CANCEL_GRACE_SECONDS):
                _log("engine process ignored the stop request; terminating")
                break
    finally:
        _stop(proc)
        try:
            recv_conn.close()
        except Exception:
            pass

    if saw_terminal:
        return

    # The child went away without a result. Say what happened, in terms
    # that distinguish "the engine broke" from "your file is bad".
    if cancel_event.is_set():
        q.put(("cancelled", None))
        return

    # A child that died at once without a word never reached the engine:
    # transcribe_worker logs before it does anything else. That is the
    # shape of an environment refusing to spawn - the Windows app runs
    # under pythonw.exe, which has no console and no standard streams,
    # and that is the configuration least like the one CI exercises. Do
    # the run in-process rather than hand back a failure.
    #
    # Unless the child did start and was killed. A signal death, or a
    # Windows fault code, is the engine crashing or the operating system
    # taking its memory back - and re-running that in the app is the one
    # thing this whole module exists to prevent. It would reproduce the
    # crash in the process holding the run, the log and the review
    # session somebody has been labelling. An engine that segfaults on
    # import, or a model too large for the machine, both land here well
    # inside the silent-death window, because the first thing the child
    # does is decode the recording - hundreds of megabytes for a long
    # one - before it has anything to report.
    code = proc.exitcode
    was_killed = code is not None and (
        code < 0 or (code & 0xFFFFFFFF) in _WINDOWS_FAULTS)
    if (not saw_anything and not was_killed
            and time.monotonic() - started_at < SILENT_DEATH_SECONDS):
        _log(f"engine process exited silently (code {code}); "
             "running the transcription in-process instead")
        q.put(("log",
               "[The separate engine process did not start; running it "
               "in the app instead.]\n"))
        transcribe_worker(params, q, cancel_event)
        return

    detail = _describe_exit(proc.exitcode, cancelled=False)
    q.put(("error", (detail or "The transcription engine stopped.")
           + _CRASH_ADVICE))


def _stop(proc):
    """Terminate, then kill. Returns once the child is gone."""
    if proc.exitcode is not None:
        return
    proc.join(TERMINATE_GRACE_SECONDS)
    if proc.is_alive():
        proc.terminate()
        proc.join(TERMINATE_GRACE_SECONDS)
    if proc.is_alive():
        proc.kill()
        proc.join(TERMINATE_GRACE_SECONDS)
    if proc.is_alive():
        _log(f"engine process {proc.pid} would not die")


def engine_runs_in_process():
    """True when the engine is deliberately kept on a thread.

    TRANSCRIBR_INPROCESS_ENGINE=1 puts it back the way it was before
    0.9.15, which makes a debugger or a profiler able to see it. A crash
    then takes the app with it, which is the whole reason it is not the
    default."""
    return os.environ.get("TRANSCRIBR_INPROCESS_ENGINE", "").strip() == "1"


def default_worker_fn():
    """The worker RunController should use."""
    return transcribe_worker if engine_runs_in_process() \
        else run_worker_subprocess
