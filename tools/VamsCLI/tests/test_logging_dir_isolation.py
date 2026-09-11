"""Guards that the test suite never touches the real user log directory.

The `redirect_log_dir` autouse fixture in `conftest.py` exists because `initialize_logging()`
constructs a `RotatingFileHandler`, which opens its file eagerly and raises `FileNotFoundError`
when the parent directory does not exist. `mock_logging` patches `ensure_log_dir` away, so nothing
creates it; a test that imported `initialize_logging` directly (so the `initialize_logging` patch
does not apply to its reference) then fails on any machine without `~/.config/vamscli/logs` —
i.e. every fresh CI runner, while a developer machine passes because real CLI use already made the
directory. These tests fail if that fixture is removed or stops covering a code path.
"""

from pathlib import Path

import pytest

import vamscli.utils.logging as vamscli_logging
from vamscli.constants import LOG_DIR_NAME, LOG_FILE_NAME, get_config_dir
from vamscli.utils.logging import get_log_file_path, initialize_logging


def _real_log_dir() -> Path:
    """The log directory the CLI would use in production, computed without the patch."""
    return get_config_dir() / LOG_DIR_NAME


class TestLogDirRedirection:
    def test_get_log_dir_is_not_the_real_user_dir(self, redirect_log_dir):
        # Resolve through the module, not a from-import: `from ... import get_log_dir` binds the
        # original function object, which patch() cannot reach. The module attribute is what the
        # production callers inside vamscli.utils.logging actually consult.
        assert vamscli_logging.get_log_dir() == redirect_log_dir
        assert vamscli_logging.get_log_dir() != _real_log_dir()

    def test_log_file_path_stays_under_the_redirected_dir(self, redirect_log_dir):
        # get_log_file_path() derives from get_log_dir(), so redirecting the one function is
        # enough to contain every log path in the module.
        log_path = get_log_file_path()
        assert log_path.parent == redirect_log_dir
        assert log_path.name == LOG_FILE_NAME

    def test_redirected_dir_exists_so_the_file_handler_can_open(self, redirect_log_dir):
        # mock_logging patches ensure_log_dir out, so the directory must already exist or
        # RotatingFileHandler raises FileNotFoundError.
        assert redirect_log_dir.is_dir()

    @pytest.mark.no_mock_logging
    def test_real_initialize_logging_writes_only_into_the_temp_dir(self, redirect_log_dir):
        """The failing CI path, run for real: no_mock_logging leaves initialize_logging unpatched."""
        vamscli_logging._logger = None
        try:
            logger = initialize_logging(verbose=False)
            assert logger is not None
            logger.info("isolation probe")
            for handler in logger.handlers:
                handler.flush()

            written = list(redirect_log_dir.glob(f"{LOG_FILE_NAME}*"))
            assert written, "expected the log file to land in the redirected directory"
            # Assert on where the handler is pointed, not on the real file's absence: a developer
            # machine that has ever run the CLI already has that file, so an existence check there
            # would fail for reasons unrelated to isolation.
            handler_paths = [
                Path(h.baseFilename) for h in logger.handlers if hasattr(h, "baseFilename")
            ]
            assert handler_paths, "expected a file handler on the logger"
            for handler_path in handler_paths:
                assert handler_path.parent == redirect_log_dir
                assert handler_path.parent != _real_log_dir()
        finally:
            if vamscli_logging._logger is not None:
                for handler in list(vamscli_logging._logger.handlers):
                    handler.close()
                    vamscli_logging._logger.removeHandler(handler)
            vamscli_logging._logger = None
