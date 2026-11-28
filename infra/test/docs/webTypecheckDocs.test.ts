/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `npm run build` in web/ is `vite build`, which transpiles TypeScript without type-checking it.
 * The type check is `npm run typecheck` (`tsc --noEmit`). The web steering checklists and the
 * /deploy-check skill name the type check, not the build, as the way to find TypeScript errors.
 */

import { capture, read } from "./support/docsText";

const CHECKLISTS = [
    "web/CLAUDE.md",
    ".kiro/steering/WEB_DEVELOPMENT_WORKFLOW.md",
    ".kiro/steering/WEB_FRONTEND.md",
    ".claude/commands/deploy-check.md",
];

describe("web type check is documented as npm run typecheck", () => {
    test("web/package.json defines typecheck as tsc --noEmit", () => {
        const scripts = JSON.parse(read("web/package.json")).scripts;
        expect(scripts.build).toBe("vite build");
        expect(scripts.typecheck).toBe("tsc --noEmit");
    });

    // TEMPORARY-TEST: pins the rewording of the four checklists that named npm run build as the TypeScript check
    test.each(CHECKLISTS)(
        "%s names npm run typecheck wherever it pairs TypeScript errors with the build",
        (file) => {
            const offending = read(file)
                .split("\n")
                .filter(
                    (line) =>
                        /typescript/i.test(line) &&
                        /error/i.test(line) &&
                        line.includes("npm run build") &&
                        !line.includes("npm run typecheck")
                );
            expect(offending).toEqual([]);
            expect(read(file)).toContain("npm run typecheck");
        }
    );

    // TEMPORARY-TEST: pins the type check added to /deploy-check step 7
    test("deploy-check step 7 runs npm run typecheck after the build", () => {
        const step7 = capture(
            read(".claude/commands/deploy-check.md"),
            /#### 7\. Frontend Build\n([\s\S]*?)\n#### 8\./
        );
        const build = step7.indexOf("npm run build");
        const typecheck = step7.indexOf("npm run typecheck");
        expect(build).toBeGreaterThanOrEqual(0);
        expect(typecheck).toBeGreaterThan(build);
        expect(step7).not.toMatch(/^-\s+TypeScript type errors\s*$/m);
    });
});
