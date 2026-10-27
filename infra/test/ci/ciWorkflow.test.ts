/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Properties of `.github/workflows/ci.yml` that no build step checks: the workflow is only run by
 * GitHub, so an edit that drops one of them passes every local gate.
 *
 * Steps are found by their content (the command a `run:` line calls), never by job, step name or
 * line, so the checks hold when steps move between jobs.
 */

import { read } from "../docs/support/docsText";

const CI = ".github/workflows/ci.yml";

/** Every `run:` command in the workflow, single-line and block form, one entry per command line. */
function runCommands(text: string): string[] {
    const lines = text.split("\n");
    const commands: string[] = [];
    for (let i = 0; i < lines.length; i++) {
        const m = /^(\s*)(?:- )?run:\s*(.*)$/.exec(lines[i]);
        if (!m) {
            continue;
        }
        const inline = m[2].trim();
        if (inline !== "|" && inline !== ">" && inline !== "") {
            commands.push(inline);
            continue;
        }
        const indent = m[1].length;
        for (let j = i + 1; j < lines.length; j++) {
            const body = lines[j];
            if (body.trim() === "") {
                continue;
            }
            if (body.length - body.trimStart().length <= indent) {
                break;
            }
            commands.push(body.trim());
        }
    }
    return commands;
}

const jestCommands = (text: string) =>
    runCommands(text).filter((command) => /(^|\s)(npx\s+)?jest(\s|$)/.test(command));

describe("CI workflow", () => {
    const text = read(CI);

    test("the workflow runs the web Jest suite (control)", () => {
        expect(jestCommands(text).length).toBeGreaterThan(0);
    });

    test("every Jest run collects coverage, so the web coverage thresholds apply", () => {
        jestCommands(text).forEach((command) => {
            expect(command).toContain("--coverage");
        });
    });

    test("the web and backend suites run on pull requests as well as pushes", () => {
        expect(runCommands(text).some((command) => /^pytest\b/.test(command))).toBe(true);
        expect(text).not.toMatch(/if:\s*\$?\{?\{?\s*github\.event_name\s*==\s*['"]push['"]/);
    });

    test("push and pull_request both filter on main, release, development and development-*", () => {
        const filters = [...text.matchAll(/^\s+branches:\s*\[(.*)\]\s*$/gm)].map((m) =>
            m[1].split(",").map((b) => b.trim().replace(/^"|"$/g, ""))
        );
        expect(filters.length).toBe(2);
        filters.forEach((branches) => {
            expect(branches).toEqual(
                expect.arrayContaining(["main", "release/**", "development", "development-*"])
            );
        });
    });

    test("a new pull request commit cancels that pull request's older run", () => {
        expect(text).toMatch(
            /^concurrency:\n\s+group: \$\{\{ github\.workflow \}\}-\$\{\{ github\.event\.pull_request\.number \|\| github\.run_id \}\}\n\s+cancel-in-progress: true$/m
        );
    });

    describe("automated security helper scan", () => {
        const ashCheckout = (): string =>
            /- name: Clone automated security helper\n([\s\S]*?)\n\s*\n/.exec(text)?.[1] ?? "";

        test("the scan is cloned at a pinned commit", () => {
            expect(ashCheckout()).toContain("repository: aws-samples/automated-security-helper");
            expect(ashCheckout()).toMatch(/^\s+ref: [0-9a-f]{40}$/m);
        });

        test("the scan's exit code is captured, not discarded", () => {
            const ashRuns = runCommands(text).filter((command) => /^ash\s/.test(command));
            expect(ashRuns.length).toBe(1);
            expect(ashRuns[0]).not.toMatch(/\|\|\s*true\s*$/);
            expect(ashRuns[0]).toContain("|| rc=$?");
        });

        test("results go to a non-hidden directory the report upload reads", () => {
            const outputDir = /ash .*--output-dir "\$GITHUB_WORKSPACE\/([^"]+)"/.exec(text)?.[1];
            expect(outputDir).toBeDefined();
            expect(outputDir?.startsWith(".")).toBe(false);
            expect(text).toMatch(new RegExp(`^\\s+path: ${outputDir}/?$`, "m"));
        });

        test("the summary step always ends with exit 0", () => {
            const summary =
                /- name: Summarize automated security helper results\n([\s\S]*?)\n\s*\n/.exec(
                    text
                )?.[1];
            expect(summary).toBeDefined();
            expect(summary).toContain("set +e");
            expect(summary).toContain("GITHUB_STEP_SUMMARY");
            expect(summary?.trimEnd().endsWith("exit 0")).toBe(true);
        });
    });
});
