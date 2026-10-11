/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `getConfig()` refuses a deployment with no stack name.
 *
 * `app.baseStackName` is suffixed with the Region and becomes part of every stack name
 * (`vams-core-<baseStackName>-<region>`), the SSM parameter prefix and the resource-name registry. With
 * the field null, no `stack-name` context and no `STACK_NAME` variable, the concatenation used to
 * produce the literal `undefined-<region>` and the deployment went ahead under that name. `bin/infra.ts`
 * carried `|| "dev"` fallbacks that could never fire because the composed value is always truthy.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
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
    mutate(config);
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(config);
            return realReadFileSync(p, ...rest);
        }
    );
    return () => Config.getConfig(newTestApp());
}

describe("getConfig() requires a stack name", () => {
    const savedStackName = process.env.STACK_NAME;

    beforeEach(() => {
        delete process.env.STACK_NAME;
    });

    afterEach(() => {
        if (savedStackName === undefined) delete process.env.STACK_NAME;
        else process.env.STACK_NAME = savedStackName;
        (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
    });

    test("a null app.baseStackName with no context and no STACK_NAME is rejected", () => {
        expect(resolve((c) => (c.app.baseStackName = null))).toThrow(
            /app\.baseStackName must be set/
        );
    });

    test("an empty app.baseStackName is rejected", () => {
        expect(resolve((c) => (c.app.baseStackName = ""))).toThrow(
            /app\.baseStackName must be set/
        );
    });

    test("the STACK_NAME variable satisfies the rule and is suffixed with the Region", () => {
        process.env.STACK_NAME = "fromenv";
        const config = resolve((c) => (c.app.baseStackName = null))();
        expect(config.app.baseStackName).toBe("fromenv-us-east-1");
    });

    test("a configured app.baseStackName is suffixed with the Region", () => {
        const config = resolve((c) => (c.app.baseStackName = "vamstest"))();
        expect(config.app.baseStackName).toBe("vamstest-us-east-1");
    });
});
