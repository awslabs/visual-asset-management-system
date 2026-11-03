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

/** The repository root, two levels above infra/test/support. */
const REPO_ROOT = path.resolve(__dirname, "../../..");

/**
 * `git show HEAD:<repoRelativePath>`, or undefined when git or the repository is unavailable (for
 * example a source archive with no `.git`). Throws when git works but the file is not committed.
 */
export function committedFile(repoRelativePath: string): string | undefined {
    try {
        execFileSync("git", ["rev-parse", "--verify", "HEAD"], {
            cwd: REPO_ROOT,
            stdio: ["ignore", "pipe", "ignore"],
        });
    } catch {
        return undefined;
    }
    return execFileSync("git", ["show", `HEAD:${repoRelativePath}`], {
        cwd: REPO_ROOT,
        encoding: "utf8",
        stdio: ["ignore", "pipe", "pipe"],
        maxBuffer: 16 * 1024 * 1024,
    });
}

/** Why a committed-file test was skipped, printed once so a skipped run is not read as a pass. */
export const NO_GIT_MESSAGE =
    "git (or the repository's .git) is unavailable, so the committed file cannot be read; skipped.";
