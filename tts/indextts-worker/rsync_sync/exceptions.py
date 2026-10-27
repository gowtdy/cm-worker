"""Custom exception hierarchy for the rsync sync subsystem."""


class SyncError(Exception):
    """Base exception for all sync-related errors."""


class TransportError(SyncError):
    """Raised when an SSH/rsync transport operation fails."""


class ConnectionError(TransportError):
    """Raised when an SSH ControlMaster connection cannot be established."""


class TransferError(TransportError):
    """Raised when an rsync file transfer fails."""


class MkdirError(TransportError):
    """Raised when mkdir on the remote host fails."""


class QueueFullError(SyncError):
    """Raised when the sync queue is full and the enqueue times out."""


class WorkersNotReadyError(SyncError):
    """Raised when trying to enqueue a job before workers are started."""


class ConfigError(SyncError):
    """Raised when sync configuration is invalid."""


class ProxyConfigError(ConfigError):
    """Raised when proxy configuration cannot be loaded or is empty."""