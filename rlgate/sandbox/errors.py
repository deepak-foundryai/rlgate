"""Backend errors, in a leaf module every backend can import without cycles."""


class BackendUnavailable(RuntimeError):
    """A backend can't be used or an environment couldn't be stood up; the message says why
    and how to fix it."""


class IsolationUnavailable(BackendUnavailable):
    """The backend can't give an overlay that is isolated from everything outside the
    workspace (the local backend runs on this machine), so a payload that writes outside the
    workspace (site-packages, /tmp) must not run there."""


class BuildFailed(BackendUnavailable):
    """An image the task uses failed to build (or pull): a defect in the task, reported as a
    finding (RG005), not an infrastructure error. ``failures`` is [(which image, log tail)]."""

    def __init__(self, failures: list[tuple[str, str]]) -> None:
        self.failures = failures
        super().__init__("; ".join(f"{what} failed to build" for what, _ in failures))


BUILD_COMMANDS = ("docker build", "docker pull", "compose build")


def is_build_error(e: Exception) -> bool:
    """A ``Machine.check`` failure of a build/pull command (its message starts with it)."""
    return str(e).startswith(BUILD_COMMANDS)
