/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The OCCT (OpenCascade) loader's Embind glue compiles functions with `new Function`. Under the
 * default Content Security Policy, which has `'wasm-unsafe-eval'` but not `'unsafe-eval'`, the
 * browser refuses that and the load fails with its own CSP violation text. These tests pin that
 * the viewer names the deployment setting instead, and that every other failure is still wrapped.
 */

import { loadFile } from "./fileLoaders";
import { loadOCCTFile } from "./occtLoader";

jest.mock("./occtLoader", () => ({
    loadOCCTFile: jest.fn(),
}));

const mockedLoadOCCTFile = loadOCCTFile as jest.MockedFunction<typeof loadOCCTFile>;

/** Chromium's wording for a `new Function` call blocked by a script-src without 'unsafe-eval'. */
const CHROMIUM_CSP_TEXT =
    "Evaluating a string as JavaScript violates the following Content Security Policy directive " +
    "because 'unsafe-eval' is not an allowed source of script: \"script-src 'self' 'wasm-unsafe-eval'\".";

const SETTING = "app.webUi.allowUnsafeEvalFeatures";

/** loadFile's message for a failure, so a resolved load fails the test instead of passing it. */
async function failureMessage(): Promise<string> {
    try {
        await loadFile(new ArrayBuffer(8), "step", "part.step");
    } catch (error: any) {
        return error.message;
    }
    throw new Error("loadFile resolved; expected it to reject");
}

describe("loadFile OCCT error messages", () => {
    const originalCreateObjectURL = URL.createObjectURL;
    const originalRevokeObjectURL = URL.revokeObjectURL;

    beforeEach(() => {
        mockedLoadOCCTFile.mockReset();
        // jsdom implements neither, and loadFile hands the OCCT loader a blob URL.
        URL.createObjectURL = jest.fn(() => "blob:test");
        URL.revokeObjectURL = jest.fn();
        (window as any).THREEBundle = { THREE: {}, occtimportjs: jest.fn() };
    });

    afterEach(() => {
        URL.createObjectURL = originalCreateObjectURL;
        URL.revokeObjectURL = originalRevokeObjectURL;
        delete (window as any).THREEBundle;
    });

    it("names the allowUnsafeEvalFeatures setting when the loader throws an EvalError", async () => {
        mockedLoadOCCTFile.mockRejectedValue(new EvalError("call to Function() blocked by CSP"));

        const message = await failureMessage();

        expect(message).toContain("blocked by the Content Security Policy");
        expect(message).toContain(SETTING);
        expect(message).not.toContain("call to Function() blocked by CSP");
        expect(message).not.toContain("Failed to load CAD file");
    });

    it("names the setting instead of the browser's CSP violation text", async () => {
        mockedLoadOCCTFile.mockRejectedValue(new Error(CHROMIUM_CSP_TEXT));

        const message = await failureMessage();

        expect(message).toContain(SETTING);
        expect(message).not.toContain(CHROMIUM_CSP_TEXT);
        expect(message).not.toContain("script-src");
        expect(message).not.toContain("Failed to load CAD file");
    });

    it("leads with the CAD heading the viewer gives priority to across files", async () => {
        mockedLoadOCCTFile.mockRejectedValue(new EvalError("blocked"));

        // ThreeJSViewerComponent shows a CAD error first when every selected file failed, and
        // recognizes one by this heading (shared with the SharedArrayBuffer message).
        expect(await failureMessage()).toMatch(/^CAD Format Support Not Available\./);
    });

    it("keeps wrapping every other loader failure", async () => {
        mockedLoadOCCTFile.mockRejectedValue(new Error("Unsupported OCCT format: xyz"));

        const message = await failureMessage();

        expect(message).toBe("Failed to load CAD file: Unsupported OCCT format: xyz");
        expect(message).not.toContain(SETTING);
    });

    it("revokes the blob URL when the loader fails", async () => {
        mockedLoadOCCTFile.mockRejectedValue(new EvalError("blocked"));

        await failureMessage();

        expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test");
    });
});
