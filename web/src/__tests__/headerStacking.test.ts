/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * index.scss declared #headerWrapper twice: z-index 1002, then a later rule with z-index 1000, which
 * applied. A menu opened from the sticky header paints inside the header's stacking context, so at
 * the mobile toolbar's z-index (1000, also sticky, later in the document) the toolbar covered the
 * Settings menu's first item (Light Theme) at mobile-layout widths. The header's effective z-index
 * is read from index.scss and the mobile layers from the installed Cloudscape AppLayout CSS, so a
 * Cloudscape upgrade that raises those layers fails here too.
 */

import fs from "fs";
import path from "path";

const indexScss = fs.readFileSync(path.join(__dirname, "../styles/index.scss"), "utf8");
const componentsDir = path.dirname(require.resolve("@cloudscape-design/components/package.json"));
const appLayoutCss = fs.readFileSync(
    path.join(componentsDir, "app-layout/visual-refresh/styles.scoped.css"),
    "utf8"
);

const zIndexes = (body: string) =>
    Array.from(body.matchAll(/z-index:\s*(\d+)/g)).map(([, n]) => Number(n));

/** Same selector, same specificity: the last declared z-index applies. */
function headerZIndex(): number {
    const declared = Array.from(
        indexScss.matchAll(/(?:^|\n)#headerWrapper\s*\{([^}]*)\}/g)
    ).flatMap(([, body]) => zIndexes(body));
    if (!declared.length) throw new Error("no #headerWrapper z-index in index.scss");
    return declared[declared.length - 1];
}

/** z-indexes of AppLayout rules whose selector names one of `layers`. */
function layerZIndexes(layers: RegExp): number[] {
    return Array.from(appLayoutCss.matchAll(/([^{}]+)\{([^{}]*)\}/g))
        .filter(([, selector]) => layers.test(selector))
        .flatMap(([, , body]) => zIndexes(body));
}

const MOBILE_LAYERS =
    /awsui_(mobile-toolbar|navigation-container|tools-container|drawers-container)_/;

describe("header stacking", () => {
    it("finds the mobile toolbar in the installed Cloudscape CSS", () => {
        // Control: an empty list would make the next check pass trivially.
        expect(layerZIndexes(/awsui_mobile-toolbar_/).length).toBeGreaterThan(0);
    });

    it("keeps the header above the AppLayout mobile toolbar and drawers", () => {
        expect(headerZIndex()).toBeGreaterThan(Math.max(...layerZIndexes(MOBILE_LAYERS)));
    });
});
