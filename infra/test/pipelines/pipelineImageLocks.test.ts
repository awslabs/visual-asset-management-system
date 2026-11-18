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
        const pin = /^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==\S+/.exec(line);
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

/** Direct dependency names in a requirements.txt. */
function directDependencies(text: string): string[] {
    return text
        .split(/\r?\n/)
        .map((l) => l.replace(/#.*/, "").trim())
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

    it("carries a lock generated from requirements.txt with a hash on every pin", () => {
        // [control] the parser found the pins at all.
        expect(entries.length).toBeGreaterThan(0);
        expect(lock).toMatch(/^#\s+uv pip compile requirements\.txt --generate-hashes /m);
        expect(entries.filter((e) => e.hashes === 0).map((e) => e.name)).toEqual([]);
    });

    it("locks every direct dependency", () => {
        const locked = new Set(entries.map((e) => e.name));
        const direct = directDependencies(
            fs.readFileSync(path.join(root, "requirements.txt"), "utf-8")
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
