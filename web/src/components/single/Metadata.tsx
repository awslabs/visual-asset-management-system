/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Metadata data model used by the asset upload workflow.
 * A simple key-value map of string metadata fields.
 */
export class Metadata {
    [key: string]: string;
}
