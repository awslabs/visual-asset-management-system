# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""POST /search/nlp: natural-language search over the vector embeddings table.

The query is embedded with the index's model and matched with ``SearchVectors`` against the latest,
non-archived items of every database the caller may read: one unpartitioned call when the caller sees
every live database and named none, otherwise one call per target (at most MAX_TARGETS, on
SEARCH_WORKERS threads). Each target gets a *breadth* call over whole-file items and, with
``includeSegments``, a *depth* call over every item; only a full breadth window marks the answer
truncated because breadth windows hold one item per file. Candidates are merged by distance, collapsed
to one hit per (database, asset, path, version) with the file's matching segments counted and the best
one named, ordered so the file types the query mentions come first, joined to their asset rows and
authorized per hit through Casbin. When an OpenSearch mode is enabled the OpenSearch-only constraints
are applied and ``_source`` enriched through the ``/search`` code, imported lazily. The answer is the
OpenSearch-style envelope the web table, CLI formatters and MCP helpers read; ``SearchVectors`` has no
cursor, so there is no pagination and ``size`` is the whole window.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import boto3
from aws_lambda_powertools.utilities.parser import ValidationError, parse
from aws_lambda_powertools.utilities.typing import LambdaContext
from botocore.config import Config

from common.apiRoutes import API_SEARCH_NLP
from common.constants import STANDARD_JSON_RESPONSE
from common.databaseAccess import DatabaseAccessManager
from common.resourceNames import ResourceKeys, get_table_name
from common.vectorsearch import fileClassIntent
from common.vectorsearch.embeddings import EmbeddingModelError, embed_text
from common.vectorsearch.vectorStore import MAX_TOP_K, DynamoDbVectorStore, VectorHit, VectorIndexNotReady
from customLogging.logger import safeLogger
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from models.common import (
    APIGatewayProxyResponseV2,
    VAMSGeneralErrorResponse,
    authorization_error,
    general_error,
    internal_error,
    success,
    validation_error,
    validation_error_message,
)
from models.vectorsearch import VECTOR_INDEX_LABEL, NlpSearchRequestModel

logger = safeLogger(service_name="VectorSearchService")
retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb_client = boto3.client("dynamodb", config=retry_config)
dynamodb = boto3.resource("dynamodb", config=retry_config)

try:
    vector_table_name = get_table_name(ResourceKeys.VECTOR_EMBEDDINGS_STORAGE_TABLE)
    asset_storage_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    vector_index_name = os.environ["VECTOR_INDEX_NAME"]
    embedding_model_id = os.environ["EMBEDDING_MODEL_ID"]
    embedding_dimensions = int(os.environ["EMBEDDING_DIMENSIONS"])
except Exception as e:
    logger.exception("Failed loading environment variables or resolving resource names")
    raise e
opensearch_disabled = os.environ.get("OPENSEARCH_DISABLED", "true") == "true"

SEARCH_WORKERS = 16
MAX_TARGETS = 200
BATCH_GET_MAX_KEYS = 100
# Document ids per OpenSearch query of the constrain/enrich step; each query is sized to its own window.
OPENSEARCH_ENRICH_WINDOW = 100
DELETED_PARTITION_SUFFIX = "#deleted"
INDEX_BUILDING_MESSAGE = "Vector index is being built"
ROUTE_NOT_SERVED_MESSAGE = "Route is not served by this function"
INVALID_BODY_MESSAGE = "Request body is not valid JSON"
ENRICHMENT_FAILED_MESSAGE = "OpenSearch enrichment failed; hits are returned without it"
_SEGMENT_FIELDS = ("segmentKey", "segmentKind", "segmentLabel", "segmentStartMs", "segmentEndMs")
_WHOLE_FILE_SEGMENT_KIND = "none"


def _store() -> DynamoDbVectorStore:
    return DynamoDbVectorStore(vector_table_name, vector_index_name, embedding_model_id, embedding_dimensions, dynamodb_client)


def _warning(code: str, message: str) -> Dict[str, str]:
    return {"code": code, "message": message}


@dataclass
class SearchPlan:
    """Which ``SearchVectors`` calls answer a request."""

    targets: List[Optional[str]]  # None is the one unpartitioned call
    targets_cut: bool
    databases_searched: int
    top_k: int


