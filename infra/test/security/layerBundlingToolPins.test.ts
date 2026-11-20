/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { layerBundlingCommand } from "../../lib/helper/lambda";

/**
 * Every tool the Lambda layer bundling command installs is pinned to an exact version, and the command
 * never resolves dependencies.
 *
 * Each layer's dependencies are pinned by its committed `requirements.txt`, exported from the layer's
 * lock file. The one tool the command installs (pip) is installed by the command itself. An unpinned
 * install takes whatever PyPI serves at build time, so two builds of the same commit can differ and a
 * just-published release is used before it has aged. The dependency install
 * (`pip install -r requirements.txt`) is pinned by the exported file and is not a tool install. A
 * resolver (`poetry lock`, `uv lock`, a bare `pip install <package>`) must not run at deploy time:
 * whatever it resolved would not be what the lock names.
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
        expect(packages.flat().sort()).toEqual(["pip"]);
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

    test("pip is pinned to the reviewed version", () => {
        const pinned = installSteps()
            .flatMap((step) => step.split(/\s+/))
            .filter((token) => !token.startsWith("-"));
        expect(pinned).toContain("pip==26.2.1");
    });

    test("the command installs the layer's committed requirements.txt and runs no resolver", () => {
        const command = layerBundlingCommand();
        const steps = command.split(" && ").map((step) => step.trim());
        // Control: the one dependency install is the pinned export, into the layer's asset directory.
        expect(steps).toContain(
            "pip install --root-user-action=ignore -r requirements.txt -t /asset-output/python"
        );
        // Every pip install is either the pinned pip upgrade or that requirements install.
        const pipInstalls = steps.filter((step) => step.startsWith("pip install"));
        expect(pipInstalls).toHaveLength(2);
        // The guard's message names uv lock and uv export for the reader; it is the only step that may.
        const executed = steps.filter((step) => !step.startsWith("if [ ! -f requirements.txt ]"));
        for (const forbidden of [
            "poetry",
            "uv lock",
            "uv pip",
            "uv export",
            "uv sync",
            "pip-compile",
            "pip lock",
        ]) {
            expect(executed.join(" && ")).not.toContain(forbidden);
        }
        // A layer directory without a requirements.txt fails with a message instead of resolving.
        const guard = steps.find((step) => step.startsWith("if [ ! -f requirements.txt ]"));
        expect(guard).toBeDefined();
        expect(guard).toContain("exit 1");
        expect(steps.indexOf(guard as string)).toBeLessThan(
            steps.indexOf(
                "pip install --root-user-action=ignore -r requirements.txt -t /asset-output/python"
            )
        );
    });
});
