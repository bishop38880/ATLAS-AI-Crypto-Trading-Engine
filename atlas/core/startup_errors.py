class StartupFailedError(RuntimeError):
    """Raised when a critical startup step exhausts all retries."""


class StartupStepError(RuntimeError):
    """Raised by an individual step to signal failure (caught by retry loop)."""
