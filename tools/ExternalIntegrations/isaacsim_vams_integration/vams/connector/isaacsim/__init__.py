"""
VAMS Connector for Isaac Sim.

This Omniverse Kit extension provides integration with the Visual Asset
Management System (VAMS) for browsing, downloading, uploading, and managing
3D assets directly from within Isaac Sim.

Quick start (scripting, without UI):
    from vams.connector.isaacsim import IsaacVAMSConnector

    connector = IsaacVAMSConnector()
    connector.login("user@example.com", "password")
    databases = connector.list_databases()
"""

from .extension import VamsConnectorExtension  # noqa: F401 - discovered by Kit
from .connector import IsaacVAMSConnector
from .vams_cli_service import (
    VamsCliService,
    VamsCliError,
    VamsAuthenticationError,
    VamsNotInstalledError,
    VamsProfileNotSetupError,
    AuthStatus,
    Database,
    Asset,
    AssetFile,
    DownloadResult,
    Workflow,
    WorkflowExecution,
)

__all__ = [
    "VamsConnectorExtension",
    "IsaacVAMSConnector",
    "VamsCliService",
    "VamsCliError",
    "VamsAuthenticationError",
    "VamsNotInstalledError",
    "VamsProfileNotSetupError",
    "AuthStatus",
    "Database",
    "Asset",
    "AssetFile",
    "DownloadResult",
    "Workflow",
    "WorkflowExecution",
]
