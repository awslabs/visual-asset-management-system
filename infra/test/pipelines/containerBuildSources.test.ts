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
 *   3. The other three Fargate images (both pcPotreeViewer images and 3dThumbnail) and the three Lambda
 *      images of the system GenAI metadata pipeline (the 3dThumbnail Dockerfile.lambda, Blender, media)
 *      must likewise declare a non-root `USER`, in the stage that actually runs, along with the writable
 *      HOME and, where the image creates one, the scratch directory that user needs. The Dockerfile half of this is inert on its own: the shared Batch
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
import * as os from "os";
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
const COG_DOCKERFILE = path.join(
    PIPELINES_DIR,
    "conversion",
    "cogConvert",
    "container",
    "Dockerfile"
);

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

describe("the cogConvert image", () => {
    const text = fs.readFileSync(COG_DOCKERFILE, "utf-8");
    const lines = text.split(/\r?\n/);

    it("is pinned to a digest, not only a tag, in both stages", () => {
        const froms = lines.filter((l) => /^FROM\s/.test(l));
        expect(froms).toHaveLength(2);
        for (const from of froms) expect(from).toMatch(/@sha256:[0-9a-f]{64}/);
    });

    it("runs on a slim base: the runtime stage is not the conda build image", () => {
        const froms = lines.filter((l) => /^FROM\s/.test(l));
        expect(froms[0]).toContain("miniforge3");
        expect(froms[1]).not.toContain("miniforge3");
        expect(text).toContain("COPY --from=build /opt/cog /opt/cog");
    });

    it("installs the GDAL stack from a hash-listed explicit file and Python from the hash lock", () => {
        expect(text).toContain("--file conda-explicit.txt");
        expect(text).toContain("--require-hashes");
    });

    it("fetches nothing but its hash-checked package lists", () => {
        // The Image to 3D download rule below wants every curl or wget checked against a SHA-256 in the
        // same instruction. This image runs neither and clones nothing, so its only network inputs are
        // conda-explicit.txt and the hash lock, and each conda package line has to carry its SHA-256.
        // Its two base images are covered by the digest rule's walk over every pipeline Dockerfile.
        const steps = instructions(text).filter((s) => !/^\s*#/.test(s));
        expect(
            steps.filter((s) => DOWNLOAD.test(s) || /git clone|^ADD\s+https?:/i.test(s))
        ).toEqual([]);
        const packages = fs
            .readFileSync(path.join(path.dirname(COG_DOCKERFILE), "conda-explicit.txt"), "utf-8")
            .split(/\r?\n/)
            .filter((l) => /^https:\/\//.test(l));
        expect(packages.length).toBeGreaterThan(0);
        expect(packages.filter((l) => !/#[0-9a-f]{64}$/.test(l))).toEqual([]);
    });

    it("drops root before the entrypoint", () => {
        const userAt = lines.findIndex((l) => /^USER\s+\w/.test(l));
        expect(userAt).toBeGreaterThan(-1);
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
    // The three Lambda images of the system GenAI metadata pipeline. Each writes only under /tmp, which
    // the Lambda runtime provides, so none names a scratch directory here.
    {
        label: "3dThumbnail Lambda image",
        file: path.join(PIPELINES_DIR, "preview", "3dThumbnail", "container", "Dockerfile.lambda"),
    },
    {
        label: "system genAiMetadata blender",
        file: path.join(
            PIPELINES_DIR,
            "system",
            "genAiMetadata",
            "containers",
            "blender",
            "Dockerfile"
        ),
    },
    {
        label: "system genAiMetadata media",
        file: path.join(
            PIPELINES_DIR,
            "system",
            "genAiMetadata",
            "containers",
            "media",
            "Dockerfile"
        ),
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
    {
        label: "cogConvert",
        file: COG_DOCKERFILE,
        // The package the entrypoint imports at run time.
        readSources: ["./cog_convert_pipeline/"],
    },
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
 * publisher can re-point, and the Image to 3D CUDA bases are where that bit (R3-9). An earlier build
 * stage, `scratch` and a stage index are not image references; an `ARG`-supplied base is checked through
 * its default.
 */
/** Every Dockerfile under backendPipelines (`Dockerfile`, `Dockerfile.<name>`, `Dockerfile_<name>`). */
function pipelineDockerfiles(dir: string = PIPELINES_DIR): string[] {
    const out: string[] = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            if (["node_modules", "__pycache__", ".venv", ".git"].includes(entry.name)) continue;
            out.push(...pipelineDockerfiles(full));
        } else if (/^Dockerfile([._].+)?$/.test(entry.name) && full !== SPLAT_SYNCED_DOCKERFILE) {
            out.push(full);
        }
    }
    return out;
}

/**
 * Not a tracked file: `SplatToolboxConstruct` copies it from the pinned upstream on every synth and it is
 * gitignored, so it exists only in a tree that has been synthesized. `dockerfilePinAudit.ts` audits its pins
 * (`splatDockerfilePinAudit.test.ts`).
 */
const SPLAT_SYNCED_DOCKERFILE = path.join(
    PIPELINES_DIR,
    "3dRecon",
    "splatToolbox",
    "container",
    "Dockerfile"
);

/**
 * Pipeline Dockerfiles the digest rule does not check, each with the reason. A new Dockerfile is checked
 * from the day it is added; it can only be left out by naming it here.
 */
const DIGEST_RULE_EXEMPTIONS: { file: string; reason: string }[] = [
    {
        file: path.join(PIPELINES_DIR, "conversion", "3dBasic", "lambdaContainer", "Dockerfile"),
        reason:
            "The 3D Basic conversion image is reworked under #517, which owns that directory; the " +
            "container hardening leaves it as it is.",
    },
];

/** A Dockerfile's instructions: backslash continuations joined, comment lines dropped. */
function dockerInstructions(text: string): string[] {
    const out: string[] = [];
    let current: string | null = null;
    for (const raw of text.split(/\r?\n/)) {
        if (current === null && /^\s*#/.test(raw)) continue;
        const continues = /\\\s*$/.test(raw);
        const body = raw.replace(/\\\s*$/, "").trim();
        current = current === null ? body : `${current} ${body}`;
        if (continues) continue;
        if (current !== "") out.push(current);
        current = null;
    }
    if (current !== null && current !== "") out.push(current);
    return out;
}

/** The default of `ARG <name>=<value>` in a Dockerfile, or undefined. */
function argDefault(text: string, name: string): string | undefined {
    const m = new RegExp(`^ARG\\s+${name}=("?)(\\S+?)\\1\\s*$`, "im").exec(text);
    return m ? m[2] : undefined;
}

const DIGEST_SUFFIX = /@sha256:[0-9a-f]{64}$/;

/**
 * Why `ref` is not pinned by digest, or null when it is pinned or is not an image: an earlier build
 * stage, `scratch`, or a stage index. An `ARG`-supplied reference is checked through its default, whose
 * digest may itself come from another `ARG` (`...@${DIGEST}`).
 */
function unpinned(ref: string, stages: Set<string>, text: string): string | null {
    if (stages.has(ref.toLowerCase()) || ref.toLowerCase() === "scratch" || /^\d+$/.test(ref))
        return null;
    const arg = /^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$/.exec(ref);
    if (arg) {
        const value = argDefault(text, arg[1]) ?? "";
        const viaArg = /@\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$/.exec(value);
        const digest = viaArg ? argDefault(text, viaArg[1]) ?? "" : value.split("@")[1] ?? "";
        return /^sha256:[0-9a-f]{64}$/.test(digest)
            ? null
            : `\${${arg[1]}} = ${value || "(no default)"}`;
    }
    return DIGEST_SUFFIX.test(ref) ? null : ref;
}

/**
 * The image references in one Dockerfile that are not pinned by digest: every `FROM` operand (an untagged
 * one means `latest`), every `COPY --from=` and every `RUN --mount=...,from=` that names an image rather
 * than a stage. Instructions match in any case, and a registry may carry a port (`host:5000/img:1.0`).
 */
function unpinnedReferences(text: string): string[] {
    const offenders: string[] = [];
    const stages = new Set<string>();
    for (const step of dockerInstructions(text)) {
        const from = /^FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?\s*$/i.exec(step);
        if (from) {
            const why = unpinned(from[1], stages, text);
            if (why) offenders.push(why);
            if (from[2]) stages.add(from[2].toLowerCase());
            continue;
        }
        const copy = /^COPY\s+(?:--\S+\s+)*?--from=(\S+)/i.exec(step);
        if (copy) {
            const why = unpinned(copy[1], stages, text);
            if (why) offenders.push(why);
            continue;
        }
        if (/^RUN\s/i.test(step)) {
            for (const mount of step.matchAll(/--mount=(\S+)/gi)) {
                const source = /(?:^|,)from=([^,\s]+)/i.exec(mount[1]);
                const why = source ? unpinned(source[1], stages, text) : null;
                if (why) offenders.push(why);
            }
        }
    }
    return offenders;
}

describe("base images are not pulled from a floating tag", () => {
    const exempt = new Set(DIGEST_RULE_EXEMPTIONS.map((e) => e.file));
    const FOUND = pipelineDockerfiles();
    const ALL_DOCKERFILES = FOUND.filter((f) => !exempt.has(f));

    it("examines every pipeline Dockerfile but the named exemptions", () => {
        // Controls: the walk finds the tree's Dockerfiles, including every one the other rules here name,
        // and every exemption still names a file (a stale entry would hide nothing and mean nothing).
        expect(FOUND.length).toBeGreaterThanOrEqual(25);
        for (const f of [COORD_DOCKERFILE, ...NON_ROOT_IMAGES.map((i) => i.file)]) {
            expect(ALL_DOCKERFILES).toContain(f);
        }
        for (const e of DIGEST_RULE_EXEMPTIONS) {
            expect(FOUND).toContain(e.file);
            expect(e.reason.length).toBeGreaterThan(20);
        }
    });

    it.each(ALL_DOCKERFILES)("%s pins each base image by digest", (file) => {
        expect(unpinnedReferences(fs.readFileSync(file, "utf-8"))).toEqual([]);
    });

    it("the detector passes digest-pinned references and what is not an image", () => {
        const d = "a".repeat(64);
        const pinned = [
            `FROM --platform=linux/amd64 public.ecr.aws/docker/library/python:3.12-slim@sha256:${d} AS build`,
            `from registry.example.com:5000/team/img:1.0@sha256:${d} as second`,
            "FROM build",
            "FROM scratch",
            `COPY --from=ghcr.io/astral-sh/uv:0.8.12@sha256:${d} /uv /bin/`,
            "COPY --from=build /opt/venv /opt/venv",
            "copy --chown=10000:10000 --from=second /a /b",
            "COPY --from=0 /a /b",
            "RUN --mount=type=cache,target=/root/.cache pip install x",
            "RUN --mount=type=bind,from=build,source=/a,target=/b true",
            `RUN --mount=type=bind,from=alpine:3.20@sha256:${d},target=/b true`,
            `ARG D=sha256:${"d".repeat(64)}`,
            "ARG BASE_IMAGE=nvcr.io/x/y:1@${D}",
            "FROM ${BASE_IMAGE}",
            // A python `from x import y` inside a RUN is part of that instruction, not a FROM.
            "RUN python -c \\",
            "from os import path",
        ].join("\n");
        expect(unpinnedReferences(pinned)).toEqual([]);
    });

    // Each form, added to a scratch copy of a tracked, pinned Dockerfile, makes that copy fail.
    const SCRATCH_SOURCE = path.join(
        PIPELINES_DIR,
        "genAi",
        "nvidia",
        "cosmos",
        "reason",
        "container",
        "Dockerfile"
    );
    it.each([
        ["an untagged FROM", "FROM ubuntu", "ubuntu"],
        ["an untagged FROM ... AS", "FROM ubuntu AS tools", "ubuntu"],
        ["a lowercase from", "from ubuntu:22.04", "ubuntu:22.04"],
        [
            "a registry with a port",
            "FROM registry.example.com:5000/team/img:1.0",
            "registry.example.com:5000/team/img:1.0",
        ],
        [
            "a RUN --mount from an image",
            "RUN --mount=type=bind,from=alpine:3.20,source=/etc,target=/x true",
            "alpine:3.20",
        ],
        [
            "a bare COPY --from that names no stage",
            "COPY --from=alpine /etc/os-release /tmp/",
            "alpine",
        ],
        ["a tagged FROM", "FROM python:3.12-slim", "python:3.12-slim"],
        [
            "an unpinned COPY --from image",
            "COPY --from=ghcr.io/astral-sh/uv:0.8.12 /uv /bin/",
            "ghcr.io/astral-sh/uv:0.8.12",
        ],
        [
            "an ARG base without a digest",
            "ARG PROBE_BASE=nvcr.io/x/y:1\nFROM ${PROBE_BASE}",
            "${PROBE_BASE} = nvcr.io/x/y:1",
        ],
    ])("a scratch copy with %s fails", (_label, added, offender) => {
        const original = fs.readFileSync(SCRATCH_SOURCE, "utf-8");
        // [control] the tracked file itself passes.
        expect(unpinnedReferences(original)).toEqual([]);
        const dir = fs.mkdtempSync(path.join(os.tmpdir(), "digest-rule-"));
        const copy = path.join(dir, "Dockerfile");
        try {
            fs.writeFileSync(copy, `${original}\n${added}\n`);
            expect(unpinnedReferences(fs.readFileSync(copy, "utf-8"))).toEqual([offender]);
        } finally {
            fs.rmSync(dir, { recursive: true, force: true });
        }
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
        // The line that fixes the running process: an explicit ENTRYPOINT, or -- for a Lambda image that
        // inherits /lambda-entrypoint.sh from its pinned base and restates nothing -- the CMD naming the
        // handler. Either way a USER after it never applies to the process.
        const processAt = stage.findIndex((l) => /^(ENTRYPOINT|CMD)\s/.test(l));

        it("the runtime stage is isolated from the build stage", () => {
            // Non-vacuity of the slice itself, and the whole point of slicing. `conda-pack` appears only in
            // Dockerfile_PDAL's discarded build stage, so its absence from the slice proves the slice really
            // starts at the runtime FROM — without this, a slice that silently returned the whole file would
            // make every assertion below reproduce the stage-blind bug it exists to avoid.
            expect(stage.length).toBeGreaterThan(0);
            expect(stage.join("\n")).not.toContain("conda-pack");
            expect(processAt).toBeGreaterThan(-1);
        });

        it("declares a USER in the stage that runs", () => {
            expect(userAt).toBeGreaterThan(-1);
        });

        it("that USER is not root", () => {
            const name = (stage[userAt] ?? "").replace(/^USER\s+/, "").trim();
            expect(name).not.toBe("");
            expect(name).not.toMatch(/^(root|0)(:|$)/);
        });

        it("switches user after the last COPY and before the ENTRYPOINT or CMD", () => {
            // A USER ahead of the last COPY leaves the copied application root-owned; a USER after
            // the ENTRYPOINT/CMD never applies to the running process.
            const lastCopyAt = stage.reduce((acc, l, i) => (/^COPY\s/.test(l) ? i : acc), -1);
            expect(userAt).toBeGreaterThan(lastCopyAt);
            expect(userAt).toBeLessThan(processAt);
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
    {
        // Not deployed today (its construct asset is commented out), held to the same rules so it
        // builds what was reviewed the day it is wired back in.
        label: "cosmos predict v1",
        file: path.join(
            PIPELINES_DIR,
            "genAi",
            "nvidia",
            "cosmos",
            "predict",
            "containerv1",
            "Dockerfile"
        ),
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
        expect(UPSTREAM_CLONE_DOCKERFILES.length).toBe(13);
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
            // cosmos-predict1, and APEX inside the environment-setup RUN
            "cosmos predict v1=2",
            // MoGe, utils3d
            "image to 3d blenderagent=2",
            "image to 3d hunyuan3d=1",
            // SAM 3D Objects, and the DINOv2 checkout torch.hub loads its encoder from
            "image to 3d sam3d=2",
            // Stable3DGen, StableNormal, DINOv2
            "image to 3d stable3dgen=3",
            // Step1X-3D, nvdiffrast, pytorch3d
            "image to 3d step1x3d=3",
            // TRELLIS.2, CuMesh, FlexGEMM, nvdiffrast, utils3d
            "image to 3d trellis2=5",
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
        expect(SHA256_CHECK.test('python -c "assert os.path.getsize(p) > 150_000_000"')).toBe(
            false
        );
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

    it("carries torch.hub's trusted list when it sets TORCH_HOME, which uid 10000 cannot create", () => {
        // torch.hub touches $TORCH_HOME/hub/trusted_list on first use when it is missing; under the
        // root-owned hub directory that was a PermissionError and the Stable3DGen run failed.
        const torchHome = steps
            .map((s) => /^ENV TORCH_HOME=(\S+)/.exec(s.trim())?.[1])
            .find((v) => v !== undefined);
        if (torchHome === undefined) return;
        const created = steps.findIndex((s) => s.includes(`touch ${torchHome}/hub/trusted_list`));
        expect(created).toBeGreaterThan(-1);
        expect(created).toBeLessThan(userAt);
    });
});
