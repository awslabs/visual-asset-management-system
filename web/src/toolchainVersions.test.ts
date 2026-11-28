/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Jest runner, the transformer `jest.config.js` names (`babel-jest`) and the jsdom environment
 * are declared in web/package.json on one major version, so a preset update that brings its own
 * runner cannot move the major under the suite.
 */

import fs from "fs";
import path from "path";

const PACKAGE_JSON = path.join(__dirname, "..", "package.json");

const major = (range: string | undefined): number | undefined => {
    const match = /(\d+)\./.exec(range ?? "");
    return match ? Number(match[1]) : undefined;
};

describe("web Jest toolchain", () => {
    const devDependencies: Record<string, string> = JSON.parse(
        fs.readFileSync(PACKAGE_JSON, "utf8")
    ).devDependencies;

    it("declares jest, babel-jest and jest-environment-jsdom directly", () => {
        expect(devDependencies.jest).toBeDefined();
        expect(devDependencies["babel-jest"]).toBeDefined();
        expect(devDependencies["jest-environment-jsdom"]).toBeDefined();
    });

    it("keeps the runner, transformer and environment on the same major", () => {
        const runner = major(devDependencies.jest);
        expect(runner).toBeDefined();
        expect(major(devDependencies["babel-jest"])).toBe(runner);
        expect(major(devDependencies["jest-environment-jsdom"])).toBe(runner);
        expect(major(devDependencies["@jest/types"])).toBe(runner);
    });
});
