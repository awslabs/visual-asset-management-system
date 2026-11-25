"""VAMS as an Omniverse Storage provider for the Isaac Sim Content window.

``server.py`` runs as its own process, started by the extension with the VAMS CLI's Python, never inside Kit.
``token_helper.py`` runs the same way, once per sign-in request from the client library. ``naming.py`` is
shared, pure Python. ``_proto/`` holds Python modules generated from NVIDIA's public Omniverse Storage API
protos (``proto/``, Apache-2.0); see ``proto/README.md``.

This package is not imported by the extension's Kit modules: the extension starts the two scripts by path.
"""
