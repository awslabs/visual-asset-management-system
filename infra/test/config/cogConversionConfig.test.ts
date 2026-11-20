/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `app.pipelines.useCogConversion`: opt-in, VPC-gated, filled with defaults when absent, and mirrored in
 * the ConfigBuilder (the rule that names it as VPC-requiring).
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { newTestApp } from "../support/testApp";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

function resolve(mutate: (c: any) => void): () => Config.Config {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-east-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    mutate(config);
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(config);
            return realReadFileSync(p, ...rest);
        }
    );
    return () => Config.getConfig(newTestApp());
}

afterEach(() => {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
});

describe("app.pipelines.useCogConversion", () => {
    test("is off by default with its upload trigger off", () => {
        const config = resolve((_c) => undefined)();
        expect(config.app.pipelines.useCogConversion.enabled).toBe(false);
        expect(config.app.pipelines.useCogConversion.autoRegisterAutoTriggerOnFileUpload).toBe(
            false
        );
    });

    test("a config that omits the block gets the defaults (the JSON templates register it)", () => {
        const config = resolve((c) => {
            delete c.app.pipelines.useCogConversion;
        })();
        expect(config.app.pipelines.useCogConversion).toEqual({
            enabled: false,
            useCodeBuild: false,
            autoRegisterWithVAMS: false,
            autoRegisterAutoTriggerOnFileUpload: false,
        });
    });

    test("enabling it without the global VPC is refused, naming the key", () => {
        expect(
            resolve((c) => {
                c.app.useGlobalVpc.enabled = false;
                c.app.pipelines.useCogConversion.enabled = true;
            })
        ).toThrow(/useGlobalVpc\.enabled must be true.*pipelines\.useCogConversion/s);
    });

    test("enabling it with the global VPC is accepted", () => {
        expect(
            resolve((c) => {
                c.app.useGlobalVpc.enabled = true;
                c.app.pipelines.useCogConversion.enabled = true;
            })
        ).not.toThrow();
    });

    test("the ConfigBuilder flags it as VPC-requiring, as getConfig does", () => {
        const cfg: any = makeDefaultConfig("commercial");
        cfg.app.useGlobalVpc.enabled = false;
        cfg.app.pipelines.useCogConversion.enabled = true;
        const results = evaluateRules(cfg);
        expect(JSON.stringify(results)).toContain("pipelines.useCogConversion");
    });
});
