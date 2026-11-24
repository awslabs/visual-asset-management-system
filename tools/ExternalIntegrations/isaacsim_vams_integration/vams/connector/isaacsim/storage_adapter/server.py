"""VAMS Omniverse Storage adapter: VAMS databases, assets and files as an Omniverse Storage provider.

Run by the extension with the VAMS CLI's own Python, as its own process (never inside Kit):

    python server.py --profile <VAMS CLI profile> [--http-port 0] [--grpc-port 0]

It prints one JSON line ``{"discovery": url, "grpc": url, "root": address}`` on stdout when it is ready, and
serves until it is stopped. The tree is ``vams://vams/<database id>/<asset name> (<asset id>)/<file path>``, and
each database the caller may see is a top-level address, so the Content window lists the databases directly under
the connection (the list is read when the connection is made). Read-only.

Three listeners, all bound to 127.0.0.1 only: an HTTP discovery document (Omniverse Discovery catalog, schema
version 2), the Storage API gRPC services the Omniverse client library calls (capabilities, filefolder,
fileobject; v1alpha and v1beta), and a notification consumer for the Content window's folder subscriptions on a
gRPC server of its own.

The adapter holds no credential of its own. Every gRPC call must carry the caller's VAMS token in its
``authorization`` metadata, which the extension's sign-in callback supplies from the user's own VAMS CLI
profile. Each call builds a VAMS CLI ``APIClient`` with that token (``bearer_token`` mode: no profile read or
written, no refresh), so the VAMS authorizer and its permission checks decide every access. A call without a
token is refused (UNAUTHENTICATED). Denied and missing items both answer NOT_FOUND. File bytes never pass
through the adapter: a read answers with a redirect to the VAMS presigned download URL. Tokens are never logged.
"""
import argparse
import base64
import hashlib
import importlib
import json
import os
import sys
import threading
import time
import urllib.parse
from concurrent import futures
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
PROTO_DIR = os.path.join(HERE, "_proto")
if PROTO_DIR not in sys.path:
    sys.path.insert(0, PROTO_DIR)

from naming import MAX_SEGMENT_BYTES, asset_folder, asset_id_of  # noqa: E402

ROOT = "vams://vams"
VERSIONS = ("v1alpha", "v1beta")
SERVICES = ("capabilities", "filefolder", "fileobject")
CACHE_TTL_SECONDS = 30.0
MAX_FOLDER_SUBSCRIPTIONS = 64
PAGE_SIZE = 200


class Denied(Exception):
    """No usable caller token, or VAMS refused it."""


class Missing(Exception):
    """Not found, or not visible to the caller."""


def split_address(address):
    """Path segments of an address under ROOT. Refuses other schemes and '.'/'..' segments (Missing)."""
    text = urllib.parse.unquote(address or "")
    if text.startswith(ROOT):
        text = text[len(ROOT):]
    elif "://" in text:
        raise Missing()
    parts = [p for p in text.split("/") if p]
    if any(p in (".", "..") for p in parts):
        raise Missing()
    return parts


def join_address(*parts):
    return "/".join([ROOT] + [p for p in parts if p])


def caller_token(metadata):
    for key, value in metadata or ():
        if key.lower() == "authorization" and value:
            return value[7:].strip() if value.lower().startswith("bearer ") else value.strip()
    return None


def files_under(files, sub):
    """(sub-folder names, [(file name, item)]) directly under the folder `sub` of an asset's file map."""
    prefix = "/".join(sub) + "/" if sub else ""
    folders, leaves = set(), []
    for rel, item in files.items():
        if not rel.startswith(prefix):
            continue
        rest = rel[len(prefix):]
        if "/" in rest:
            folders.add(rest.split("/", 1)[0])
        else:
            leaves.append((rest, item))
    return sorted(folders), sorted(leaves, key=lambda pair: pair[0])


def parse_vams_date(value):
    """A VAMS date as an aware datetime, or None. VAMS writes ISO 8601 (with or without 'Z'), and database records
    'November 24 2026 - 13:54:15'."""
    from datetime import timezone

    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    for parse in (lambda t: datetime.fromisoformat(t.replace("Z", "+00:00")),
                  lambda t: datetime.strptime(t, "%B %d %Y - %H:%M:%S")):
        try:
            when = parse(text)
        except ValueError:
            continue
        return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return None


