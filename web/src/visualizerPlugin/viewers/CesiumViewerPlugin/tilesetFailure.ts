/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { describeStreamFailure } from "../../core/streamFailure";

/** The JSON body of a Cesium request error's response, which Cesium keeps as text. */
function responseBody(response: unknown): any {
    if (typeof response !== "string") return response;
    try {
        return JSON.parse(response);
    } catch {
        return undefined;
    }
}

/**
 * Why the tileset at `key` failed to load. A Cesium `RequestErrorEvent` carries the HTTP status
 * (`statusCode`, absent when the request got no readable response) and the response text, so it is
 * named like any other stream failure; any other failure keeps Cesium's own message.
 */
export function describeTilesetFailure(key: string, error: any): string {
    if (error && typeof error === "object" && "statusCode" in error) {
        return describeStreamFailure(`the tileset "${key}"`, {
            status: error.statusCode,
            body: responseBody(error.response),
        });
    }
    const message = error?.message || error?.toString() || "Unknown error";
    return `Tileset loading failed for "${key}": ${message}`;
}

/** The notice for tiles of a loaded tileset that failed, which otherwise leave holes in the model. */
export function tileFailureNotice(count: number, lastMessage?: string): string {
    const requests = count === 1 ? "1 tile request" : `${count} tile requests`;
    const last = lastMessage ? ` Last error: ${lastMessage}` : "";
    return `${requests} failed, so the model may be incomplete.${last}`;
}

/** The notice for tilesets that failed beside others that loaded, naming the first failure. */
export function partialTilesetsNotice(failures: string[], total: number): string {
    return `${failures.length} of ${total} tilesets failed to load. ${failures[0]}`;
}
