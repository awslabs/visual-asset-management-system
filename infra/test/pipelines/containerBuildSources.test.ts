/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Properties of the container BUILD sources under `backendPipelines/` — the buildspecs CodeBuild runs and
 * the Dockerfiles it builds. `containerImagePinning.test.ts` covers the CDK construct sources and their
 * `image:` references; neither of the files scanned here is reachable from a synthesized template, because
 * CodeBuild receives them as an `s3assets.Asset` whose contents CloudFormation never inspects.
 *
 * Two properties, both of which fail in a way a deploy reports as success:
 *
 *   1. A buildspec must derive the ECR registry host from the CDK-supplied `ECR_REPO_URI` rather than
 *      composing it from a literal DNS suffix. `${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_DEFAULT_REGION}.amazonaws.com`
 *      is correct in the commercial partition and wrong in every other one — `amazonaws.com.cn`,
 *      `amazonaws.eu` (EU Sovereign), the GovCloud and ISO suffixes. The push target in the same file
 *      already uses `ECR_REPO_URI`, so a composed login host can disagree with the registry actually being
 *      pushed to, and the failure appears as an authentication error inside CodeBuild in exactly the
 *      partitions there is no environment here to test in.
 *   2. The coordinateTransform image must stay digest-pinned, must not run as root, and must install the
 *      shared libraries the open3d wheel links against. Those libraries are absent from the slim base
 *      image, and their absence surfaces as `ImportError: libGL.so.1` at task start — after the pipeline
 *      has been dispatched, so the execution fails rather than the build.
 *   3. The other three Fargate images (both pcPotreeViewer images and 3dThumbnail) must likewise declare
 *      a non-root `USER`, in the stage that actually runs, along with the writable HOME and scratch
 *      directory that user needs. The Dockerfile half of this is inert on its own: the shared Batch
 *      construct used to set `user: "root"` on every Fargate container definition, which REPLACES the
 *      image's USER, so `fargateBatchContainerUser.test.ts` pins the absence of that override.
 *
 * Deliberately NOT a glob over `**\/Dockerfile`. `backendPipelines/3dRecon/splatToolbox/container/Dockerfile`
 * is gitignored and absent from a fresh checkout (the ".gitignore" pipeline-source-download block), so a
 * glob passes locally and fails in CI on a file that is not supposed to be there. The buildspecs, by
 * contrast, are all tracked, so globbing those is safe — and it is what makes a new pipeline's buildspec
 * covered the day it is added rather than the day someone remembers to add it here.
 */

import * as fs from "fs";
import * as path from "path";

const REPO_ROOT = path.join(__dirname, "..", "..", "..");
const PIPELINES_DIR = path.join(REPO_ROOT, "backendPipelines");
/** Add-on build sources shipped with the repository, scanned with the pipeline buildspecs. */
const ADDONS_DIR = path.join(REPO_ROOT, "backendAddons");

/** Every buildspec.yml under both build-source roots. */
function allBuildspecs(): string[] {
    return [...buildspecs(PIPELINES_DIR), ...buildspecs(ADDONS_DIR)];
}
const COORD_DOCKERFILE = path.join(
    PIPELINES_DIR,
    "conversion",
    "coordinateTransform",
    "container",
    "Dockerfile"
);

/** The Image to 3D models, one Dockerfile each (`Dockerfile.<model>`). */
const IMAGE_TO_3D_MODELS = [
    "blenderagent",
    "hunyuan3d",
    "sam3d",
    "stable3dgen",
    "step1x3d",
    "trellis2",
    "triposr",
] as const;
const IMAGE_TO_3D_CONTAINER_DIR = path.join(PIPELINES_DIR, "genAi", "imageTo3d", "container");
function imageTo3dDockerfile(model: string): string {
    return path.join(IMAGE_TO_3D_CONTAINER_DIR, `Dockerfile.${model}`);
}

/** Every buildspec.yml under `dir`, recursively. */
function buildspecs(dir: string): string[] {
    const out: string[] = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            if (entry.name === "node_modules" || entry.name === "__pycache__") continue;
            out.push(...buildspecs(full));
        } else if (entry.name === "buildspec.yml") {
            out.push(full);
        }
    }
    return out;
}

/** A login command that composes its host from a hardcoded DNS suffix, in any partition's suffix. */
const COMPOSED_HOST = /--password-stdin\s+\S*dkr\.ecr\.\S*amazonaws\.(com|com\.cn|eu)/;
/** The partition-agnostic form: take everything before the first "/" of the repository URI. */
const DERIVED_HOST = "--password-stdin ${ECR_REPO_URI%%/*}";

