/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * @jest-environment node
 */

/**
 * The Potree install (web/customInstalls/potree/potreeInstall.js, run by `postinstall`) clones
 * potree/potree at a pinned commit, applies the VAMS fork patch, and builds it. An unpatched Potree
 * sends no VAMS auth header on its point-cloud requests and never rejects a failed load, so every
 * clone, checkout, patch, install or build error must fail the install rather than ship that build.
 * The cloned source sits outside web/, so web/.npmrc does not reach its npm install: the
 * release-age floor is passed on the command line.
 */

import fs from "fs";
import os from "os";
import path from "path";

const mockClone = jest.fn();
const mockCheckout = jest.fn();
const mockApplyPatch = jest.fn();
jest.mock("simple-git", () => ({
    simpleGit: () => ({
        clone: mockClone,
        checkout: mockCheckout,
        applyPatch: mockApplyPatch,
    }),
}));

const mockExecSync = jest.fn();
jest.mock("child_process", () => ({
    execSync: (...args: unknown[]) => mockExecSync(...args),
}));

const install = jest.requireActual("../../../../customInstalls/potree/potreeInstall");

const POTREE_DIR = path.resolve(__dirname, "../../../../customInstalls/potree");

describe("potreeInstall", () => {
    let errorSpy: jest.SpyInstance;
    let logSpy: jest.SpyInstance;

    beforeEach(() => {
        jest.clearAllMocks();
        mockClone.mockResolvedValue(undefined);
        mockCheckout.mockResolvedValue(undefined);
        mockApplyPatch.mockResolvedValue(undefined);
        errorSpy = jest.spyOn(console, "error").mockImplementation(() => undefined);
        logSpy = jest.spyOn(console, "log").mockImplementation(() => undefined);
    });

    afterEach(() => {
        errorSpy.mockRestore();
        logSpy.mockRestore();
    });

    it("ships the fork patch named for the pinned commit, and no other patch", () => {
        const patches = fs.readdirSync(POTREE_DIR).filter((f) => f.endsWith(".patch"));
        expect(patches).toEqual([`Potree-Fork_${install.gitRepoCommitHash}.patch`]);
        expect(install.patchFilePath).toBe(`./../Potree-Fork_${install.gitRepoCommitHash}.patch`);
    });

    it("applies the patch after checking out the pinned commit", async () => {
        await install.gitActions();
        expect(mockCheckout).toHaveBeenCalledWith(install.gitRepoCommitHash);
        expect(mockApplyPatch).toHaveBeenCalledWith(install.patchFilePath, [
            "--ignore-whitespace",
            "--whitespace=fix",
        ]);
    });

    it.each([
        ["clone", mockClone],
        ["checkout", mockCheckout],
        ["patch apply", mockApplyPatch],
    ])("fails when the git %s step fails", async (_step, mock) => {
        mock.mockRejectedValueOnce(new Error("git failed"));
        await expect(install.gitActions()).rejects.toThrow("git failed");
    });

    it("installs with the 7-day release-age floor", () => {
        install.npmBuild();
        expect(mockExecSync.mock.calls[0][0]).toBe("npm install --min-release-age=7");
        expect(mockExecSync.mock.calls[1][0]).toBe("npm run build");
    });

    it("fails when npm install fails, without running the build", () => {
        mockExecSync.mockImplementationOnce(() => {
            throw new Error("install failed");
        });
        expect(() => install.npmBuild()).toThrow("install failed");
        expect(mockExecSync).toHaveBeenCalledTimes(1);
    });

    it("attributes every library Potree vendors in a NOTICE.md row with its version and licence", () => {
        const notice = fs.readFileSync(path.resolve(POTREE_DIR, "../../../NOTICE.md"), "utf8");
        for (const lib of install.bundledLibraries) {
            const row = notice
                .split("\n")
                .find((line: string) => line.startsWith(`| ${lib.name} `));
            expect(row).toBeDefined();
            expect(row).toContain(`[${lib.version}](${lib.repository})`);
            expect(row).toContain(`| ${lib.license} `);
        }
    });

    it("refuses vendored files that are missing or changed", () => {
        const dir = fs.mkdtempSync(path.join(os.tmpdir(), "potree-vendored-"));
        try {
            expect(() => install.verifyBundledLibraries(dir)).toThrow(/vendored libraries changed/);
            // A file with the recorded hash cannot be fabricated, so check the refusal names the
            // changed file and leave acceptance to the real build (the install runs this check).
            const file = Object.keys(install.bundledLibraryFiles)[0];
            fs.mkdirSync(path.join(dir, path.dirname(file)), { recursive: true });
            fs.writeFileSync(path.join(dir, file), "changed");
            expect(() => install.verifyBundledLibraries(dir)).toThrow(file);
        } finally {
            fs.rmSync(dir, { recursive: true, force: true });
        }
    });

    it("fails when npm run build fails", () => {
        mockExecSync.mockImplementationOnce(() => undefined);
        mockExecSync.mockImplementationOnce(() => {
            throw new Error("build failed");
        });
        expect(() => install.npmBuild()).toThrow("build failed");
    });
});