def status_for(error):
    """The gRPC status a VAMS CLI error maps to; never the error's own text."""
    import grpc

    name = type(error).__name__
    if isinstance(error, Denied) or (name == "AuthenticationError" and "401" in str(error)):
        return grpc.StatusCode.UNAUTHENTICATED
    if isinstance(error, Missing) or name in ("AuthenticationError", "PermissionDeniedError", "AssetNotFoundError",
                                               "DatabaseNotFoundError"):
        return grpc.StatusCode.NOT_FOUND
    return grpc.StatusCode.UNAVAILABLE


class VamsTree:
    """Reads the VAMS tree as one caller, through the VAMS CLI API client."""

    def __init__(self, api_url, log=None, page_size=PAGE_SIZE, segment_limit=MAX_SEGMENT_BYTES):
        self.api_url = api_url
        self.log = log or (lambda **_: None)
        self.page_size = page_size
        self.segment_limit = segment_limit
        self._cache = {}
        self._lock = threading.Lock()

    def client(self, metadata):
        from vamscli.utils.api_client import APIClient

        token = caller_token(metadata)
        if not token:
            raise Denied()
        return APIClient(self.api_url, bearer_token=token), hashlib.sha256(token.encode()).hexdigest()[:16]

    @staticmethod
    def _pages(fetch):
        token = None
        while True:
            page = fetch(token)
            yield page.get("Items", page.get("items", []))
            token = page.get("NextToken")
            if not token:
                return

    def _cached(self, key, load):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < CACHE_TTL_SECONDS:
                return hit[1]
        value = load()
        with self._lock:
            self._cache[key] = (time.monotonic(), value)
        return value

    def databases(self, api, who=None):
        """The database ids the caller may see, as one page; cached briefly per caller."""
        def load():
            found = []

            def fetch(token):
                params = {"pageSize": self.page_size}
                if token:
                    params["startingToken"] = token
                return api.list_databases(params=params)
            for items in self._pages(fetch):
                found.extend(i["databaseId"] for i in items if i.get("databaseId"))
            return sorted(found)
        yield self._cached(("databases", who or "-"), load)

    def asset_folders(self, api, database_id, who=None):
        """The caller's asset folder names in a database, as one page; cached briefly per caller."""
        from vamscli.constants import API_DATABASE_ASSETS
        from vamscli.utils.request_paths import route_path

        endpoint = route_path(API_DATABASE_ASSETS, databaseId=database_id)

        def load():
            found = []

            def fetch(token):
                params = {"pageSize": self.page_size}
                if token:
                    params["startingToken"] = token
                return api.get(endpoint, include_auth=True, params=params).json()
            for items in self._pages(fetch):
                found.extend(asset_folder(i.get("assetName"), i["assetId"], self.segment_limit)
                             for i in items if i.get("assetId"))
            return sorted(found)
        yield self._cached(("assets", who or "-", database_id), load)

    def files(self, api, who, database_id, asset_id):
        """An asset's current files keyed by path without the leading '/'; cached per caller briefly."""
        def load():
            found = {}
            for items in self._pages(lambda t: api.list_asset_files(database_id, asset_id,
                                                                    {"startingToken": t} if t else {})):
                for item in items:
                    rel = (item.get("relativePath") or "").lstrip("/")
                    if rel and not item.get("isFolder") and not item.get("isArchived"):
                        found[rel] = item
            return found
        return self._cached(("files", who, database_id, asset_id), load)

    def resolve(self, api, who, parts):
        if len(parts) < 3:
            raise Missing()
        database_id, asset_id, rel = parts[0], asset_id_of(parts[1]), "/".join(parts[2:])
        item = self.files(api, who, database_id, asset_id).get(rel)
        if not item:
            raise Missing()
        return database_id, asset_id, rel, item

    @staticmethod
    def download_url(api, database_id, asset_id, rel, version_id=None):
        url = api.download_asset_file(database_id, asset_id, "/" + rel, version_id=version_id or None).get(
            "downloadUrl")
        if not url:
            raise Missing()
        return url


