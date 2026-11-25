"""Shows VAMS in the Isaac Sim Content window as an Omniverse Storage connection.

The extension starts ``storage_adapter/server.py`` with the VAMS CLI's own Python as a child process on loopback,
then registers its discovery URL with the Content Browser ("Omniverse Storage" > "VAMS"), or, without the
Content Browser, with the Omniverse client library directly so ``vams://`` URLs still resolve in scripts.
The Content Browser's tree is ``VAMS > vams > <database> > <asset name> (<asset id>) > <files>``.

The client library asks for a sign-in token for that one discovery origin through
``omni.client.register_storage_auth_callback``; the callback answers only for the exact origin
(scheme, host, port) of this adapter, with the user's own VAMS CLI token, read by ``storage_adapter/token_helper.py``
through a pipe. The token is never written to a file or a log. The adapter forwards it to VAMS on every call.

Needs Kit 110.2 or later (Isaac Sim 6.1+): earlier Kit has no Omniverse Storage provider in the client library.
"""
import asyncio
import collections
import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import warnings
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

SERVICE_NAME = "VAMS"
_HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_SCRIPT = os.path.join(_HERE, "storage_adapter", "server.py")
TOKEN_SCRIPT = os.path.join(_HERE, "storage_adapter", "token_helper.py")
READY_TIMEOUT_SECONDS = 30.0
TOKEN_TIMEOUT_SECONDS = 20.0
# A token VAMS refused is not handed out again for this long; until then a request fails at once instead of
# waiting. The client library asks again, about twice a second and for as long as the request lasts, whenever
# the token it holds is refused, so without this a request made after the sign-in expired never returns.
REFUSED_RETRY_SECONDS = 60.0
# After a refusal the client library does not ask again on its own, so the profile is checked this often and the
# connection made again once it holds a new sign-in, or the refused one is due for another try.
SIGN_IN_POLL_SECONDS = 30.0


class ContentConnectionError(Exception):
    """The Content window connection could not be started; the message is safe to show."""


def origin_of(url):
    """(scheme, lowercase host, port with defaults) of a URL, or None. Exact-origin matching only."""
    try:
        parts = urlsplit(url or "")
        if not parts.scheme or not parts.hostname or parts.username or parts.password:
            return None
        port = parts.port or {"http": 80, "https": 443}.get(parts.scheme.lower())
    except ValueError:
        return None
    return parts.scheme.lower(), parts.hostname.lower(), port