describe("container buildspecs are partition-portable", () => {
    const files = allBuildspecs();

    it("finds the buildspecs to scan", () => {
        // Control. An empty list would make both assertions below pass while checking nothing, which is
        // the failure mode of every file-scanning test.
        expect(files.length).toBeGreaterThanOrEqual(9);
    });

    it("the composed-host detector actually detects", () => {
        // Control for the regex itself, against a literal rather than against the tree. Asserting only
        // that the tree is clean cannot distinguish "no offenders" from "a pattern that matches nothing",
        // and this stays true after the offending lines are gone — which a git-based control would not.
        expect(
            COMPOSED_HOST.test(
                "- aws ecr get-login-password --region ${AWS_DEFAULT_REGION} | docker login --username AWS " +
                    "--password-stdin ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_DEFAULT_REGION}.amazonaws.com"
            )
        ).toBe(true);
        expect(
            COMPOSED_HOST.test(
                "- aws ecr get-login-password --region ${AWS_DEFAULT_REGION} | docker login --username AWS " +
                    "--password-stdin ${ECR_REPO_URI%%/*}"
            )
        ).toBe(false);
    });

    it("no buildspec composes the ECR host from a literal DNS suffix", () => {
        const offenders: string[] = [];
        for (const file of files) {
            for (const line of fs.readFileSync(file, "utf-8").split(/\r?\n/)) {
                if (/^\s*#/.test(line)) continue;
                if (COMPOSED_HOST.test(line)) {
                    offenders.push(path.relative(REPO_ROOT, file));
                }
            }
        }
        expect(offenders).toEqual([]);
    });

    it("every buildspec that logs in to ECR derives the host from ECR_REPO_URI", () => {
        // The converse of the check above: a buildspec could drop the bad host without gaining a good
        // one, which passes the negative assertion and breaks the build.
        const missing: string[] = [];
        for (const file of files) {
            const text = fs.readFileSync(file, "utf-8");
            if (!/get-login-password/.test(text)) continue;
            if (!text.includes(DERIVED_HOST)) missing.push(path.relative(REPO_ROOT, file));
        }
        expect(missing).toEqual([]);
    });

    it("a buildspec that derives the host also supplies ECR_REPO_URI to its push", () => {
        // `${ECR_REPO_URI%%/*}` expands to the empty string when the variable is unset, and `docker login`
        // with an empty registry silently targets Docker Hub. The variable is set by each pipeline's
        // CodeBuild construct; that it is also USED for the push is the in-file evidence it exists.
        const unsupplied: string[] = [];
        for (const file of files) {
            const text = fs.readFileSync(file, "utf-8");
            if (!text.includes(DERIVED_HOST)) continue;
            const uses = (text.match(/ECR_REPO_URI/g) || []).length;
            if (uses < 2) unsupplied.push(`${path.relative(REPO_ROOT, file)} (${uses} reference)`);
        }
        expect(unsupplied).toEqual([]);
    });
});

/** A buildspec that supplies its own IMAGE_TAG value, which is what makes the tag mutable. */
const DEFAULTED_IMAGE_TAG = /^\s*IMAGE_TAG:\s*\S/m;

describe("container buildspecs push an immutable tag", () => {
    const files = allBuildspecs();

    it("finds the buildspecs to scan", () => {
        // Control, as above: an empty list passes every assertion in this block.
        expect(files.length).toBeGreaterThanOrEqual(9);
    });

    it("the defaulted-tag detector actually detects", () => {
        // Control for the regex against a literal. `IMAGE_TAG` appears in every buildspec as
        // `${IMAGE_TAG}`, so a detector that merely looked for the name would match a clean file.
        expect(
            DEFAULTED_IMAGE_TAG.test('env:\n    variables:\n        IMAGE_TAG: "latest"\n')
        ).toBe(true);
        expect(
            DEFAULTED_IMAGE_TAG.test(
                'env:\n    variables:\n        CACHE_TAG: "latest"\n' +
                    '            - IMAGE_URI="${ECR_REPO_URI}:${IMAGE_TAG}"\n'
            )
        ).toBe(false);
    });

    it("no buildspec defaults IMAGE_TAG", () => {
        // The tag comes from the pipeline's CodeBuild project, which is the only place it can agree
        // with the tag the Batch job definition names. A default in the buildspec is the failure mode
        // that deploys green: a construct that forgets the variable pushes `latest`, the job
        // definition names a content hash, and every execution fails with CannotPullContainerError.
        const offenders = files
            .filter((file) => DEFAULTED_IMAGE_TAG.test(fs.readFileSync(file, "utf-8")))
            .map((file) => path.relative(REPO_ROOT, file));
        expect(offenders).toEqual([]);
    });

    it("every buildspec fails the build when IMAGE_TAG is not supplied", () => {
        // The converse. Removing the default alone leaves `${IMAGE_TAG}` expanding to the empty
        // string, so the push target becomes `<repo>:` — which docker rejects with a message about
        // the reference format rather than about the missing variable.
        const missing = files
            .filter((file) => !fs.readFileSync(file, "utf-8").includes('test -n "${IMAGE_TAG}"'))
            .map((file) => path.relative(REPO_ROOT, file));
        expect(missing).toEqual([]);
    });

    it("every buildspec pushes the cache alias beside the immutable tag", () => {
        // The layer cache is the reason a mutable alias is kept at all: a content-addressed tag never
        // pre-exists, so `--cache-from` against it is always a miss and the GPU images rebuild from
        // scratch every time. Pushing the alias is also what keeps the ECR maxImageCount lifecycle
        // rule from expiring the newest image.
        const offenders: string[] = [];
        for (const file of files) {
            const text = fs.readFileSync(file, "utf-8");
            const relative = path.relative(REPO_ROOT, file);
            if (!text.includes("docker push ${IMAGE_URI}"))
                offenders.push(`${relative}: no immutable push`);
            if (!text.includes("docker push ${CACHE_URI}"))
                offenders.push(`${relative}: no alias push`);
            if (!text.includes("--cache-from ${CACHE_URI}"))
                offenders.push(`${relative}: cache-from is not the alias`);
        }
        expect(offenders).toEqual([]);
    });
});

describe("the coordinateTransform image", () => {
    // Named explicitly rather than globbed: see the header note about the gitignored splat Dockerfile.
    const text = fs.readFileSync(COORD_DOCKERFILE, "utf-8");
    const lines = text.split(/\r?\n/);

    it("is pinned to a digest, not only a tag", () => {
        // `.*` rather than `\S*` between FROM and the image reference: the line carries a
        // `--platform=linux/amd64` flag first, and a non-whitespace class cannot cross the space
        // before the registry host.
        expect(text).toMatch(/^FROM\s+.*python:3\.11-slim@sha256:[0-9a-f]{64}/m);
    });

    it("installs the shared libraries the open3d wheel links against", () => {
        // Determined from the wheel's DT_NEEDED entries against the pinned base image's own layers:
        // libstdc++ and libudev are already present, these three are not, and libtbb/libc++ ship inside
        // the wheel. Each is asserted separately so a partial regression names the missing one.
        for (const pkg of ["libgl1", "libgomp1", "libx11-6"]) {
            expect(text).toContain(pkg);
        }
    });

    it("drops root before the entrypoint", () => {
        const userAt = lines.findIndex((l) => /^USER\s+\w/.test(l));
        expect(userAt).toBeGreaterThan(-1);
        // Order is the substance: a USER before the last COPY leaves the copied files root-owned, and a
        // USER after ENTRYPOINT is a no-op for the running process.
        const lastCopyAt = lines.reduce((acc, l, i) => (/^COPY\s/.test(l) ? i : acc), -1);
        const entrypointAt = lines.findIndex((l) => /^ENTRYPOINT\s/.test(l));
        expect(userAt).toBeGreaterThan(lastCopyAt);
        if (entrypointAt > -1) expect(userAt).toBeLessThan(entrypointAt);
    });
});

/**
 * The remaining Fargate images must drop root too, and the assertion has to be PER STAGE.
 *
 * The block above computes its indices over the whole file, which is correct for the single-stage
 * coordinateTransform Dockerfile and blind to a stage boundary in a multi-stage one. `Dockerfile_PDAL`
 * carried its account creation in the FIRST stage and its `USER` in the runtime stage, both commented
 * out; uncommenting both — the obvious way to attempt this fix — satisfies the whole-file ordering check
 * (the `USER` does sit after the last `COPY` and before the `ENTRYPOINT`) and fails `docker build` with
 * "unable to find user appuser", because a user created in a discarded stage does not exist in the final
 * image. Every assertion below is therefore made against the slice from the LAST `FROM` onwards, and one
 * of them requires the account to be created in that same slice.
 *
 * Named explicitly, not globbed, for the same reason as the block above.
 */
const NON_ROOT_IMAGES: {
    label: string;
    file: string;
    scratchDir?: string;
    readSources?: string[];
}[] = [
    {
        label: "pcPotreeViewer PDAL",
        file: path.join(PIPELINES_DIR, "preview", "pcPotreeViewer", "container", "Dockerfile_PDAL"),
        // No WORKDIR in the runtime stage, so create_dir(["tmp", ...]) resolves against / and lands in
        // the 1777 /tmp. Nothing to chown.
    },
    {
        label: "pcPotreeViewer Potree",
        file: path.join(
            PIPELINES_DIR,
            "preview",
            "pcPotreeViewer",
            "container",
            "Dockerfile_Potree"
        ),
        scratchDir: "/PotreeConverterBuild/tmp",
    },
    {
        label: "3dThumbnail",
        file: path.join(PIPELINES_DIR, "preview", "3dThumbnail", "container", "Dockerfile"),
        scratchDir: "/app/tmp",
    },
    {
        label: "videoSopBom",
        file: path.join(PIPELINES_DIR, "genAi", "videoSopBom", "container", "Dockerfile"),
        scratchDir: "/app/tmp",
        // The package the entrypoint imports at run time.
        readSources: ["./video_sop_bom_pipeline/"],
    },
    // The seven Image to 3D images run as uid 10000 (the #327 design). Their scratch space is /tmp and
    // their read-bit normalization is one chmod over /opt/ml/code, asserted in the Image to 3D block below.
    ...IMAGE_TO_3D_MODELS.map((model) => ({
        label: `Image to 3D ${model}`,
        file: imageTo3dDockerfile(model),
    })),
];

/**
 * Every base image these Dockerfiles pull must be pinned by digest, not only by tag.
 *
 * A tag is a moving reference: a rebuild months apart can produce a different image from identical
 * sources, so an image that stops working cannot be told apart from a change in this repository. The
 * coordinateTransform block above already asserts this for its own image; this widens it to the rest,
 * which is the half of `S4-PIPELINES-053` left open by design (owner question 92, option A).
 *
 * The rule covers every registry and image, not only the docker-library python bases it started with:
 * a version-looking tag such as `condaforge/...:24.9.2-0` or `nvidia/cuda:12.8.1-...` is still a tag its
 * publisher can re-point, and the Image to 3D CUDA bases are where that bit (R3-9). A named build stage
 * (`FROM <stage>`) and an `ARG`-supplied base are not image references and are not matched.
 */
// `@` is excluded from BOTH groups deliberately. With a plain `(\S+)` the image group matched greedily
// through `…python:3.12-slim@sha256`, leaving the 64 hex characters to satisfy the tag group — so a
// digest-pinned line was reported as floating, and the rule would have failed the very lines it exists
// to require. The positive control below is what caught that. The optional trailing `AS <name>` is what
// lets a builder stage's line be checked at all: without it the `$` anchor made every named stage pass.
const FLOATING_TAG =
    /^FROM\s+(?:--\S+\s+)*([^\s:@$]+(?:\/[^\s:@]+)*):([A-Za-z0-9._-]+)(?:\s+(?:AS|as)\s+\S+)?\s*$/;

describe("base images are not pulled from a floating tag", () => {
    const ALL_DOCKERFILES = [COORD_DOCKERFILE, ...NON_ROOT_IMAGES.map((i) => i.file)];

    it("examines every Dockerfile it names", () => {
        // Control: a typo'd path would otherwise make the rule below pass over an empty set.
        expect(ALL_DOCKERFILES.length).toBeGreaterThanOrEqual(12);
        for (const f of ALL_DOCKERFILES) expect(fs.existsSync(f)).toBe(true);
    });

    it.each(ALL_DOCKERFILES)("%s pins each base image by digest", (file) => {
        const text = fs.readFileSync(file, "utf-8");
        const offenders: string[] = [];
        for (const line of text.split(/\r?\n/)) {
            if (!/^FROM\s/i.test(line)) continue;
            const m = FLOATING_TAG.exec(line.trim());
            if (!m) continue; // already carries @sha256, or is a named build stage
            const [, image, tag] = m;
            offenders.push(`${image}:${tag}`);
        }
        expect(offenders).toEqual([]);
    });

    it("the detector recognises a floating reference", () => {
        // Positive control. Without it a regex that matched nothing would pass every case above.
        const sample = "FROM --platform=linux/amd64 public.ecr.aws/docker/library/python:3.12-slim";
        const m = FLOATING_TAG.exec(sample.trim());
        expect(m).not.toBeNull();
        expect(m![2]).toBe("3.12-slim");
        // And that a digest-pinned line is NOT reported, which is what makes the rule discriminating.
        expect(FLOATING_TAG.exec(`${sample}@sha256:${"a".repeat(64)}`.trim())).toBeNull();
        // A named stage is checked too, and any registry's tag counts, not only python's.
        expect(
            FLOATING_TAG.exec("FROM --platform=linux/amd64 condaforge/mambaforge:24.9.2-0 as build")
        ).not.toBeNull();
        expect(FLOATING_TAG.exec("FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04")).not.toBeNull();
        expect(
            FLOATING_TAG.exec(`FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04@sha256:${"b".repeat(64)}`)
        ).toBeNull();
        // A build stage reference and an ARG-supplied base are not image tags.
        expect(FLOATING_TAG.exec("FROM builder")).toBeNull();
        expect(FLOATING_TAG.exec("FROM ${BASE_IMAGE}")).toBeNull();
    });
});

/** The lines from the last `FROM` onwards — the only stage that contributes to the final image. */
function runtimeStage(text: string): string[] {
    const lines = text.split(/\r?\n/);
    // Case-sensitive on purpose: a `python -c` continuation line can start with `from x import y`, and
    // matching it as a stage boundary sliced the SAM 3D and TRELLIS.2 images in the middle of a RUN.
    const lastFrom = lines.reduce((acc, l, i) => (/^FROM\s/.test(l) ? i : acc), -1);
    expect(lastFrom).toBeGreaterThan(-1);
    return lines.slice(lastFrom);
}

describe.each(NON_ROOT_IMAGES)(
    "the $label image runs as a non-root user",
    ({ file, scratchDir, readSources }) => {
        const text = fs.readFileSync(file, "utf-8");
        const stage = runtimeStage(text);
        // The LAST USER is the one the container runs as. An image built on a base that drops root
        // (micromamba) switches back to root for its installs first, and that earlier USER is not the
        // runtime one.
        const userAt = stage.reduce((acc, l, i) => (/^USER\s+\S/.test(l) ? i : acc), -1);

        it("the runtime stage is isolated from the build stage", () => {
            // Non-vacuity of the slice itself, and the whole point of slicing. `conda-pack` appears only in
            // Dockerfile_PDAL's discarded build stage, so its absence from the slice proves the slice really
            // starts at the runtime FROM — without this, a slice that silently returned the whole file would
            // make every assertion below reproduce the stage-blind bug it exists to avoid.
            expect(stage.length).toBeGreaterThan(0);
            expect(stage.join("\n")).not.toContain("conda-pack");
            expect(stage.some((l) => /^ENTRYPOINT\s/.test(l))).toBe(true);
        });

        it("declares a USER in the stage that runs", () => {
            expect(userAt).toBeGreaterThan(-1);
        });

        it("that USER is not root", () => {
            const name = (stage[userAt] ?? "").replace(/^USER\s+/, "").trim();
            expect(name).not.toBe("");
            expect(name).not.toMatch(/^(root|0)(:|$)/);
        });

        it("switches user after the last COPY and before the ENTRYPOINT", () => {
            // A USER ahead of the last COPY leaves the copied application root-owned; a USER after
            // ENTRYPOINT never applies to the running process.
            const lastCopyAt = stage.reduce((acc, l, i) => (/^COPY\s/.test(l) ? i : acc), -1);
            const entrypointAt = stage.findIndex((l) => /^ENTRYPOINT\s/.test(l));
            expect(userAt).toBeGreaterThan(lastCopyAt);
            expect(userAt).toBeLessThan(entrypointAt);
        });

        it("creates the account it switches to, in the same stage", () => {
            // A user created in a discarded stage does not exist in the final image, and `USER` on an unknown
            // account fails the BUILD with "unable to find user" — the trap the commented-out lines this
            // change replaced were sitting in.
            const name = (stage[userAt] ?? "")
                .replace(/^USER\s+/, "")
                .trim()
                .split(":")[0];
            const creation = stage.filter((l) => /\b(adduser|useradd)\b/.test(l)).join("\n");
            expect(creation).toContain(name);
        });

        it("gives that account a writable HOME", () => {
            // conda run, boto3, PyVista/VTK and matplotlib all read HOME and write under it. A system
            // account whose home does not exist fails at container start, not at build.
            const joined = stage.join("\n");
            expect(joined).toMatch(/ENV\s+HOME=|--create-home|\s-h\s/);
        });

        if (scratchDir) {
            it(`hands ${scratchDir} to that account`, () => {
                // The pipelines create their working directories with a RELATIVE path, so they resolve
                // against the stage's WORKDIR. Where that is root-owned the directory has to be pre-created
                // and chowned, or the first mkdir fails with EACCES after the job has been dispatched.
                const joined = stage.join("\n");
                expect(joined).toContain(scratchDir);
                expect(joined).toMatch(/\bchown\b/);
            });
        }

        if (readSources) {
            it.each(readSources)(
                "normalizes the read bits of %s right after copying it",
                (source) => {
                    // docker COPY keeps the build host's mode bits, so a checkout made under umask
                    // 077 or 027 copies root-owned 600/640 files the non-root USER cannot read, and
                    // the job fails at import while the image builds green (backendPipelines/CLAUDE.md
                    // "A Non-Root Container Normalizes Read Bits on the Source It COPYs").
                    const steps = instructions(stage.join("\n")).filter(
                        (step) => step.trim() !== "" && !/^\s*#/.test(step)
                    );
                    const copyAt = steps.findIndex(
                        (step) => /^COPY\s/.test(step) && step.trim().split(/\s+/).pop() === source
                    );
                    expect(copyAt).toBeGreaterThan(-1);
                    const next = steps[copyAt + 1] ?? "";
                    expect(next).toMatch(/^RUN\s+chmod\s+-R\s+a\+rX\s/);
                    const targets = next
                        .trim()
                        .split(/\s+/)
                        .slice(4)
                        .map((target) => path.posix.basename(target));
                    expect(targets).toContain(path.posix.basename(source));
                }
            );
        }
    }
);

/**
 * The GPU images that clone an upstream repository at build time -- the five NVIDIA images and all seven
 * Image to 3D images -- must each clone a fixed revision rather than whatever the default branch
 * holds that day.
 *
 * An unpinned clone makes the framework version part of WHEN the image was built rather than of which
 * VAMS commit built it: two builds of the same commit ship different inference code, `uv sync --locked`
 * resolves against a different lockfile, and the transfer container's entrypoint patch — which matches
 * an exact upstream source line — silently degrades to a warning once upstream edits that line.
 *
 * The property a per-line check gets wrong is the first one. A `git checkout` in a LATER `RUN` is a
 * different layer and does not pin the clone that already happened, so every assertion here is made
 * against LOGICAL instructions, with backslash continuations joined.
 *
 * Named explicitly rather than globbed, for the reason in the file header: the splat Dockerfile is
 * gitignored, so a glob passes locally and fails in CI.
 */
const UPSTREAM_CLONE_DOCKERFILES: { label: string; file: string }[] = [
    {
        label: "cosmos 3",
        file: path.join(PIPELINES_DIR, "genAi", "nvidia", "cosmos", "3", "container", "Dockerfile"),
    },
    {
        label: "cosmos predict v2.5",
        file: path.join(
            PIPELINES_DIR,
            "genAi",
            "nvidia",
            "cosmos",
            "predict",
            "containerv2.5",
            "Dockerfile"
        ),
    },
    {
        label: "cosmos transfer",
        file: path.join(
            PIPELINES_DIR,
            "genAi",
            "nvidia",
            "cosmos",
            "transfer",
            "container",
            "Dockerfile"
        ),
    },
    {
        label: "cosmos reason",
        file: path.join(
            PIPELINES_DIR,
            "genAi",
            "nvidia",
            "cosmos",
            "reason",
            "container",
            "Dockerfile"
        ),
    },
    {
        label: "gr00t",
        file: path.join(PIPELINES_DIR, "genAi", "nvidia", "gr00t", "container", "Dockerfile"),
    },
    // All seven Image to 3D images clone at least one upstream repository.
    ...IMAGE_TO_3D_MODELS.map((model) => ({
        label: `image to 3d ${model}`,
        file: imageTo3dDockerfile(model),
    })),
];

/** Logical Dockerfile instructions: backslash continuations joined, so one RUN is one entry. */
function instructions(text: string): string[] {
    const out: string[] = [];
    let current: string | null = null;
    for (const raw of text.split(/\r?\n/)) {
        const continues = /\\\s*$/.test(raw);
        const body = raw.replace(/\\\s*$/, "").trimEnd();
        current = current === null ? body : current + " " + body.trim();
        if (continues) continue;
        out.push(current);
        current = null;
    }
    if (current !== null) out.push(current);
    return out;
}

/** `ARG NAME=value` declarations, so a `${NAME}` revision can be resolved to its default. */
function argDefaults(text: string): Record<string, string> {
    const out: Record<string, string> = {};
    for (const match of text.matchAll(/^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)=(\S+)/gm)) {
        out[match[1]] = match[2];
    }
    return out;
}