def _register(server, tree, version):
    from google.protobuf.timestamp_pb2 import Timestamp

    def mod(name):
        return importlib.import_module(f"nvidia.omniverse.storage.{name}")

    cap = mod(f"capabilities.{version}.capabilities_pb2")
    cap_grpc = mod(f"capabilities.{version}.capabilities_pb2_grpc")
    ff = mod(f"filefolder.{version}.filefolder_service_pb2")
    ff_grpc = mod(f"filefolder.{version}.filefolder_service_pb2_grpc")
    fo = mod(f"fileobject.{version}.fileobject_pb2")
    fos = mod(f"fileobject.{version}.fileobject_service_pb2")
    fos_grpc = mod(f"fileobject.{version}.fileobject_service_pb2_grpc")

    def resource_info(database_id, asset_id, rel, item):
        stamp = Timestamp()
        created = parse_vams_date(item.get("dateCreatedCurrentVersion"))
        if created:
            stamp.FromDatetime(created)
        identity = base64.urlsafe_b64encode(json.dumps(
            {"d": database_id, "a": asset_id, "p": rel, "v": item.get("versionId") or ""}).encode()).decode()
        return fo.ResourceInfo(resource_identity=fo.ResourceIdentity(encoded_identity=identity),
                               metadata=fo.Metadata(data_object_size=int(item.get("size") or 0),
                                                    last_modified_timestamp=stamp))

    def unary(fn):
        def call(self, request, context):
            try:
                return fn(self, request, context)
            except Exception as error:  # noqa: BLE001 - mapped to a status, never echoed
                tree.log(rpc=fn.__name__, error=type(error).__name__)
                context.abort(status_for(error), "request failed")
        call.__name__ = fn.__name__
        return call

    def stream(fn):
        def call(self, request, context):
            try:
                yield from fn(self, request, context)
            except Exception as error:  # noqa: BLE001 - mapped to a status, never echoed
                tree.log(rpc=fn.__name__, error=type(error).__name__)
                context.abort(status_for(error), "request failed")
        call.__name__ = fn.__name__
        return call

    class Capabilities(cap_grpc.CapabilitiesServiceServicer):
        def ListServices(self, request, context):
            return cap.ListServicesResponse(services=[cap.ServiceEntry(service_name=n, service_versions=list(VERSIONS))
                                                      for n in SERVICES])

        def ListTopLevelAddresses(self, request, context):
            """One top-level address per database the caller may see, so the Content window lists the databases
            directly under the connection. Without a usable sign-in (or with no database), the root itself."""
            try:
                api, who = tree.client(context.invocation_metadata())
                addresses = [join_address(d) for ids in tree.databases(api, who) for d in ids]
            except Exception as error:  # noqa: BLE001 - the root is always a valid answer
                tree.log(rpc="ListTopLevelAddresses", error=type(error).__name__)
                addresses = []
            return cap.ListTopLevelAddressesResponse(items=[cap.TopLevelAddressEntry(top_level_address=a)
                                                            for a in addresses or [ROOT]])

        def ListRoutes(self, request, context):
            return cap.ListRoutesResponse(items=[cap.Route(wildcard_pattern=f"{ROOT}/**")])

    class Folders(ff_grpc.FileFolderServiceServicer):
        def GetFolderMode(self, request, context):
            return ff.GetFolderModeResponse(folder_mode=ff.FOLDER_MODE_NATIVE)

        def _list(self, request, context, with_stat):
            api, who = tree.client(context.invocation_metadata())
            parts = split_address(request.folder.uri)
            tree.log(rpc="ListStat" if with_stat else "List", depth=len(parts))
            response = ff.ListStatResponse if with_stat else ff.ListResponse
            if not parts:
                for ids in tree.databases(api, who):
                    yield response(subfolder_addresses=[ff.FolderAddress(uri=join_address(i)) for i in ids])
                return
            if len(parts) == 1:
                for names in tree.asset_folders(api, parts[0], who):
                    yield response(subfolder_addresses=[ff.FolderAddress(uri=join_address(parts[0], n))
                                                        for n in names])
                return
            database_id, segment, sub = parts[0], parts[1], parts[2:]
            asset_id = asset_id_of(segment)
            folders, leaves = files_under(tree.files(api, who, database_id, asset_id), sub)
            if sub and not folders and not leaves:
                raise Missing()
            folder_addresses = [ff.FolderAddress(uri=join_address(database_id, segment, *sub, f)) for f in folders]
            if with_stat:
                yield response(subfolder_addresses=folder_addresses, entries=[
                    ff.ListItem(resource_address=join_address(database_id, segment, *sub, name),
                                resource_info=resource_info(database_id, asset_id, "/".join(sub + [name]), item))
                    for name, item in leaves])
            else:
                yield response(subfolder_addresses=folder_addresses,
                               sub_resource_addresses=[join_address(database_id, segment, *sub, name)
                                                       for name, _ in leaves])

        @stream
        def List(self, request, context):
            yield from self._list(request, context, False)

        @stream
        def ListStat(self, request, context):
            yield from self._list(request, context, True)

    class Objects(fos_grpc.FileObjectServiceServicer):
        @unary
        def Stat(self, request, context):
            api, who = tree.client(context.invocation_metadata())
            database_id, asset_id, rel, item = tree.resolve(api, who, split_address(request.resource_address))
            return fos.StatResponse(resource_info=resource_info(database_id, asset_id, rel, item))

        def IsReadOnly(self, request, context):
            return fos.IsReadOnlyResponse(is_read_only=True)

        @stream
        def ReadFromAddress(self, request, context):
            api, who = tree.client(context.invocation_metadata())
            database_id, asset_id, rel, item = tree.resolve(api, who, split_address(request.resource_address))
            tree.log(rpc="ReadFromAddress", size=int(item.get("size") or 0))
            yield fos.ReadFromAddressResponse(resource_info=resource_info(database_id, asset_id, rel, item))
            yield fos.ReadFromAddressResponse(redirect=fo.Redirect(
                redirect_target_url=tree.download_url(api, database_id, asset_id, rel)))

        @stream
        def Read(self, request, context):
            api, who = tree.client(context.invocation_metadata())
            try:
                ref = json.loads(base64.urlsafe_b64decode(request.resource_identity.encoded_identity))
                parts = [ref["d"], ref["a"]] + ref["p"].split("/")
            except Exception:  # noqa: BLE001
                raise Missing()
            database_id, asset_id, rel, item = tree.resolve(api, who, parts)
            tree.log(rpc="Read", size=int(item.get("size") or 0))
            yield fos.ReadResponse(metadata=resource_info(database_id, asset_id, rel, item).metadata)
            yield fos.ReadResponse(redirect=fo.Redirect(
                redirect_target_url=tree.download_url(api, database_id, asset_id, rel, ref.get("v"))))

    cap_grpc.add_CapabilitiesServiceServicer_to_server(Capabilities(), server)
    ff_grpc.add_FileFolderServiceServicer_to_server(Folders(), server)
    fos_grpc.add_FileObjectServiceServicer_to_server(Objects(), server)
    return [f"nvidia.omniverse.storage.{n}.{version}.{s}" for n, s in
            (("capabilities", "CapabilitiesService"), ("filefolder", "FileFolderService"),
             ("fileobject", "FileObjectService"))]


