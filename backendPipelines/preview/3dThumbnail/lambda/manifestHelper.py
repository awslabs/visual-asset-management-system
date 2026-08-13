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
the manifest and falling back to the legacy payload fields when no manifest is present, so a payload
without a manifest resolves to the pre-manifest behavior. A payload that DOES reference a manifest
which cannot be read is an error, not a fallback: the manifest is the sole carrier of asset identity
and the output paths, so resolving blanks would start a job that fails only after its compute is
provisioned.
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
    """Fetch + parse the manifest JSON from S3.

    Returns ``None`` when no location is supplied, so a legacy payload carrying its fields inline
    still resolves. A location that is supplied but cannot be read RAISES: the manifest is the only
    carrier of the asset, database, and output paths, so continuing without it yields an execution
    with blank identity that provisions its compute before failing."""
    if not manifest_s3_location:
        return None
    bucket, key = parse_s3_uri(manifest_s3_location)
    if not bucket or not key:
        raise Exception(
            f"The workflow supplied a malformed input manifest location: {manifest_s3_location}")
    try:
        resp = s3_client.get_object(Bucket=bucket, Key=key)
        return json.loads(resp["Body"].read().decode("utf-8"))
    except Exception as e:
        raise Exception(
            f"Could not read the workflow input manifest at {manifest_s3_location}: {e}")


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


def get_database_metadata(envelope, database_id):
    """Return the metadata map recorded for one databaseId in a v2 grouped envelope's top-level
    ``databases`` list, or ``{}`` when the envelope carries no entry for it.

    A run that captured exactly ONE database resolves to that database whatever the requested id. A
    file-less run has no asset to project through, so its subject databaseId is empty and an id match
    would find nothing — yet the one database it named is unambiguously the database its metadata
    describes. With several databases the requested id is the only thing that can disambiguate them,
    so the match is required."""
    groups = (envelope or {}).get("databases", []) or []
    if len(groups) == 1:
        return (groups[0] or {}).get("metadata") or {}
    for group in groups:
        if (group or {}).get("databaseId") == database_id:
            return (group or {}).get("metadata") or {}
    return {}


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


def database_metadata_for(metadata_body, database_id):
    """Database-level metadata for a database. Reads the v2 top-level ``databases`` entry (a lone
    captured database resolves whatever the requested id, see ``get_database_metadata``), falling
    back to the legacy ``VAMS.databaseMetadata`` scope for a v1 body."""
    if isinstance(metadata_body, dict) and "assets" in metadata_body:
        return get_database_metadata(metadata_body, database_id)
    return _legacy_scope(metadata_body, "databaseMetadata")


