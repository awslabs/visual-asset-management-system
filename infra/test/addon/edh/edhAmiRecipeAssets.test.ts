/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The asset walker behind the recipe bundle hash. The hash must cover exactly the files the
 * BucketDeployment uploads, so the walker's exclusions and the `Source.asset` exclude globs are
 * derived from one list and asserted against a real repository tree. The recipe directory upload
 * leaves out credential files only. The hash reads each file by the line-ending rule of CDK's asset
 * fingerprint, which a control here asserts against CDK itself.
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { LoadedEdhAmiRecipe } from "../../../config/addon/edhWorkstationsConfig";
import {
    ASSET_EXCLUDE_DIRS,
    ASSET_EXCLUDE_GLOBS,
    CREDENTIAL_EXCLUDE_GLOBS,
    bundleHashOf,
    hashedBytes,
    walkFilesSorted,
} from "../../../lib/nestedStacks/addon/edhWorkstations/constructs/edhAmiBuild-construct";

const REPO = path.join(__dirname, "..", "..", "..", "..");
const CLI = path.join(REPO, "tools", "VamsCLI");
/** Leading bytes CDK's asset fingerprint searches for a NUL byte, which marks a file binary. */
const SNIFF_BYTES = 8 * 1024;

/** Every file under `dir` as a POSIX-relative path, with nothing excluded. */
const filesUnder = (dir: string, relative = ""): string[] =>
    fs.readdirSync(path.join(dir, relative), { withFileTypes: true }).flatMap((entry) => {
        const child = relative ? `${relative}/${entry.name}` : entry.name;
        return entry.isDirectory() ? filesUnder(dir, child) : [child];
    });

/** The files `Source.asset` stages from `dir`, copied the way the upload copies them. */
const stagedFrom = (dir: string, exclude: string[] = ASSET_EXCLUDE_GLOBS): string[] => {
    const staged = fs.mkdtempSync(path.join(os.tmpdir(), "edh-asset-staged-"));
    try {
        cdk.FileSystem.copyDirectory(dir, staged, { exclude });
        return filesUnder(staged).sort();
    } finally {
        fs.rmSync(staged, { recursive: true, force: true });
    }
};

/**
 * A CRLF file whose one NUL byte sits `nulAt` bytes in, after a first line and `x` padding, and its
 * LF twin: the same bytes with every CRLF written as LF.
 */
const nulBearing = (nulAt: number): { crlf: Buffer; lf: Buffer } => {
    const padding = "x".repeat(nulAt - "first = 1\r\n".length);
    return {
        crlf: Buffer.from(`first = 1\r\n${padding}\0\r\nlast = 2\r\n`),
        lf: Buffer.from(`first = 1\n${padding}\0\nlast = 2\n`),
    };
};

