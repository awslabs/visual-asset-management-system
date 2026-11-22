/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The pipeline images other than Image to 3D and 3dThumbnail follow `backendPipelines/CLAUDE.md`
 * "Pinning a Container's Dependencies": their Python packages come from a lock with a hash per file,
 * installed with `--require-hashes`, and every stage that installs OS packages applies the security updates
 * the digest-pinned base predates. A `requirements.txt` of direct dependencies leaves every transitive
 * package to whatever the index serves on build day, so two builds of one commit could ship different code.
 *
 * The PDAL image is conda-based: its build environment comes from conda's explicit format, one package
 * build per line with its SHA-256, which `conda create --file` installs without a solve.
 *
 * The base digests are asserted by `containerBuildSources.test.ts`; this file covers the packages.
 */

import * as fs from "fs";
import * as path from "path";

const PIPELINES_DIR = path.join(__dirname, "..", "..", "..", "backendPipelines");

const PIP_LOCKED_IMAGES: { label: string; dir: string; dockerfile: string }[] = [
    { label: "CAD STEP agent", dir: "genAi/cadStepAgent/container", dockerfile: "Dockerfile" },
    { label: "Video SOP/BOM", dir: "genAi/videoSopBom/container", dockerfile: "Dockerfile" },
    { label: "zipArchive", dir: "utility/zipArchive/container", dockerfile: "Dockerfile" },
    {
        label: "coordinate transform",
        dir: "conversion/coordinateTransform/container",
        dockerfile: "Dockerfile",
    },
    {
        label: "pcPotreeViewer PDAL",
        dir: "preview/pcPotreeViewer/container",
        dockerfile: "Dockerfile_PDAL",
    },
];

/** Logical Dockerfile instructions: backslash continuations joined, comments dropped. */
function instructions(text: string): string[] {
    const out: string[] = [];
    let current: string | null = null;
    for (const raw of text.split(/\r?\n/)) {
        if (current === null && /^\s*#/.test(raw)) continue;
        const continues = /\\\s*$/.test(raw);
        const body = raw.replace(/\\\s*$/, "").trimEnd();
        current = current === null ? body : current + " " + body.trim();
        if (continues) continue;
        if (current.trim() !== "") out.push(current);
        current = null;
    }
    if (current !== null && current.trim() !== "") out.push(current);
    return out;
}

/** `name==version` pins of a uv-generated lock, each with the hashes that follow it. */
function lockEntries(text: string): { name: string; hashes: number }[] {
    const entries: { name: string; hashes: number }[] = [];
    for (const line of text.split(/\r?\n/)) {
        // A pin by version, or by direct URL (`name @ https://...`), which the lock hashes the same way.
        const pin =
            /^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==\S+/.exec(line) ??
            /^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])? @ https:\/\/\S+/.exec(line);
        if (pin) entries.push({ name: normalize(pin[1]), hashes: 0 });
        else if (/^\s+--hash=sha256:[0-9a-f]{64}/.test(line) && entries.length) {
            entries[entries.length - 1].hashes += 1;
        }
    }
    return entries;
}

/** PEP 503 name normalization. */
function normalize(name: string): string {
    return name.toLowerCase().replace(/[-_.]+/g, "-");
}

