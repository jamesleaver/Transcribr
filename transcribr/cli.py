"""Command line, and starting the app."""

from pathlib import Path
import os
import sys
import threading
import traceback

from .version import ABOUT_TEXT, __version__
from .platformutil import _log, set_annotate_mode
from .server import WebBackend


def _parse_args(argv):
    import argparse
    p = argparse.ArgumentParser(
        prog="transcribr",
        description="Transcribr - local Whisper transcription GUI.")
    p.add_argument("--web", action="store_true",
                   help="open the app window (this is the default)")
    p.add_argument("--serve", action="store_true",
                   help="run the backend only and print its URL "
                        "(development, or using the app from a browser)")
    p.add_argument("--port", type=int, default=None,
                   help="port for --serve (default 8737)")
    p.add_argument("--dev-token", default=None,
                   help="fixed API token for --serve (default 'dev')")
    p.add_argument("--annotate", action="store_true",
                   help="show the developer annotation overlay (pin "
                        "notes to UI elements; saved to annotations.json "
                        "in the config dir). Also: TRANSCRIBR_ANNOTATE=1, "
                        "or `touch <config dir>/annotate.on` for the "
                        "double-clicked app")
    return p.parse_args(argv)


def main(argv=None):
    # Belt and braces for a packaged build: the engine runs in a child
    # process, and a frozen executable re-invoking itself has to be told
    # that is what is happening. A no-op everywhere else.
    import multiprocessing
    multiprocessing.freeze_support()

    args = _parse_args(sys.argv[1:] if argv is None else argv)
    set_annotate_mode(
        args.annotate
        or os.environ.get("TRANSCRIBR_ANNOTATE", "").strip() == "1")
    if args.serve:
        return main_serve(args)
    if os.environ.get("TRANSCRIBR_UI", "").strip().lower() == "tk":
        print("Note: the classic Tk interface was retired in 0.7.0; "
              "opening the current interface.", file=sys.stderr)
    return main_web(args)


def _require_web_stack():
    """Exit with instructions rather than a traceback when the web
    interface is requested under a Python that lacks bottle (the
    installers add it to Transcribr's own environment; a bare system
    python usually won't have it)."""
    try:
        import bottle  # noqa: F401  (probe: the ImportError is the point)
        return
    except ImportError:
        pass
    if sys.platform == "darwin":
        venv_python = (Path.home() / "Library" / "Application Support"
                       / "Transcribr" / "venv" / "bin" / "python")
    elif sys.platform == "win32":
        venv_python = (Path(os.environ.get("LOCALAPPDATA", ""))
                       / "Transcribr" / "venv" / "Scripts" / "python.exe")
    else:
        venv_python = None
    lines = [
        "The web interface needs the 'bottle' package, which is not",
        f"installed for this Python ({sys.executable}).",
        "",
    ]
    if venv_python is not None and venv_python.exists():
        lines += [
            "Easiest fix - run Transcribr through its installed "
            "environment:",
            f'  "{venv_python}" -m transcribr --serve',
            "",
        ]
    lines += [
        "Or install bottle for this Python:",
        "  python3 -m pip install bottle",
    ]
    sys.exit("\n".join(lines))