describe("recipe asset walker", () => {
    const files = walkFilesSorted(CLI);
    let fixtureDir: string;

    beforeAll(() => {
        fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), "edh-asset-walk-"));
        // Build fixture tree with excluded and kept entries.
        fs.mkdirSync(path.join(fixtureDir, "src"), { recursive: true });
        fs.writeFileSync(path.join(fixtureDir, "src", "keep.py"), "# keep");
        fs.mkdirSync(path.join(fixtureDir, "src", "__pycache__"), { recursive: true });
        fs.writeFileSync(
            path.join(fixtureDir, "src", "__pycache__", "keep.cpython-313.pyc"),
            "bytecode"
        );
        fs.mkdirSync(path.join(fixtureDir, "build"), { recursive: true });
        fs.writeFileSync(path.join(fixtureDir, "build", "out.txt"), "artifact");
        fs.mkdirSync(path.join(fixtureDir, "dist"), { recursive: true });
        fs.writeFileSync(path.join(fixtureDir, "dist", "out.whl"), "wheel");
        fs.mkdirSync(path.join(fixtureDir, "pkg.egg-info"), { recursive: true });
        fs.writeFileSync(path.join(fixtureDir, "pkg.egg-info", "PKG-INFO"), "meta");
        fs.mkdirSync(path.join(fixtureDir, ".pytest_cache", "v"), { recursive: true });
        fs.writeFileSync(path.join(fixtureDir, ".pytest_cache", "v", "cache"), "cache");
        fs.writeFileSync(path.join(fixtureDir, "stale.pyc"), "compiled");
        fs.mkdirSync(path.join(fixtureDir, "nested"), { recursive: true });
        fs.writeFileSync(path.join(fixtureDir, "nested", "build"), "file named build");
        fs.mkdirSync(path.join(fixtureDir, "weird.pyc"), { recursive: true });
        fs.writeFileSync(path.join(fixtureDir, "weird.pyc", "inner.txt"), "inside .pyc dir");
        // The same exclusions under directories whose names start with a dot, beside a kept file.
        fs.mkdirSync(path.join(fixtureDir, ".config", "__pycache__"), { recursive: true });
        fs.writeFileSync(path.join(fixtureDir, ".config", "keep.txt"), "kept");
        fs.writeFileSync(path.join(fixtureDir, ".config", "stale.pyc"), "compiled");
        fs.writeFileSync(path.join(fixtureDir, ".config", "__pycache__", "x.pyc"), "bytecode");
        fs.mkdirSync(path.join(fixtureDir, ".hidden", "build"), { recursive: true });
        fs.writeFileSync(path.join(fixtureDir, ".hidden", "build", "out.txt"), "artifact");
        fs.mkdirSync(path.join(fixtureDir, ".hidden", "pkg.egg-info"), { recursive: true });
        fs.writeFileSync(path.join(fixtureDir, ".hidden", "pkg.egg-info", "PKG-INFO"), "meta");
    });

    afterAll(() => {
        fs.rmSync(fixtureDir, { recursive: true, force: true });
    });

    test("[control] the walk finds the CLI's packaging and source", () => {
        expect(files).toContain("pyproject.toml");
        expect(files).toContain("vamscli/main.py");
    });

    test("paths are POSIX-separated, relative and sorted", () => {
        expect(files.some((f) => f.includes("\\"))).toBe(false);
        expect(files.some((f) => path.isAbsolute(f))).toBe(false);
        expect([...files].sort()).toEqual(files);
    });

    test("build artefacts, caches and compiled files are excluded", () => {
        expect(walkFilesSorted(fixtureDir)).toEqual([".config/keep.txt", "src/keep.py"]);
    });

    test("the upload stages exactly the files the walker lists, under a dot directory too", () => {
        expect(stagedFrom(fixtureDir)).toEqual(walkFilesSorted(fixtureDir));
    });

    /** A filesystem that refuses junctions cannot exercise the refusal; probe before declaring. */
    const canJunction = ((): boolean => {
        const probe = fs.mkdtempSync(path.join(os.tmpdir(), "edh-junction-probe-"));
        try {
            fs.mkdirSync(path.join(probe, "target"));
            fs.symlinkSync(path.join(probe, "target"), path.join(probe, "link"), "junction");
            return true;
        } catch {
            return false;
        } finally {
            fs.rmSync(probe, { recursive: true, force: true });
        }
    })();

    (canJunction ? test : test.skip)("symlinks are refused at synth", () => {
        const linkDir = fs.mkdtempSync(path.join(os.tmpdir(), "edh-asset-link-"));
        try {
            fs.mkdirSync(path.join(linkDir, "src"));
            fs.writeFileSync(path.join(linkDir, "src", "keep.py"), "# keep");
            fs.symlinkSync(path.join(linkDir, "src"), path.join(linkDir, "link"), "junction");
            expect(() => walkFilesSorted(linkDir)).toThrow(/symbolic links/);
        } finally {
            fs.rmSync(linkDir, { recursive: true, force: true });
        }
    });

    test("the ASSET_EXCLUDE_GLOBS derivation lists build, *.egg-info and *.pyc as base names", () => {
        expect(ASSET_EXCLUDE_GLOBS).toContain("build");
        expect(ASSET_EXCLUDE_GLOBS).toContain("*.egg-info");
        expect(ASSET_EXCLUDE_GLOBS).toContain("*.pyc");
        expect(ASSET_EXCLUDE_GLOBS.filter((glob) => glob.includes("/"))).toEqual([]);
    });

    test("a missing directory is an error, not an empty walk", () => {
        expect(() => walkFilesSorted(path.join(REPO, "tools", "NotThere"))).toThrow();
    });
});