def to_legacy_vams_view(metadata_body, database_id="", asset_id="", file_key=""):
    """Project a metadata payload onto the legacy ``{"VAMS": {assetData, assetMetadata,
    fileMetadata, fileAttributes, databaseMetadata}}`` view existing pipeline readers dig into.

    - A v2 grouped body is projected for the given (databaseId, assetId, fileKey): assetData +
      assetMetadata come from the asset's '/' record, fileMetadata/fileAttributes from the fileKey
      record. A fileKey of '/' (whole-asset selection) resolves to the asset-level record only,
      leaving the file scopes empty — mirroring the writer, which emits no per-file record for a
      whole-asset selection. databaseMetadata is the entry the envelope's top-level 'databases' list
      holds for THE databaseId being projected, so the five scopes describe one coherent (database,
      asset, file) subject; it is empty when the list carries no entry for that database.
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
            "databaseMetadata": get_database_metadata(body, database_id),
        }}
    return body


def resolved_file_key(resolved):
    """The per-file metadata key for a resolved manifest: the first input file's relative path,
    or '/' (asset level) when the selection carries no input files."""
    input_files = (resolved or {}).get("inputFiles") or []
    if not input_files:
        return "/"
    return _normalize_file_key(input_files[0].get("relativePath"))


def run_vams_view(metadata_body, resolved):
    """Project a metadata payload onto the legacy ``{"VAMS": {...}}`` view for the subject a RESOLVED
    manifest describes, choosing that subject the way the backend's renderer does.

    The subject is the run's primary INPUT FILE when the manifest carries one: its (databaseId,
    assetId) plus ``resolved_file_key``. With no input files (arity 'none') the resolved identity is
    the run's OUTPUT TARGET, which is where results are written rather than where metadata was read
    from — so the subject becomes the envelope's FIRST asset group at its asset level ('/'). That
    group is the run's first metadata-source asset, since a run with no input files contributes no
    other asset to the envelope. An envelope naming no asset at all (a database-only selection) keeps
    an empty subject, leaving the asset and file scopes empty while ``databaseMetadata`` still resolves
    through its lone-database rule.

    Mirrors the subject chain in the backend's render path (input file, else first metadata-source
    asset at '/', else nothing), so a template tag and a pipeline's own metadata read resolve to the
    same values. A v1 body passes through ``to_legacy_vams_view`` unchanged."""
    body = metadata_body or {}
    if not isinstance(body, dict):
        return {}
    if not (body.get("schemaVersion") == METADATA_SCHEMA_VERSION_GROUPED and "assets" in body):
        return to_legacy_vams_view(body)
    resolved = resolved or {}
    if resolved.get("inputFiles"):
        return to_legacy_vams_view(body, resolved.get("databaseId", ""), resolved.get("assetId", ""),
                                   resolved_file_key(resolved))
    groups = body.get("assets") or []
    first_group = (groups[0] if groups else {}) or {}
    return to_legacy_vams_view(body, first_group.get("databaseId", "") or "",
                               first_group.get("assetId", "") or "", "/")


class InputConfigurationError(RuntimeError):
    """Raised when an input-configuration file exists but cannot be parsed as a JSON object."""


def fetch_input_configuration(s3_client, input_configuration_s3_location):
    """Fetch + parse the per-pipeline input configuration file (the user-defined
    ``inputParameters``) from S3, returning the parsed object. The configuration file is the
    raw user JSON (it carries no VAMS schema envelope), so it is returned as parsed.

    ``{}`` when no configuration file was supplied or it could not be fetched — callers rely on that
    falsy result to fall back to the legacy inline ``inputParameters`` field. Raises
    ``InputConfigurationError`` when the file WAS fetched but its body is not a JSON object.

    The distinction is the point. Returning ``{}`` for a malformed body makes it indistinguishable from
    "no configuration file", so the pipeline runs on its defaults (or a stale inline field), reports
    SUCCESS, and every parameter the caller set is silently gone. This is the launch path, so failing
    here costs nothing but a failed execution record; failing later costs a full job that did the wrong
    thing.

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
    except Exception:  # nosec B110 - an unfetchable config file yields {} (the legacy fallback)
        return {}
    if not body or not body.strip():
        return {}
    try:
        parsed = json.loads(body)
    except ValueError as e:
        raise InputConfigurationError(
            f"The input configuration at {input_configuration_s3_location} is not valid JSON: {e}")
    if not isinstance(parsed, dict):
        raise InputConfigurationError(
            f"The input configuration at {input_configuration_s3_location} is not a JSON object")
    return parsed


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

    # A run with no input files (inputFileArity "none", or a results-only workflow) has no input file
    # to take an asset identity from, so it falls back to the execution's OUTPUT target. That target
    # is carried in two different shapes and both are read here:
    #   - the manifest's `outputTarget` block, which is where a manifest-driven run carries it; and
    #   - top-level `outputAssetId`/`outputDatabaseId` on the Step Functions body, the legacy shape.
    # The per-pipeline task body deliberately carries only the manifest POINTER, so reading the
    # legacy keys alone leaves assetId empty for every arity-"none" run — and the container then
    # fails with "assetId is required in pipeline definition" after the job has been scheduled.
    # Applied before the no-manifest return so both paths get it; a manifest input file below still
    # wins when one is present.
    output_target = (manifest or {}).get("outputTarget") or {}
    if not resolved["assetId"]:
        resolved["assetId"] = output_target.get("assetId") or data.get("outputAssetId") or ""
    if not resolved["databaseId"]:
        resolved["databaseId"] = output_target.get("databaseId") or data.get("outputDatabaseId") or ""

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
    """Convenience wrapper: fetch the manifest referenced by the payload and return the normalized
    resolved inputs. Falls back to the legacy payload fields only when the payload references no
    manifest; a referenced manifest that cannot be read raises."""
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


def _unambiguous_vams_view(envelope):
    """Project a v2 grouped envelope onto the legacy view WITHOUT a caller-supplied subject.

    A scope resolves only where it is unambiguous. The asset and file scopes need exactly ONE asset
    group — several assets leave no way to tell which one a value belongs to — and the file scopes
    additionally need exactly one per-file record within it; anything else leaves those scopes empty.
    The database scope carries its own lone-database rule (``get_database_metadata``), so it still
    resolves for a file-less run whose envelope names one database and no asset."""
    assets = envelope.get("assets", []) or []
    database_id, asset_id, file_key = "", "", "/"
    if len(assets) == 1:
        group = assets[0] or {}
        database_id = group.get("databaseId", "") or ""
        asset_id = group.get("assetId", "") or ""
        file_records = [record for record in (group.get("files") or [])
                        if (record or {}).get("fileKey") not in (None, "", "/")]
        if len(file_records) == 1:
            file_key = file_records[0].get("fileKey") or "/"
    return to_legacy_vams_view(envelope, database_id, asset_id, file_key)


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
    ``metadata_scopes`` explicitly for a pipeline whose setting genuinely is per-file, or to consult
    ``databaseMetadata`` — the scope that lets a standing value live on the database rather than on
    each asset (``("assetMetadata", "databaseMetadata")``).

    ``metadata`` may be either the v2 grouped envelope ``fetch_metadata`` returns or a legacy
    ``{"VAMS": {...}}`` view a caller already projected with ``to_legacy_vams_view``. A grouped
    envelope is projected here through ``_unambiguous_vams_view``, so a scope supplies a value only
    when the envelope leaves no doubt which asset (or database) it came from.

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
    if md.get("schemaVersion") == METADATA_SCHEMA_VERSION_GROUPED and "assets" in md:
        md = _unambiguous_vams_view(md)
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
