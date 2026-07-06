# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")

# Env vars required by the Garnet indexers at import time
os.environ.setdefault("GARNET_INGESTION_QUEUE_URL",
                      "https://sqs.us-east-1.amazonaws.com/123456789012/garnet-ingest")
os.environ.setdefault("GARNET_API_ENDPOINT", "https://garnet.example.com")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-assets")
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "test-databases")
os.environ.setdefault("DATABASE_METADATA_STORAGE_TABLE_NAME", "test-db-metadata")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata")
os.environ.setdefault("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attributes")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets")
os.environ.setdefault("ASSET_LINKS_STORAGE_TABLE_V2_NAME", "test-asset-links")
os.environ.setdefault("ASSET_LINKS_METADATA_STORAGE_TABLE_NAME", "test-asset-links-meta")
os.environ.setdefault("ASSET_VERSIONS_STORAGE_TABLE_NAME", "test-asset-versions")
os.environ.setdefault("SYNC_TRACKING_OUTBOUND_STORAGE_TABLE_NAME", "test-sync-tracking")
