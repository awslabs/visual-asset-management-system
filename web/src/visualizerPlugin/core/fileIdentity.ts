/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { FileInfo } from "./types";

/** The parts of a file that establish which file it is. */
export type FileIdentityParts = Pick<FileInfo, "key" | "assetId" | "databaseId">;

/** Separates the parts. A control character cannot appear in a path, asset id or database id. */
const SEPARATOR = "\u0000";

/**
 * A file's identity, for selection sets, de-duplication and removal.
 *
 * A key on its own is NOT unique. The same asset-relative path exists in many assets — `model.glb`
 * under one asset is a different file from `model.glb` under another — so a file is identified by its
 * owning database, its owning asset and its key together. Treating the key as the identity made two
 * such files collapse into a single selection: checking the second one appeared to do nothing,
 * because it was taken for a file that was already selected.
 */
export function fileIdentity(file: FileIdentityParts): string {
    return [file?.databaseId ?? "", file?.assetId ?? "", file?.key ?? ""].join(SEPARATOR);
}