class CallLog:
    """gRPC server interceptor: one log line per call, by method name only (never metadata or tokens), including
    methods the adapter does not implement, which is how an unmet client expectation shows up."""

    def __new__(cls, log):
        import grpc

        class _Interceptor(grpc.ServerInterceptor):
            def intercept_service(self, continuation, handler_call_details):
                handler = continuation(handler_call_details)
                log(call=handler_call_details.method, implemented=handler is not None)
                return handler
        return _Interceptor()


def _register_events(server, tree):
    """The notification consumer the client library uses for the Content window's folder subscriptions
    (omni.storage.created / deleted / dir_created / dir_deleted under the folder). Without one in the catalog the
    library logs 'ListSubscribeRequest ... Not Supported' for every folder opened. The library keeps one stream and
    adds each watched folder as a filter update, which is acknowledged; no events are delivered, so a folder shows
    changes made elsewhere when it is opened again."""
    import uuid

    from nvidia.omniverse.notifications.consumer.v1beta import event_consumer_pb2 as ec
    from nvidia.omniverse.notifications.consumer.v1beta import event_consumer_pb2_grpc as ec_grpc

    class Events(ec_grpc.EventConsumerServiceServicer):
        def ConsumeNonDurableEvents(self, request_iterator, context):
            token = uuid.uuid4().hex
            yield ec.ConsumeNonDurableEventsResponse(reconnect_token=token, supports_filter_update_acks=True)
            for request in request_iterator:
                groups = [(g.event_type, [(int(f.filter_type), f.resource_id[:80]) for f in g.filters])
                          for g in request.filter_groups]
                tree.log(rpc="ConsumeNonDurableEvents", groups=groups[:6], reconnect=bool(request.reconnect_token))
                yield ec.ConsumeNonDurableEventsResponse(
                    reconnect_token=token, supports_filter_update_acks=True,
                    filter_update_ack=ec.FilterUpdateAck(applied_filter_groups=list(request.filter_groups)))

    ec_grpc.add_EventConsumerServiceServicer_to_server(Events(), server)
    return "nvidia.omniverse.notifications.consumer.v1beta.EventConsumerService"


