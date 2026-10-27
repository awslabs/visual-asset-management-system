/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Hunyuan3D-2 image-to-3D model carries a TERRITORIAL licence, and a wrong Region is not a deploy
 * failure.
 *
 * The Tencent Hunyuan 3D 2.0 Community License Agreement grants a royalty-free licence "for any purpose
 * and in any field of use" — so commercial use is permitted — but only inside a Territory it defines as
 * "the worldwide territory, excluding the territory of the European Union, United Kingdom and South
 * Korea", and it states that "any such use outside the Territory is unlicensed and unauthorized". Nothing
 * in the model, its weights or its container reports that: a deployment in Frankfurt or Seoul builds the
 * image, pulls the weights and produces meshes exactly as one in Oregon does. The only place the condition
 * can be enforced is the configuration, which is why `getConfig()` rejects it.
 *
 * This differs in kind from the peer models' licence problems, which are about USE (nvdiffrast's
 * research-or-evaluation limitation) rather than about geography. It resembles the per-partition Bedrock
 * model id instead — a value that is right in one partition and wrong in another — and is tested the same
 * way, with one difference: the predicate is exported from config.ts and exercised directly here, rather
 * than reimplemented in the test, so the two cannot drift.
 *
 * The prefix rule is the part worth pinning. European Regions are matched by `eu-` / `eusc-` prefix rather
 * than enumerated, so a Region launched after this was written is excluded by default instead of silently
 * permitted — the safe direction for a licence check. Zurich carries the same prefix and is in neither the
 * European Union nor the United Kingdom, so it is the named exception and a test that it stays permitted
 * is what stops the prefix rule being tightened into a wrong answer.
 */

import * as fs from "fs";
import * as path from "path";

import { isOutsideHunyuan3dLicenceTerritory } from "../config/config";

const CONFIG_DIR = path.join(__dirname, "..", "config");

function template(name: string): any {
    return JSON.parse(
        fs.readFileSync(path.join(CONFIG_DIR, `config.template.${name}.json`), "utf-8")
    );
}

function hunyuan3dEnabled(name: string): unknown {
    return template(name).app.pipelines.useImageTo3d.models.hunyuan3d?.enabled;
}

describe("Hunyuan3D-2 licence Territory", () => {
    it.each([
        "eu-west-1", // Ireland
        "eu-west-2", // London — the United Kingdom
        "eu-west-3", // Paris
        "eu-central-1", // Frankfurt
        "eu-north-1", // Stockholm
        "eu-south-1", // Milan
        "eu-south-2", // Spain
        "eusc-de-east-1", // EU Sovereign Cloud (Germany)
        "ap-northeast-2", // Seoul — South Korea
    ])("excludes %s", (region) => {
        expect(isOutsideHunyuan3dLicenceTerritory(region)).toBe(true);
    });

    it.each([
        "us-west-2",
        "us-east-1",
        "ap-northeast-1", // Tokyo, not Seoul
        "ap-southeast-2",
        "ca-central-1",
        "sa-east-1",
        "me-central-1",
        "us-gov-west-1",
    ])("permits %s", (region) => {
        expect(isOutsideHunyuan3dLicenceTerritory(region)).toBe(false);
    });

    it("permits eu-central-2, which is Zurich and in neither the EU nor the UK", () => {
        // Named exception to the eu- prefix rule. If this ever flips, the prefix rule has been tightened
        // into a wrong answer rather than a conservative one.
        expect(isOutsideHunyuan3dLicenceTerritory("eu-central-2")).toBe(false);
    });

    it("excludes a European Region that does not exist yet", () => {
        // The prefix rule's whole purpose: a Region launched after this was written must be excluded by
        // default rather than silently permitted, because the safe direction for a licence check is to
        // refuse and make the operator decide.
        expect(isOutsideHunyuan3dLicenceTerritory("eu-north-9")).toBe(true);
    });

    it("treats an unset Region as permitted, leaving the decision to deploy time", () => {
        // A blank Region resolves from CDK context or the environment at deploy time, so nothing here can
        // know it. Failing on unknown would reject every context-driven deployment.
        expect(isOutsideHunyuan3dLicenceTerritory(undefined)).toBe(false);
        expect(isOutsideHunyuan3dLicenceTerritory("")).toBe(false);
    });
});

describe("Hunyuan3D-2 in the shipped configuration templates", () => {
    it.each(["commercial", "govcloud", "eusovereign"])(
        "ships disabled in the %s template",
        (name) => {
            expect(hunyuan3dEnabled(name)).toBe(false);
        }
    );

    it("is never enabled in the EU Sovereign template, whose Region has no licence", () => {
        const region = template("eusovereign").env.region;
        if (region && isOutsideHunyuan3dLicenceTerritory(region)) {
            expect(hunyuan3dEnabled("eusovereign")).toBe(false);
        }
    });

    it("declares the model in every template rather than leaving it to the getConfig default", () => {
        // A template missing the key falls back to the getConfig() default and drops any operator-set
        // value, which is the failure mode recorded for config templates generally.
        for (const name of ["commercial", "govcloud", "eusovereign"]) {
            expect(hunyuan3dEnabled(name)).not.toBeUndefined();
        }
    });
});
