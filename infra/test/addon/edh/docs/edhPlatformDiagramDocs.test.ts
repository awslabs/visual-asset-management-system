/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The enablement page shows EDH's own platform architecture -- everything an EDH installation may deploy,
 * independent of VAMS -- under "Step 2: Deploy the EDH cluster". The image is EDH's diagram, supplied as a
 * PNG with no draw.io source in this repository, so it is held here rather than in the add-on's draw.io
 * diagram list: the file exists as a PNG, the page embeds it once in that section, and the section points
 * to the add-on's own overview diagram for the part VAMS uses.
 *
 * Durable (root CLAUDE.md Rule 13): the page's sections can move, and EDH can publish a new diagram.
 */

import * as fs from "fs";
import * as path from "path";
import { REPO_ROOT, exists, page, sectionOf } from "../../../docs/support/docsText";
import { pageDir as PAGE_DIR } from "./placement";

const ENABLE = `${PAGE_DIR}/enable.md`;
const IMAGE = "documentation/docusaurus-site/static/img/edh_platformArchitecture.png";
const EMBED = "](/img/edh_platformArchitecture.png)";
const SECTION = "### What EDH deploys";
const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

describe("EDH platform architecture diagram", () => {
    test("the image is a PNG in the site's image folder", () => {
        expect(exists(IMAGE)).toBe(true);
        const head = fs.readFileSync(path.join(REPO_ROOT, IMAGE)).subarray(0, PNG_SIGNATURE.length);
        expect(head.equals(PNG_SIGNATURE)).toBe(true);
    });

    test("the enablement page embeds it once, in the section on what EDH deploys", () => {
        const text = page(ENABLE);
        expect(text.split(EMBED)).toHaveLength(2);
        expect(sectionOf(text, SECTION)).toContain(EMBED);
    });

    test("the section separates EDH's platform from the part the add-on uses", () => {
        const section = sectionOf(page(ENABLE), SECTION);
        expect(section).toContain("independent of VAMS");
        expect(section).toContain("(overview.md#how-vams-integrates-with-edh)");
    });
});
