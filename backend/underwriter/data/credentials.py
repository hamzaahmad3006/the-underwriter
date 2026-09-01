"""Read-only credentials for the data layer (ALP-004, FR-000).

The system holds two Alpaca key pairs. The trading pair lives only in the
Execution Engine's process scope; this module deliberately cannot return it,
so a mistake here fails at import rather than at 3am against a live book.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class MissingCredentialsError(RuntimeError):
    """No usable read-only credentials were configured."""


@dataclass(frozen=True, slots=True)
class DataCredentials:
    """A read-only Alpaca key pair, and where it came from."""

    api_key: str
    secret_key: str
    is_dedicated_data_key: bool

    def __repr__(self) -> str:
        """Never let a secret reach a log line or a traceback."""
        kind = "dedicated" if self.is_dedicated_data_key else "shared"
        return f"DataCredentials(kind={kind}, api_key=***, secret_key=***)"


def load_data_credentials() -> DataCredentials:
    """Prefer a dedicated read-only key; fall back to the account key.

    ALP-004 wants separate data credentials so the data path cannot trade even
    in principle. Alpaca does not always issue them, so the fallback is the
    account pair — recorded as `is_dedicated_data_key=False` so the degradation
    is visible in `/health/deep` rather than silent.
    """
    data_key = os.environ.get("ALPACA_DATA_API_KEY", "").strip()
    data_secret = os.environ.get("ALPACA_DATA_SECRET_KEY", "").strip()

    if data_key and data_secret:
        return DataCredentials(data_key, data_secret, is_dedicated_data_key=True)

    api_key = os.environ.get("ALPACA_API_KEY", "").strip()
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "").strip()

    if api_key and secret_key:
        return DataCredentials(api_key, secret_key, is_dedicated_data_key=False)

    raise MissingCredentialsError(
        "No Alpaca credentials configured. Set ALPACA_DATA_API_KEY/"
        "ALPACA_DATA_SECRET_KEY (preferred, ALP-004) or ALPACA_API_KEY/"
        "ALPACA_SECRET_KEY."
    )


def has_credentials() -> bool:
    """For readiness reporting, without raising."""
    try:
        load_data_credentials()
    except MissingCredentialsError:
        return False
    return True
