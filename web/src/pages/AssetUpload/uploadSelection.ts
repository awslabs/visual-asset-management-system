/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Glue between the upload preview (components/form/uploadPaths.ts) and the upload pages: the
 * mapping of planned files onto the upload table items the upload workflow takes, and the
 * advisory existing-path lookup of the Add Files page.
 */

import { fetchAssetS3FilesPage } from "../../services/APIService";
import { assetFilePath } from "../../utils/assetFilePath";
import { ExistingState, PlannedFile } from "../../components/form/uploadPaths";
import { FileUploadTableItem } from "./FileUploadTable";

/** The planned files as the queued items the upload workflow and UploadManager take. */
export function plannedToUploadItems(planned: PlannedFile[]): FileUploadTableItem[] {
    return planned.map((p, index) => ({
        handle: p.handle,
        index,
        name: p.name,
        size: p.size,
        relativePath: p.finalPath,
        progress: 0,
        status: "Queued",
        loaded: 0,
        total: p.size,
    }));
}

/** Upper bound on the keys read for the existing-path check, so its cost stays bounded. */
export const EXISTING_PATH_KEY_CAP = 10000;

export interface ExistingPathsResult {
    /** Final asset path ("/scene/a.png") -> current or archived. */
    paths: Map<string, ExistingState>;
    /** False when the listing failed, was refused, or hit the cap. */
    complete: boolean;
}

/**
 * List the asset's files under the destination (current and archived) through the list-files
 * API, which authorizes the read. The result only marks the preview: a refusal or failure yields
 * complete=false and no marks, and the upload itself is still enforced by the API.
 */
export async function fetchExistingPaths({
    databaseId,
    assetId,
    destination,
    cap = EXISTING_PATH_KEY_CAP,
}: {
    databaseId: string;
    assetId: string;
    destination: string;
    cap?: number;
}): Promise<ExistingPathsResult> {
    const paths = new Map<string, ExistingState>();
    // The prefix is the destination's path in the asset: the API drops exactly one leading "/" (the path
    // marker), so "//" is the folder of the keys with an empty segment after the asset's folder, not the
    // whole asset (#1977). Item relativePaths come back relative to that prefix with one leading "/".
    const prefix = destination === "/" ? null : assetFilePath(destination);
    let startingToken: string | null = null;
    let seen = 0;
    try {
        do {
            const page: any = await fetchAssetS3FilesPage({
                databaseId,
                assetId,
                includeArchived: true,
                basic: true,
                prefix,
                startingToken,
            });
            if (!page?.success) return { paths: new Map(), complete: false };
            for (const item of page.items || []) {
                if (item.isFolder) continue;
                const listed = String(item.relativePath || "");
                const rel = listed.startsWith("/") ? listed.slice(1) : listed;
                if (!rel) continue;
                paths.set(destination + rel, item.isArchived ? "archived" : "exists");
            }
            seen += (page.items || []).length;
            startingToken = page.nextToken || null;
            if (startingToken && seen >= cap) return { paths, complete: false };
        } while (startingToken);
    } catch (error) {
        console.warn("Existing-path check failed:", error);
        return { paths: new Map(), complete: false };
    }
    return { paths, complete: true };
}
