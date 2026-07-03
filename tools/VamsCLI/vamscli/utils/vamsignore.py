"""Ignore-file (.vamsignore) pattern matching for sync operations."""

from pathlib import Path
from typing import List, Optional

import pathspec

from ..constants import DEFAULT_IGNORE_FILE_NAME
from .exceptions import InvalidSyncIgnoreFileError


class VamsIgnoreMatcher:
    """Matches asset-relative file keys against gitignore-style patterns.

    Patterns follow .gitignore semantics (wildcards, '**', negation with '!',
    directory-only patterns with a trailing '/', last match wins). Keys are
    matched against their asset-relative path with the leading slash removed.
    """

    def __init__(self, patterns: Optional[List[str]] = None):
        self.patterns = list(patterns or [])
        self._spec = pathspec.PathSpec.from_lines('gitwildmatch', self.patterns)

    @classmethod
    def from_file(cls, ignore_file: Path,
                  extra_patterns: Optional[List[str]] = None) -> 'VamsIgnoreMatcher':
        """Load patterns from an ignore file, appending any extra patterns."""
        if not ignore_file.exists():
            raise InvalidSyncIgnoreFileError(f"Ignore file not found: {ignore_file}")
        if not ignore_file.is_file():
            raise InvalidSyncIgnoreFileError(f"Ignore file path is not a file: {ignore_file}")
        try:
            content = ignore_file.read_text(encoding='utf-8-sig')
        except (OSError, UnicodeDecodeError) as e:
            raise InvalidSyncIgnoreFileError(f"Unable to read ignore file {ignore_file}: {e}")
        return cls(content.splitlines() + list(extra_patterns or []))

    @classmethod
    def for_directory(cls, local_directory: Path,
                      ignore_file_override: Optional[str] = None,
                      extra_patterns: Optional[List[str]] = None) -> 'VamsIgnoreMatcher':
        """Build a matcher for a local sync directory.

        Uses the override file when provided (must exist), otherwise the
        default ignore file in the directory root when present, otherwise
        just the extra patterns.
        """
        if ignore_file_override:
            return cls.from_file(Path(ignore_file_override), extra_patterns)

        default_file = local_directory / DEFAULT_IGNORE_FILE_NAME
        if default_file.is_file():
            return cls.from_file(default_file, extra_patterns)

        return cls(list(extra_patterns or []))

    @property
    def has_patterns(self) -> bool:
        """Whether any effective (non-comment) patterns are loaded."""
        return any(p.strip() and not p.strip().startswith('#') for p in self.patterns)

    def is_ignored(self, relative_key: str) -> bool:
        """Check whether an asset-relative key matches the ignore patterns."""
        if not self.patterns:
            return False
        normalized = relative_key.replace('\\', '/').lstrip('/')
        if not normalized:
            return False
        return self._spec.match_file(normalized)
