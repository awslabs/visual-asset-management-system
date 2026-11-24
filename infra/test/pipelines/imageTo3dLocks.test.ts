/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Image to 3D images install their Python packages from one hashed lock per image (#1726), under
 * `backendPipelines/genAi/imageTo3d/container/locks/<model>/`. The lock holds the image's WHOLE installed set,
 * taken from a build of the image before the lock existed (`frozen-set.txt`), so a rebuild ships the versions
 * the image already shipped and nothing resolves on build day. One shared `requirements.txt` cannot serve
 * seven images (the reverted attempt moved numpy, pillow and scipy under images that compile extensions
 * against them), which is why each image has its own.
 *
 * All seven images are converted, so the shared `requirements.txt` and `vams_utils/requirements.txt` are gone, with
 * their exemptions in `.github/python-lock-exemptions.json`. The audit (`python-dependency-audit`) checks
 * `uv lock --check`, the exports and the advisories of every lock; this file checks that the Dockerfile uses them and
 * that the pieces agree. SAM 3D also locks its conda environment (`locks/sam3d/conda-explicit.txt`).
 */

import * as fs from "fs";
import * as path from "path";

const CONTAINER_DIR = path.join(
    __dirname,
    "..",
    "..",
    "..",
    "backendPipelines",
    "genAi",
    "imageTo3d",
    "container"
);

interface ConvertedImage {
    model: string;
    dockerfile: string;
    /** Upstream requirement files the lock was resolved from, relative to the upstream clone. */
    upstreamFiles: string[];
    /** Upstream lines the Dockerfile deletes or the lock replaces, with the pyproject override that replaces them. */
    upstreamLinesNotLocked: RegExp[];
    /** The PyTorch index torch and its siblings come from (default: the CUDA 12.8 index). */
    torchIndex?: { name: string; url: string };
}

const CU128 = { name: "pytorch-cu128", url: "https://download.pytorch.org/whl/cu128" };

const CONVERTED: ConvertedImage[] = [
    {
        model: "stable3dgen",
        dockerfile: "Dockerfile.stable3dgen",
        upstreamFiles: ["requirements.txt"],
        upstreamLinesNotLocked: [],
    },
    {
        model: "triposr",
        dockerfile: "Dockerfile.triposr",
        upstreamFiles: ["requirements.txt"],
        upstreamLinesNotLocked: [/^git\+https:\/\/github\.com\/tatsy\/torchmcubes\.git$/],
    },
    {
        // nvdiffrast and pytorch3d are built from pinned commits (out-of-lock.json); pymeshlab is the GPL group's.
        model: "step1x3d",
        dockerfile: "Dockerfile.step1x3d",
        upstreamFiles: ["requirements.txt"],
        upstreamLinesNotLocked: [
            /^git\+https:\/\/github\.com\/NVlabs\/nvdiffrast\.git$/,
            /^git\+https:\/\/github\.com\/facebookresearch\/pytorch3d\.git@stable$/,
            /^pymeshlab==/,
        ],
    },
    {
        // The Dockerfile has always filtered these out of upstream's list: torch and torchvision (the CUDA build),
        // pymeshlab (GPL group) and the demo-only gradio, fastapi and uvicorn.
        model: "hunyuan3d",
        dockerfile: "Dockerfile.hunyuan3d",
        upstreamFiles: ["requirements.txt"],
        upstreamLinesNotLocked: [/^(torch|torchvision|pymeshlab|gradio|fastapi|uvicorn)$/],
    },
    {
        // CUDA 12.4 torch from its own explicit index (PyPI stays the default for the NVIDIA runtime wheels), the prebuilt
        // flash-attn wheel in the lock, and five packages built from pinned clones (utils3d hash-checked). Upstream's
        // setup.sh names no versions and the Dockerfile's own list is what the lock mirrors, so no upstream file is recorded.
        model: "trellis2",
        dockerfile: "Dockerfile.trellis2",
        upstreamFiles: [],
        upstreamLinesNotLocked: [],
        torchIndex: { name: "pytorch-cu124", url: "https://download.pytorch.org/whl/cu124" },
    },
    {
        // The Blender agent has no upstream requirements file: its set is the shared pipeline dependencies, LiteLLM and
        // its dependencies, the CUDA 12.8 torch and what MoGe-2 imports. MoGe and utils3d are built from pinned clones.
        model: "blenderagent",
        dockerfile: "Dockerfile.blenderagent",
        upstreamFiles: [],
        upstreamLinesNotLocked: [],
    },
    {
        // SAM 3D: a conda environment (conda-explicit.txt) and the CUDA 12.1 torch stack from its own explicit index. The git
        // requirements are built from pinned clones, kaolin is NVIDIA's wheel by its SHA-256 (PyPI does not carry it), the
        // flash-attn wheel is locked by URL, pymeshfix is the GPL group's, and nvidia-pyindex is left out (out-of-lock.json,
        // pyproject.toml).
        model: "sam3d",
        dockerfile: "Dockerfile.sam3d",
        upstreamFiles: [
            "requirements.txt",
            "requirements.dev.txt",
            "requirements.p3d.txt",
            "requirements.inference.txt",
        ],
        upstreamLinesNotLocked: [
            /^MoGe @ git\+https:\/\/github\.com\/microsoft\/MoGe\.git@[0-9a-f]{40}$/,
            /^gsplat @ git\+https:\/\/github\.com\/nerfstudio-project\/gsplat\.git@[0-9a-f]{40}$/,
            /^pytorch3d @ git\+https:\/\/github\.com\/facebookresearch\/pytorch3d\.git@[0-9a-f]{40}$/,
            /^(kaolin|flash_attn|pymeshfix|nvidia-pyindex)==/,
        ],
        torchIndex: { name: "pytorch-cu121", url: "https://download.pytorch.org/whl/cu121" },
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

/** PEP 503 name normalization. */
function normalize(name: string): string {
    return name.toLowerCase().replace(/[-_.]+/g, "-");
}

/** `name -> version` of the pins of a uv export, and the hashes that follow each. */
function exportPins(text: string): { name: string; version: string; hashes: number }[] {
    const pins: { name: string; version: string; hashes: number }[] = [];
    for (const line of text.split(/\r?\n/)) {
        const pin = /^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)/.exec(line);
        // A prebuilt wheel (`name @ https://.../<dist>-<version>[+label]-<tags>.whl`) is read as its public version, the
        // way the audit and check_installed_set.py read it.
        const wheel =
            /^([A-Za-z0-9][A-Za-z0-9._-]*)\s+@\s+https:\/\/[^\s;]*\/[A-Za-z0-9_.]+-([0-9][A-Za-z0-9_.!]*)(?:\+[A-Za-z0-9_.]+)?-[^\s;/]+\.whl\b/.exec(
                line
            );
        if (pin) pins.push({ name: normalize(pin[1]), version: pin[2], hashes: 0 });
        else if (wheel) pins.push({ name: normalize(wheel[1]), version: wheel[2], hashes: 0 });
        else if (/^\s+--hash=sha256:[0-9a-f]{64}/.test(line) && pins.length) {
            pins[pins.length - 1].hashes += 1;
        }
    }
    return pins;
}

/** `name==version` lines of a frozen-set.txt. */
function frozenPins(text: string): Map<string, string> {
    const pins = new Map<string, string>();
    for (const line of text.split(/\r?\n/)) {
        if (line.trim() === "" || line.startsWith("#")) continue;
        const pin = /^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)$/.exec(line);
        expect([line, pin !== null]).toEqual([line, true]);
        pins.set(normalize(pin![1]), pin![2]);
    }
    return pins;
}

/** The quoted entries of a TOML array that starts at `key = [` and ends at the next line `]`. */
function tomlArray(text: string, key: string, after = 0): string[] {
    const start = text.indexOf(`${key} = [`, after);
    expect(start).toBeGreaterThanOrEqual(0);
    const end = text.indexOf("\n]", start);
    return text
        .slice(start, end)
        .split(/\r?\n/)
        .slice(1)
        .map((l) => l.replace(/^\s*#.*$/, "").trim())
        .filter((l) => l.startsWith('"'))
        .map((l) => JSON.parse(l.replace(/,\s*$/, "")) as string);
}

describe.each(CONVERTED)(
    "the $model image lock",
    ({ model, dockerfile, upstreamFiles, upstreamLinesNotLocked, torchIndex = CU128 }) => {
        const dir = path.join(CONTAINER_DIR, "locks", model);
        const read = (name: string) => fs.readFileSync(path.join(dir, name), "utf-8");
        const dockerText = fs.readFileSync(path.join(CONTAINER_DIR, dockerfile), "utf-8");
        const steps = instructions(dockerText);
        const pyproject = read("pyproject.toml");
        const frozen = frozenPins(read("frozen-set.txt"));
        const outOfLock: {
            name: string;
            version: string;
            commit?: string;
            treeSha256?: string;
            wheelSha256?: string;
        }[] = JSON.parse(read("out-of-lock.json")).packages;

        it("installs every Python package from a hashed lock, except the recorded source builds", () => {
            const installs = steps.filter((s) => /\bpip3?\s+install\b/.test(s));
            // [control] the Dockerfile has locked installs at all.
            expect(
                installs.filter((s) => /--require-hashes/.test(s)).length
            ).toBeGreaterThanOrEqual(3);
            const unlocked = installs.filter((s) => !/--require-hashes/.test(s));
            for (const step of installs.filter((s) => /--require-hashes/.test(s))) {
                expect(step).toMatch(/--no-deps/);
                expect(step).toMatch(
                    /-r \/opt\/locks\/[a-z0-9]+\/requirements(-build|-gpl)?\.lock\b/
                );
            }
            // What is left is a build from a pinned clone of the recorded out-of-lock packages: no resolver, no index.
            // One RUN can build several packages, so every `pip install` in it is checked on its own.
            const builds = unlocked.flatMap((step) =>
                step
                    .split(/\bpip3?\s+install\b/)
                    .slice(1)
                    .map((segment) => segment.split(/;|&&/)[0].trim())
            );
            for (const build of builds) {
                expect(build).toMatch(/--no-deps/);
                expect(build).toMatch(/--no-build-isolation/);
                // A clone directory, or a downloaded wheel file checked by its SHA-256 (out-of-lock.json `wheelSha256`).
                expect(build).toMatch(
                    /\s\/(opt|tmp)\/[A-Za-z0-9_-]+(\/[A-Za-z0-9_-]+)*(\/[A-Za-z0-9_.+-]+\.whl)?$/
                );
            }
            expect(builds.length).toBe(outOfLock.length);
            // The upgrade of pip, setuptools and wheel that resolved on build day is gone.
            expect(steps.filter((s) => /--upgrade/.test(s) && /\bpip\b/.test(s))).toEqual([]);
            // And no install reads the shared requirements file or an upstream one.
            expect(
                steps.filter(
                    (s) => /\bpip3?\s+install\b/.test(s) && /\brequirements\.txt\b/.test(s)
                )
            ).toEqual([]);
            expect(dockerText).not.toMatch(/COPY requirements\.txt/);
        });

        it("installs the build tools, then the set, then the GPL pair only when requested", () => {
            const at = (re: RegExp) => steps.findIndex((s) => /^RUN /.test(s) && re.test(s));
            const build = at(/requirements-build\.lock/);
            const main = at(/-r \/opt\/locks\/\w+\/requirements\.lock/);
            const gpl = at(/requirements-gpl\.lock/);
            const check = at(/check_installed_set\.py/);
            expect([build, main, gpl, check].every((i) => i >= 0)).toBe(true);
            expect(build).toBeLessThan(main);
            expect(main).toBeLessThan(gpl);
            expect(steps[gpl]).toMatch(/if \[ "\$ENABLE_GPL_MESH_REPAIR" = "true" \]/);
            // The set is checked after the last install of any kind, and pip check runs with it.
            const lastInstall = steps.reduce(
                (acc, s, i) => (/\bpip3?\s+install\b/.test(s) ? i : acc),
                -1
            );
            expect(check).toBeGreaterThan(lastInstall);
            expect(steps[check]).toMatch(/--gpl "\$ENABLE_GPL_MESH_REPAIR"/);
            expect(steps[check]).toMatch(/pip check/);
        });

        it("takes torch and its siblings from the explicit PyTorch index, and nothing else from it", () => {
            expect(pyproject).toContain(
                `[[tool.uv.index]]\nname = "${torchIndex.name}"\nurl = "${torchIndex.url}"\nexplicit = true`
            );
            const sources = /\[tool\.uv\.sources\]\n([\s\S]*)$/.exec(pyproject)![1];
            for (const line of sources.split(/\r?\n/).filter((l) => l.trim() !== "")) {
                expect(line).toMatch(
                    new RegExp(
                        `^(torch|torchvision|torchaudio|xformers) = \\{ index = "${torchIndex.name}" \\}$`
                    )
                );
            }
            const install = steps.find((s) => /-r \/opt\/locks\/\w+\/requirements\.lock/.test(s))!;
            expect(install).toContain(`--extra-index-url ${torchIndex.url}`);
        });

        it("copies the lock files, the recorded upstream files and the checks into the image", () => {
            const copies = steps.filter((s) => /^COPY /.test(s)).join("\n");
            for (const file of [
                "requirements-build.lock",
                "requirements.lock",
                "requirements-gpl.lock",
                "out-of-lock.json",
            ]) {
                expect(copies).toContain(`locks/${model}/${file}`);
                expect(fs.existsSync(path.join(dir, file))).toBe(true);
            }
            if (upstreamFiles.length > 0) {
                expect(copies).toContain(`locks/${model}/upstream /opt/locks/${model}/upstream`);
            }
            expect(copies).toContain(
                upstreamFiles.length > 0
                    ? "locks/tools/check_installed_set.py locks/tools/check_upstream_requirements.py /opt/locks/tools/"
                    : "locks/tools/check_installed_set.py /opt/locks/tools/"
            );
        });

        it("compares each recorded upstream file with the clone right after the clone", () => {
            if (upstreamFiles.length === 0) return;
            const clone = steps.findIndex(
                (s) =>
                    /git clone .*\/opt\/\w+ /.test(s) ||
                    /git clone https:\/\/github\.com\/[^ ]+ \/opt\/\w+ &&/.test(s)
            );
            expect(clone).toBeGreaterThanOrEqual(0);
            const step = steps[clone];
            expect(step).toMatch(/test "\$\(git rev-parse HEAD\)" = /);
            expect(step).toContain(
                `check_upstream_requirements.py --recorded /opt/locks/${model}/upstream --clone /opt/`
            );
            for (const file of upstreamFiles) {
                expect(step).toContain(file);
                expect(fs.existsSync(path.join(dir, "upstream", `${file}.recorded`))).toBe(true);
            }
        });

        it("lists every upstream requirement line as a dependency, apart from the ones it replaces", () => {
            const dependencies = tomlArray(pyproject, "dependencies");
            for (const file of upstreamFiles) {
                const lines = read(`upstream/${file}.recorded`)
                    .split(/\r?\n/)
                    .map((l) => l.trim())
                    .filter((l) => l !== "" && !l.startsWith("#"));
                expect(lines.length).toBeGreaterThan(0);
                for (const line of lines) {
                    if (upstreamLinesNotLocked.some((re) => re.test(line))) continue;
                    expect([line, dependencies.includes(line)]).toEqual([line, true]);
                }
            }
        });

        it("keeps the constraints, the frozen set and the exports equal", () => {
            const constraints = tomlArray(
                pyproject,
                "constraint-dependencies",
                pyproject.indexOf("# BEGIN frozen-set constraints")
            );
            expect(constraints).toEqual(
                [...frozen].map(
                    ([name]) => constraints.find((c) => normalize(c.split("==")[0]) === name)!
                )
            );
            expect(
                new Map(
                    constraints.map((c) => [
                        normalize(c.split("==")[0]),
                        c.split("==").slice(1).join("=="),
                    ])
                )
            ).toEqual(frozen);

            const locked = new Map<string, string>();
            for (const file of [
                "requirements-build.lock",
                "requirements.lock",
                "requirements-gpl.lock",
            ]) {
                const text = read(file);
                expect(text).toMatch(
                    /^# {4}uv export --frozen --no-emit-project --no-dev --no-annotate /m
                );
                expect(text).not.toMatch(/--no-hashes/);
                const pins = exportPins(text);
                expect(pins.length).toBeGreaterThan(0);
                expect(pins.filter((p) => p.hashes === 0).map((p) => p.name)).toEqual([]);
                for (const pin of pins) {
                    // One package, one version, across the image's locks.
                    expect([pin.name, locked.get(pin.name) ?? pin.version]).toEqual([
                        pin.name,
                        pin.version,
                    ]);
                    locked.set(pin.name, pin.version);
                }
            }
            // The locks hold exactly the frozen set: no package added, dropped or moved by resolving.
            expect(locked).toEqual(frozen);
        });

        it("builds each out-of-lock package from a pinned commit named in the Dockerfile, and none is also locked", () => {
            for (const entry of outOfLock) {
                expect(frozen.has(normalize(entry.name))).toBe(false);
                expect(entry.version).toMatch(/^\d/);
                // A prebuilt wheel no index the audit reads carries (kaolin) is checked by its SHA-256 instead of a commit.
                if (entry.wheelSha256 !== undefined) {
                    expect(entry.commit).toBeUndefined();
                    expect(entry.wheelSha256).toMatch(/^[0-9a-f]{64}$/);
                    expect(dockerText).toContain(`=${entry.wheelSha256}`);
                    expect(dockerText).toMatch(/sha256sum -c -/);
                    continue;
                }
                expect(entry.commit).toMatch(/^[0-9a-f]{40}$/);
                expect(dockerText).toContain(`=${entry.commit}`);
                // A package built from a clone whose files are hash-checked records the digest the build compares with.
                if (entry.treeSha256 !== undefined) {
                    expect(entry.treeSha256).toMatch(/^[0-9a-f]{64}$/);
                    expect(dockerText).toContain(`=${entry.treeSha256}`);
                    expect(dockerText).toMatch(
                        /git ls-files -z \| LC_ALL=C sort -z \| xargs -0 sha256sum \| sha256sum/
                    );
                }
            }
        });

        it("keeps the GPL pair out of the main lock and in its own group", () => {
            expect(exportPins(read("requirements.lock")).map((p) => p.name)).not.toEqual(
                expect.arrayContaining(["pymeshfix"])
            );
            expect(exportPins(read("requirements-gpl.lock")).map((p) => p.name)).toEqual(
                expect.arrayContaining(["pymeshfix", "pymeshlab"])
            );
        });
    }
);

describe("the Stable3DGen image's pillow and gradio", () => {
    const dir = path.join(CONTAINER_DIR, "locks", "stable3dgen");
    const read = (name: string) => fs.readFileSync(path.join(dir, name), "utf-8");
    const locked = new Map(
        ["requirements-build.lock", "requirements.lock", "requirements-gpl.lock"].flatMap((file) =>
            exportPins(read(file)).map((p) => [p.name, p.version] as [string, string])
        )
    );

    it("installs pillow 13 or later, with no pillow entry for the image in the audit allowlist", () => {
        expect(Number((locked.get("pillow") ?? "0").split(".")[0])).toBeGreaterThanOrEqual(13);
        const entries: { package: string; projects: string[] }[] = JSON.parse(
            fs.readFileSync(
                path.join(__dirname, "..", "..", "..", ".github", "python-audit-allowlist.json"),
                "utf-8"
            )
        ).entries;
        const project = "backendPipelines/genAi/imageTo3d/container/locks/stable3dgen";
        expect(
            entries.filter((e) => normalize(e.package) === "pillow" && e.projects.includes(project))
        ).toEqual([]);
    });

    it("leaves gradio out of the lock through an override, and the build imports the job's modules without it", () => {
        expect(locked.has("gradio")).toBe(false);
        expect(
            tomlArray(read("pyproject.toml"), "override-dependencies").filter((o) =>
                o.startsWith("gradio")
            )
        ).toEqual(["gradio ; sys_platform == 'never'"]);
        const dockerText = fs.readFileSync(
            path.join(CONTAINER_DIR, "Dockerfile.stable3dgen"),
            "utf-8"
        );
        const check = instructions(dockerText).find((s) =>
            s.includes("Stable3DGen job modules import")
        );
        expect(check).toBeDefined();
        expect(check).toContain("find_spec('gradio') is None");
        expect(check).toContain("'__main__.py'");
        expect(check).toContain("from hi3dgen.pipelines import Hi3DGenPipeline");
        expect(check).toContain("import hubconf");
        expect(check).toContain("/opt/torchhome/hub/facebookresearch_dinov2_main/hubconf.py");
        // After the code is copied into the image, so the entry module it loads is the one that ships.
        const codeCopy = dockerText.indexOf("COPY __init__.py __main__.py");
        expect(codeCopy).toBeGreaterThan(0);
        expect(dockerText.indexOf("Stable3DGen job modules import")).toBeGreaterThan(codeCopy);
    });
});

describe("the shared requirement files", () => {
    const root = path.join(__dirname, "..", "..", "..");
    const exemptions = (): string[] =>
        JSON.parse(
            fs.readFileSync(path.join(root, ".github", "python-lock-exemptions.json"), "utf-8")
        ).exemptions.map((e: { path: string }) => e.path);

    it("are gone with their exemptions, now that every image is converted", () => {
        const dockerfiles = fs
            .readdirSync(CONTAINER_DIR)
            .filter((f) => f.startsWith("Dockerfile."))
            .sort();
        expect(dockerfiles).toEqual(CONVERTED.map((c) => c.dockerfile).sort());
        expect(fs.existsSync(path.join(CONTAINER_DIR, "requirements.txt"))).toBe(false);
        expect(fs.existsSync(path.join(CONTAINER_DIR, "vams_utils", "requirements.txt"))).toBe(
            false
        );
        const gone = [
            "backendPipelines/genAi/imageTo3d/container/requirements.txt",
            "backendPipelines/genAi/imageTo3d/container/vams_utils/requirements.txt",
        ];
        expect(exemptions().filter((e) => gone.includes(e))).toEqual([]);
        // [control] the exemption list is read: the upstream-owned Splat Toolbox file is still on it.
        expect(exemptions()).toContain(
            "backendPipelines/3dRecon/splatToolbox/container/requirements.txt"
        );
    });

    it("are read by no Dockerfile and no pipeline test setting", () => {
        for (const file of CONVERTED.map((c) => c.dockerfile)) {
            const steps = instructions(fs.readFileSync(path.join(CONTAINER_DIR, file), "utf-8"));
            expect([
                file,
                steps.filter((s) => /^(COPY|ADD) .*\brequirements\.txt\b/.test(s)),
            ]).toEqual([file, []]);
        }
        const settings = fs.readFileSync(
            path.join(root, "backendPipelines", "ci", "suite-settings.json"),
            "utf-8"
        );
        expect(settings).not.toContain("imageTo3d/container/requirements.txt");
    });
});

describe("the SAM 3D conda environment", () => {
    const dir = path.join(CONTAINER_DIR, "locks", "sam3d");
    const explicit = fs.readFileSync(path.join(dir, "conda-explicit.txt"), "utf-8");
    const packages = explicit
        .split(/\r?\n/)
        .filter((l) => l !== "" && !l.startsWith("#") && l !== "@EXPLICIT");
    const dockerText = fs.readFileSync(path.join(CONTAINER_DIR, "Dockerfile.sam3d"), "utf-8");
    const steps = instructions(dockerText);

    it("names every package of upstream's environment file, each with a SHA-256", () => {
        expect(explicit.split(/\r?\n/)).toContain("@EXPLICIT");
        for (const line of packages) {
            expect([
                line,
                /^https:\/\/conda\.anaconda\.org\/conda-forge\/(linux-64|noarch)\/[A-Za-z0-9._+-]+\.(conda|tar\.bz2)#sha256:[0-9a-f]{64}$/.test(
                    line
                ),
            ]).toEqual([line, true]);
        }
        // `name=version=build` of each file, compared with the recorded default.yml.
        const fromFile = packages
            .map((l) => /\/([^/]+)\.(conda|tar\.bz2)#/.exec(l)![1])
            .map((f) => /^(.+)-([^-]+)-([^-]+)$/.exec(f)!)
            .map((m) => `${m[1]}=${m[2]}=${m[3]}`)
            .sort();
        const recorded = fs
            .readFileSync(
                path.join(dir, "upstream", "environments", "default.yml.recorded"),
                "utf-8"
            )
            .split(/\r?\n/)
            .filter((l) => /^ {2}- \S+=\S+=\S+$/.test(l))
            .map((l) => l.trim().slice(2))
            .sort();
        expect(recorded.length).toBeGreaterThan(200);
        expect(fromFile).toEqual(recorded);
    });

    it("pins every package with platform markers only, so the audit reads every line", () => {
        // pip-audit evaluates each line's environment marker against its own interpreter (Python 3.13 in CI) and skips a
        // line whose marker does not hold, without a finding. The image's interpreter is 3.11.0, so a Python-version
        // marker would leave lines unaudited; the lock's requires-python range keeps uv from writing one.
        for (const file of [
            "requirements-build.lock",
            "requirements.lock",
            "requirements-gpl.lock",
        ]) {
            const text = fs.readFileSync(path.join(dir, file), "utf-8");
            expect([file, /python_(full_)?version/.test(text)]).toEqual([file, false]);
        }
    });

    it("creates the environment from that file, after refusing a line without a checksum, and checks default.yml after the clone", () => {
        const create = steps.findIndex((s) => /micromamba create/.test(s));
        expect(create).toBeGreaterThanOrEqual(0);
        expect(steps[create]).toContain('-f "$f"');
        expect(steps[create]).toContain("f=/opt/locks/sam3d/conda-explicit.txt");
        expect(steps[create]).toMatch(/#sha256:\[0-9a-f\]\{64\}\)\$' "\$f"/);
        // The only environment created is the checksummed one; upstream's file is never resolved.
        expect(steps.filter((s) => /micromamba (create|install|env)/.test(s))).toEqual([
            steps[create],
        ]);
        expect(steps[create]).not.toContain("default.yml");
        const clone = steps.findIndex((s) => /git clone "\$SAM3D_REPO_URL" \/opt\/sam3d/.test(s));
        expect(clone).toBeGreaterThan(create);
        expect(steps[clone]).toContain("environments/default.yml");
    });
});
