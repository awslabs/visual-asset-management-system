/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The GitHub deployment line of documentation/CLAUDE.md and of its Kiro mirror names the actions
 * `.github/workflows/docs.yml` uses, so an agent editing the workflow is not steered toward an
 * action it does not run. Each line names at least one action, and every action a line names is in
 * a `uses:` line of the workflow.
 */

import { read } from "./support/docsText";

const WORKFLOW = ".github/workflows/docs.yml";
const STEERING = ["documentation/CLAUDE.md", ".kiro/steering/DOCUMENTATION_WORKFLOW.md"];

/** `owner/name` of every action the workflow runs. */
const workflowActions = (): Set<string> =>
    new Set(
        [...read(WORKFLOW).matchAll(/^\s*(?:- )?uses:\s*([\w.-]+\/[\w.-]+)@/gm)].map((m) => m[1])
    );

/** The steering file's GitHub deployment line. */
const deploymentLine = (file: string): string =>
    read(file)
        .split("\n")
        .find((line) => line.startsWith("-   **GitHub**:") && line.includes(WORKFLOW)) ?? "";

/** `owner/name` of every action named in backticks on a line (an `@ref` suffix is dropped). */
const namedActions = (line: string): string[] =>
    [...line.matchAll(/`([\w.-]+\/[\w.-]+?)(?:@[^`]*)?`/g)]
        .map((m) => m[1])
        .filter(
            (name) => !name.startsWith(".") && !name.includes("release") && name !== "gh-pages"
        );

describe("docs deployment steering names the workflow's actions", () => {
    test("the workflow runs actions (control)", () => {
        expect(workflowActions().size).toBeGreaterThan(0);
    });

    test.each(STEERING)("%s names at least one action, all of them in docs.yml", (file) => {
        const line = deploymentLine(file);
        expect(line).not.toBe("");
        const named = namedActions(line);
        expect(named.length).toBeGreaterThan(0);
        const actions = workflowActions();
        named.forEach((name) => expect(actions).toContain(name));
    });
});
