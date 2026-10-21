/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * CDK context `configFile` selects the configuration file `getConfig()` reads.
 *
 * Omitting it reads `infra/config/config.json`. A file name or relative path resolves in
 * `infra/config/`, an absolute path is used as given, and an unreadable or malformed file fails with a
 * message that names the path tried.
 */

import * as fs from "fs";
import { join } from "path";
import * as Config from "../../config/config";
import { DEFAULT_CONFIG_FILE_NAME, resolveConfigFilePath } from "../../config/configFile";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const CONFIG_DIR = join(__dirname, "..", "..", "config");
const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

function configWithStackName(baseStackName: string): string {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-east-1";
    config.env.account = "123456789012";
    config.app.baseStackName = baseStackName;
    return JSON.stringify(config);
}

/** Serve `files` (absolute path -> contents) and delegate every other read to the real fs. */
function serve(files: Record<string, string>): string[] {
    const reads: string[] = [];
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p in files) {
                reads.push(p);
                return files[p];
            }
            return realReadFileSync(p, ...rest);
        }
    );
    return reads;
}

describe("resolveConfigFilePath()", () => {
    test.each([undefined, null, "", "   "])("%p selects config.json in infra/config", (value) => {
        expect(resolveConfigFilePath(value)).toBe(join(CONFIG_DIR, DEFAULT_CONFIG_FILE_NAME));
    });

    test("a file name resolves in infra/config", () => {
        expect(resolveConfigFilePath("config.staging.json")).toBe(
            join(CONFIG_DIR, "config.staging.json")
        );
    });

    test("a relative path resolves against infra/config", () => {
        expect(resolveConfigFilePath("envs/dev.json")).toBe(join(CONFIG_DIR, "envs", "dev.json"));
    });

    test("an absolute path is used as given", () => {
        const absolute = join(CONFIG_DIR, "..", "..", "deployments", "prod.json");
        expect(resolveConfigFilePath(absolute)).toBe(absolute);
    });

    test("a non-string value is rejected", () => {
        expect(() => resolveConfigFilePath(42)).toThrow(/`configFile` must be a file name or path/);
    });
});

describe("getConfig() reads the file named by CDK context configFile", () => {
    const defaultPath = join(CONFIG_DIR, DEFAULT_CONFIG_FILE_NAME);
    const stagingPath = join(CONFIG_DIR, "config.staging.json");
    const savedStackName = process.env.STACK_NAME;

    beforeEach(() => {
        delete process.env.STACK_NAME;
    });

    afterEach(() => {
        if (savedStackName === undefined) delete process.env.STACK_NAME;
        else process.env.STACK_NAME = savedStackName;
        (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
    });

    test("without the context it reads config.json", () => {
        const reads = serve({
            [defaultPath]: configWithStackName("fromdefault"),
            [stagingPath]: configWithStackName("fromstaging"),
        });
        const config = Config.getConfig(newTestApp());
        expect(config.app.baseStackName).toBe("fromdefault-us-east-1");
        expect(reads).toEqual([defaultPath]);
    });

    test("with the context it reads the named file and not config.json", () => {
        const reads = serve({
            [defaultPath]: configWithStackName("fromdefault"),
            [stagingPath]: configWithStackName("fromstaging"),
        });
        const config = Config.getConfig(
            newTestApp({ context: { configFile: "config.staging.json" } })
        );
        expect(config.app.baseStackName).toBe("fromstaging-us-east-1");
        expect(reads).toEqual([stagingPath]);
    });

    test("a file that cannot be read fails naming the path tried", () => {
        serve({});
        const missing = join(CONFIG_DIR, "config.does-not-exist.json");
        expect(() =>
            Config.getConfig(newTestApp({ context: { configFile: "config.does-not-exist.json" } }))
        ).toThrow(`cannot read the configuration file ${missing}`);
    });

    test("a file that is not JSON fails naming the path tried", () => {
        serve({ [stagingPath]: "{ not json" });
        expect(() =>
            Config.getConfig(newTestApp({ context: { configFile: "config.staging.json" } }))
        ).toThrow(`the configuration file ${stagingPath} is not valid JSON`);
    });
});
