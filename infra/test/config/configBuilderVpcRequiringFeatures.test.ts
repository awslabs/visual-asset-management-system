/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The ConfigBuilder refuses a VPC-requiring feature without a VPC for every core feature getConfig()
 * refuses. getConfig() pushes each enabled feature's label onto `vpcRequiringFeatures` and throws when
 * app.useGlobalVpc.enabled is false; validation.ts keeps one `VPC_REQUIRING_FEATURES` entry per label
 * (the add-on lane keeps its own lists in both places). The labels are read from both sources, so a
 * feature added to one list and not the other fails here.
 */

import * as fs from "fs";
import * as path from "path";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";

const CONFIG_TS = path.join(__dirname, "..", "..", "config", "config.ts");
const VALIDATION_TS = path.join(
    __dirname,
    "..",
    "..",
    "..",
    "documentation",
    "docusaurus-site",
    "src",
    "components",
    "ConfigBuilder",
    "validation.ts"
);

function getConfigLabels(): string[] {
    const source = fs.readFileSync(CONFIG_TS, "utf-8");
    return [...source.matchAll(/vpcRequiringFeatures\.push\(\s*"([^"]+)"\s*\)/g)].map((m) => m[1]);
}

function builderLabels(): string[] {
    const source = fs.readFileSync(VALIDATION_TS, "utf-8");
    const start = source.indexOf("const VPC_REQUIRING_FEATURES");
    const end = source.indexOf("...ADDON_VPC_REQUIRING_FEATURES", start);
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
    return [...source.slice(start, end).matchAll(/label:\s*"([^"]+)"/g)].map((m) => m[1]);
}

describe("ConfigBuilder VPC-requiring features match getConfig()", () => {
    test("both list the same core features", () => {
        const fromConfig = getConfigLabels();
        expect(fromConfig.length).toBeGreaterThan(0);
        expect([...builderLabels()].sort()).toEqual([...fromConfig].sort());
    });

    test("enabling Image to 3D without a VPC is refused under the label getConfig() uses", () => {
        const config = makeDefaultConfig("commercial");
        config.app.useGlobalVpc.enabled = false;
        config.app.pipelines.useImageTo3d.enabled = true;
        const rule = evaluateRules(config).find((r) => r.id === "vpc-required-image-to-3d");
        expect(rule).toBeDefined();
        expect(rule?.severity).toBe("error");
        expect(rule?.message).toContain("pipelines.useImageTo3d");
    });

    test("with a VPC, Image to 3D raises no VPC rule", () => {
        const config = makeDefaultConfig("commercial");
        config.app.useGlobalVpc.enabled = true;
        config.app.pipelines.useImageTo3d.enabled = true;
        expect(evaluateRules(config).map((r) => r.id)).not.toContain("vpc-required-image-to-3d");
    });
});
