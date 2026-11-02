/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The VAMS version string is duplicated across surfaces that ship independently: the root
 * package.json (and its lockfile header), the CDK stack descriptions (VAMS_VERSION), the CLI's
 * `--version`, and the MCP server's package metadata and importable `__version__`. Nothing joins
 * them at build time, so a partial roll leaves one surface reporting the previous release. Each
 * file is read as text and compared to VAMS_VERSION; a mismatch names the file.
 */

import * as fs from "fs";
import * as path from "path";
import { VAMS_VERSION } from "../../config/config";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");

function read(relativePath: string): string {
    return fs.readFileSync(path.join(REPO_ROOT, relativePath), "utf8");
}

function capture(text: string, pattern: RegExp, label: string): string {
    const match = pattern.exec(text);
    if (!match) {
        throw new Error(`${label}: no line matches ${pattern}`);
    }
    return match[1];
}

describe("VAMS version roll", () => {
    it("VAMS_VERSION is a release version", () => {
        expect(VAMS_VERSION).toMatch(/^\d+\.\d+\.\d+$/);
    });

    it("root package.json and the lockfile header carry VAMS_VERSION", () => {
        const pkg = JSON.parse(read("package.json"));
        const lock = JSON.parse(read("package-lock.json"));
        expect(pkg.version).toBe(VAMS_VERSION);
        expect(lock.version).toBe(VAMS_VERSION);
        expect(lock.packages[""].version).toBe(VAMS_VERSION);
    });

    it("the CLI carries VAMS_VERSION in both of its constants", () => {
        const text = read("tools/VamsCLI/vamscli/version.py");
        expect(capture(text, /^__version__ = "([^"]+)"/m, "version.py __version__")).toBe(
            VAMS_VERSION
        );
        expect(capture(text, /^CLI_VERSION = "([^"]+)"/m, "version.py CLI_VERSION")).toBe(
            VAMS_VERSION
        );
    });

    it("the MCP server carries VAMS_VERSION in its package metadata and importable __version__", () => {
        expect(
            capture(read("tools/VamsMCP/pyproject.toml"), /^version = "([^"]+)"/m, "MCP pyproject")
        ).toBe(VAMS_VERSION);
        expect(
            capture(
                read("tools/VamsMCP/vams_mcp/__init__.py"),
                /^__version__ = "([^"]+)"/m,
                "MCP __init__"
            )
        ).toBe(VAMS_VERSION);
    });
});
