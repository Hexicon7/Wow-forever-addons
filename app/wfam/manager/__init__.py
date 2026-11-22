"""The addon manager: one class put together from a mixin per area (see the modules here)."""
from __future__ import annotations

from .adding import AddingMixin
from .auras import AurasMixin
from .backups import BackupsMixin
from .base import ManagerBase
from .diagnostics import DiagnosticsMixin
from .github import GitHubMixin
from .housekeeping import HousekeepingMixin
from .insights import InsightsMixin
from .local import LocalFoldersMixin
from .selfupdate import SelfUpdateMixin
from .setup_sync import SetupSyncMixin
from .sharing import SharingMixin
from .tukui import TukuiMixin
from .updates import UpdatesMixin
from .versions import VersionsMixin
from .views import ViewsMixin
from .wago import WagoMixin


class Manager(ViewsMixin, InsightsMixin, HousekeepingMixin, UpdatesMixin, SelfUpdateMixin, AddingMixin, GitHubMixin, AurasMixin, TukuiMixin, WagoMixin, SharingMixin, DiagnosticsMixin, LocalFoldersMixin, VersionsMixin, SetupSyncMixin, BackupsMixin, ManagerBase):
    """Installs, updates and keeps track of addons. Every method lives in one of the mixins."""

__all__ = ["Manager"]
