"""Exceptions the program raises (shown to the user as plain messages)."""
from __future__ import annotations


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class UserError(Exception):
    """An expected problem with a clear message for the user."""


class CurseForgeError(UserError):
    pass


class NotFound(CurseForgeError):
    pass


class DistributionDisabled(CurseForgeError):
    pass


class InstallError(UserError):
    pass


class BusyError(Exception):
    pass


# --------------------------------------------------------------------------- #
# HTTP: reused connections (keep-alive) + gzip, so many small requests stay fast
# --------------------------------------------------------------------------- #
class NetError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason
