/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The committed contents of a tracked file, read from git rather than from the working tree.
 *
 * `infra/config/config.json` is tracked but is also the file an operator edits for a real deployment,
 * so a test about the SHIPPED value must not read the working copy: an operator's legitimate edit
 * (an existing user pool keeping a setting, Cognito off for an external IdP) would fail their own
 * `npm test`. `HEAD` is the commit under test in CI and the last commit locally.
 */

import { execFileSync } from "child_process";
import * as path from "path";

/** The VAMS root, two levels above infra/test/support. */
const VAMS_ROOT = path.resolve(__dirname, "../../..");

export interface CommittedFile {
    /** The file's contents at HEAD; undefined when it could not be read. */
    content?: string;
    /** Why the contents could not be read, for a skip message. */
    skipReason?: string;
}

/**
 * `git show HEAD:./<vamsRelativePath>` run from the VAMS root. The `./` makes the path relative to that
 * directory rather than to the repository root, so it also resolves when VAMS is vendored into a
 * subdirectory of a larger repository. Reports a skip reason, rather than throwing, when git or the
 * repository is unavailable (a source archive with no `.git`) or the file is not in HEAD.
 */
export function readCommittedFile(vamsRelativePath: string, vamsRoot = VAMS_ROOT): CommittedFile {
    try {
        execFileSync("git", ["rev-parse", "--verify", "HEAD"], {
            cwd: vamsRoot,
            stdio: ["ignore", "pipe", "ignore"],
        });
    } catch {
        return {
            skipReason:
                "git (or the repository's .git) is unavailable, so the committed file cannot be read; skipped.",
        };
    }
    try {
        const content = execFileSync("git", ["show", `HEAD:./${vamsRelativePath}`], {
            cwd: vamsRoot,
            encoding: "utf8",
            stdio: ["ignore", "pipe", "pipe"],
            maxBuffer: 16 * 1024 * 1024,
        });
        return { content };
    } catch {
        return {
            skipReason: `${vamsRelativePath} is not in the HEAD commit of the repository at ${vamsRoot}, so the committed file cannot be read; skipped.`,
        };
    }
}
