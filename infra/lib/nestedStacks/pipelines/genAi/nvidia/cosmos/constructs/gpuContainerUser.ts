/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * GPU container user ID and group ID for running NVIDIA/Isaac Batch jobs as non-root.
 *
 * Issue #327: GPU pipeline containers previously ran as root because they share a Hugging Face
 * model cache on EFS, and adding USER to the Dockerfile broke the mount unless the filesystem
 * ownership matched. This uid/gid pair is used consistently across:
 * - The USER directive in each GPU container Dockerfile
 * - The EFS access point POSIX user for the shared model cache
 * - The CDK EFS createAcl for root directory ownership
 *
 * The value 10000:10000 follows the common non-root container convention and avoids conflicts
 * with system accounts (< 1000) and typical user accounts (1000-9999).
 */
export const GPU_CONTAINER_UID = 10000;
export const GPU_CONTAINER_GID = 10000;