describe("credential files in a recipe asset tree", () => {
    // `.envrc` starts with `.env` but carries no dot after it, so it is kept.
    const KEPT = [".envrc", "src/env.py", "src/keep.py"];
    let tree: string;

    beforeAll(() => {
        tree = fs.mkdtempSync(path.join(os.tmpdir(), "edh-asset-credentials-"));
        // `.env` and `.env.*` at the root, in a subdirectory and under a dot directory.
        for (const relative of [
            ".env",
            ".env.local",
            "src/.env.production",
            ".config/.env",
            ...KEPT,
        ]) {
            fs.mkdirSync(path.dirname(path.join(tree, relative)), { recursive: true });
            fs.writeFileSync(path.join(tree, relative), "placeholder\n");
        }
    });

    afterAll(() => {
        fs.rmSync(tree, { recursive: true, force: true });
    });

    test("[control] the tree holds every credential file and every kept file", () => {
        expect(filesUnder(tree)).toHaveLength(4 + KEPT.length);
    });

    test("the walker leaves out .env and .env.* at every depth", () => {
        expect(walkFilesSorted(tree)).toEqual(KEPT);
    });

    test("the upload stages exactly the files the walker lists", () => {
        expect(stagedFrom(tree)).toEqual(KEPT);
        expect(stagedFrom(tree)).toEqual(walkFilesSorted(tree));
    });
});

describe("credential files in a recipe directory", () => {
    // A manifest may name a component file under any directory, so only credential files are left out.
    const KEPT = [
        ".envrc",
        "build/010-install.yml",
        "components/010-baseline.yml",
        "components/__pycache__/notes.txt",
        "recipe.json",
    ];
    let recipeDir: string;

    beforeAll(() => {
        recipeDir = fs.mkdtempSync(path.join(os.tmpdir(), "edh-recipe-credentials-"));
        for (const relative of [".env", ".env.local", "components/.env.production", ...KEPT]) {
            fs.mkdirSync(path.dirname(path.join(recipeDir, relative)), { recursive: true });
            fs.writeFileSync(path.join(recipeDir, relative), "placeholder\n");
        }
    });

    afterAll(() => {
        fs.rmSync(recipeDir, { recursive: true, force: true });
    });

    test("[control] the directory holds every credential file and every kept file", () => {
        expect(filesUnder(recipeDir)).toHaveLength(3 + KEPT.length);
    });

    test("the upload stages every file but .env and .env.*, at every depth", () => {
        expect(stagedFrom(recipeDir, CREDENTIAL_EXCLUDE_GLOBS)).toEqual(KEPT);
    });
});