/** Clone instructions of one Dockerfile, as logical instructions. */
function cloneInstructions(text: string): string[] {
    return instructions(text).filter((i) => /^RUN\s.*git clone/.test(i));
}

const FULL_SHA = /^[0-9a-f]{40}$/;
const MOVING_COPY_FROM = /COPY\s+--from=\S+:(latest|main|master)\b/;
const UNVERSIONED_AWS_CLI = /awscli-exe-linux-x86_64\.zip/;

describe("the container images that clone an upstream repository pin its revision", () => {
    it("[control] the Dockerfiles are all present and all clone something", () => {
        // Both halves matter. A missing file would make every assertion below pass on an empty string,
        // and an image that stopped cloning would make the pin assertions vacuously true.
        expect(UPSTREAM_CLONE_DOCKERFILES.length).toBe(12);
        const cloneCounts = UPSTREAM_CLONE_DOCKERFILES.map(({ label, file }) => {
            expect(fs.existsSync(file)).toBe(true);
            return `${label}=${cloneInstructions(fs.readFileSync(file, "utf-8")).length}`;
        });
        expect(cloneCounts).toEqual([
            "cosmos 3=1",
            "cosmos predict v2.5=1",
            "cosmos transfer=1",
            "cosmos reason=1",
            "gr00t=1",
            // MoGe
            "image to 3d blenderagent=1",
            "image to 3d hunyuan3d=1",
            // SAM 3D Objects, and the DINOv2 checkout torch.hub loads its encoder from
            "image to 3d sam3d=2",
            // Stable3DGen, StableNormal, DINOv2
            "image to 3d stable3dgen=3",
            // Step1X-3D, nvdiffrast
            "image to 3d step1x3d=2",
            // TRELLIS.2, CuMesh, FlexGEMM, nvdiffrast
            "image to 3d trellis2=4",
            // TripoSR, torchmcubes
            "image to 3d triposr=2",
        ]);
    });

    it("[control] the instruction joiner really joins continuations", () => {
        // The property the pin assertion depends on. Without joining, a clone and its checkout look like
        // separate instructions and the pin reads as absent; with over-eager joining, a checkout in a
        // genuinely separate RUN would read as present, which is the mistake this block exists to catch.
        const joined = instructions(
            "RUN git clone ${REPO} /opt/x && \\\n    git checkout --detach ${C}\nRUN echo done"
        );
        expect(joined).toEqual([
            "RUN git clone ${REPO} /opt/x && git checkout --detach ${C}",
            "RUN echo done",
        ]);
    });

    it("every clone checks out a 40-hex commit and verifies it landed", () => {
        const offenders: string[] = [];
        for (const { label, file } of UPSTREAM_CLONE_DOCKERFILES) {
            const text = fs.readFileSync(file, "utf-8");
            const args = argDefaults(text);
            for (const instruction of cloneInstructions(text)) {
                const checkout = instruction.match(
                    /git checkout (?:--detach )?\$\{([A-Za-z_][A-Za-z0-9_]*)\}/
                );
                if (!checkout) {
                    offenders.push(
                        `${label}: clone with no checkout of an ARG in the same instruction`
                    );
                    continue;
                }
                const revision = args[checkout[1]];
                if (!revision || !FULL_SHA.test(revision)) {
                    // A branch or tag name moves, so a `git checkout` of one is not a pin.
                    offenders.push(
                        `${label}: ${checkout[1]} default is "${revision}", not a 40-hex commit`
                    );
                }
                // The equality test is what makes the pin enforced rather than decorative: without it a
                // `--build-arg <ARG>=main` builds and ships while reporting a pin.
                if (!instruction.includes(`test "$(git rev-parse HEAD)" = "\${${checkout[1]}}"`)) {
                    offenders.push(`${label}: no rev-parse equality test against ${checkout[1]}`);
                }
                // The resolved revision has to reach the running container, or a run cannot be tied to
                // the code it ran.
                if (!instruction.includes("VAMS_UPSTREAM_COMMIT")) {
                    offenders.push(`${label}: does not record VAMS_UPSTREAM_COMMIT`);
                }
            }
        }
        expect(offenders).toEqual([]);
    });

    it("no image copies from a moving tag", () => {
        // `COPY --from=ghcr.io/astral-sh/uv:latest` pulls a different binary on every build, and it is
        // not covered by the clone assertions above.
        const offenders: string[] = [];
        for (const { label, file } of UPSTREAM_CLONE_DOCKERFILES) {
            for (const instruction of instructions(fs.readFileSync(file, "utf-8"))) {
                if (MOVING_COPY_FROM.test(instruction)) {
                    offenders.push(`${label}: ${instruction.trim().slice(0, 80)}`);
                }
            }
        }
        expect(offenders).toEqual([]);
    });

    it("the AWS CLI installer URL carries a version", () => {
        // A second unpinned build input on the same rebuild. Its failure mode is milder than the
        // clone's, but it is the same class and the same one-line fix.
        const offenders: string[] = [];
        for (const { label, file } of UPSTREAM_CLONE_DOCKERFILES) {
            const text = fs.readFileSync(file, "utf-8");
            if (!/awscli-exe-linux-x86_64/.test(text)) continue;
            if (UNVERSIONED_AWS_CLI.test(text)) {
                offenders.push(`${label}: unversioned awscli-exe-linux-x86_64.zip`);
            }
        }
        expect(offenders).toEqual([]);
    });

    it("the moving-tag and unversioned-installer detectors actually detect", () => {
        // Controls against literals, so "no offenders" cannot be confused with "a pattern that matches
        // nothing". Both stay true after the offending lines are gone.
        expect(
            MOVING_COPY_FROM.test("COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv")
        ).toBe(true);
        expect(
            MOVING_COPY_FROM.test("COPY --from=ghcr.io/astral-sh/uv:0.8.12 /uv /usr/local/bin/uv")
        ).toBe(false);
        expect(
            UNVERSIONED_AWS_CLI.test(
                'curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip"'
            )
        ).toBe(true);
        expect(
            UNVERSIONED_AWS_CLI.test(
                'curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64-${AWS_CLI_VERSION}.zip"'
            )
        ).toBe(false);
    });
});

