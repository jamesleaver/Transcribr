"""`python -m transcribr` - the entry point the app bundle runs.

Deliberately importing nothing at module level: multiprocessing's
spawn re-imports the main module inside every engine child process,
and the child has no use for the HTTP server or the window.
"""

import sys

if __name__ == "__main__":
    from .cli import main

    sys.exit(main())