describe("the CDK asset fingerprint, whose line-ending rule the bundle hash follows", () => {
    // CDK caches each file's fingerprint by its root directory, inode, modification time and size,
    // so every variant is written to a directory of its own and none is answered from that cache.
    const dirs: string[] = [];

    /** CDK's fingerprint of a fresh tree holding `src/tool.py` with `content`. */
    const fingerprintOf = (content: string | Buffer): string => {
        const dir = fs.mkdtempSync(path.join(os.tmpdir(), "edh-asset-fingerprint-"));
        dirs.push(dir);
        fs.mkdirSync(path.join(dir, "src"));
        fs.writeFileSync(path.join(dir, "src", "tool.py"), content);
        return cdk.FileSystem.fingerprint(dir, { exclude: ASSET_EXCLUDE_GLOBS });
    };

    afterAll(() => {
        for (const dir of dirs) {
            fs.rmSync(dir, { recursive: true, force: true });
        }
    });

    test("[control] different content fingerprints differently", () => {
        expect(fingerprintOf("a = 1\n")).not.toBe(fingerprintOf("a = 2\n"));
    });

    test("a CRLF tree and its LF twin fingerprint the same", () => {
        expect(fingerprintOf("a = 1\r\nb = 2\r\n")).toBe(fingerprintOf("a = 1\nb = 2\n"));
    });

    test("a NUL byte in the first 8 KiB keeps a file's CRLF in the fingerprint", () => {
        const { crlf, lf } = nulBearing(SNIFF_BYTES - 1);
        expect(fingerprintOf(crlf)).not.toBe(fingerprintOf(lf));
    });

    test("a NUL byte past the first 8 KiB does not", () => {
        const { crlf, lf } = nulBearing(SNIFF_BYTES);
        expect(fingerprintOf(crlf)).toBe(fingerprintOf(lf));
    });
});

describe("the bytes the bundle hash reads from a file", () => {
    let dir: string;

    /** Writes `content` to a new file and reads it back through hashedBytes. */
    const read = (content: string | Buffer): Buffer => {
        const file = path.join(dir, `file-${fs.readdirSync(dir).length}`);
        fs.writeFileSync(file, content);
        return hashedBytes(file);
    };

    beforeAll(() => {
        dir = fs.mkdtempSync(path.join(os.tmpdir(), "edh-hashed-bytes-"));
    });

    afterAll(() => {
        fs.rmSync(dir, { recursive: true, force: true });
    });

    test("CRLF text reads as its LF twin", () => {
        expect(read("a = 1\r\nb = 2\r\n")).toEqual(Buffer.from("a = 1\nb = 2\n"));
    });

    test("a CR that ends no line is kept", () => {
        expect(read("a\rb\r\r\n")).toEqual(Buffer.from("a\rb\r\n"));
    });

    test("bytes that are not UTF-8 are folded without being decoded", () => {
        const bytes = Buffer.from([0x61, 0xff, 0x0d, 0x0a, 0xc3]);
        expect(read(bytes)).toEqual(Buffer.from([0x61, 0xff, 0x0a, 0xc3]));
    });

    test("a file with a NUL byte in its first 8 KiB reads as it is", () => {
        const { crlf } = nulBearing(SNIFF_BYTES - 1);
        expect(read(crlf)).toEqual(crlf);
    });

    test("a CRLF file whose only NUL byte lies past the first 8 KiB is folded", () => {
        const { crlf, lf } = nulBearing(SNIFF_BYTES);
        expect(read(crlf)).toEqual(lf);
    });

    test("an LF file and an empty file read as they are", () => {
        expect(read("a = 1\nb = 2\n")).toEqual(Buffer.from("a = 1\nb = 2\n"));
        expect(read("")).toEqual(Buffer.alloc(0));
    });
});