/**
 * What the Image to 3D images download besides their clones -- the Blender tarball, the Codex and bwrap
 * release binaries, the u2net and DINOv2 weights, the flash-attn wheels, the AWS CLI -- is checked against
 * a SHA-256 in the same instruction that downloads it, so a changed or substituted file fails the build.
 * A size check, which is what the weights had, passes any file of the right size.
 *
 * A download is a `curl` or `wget` with an option after it; a package NAMED curl in an apt-get list is
 * not one, which is why the detector does not match on the word alone.
 */
const DOWNLOAD = /\b(?:curl|wget)\s+-/;
const SHA256_CHECK = /sha256sum\s+(?:-c|--check)\b/;

describe("the Image to 3D images check what they download", () => {
    const images = IMAGE_TO_3D_MODELS.map((model) => ({
        model,
        steps: instructions(fs.readFileSync(imageTo3dDockerfile(model), "utf-8")).filter(
            (step) => !/^\s*#/.test(step)
        ),
    }));

    it("[control] every image downloads something, and the detectors tell a download from a package", () => {
        for (const { model, steps } of images) {
            expect([model, steps.some((s) => DOWNLOAD.test(s))]).toEqual([model, true]);
        }
        expect(DOWNLOAD.test("RUN apt-get install -y curl git unzip")).toBe(false);
        expect(DOWNLOAD.test('RUN curl -fsSL -o /tmp/x "https://example.com/x"')).toBe(true);
        expect(SHA256_CHECK.test('echo "${SHA}  /tmp/x" | sha256sum -c -')).toBe(true);
        expect(SHA256_CHECK.test("python -c \"assert os.path.getsize(p) > 150_000_000\"")).toBe(false);
    });

    it("every download is checked against a SHA-256 in the same instruction", () => {
        const offenders: string[] = [];
        for (const { model, steps } of images) {
            for (const step of steps) {
                if (DOWNLOAD.test(step) && !SHA256_CHECK.test(step)) {
                    offenders.push(`${model}: ${step.trim().slice(0, 100)}`);
                }
            }
        }
        expect(offenders).toEqual([]);
    });

    it("every pinned digest is a full SHA-256", () => {
        const offenders: string[] = [];
        for (const model of IMAGE_TO_3D_MODELS) {
            const text = fs.readFileSync(imageTo3dDockerfile(model), "utf-8");
            for (const [name, value] of Object.entries(argDefaults(text))) {
                if (/SHA256$/.test(name) && !/^[0-9a-f]{64}$/.test(value)) {
                    offenders.push(`${model}: ${name}=${value}`);
                }
            }
        }
        expect(offenders).toEqual([]);
    });
});

