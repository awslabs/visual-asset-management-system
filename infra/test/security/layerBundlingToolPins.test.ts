/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { layerBundlingCommand } from "../../lib/helper/lambda";

/**
 * Every tool the Lambda layer bundling command installs is pinned to an exact version.
 *
 * Each layer's dependencies are pinned by its committed `poetry.lock`, but the tools that read the
 * lock (pip, Poetry and the export plugin) are installed by the bundling command itself. An unpinned
 * install takes whatever PyPI serves at build time, so two builds of the same commit can export
 * different requirements and a just-published release is used before it has aged. The dependency
 * installs (`pip install -r requirements.txt`) are pinned by the exported file and are not tool
 * installs.
 */

function installSteps(): string[] {
    return layerBundlingCommand()
        .split(" && ")
        .map((step) => step.trim())
        .filter((step) => step.startsWith("pip install") && !step.includes(" -r "));
}

describe("layer bundling tool pins", () => {
    test("the command installs the expected build tools", () => {
        const packages = installSteps().map((step) =>
            step
                .split(/\s+/)
                .filter((token) => token !== "pip" && token !== "install" && !token.startsWith("-"))
                .map((token) => token.split("==")[0])
        );
        expect(packages.flat().sort()).toEqual(["pip", "poetry", "poetry-plugin-export"]);
    });

    test("every build-tool install names an exact version", () => {
        const unpinned = installSteps().filter((step) =>
            step
                .split(/\s+/)
                .filter((token) => token !== "pip" && token !== "install" && !token.startsWith("-"))
                .some((token) => !/^[A-Za-z0-9._-]+==[0-9][A-Za-z0-9.]*$/.test(token))
        );
        expect(unpinned).toEqual([]);
    });

    test("poetry is the version that wrote the layer lock files", () => {
        expect(installSteps()).toContain("pip install poetry==2.1.4");
    });
});
