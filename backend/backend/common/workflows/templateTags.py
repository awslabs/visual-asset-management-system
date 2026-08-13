# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Canonical names of the system template tags recognized in pipeline input configuration.

A pipeline's input configuration (and selected execution fields) may contain ``{{tagName}}``
placeholders substituted per pipeline task by ``templateRender``. This module is the single source
of truth for the recognized tag NAMES — the bare identifier inside the braces (no ``{{ }}``). Do not
hard-code tag-name string literals at call sites; import them from here so every supported tag can
be found and changed in one place.

Each constant's value is the tag name exactly as authors write it between ``{{`` and ``}}``. The
renderer decides each tag's substitution kind (scalar string vs. JSON literal) and value; this
module only fixes the names and groups them.
"""

# --- A. Execution & workflow identity (scalar) ---
EXECUTION_ID = "executionId"
WORKFLOW_ID = "workflowId"
WORKFLOW_DATABASE_ID = "workflowDatabaseId"
TRIGGER_TYPE = "triggerType"
EXECUTING_USER_NAME = "executingUserName"

# --- B. Pipeline-task identity (scalar) ---
PIPELINE_EXECUTION_ID = "pipelineExecutionId"
PIPELINE_ID = "pipelineId"
PIPELINE_NAME = "pipelineName"
PIPELINE_DATABASE_ID = "pipelineDatabaseId"
JOB_NAME = "jobName"

# --- C. Timestamps (scalar) ---
JOB_START_TIMESTAMP = "jobStartTimestamp"
JOB_START_TIMESTAMP_UNIX = "jobStartTimestampUnix"
JOB_START_DATE = "jobStartDate"
EXECUTION_START_TIMESTAMP = "executionStartTimestamp"

# --- D. First input file (scalar) ---
FIRST_ASSET_FILE_DATABASE_ID = "firstAssetFileDatabaseId"
FIRST_ASSET_FILE_ASSET_ID = "firstAssetFileAssetId"
FIRST_ASSET_FILE_ASSET_BUCKET = "firstAssetFileAssetBucket"
FIRST_ASSET_FILE_ASSET_ROOT_S3_KEY = "firstAssetFileAssetRootS3Key"
FIRST_ASSET_FILE_RELATIVE_PATH = "firstAssetFileRelativePath"
FIRST_ASSET_FILE_KEY = "firstAssetFileKey"
FIRST_ASSET_FILE_VERSION_ID = "firstAssetFileVersionId"
FIRST_ASSET_FILE_AUX_PREVIEW_PREFIX = "firstAssetFileAuxPreviewPrefix"
FIRST_ASSET_FILE_S3_URI = "firstAssetFileS3Uri"
FIRST_ASSET_FILE_AUX_PREVIEW_S3_URI = "firstAssetFileAuxPreviewS3Uri"
FIRST_ASSET_FILE_FILE_NAME = "firstAssetFileFileName"
FIRST_ASSET_FILE_FILE_NAME_NO_EXT = "firstAssetFileFileNameNoExt"
FIRST_ASSET_FILE_FILE_EXTENSION = "firstAssetFileFileExtension"

# --- E. Input-file collections (JSON literal) ---
ASSET_FILE_KEY_ARRAY = "assetFileKeyArray"
ASSET_FILE_RELATIVE_PATH_ARRAY = "assetFileRelativePathArray"
ASSET_FILE_S3_URI_ARRAY = "assetFileS3UriArray"
ASSET_FILE_VERSION_ID_ARRAY = "assetFileVersionIdArray"
ASSET_FILE_OBJECT_ARRAY = "assetFileObjectArray"
ASSET_FILE_ASSET_ID_ARRAY = "assetFileAssetIdArray"
ASSET_FILE_UNIQUE_ASSET_ID_ARRAY = "assetFileUniqueAssetIdArray"
ASSET_FILE_DATABASE_ID_ARRAY = "assetFileDatabaseIdArray"
ASSET_FILE_UNIQUE_DATABASE_ID_ARRAY = "assetFileUniqueDatabaseIdArray"
ASSET_FILE_COUNT = "assetFileCount"

# --- F. Output locations (scalar) ---
OUTPUT_BUCKET = "outputBucket"
OUTPUT_FILES_PREFIX = "outputFilesPrefix"
OUTPUT_FILES_S3_URI = "outputFilesS3Uri"
OUTPUT_PREVIEWS_PREFIX = "outputPreviewsPrefix"
OUTPUT_PREVIEWS_S3_URI = "outputPreviewsS3Uri"
OUTPUT_METADATA_PREFIX = "outputMetadataPrefix"
OUTPUT_METADATA_S3_URI = "outputMetadataS3Uri"
OUTPUT_RESULTS_PREFIX = "outputResultsPrefix"
OUTPUT_RESULTS_S3_URI = "outputResultsS3Uri"
OUTPUT_TARGET_ASSET_ID = "outputTargetAssetId"
OUTPUT_TARGET_DATABASE_ID = "outputTargetDatabaseId"
OUTPUT_TARGET_LOCATION_TYPE = "outputTargetLocationType"
OUTPUT_TARGET_ASSET_ROOT_S3_KEY = "outputTargetAssetRootS3Key"
OUTPUT_FILE_BASE_EXECUTION_PATH_EXTENSION = "outputFileBaseExecutionPathExtension"

# --- G. Auxiliary locations (scalar) ---
AUX_BUCKET = "auxBucket"
AUX_TEMP_PREFIX = "auxTempPrefix"
AUX_TEMP_S3_URI = "auxTempS3Uri"
AUX_PREVIEW_PIPELINE_SUFFIX = "auxPreviewPipelineSuffix"

# --- H. Metadata / configuration locations (scalar) ---
INPUT_METADATA_S3_LOCATION = "inputMetadataS3Location"
INPUT_CONFIGURATION_S3_LOCATION = "inputConfigurationS3Location"

# --- I. System / orchestration (scalar) ---
ORCHESTRATION_BUS_ARN = "orchestrationBusArn"
ORCHESTRATION_EVENT_PREFIX = "orchestrationEventPrefix"

# --- J. Metadata content (JSON object; lazy metadata read) ---
INPUT_METADATA_OBJECT = "inputMetadataObject"
ASSET_METADATA_OBJECT = "assetMetadataObject"
FILE_METADATA_OBJECT = "fileMetadataObject"
FILE_ATTRIBUTES_OBJECT = "fileAttributesObject"
ASSET_DATA_OBJECT = "assetDataObject"
DATABASE_METADATA_OBJECT = "databaseMetadataObject"

# --- K. Deadline Cloud (scalar) ---
# Reserved for the future pipeline system-configuration that sets a pipeline's Deadline Cloud farm
# / queue / storage profile. They are DEFINED now (so a config that references them renders without
# tripping the strict unknown-tag check) but resolve to EMPTY strings until the pipeline
# configuration supplies them during the pipeline/workflow overhaul. Keeping them here (rather than
# leaving them unknown) is what lets a Deadline OpenJD template be authored against them today.
DEADLINE_FARM_ID = "deadlineFarmId"
DEADLINE_QUEUE_ID = "deadlineQueueId"
DEADLINE_STORAGE_PROFILE_ID = "deadlineStorageProfileId"

# Deadline Cloud tags default to empty until the pipeline configuration supplies them (see above).
DEADLINE_TAGS = (
    DEADLINE_FARM_ID,
    DEADLINE_QUEUE_ID,
    DEADLINE_STORAGE_PROFILE_ID,
)


# The complete set of reserved system tag names — every {{tag}} the renderer resolves itself. A
# user-defined template tag key may NOT collide with any of these, and a caller may not supply a
# value for one (the engine owns them). The shared tag-schema validator (common/templateTagSchema.py)
# checks declared tag keys and caller-provided tag keys against this set. Metadata-content tags whose
# names begin with the dynamic ``metadata_`` prefix are also reserved (see METADATA_DYNAMIC_TAG_PREFIX).
SYSTEM_TAG_NAMES = frozenset(
    {
        # A. Execution & workflow identity
        EXECUTION_ID, WORKFLOW_ID, WORKFLOW_DATABASE_ID, TRIGGER_TYPE, EXECUTING_USER_NAME,
        # B. Pipeline-task identity
        PIPELINE_EXECUTION_ID, PIPELINE_ID, PIPELINE_NAME, PIPELINE_DATABASE_ID, JOB_NAME,
        # C. Timestamps
        JOB_START_TIMESTAMP, JOB_START_TIMESTAMP_UNIX, JOB_START_DATE, EXECUTION_START_TIMESTAMP,
        # D. First input file
        FIRST_ASSET_FILE_DATABASE_ID, FIRST_ASSET_FILE_ASSET_ID, FIRST_ASSET_FILE_ASSET_BUCKET,
        FIRST_ASSET_FILE_ASSET_ROOT_S3_KEY, FIRST_ASSET_FILE_RELATIVE_PATH, FIRST_ASSET_FILE_KEY,
        FIRST_ASSET_FILE_VERSION_ID, FIRST_ASSET_FILE_AUX_PREVIEW_PREFIX, FIRST_ASSET_FILE_S3_URI,
        FIRST_ASSET_FILE_AUX_PREVIEW_S3_URI, FIRST_ASSET_FILE_FILE_NAME,
        FIRST_ASSET_FILE_FILE_NAME_NO_EXT, FIRST_ASSET_FILE_FILE_EXTENSION,
        # E. Input-file collections
        ASSET_FILE_KEY_ARRAY, ASSET_FILE_RELATIVE_PATH_ARRAY, ASSET_FILE_S3_URI_ARRAY,
        ASSET_FILE_VERSION_ID_ARRAY, ASSET_FILE_OBJECT_ARRAY, ASSET_FILE_ASSET_ID_ARRAY,
        ASSET_FILE_UNIQUE_ASSET_ID_ARRAY, ASSET_FILE_DATABASE_ID_ARRAY,
        ASSET_FILE_UNIQUE_DATABASE_ID_ARRAY, ASSET_FILE_COUNT,
        # F. Output locations
        OUTPUT_BUCKET, OUTPUT_FILES_PREFIX, OUTPUT_FILES_S3_URI, OUTPUT_PREVIEWS_PREFIX,
        OUTPUT_PREVIEWS_S3_URI, OUTPUT_METADATA_PREFIX, OUTPUT_METADATA_S3_URI, OUTPUT_RESULTS_PREFIX,
        OUTPUT_RESULTS_S3_URI, OUTPUT_TARGET_ASSET_ID, OUTPUT_TARGET_DATABASE_ID,
        OUTPUT_TARGET_LOCATION_TYPE, OUTPUT_TARGET_ASSET_ROOT_S3_KEY,
        OUTPUT_FILE_BASE_EXECUTION_PATH_EXTENSION,
        # G. Auxiliary locations
        AUX_BUCKET, AUX_TEMP_PREFIX, AUX_TEMP_S3_URI, AUX_PREVIEW_PIPELINE_SUFFIX,
        # H. Metadata / configuration locations
        INPUT_METADATA_S3_LOCATION, INPUT_CONFIGURATION_S3_LOCATION,
        # I. System / orchestration
        ORCHESTRATION_BUS_ARN, ORCHESTRATION_EVENT_PREFIX,
        # J. Metadata content
        INPUT_METADATA_OBJECT, ASSET_METADATA_OBJECT, FILE_METADATA_OBJECT,
        FILE_ATTRIBUTES_OBJECT, ASSET_DATA_OBJECT, DATABASE_METADATA_OBJECT,
        # K. Deadline Cloud
        DEADLINE_FARM_ID, DEADLINE_QUEUE_ID, DEADLINE_STORAGE_PROFILE_ID,
    }
)

# Reserved prefix for the future dynamic metadata tags ({{metadata_<key>}}). A user-defined tag key
# may not start with this prefix.
METADATA_DYNAMIC_TAG_PREFIX = "metadata_"


def is_reserved_tag_key(tag_key: str) -> bool:
    """True when a tag key collides with a system tag name or the reserved dynamic-metadata prefix.
    User-defined template tag keys must be rejected when this returns True."""
    if not tag_key:
        return False
    return tag_key in SYSTEM_TAG_NAMES or tag_key.startswith(METADATA_DYNAMIC_TAG_PREFIX)
