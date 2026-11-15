/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `readCommittedFile` reads a path relative to the VAMS root, which is not always the repository root:
 * a package that vendors VAMS under a subdirectory, or a monorepo fork, has the `.git` higher up.
 */

import { execFileSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { readCommittedFile } from "./committedFile";

const FILE = "infra/config/config.json";

const git = (cwd: string, ...args: string[]) =>
    execFileSync(
        "git",
        [
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            ...args,
        ],
        { cwd, stdio: ["ignore", "pipe", "pipe"] }
    );

let tmp: string;

beforeEach(() => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), "vams-committed-file-"));
});

afterEach(() => {
    fs.rmSync(tmp, { recursive: true, force: true });
});

/** A repository at `repoDir` with VAMS under `vamsSubdir`, the file committed and then edited. */
function makeRepo(vamsSubdir: string): string {
    const vamsRoot = path.join(tmp, vamsSubdir);
    fs.mkdirSync(path.join(vamsRoot, "infra/config"), { recursive: true });
    fs.writeFileSync(path.join(vamsRoot, FILE), '{"committed": true}');
    git(tmp, "init", "-q");
    git(tmp, "add", ".");
    git(tmp, "commit", "-q", "-m", "init");
    fs.writeFileSync(path.join(vamsRoot, FILE), '{"committed": false}');
    return vamsRoot;
}

describe("readCommittedFile", () => {
    test("reads the committed blob, not the working copy, at the repository root", () => {
        const r = readCommittedFile(FILE, makeRepo("."));
        expect(r.skipReason).toBeUndefined();
        expect(JSON.parse(r.content as string)).toEqual({ committed: true });
    });

    test("reads the committed blob when VAMS is a subdirectory of the repository", () => {
        const r = readCommittedFile(FILE, makeRepo("visual-asset-management-system"));
        expect(r.skipReason).toBeUndefined();
        expect(JSON.parse(r.content as string)).toEqual({ committed: true });
    });

    test("a path missing from HEAD skips with a message instead of throwing", () => {
        const r = readCommittedFile("infra/config/absent.json", makeRepo("vendor/vams"));
        expect(r.content).toBeUndefined();
        expect(r.skipReason).toContain("infra/config/absent.json is not in the HEAD commit");
    });

    test("a directory outside any repository skips with a message", () => {
        const r = readCommittedFile(FILE, tmp);
        expect(r.content).toBeUndefined();
        expect(r.skipReason).toContain("git");
    });
});
