/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The shipped Linux AMI recipes install the VAMS CLI's Python packages from a lock with a hash per file,
 * as `backendPipelines/CLAUDE.md` "Pinning a Container's Dependencies" has the pipeline images do: the
 * dependencies and the build backend from `tools/VamsCLI/requirements.lock`, then the CLI itself with no
 * index. An install from `requirements.txt` or an `--upgrade` of the pip toolchain leaves the packages to
 * whatever the index serves on build day, so two builds of one recipe version could ship different code.
 */

import * as fs from "fs";
import * as path from "path";
import {
    EDH_SHIPPED_AMI_RECIPE_DIRS,
    loadEdhAmiRecipe,
} from "../../../config/addon/edhWorkstationsConfig";

const REPO = path.join(__dirname, "..", "..", "..", "..");
const CLI_DIR = path.join(REPO, "tools", "VamsCLI");
const LOCK_INSTALL_PATH = "/opt/vams/vamscli-src/requirements.lock";

/** Shell lines of a component, backslash continuations joined and YAML list markers dropped. */
function shellLines(text: string): string[] {
    const out: string[] = [];
    let current: string | null = null;
    for (const raw of text.split(/\r?\n/)) {
        const continues = /\\\s*$/.test(raw);
        const body = raw
            .replace(/\\\s*$/, "")
            .trim()
            .replace(/^- /, "");
        if (current === null && body.startsWith("#")) continue;
        current = current === null ? body : `${current} ${body}`;
        if (continues) continue;
        if (current !== "") out.push(current);
        current = null;
    }
    if (current) out.push(current);
    return out;
}

/** PEP 503 name normalization. */
function normalize(name: string): string {
    return name.toLowerCase().replace(/[-_.]+/g, "-");
}

/** `name==version` pins of a uv-generated lock, each with the number of hashes that follow it. */
function lockEntries(text: string): { name: string; version: string; hashes: number }[] {
    const entries: { name: string; version: string; hashes: number }[] = [];
    for (const line of text.split(/\r?\n/)) {
        const pin = /^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==(\S+)/.exec(line);
        if (pin) entries.push({ name: normalize(pin[1]), version: pin[2], hashes: 0 });
        else if (/^\s+--hash=sha256:[0-9a-f]{64}/.test(line) && entries.length) {
            entries[entries.length - 1].hashes += 1;
        }
    }
    return entries;
}

