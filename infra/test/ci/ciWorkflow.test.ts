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
});
