# Proxy package: redirect sub-package resolution to the real source tree.
# Kit adds config/ to sys.path, but the actual code lives in ../vams/.
import os as _os

__path__ = [_os.path.normpath(_os.path.join(_os.path.dirname(__file__), "..", "..", "vams"))]