def _plan(request: NlpSearchRequestModel, accessible: List[str], live_count: int) -> SearchPlan:
    """One unpartitioned call when the caller sees every live database and named none; else one call per
    accessible target, the named ones first when the request names databases."""
    accessible_set = set(accessible)
    if request.databaseIds:
        targets: List[Optional[str]] = list(dict.fromkeys(d for d in request.databaseIds if d in accessible_set))
    elif accessible and len(accessible) == live_count:
        targets = [None]
    else:
        targets = list(accessible)
    cut = len(targets) > MAX_TARGETS
    targets = targets[:MAX_TARGETS]
    searched = live_count if targets == [None] else len(targets)
    return SearchPlan(targets, cut, searched, min(MAX_TOP_K, request.size * 2))


def _filters_for(request: NlpSearchRequestModel, database_id: Optional[str], segment_kind: Optional[str]) -> Dict[str, str]:
    """Equality filters in the index's attribute order; every key is one of INLINE_FILTER_ATTRIBUTES."""
    filters: Dict[str, str] = {}
    if database_id is not None:
        filters["databaseId"] = database_id
    filters["isLatest"] = "true"
    if not request.includeArchived:
        filters["isArchived"] = "false"
    if len(request.fileClasses) == 1:
        filters["fileClass"] = request.fileClasses[0]
    if len(request.fileExtensions) == 1:
        filters["fileExt"] = request.fileExtensions[0]
    filters["embeddingModelId"] = embedding_model_id
    if segment_kind is not None:
        filters["segmentKind"] = segment_kind
    return filters


def _run_searches(store, vector: List[float], request: NlpSearchRequestModel, plan: SearchPlan) -> Tuple[List[VectorHit], bool, bool]:
    """Every planned call; returns the hits and whether any breadth / any depth window came back full."""
    jobs: List[Tuple[str, Dict[str, str]]] = []
    for target in plan.targets:
        jobs.append(("breadth", _filters_for(request, target, _WHOLE_FILE_SEGMENT_KIND)))
        if request.includeSegments:
            jobs.append(("depth", _filters_for(request, target, None)))

    def run(job):
        kind, filters = job
        hits = store.search(vector, top_k=plan.top_k, filters=filters)
        return kind, hits, len(hits) >= plan.top_k

    with ThreadPoolExecutor(max_workers=min(SEARCH_WORKERS, len(jobs))) as pool:
        results = list(pool.map(run, jobs))
    breadth_full = any(full for kind, _, full in results if kind == "breadth")
    depth_full = any(full for kind, _, full in results if kind == "depth")
    return [hit for _, hits, _ in results for hit in hits], breadth_full, depth_full


