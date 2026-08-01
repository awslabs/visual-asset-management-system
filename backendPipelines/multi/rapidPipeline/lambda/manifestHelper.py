#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Shared manifest-reading helper for VAMS use-case pipelines.

VAMS pipelines are standalone Lambda code assets that cannot import the backend package, so
this module is vendored into each pipeline's ``lambda/`` directory (like ``customLogging``).
Keep it dependency-light (boto3 only) so the same file can also be copied into a container.

The workflow ASL delivers a per-pipeline manifest envelope (written to the asset bucket and
pointed at by ``inputManifestS3Location`` in the Step Functions payload). The envelope groups
everything static a pipeline needs:

    {
      "schemaVersion": 1,
      "inputFiles": [ { "relativePath", "databaseId", "assetId",
                        "assetRootS3Key", "auxPreviewPrefix", "bucket", "key", "versionId" } ],
      "inputMetadataS3Location": "s3://.../metadata.json",
      "outputs": { "bucket", "files", "previews", "metadata", "results" },  # bucket + relative keys
      "auxBucket": "aux-bucket-name",
      "auxTempPrefix": "pipelines/{pipelineName}/{executionId}/",
      "auxPreviewPipelineSuffix": "",
      "systemConfig": { "orchestrationBusArn", "orchestrationEventPrefix" }
    }

