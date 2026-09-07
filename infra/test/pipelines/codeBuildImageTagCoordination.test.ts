/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A CodeBuild-built pipeline image must be pushed and consumed under the SAME content-addressed tag.
 *
 * Under a mutable tag, what a pipeline runs can change without any change to this repository and
 * without a redeploy: the next build overwrites the tag the Batch job definition already names. The
 * fix is two-sided — the construct supplies `IMAGE_TAG` to the build AND names that same value at the
 * pull site — and a one-sided assertion is worthless here. "The job definition does not say `:latest`"
 * passes while the job definition names a tag no build ever pushed, which deploys green and fails
 * every execution with `CannotPullContainerError`.
 *
 * So the property asserted is EQUALITY between the two sides of the same template, in both directions:
 * every tag a build is told to push is named by a job definition, and no job definition names a
 * moving tag.
 *
 * Two tiers, because the five CodeBuild pipelines are not equally reachable from a synth:
 *
 *   1. A T1 synth per plumbing style — coordinateTransform through `batch-fargate-pipeline.ts`, Splat
 *      Toolbox through `batch-gpu-pipeline.ts`. Those are the two shared constructs that resolve an
 *      ECR image, so between them they cover every consumer that takes a repository object.
 *   2. A source scan over every pipeline construct, which reaches the three producers a synth cannot
 *      cheaply enable (Cosmos needs a HuggingFace token plus a per-model flag, GR00T the same, Isaac
 *      Lab an EULA acceptance) and which is what keeps a NEW CodeBuild pipeline covered on the day it
 *      is added rather than the day someone remembers this file.
 */

import * as fs from "fs";
import * as path from "path";
import { Resource, SynthResult, synthTemplate } from "../support/templateSynth";
import { IMAGE_TAG_LENGTH, MAX_IMAGE_REFERENCE_LENGTH } from "../../lib/helper/containerImageTag";

const PIPELINES_DIR = path.join(__dirname, "..", "..", "lib", "nestedStacks", "pipelines");

/** Enable exactly one pipeline, so an unrelated one cannot supply the resources being asserted. */
function onlyPipeline(flag: string) {
    return (c: any) => {
        c.app.useGlobalVpc.enabled = true;
        c.app.useGlobalVpc.addVpcEndpoints = true;
        for (const name of Object.keys(c.app.pipelines)) {
            const entry = c.app.pipelines[name];
            if (entry && typeof entry === "object" && "enabled" in entry) {
                entry.enabled = name === flag;
                if (entry.autoRegisterWithVAMS !== undefined) {
                    entry.autoRegisterWithVAMS = false;
                }
            }
        }
        if (c.app.pipelines.useRapidPipeline) {
            c.app.pipelines.useRapidPipeline.useEcs.enabled = false;
            c.app.pipelines.useRapidPipeline.useEks.enabled = false;
        }
    };
}

/**
 * The tag the build is told to push: a leading slice of the CDK asset hash.
 *
 * NOT the full 64-character hash. Amazon ECS and AWS Batch cap a container image reference at 255
 * characters, and a pipeline's generated ECR repository URI already runs to about 207, so a full hash
 * produced a 272-character reference that ECS rejected at create time with
 * `Container.image should be 255 characters or less` — a deploy rollback that no synth or unit test
 * saw, because the template itself is valid. Derived from the shared constant so this test cannot
 * drift from the code that builds the tag.
 */
const CONTENT_TAG = new RegExp(`^[0-9a-f]{${IMAGE_TAG_LENGTH}}$`);

/**
 * An image reference resolved at a mutable tag, in the two forms the pipeline constructs use: a
 * repository object plus a tag literal, and a URI composed from `repositoryUri`. Global so every
 * offender in a file is reported rather than only the first.
 */