def _item_key(item: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    return (item["databaseId"], item["assetId"], item["filePath"], item.get("versionId", "null"), item.get("segmentKey", ""))


def _merge(hits: List[VectorHit]) -> List[VectorHit]:
    """Distance-ascending, one entry per item."""
    seen = set()
    merged = []
    for hit in sorted(hits, key=lambda h: h.distance):
        key = _item_key(hit.item)
        if key not in seen:
            seen.add(key)
            merged.append(hit)
    return merged


def _post_filter(hits: List[VectorHit], request: NlpSearchRequestModel) -> List[VectorHit]:
    """The multi-valued class/extension constraints the equality-only index cannot push down."""
    if len(request.fileClasses) > 1:
        hits = [h for h in hits if h.item.get("fileClass") in request.fileClasses]
    if len(request.fileExtensions) > 1:
        hits = [h for h in hits if h.item.get("fileExt") in request.fileExtensions]
    return hits


def _segment_ref(item: Dict[str, Any]) -> Dict[str, Any]:
    return {name: item.get(name) for name in _SEGMENT_FIELDS}


def _collapse_hits(hits: List[VectorHit]) -> List[Dict[str, Any]]:
    """One entry per (database, asset, path, version): the best item, how many of the file's segment
    items were candidates, and the winning segment when a segment won. ``hits`` is distance-ascending."""
    groups: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for hit in hits:
        item = hit.item
        is_segment = item.get("segmentKind", _WHOLE_FILE_SEGMENT_KIND) != _WHOLE_FILE_SEGMENT_KIND
        key = (item["databaseId"], item["assetId"], item["filePath"], item.get("versionId", "null"))
        group = groups.get(key)
        if group is None:
            group = {"item": item, "distance": hit.distance, "segmentHits": 0,
                     "bestSegment": _segment_ref(item) if is_segment else None}
            groups[key] = group
        if is_segment:
            group["segmentHits"] += 1
    return list(groups.values())


def _partition_by_intent(hits: List[Dict[str, Any]], classes: List[str]) -> List[Dict[str, Any]]:
    """Stable: hits of the named classes first, everything else after, order preserved within each part."""
    if not classes:
        return hits
    wanted = set(classes)
    return [h for h in hits if h["item"].get("fileClass") in wanted] + [h for h in hits if h["item"].get("fileClass") not in wanted]


def _batch_get(keys: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for start in range(0, len(keys), BATCH_GET_MAX_KEYS):
        request = {asset_storage_table_name: {"Keys": keys[start:start + BATCH_GET_MAX_KEYS]}}
        while request:
            response = dynamodb.batch_get_item(RequestItems=request)
            rows.extend(response.get("Responses", {}).get(asset_storage_table_name, []))
            request = response.get("UnprocessedKeys") or {}
    return rows


def _load_asset_rows(collapsed: List[Dict[str, Any]], include_archived: bool) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Asset rows by (databaseId, assetId); a row missing from the live partition is looked up under
    ``{db}#deleted`` only when archived items were asked for."""
    wanted = list(dict.fromkeys((h["item"]["databaseId"], h["item"]["assetId"]) for h in collapsed))
    rows: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if not wanted:
        return rows
    for row in _batch_get([{"databaseId": db, "assetId": a} for db, a in wanted]):
        rows[(row["databaseId"], row["assetId"])] = row
    if include_archived:
        missing = [k for k in wanted if k not in rows]
        if missing:
            for row in _batch_get([{"databaseId": f"{db}{DELETED_PARTITION_SUFFIX}", "assetId": a} for db, a in missing]):
                rows[(row["databaseId"][: -len(DELETED_PARTITION_SUFFIX)], row["assetId"])] = row
    return rows


def _authorize(collapsed: List[Dict[str, Any]], rows: Dict[Tuple[str, str], Dict[str, Any]], claims_and_roles) -> List[Dict[str, Any]]:
    """Casbin per hit on the asset object; a hit is kept only when enforce() passes, so an empty token
    list yields an empty answer."""
    if len(claims_and_roles.get("tokens", [])) == 0:
        return []
    enforcer = CasbinEnforcer(claims_and_roles)
    allowed = []
    for hit in collapsed:
        item = hit["item"]
        row = rows.get((item["databaseId"], item["assetId"]))
        if row is None:
            continue
        asset_object = {
            "databaseId": item["databaseId"],
            "assetName": row.get("assetName", ""),
            "tags": row.get("tags", []),
            "assetType": row.get("assetType", ""),
            "object__type": "asset",
        }
        if enforcer.enforce(asset_object, "GET"):
            hit["asset"] = row
            allowed.append(hit)
    return allowed


def _score(distance: float) -> float:
    return max(0.0, min(1.0, 1.0 - distance))


def _file_hit(hit: Dict[str, Any]) -> Dict[str, Any]:
    item, row, distance = hit["item"], hit["asset"], float(hit["distance"])
    version = item.get("versionId", "null")
    ext = item.get("fileExt", "none")
    source = {
        "str_rectype": "file",
        "str_databaseid": item["databaseId"],
        "str_assetid": item["assetId"],
        "str_key": item["filePath"],
        "str_fileext": "" if ext == "none" else ext,
        "str_assetname": row.get("assetName", ""),
        "str_assettype": row.get("assetType", ""),
        "list_tags": row.get("tags", []),
        "bool_archived": item.get("isArchived") == "true",
        "str_s3_version_id": "" if version == "null" else version,
        "num_filesize": int(item.get("fileSize") or 0),
    }
    if item.get("previewFileKey"):
        source["str_previewfilekey"] = item["previewFileKey"]
    return {
        "_index": VECTOR_INDEX_LABEL,
        "_id": f"{item['databaseId']}#{item['assetId']}#{item['filePath']}#{version}",
        "_score": _score(distance),
        "_index_type": "file",
        "_source": source,
        "_vector": {
            "distance": distance,
            "embeddingModelId": item.get("embeddingModelId", ""),
            "sourceModalities": item.get("sourceModalities", []),
            "indexedAt": item.get("indexedAt", ""),
            "fileClass": item.get("fileClass", ""),
            "segmentHits": hit["segmentHits"],
            "bestSegment": hit["bestSegment"],
        },
        "asset": row,
    }


def _asset_hits(file_hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Best file hit per asset, presented as an asset record."""
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for hit in file_hits:
        src, row = hit["_source"], hit["asset"]
        key = (src["str_databaseid"], src["str_assetid"])
        if key in out:
            continue
        out[key] = {
            "_index": VECTOR_INDEX_LABEL,
            "_id": f"{key[0]}#{key[1]}",
            "_score": hit["_score"],
            "_index_type": "asset",
            "_source": {
                "str_rectype": "asset",
                "str_databaseid": key[0],
                "str_assetid": key[1],
                "str_assetname": row.get("assetName", ""),
                "str_assettype": row.get("assetType", ""),
                "list_tags": row.get("tags", []),
                "bool_archived": src["bool_archived"],
            },
            "_vector": hit["_vector"],
            "asset": row,
        }
    return list(out.values())


def _strip(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{k: v for k, v in hit.items() if k != "asset"} for hit in hits]


def _envelope(hits, total, request, databases_searched, candidates, collapsed, class_intent, truncated, warnings, started) -> Dict[str, Any]:
    hits = _strip(hits)
    return {
        "took": int((time.monotonic() - started) * 1000),
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
        "hits": {
            "total": {"value": total, "relation": "gte" if truncated else "eq"},
            "max_score": hits[0]["_score"] if hits else None,
            "hits": hits,
        },
        "aggregations": {},
        "aggregationTotal": total,
        "nlp": {
            "query": request.query,
            "embeddingModelId": embedding_model_id,
            "databasesSearched": databases_searched,
            "candidatesEvaluated": candidates,
            "itemsCollapsed": collapsed,
            "classIntent": class_intent,
            "truncated": truncated,
        },
        "warnings": warnings,
    }


def _opensearch_constraints(request: NlpSearchRequestModel, search_module, databases: List[str]) -> List[Dict[str, Any]]:
    """The OpenSearch-only constraints as ``bool.filter`` clauses, built by the /search query builder from
    a keyword request that carries them and no text query; ``tags`` become a should-of-terms clause."""
    from models.search import SearchRequestModel

    keyword_request = SearchRequestModel(
        query=None,
        filters=request.filters or [],
        metadataQuery=request.metadataQuery,
        metadataSearchMode=request.metadataSearchMode or "both",
        geoSearch=request.geoSearch,
        includeArchived=request.includeArchived,
        entityTypes=["file"],
    )
    builder = search_module.DualIndexQueryBuilder(DatabaseAccessManager())
    clauses = [builder._build_query_clause(keyword_request, databases, "file")]
    if request.tags:
        clauses.append({"bool": {"should": [{"term": {"list_tags.keyword": tag}} for tag in request.tags], "minimum_should_match": 1}})
    return clauses


def _opensearch_step(request: NlpSearchRequestModel, file_hits: List[Dict[str, Any]], warnings: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Constrain by the OpenSearch-only fields and merge ``_source`` from the file index. Imports the
    /search module here, once OPENSEARCH_DISABLED has been ruled out, so an OpenSearch-less deployment
    never loads opensearch-py. The hits go to OpenSearch in windows of OPENSEARCH_ENRICH_WINDOW ids, each
    query sized to its own window, so every hit is answered: an ``ids`` filter scores every document the
    same, and one query over more ids than its size would keep an arbitrary subset. Any failure degrades
    to a warning and the hits stay as they were."""
    if not file_hits:
        return file_hits
    try:
        from handlers.osSemanticSearch import search as opensearch_search

        from common.indexing.documentIds import build_file_document_id

        manager = opensearch_search.DualIndexSearchManager()
        ids = [build_file_document_id(h["_source"]["str_databaseid"], h["_source"]["str_assetid"], h["_source"]["str_key"]) for h in file_hits]
        constrained = bool(request.opensearch_only_fields())
        shared_clauses: List[Dict[str, Any]] = []
        if constrained:
            databases = list(dict.fromkeys(h["_source"]["str_databaseid"] for h in file_hits))
            shared_clauses.extend(_opensearch_constraints(request, opensearch_search, databases))
        if not request.includeArchived:
            shared_clauses.append({"term": {"bool_archived": False}})
        kept = []
        for start in range(0, len(file_hits), OPENSEARCH_ENRICH_WINDOW):
            window_ids = ids[start:start + OPENSEARCH_ENRICH_WINDOW]
            window_hits = file_hits[start:start + OPENSEARCH_ENRICH_WINDOW]
            clauses = [{"ids": {"values": window_ids}}] + shared_clauses
            response = manager.client.search(
                index=manager.file_index, body={"size": len(window_ids), "query": {"bool": {"filter": clauses}}}
            )
            by_id = {hit["_id"]: hit.get("_source", {}) for hit in response.get("hits", {}).get("hits", [])}
            for doc_id, hit in zip(window_ids, window_hits):
                source = by_id.get(doc_id)
                if source is None:
                    if not constrained:
                        kept.append(hit)
                    continue
                merged = dict(source)
                merged.update(hit["_source"])
                hit["_source"] = merged
                kept.append(hit)
        return kept
    except Exception:
        logger.exception("OpenSearch enrichment failed")
        warnings.append(_warning("opensearch:enrichment_failed", ENRICHMENT_FAILED_MESSAGE))
        return file_hits


def _parse_request(event) -> NlpSearchRequestModel:
    try:
        body = json.loads(event.get("body") or "{}")
    except (TypeError, ValueError):
        raise VAMSGeneralErrorResponse(INVALID_BODY_MESSAGE)
    return parse(body, model=NlpSearchRequestModel)


def handle_nlp_search(event, claims_and_roles) -> APIGatewayProxyResponseV2:
    started = time.monotonic()
    request = _parse_request(event)
    warnings: List[Dict[str, str]] = []
    ignored = request.opensearch_only_fields()
    if opensearch_disabled and ignored:
        warnings.append(_warning("opensearch:fields_ignored", f"OpenSearch is not enabled; ignored: {', '.join(ignored)}"))
    # includeArchived controls archived assets and files, not deleted databases: the scan is always over
    # the live database rows (show_deleted=True would return ONLY the `#deleted` rows), as /search does.
    # Archived items of a live database are reached through the isArchived filter and the `#deleted`
    # asset partition below.
    accessible, live_count = DatabaseAccessManager().get_accessible_databases_with_count(
        claims_and_roles, show_deleted=False
    )
    plan = _plan(request, accessible, live_count)
    class_intent = fileClassIntent.detect(request.query) if not request.fileClasses else []
    if not plan.targets:
        warnings.append(_warning("databases:none_accessible", "No searchable database is accessible to the caller"))
        return success(body=_envelope([], 0, request, 0, 0, 0, class_intent, False, warnings, started))
    vector = embed_text(request.query, model_id=embedding_model_id, dimensions=embedding_dimensions, purpose="query")
    raw, breadth_full, depth_full = _run_searches(_store(), vector, request, plan)
    merged = _post_filter(_merge(raw), request)
    collapsed = _partition_by_intent(_collapse_hits(merged), class_intent)
    truncated = breadth_full or plan.targets_cut
    if breadth_full:
        warnings.append(_warning("truncated:window", f"A result window of {plan.top_k} filled; more files may match"))
    if plan.targets_cut:
        warnings.append(_warning("truncated:targets", f"Only the first {MAX_TARGETS} accessible databases were searched"))
    if depth_full and not breadth_full:
        warnings.append(_warning("segments:window_full", f"A segment window of {plan.top_k} filled; more segments may match"))
    authorized = _authorize(collapsed, _load_asset_rows(collapsed, request.includeArchived), claims_and_roles)
    file_hits = [_file_hit(h) for h in authorized]
    if not opensearch_disabled and (request.enrich or ignored):
        file_hits = _opensearch_step(request, file_hits, warnings)
    result = _asset_hits(file_hits) if request.entityTypes == ["asset"] else file_hits
    return success(
        body=_envelope(
            result[: request.size], len(result), request, plan.databases_searched, len(merged),
            len(merged) - len(collapsed), class_intent, truncated, warnings, started,
        )
    )


def _service_unavailable(message: str) -> APIGatewayProxyResponseV2:
    response = dict(STANDARD_JSON_RESPONSE)
    response["statusCode"] = 503
    response["body"] = json.dumps({"message": message})
    return response


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    claims_and_roles = request_to_claims(event)
    try:
        path = event["requestContext"]["http"]["path"]
        method = event["requestContext"]["http"]["method"]
        method_allowed_on_api = False
        if len(claims_and_roles.get("tokens", [])) > 0:
            if CasbinEnforcer(claims_and_roles).enforceAPI(event):
                method_allowed_on_api = True
        if not method_allowed_on_api:
            return authorization_error()
        if method == "POST" and API_SEARCH_NLP.matches(path):
            return handle_nlp_search(event, claims_and_roles)
        return validation_error(body={"message": ROUTE_NOT_SERVED_MESSAGE}, event=event)
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={"message": validation_error_message(v)}, event=event)
    except VectorIndexNotReady:
        logger.warning("SearchVectors refused: the vector index is not ready")
        return _service_unavailable(INDEX_BUILDING_MESSAGE)
    except EmbeddingModelError as e:
        logger.exception(f"Query embedding failed: {e.code}")
        return general_error(body={"message": f"Query embedding failed: {e.code}"}, event=event)
    except VAMSGeneralErrorResponse as v:
        return general_error(body={"message": str(v)}, event=event)
    except Exception:
        logger.exception("Internal error")
        return internal_error(event=event)