Locations are carried as relative keys plus their bucket (never a pre-built ``s3://`` URI): the
``outputs`` block pairs a single ``bucket`` with bucket-relative prefixes, ``auxBucket`` is the
auxiliary bucket NAME, ``auxTempPrefix`` is bucket-relative, and each input file carries its own
``assetRootS3Key`` (bucket-relative asset root) and ``auxPreviewPrefix`` (bucket-relative, unique
per file). ``resolve_inputs`` RECONSTRUCTS the ``s3://`` forms into the same flat field names the
pipelines already forward (``inputS3AssetFilePath``, ``outputS3AssetFilesPath``, ...), preferring
the manifest and falling back to the legacy payload fields when no manifest is present. This keeps
the change non-breaking: a payload without a manifest resolves exactly to today's behavior.
"""

import json

# Output S3 paths must keep a trailing slash so containers can append filenames.
_OUTPUT_KEYS = ("files", "previews", "metadata", "results")


def _join_s3(bucket, key):
    """Reconstruct an ``s3://bucket/key`` URI from a bucket name + bucket-relative key. Returns
    ``""`` when the bucket is empty; a missing key yields the bucket root (``s3://bucket/``)."""
    if not bucket:
        return ""
    return f"s3://{bucket}/{key or ''}"


def parse_s3_uri(uri):
    """Split an ``s3://bucket/key`` URI into ``(bucket, key)``. Returns ``("", "")`` for an
    empty or non-s3 value."""
    if not uri or not uri.startswith("s3://"):
        return "", ""
    without_scheme = uri[len("s3://"):]
    if "/" in without_scheme:
        bucket, key = without_scheme.split("/", 1)
    else:
        bucket, key = without_scheme, ""
    return bucket, key


def manifest_location(data):
    """The manifest S3 location from the Step Functions payload body, or ``""``."""
    return (data or {}).get("inputManifestS3Location", "") or ""


def fetch_manifest(s3_client, manifest_s3_location):
    """Fetch + parse the manifest JSON from S3. Best-effort: returns ``None`` on a missing
    location or any S3/parse failure so the caller falls back to the legacy payload fields."""
    if not manifest_s3_location:
        return None
    bucket, key = parse_s3_uri(manifest_s3_location)
    if not bucket or not key:
        return None
    try:
        resp = s3_client.get_object(Bucket=bucket, Key=key)
        return json.loads(resp["Body"].read().decode("utf-8"))
    except Exception:  # nosec B110 - best-effort; legacy fields are the fallback
        return None


def fetch_metadata(s3_client, input_metadata_s3_location):
    """Fetch + parse the shared input-metadata file from S3 and unwrap the Stage-3 metadata
    envelope, returning the metadata payload dict.

    The metadata file is written as ``{"schemaVersion": N, "metadata": {...}}``; this returns
    the inner ``metadata`` object. An un-enveloped (legacy) file is returned as-is. Best-effort:
    returns ``{}`` on a missing location or any S3/parse failure.

    This is the function any consumer DOWNSTREAM of the vamsExecute lambda (a container or a
    later lambda) must call to obtain metadata: metadata content is never forwarded past the
    vamsExecute lambda — only its S3 location travels — so reading the file from S3 here removes
    the inline-payload size limit (AWS Batch / ECS command overrides, Step Functions payloads)."""
    if not input_metadata_s3_location:
        return {}
    bucket, key = parse_s3_uri(input_metadata_s3_location)
    if not bucket or not key:
        return {}
    try:
        resp = s3_client.get_object(Bucket=bucket, Key=key)
        body = json.loads(resp["Body"].read().decode("utf-8"))
    except Exception:  # nosec B110 - best-effort; an unreadable metadata file yields {}
        return {}
    if isinstance(body, dict) and "metadata" in body and "schemaVersion" in body:
        return body.get("metadata") or {}
    return body if isinstance(body, dict) else {}


# Schema version of the grouped-by-asset (v2) input-metadata envelope. A v1 body is the legacy
# {"schemaVersion": 1, "metadata": {...}} wrapper that fetch_metadata unwraps to the inner object.
METADATA_SCHEMA_VERSION_GROUPED = 2


def _normalize_file_key(file_key):
    """Asset-relative file keys are stored with a single leading slash ('/folder/a.glb'). Accepts
    either form so callers can pass a manifest relativePath verbatim."""
    if not file_key:
        return "/"
    return "/" + str(file_key).lstrip("/")


def get_asset_file_record(envelope, database_id, asset_id, file_key):
    """Return the ``{fileKey, metadata, attributes?}`` record for a (databaseId, assetId, fileKey)
    from a v2 grouped envelope, or ``None`` when absent. The file key is normalized before
    comparison. Asset-level metadata is the fileKey '/' record."""
    fk = _normalize_file_key(file_key)
    for asset in (envelope or {}).get("assets", []) or []:
        if asset.get("databaseId") == database_id and asset.get("assetId") == asset_id:
            for file_record in asset.get("files", []) or []:
                if file_record.get("fileKey") == fk:
                    return file_record
    return None


def _legacy_scope(metadata_body, scope):
    """Read one scope out of a legacy ``{"VAMS": {...}}`` body."""
    if not isinstance(metadata_body, dict):
        return {}
    return ((metadata_body.get("VAMS") or {}).get(scope)) or {}


def asset_metadata_for(metadata_body, database_id, asset_id):
    """Asset-level metadata for an asset. Reads the v2 fileKey '/' record, falling back to the
    legacy ``VAMS.assetMetadata`` scope for a v1 body."""
    if isinstance(metadata_body, dict) and "assets" in metadata_body:
        record = get_asset_file_record(metadata_body, database_id, asset_id, "/") or {}
        return record.get("metadata") or {}
    return _legacy_scope(metadata_body, "assetMetadata")


def file_metadata_for(metadata_body, database_id, asset_id, file_key):
    """Per-file metadata for a file, falling back to the legacy ``VAMS.fileMetadata`` scope."""
    if isinstance(metadata_body, dict) and "assets" in metadata_body:
        record = get_asset_file_record(metadata_body, database_id, asset_id, file_key) or {}
        return record.get("metadata") or {}
    return _legacy_scope(metadata_body, "fileMetadata")


def file_attributes_for(metadata_body, database_id, asset_id, file_key):
    """Per-file attributes for a file, falling back to the legacy ``VAMS.fileAttributes`` scope."""
    if isinstance(metadata_body, dict) and "assets" in metadata_body:
        record = get_asset_file_record(metadata_body, database_id, asset_id, file_key) or {}
        return record.get("attributes") or {}
    return _legacy_scope(metadata_body, "fileAttributes")


def to_legacy_vams_view(metadata_body, database_id="", asset_id="", file_key=""):
    """Project a metadata payload onto the legacy ``{"VAMS": {assetData, assetMetadata,
    fileMetadata, fileAttributes}}`` view existing pipeline readers dig into.

    - A v2 grouped body is projected for the given (databaseId, assetId, fileKey): assetData +
      assetMetadata come from the asset's '/' record, fileMetadata/fileAttributes from the fileKey
      record. A fileKey of '/' (whole-asset selection) resolves to the asset-level record only,
      leaving the file scopes empty — mirroring the writer, which emits no per-file record for a
      whole-asset selection.
    - A body already in the ``{"VAMS": {...}}`` shape (or any non-grouped dict) passes through
      unchanged; ``{}`` when it is not a usable dict.

    Mirrors backend ``executionRecords.to_legacy_vams_view`` so the backend render path and the
    pipeline read path resolve metadata identically."""
    body = metadata_body or {}
    if not isinstance(body, dict):
        return {}
    if body.get("schemaVersion") == METADATA_SCHEMA_VERSION_GROUPED and "assets" in body:
        asset_group = {}
        for asset in body.get("assets", []) or []:
            if asset.get("databaseId") == database_id and asset.get("assetId") == asset_id:
                asset_group = asset
                break
        asset_record = get_asset_file_record(body, database_id, asset_id, "/") or {}
        is_asset_level = _normalize_file_key(file_key) == "/"
        file_record = {} if is_asset_level else (
            get_asset_file_record(body, database_id, asset_id, file_key) or {})
        return {"VAMS": {
            "assetData": asset_group.get("assetData") or {},
            "assetMetadata": asset_record.get("metadata") or {},
            "fileMetadata": file_record.get("metadata") or {},
            "fileAttributes": file_record.get("attributes") or {},
        }}
    return body


def resolved_file_key(resolved):
    """The per-file metadata key for a resolved manifest: the first input file's relative path,
    or '/' (asset level) when the selection carries no input files."""
    input_files = (resolved or {}).get("inputFiles") or []
    if not input_files:
        return "/"
    return _normalize_file_key(input_files[0].get("relativePath"))


def fetch_input_configuration(s3_client, input_configuration_s3_location):
    """Fetch + parse the per-pipeline input configuration file (the user-defined
    ``inputParameters``) from S3, returning the parsed object. The configuration file is the
    raw user JSON (it carries no VAMS schema envelope), so it is returned as parsed. Best-effort:
    returns ``{}`` on a missing location or any S3/parse failure.

    Like metadata, the configuration content is never forwarded past the vamsExecute lambda —
    only its S3 location travels — so a consumer reads the file from S3 here, removing the
    inline-payload size limit."""
    if not input_configuration_s3_location:
        return {}
    bucket, key = parse_s3_uri(input_configuration_s3_location)
    if not bucket or not key:
        return {}
    try:
        resp = s3_client.get_object(Bucket=bucket, Key=key)
        body = resp["Body"].read().decode("utf-8")
        return json.loads(body) if body else {}
    except Exception:  # nosec B110 - best-effort; an unreadable config file yields {}
        return {}


def pipeline_execution_id_from_event_prefix(orchestration_event_prefix):
    """The pipeline execution id encoded in the orchestration event prefix
    (``<source>.execution.<executionId>.pipeline.<pipelineExecutionId>``), or ``""`` when the
    prefix is empty/unrecognized. Used when registering sub-process ARNs so the registration
    lambda can route the event to the correct PipelineExecutions row."""
    if not orchestration_event_prefix:
        return ""
    marker = ".pipeline."
    idx = orchestration_event_prefix.rfind(marker)
    if idx < 0:
        return ""
    return orchestration_event_prefix[idx + len(marker):]


def _first_input_file_uri(manifest):
    """``s3://bucket/key`` of the manifest's first resolved input file, or ``""``."""
    input_files = (manifest or {}).get("inputFiles") or []
    if not input_files:
        return ""
    first = input_files[0]
    bucket, key = first.get("bucket", ""), first.get("key", "")
    return f"s3://{bucket}/{key}" if bucket and key else ""


def resolve_inputs(data, manifest=None):
    """Normalize the pipeline's resolved inputs from the manifest (preferred) or the legacy
    payload fields (fallback). ``data`` is the Step Functions payload body; ``manifest`` is the
    fetched envelope (or ``None``). Returns a flat dict using the legacy field names every
    pipeline already forwards, plus the resolved input-file list and orchestration config.

    Manifest values override legacy values only when present, so a partial manifest still
    degrades gracefully to the legacy payload."""
    data = data or {}

    # Legacy payload values (the pre-manifest contract).
    resolved = {
        "inputS3AssetFilePath": data.get("inputS3AssetFilePath", ""),
        "outputS3AssetFilesPath": data.get("outputS3AssetFilesPath", ""),
        "outputS3AssetPreviewPath": data.get("outputS3AssetPreviewPath", ""),
        "outputS3AssetMetadataPath": data.get("outputS3AssetMetadataPath", ""),
        "inputOutputS3AssetAuxiliaryFilesPath": data.get("inputOutputS3AssetAuxiliaryFilesPath", ""),
        "inputMetadataS3Location": data.get("inputMetadataS3Location", ""),
        # The per-pipeline input configuration (the user-defined inputParameters) is delivered
        # as a config file; its S3 location rides in the SFN body, not the manifest envelope.
        "inputConfigurationS3Location": data.get("inputConfigurationS3Location", ""),
        # assetId / databaseId are sourced only from the manifest's first input file (below);
        # they no longer travel in the SFN body, so there is no legacy payload fallback.
        "assetId": "",
        "databaseId": "",
        "inputFiles": [],
        # Reconstructed s3:// aux preview location for the first input file (auxBucket +
        # per-file auxPreviewPrefix + the per-pipeline auxPreviewPipelineSuffix). No legacy
        # fallback: aux preview locations are manifest-only.
        "auxPreviewS3Path": "",
        # The per-pipeline viewer subfolder from the manifest (empty until sourced from the
        # pipeline configuration). Exposed so a viewer pipeline can detect the empty case and
        # apply its own hardcoded fallback subfolder so it does not break in the interim.
        "auxPreviewPipelineSuffix": "",
        "orchestrationBusArn": "",
        "orchestrationEventPrefix": "",
        "manifestUsed": False,
    }

    # A results-only pipeline (inputFileArity "none") has no input files, so the asset identity
    # falls back to the execution's output target, which the Step Functions body always carries.
    # Applied before the no-manifest return so both paths get it; a manifest input file below
    # still wins when one is present.
    if not resolved["assetId"] and data.get("outputAssetId"):
        resolved["assetId"] = data["outputAssetId"]
    if not resolved["databaseId"] and data.get("outputDatabaseId"):
        resolved["databaseId"] = data["outputDatabaseId"]

    if not manifest:
        return resolved

    resolved["manifestUsed"] = True
    resolved["inputFiles"] = manifest.get("inputFiles") or []

    # The input path and the asset/database identity both come from the manifest's first
    # resolved input file, and only when that entry yields a usable s3://bucket/key. Keeping
    # them coupled means a malformed first entry leaves BOTH on the legacy values rather than
    # pairing a legacy input key with a manifest assetId (which the container could not slice).
    first_uri = _first_input_file_uri(manifest)
    if first_uri:
        resolved["inputS3AssetFilePath"] = first_uri
        first_file = resolved["inputFiles"][0]
        if first_file.get("assetId"):
            resolved["assetId"] = first_file["assetId"]
        if first_file.get("databaseId"):
            resolved["databaseId"] = first_file["databaseId"]

    # Outputs are reconstructed from the single output bucket + each bucket-relative prefix.
    outputs = manifest.get("outputs") or {}
    output_bucket = outputs.get("bucket", "")
    if outputs.get("files"):
        resolved["outputS3AssetFilesPath"] = _join_s3(output_bucket, outputs["files"])
    if outputs.get("previews"):
        resolved["outputS3AssetPreviewPath"] = _join_s3(output_bucket, outputs["previews"])
    if outputs.get("metadata"):
        resolved["outputS3AssetMetadataPath"] = _join_s3(output_bucket, outputs["metadata"])

    # The aux temporary working path is reconstructed from the aux bucket + bucket-relative
    # aux temp prefix.
    aux_bucket = manifest.get("auxBucket", "")
    if aux_bucket and manifest.get("auxTempPrefix"):
        resolved["inputOutputS3AssetAuxiliaryFilesPath"] = _join_s3(aux_bucket, manifest["auxTempPrefix"])

    # The per-pipeline viewer subfolder from the manifest (empty until sourced from the pipeline
    # configuration); exposed so a viewer pipeline can apply its own fallback when empty.
    resolved["auxPreviewPipelineSuffix"] = manifest.get("auxPreviewPipelineSuffix", "") or ""

    # The aux PREVIEW path is per-input-file: aux bucket + the first input file's own
    # auxPreviewPrefix + the per-pipeline auxPreviewPipelineSuffix (viewer subfolder, e.g.
    # "/PotreeViewer"; empty by default). Pipelines writing preview/viewer data use this.
    input_files = resolved["inputFiles"]
    if aux_bucket and input_files:
        file_preview_prefix = (input_files[0] or {}).get("auxPreviewPrefix", "")
        if file_preview_prefix:
            pipeline_suffix = (manifest.get("auxPreviewPipelineSuffix", "") or "").strip("/")
            preview_key = file_preview_prefix.rstrip("/")
            if pipeline_suffix:
                preview_key = f"{preview_key}/{pipeline_suffix}"
            resolved["auxPreviewS3Path"] = _join_s3(aux_bucket, preview_key)

    if manifest.get("inputMetadataS3Location"):
        resolved["inputMetadataS3Location"] = manifest["inputMetadataS3Location"]

    system_config = manifest.get("systemConfig") or {}
    resolved["orchestrationBusArn"] = system_config.get("orchestrationBusArn", "") or ""
    resolved["orchestrationEventPrefix"] = system_config.get("orchestrationEventPrefix", "") or ""

    return resolved


def resolve_pipeline_inputs(data, s3_client):
    """Convenience wrapper: fetch the manifest referenced by the payload (best-effort) and
    return the normalized resolved inputs. Falls back to the legacy payload fields when no
    manifest is available."""
    manifest = fetch_manifest(s3_client, manifest_location(data))
    return resolve_inputs(data, manifest)


def enforce_single_input_file(resolved):
    """Raise when a resolved manifest carries more than one input file.

    The SFN/manifest layer is multi-file-ready (a manifest may carry many input files), but the
    use-case pipelines still process a single file per execution. Until per-pipeline multi-file
    support is a workflow/pipeline configuration flag, a ``vamsExecute`` lambda calls this to fail
    fast with a clear message rather than silently processing only the first file. A manifest with
    zero or one input file (or a legacy no-manifest payload) passes through."""
    input_files = (resolved or {}).get("inputFiles") or []
    if len(input_files) > 1:
        raise Exception(
            f"This pipeline processes a single input file per execution, but the workflow "
            f"manifest supplied {len(input_files)} input files. Multi-file input is not yet "
            f"supported for this pipeline.")


def resolve_input_setting(input_configuration, metadata, config_keys, metadata_key,
                          metadata_scopes=("assetMetadata",)):
    """One pipeline input setting, resolved CONFIG-FIRST with an ASSET-METADATA fallback.

    Precedence is deliberately configuration-first: the input configuration is what the person
    launching the run just supplied on the execute screen (a template's dynamic tag, e.g. a prompt),
    so it must win over a value saved on the asset earlier. When the configuration leaves the field
    BLANK, the asset's metadata supplies it — which is what lets an asset carry a standing default
    while still being overridable per execution.

    Only ``assetMetadata`` is consulted by default: these settings describe the RUN (a prompt, a seed,
    a frame count), not an individual file, and a workflow may select many files. Pass
    ``metadata_scopes`` explicitly for a pipeline whose setting genuinely is per-file.

    ``config_keys`` is tried in order, so a template may use either the canonical upper-case key or a
    lower-case alias. Returns "" when neither source has a value.
    """
    config = input_configuration
    if isinstance(config, str):
        try:
            config = json.loads(config) if config else {}
        except (ValueError, TypeError):
            config = {}
    if isinstance(config, dict):
        for key in (config_keys if isinstance(config_keys, (list, tuple)) else [config_keys]):
            value = config.get(key)
            # A blank/whitespace value counts as "not supplied" so it falls through to metadata.
            if value is not None and str(value).strip() != "":
                return value

    md = metadata
    if isinstance(md, str):
        try:
            md = json.loads(md) if md else {}
        except (ValueError, TypeError):
            md = {}
    if not isinstance(md, dict):
        return ""
    vams = md.get("VAMS", {})
    if not isinstance(vams, dict):
        return ""
    for scope in metadata_scopes:
        scope_md = vams.get(scope, {})
        if isinstance(scope_md, dict):
            value = scope_md.get(metadata_key)
            if value is not None and str(value).strip() != "":
                return value
    return ""