def main_serve(args):
    """Web backend without a window: for development and as the
    escape hatch when the native window can't start."""
    _require_web_stack()
    token = args.dev_token or "dev"
    backend = WebBackend(token)
    backend.start_pump()
    server = _bind_or_exit(backend,
                           args.port if args.port is not None else 8737)
    url = f"http://127.0.0.1:{server.server_port}/?token={token}"
    _log(f"--serve listening on {server.server_port}")
    print(f"Transcribr {__version__} web backend running:\n  {url}")
    print("Open the URL in a browser. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _bind_or_exit(backend, port):
    """Bind the web server or exit with a plain-English explanation
    (a fixed port can collide with another Transcribr session)."""
    try:
        return backend.serve(port=port)
    except OSError as e:
        sys.exit(
            f"Could not start Transcribr's local server on port {port}:"
            f"\n  {e}\n\n"
            "Another Transcribr session (or another program) is probably "
            "using that port.\nClose it, or pick a different port:\n"
            f"  python3 -m transcribr --port {port + 1}")


# Kept module-global so the Objective-C target object isn't collected
# while the menu still points at it.
_ABOUT_MENU_TARGET = None


def _hook_about_menu():
    """macOS: make the application menu's About item show Transcribr's
    own about text (a native alert) instead of the bare standard panel.
    Best-effort - a failure just leaves the default item."""
    global _ABOUT_MENU_TARGET
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSAlert, NSApp
        from Foundation import NSObject
        from PyObjCTools import AppHelper

        class _AboutTarget(NSObject):
            def showAbout_(self, _sender):
                alert = NSAlert.alloc().init()
                alert.setMessageText_("Transcribr")
                alert.setInformativeText_(ABOUT_TEXT)
                alert.runModal()

        _ABOUT_MENU_TARGET = _AboutTarget.alloc().init()

        def patch():
            try:
                main_menu = NSApp.mainMenu()
                if main_menu is None or main_menu.numberOfItems() == 0:
                    return
                app_menu = main_menu.itemAtIndex_(0).submenu()
                if app_menu is None:
                    return
                for i in range(app_menu.numberOfItems()):
                    item = app_menu.itemAtIndex_(i)
                    if "about" in str(item.title() or "").lower():
                        item.setTarget_(_ABOUT_MENU_TARGET)
                        item.setAction_("showAbout:")
                        break
            except Exception:
                _log("about-menu patch failed:\n"
                     + traceback.format_exc())

        # The menu exists once the app has finished launching; the
        # window's loaded event is comfortably after that, and
        # callAfter hops us onto the main thread AppKit requires.
        AppHelper.callAfter(patch)
    except Exception:
        _log("about-menu hook unavailable:\n" + traceback.format_exc())


def _attach_drop_handler(backend, window):
    """Bridge OS drag-and-drop to the front end. pywebview reveals the
    dropped files' real filesystem paths on the PYTHON side only
    (pywebviewFullPath); we forward them as a files_dropped SSE event
    and the page applies the 1-file/many-files rule. prevent_default on
    dragover is required or the drop never fires."""
    try:
        from webview.dom import DOMEventHandler

        def on_drop(e):
            files = (e.get("dataTransfer") or {}).get("files") or []
            paths = [f.get("pywebviewFullPath") for f in files
                     if isinstance(f, dict)]
            paths = [p for p in paths if p]
            if paths:
                backend.broker.publish("files_dropped", {"paths": paths})

        window.dom.document.events.dragover += DOMEventHandler(
            lambda e: None, prevent_default=True)
        window.dom.document.events.drop += DOMEventHandler(
            on_drop, prevent_default=True)
    except Exception:
        _log("drop-handler attach failed:\n" + traceback.format_exc())


def main_web(args):
    """The desktop app: the web backend on daemon threads plus a
    native window (WKWebView / WebView2) pointed at it. webview.start()
    must own the main thread - notably on macOS."""
    _require_web_stack()
    try:
        import webview  # noqa: F401
    except ImportError:
        sys.exit(
            "The native window needs the 'pywebview' package, which is "
            f"not installed for this Python ({sys.executable}).\n"
            "Install it with:\n"
            "  python3 -m pip install pywebview\n"
            "or run without a window:  python3 -m transcribr --serve")
    import webview

    # Menu-bar/taskbar identity (ports of the Tk main()'s NSBundle
    # rename; AppUserModelID keeps the Windows taskbar icon grouped).
    if sys.platform == "darwin":
        try:
            from Foundation import NSBundle
            bundle = NSBundle.mainBundle()
            if bundle is not None:
                info = (bundle.localizedInfoDictionary()
                        or bundle.infoDictionary())
                if info is not None:
                    info["CFBundleName"] = "Transcribr"
        except Exception:
            pass
    elif sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "local.transcribr")
        except Exception:
            pass

    if args.dev_token:
        token = args.dev_token
    else:
        import secrets
        token = secrets.token_urlsafe(32)

    backend = WebBackend(token)
    backend.has_window = True
    backend.start_pump()
    server = _bind_or_exit(backend,
                           args.port if args.port is not None else 0)
    threading.Thread(target=server.serve_forever, daemon=True,
                     name="transcribr-http").start()
    url = f"http://127.0.0.1:{server.server_port}/?token={token}"
    _log(f"Transcribr {__version__} web window starting - "
         f"port {server.server_port}")

    window = webview.create_window(
        f"Transcribr {__version__}", url,
        width=1200, height=800, min_size=(900, 620))
    backend.window = window

    def on_loaded():
        _attach_drop_handler(backend, window)
        _hook_about_menu()

    def on_closed():
        # A pending (debounced) autosave must not be lost with the
        # window; flush it, then stop the server.
        try:
            if backend.review is not None and not backend.review.closed:
                backend.review.model.flush_autosave()
        except Exception:
            _log("autosave flush on close failed:\n"
                 + traceback.format_exc())
        finally:
            threading.Thread(target=server.shutdown,
                             daemon=True).start()

    window.events.loaded += on_loaded
    window.events.closed += on_closed
    webview.start()



if __name__ == "__main__":
    main()
