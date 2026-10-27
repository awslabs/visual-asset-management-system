/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Asset data is read from the API, not from the browser's localforage (IndexedDB) store. The only
 * module that touches localforage is the session manager, which empties it on sign-out.
 */

import fs from "fs";
import path from "path";

const SRC = path.join(__dirname, "..");

function sourceFiles(dir: string): string[] {
    return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) return sourceFiles(full);
        return /\.(ts|tsx)$/.test(entry.name) && !/\.test\.(ts|tsx)$/.test(entry.name)
            ? [full]
            : [];
    });
}

describe("localforage usage", () => {
    it("is limited to the session manager", () => {
        const importers = sourceFiles(SRC)
            .filter((file) => /from\s+["']localforage["']/.test(fs.readFileSync(file, "utf8")))
            .map((file) => path.relative(SRC, file).split(path.sep).join("/"));

        expect(importers).toEqual(["utils/sessionManager.ts"]);
    });
});