/**
 * The Image to 3D images run as uid 10000 (the block above checks the USER), and three more things make
 * that work: the pipeline code is readable by that user whatever umask the build host had, the HuggingFace
 * pin guard ships in the image, and the build proves the guard installs against the huggingface_hub that
 * image resolved.
 */
describe.each(IMAGE_TO_3D_MODELS)("the Image to 3D %s image", (model) => {
    const stage = runtimeStage(fs.readFileSync(imageTo3dDockerfile(model), "utf-8"));
    const steps = instructions(stage.join("\n")).filter((s) => s.trim() !== "" && !/^\s*#/.test(s));
    const lastCodeCopy = steps.reduce(
        (acc, s, i) => (/^COPY\s.*\s\/opt\/ml\/code\S*\s*$/.test(s) ? i : acc),
        -1
    );
    const userAt = steps.reduce((acc, s, i) => (/^USER\s/.test(s) ? i : acc), -1);

    it("runs as uid 10000", () => {
        expect(steps[userAt]?.trim()).toBe("USER 10000:10000");
    });

    it("normalizes the read bits of the pipeline code after its last COPY and before USER", () => {
        const chmodAt = steps.findIndex(
            (s, i) => i > lastCodeCopy && /chmod -R a\+rX \/opt\/ml\/code(\s|$)/.test(s)
        );
        expect(lastCodeCopy).toBeGreaterThan(-1);
        expect(chmodAt).toBeGreaterThan(lastCodeCopy);
        expect(chmodAt).toBeLessThan(userAt);
    });

    it("ships the HuggingFace pin guard and checks at build time that it installs", () => {
        expect(steps.some((s) => /^COPY hfguard \/opt\/ml\/code\/hfguard\s*$/.test(s))).toBe(true);
        const check = steps.findIndex((s) => s.includes("from hfguard import hf_pins"));
        expect(check).toBeGreaterThan(lastCodeCopy);
        expect(check).toBeLessThan(userAt);
    });
});