/** Direct dependency names in a pyproject.toml's `[project] dependencies` list. */
function directDependencies(text: string): string[] {
    const block = /^dependencies = \[([\s\S]*?)^\]/m.exec(text);
    expect(block).not.toBeNull(); // [control] the dependency list was found
    return block![1]
        .split(/\r?\n/)
        .map((l) =>
            l
                .replace(/#.*/, "")
                .trim()
                .replace(/^"|",?$/g, "")
        )
        .filter(Boolean)
        .map((l) => normalize(/^[A-Za-z0-9][A-Za-z0-9._-]*/.exec(l)![0]));
}

describe.each(PIP_LOCKED_IMAGES)("the $label image", ({ dir, dockerfile }) => {
    const root = path.join(PIPELINES_DIR, dir);
    const steps = instructions(fs.readFileSync(path.join(root, dockerfile), "utf-8"));
    const lock = fs.readFileSync(path.join(root, "requirements.lock"), "utf-8");
    const entries = lockEntries(lock);

    it("installs its Python packages from the lock with hashes required, as wheels only", () => {
        const installs = steps.filter((s) => /\bpip3?\s+install\b/.test(s));
        expect(installs.length).toBeGreaterThan(0);
        for (const step of installs) {
            expect(step).toMatch(/--require-hashes/);
            expect(step).toMatch(/--only-binary :all:/);
            expect(step).toMatch(/-r \S*requirements\.lock\b/);
            expect(step).not.toMatch(/-r \S*requirements\.txt\b/);
        }
    });

    it("carries the hashed uv export of uv.lock, with a hash on every pin", () => {
        // [control] the parser found the pins at all.
        expect(entries.length).toBeGreaterThan(0);
        // The audit regenerates this file from the command in its header and requires it to match, so the
        // image installs what `uv.lock` holds; `--no-hashes` would defeat `--require-hashes`.
        expect(lock).toMatch(/^#\s+uv export --frozen /m);
        expect(lock).toMatch(/^#\s+uv export .*-o requirements\.lock$/m);
        expect(lock).not.toMatch(/^#\s+uv export .*--no-hashes/m);
        expect(entries.filter((e) => e.hashes === 0).map((e) => e.name)).toEqual([]);
    });

    it("locks every direct dependency", () => {
        const locked = new Set(entries.map((e) => e.name));
        const direct = directDependencies(
            fs.readFileSync(path.join(root, "pyproject.toml"), "utf-8")
        );
        expect(direct.length).toBeGreaterThan(0);
        expect(direct.filter((name) => !locked.has(name))).toEqual([]);
    });

    it("applies the OS security updates before any package install, in every stage that installs", () => {
        for (const step of steps.filter((s) => /apt-get install/.test(s))) {
            const update = step.indexOf("apt-get update");
            const upgrade = step.indexOf("apt-get upgrade -y");
            const install = step.indexOf("apt-get install");
            expect([update >= 0, upgrade > update, install > upgrade]).toEqual([true, true, true]);
        }
        // And the runtime stage applies them even where it installs nothing else.
        const lastFrom = steps.reduce((acc, s, i) => (/^FROM\s/.test(s) ? i : acc), -1);
        expect(
            steps.slice(lastFrom).some((s) => /apt-get update && apt-get upgrade -y/.test(s))
        ).toBe(true);
    });
});

describe("the PDAL build environment", () => {
    const root = path.join(PIPELINES_DIR, "preview/pcPotreeViewer/container");
    const steps = instructions(fs.readFileSync(path.join(root, "Dockerfile_PDAL"), "utf-8"));
    const explicit = fs.readFileSync(path.join(root, "pdal-build.explicit.txt"), "utf-8");
    const packages = explicit.split(/\r?\n/).filter((l) => /^https:\/\//.test(l));

    it("is created from the explicit lock, with no solve and no other conda install", () => {
        expect(steps).toContain(
            "RUN conda create -n pdal -y --file /tmp/pdal-build.explicit.txt && rm -f /tmp/pdal-build.explicit.txt"
        );
        expect(steps.filter((s) => /\b(mamba|conda) install\b/.test(s))).toEqual([]);
    });

    it("pins every package build to a SHA-256", () => {
        expect(explicit).toMatch(/^@EXPLICIT$/m);
        expect(packages.length).toBeGreaterThan(100);
        expect(
            packages.filter((l) => !/\.conda#[0-9a-f]{64}$|\.tar\.bz2#[0-9a-f]{64}$/.test(l))
        ).toEqual([]);
    });

    it("holds PDAL's dependencies but not PDAL, which the image builds from source", () => {
        expect(
            packages.filter((l) => /\/(libpdal|pdal|python-pdal)(-[a-z0-9]+)?-\d/.test(l))
        ).toEqual([]);
        for (const dependency of ["gdal", "proj", "xerces-c", "hdf5", "tiledb", "libpq"]) {
            expect([
                dependency,
                packages.some(
                    (l) => l.includes(`/${dependency}-`) || l.includes(`/lib${dependency}-`)
                ),
            ]).toEqual([dependency, true]);
        }
    });
});

/** Every `pip install` / `uv pip install` / `uv sync` instruction of a Dockerfile. */
function installSteps(steps: string[]): string[] {
    return steps.filter((s) => /\b(pip3?\s+install|uv\s+pip\s+install|uv\s+sync)\b/.test(s));
}

/**
 * Images whose one Python install is a runtime-stage `pip install`, now from a hashed lock. Their bases
 * are large vendor or Alpine images whose OS packages the image does not manage, so the OS-update rule
 * above is not asserted for them.
 */
const PIP_LOCKED_RUNTIME_INSTALLS: { label: string; dir: string; dockerfile: string }[] = [
    {
        label: "Isaac Lab training",
        dir: "simulation/isaacLabTraining/container",
        dockerfile: "Dockerfile",
    },
];

/**
 * The Potree image: a runtime-stage `pip install` of the hashed uv export, on an Alpine base whose OS packages
 * the image does not manage (so the OS-update rule above is not asserted). Its lock is shared with the PDAL
 * image, which the first list covers.
 */
describe("the pcPotreeViewer Potree image", () => {
    const root = path.join(PIPELINES_DIR, "preview/pcPotreeViewer/container");
    const steps = instructions(fs.readFileSync(path.join(root, "Dockerfile_Potree"), "utf-8"));
    const lock = fs.readFileSync(path.join(root, "requirements.lock"), "utf-8");
    const entries = lockEntries(lock);

    it("installs its Python packages from the hashed lock only, as wheels", () => {
        const installs = installSteps(steps);
        expect(installs.length).toBeGreaterThan(0);
        for (const step of installs) {
            expect(step).toMatch(/--require-hashes/);
            expect(step).toMatch(/--only-binary :all:/);
            expect(step).toMatch(/-r \S*requirements\.lock\b/);
        }
    });

    it("locks every direct dependency of pyproject.toml, with a hash on every pin", () => {
        expect(lock).toMatch(/^#\s+uv export --frozen /m);
        expect(lock).not.toMatch(/^#\s+uv export .*--no-hashes/m);
        expect(entries.length).toBeGreaterThan(0);
        expect(entries.filter((e) => e.hashes === 0).map((e) => e.name)).toEqual([]);
        const locked = new Set(entries.map((e) => e.name));
        const direct = directDependencies(
            fs.readFileSync(path.join(root, "pyproject.toml"), "utf-8")
        );
        expect(direct.length).toBeGreaterThan(0);
        expect(direct.filter((name) => !locked.has(name))).toEqual([]);
    });
});

describe.each(PIP_LOCKED_RUNTIME_INSTALLS)("the $label image", ({ dir, dockerfile }) => {
    const root = path.join(PIPELINES_DIR, dir);
    const steps = instructions(fs.readFileSync(path.join(root, dockerfile), "utf-8"));
    const lock = fs.readFileSync(path.join(root, "requirements.lock"), "utf-8");
    const entries = lockEntries(lock);

    it("installs its Python packages from the hashed lock only, as wheels", () => {
        const installs = installSteps(steps);
        expect(installs.length).toBeGreaterThan(0);
        for (const step of installs) {
            expect(step).toMatch(/--require-hashes/);
            expect(step).toMatch(/--only-binary :all:/);
            expect(step).toMatch(/-r \S*requirements\.lock\b/);
        }
    });

    it("locks every direct dependency of pyproject.toml, with a hash on every pin", () => {
        expect(lock).toMatch(/^#\s+uv export --frozen /m);
        expect(lock).not.toMatch(/^#\s+uv export .*--no-hashes/m);
        expect(entries.length).toBeGreaterThan(0);
        expect(entries.filter((e) => e.hashes === 0).map((e) => e.name)).toEqual([]);
        const locked = new Set(entries.map((e) => e.name));
        const direct = directDependencies(
            fs.readFileSync(path.join(root, "pyproject.toml"), "utf-8")
        );
        expect(direct.length).toBeGreaterThan(0);
        expect(direct.filter((name) => !locked.has(name))).toEqual([]);
    });
});

describe("the Cosmos images", () => {
    const COSMOS = [
        "genAi/nvidia/cosmos/3/container",
        "genAi/nvidia/cosmos/predict/containerv2.5",
        "genAi/nvidia/cosmos/reason/container",
        "genAi/nvidia/cosmos/transfer/container",
    ];

    it.each(COSMOS)("%s installs from its upstream uv.lock, or from a hashed lock", (dir) => {
        // `uv sync --locked` or `--frozen` installs exactly the upstream lock at the pinned commit, every file
        // checked against its recorded SHA-256, with no resolution of its own.
        const steps = instructions(
            fs.readFileSync(path.join(PIPELINES_DIR, dir, "Dockerfile"), "utf-8")
        );
        const installs = installSteps(steps);
        expect(installs.some((s) => /\buv sync\b/.test(s))).toBe(true);
        for (const step of installs) {
            if (/\buv sync\b/.test(step)) expect(step).toMatch(/--(locked|frozen)\b/);
            else expect(step).toMatch(/--require-hashes .*-r \S+\.lock\b/);
        }
    });

    it("Cosmos Transfer's NCCL upgrade comes from its hashed lock", () => {
        const root = path.join(PIPELINES_DIR, "genAi/nvidia/cosmos/transfer/container");
        const lock = fs.readFileSync(path.join(root, "requirements-nccl.lock"), "utf-8");
        // The hashed uv export of uv.lock, which the audit regenerates from this header and requires to match.
        expect(lock).toMatch(/^#\s+uv export --frozen .*-o requirements-nccl\.lock$/m);
        expect(lock).not.toMatch(/^#\s+uv export .*--no-hashes/m);
        const entries = lockEntries(lock);
        expect(entries.map((e) => e.name)).toEqual(["nvidia-nccl-cu12"]);
        expect(entries[0].hashes).toBeGreaterThan(0);
    });
});

describe("the GR00T image", () => {
    const root = path.join(PIPELINES_DIR, "genAi/nvidia/gr00t/container");
    const steps = instructions(fs.readFileSync(path.join(root, "Dockerfile"), "utf-8"));
    const lock = fs.readFileSync(path.join(root, "requirements.lock"), "utf-8");
    const buildLock = fs.readFileSync(path.join(root, "requirements-build.lock"), "utf-8");
    const versions = (text: string) =>
        new Map(
            [...text.matchAll(/^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)/gm)].map((m) => [
                normalize(m[1]),
                m[2],
            ])
        );

    it("installs the build tools, then the lock with no build isolation, then Isaac-GR00T without dependencies", () => {
        const installs = installSteps(steps);
        expect(installs).toEqual([
            expect.stringMatching(
                /--require-hashes --only-binary :all: -r \S*requirements-build\.lock$/
            ),
            expect.stringMatching(
                /--require-hashes --no-build-isolation -r \S*requirements\.lock$/
            ),
            expect.stringMatching(/--no-deps --no-build-isolation -e \.$/),
        ]);
    });

    it("locks Isaac-GR00T's base dependencies and the image's own, a hash on every pin", () => {
        expect(lock).toMatch(
            /^#\s+uv pip compile \/workspace\/pyproject\.toml --extra base requirements-image\.txt --generate-hashes /m
        );
        const entries = lockEntries(lock);
        expect(entries.length).toBeGreaterThan(100);
        expect(entries.filter((e) => e.hashes === 0).map((e) => e.name)).toEqual([]);
        const locked = new Set(entries.map((e) => e.name));
        for (const name of [
            "torch",
            "tensorflow",
            "transformers",
            "flash-attn",
            "gpustat",
            "setuptools-scm",
        ]) {
            expect([name, locked.has(name)]).toEqual([name, true]);
        }
    });

    it("builds with the same tool versions the main lock holds", () => {
        const main = versions(lock);
        const build = versions(buildLock);
        expect(build.size).toBeGreaterThan(0);
        for (const [name, version] of build) {
            if (main.has(name)) expect([name, version]).toEqual([name, main.get(name)]);
        }
        expect(lockEntries(buildLock).filter((e) => e.hashes === 0)).toEqual([]);
    });
});
