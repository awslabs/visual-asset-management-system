/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * web/CLAUDE.md Rule 2: a new Cloudscape import names the component's subpath, not the barrel
 * `@cloudscape-design/components`, which bundles the whole library. The compliance pages and
 * components import no component from the barrel, and the compliance additions to the asset details
 * pane (Alert, Badge) and the database list (Box) are subpath imports. Older barrel imports in those
 * two files stay as they are.
 */

import fs from "fs";
import path from "path";

const SRC = path.join(__dirname, "..");
const read = (relative: string) => fs.readFileSync(path.join(SRC, relative), "utf8");

/** The names a file imports from the barrel, type imports included. */
const barrelNames = (source: string): string[] =>
    Array.from(
        source.matchAll(
            /import\s+(?:type\s+)?\{([^}]*)\}\s+from\s+"@cloudscape-design\/components"/g
        )
    ).flatMap(([, names]) =>
        names
            .split(",")
            .map((name) => name.trim())
            .filter(Boolean)
    );

const complianceSources = (): string[] => {
    const pages = fs
        .readdirSync(path.join(SRC, "pages"))
        .filter((file) => /Compliance.*\.tsx$/.test(file) && !file.includes(".test."))
        .map((file) => `pages/${file}`);
    const components = fs
        .readdirSync(path.join(SRC, "components/compliance"))
        .filter((file) => /\.tsx?$/.test(file) && !file.includes(".test."))
        .map((file) => `components/compliance/${file}`);
    return [...pages, ...components, "components/asset/tabs/ComplianceTab.tsx"];
};

describe("Cloudscape subpath imports in the compliance code", () => {
    it("reads the compliance sources (control)", () => {
        expect(complianceSources()).toContain("pages/ComplianceSchemas.tsx");
    });

    it.each(complianceSources())("%s imports no component from the barrel", (file) => {
        expect(barrelNames(read(file))).toEqual([]);
    });

    it("imports the asset details pane's Alert and Badge from their subpaths", () => {
        expect(barrelNames(read("components/asset/AssetDetailsPane.tsx"))).not.toEqual(
            expect.arrayContaining(["Alert"])
        );
        expect(barrelNames(read("components/asset/AssetDetailsPane.tsx"))).not.toEqual(
            expect.arrayContaining(["Badge"])
        );
    });

    it("imports the database list's Box from its subpath", () => {
        expect(
            barrelNames(read("components/list/list-definitions/DatabaseListDefinition.tsx"))
        ).not.toContain("Box");
    });
});
