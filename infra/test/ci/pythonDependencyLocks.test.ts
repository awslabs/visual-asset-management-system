/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every Python dependency set in the repository is audited by the `python-dependency-audit` CI job,
 * or is listed as an exemption with a reason.
 *
 * The job audits each directory that has a tracked uv.lock, found with `git ls-files`. A new Lambda
 * layer, pipeline, container or package that adds a pyproject.toml or requirements file without a
 * uv.lock would not be audited, so this test fails until the author adds the lock (and the job then
 * covers it with no workflow edit) or adds a reasoned entry to `.github/python-lock-exemptions.json`.
 *
 * It also checks the audit allowlist `.github/python-audit-allowlist.json`: each entry needs a
 * reason and a review date that has not passed. The job itself fails an entry that matches no
 * current finding, because only a live audit knows the findings.
 */

import { execFileSync } from "child_process";
import * as path from "path";
import { REPO_ROOT, read } from "../docs/support/docsText";

const EXEMPTIONS = ".github/python-lock-exemptions.json";
const ALLOWLIST = ".github/python-audit-allowlist.json";

interface Exemption {
    path: string;
    reason: string;
    plannedIn: string;
}

interface AllowlistEntry {
    id: string;
    package: string;
    reason: string;
    reviewBy: string;
}

/** Tracked files, relative to the VAMS root; undefined when git is unavailable (a source archive). */
function trackedFiles(): string[] | undefined {
    try {
        return execFileSync("git", ["ls-files", "-z"], {
            cwd: REPO_ROOT,
            encoding: "utf8",
            maxBuffer: 64 * 1024 * 1024,
            stdio: ["ignore", "pipe", "ignore"],
        })
            .split("\0")
            .filter((p) => p !== "");
    } catch {
        return undefined;
    }
}

const isRequirementsFile = (file: string): boolean =>
    /^requirements[^/]*\.txt$/.test(path.posix.basename(file));

/** A requirements file with at least one line that is not blank or a comment. */
export const hasRequirementLines = (text: string): boolean =>
    text.split("\n").some((line) => line.trim() !== "" && !line.trim().startsWith("#"));

/**
 * A pyproject.toml that declares dependencies: a non-empty `[project] dependencies` list, a
 * `[dependency-groups]` table, optional dependencies, or Poetry dependency tables.
 */
