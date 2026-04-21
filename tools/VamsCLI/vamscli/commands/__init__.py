"""Commands module for VamsCLI."""

from .setup import setup
from .auth import auth
from .assets import assets
from .assetsExport import assets_export
from .asset_version import asset_version
from .asset_links import asset_links
from .file import file
from .profile import profile
from .database import database
from .tag import tag
from .tag_type import tag_type
from .metadata import metadata
from .metadata_schema import metadata_schema
from .features import features
from .search import search
from .user import user
from .roleUserConstraints import role

__all__ = [
    'setup', 'auth', 'assets', 'assets_export', 'asset_version', 'asset_links', 
    'file', 'profile', 'database', 'tag', 
    'tag_type', 'metadata', 'metadata_schema', 'features', 'search', 'user', 'role'
]