describe("recipe bundle hash", () => {
    let root: string;

    const componentPath = (): string =>
        path.join(root, "recipes", "probe", "components", "010-install.yml");
    const assetFile = (name: string): string => path.join(root, "trees", "tool", "src", name);

    /** One recipe with one component file and one asset tree, rooted at the temporary tree. */
    const recipe = (): LoadedEdhAmiRecipe => ({
        recipeDir: "recipes/probe",
        recipeId: "probe",
        manifest: {} as LoadedEdhAmiRecipe["manifest"],
        componentFiles: ["recipes/probe/components/010-install.yml"],
        assets: [{ id: "tool", repoPath: "trees/tool" }],
        buildOnDeploy: false,
    });

    /** Rewrites the recipe files and the asset file as lines that each end with `eol`. */
    const writeLines = (eol: string): void => {
        const text = (...lines: string[]): string => lines.map((line) => `${line}${eol}`).join("");
        fs.writeFileSync(
            path.join(root, "recipes", "probe", "recipe.json"),
            text("{", '    "recipeId": "probe"', "}")
        );
        fs.writeFileSync(componentPath(), text("schemaVersion: 1.0", "phases: []"));
        fs.writeFileSync(assetFile("tool.py"), text("import sys", "print(1)"));
    };

    beforeEach(() => {
        root = fs.mkdtempSync(path.join(os.tmpdir(), "edh-bundle-hash-"));
        fs.mkdirSync(path.join(root, "recipes", "probe", "components"), { recursive: true });
        fs.writeFileSync(path.join(root, "recipes", "probe", "recipe.json"), "{}");
        fs.writeFileSync(componentPath(), "schemaVersion: 1.0\n");
        fs.mkdirSync(path.join(root, "trees", "tool", "src"), { recursive: true });
        fs.writeFileSync(assetFile("tool.py"), "print(1)\n");
    });

    afterEach(() => {
        fs.rmSync(root, { recursive: true, force: true });
    });

    test("the same content hashes the same twice", () => {
        const hash = bundleHashOf([recipe()], root);
        expect(hash).toMatch(/^[0-9a-f]{16}$/);
        expect(bundleHashOf([recipe()], root)).toBe(hash);
    });

    test("one changed byte in an asset file moves the hash", () => {
        const before = bundleHashOf([recipe()], root);
        fs.writeFileSync(assetFile("tool.py"), "print(2)\n");
        expect(bundleHashOf([recipe()], root)).not.toBe(before);
    });

    test("a file added to the asset tree moves the hash", () => {
        const before = bundleHashOf([recipe()], root);
        fs.writeFileSync(assetFile("extra.py"), "print(1)\n");
        expect(bundleHashOf([recipe()], root)).not.toBe(before);
    });

    test("a changed component file moves the hash", () => {
        const before = bundleHashOf([recipe()], root);
        fs.writeFileSync(componentPath(), "schemaVersion: 1.0\n# one more line\n");
        expect(bundleHashOf([recipe()], root)).not.toBe(before);
    });

    test("a rebuilt file in the asset tree leaves the hash alone", () => {
        const before = bundleHashOf([recipe()], root);
        fs.writeFileSync(assetFile("tool.pyc"), "bytecode");
        expect(bundleHashOf([recipe()], root)).toBe(before);
    });

    test("a credential file in the asset tree leaves the hash alone", () => {
        const tool = path.join(root, "trees", "tool");
        const before = bundleHashOf([recipe()], root);
        fs.mkdirSync(path.join(tool, ".config"));
        for (const relative of [".env", ".env.local", "src/.env.production", ".config/.env"]) {
            fs.writeFileSync(path.join(tool, relative), "placeholder\n");
        }
        expect(bundleHashOf([recipe()], root)).toBe(before);
        fs.writeFileSync(assetFile("env.py"), "placeholder\n");
        expect(bundleHashOf([recipe()], root)).not.toBe(before);
    });

    test("recipe files and an asset tree written with CRLF hash as their LF twin", () => {
        writeLines("\n");
        const lf = bundleHashOf([recipe()], root);
        writeLines("\r\n");
        expect(bundleHashOf([recipe()], root)).toBe(lf);
    });

    test("an asset file with a NUL byte in its first 8 KiB keeps its CRLF in the hash", () => {
        const { crlf, lf } = nulBearing(SNIFF_BYTES - 1);
        fs.writeFileSync(assetFile("blob.bin"), lf);
        const folded = bundleHashOf([recipe()], root);
        fs.writeFileSync(assetFile("blob.bin"), crlf);
        expect(bundleHashOf([recipe()], root)).not.toBe(folded);
    });

    test("the order the recipes are seeded in is part of the hash", () => {
        const other: LoadedEdhAmiRecipe = { ...recipe(), recipeId: "other" };
        expect(bundleHashOf([recipe(), other], root)).not.toBe(
            bundleHashOf([other, recipe()], root)
        );
    });
});