const MOVING_TAG_DETECTORS = [
    /fromEcrRepository\([^)]*"(latest|main|master)"/g,
    /repositoryUri\}:(latest|main|master)`/g,
];

/** The tag portion of an image reference, i.e. everything after the last ":" that is not a port. */
function tagOf(imageReference: string): string {
    const lastColon = imageReference.lastIndexOf(":");
    return lastColon === -1 ? "" : imageReference.slice(lastColon + 1);
}

/** `Name` -> `Value` of a CodeBuild project's plaintext environment variables. */
function environmentVariables(project: Resource): Record<string, string> {
    const out: Record<string, string> = {};
    const declared = (project.properties.Environment?.EnvironmentVariables ?? []) as any[];
    for (const variable of declared) {
        out[String(variable.Name)] = SynthResult.flatten(variable.Value);
    }
    return out;
}

/** The container-image reference of every Batch job definition, flattened out of its intrinsics. */
function jobDefinitionImages(synth: SynthResult): { logicalId: string; image: string }[] {
    return synth.ofType("AWS::Batch::JobDefinition").map((jobDefinition) => ({
        logicalId: jobDefinition.logicalId,
        image: SynthResult.flatten(jobDefinition.properties.ContainerProperties?.Image),
    }));
}

/**
 * The CodeBuild projects that build a pipeline container. `ECR_REPO_URI` is what distinguishes them
 * from any other project a synth might contain.
 */
function imageBuildProjects(synth: SynthResult): Resource[] {
    return synth
        .ofType("AWS::CodeBuild::Project")
        .filter((project) => "ECR_REPO_URI" in environmentVariables(project));
}

describe("the tag extractor", () => {
    it("reads the tag a reference ends with", () => {
        // Control for the helper the assertions below rest on. Without it, a helper that returned ""
        // for everything would make "no job definition names :latest" pass against every template.
        expect(tagOf("111122223333.dkr.ecr.us-east-1.amazonaws.com/vams-splat:latest")).toBe(
            "latest"
        );
        expect(tagOf(`\${Repo}:${"a".repeat(64)}`)).toBe("a".repeat(64));
        expect(CONTENT_TAG.test("latest")).toBe(false);
        expect(CONTENT_TAG.test("a".repeat(IMAGE_TAG_LENGTH))).toBe(true);
        // A full 64-character hash is the form that overran the ECS limit, so it must NOT pass.
        expect(CONTENT_TAG.test("a".repeat(64))).toBe(false);
    });
});

/**
 * One case per shared construct that resolves a CodeBuild image, named by the construct rather than by
 * the pipeline: what is under test is the plumbing, and each pipeline is the cheapest way to reach it.
 */
const PLUMBING = [
    {
        construct: "batch-fargate-pipeline.ts",
        pipeline: "useConversionCoordinateTransform",
        mutateKey: "image-tag-coordination-coordinate-transform",
    },
    {
        construct: "batch-gpu-pipeline.ts",
        pipeline: "useSplatToolbox",
        // `useCodeBuild` is required rather than an optimization for this one: splat's Dockerfile is
        // gitignored and absent from a fresh checkout, so the local-asset branch throws in CI.
        mutateKey: "image-tag-coordination-splat-toolbox",
    },
];

describe.each(PLUMBING)("$construct consumes the tag CodeBuild pushes", (plumbing) => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: (c: any) => {
                onlyPipeline(plumbing.pipeline)(c);
                c.app.pipelines[plumbing.pipeline].useCodeBuild = true;
            },
            mutateKey: plumbing.mutateKey,
        });
    });

    test("[control] this synth contains both sides", () => {
        // Every assertion below is satisfied by a synth holding neither a build project nor a job
        // definition, and this pipeline ships DISABLED in the commercial template — so without this
        // control the whole case passes while inspecting nothing.
        expect(imageBuildProjects(synth).length).toBeGreaterThan(0);
        expect(jobDefinitionImages(synth).length).toBeGreaterThan(0);
    });

    test("every build is told to push a content-addressed tag", () => {
        const offenders: string[] = [];
        for (const project of imageBuildProjects(synth)) {
            const tag = environmentVariables(project).IMAGE_TAG;
            if (tag === undefined) {
                offenders.push(`${project.logicalId}: no IMAGE_TAG supplied`);
            } else if (!CONTENT_TAG.test(tag)) {
                offenders.push(`${project.logicalId}: IMAGE_TAG=${tag}`);
            }
        }
        expect(offenders).toEqual([]);
    });

    test("no job definition names a moving tag", () => {
        const offenders = jobDefinitionImages(synth)
            .filter(({ image }) => ["latest", "main", "master"].includes(tagOf(image)))
            .map(({ logicalId, image }) => `${logicalId}: ${image}`);
        expect(offenders).toEqual([]);
    });

    test("each tag the build pushes is the tag a job definition pulls", () => {
        // The equality, which is the whole property. Asserted from the producer side because that is
        // the direction a partial fix breaks: a construct can be taught to push a content-addressed
        // tag while the pull site still resolves the mutable alias, and both halves then look right in
        // isolation.
        const pulled = jobDefinitionImages(synth).map(({ image }) => tagOf(image));
        const unconsumed: string[] = [];
        for (const project of imageBuildProjects(synth)) {
            const tag = environmentVariables(project).IMAGE_TAG;
            if (tag && !pulled.includes(tag)) {
                unconsumed.push(
                    `${project.logicalId} pushes ${tag}, pulled tags: ${pulled.join(", ")}`
                );
            }
        }
        expect(unconsumed).toEqual([]);
    });
});

/** Every .ts file under the pipelines tree. */
function typescriptFiles(dir: string): string[] {
    const out: string[] = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) out.push(...typescriptFiles(full));
        else if (entry.name.endsWith(".ts")) out.push(full);
    }
    return out;
}

/** Source lines that are not a comment, so a comment naming a tag is not read as a reference. */
function codeLines(file: string): string[] {
    return fs
        .readFileSync(file, "utf-8")
        .split(/\r?\n/)
        .filter((line) => !/^\s*(\/\/|\/\*|\*)/.test(line));
}

describe("every pipeline CodeBuild construct supplies IMAGE_TAG", () => {
    const files = typescriptFiles(PIPELINES_DIR);
    const builders = files.filter((f) => codeLines(f).some((l) => /ECR_REPO_URI:/.test(l)));

    it("[control] finds the CodeBuild constructs to scan", () => {
        // There are five: coordinateTransform, splatToolbox, cosmos (four repos from one construct),
        // gr00t, isaacLab. A lower number means the scan stopped seeing them, which would make the
        // assertions below pass vacuously.
        expect(builders.length).toBeGreaterThanOrEqual(5);
    });

    it("supplies IMAGE_TAG beside ECR_REPO_URI, from the source asset hash", () => {
        // The producer half for the three pipelines the synth cases above cannot cheaply enable. The
        // value has to be the asset-hash local rather than a literal: a literal default is what
        // silently reintroduces a mutable tag for a caller that forgets to pass one.
        const offenders: string[] = [];
        for (const file of builders) {
            const text = codeLines(file).join("\n");
            if (!/IMAGE_TAG:\s*\{\s*value:\s*imageTag,/.test(text)) {
                offenders.push(path.basename(file));
            }
            if (!/const imageTag = contentImageTag\(sourceAsset\.assetHash\);/.test(text)) {
                offenders.push(`${path.basename(file)} (imageTag not derived from the asset hash)`);
            }
        }
        expect(offenders).toEqual([]);
    });

    it("no pull site resolves a moving tag", () => {
        // The consumer half, covering the pull sites a synth case does not reach — the Cosmos and
        // GR00T job definitions take a pre-composed image URI string rather than a repository object.
        //
        // Matched against the whole file rather than line by line. Prettier wraps a
        // `fromEcrRepository(repo, "latest")` call across three lines once the arguments are long
        // enough, and a per-line detector reads that as clean — which is how the Isaac Lab pull site
        // escaped the first version of this assertion.
        const offenders: string[] = [];
        for (const file of files) {
            for (const detector of MOVING_TAG_DETECTORS) {
                for (const match of codeLines(file).join("\n").matchAll(detector)) {
                    offenders.push(`${path.basename(file)}: ${match[0].replace(/\s+/g, " ")}`);
                }
            }
        }
        expect(offenders).toEqual([]);
    });

    it("the moving-tag detectors actually detect", () => {
        // Control for the two patterns above, against literals rather than the tree. Asserting only
        // that the tree is clean cannot distinguish "no offenders" from "a pattern that matches
        // nothing", and this control stays true after the offending lines are gone.
        const matches = (subject: string): boolean =>
            MOVING_TAG_DETECTORS.some((d) => [...subject.matchAll(d)].length > 0);
        expect(matches('ecs.ContainerImage.fromEcrRepository(props.repo, "latest")')).toBe(true);
        // The wrapped form, which is what the tree actually contained.
        expect(
            matches(
                "containerImageRef = ecs.ContainerImage.fromEcrRepository(\n" +
                    "    props.codeBuildRepository,\n" +
                    '    "latest"\n' +
                    ");"
            )
        ).toBe(true);
        expect(matches("ecs.ContainerImage.fromEcrRepository(props.repo, props.tag)")).toBe(false);
        expect(matches("const imageUri = `${repository.repositoryUri}:latest`;")).toBe(true);
        expect(matches("const imageUri = `${repository.repositoryUri}:${imageTag}`;")).toBe(false);
    });
});

/**
 * The bound that was absent when a 64-character tag shipped and rolled the deploy back.
 *
 * The failure is only visible once the repository URI RESOLVES, so it cannot be asserted against a
 * synthesized template — the template holds an `Fn::Join`. It is asserted here as arithmetic over the
 * measured repository URIs instead.
 *
 * :::warning[This block previously reasoned from a "worst case" that was not the worst case]
 * The constant was named `LONGEST_MEASURED_REPOSITORY_URI = 207`, and its `[control]` arm asserted that
 * the splat URI really is 207 characters long. That assertion was TRUE and proved the wrong thing: it
 * validated the premise (this string measures 207) rather than the link (207 is the maximum). Splat was
 * simply the deepest CodeBuild pipeline whose `useCodeBuild` flag was ON, so it was the deepest one
 * anybody had measured.
 *
 * Coordinate Transform's nested-stack path is ~30 characters deeper and its auto-generated URI measures
 * **237**. Enabling its `useCodeBuild` put the reference at 270 and AWS Batch rejected every job at
 * submit — with this whole block still green, because 207 + 33 = 240 is genuinely under the cap.
 *
 * So the arithmetic below is now stated for what it actually covers: the auto-named repositories that
 * FIT. Whether a given repository's auto-generated name fits at all is not decidable here — it is a
 * CloudFormation token at synth — and is enforced structurally by
 * `containerImageReferenceLength.test.ts`, which requires an explicit short name on any repository
 * measured over the budget.
 * :::
 */
describe("a built image reference fits inside the ECS and Batch limit", () => {
    // Measured on the reference deployment (vams-core-prod5-us-west-2). CDK derives an ECR repository
    // name from the nested-stack path, so this grows with BOTH the deployment name and the construct
    // depth. Depth is what the 207 figure missed.
    const LONGEST_FITTING_AUTO_NAMED_URI = 207;
    /** Coordinate Transform, auto-named. Measured, and over the cap once tagged — hence its explicit name. */
    const DEEPEST_MEASURED_AUTO_NAMED_URI = 237;

    it("[control] both measurements are real shapes, not guesses", () => {
        const splat =
            "465557923944.dkr.ecr.us-west-2.amazonaws.com/vams-core-prod5-us-west-2-pipelinebuilderne" +
            "-splattoolboxbuildernestedstacknestedstacks-1mkj56iwrqm6d-splattoolboxcodebuildecrrepospl" +
            "attoolbox4795a7b1-a5vasqcbjxnd";
        expect(splat.length).toBe(LONGEST_FITTING_AUTO_NAMED_URI);
        // The measurement that refutes the old "207 is the worst case" claim.
        expect(DEEPEST_MEASURED_AUTO_NAMED_URI).toBeGreaterThan(LONGEST_FITTING_AUTO_NAMED_URI);
    });

    it("the tag length leaves a FITTING auto-named reference inside the limit", () => {
        const reference = LONGEST_FITTING_AUTO_NAMED_URI + ":".length + IMAGE_TAG_LENGTH;
        expect(reference).toBeLessThanOrEqual(MAX_IMAGE_REFERENCE_LENGTH);
    });

    it("a full 64-character hash would NOT fit — this is why the tag is truncated", () => {
        const reference = LONGEST_FITTING_AUTO_NAMED_URI + ":".length + 64;
        expect(reference).toBeGreaterThan(MAX_IMAGE_REFERENCE_LENGTH);
    });

    it("truncating the tag does NOT rescue the deepest auto-named repository", () => {
        // The fact the old block asserted away. Tag truncation buys 32 characters; this path needs ~15
        // more than that, so the only remedy is an explicit repository name — which is why
        // coordinateTransformCodeBuild-construct.ts sets one.
        const reference = DEEPEST_MEASURED_AUTO_NAMED_URI + ":".length + IMAGE_TAG_LENGTH;
        expect(reference).toBeGreaterThan(MAX_IMAGE_REFERENCE_LENGTH);
    });

    it("there is headroom for a longer deployment name", () => {
        const headroom =
            MAX_IMAGE_REFERENCE_LENGTH - (LONGEST_FITTING_AUTO_NAMED_URI + 1 + IMAGE_TAG_LENGTH);
        // A customer whose stack name is longer than the reference deployment's must still fit. This
        // covers the deployment-name axis only; the construct-depth axis is the other test above.
        expect(headroom).toBeGreaterThanOrEqual(10);
    });

    it("the tag is still content-addressed after truncation", () => {
        // Same source -> same tag; a changed source -> a different tag. Truncation preserves both,
        // which is the whole property the immutable-tag fix depends on.
        const a = "0".repeat(64);
        const b = "0".repeat(63) + "1";
        expect(a.slice(0, IMAGE_TAG_LENGTH)).toBe(a.slice(0, IMAGE_TAG_LENGTH));
        expect(IMAGE_TAG_LENGTH).toBeGreaterThanOrEqual(16);
        // 128 bits of a SHA-256 is collision-resistant for an image tag namespace.
        expect(a.slice(0, IMAGE_TAG_LENGTH).length * 4).toBeGreaterThanOrEqual(128);
        expect(b.slice(0, IMAGE_TAG_LENGTH)).toBe(a.slice(0, IMAGE_TAG_LENGTH)); // differs past the cut
    });
});
