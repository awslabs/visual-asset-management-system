/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The API origin verification header and secret keys are declared in CDK and in the backend
 * (issue #714), and must agree:
 *
 *   - `Config.API_ORIGIN_VERIFY_HEADER_NAME` is the header CloudFront adds and the regional web ACL
 *     matches; `ORIGIN_VERIFY_HEADER_NAME` in `common/auth/originVerify.py` is the header the REST
 *     authorizer reads and the rotation function rewrites. A mismatch leaves every CloudFront request
 *     resolved to the edge address and the rotation unable to find the origin headers.
 *   - The header is a `CORE_LOG_SENSITIVE_KEYS` entry of `common/constants.py`, whose merged
 *     `LOG_SENSITIVE_KEYS` `customLogging/logger.py` imports, so handler, authorizer and
 *     audit logs redact it.
 *   - `current` / `previous` are the JSON keys CDK generates and references and the backend reads.
 */

import * as fs from "fs";
import * as path from "path";
import * as Config from "../../config/config";

const BACKEND = path.join(__dirname, "..", "..", "..", "backend", "backend");
const read = (rel: string) => fs.readFileSync(path.join(BACKEND, rel), "utf8");

function pythonString(source: string, name: string): string {
    const match = source.match(new RegExp(`^${name}\\s*=\\s*"([^"]*)"`, "m"));
    expect(match).not.toBeNull();
    return (match as RegExpMatchArray)[1];
}

describe("API origin verification constants: CDK and backend agree", () => {
    const originVerify = read(path.join("common", "auth", "originVerify.py"));

    test("header name", () => {
        expect(pythonString(originVerify, "ORIGIN_VERIFY_HEADER_NAME")).toBe(
            Config.API_ORIGIN_VERIFY_HEADER_NAME
        );
    });

    test("secret JSON keys", () => {
        expect(pythonString(originVerify, "CURRENT_KEY")).toBe(
            Config.API_ORIGIN_VERIFY_CURRENT_KEY
        );
        expect(pythonString(originVerify, "PREVIOUS_KEY")).toBe(
            Config.API_ORIGIN_VERIFY_PREVIOUS_KEY
        );
    });

    test("the logger redacts the header", () => {
        // The core list lives in common/constants.py; customLogging/logger.py imports the merged
        // LOG_SENSITIVE_KEYS built from it.
        const constants = read(path.join("common", "constants.py"));
        const block = constants.match(/^CORE_LOG_SENSITIVE_KEYS = \(([\s\S]*?)\)\s*$/m);
        expect(block).not.toBeNull();
        const keys = [...(block as RegExpMatchArray)[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
        expect(keys).toContain("authorization"); // control: the block was parsed
        expect(keys).toContain(Config.API_ORIGIN_VERIFY_HEADER_NAME);
        const logger = read(path.join("customLogging", "logger.py"));
        expect(logger).toMatch(/^from common\.constants import LOG_SENSITIVE_KEYS as SENSITIVE_KEYS$/m);
    });
});
