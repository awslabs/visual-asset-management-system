/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { storedNoFile } from "./uploadOutcome";

const row = (index: number, status: string) => ({
    index,
    relativePath: `/file-${index}`,
    status,
});

describe("storedNoFile", () => {
    it("is true when every file failed", () => {
        expect(storedNoFile([row(0, "Failed"), row(1, "Failed")], new Set(), new Set())).toBe(true);
    });

    it("is true for a file the server refused whose row still reads Completed", () => {
        expect(storedNoFile([row(0, "Completed")], new Set(), new Set(["/file-0"]))).toBe(true);
    });

    it("is false when one file failed and another was stored", () => {
        expect(storedNoFile([row(0, "Failed"), row(1, "Completed")], new Set(), new Set())).toBe(
            false
        );
    });

    it("is false when every file was cancelled", () => {
        expect(
            storedNoFile([row(0, "Cancelled"), row(1, "Cancelled")], new Set([0, 1]), new Set())
        ).toBe(false);
    });

    it("is true when the files that were not cancelled all failed", () => {
        expect(storedNoFile([row(0, "Cancelled"), row(1, "Failed")], new Set([0]), new Set())).toBe(
            true
        );
    });

    it("counts a cancelled file as cancelled after a skipped sequence marked it Failed", () => {
        // Skipping a completion sequence marks every file of it Failed, cancelled ones included.
        expect(storedNoFile([row(0, "Failed"), row(1, "Completed")], new Set([0]), new Set())).toBe(
            false
        );
        expect(storedNoFile([row(0, "Failed")], new Set([0]), new Set())).toBe(false);
    });

    it("ignores the server's refusal of a cancelled file", () => {
        // A cancelled file is sent with no parts and the server answers that it stored nothing.
        expect(
            storedNoFile(
                [row(0, "Cancelled"), row(1, "Completed")],
                new Set([0]),
                new Set(["/file-0"])
            )
        ).toBe(false);
    });

    it("is false for an upload with no files", () => {
        expect(storedNoFile([], new Set(), new Set())).toBe(false);
    });
});
