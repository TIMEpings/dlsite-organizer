"""Single source of truth for the application version."""

__version__ = "1.2.1"


def application_user_agent() -> str:
    """Return the User-Agent used by the application's HTTP clients."""
    return f"dlsite-organizer/{__version__}"