def cli_python(vamscli_path=None):
    """The Python interpreter of the VAMS CLI's environment, or None.

    The ``vamscli`` command is a launcher written into the environment's bin (or Scripts) folder beside its
    ``python``; following a symlink such as ``/usr/local/bin/vamscli`` reaches it. Otherwise the launcher's
    ``#!`` line names the interpreter.
    """
    found = vamscli_path if vamscli_path and os.path.isfile(vamscli_path) else shutil.which(vamscli_path or "vamscli")
    if not found:
        return None
    real = os.path.realpath(found)
    folder = os.path.dirname(real)
    for name in ("python", "python3", "python.exe"):
        candidate = os.path.join(folder, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    try:
        with open(real, "rb") as handle:
            first = handle.readline(512).decode("utf-8", "ignore").strip()
    except OSError:
        return None
    if first.startswith("#!"):
        interpreter = first[2:].strip().split()[0] if first[2:].strip() else ""
        if os.path.isfile(interpreter):
            return interpreter
    return None


def has_storage_support(omni_client):
    """True when this Kit's client library can register an Omniverse Storage provider with a sign-in callback."""
    return all(hasattr(omni_client, name) for name in (
        "register_storage_auth_callback", "set_storage_access_token", "register_storage_from_discovery"))


class VamsContentConnection:
    """Owns the storage adapter process and its registration for one VAMS CLI profile."""

    def __init__(self, profile, vamscli_path=None, omni_client=None, content_browser_api=None,
                 popen=subprocess.Popen, run=subprocess.run, registry=None, schedule=None, clock=time.monotonic,
                 loop=None):
        self.profile = profile
        self.vamscli_path = vamscli_path
        self._client = omni_client
        self._browser_api = content_browser_api
        self._popen = popen
        self._run = run
        self._registry = registry
        self._schedule = schedule or _ensure_future
        self._process = None
        self._discovery_url = None
        self._origin = None
        self._auth_registration = None
        self._storage_registration = None
        self._service_id = None
        self._stderr_tail = collections.deque(maxlen=20)
        self._lock = threading.Lock()
        self._clock = clock
        self._given = None          # fingerprint of the token last handed to the client library
        self._refused = {}          # fingerprint -> when VAMS was seen refusing it
        self._refusal_logged = False
        self._loop = loop
        self._watching = False

    @property
    def running(self):
        return self._process is not None and self._process.poll() is None

    @property
    def discovery_url(self):
        return self._discovery_url

    # ----------------------------------------------------------------------------------------------------------
    # Sign-in callback
    # ----------------------------------------------------------------------------------------------------------

    def read_token(self):
        """The profile's VAMS token through token_helper.py, or None. The token travels only through the pipe."""
        python = cli_python(self.vamscli_path)
        if not python:
            return None
        try:
            result = self._run([python, TOKEN_SCRIPT, "--profile", self.profile], capture_output=True, text=True,
                               timeout=TOKEN_TIMEOUT_SECONDS, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout.strip().splitlines()[-1]).get("token") or None
        except (ValueError, IndexError):
            return None

    def on_auth_request(self, auth_handle, server):
        """``register_storage_auth_callback`` callback. Answers only for this adapter's exact origin.

        The client library asks once, then again whenever VAMS refuses the token it holds (the adapter answers
        UNAUTHENTICATED). A request for a new token right after one was handed out therefore marks that token as
        refused. A refused token is not handed out again for REFUSED_RETRY_SECONDS, so the request fails at once;
        a different token in the profile (a new sign-in, a renewed workstation lease) is handed out at once.
        """
        if self._origin is None or origin_of(server) != self._origin:
            return False
        if not self.running:
            # The adapter has exited, so another local process may hold its port now: the token is not handed
            # to whatever answers there. The connection is made again at the next sign-in or start.
            logger.warning("VAMS Content window: the storage adapter for profile %s is not running; no sign-in "
                           "was handed out", self.profile)
            self._client.authentication_cancel(auth_handle)
            return True
        now = self._clock()
        if self._given is not None:
            self._refused[self._given] = now
            self._given = None
        token = self.read_token()
        if not token:
            self._refuse(auth_handle, "the VAMS CLI profile has no current sign-in")
            return True
        print_ = hashlib.sha256(token.encode()).hexdigest()
        refused_at = self._refused.get(print_)
        if refused_at is not None and now - refused_at < REFUSED_RETRY_SECONDS:
            self._refuse(auth_handle, "VAMS refused the current sign-in")
            return True
        self._refused.pop(print_, None)
        self._given = print_
        self._refusal_logged = False
        return bool(self._client.set_storage_access_token(auth_handle, token))

    def _refuse(self, auth_handle, reason):
        if not self._refusal_logged:
            logger.warning("VAMS Content window: %s for profile %s; sign in again (on an EDH desktop, renew the "
                           "workstation lease) to browse VAMS", reason, self.profile)
            self._refusal_logged = True
        self._client.authentication_cancel(auth_handle)
        self._watch_for_sign_in()

    def _watch_for_sign_in(self):
        """Called on the client library's thread. Starts, once, a check on Kit's event loop that reconnects when
        the profile holds a usable sign-in again."""
        if self._watching or self._loop is None:
            return
        self._watching = True
        try:
            self._loop.call_soon_threadsafe(lambda: self._schedule(self._sign_in_watch()))
        except RuntimeError:
            self._watching = False

    def sign_in_usable(self, token):
        """True when `token` is not a token VAMS refused within REFUSED_RETRY_SECONDS."""
        refused_at = self._refused.get(hashlib.sha256(token.encode()).hexdigest())
        return refused_at is None or self._clock() - refused_at >= REFUSED_RETRY_SECONDS

    async def _sign_in_watch(self):
        try:
            while self.running:
                await asyncio.sleep(SIGN_IN_POLL_SECONDS)
                token = await asyncio.get_event_loop().run_in_executor(None, self.read_token)
                if token and self.sign_in_usable(token):
                    self.reconnect()
                    return
        finally:
            self._watching = False

    def reconnect(self):
        """Makes the connection again with a new adapter, so the client library asks for a sign-in again (once it
        has been refused one, it does not ask again for the same server). On Kit's main thread."""
        if not self.running:
            return None
        self.stop()
        try:
            return self.start()
        except ContentConnectionError as error:
            logger.warning("VAMS Content window connection not restarted: %s", error)
            return None

    # ----------------------------------------------------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------------------------------------------------

    def start(self):
        """Start the adapter and register it. Blocks until the adapter is ready (a second or two)."""
        with self._lock:
            if self.running:
                return self._discovery_url
            # Entries an earlier session left in omniverse.toml point at a loopback port that is gone. They are
            # removed before the adapter starts, so they go even when this start fails.
            if self._browser_api is not None and hasattr(self._browser_api, "add_discovery_server"):
                self._remove_stale_entries(self._browser_api)
            if not has_storage_support(self._client):
                raise ContentConnectionError(
                    "the Content window connection needs Isaac Sim 6.1 or later (Kit 110.2+)")
            python = cli_python(self.vamscli_path)
            if not python:
                raise ContentConnectionError("the VAMS CLI's Python environment was not found")
            try:
                # stdin is a pipe held only by this process: when Isaac Sim ends, however it ends, the pipe closes
                # and the adapter exits (--watch-stdin), so no adapter outlives it.
                self._process = self._popen(
                    [python, SERVER_SCRIPT, "--profile", self.profile, "--watch-stdin"], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, close_fds=True)
            except OSError as error:
                raise ContentConnectionError(f"the VAMS storage adapter could not start ({type(error).__name__})")
            threading.Thread(target=self._drain_stderr, args=(self._process,), daemon=True).start()
            ready = self._read_ready_line()
            if not ready:
                detail = self._stop_process()
                raise ContentConnectionError(
                    "the VAMS storage adapter did not start" + (f": {detail}" if detail else ""))
            self._discovery_url = ready["discovery"]
            self._origin = origin_of(self._discovery_url)
            if self._origin is None or self._origin[1] != "127.0.0.1":
                self._stop_process()
                raise ContentConnectionError("the VAMS storage adapter reported a non-loopback address")
            if self._loop is None:
                self._loop = _current_loop()
            self._given = None
            self._auth_registration = self._client.register_storage_auth_callback(self.on_auth_request)
            self._register()
            return self._discovery_url

    def _drain_stderr(self, process):
        """Keeps the adapter's stderr pipe from filling; the last lines explain a failed start."""
        try:
            for line in process.stderr:
                self._stderr_tail.append(line.rstrip()[:200])
        except (OSError, ValueError, TypeError):
            pass

    def _read_ready_line(self):
        box = {}

        def read():
            try:
                box["line"] = self._process.stdout.readline()
            except (OSError, ValueError):
                box["line"] = ""
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(READY_TIMEOUT_SECONDS)
        try:
            ready = json.loads(box.get("line") or "")
        except ValueError:
            return None
        return ready if isinstance(ready, dict) and ready.get("discovery") else None

    def _register(self):
        api = self._browser_api
        if api is not None and hasattr(api, "add_discovery_server"):
            self._remove_stale_entries(api)
            self._service_id = api.add_discovery_server(self._discovery_url, service_name=SERVICE_NAME)
            self._start_discovery()
            return
        self._storage_registration = self._client.register_storage_from_discovery(self._discovery_url)

    def _connection_registry(self):
        if self._registry is not None:
            return self._registry
        try:
            from omni.kit.widget.connection_manager import get_instance
        except ImportError:
            return None
        manager = get_instance()
        return getattr(manager, "registry", None) if manager else None

    def _start_discovery(self):
        """The Content Browser starts discovery only when its entry is expanded. Starting it now makes
        ``vams://`` URLs resolve at once, for stages and scripts as well. Runs on Kit's event loop, which the
        connection manager requires."""
        registry = self._connection_registry()
        url = self._discovery_url
        if registry is None or not hasattr(registry, "start_discovery") or not url:
            return

        async def kick():
            event = registry.start_discovery(url)
            if event is not None and hasattr(event, "wait"):
                await event.wait()
        self._schedule(kick())

    def _remove_stale_entries(self, api):
        """Removes VAMS entries a previous session left behind in the user's omniverse.toml (each points at a
        loopback port that is gone, and shows that session's addresses). They are removed from the connection
        registry by URL: the Content window may not have built its own entries yet when this runs, and it follows
        the registry's removal events."""
        try:
            registry = self._connection_registry()
            servers = list(registry.get_discovery_servers()) if registry else []
        except Exception:  # noqa: BLE001 - the registry is optional; stale entries only clutter the tree
            return
        for server in servers:
            url = getattr(server, "discovery_url", "") or ""
            origin = origin_of(url) or (None, None, None)
            if getattr(server, "name", None) != SERVICE_NAME or origin[1] != "127.0.0.1" or url == self._discovery_url:
                continue
            try:
                if hasattr(registry, "remove_discovery_server"):
                    registry.remove_discovery_server(url)
                else:
                    api.remove_discovery_server(server.service_id)
            except Exception:  # noqa: BLE001
                pass

    def stop(self):
        """Unregister and stop the adapter. Safe to call more than once."""
        with self._lock:
            if self._service_id is not None and self._browser_api is not None:
                try:
                    self._browser_api.remove_discovery_server(self._service_id)
                except Exception:  # noqa: BLE001
                    pass
                registry = self._connection_registry()
                if registry is not None and hasattr(registry, "remove_discovery_server") and self._discovery_url:
                    try:
                        registry.remove_discovery_server(self._discovery_url)  # no-op when already removed
                    except Exception:  # noqa: BLE001
                        pass
            self._service_id = None
            for registration in (self._storage_registration, self._auth_registration):
                if registration is not None and callable(getattr(registration, "stop", None)):
                    try:
                        registration.stop()
                    except Exception:  # noqa: BLE001
                        pass
            self._storage_registration = None
            self._auth_registration = None
            self._stop_process()
            self._discovery_url = None
            self._origin = None

    def _stop_process(self):
        process, self._process = self._process, None
        if process is None:
            return ""
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        return self._stderr_tail[-1] if self._stderr_tail else ""


def _current_loop():
    """Kit's event loop: the running one, else the one Kit set as current; None in a plain interpreter."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        pass
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            return None


def _ensure_future(coroutine):
    try:
        asyncio.ensure_future(coroutine)
    except RuntimeError:
        coroutine.close()


def content_browser_api():
    """The Content Browser's API object, or None when the Content Browser is not loaded."""
    try:
        import omni.kit.window.content_browser as content_browser
    except ImportError:
        return None
    instance = content_browser.get_instance() if hasattr(content_browser, "get_instance") else None
    return getattr(instance, "api", None) if instance else None

