/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Loads the committed GeoTIFF fixtures (see __fixtures__/generate_fixtures.py) for the unit tests.

import fs from "fs";
import path from "path";
import { fromArrayBuffer } from "geotiff";

const DIR = path.join(__dirname, "__fixtures__");

export function fixtureBytes(name: string): ArrayBuffer {
    const buffer = fs.readFileSync(path.join(DIR, name));
    return buffer.buffer.slice(
        buffer.byteOffset,
        buffer.byteOffset + buffer.byteLength
    ) as ArrayBuffer;
}

export function fixtureSize(name: string): number {
    return fs.statSync(path.join(DIR, name)).size;
}

export async function loadFixtureTiff(name: string) {
    return fromArrayBuffer(fixtureBytes(name));
}
