/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * An asset id placed in an in-app link, or in an API URL built outside `apiClient`, is encoded with
 * `encodeURIComponent` (#1476).
 *
 * An asset id may end in '#'. In a raw hash-router link `#/databases/db/assets/x-q#` the router reads
 * the second '#' as a fragment and opens asset `x-q`, and a viewer's stream URL loses the rest of its
 * path. The router decodes route parameters, so an encoded link opens the asset as it is. Paths sent
 * through `apiClient` are covered by `buildUrl` (`services/apiClient.pathDelimiters.test.ts`).
 */

import fs from "fs";
import path from "path";
import { matchRoutes } from "react-router";

const SRC = path.join(__dirname, "..");

function sourceFiles(dir: string): string[] {
    return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) return entry.name === "node_modules" ? [] : sourceFiles(full);
        return /\.(ts|tsx)$/.test(entry.name) && !/\.(test|spec)\.|\.testdata\./.test(entry.name)
            ? [full]
            : [];
    });
}

// An in-app asset route (`/databases/${db}/assets/${id}`) or an API URL with a base prefix
// (`${config.api}database/${db}/assets/${id}`) whose asset id is interpolated, and the
// `"#/databases/" + db + "/assets/" + id` concatenation.
const TEMPLATE = /(?:#?\/databases\/|\}database\/)\$\{[^}]+\}\/assets\/\$\{\s*([^}]+?)\s*\}/g;
const CONCATENATION =
    /["']#?\/databases\/["']\s*\+\s*[\w.[\]"']+\s*\+\s*["']\/assets\/["']\s*\+\s*([A-Za-z_][\w.[\]"']*\(?)/g;

function rawAssetIds(): string[] {
    const offenders: string[] = [];
    let encoded = 0;
    for (const file of sourceFiles(SRC)) {
        const text = fs.readFileSync(file, "utf8");
        for (const pattern of [TEMPLATE, CONCATENATION]) {
            for (const match of text.matchAll(pattern)) {
                if (match[1].startsWith("encodeURIComponent(")) {
                    encoded += 1;
                } else {
                    const line = text.slice(0, match.index).split("\n").length;
                    offenders.push(`${path.relative(SRC, file)}:${line} ${match[1]}`);
                }
            }
        }
    }
    rawAssetIds.encoded = encoded;
    return offenders;
}
rawAssetIds.encoded = 0;

describe("asset ids in in-app links and direct API URLs", () => {
    it("are all encoded", () => {
        expect(rawAssetIds()).toEqual([]);
    });

    it("are found by the scan (control)", () => {
        rawAssetIds();
        // 40 in-app links, 9 viewer stream URLs and the execution page's link at the time of #1476.
        expect(rawAssetIds.encoded).toBeGreaterThanOrEqual(50);
    });

    it("are decoded back by the router, so the asset page opens the asset itself", () => {
        const routes = [{ path: "/databases/:databaseId/assets/:assetId" }];
        const link = `/databases/db-1/assets/${encodeURIComponent("x-q#")}`;
        expect(matchRoutes(routes, link)?.[0].params.assetId).toBe("x-q#");
        // The raw link opens the sibling asset.
        expect(matchRoutes(routes, "/databases/db-1/assets/x-q#")?.[0].params.assetId).toBe("x-q");
    });
});