def exit_when_parent_goes(stream, on_gone):
    """Calls on_gone once `stream` (the adapter's stdin, a pipe the extension holds) reaches end of file, which
    happens when the extension's process ends for any reason, so the adapter never outlives Isaac Sim. Runs in a
    daemon thread. Works the same on Linux, macOS and Windows."""
    def watch():
        try:
            while stream.read(4096):
                pass
        except (OSError, ValueError):
            pass
        on_gone()
    thread = threading.Thread(target=watch, name="vams-parent-watch", daemon=True)
    thread.start()
    return thread


def discovery_document(grpc_port, services, event_port=None, event_services=()):
    document = {"schema-version": 2, "services": [{
        "name": "vams-storage", "capability_source": "grpc",
        "grpc": {"url": f"http://127.0.0.1:{grpc_port}", "endpoints": [{"service": s} for s in services]}}]}
    if event_port and event_services:
        document["services"].append({
            "name": "vams-notification-consumer", "capability_source": "grpc",
            "grpc": {"url": f"http://127.0.0.1:{event_port}", "endpoints": [{"service": s} for s in event_services]}})
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description="VAMS Omniverse Storage adapter (loopback only)")
    parser.add_argument("--profile", required=True, help="VAMS CLI profile whose deployment to serve")
    parser.add_argument("--api-url", default=None, help="VAMS API base URL; default: the profile's")
    parser.add_argument("--http-port", type=int, default=0)
    parser.add_argument("--grpc-port", type=int, default=0)
    parser.add_argument("--watch-stdin", action="store_true",
                        help="exit when stdin closes (the extension passes a pipe it holds)")
    parser.add_argument("--log", default=None, help="append one JSON line per call (no tokens); default: the "
                        "VAMS_STORAGE_ADAPTER_LOG environment variable")
    args = parser.parse_args(argv)
    args.log = args.log or os.environ.get("VAMS_STORAGE_ADAPTER_LOG") or None

    import grpc

    api_url = args.api_url
    if not api_url:
        from vamscli.utils.profile import ProfileManager

        api_url = (ProfileManager(args.profile).load_config() or {}).get("api_gateway_url")
    if not api_url:
        sys.stderr.write("VAMS CLI profile has no API URL; run vamscli setup first\n")
        return 2

    lock = threading.Lock()

    def log(**fields):
        if not args.log:
            return
        fields["t"] = round(time.time(), 3)
        with lock, open(args.log, "a") as handle:
            handle.write(json.dumps(fields) + "\n")

    tree = VamsTree(api_url, log=log)
    interceptors = [CallLog(log)] if args.log else []
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=16), interceptors=interceptors)
    services = [s for version in VERSIONS for s in _register(server, tree, version)]
    # Folder subscriptions are long-lived streams, one per open folder, each holding a worker thread: they get a
    # server and pool of their own, so they can never take the threads that serve listings and reads. Past the
    # cap a new subscription is refused (the folder still lists; it only logs that it cannot watch).
    events = grpc.server(futures.ThreadPoolExecutor(max_workers=MAX_FOLDER_SUBSCRIPTIONS),
                         interceptors=interceptors, maximum_concurrent_rpcs=MAX_FOLDER_SUBSCRIPTIONS)
    event_services = [_register_events(events, tree)]
    event_port = events.add_insecure_port("127.0.0.1:0")
    events.start()
    grpc_port = server.add_insecure_port(f"127.0.0.1:{args.grpc_port}")
    server.start()
    document = json.dumps(discovery_document(grpc_port, services, event_port, event_services)).encode()

    class Discovery(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            body = document if path in ("/discovery/api/v2/services", "/discovery/api/v2beta/services") else b""
            self.send_response(200 if body else 404)
            if body:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", args.http_port), Discovery)
    if args.watch_stdin:
        exit_when_parent_goes(sys.stdin.buffer, lambda: threading.Thread(target=http.shutdown, daemon=True).start())
    sys.stdout.write(json.dumps({"discovery": f"http://127.0.0.1:{http.server_address[1]}/discovery",
                                 "grpc": f"http://127.0.0.1:{grpc_port}", "root": ROOT}) + "\n")
    sys.stdout.flush()
    try:
        http.serve_forever()
    finally:
        server.stop(0)
        events.stop(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
