/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `app.largeFileIngestion` is no longer configuration: the large object copy engine's sizes,
 * batching and wait are fixed constants in backend/backend/common/largeObjectCopy.py (#1123).
 *
 * A deployment config written for an earlier 2.7.0 build may still carry the block. getConfig()
 * ignores it, out-of-range values included, and the templates no longer ship it.
 */

import * as fs from "fs";
import { join } from "path";
import * as Config from "../../config/config";
import { DEFAULT_CONFIG_FILE_NAME } from "../../config/configFile";
import commercialTemplate from "../../config/config.template.commercial.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import defaultConfig from "../../config/config.json";
import { newTestApp } from "../support/testApp";

const CONFIG_DIR = join(__dirname, "..", "..", "config");
const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

function serveDefault(contents: string): void {
    const defaultPath = join(CONFIG_DIR, DEFAULT_CONFIG_FILE_NAME);
    (fs.readFileSync as unknown as jest.Mock).mockImplementation((p: string, ...rest: unknown[]) =>
        p === defaultPath ? contents : realReadFileSync(p, ...rest)
    );
}

describe("app.largeFileIngestion is not configuration", () => {
    const savedStackName = process.env.STACK_NAME;

    beforeEach(() => {
        delete process.env.STACK_NAME;
    });

    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
        if (savedStackName === undefined) delete process.env.STACK_NAME;
        else process.env.STACK_NAME = savedStackName;
    });

    test.each([
        ["config.json", defaultConfig],
        ["config.template.commercial.json", commercialTemplate],
        ["config.template.govcloud.json", govcloudTemplate],
        ["config.template.eusovereign.json", eusovereignTemplate],
    ])("%s does not carry the block", (_name, file) => {
        expect((file as any).app.largeFileIngestion).toBeUndefined();
    });

    test("a config that still carries the block (even out of range) loads, and the block is unused", () => {
        const config = JSON.parse(JSON.stringify(commercialTemplate));
        config.env.region = "us-east-1";
        config.env.account = "123456789012";
        config.app.baseStackName = "legacyblock";
        config.app.largeFileIngestion = {
            apiUploadEngineThresholdBytes: 1,
            minPartSizeBytes: 1,
            partsPerBatch: 999,
            threadsPerBatch: 999,
            maxConcurrentBatches: 999,
            engineReservedConcurrency: -1,
            maxOutputIngestionWaitHours: 9999,
        };
        serveDefault(JSON.stringify(config));
        expect(() => Config.getConfig(newTestApp())).not.toThrow();
    });
});
