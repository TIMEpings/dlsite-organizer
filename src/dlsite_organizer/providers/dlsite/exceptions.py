"""Failures surfaced by the DLsite provider boundary."""


class DlsiteProviderError(Exception):
    """Base class for expected provider failures."""


class DlsiteConnectionError(DlsiteProviderError):
    """The storefront could not be reached."""


class DlsiteHttpError(DlsiteProviderError):
    """The storefront returned an unexpected HTTP status."""


class WorkNotFoundError(DlsiteProviderError):
    """The requested work does not exist in the configured section."""


class DlsiteParseError(DlsiteProviderError):
    """The response did not contain trustworthy core metadata."""
