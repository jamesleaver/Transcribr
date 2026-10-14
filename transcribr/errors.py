"""The exception types raised across the whole application.

They live on their own because everything raises them: leaving them
in the module that happened to define them first would make that
module a dependency of every other one."""


class TranscriptParseError(Exception):
    """Raised when a file can't be parsed as a Transcribr transcript."""


class _EngineNotAvailable(Exception):
    """Raised when the worker can't import the requested engine."""
    pass


class DiarizationUnavailable(Exception):
    """Speaker detection can't run; the message says why and the run
    continues without labels."""
    pass


class _CancelledByUser(Exception):
    """Raised inside whisper to abort transcription mid-run."""
    pass



class ApiFail(Exception):
    """A request failure with an HTTP status and machine-readable code;
    the route layer turns it into a JSON error response."""

    def __init__(self, status, code, message, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.extra = extra