/** Requirement lines of a requirements file: name and the specifiers after it. */
function requirements(text: string): { name: string; specifiers: string[] }[] {
    return text
        .split(/\r?\n/)
        .map((l) => l.replace(/#.*/, "").trim())
        .filter(Boolean)
        .map((l) => {
            const name = /^[A-Za-z0-9][A-Za-z0-9._-]*/.exec(l)![0];
            const rest = l.slice(name.length).trim();
            return {
                name: normalize(name),
                specifiers: rest ? rest.split(",").map((s) => s.trim()) : [],
            };
        });
}

/** The numeric release segments of a version (`2024.5.1` → [2024, 5, 1]). */
function release(version: string): number[] {
    const match = /^\d+(?:\.\d+)*/.exec(version);
    expect(match).not.toBeNull();
    return match![0].split(".").map(Number);
}

function compare(a: string, b: string): number {
    const [x, y] = [release(a), release(b)];
    for (let i = 0; i < Math.max(x.length, y.length); i++) {
        const d = (x[i] ?? 0) - (y[i] ?? 0);
        if (d !== 0) return d;
    }
    return 0;
}

/** Whether `version` meets one specifier; only the operators the CLI's manifests use are known. */
function satisfies(version: string, specifier: string): boolean {
    const match = /^(>=|<|==)\s*(\S+)$/.exec(specifier);
    expect({ specifier, known: match !== null }).toEqual({ specifier, known: true });
    const [, op, bound] = match!;
    const d = compare(version, bound);
    return op === ">=" ? d >= 0 : op === "<" ? d < 0 : d === 0;
}

const linuxRecipes = EDH_SHIPPED_AMI_RECIPE_DIRS.map((dir) =>
    loadEdhAmiRecipe(REPO, dir, false)
).filter((r) => r.manifest.osFamily === "linux");

describe("the VAMS CLI lock", () => {
    const lock = fs.readFileSync(path.join(CLI_DIR, "requirements.lock"), "utf-8");
    const entries = lockEntries(lock);
    const locked = new Map(entries.map((e) => [e.name, e.version]));
    const direct = [
        ...requirements(fs.readFileSync(path.join(CLI_DIR, "requirements.txt"), "utf-8")),
        ...requirements(fs.readFileSync(path.join(CLI_DIR, "requirements-build.txt"), "utf-8")),
    ];

    it("is exported from tools/VamsCLI/uv.lock with a hash on every pin", () => {
        // [control] the parser found the pins at all.
        expect(entries.length).toBeGreaterThan(10);
        // The export carries the runtime dependencies and the `build` dependency group (the build backend
        // of pyproject.toml), and no project or dev packages. Its pins are marker-gated where they differ by
        // platform or Python; the recipes install it with `--only-binary :all:` on Python 3.12, and the
        // CI python-dependency-audit job regenerates it from uv.lock and fails when it is stale.
        expect(lock).toMatch(
            /^#\s+uv export --frozen --no-emit-project --no-dev --no-annotate --group build -o requirements\.lock$/m
        );
        expect(entries.filter((e) => e.hashes === 0).map((e) => e.name)).toEqual([]);
    });

    it("locks every direct dependency and build requirement at a version its specifiers allow", () => {
        expect(direct.map((d) => d.name)).toEqual(
            expect.arrayContaining(["click", "setuptools", "wheel"])
        );
        const misses = direct
            .filter(
                (d) =>
                    !locked.has(d.name) ||
                    !d.specifiers.every((s) => satisfies(locked.get(d.name)!, s))
            )
            .map(
                (d) =>
                    `${d.name}${d.specifiers.join(",")} locked ${locked.get(d.name) ?? "nowhere"}`
            );
        expect(misses).toEqual([]);
    });

    it("names in requirements-build.txt exactly the build backend pyproject.toml declares", () => {
        const pyproject = fs.readFileSync(path.join(CLI_DIR, "pyproject.toml"), "utf-8");
        const requires = /\[build-system\][^[]*?requires\s*=\s*\[([^\]]*)\]/.exec(pyproject);
        expect(requires).not.toBeNull();
        const declared = requires![1]
            .split(",")
            .map((s) => s.trim().replace(/^"|"$/g, ""))
            .filter(Boolean)
            .sort();
        const build = fs
            .readFileSync(path.join(CLI_DIR, "requirements-build.txt"), "utf-8")
            .split(/\r?\n/)
            .map((l) => l.replace(/#.*/, "").trim())
            .filter(Boolean)
            .sort();
        expect(build).toEqual(declared);
        // The `build` dependency group of uv.lock is what requirements.lock exports for the build backend.
        const group = /\[dependency-groups\][^[]*?build\s*=\s*\[([^\]]*)\]/.exec(pyproject);
        expect(group).not.toBeNull();
        expect(
            group![1]
                .split(",")
                .map((s) => s.trim().replace(/^"|"$/g, ""))
                .filter(Boolean)
                .sort()
        ).toEqual(declared);
    });
});

describe.each(linuxRecipes.map((r) => [r.recipeId, r] as const))("the %s recipe", (_id, recipe) => {
    const installs = recipe.componentFiles.flatMap((file) =>
        shellLines(fs.readFileSync(path.join(REPO, file), "utf-8"))
            .filter((line) => /\bpip3?\s+install\b/.test(line))
            .map((line) => ({ file: path.basename(file), line }))
    );

    it("fetches the CLI tree that holds the lock", () => {
        expect(recipe.assets).toEqual(
            expect.arrayContaining([{ id: "vamscli", repoPath: "tools/VamsCLI" }])
        );
    });

    it("installs packages only from the lock with hashes required, as wheels, then the CLI with no index", () => {
        // [control] the recipe installs the CLI at all.
        expect(installs.length).toBeGreaterThanOrEqual(2);
        const fromLock = installs.filter(({ line }) => line.includes(`-r ${LOCK_INSTALL_PATH}`));
        expect(fromLock.length).toBeGreaterThan(0);
        for (const { file, line } of installs) {
            expect({ file, line, upgrade: /--upgrade\b|(^|\s)-U(\s|$)/.test(line) }).toEqual({
                file,
                line,
                upgrade: false,
            });
            expect({
                file,
                line,
                fromRequirementsTxt: /-r \S*requirements\.txt\b/.test(line),
            }).toEqual({
                file,
                line,
                fromRequirementsTxt: false,
            });
            if (line.includes(`-r ${LOCK_INSTALL_PATH}`)) {
                expect(line).toMatch(/--require-hashes/);
                expect(line).toMatch(/--only-binary :all:/);
            } else {
                // The CLI's own tree: built against the locked backend, nothing fetched.
                expect(line).toMatch(/--no-index/);
                expect(line).toMatch(/--no-deps/);
                expect(line).toMatch(/--no-build-isolation/);
                expect(line).toMatch(/\/opt\/vams\/vamscli-src$/);
            }
        }
    });
});