export const declaresDependencies = (text: string): boolean =>
    /^\s*dependencies\s*=\s*\[\s*(#[^\n]*\n\s*)*["']/m.test(text) ||
    /^\[(dependency-groups|project\.optional-dependencies|tool\.poetry(\.group\.[^\]]+)?\.dependencies)\]/m.test(
        text
    );

/**
 * Dependency files with no sibling uv.lock that are not exempt. `files` are repository-relative
 * paths; `content` reads one of them.
 */
export function unlockedDependencyFiles(
    files: string[],
    content: (file: string) => string,
    exemptPaths: Set<string>
): string[] {
    const lockDirs = new Set(
        files.filter((f) => path.posix.basename(f) === "uv.lock").map((f) => path.posix.dirname(f))
    );
    return files.filter((file) => {
        const base = path.posix.basename(file);
        const isDependencyFile =
            (base === "pyproject.toml" && declaresDependencies(content(file))) ||
            (isRequirementsFile(file) && hasRequirementLines(content(file)));
        return (
            isDependencyFile && !lockDirs.has(path.posix.dirname(file)) && !exemptPaths.has(file)
        );
    });
}

const files = trackedFiles();
const describeWithGit = files ? describe : describe.skip;

describe("Python dependency lock coverage (control)", () => {
    const sources: Record<string, string> = {
        "layers/new/pyproject.toml":
            '[project]\nname = "x"\ndependencies = [\n    "boto3==1.0.0",\n]\n',
        "layers/new/requirements.txt": "boto3==1.0.0\n",
        "layers/locked/pyproject.toml": '[project]\ndependencies = ["boto3==1.0.0"]\n',
        "layers/locked/uv.lock": "",
        "layers/empty/pyproject.toml": '[project]\nname = "x"\ndependencies = []\n',
        "notes/requirements.txt": "# install the CLI from the repository\n",
        "pipeline/poetry/pyproject.toml": '[tool.poetry.dependencies]\npython = "^3.12"\n',
    };
    const read = (f: string) => sources[f];

    test("a new pyproject and requirements file without a uv.lock are reported", () => {
        expect(unlockedDependencyFiles(Object.keys(sources), read, new Set())).toEqual([
            "layers/new/pyproject.toml",
            "layers/new/requirements.txt",
            "pipeline/poetry/pyproject.toml",
        ]);
    });

    test("an exemption removes a file from the report", () => {
        expect(
            unlockedDependencyFiles(
                Object.keys(sources),
                read,
                new Set([
                    "layers/new/pyproject.toml",
                    "layers/new/requirements.txt",
                    "pipeline/poetry/pyproject.toml",
                ])
            )
        ).toEqual([]);
    });
});

describeWithGit("Python dependency lock coverage", () => {
    const tracked = files ?? [];
    const exemptionFile = JSON.parse(read(EXEMPTIONS)) as { exemptions: Exemption[] };
    const exemptions = exemptionFile.exemptions;
    const trackedSet = new Set(tracked);
    const lockDirs = new Set(
        tracked
            .filter((f) => path.posix.basename(f) === "uv.lock")
            .map((f) => path.posix.dirname(f))
    );

    test("the repository has uv.lock projects for the audit job to find (control)", () => {
        expect(lockDirs.size).toBeGreaterThan(0);
        expect(lockDirs.has("backend")).toBe(true);
    });

    test("every Python dependency file has a sibling uv.lock or a reasoned exemption", () => {
        const missing = unlockedDependencyFiles(
            tracked,
            read,
            new Set(exemptions.map((e) => e.path))
        );
        if (missing.length > 0) {
            throw new Error(
                `Python dependency files with no uv.lock beside them:\n  ${missing.join(
                    "\n  "
                )}\n` +
                    "The python-dependency-audit CI job audits only directories that have a uv.lock. " +
                    "Add a pyproject.toml, run `uv lock`, and commit uv.lock with its requirements.txt " +
                    "export (see the 'Python dependencies' section of the developer backend guide), or " +
                    `add an entry with a reason and the change that will add the lock to ${EXEMPTIONS}.`
            );
        }
    });

    test("every exemption names a tracked dependency file, a reason and the change that adds its lock", () => {
        const problems: string[] = [];
        const seen = new Set<string>();
        exemptions.forEach((entry) => {
            const label = entry.path ?? JSON.stringify(entry);
            if (seen.has(entry.path)) {
                problems.push(`${label}: listed twice`);
            }
            seen.add(entry.path);
            if (!entry.reason?.trim()) {
                problems.push(`${label}: no reason`);
            }
            if (!entry.plannedIn?.trim()) {
                problems.push(`${label}: no plannedIn (the change that will add the lock)`);
            }
            if (!trackedSet.has(entry.path)) {
                problems.push(`${label}: not a tracked file any more; remove the exemption`);
            } else if (lockDirs.has(path.posix.dirname(entry.path))) {
                problems.push(`${label}: has a uv.lock beside it now; remove the exemption`);
            } else if (unlockedDependencyFiles([entry.path], read, new Set()).length === 0) {
                problems.push(`${label}: declares no Python dependencies; remove the exemption`);
            }
        });
        expect(problems).toEqual([]);
    });

    test("every uv.lock project requires the uv version the CI audit installs", () => {
        const ci = read(".github/workflows/ci.yml");
        const ciVersion =
            /uses: astral-sh\/setup-uv@[0-9a-f]{40}[^\n]*\n\s+with:\n\s+version: "([^"]+)"/.exec(
                ci
            )?.[1];
        expect(ciVersion).toMatch(/^\d+\.\d+\.\d+$/);
        lockDirs.forEach((dir) => {
            const pyproject = read(path.posix.join(dir, "pyproject.toml"));
            expect([dir, /^required-version\s*=\s*"==([^"]+)"/m.exec(pyproject)?.[1]]).toEqual([
                dir,
                ciVersion,
            ]);
        });
    });
});

describe("Python audit allowlist", () => {
    const allowlist = JSON.parse(read(ALLOWLIST)) as { entries: AllowlistEntry[] };
    const today = new Date().toISOString().slice(0, 10);

    test("the allowlist is an object with an entries list", () => {
        expect(Array.isArray(allowlist.entries)).toBe(true);
    });

    test("every entry has an id, a package, a reason and a review date that has not passed", () => {
        const problems: string[] = [];
        allowlist.entries.forEach((entry, index) => {
            const label = `entry ${index} (${entry.id})`;
            (["id", "package", "reason", "reviewBy"] as const).forEach((key) => {
                if (typeof entry[key] !== "string" || entry[key].trim() === "") {
                    problems.push(`${label}: no ${key}`);
                }
            });
            if (typeof entry.reviewBy === "string") {
                if (
                    !/^\d{4}-\d{2}-\d{2}$/.test(entry.reviewBy) ||
                    isNaN(Date.parse(entry.reviewBy))
                ) {
                    problems.push(`${label}: reviewBy is not an ISO date`);
                } else if (entry.reviewBy < today) {
                    problems.push(
                        `${label}: reviewBy ${entry.reviewBy} has passed; fix the package or review and extend the entry`
                    );
                }
            }
        });
        expect(problems).toEqual([]);
    });
});
