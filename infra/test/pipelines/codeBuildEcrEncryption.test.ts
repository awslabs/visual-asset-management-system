/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Amazon ECR repositories of the CodeBuild-built pipelines follow `app.useKmsCmkEncryption`.
 *
 * With `useCodeBuild` on, seven pipelines build their container image with AWS CodeBuild into a
 * VAMS-owned repository: coordinate transform, Gaussian splat toolbox, NVIDIA Cosmos (one repository per
 * model family), NVIDIA GR00T, Isaac Lab training, Video SOP/BOM Extraction and the GenAI CAD STEP
 * agent. With the CMK on, each repository is encrypted with the deployment's shared key; with it off,
 * each stays on the Amazon ECR AES-256 default, which is no `EncryptionConfiguration` at all. Both
 * directions are asserted for every repository.
 *
 * Amazon ECR cannot change a repository's encryption, so `EncryptionConfiguration` is a REPLACEMENT
 * property and turning the CMK on or off replaces every repository. Three things must hold for that
 * update to deploy and leave working pipelines, and none of them is visible in a single synth:
 *
 *   - The explicitly named repositories (coordinate transform, Video SOP/BOM, GenAI CAD STEP agent)
 *     change their names with the encryption (`-kms`), because AWS CloudFormation cannot replace a
 *     custom-named resource under the same name.
 *   - Every consumer references its repository, so it moves to the replacement: an AWS Batch job
 *     definition, or the CAD STEP agent's Amazon Bedrock AgentCore Runtime on its `agentcore` runtime.
 *   - The build-trigger custom resource is invoked only when one of its properties changes, so it must
 *     carry the repository; otherwise the replacement stays empty and every consumer names an image that
 *     does not exist. The Batch pipelines' builds are start-only: the deployment does not wait for the
 *     image. The CAD STEP agent's custom resource also waits for its build, so its consumers are
 *     updated once the image is in the replacement.
 *
 * These are asserted between a CMK-off and a CMK-on synth of each template. A repository's physical
 * identity is modelled as its logical id plus its replacement properties (`RepositoryName`,
 * `EncryptionConfiguration`), so a reference that resolves differently in the two synths is a property
 * AWS CloudFormation updates when it replaces the repository.
 *
 * The key is imported by ARN, so the storage nested stack that owns it never consumes a pipeline stack's
 * output; the dependency-direction check of `test/security/inPlaceUpdateSafety.test.ts` runs on the
 * CMK-on synths.
 */

import {
    ALL_TEMPLATES,
    expectAbsent,
    Resource,
    SynthResult,
    synthTemplate,
    TemplateName,
} from "../support/templateSynth";

/** One entry per repository the mutation below builds: the logical id without CDK's hash suffix. */
const EXPECTED_REPOSITORIES = [
    "CoordinateTransformPipelineCoordTransformCodeBuildEcrRepoCoordTransform",
    "SplatToolboxCodeBuildEcrReposplattoolbox",
    "CosmosCodeBuildEcrRepopredictv2",
    "CosmosCodeBuildEcrReporeason",
    "CosmosCodeBuildEcrRepotransfer",
    "CosmosCodeBuildEcrRepocosmos3",
    "Gr00tCodeBuildEcrRepogr00tfinetune",
    "IsaacLabCodeBuildEcrRepotraining",
    "VideoSopBomPipelineVideoSopBomCodeBuildEcrRepoVideoSopBom",
    "CadStepAgentPipelineCadStepAgentCodeBuildEcrRepoCadStepAgent",
];

/** The repositories with an explicit `repositoryName`, each with the name it ends in without the key. */
const EXPLICITLY_NAMED: Record<string, string> = {
    [EXPECTED_REPOSITORIES[0]]: "-coordtransform",
    [EXPECTED_REPOSITORIES[8]]: "-videosopbom",
    [EXPECTED_REPOSITORIES[9]]: "-cadstepagent",
};

/**
 * Every pipeline config entry with a `useCodeBuild` key.
 *
 * The mutation enables each entry that has the key instead of a list of its own, so a pipeline added
 * later is synthesized here as soon as its config entry exists. `[control]` then fails until its key is
 * added here and its repository to EXPECTED_REPOSITORIES, which is when its encryption gets checked.
 */
const EXPECTED_CODEBUILD_PIPELINES = [
    "useConversionCoordinateTransform",
    "useSplatToolbox",
    "useIsaacLabTraining",
    "useNvidiaCosmos",
    "useNvidiaCosmos3",
    "useNvidiaGr00t",
    "useGenAiVideoSopBom",
    "useGenAiCadStepAgent",
];

/**
 * Every pipeline with a `useCodeBuild` key on, as `containerImageReferenceLength.test.ts` does, plus one
 * model per Cosmos family and the GR00T model so each per-model repository exists. Returns the pipeline
 * keys it enabled.
 */
const enableEveryCodeBuildPipeline = (c: any, cmkOn: boolean): string[] => {
    // A CodeBuild project runs in the pipeline VPC, which the commercial template ships disabled.
    c.app.useGlobalVpc = { ...(c.app.useGlobalVpc ?? {}), enabled: true };
    c.app.useKmsCmkEncryption = { ...(c.app.useKmsCmkEncryption ?? {}), enabled: cmkOn };
    const p = c.app.pipelines;
    const enabled: string[] = [];
    for (const [key, entry] of Object.entries<Record<string, unknown>>(p)) {
        if (entry && typeof entry === "object" && "useCodeBuild" in entry) {
            entry.enabled = true;
            entry.useCodeBuild = true;
            enabled.push(key);
        }
    }
    // Synth-only values for the fields the NVIDIA constructs require before they build.
    for (const key of ["useNvidiaCosmos", "useNvidiaCosmos3", "useNvidiaGr00t"]) {
        p[key].huggingFaceToken = "synth-only";
    }
    p.useNvidiaCosmos3.acceptNvidiaEula = true;
    p.useIsaacLabTraining.acceptNvidiaEula = true;
    p.useNvidiaCosmos.modelsPredict.text2world2B_v2.enabled = true;
    p.useNvidiaCosmos.modelsReason.reason2B.enabled = true;
    p.useNvidiaCosmos.modelsTransfer.transfer2B.enabled = true;
    p.useNvidiaCosmos3.modelsOmni.nano16B.enabled = true;
    p.useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.enabled = true;
    // The restricted templates ship the Video SOP/BOM model id empty (the commercial inference
    // profiles do not exist there) and the construct requires one; a synth-only id with no
    // partition-specific prefix, as containerImageReferenceLength.test.ts supplies.
    if (!p.useGenAiVideoSopBom.bedrockModelId) {
        p.useGenAiVideoSopBom.bedrockModelId = "synth-only-model";
    }
    return enabled;
};

/** A synth with every CodeBuild pipeline on, and the pipeline keys its mutation enabled. */
const synthWith = (name: TemplateName, cmkOn: boolean): [SynthResult, string[]] => {
    // Stays empty on a cache hit, which [control] reports; each (template, CMK) pair is synthesized once.
    let enabled: string[] = [];
    const synth = synthTemplate(name, {
        mutate: (c) => {
            enabled = enableEveryCodeBuildPipeline(c, cmkOn);
        },
        mutateKey: `codebuild-ecr-encryption-cmk-${cmkOn ? "on" : "off"}`,
    });
    return [synth, enabled];
};

const repositories = (s: SynthResult): Resource[] => s.ofType("AWS::ECR::Repository");

const prefixOf = (repository: Resource): string | undefined =>
    EXPECTED_REPOSITORIES.find((prefix) => repository.logicalId.startsWith(prefix));

const repositoryByPrefix = (s: SynthResult, prefix: string): Resource => {
    const found = repositories(s).filter((r) => r.logicalId.startsWith(prefix));
    expect(found).toHaveLength(1);
    return found[0];
};

/**
 * A resource of the same build as a repository: the construct names them `EcrRepo-<key>`,
 * `CodeBuild-<key>` and `BuildTriggerCR-<key>` side by side.
 */
const siblingOf = (
    s: SynthResult,
    repository: Resource,
    type: string,
    part: string
): Resource[] => {
    const prefix = (prefixOf(repository) as string).replace("EcrRepo", part);
    return s.where(type, (r) => r.stack === repository.stack && r.logicalId.startsWith(prefix));
};
const projectOf = (s: SynthResult, repository: Resource): Resource[] =>
    siblingOf(s, repository, "AWS::CodeBuild::Project", "CodeBuild");
const triggerOf = (s: SynthResult, repository: Resource): Resource[] =>
    siblingOf(s, repository, "AWS::CloudFormation::CustomResource", "BuildTriggerCR");

const referencePattern = (logicalId: string, flags?: string): RegExp =>
    new RegExp(`\\$\\{${logicalId}(\\.[A-Za-z]+)?\\}`, flags);

/** Whether a value refers to a resource through `Ref` or `Fn::GetAtt`. */
const refersTo = (value: any, resource: Resource): boolean =>
    referencePattern(resource.logicalId).test(SynthResult.flatten(value));

/** The properties whose change makes AWS CloudFormation replace an `AWS::ECR::Repository`. */
const physicalIdentity = (repository: Resource): string =>
    JSON.stringify({
        RepositoryName: repository.properties.RepositoryName ?? null,
        EncryptionConfiguration: repository.properties.EncryptionConfiguration ?? null,
    });

/** A value with every repository reference replaced by that repository's physical identity. */
const resolved = (s: SynthResult, stack: string, value: any): string => {
    let out = SynthResult.flatten(value);
    for (const repository of repositories(s).filter((r) => r.stack === stack)) {
        out = out.replace(
            referencePattern(repository.logicalId, "g"),
            `<${repository.logicalId} ${physicalIdentity(repository)}>`
        );
    }
    return out;
};

/**
 * The resources whose container image comes from a repository, each with the image it names: AWS Batch
 * job definitions, and the CAD STEP agent's Amazon Bedrock AgentCore Runtime.
 */
const CONSUMER_IMAGES: Record<string, (r: Resource) => any> = {
    "AWS::Batch::JobDefinition": (r) => r.properties.ContainerProperties?.Image,
    "AWS::BedrockAgentCore::Runtime": (r) =>
        r.properties.AgentRuntimeArtifact?.ContainerConfiguration?.ContainerUri,
};
const consumersOf = (s: SynthResult, repository: Resource): Resource[] =>
    Object.entries(CONSUMER_IMAGES).flatMap(([type, image]) =>
        s.where(type, (r) => r.stack === repository.stack && refersTo(image(r), repository))
    );

const withoutServiceToken = (properties: Record<string, any>): Record<string, any> =>
    Object.fromEntries(Object.entries(properties).filter(([key]) => key !== "ServiceToken"));

/** The deployment's shared VAMS key in the storage nested stack: one with the CMK on, none with it off. */
const deploymentKeys = (s: SynthResult): Resource[] =>
    s.where(
        "AWS::KMS::Key",
        (r) =>
            /StorageResourcesBuilder/.test(r.stack) &&
            r.logicalId.startsWith("VAMSEncryptionKMSKey")
    );

/**
 * The storage-stack key a nested-stack value refers to, or undefined.
 *
 * A key from the storage nested stack reaches a pipeline nested stack as a parameter named
 * `referenceto…Outputs<output name>`, which every parent stack passes down unchanged. The storage
 * template's output of that name must be the key's `Arn`.
 */
const storageKeyReferencedBy = (s: SynthResult, of: Resource, value: any): string | undefined => {
    const param = value?.Ref;
    if (typeof param !== "string" || !(param in (s.templates[of.stack].Parameters ?? {}))) {
        return undefined;
    }
    const outputName = param.split("Outputs").pop() as string;
    for (const [stackKey, template] of Object.entries<any>(s.templates)) {
        if (!/StorageResourcesBuilder/.test(stackKey)) continue;
        const output = template.Outputs?.[outputName];
        if (output?.Value?.["Fn::GetAtt"]?.[1] === "Arn") return output.Value["Fn::GetAtt"][0];
    }
    return undefined;
};

const actionsOf = (statement: any): string[] => ([] as string[]).concat(statement.Action ?? []);

describe.each(ALL_TEMPLATES)("%s: CodeBuild repositories follow useKmsCmkEncryption", (name) => {
    let off: SynthResult;
    let on: SynthResult;
    let enabledOff: string[];
    let enabledOn: string[];

    beforeAll(() => {
        [off, enabledOff] = synthWith(name, false);
        [on, enabledOn] = synthWith(name, true);
    });

    it("[control] both synths emit every repository with its build, and only CMK-on has the key", () => {
        // Every assertion below is satisfied by a synth that emits no repository, and the shipped
        // templates enable none of these pipelines. A pipeline or repository missing from
        // EXPECTED_CODEBUILD_PIPELINES or EXPECTED_REPOSITORIES fails here, because several assertions
        // below check only the listed repositories.
        for (const enabled of [enabledOff, enabledOn]) {
            expect([...enabled].sort()).toEqual([...EXPECTED_CODEBUILD_PIPELINES].sort());
        }
        for (const s of [off, on]) {
            // An unlisted repository shows as its logical id: toEqual ignores undefined array items.
            expect(
                repositories(s)
                    .map((r) => prefixOf(r) ?? r.logicalId)
                    .sort()
            ).toEqual([...EXPECTED_REPOSITORIES].sort());
            for (const repository of repositories(s)) {
                expect(projectOf(s, repository)).toHaveLength(1);
                expect(triggerOf(s, repository)).toHaveLength(1);
            }
        }
        expect(deploymentKeys(off)).toHaveLength(0);
        expect(deploymentKeys(on)).toHaveLength(1);
    });

    it("with the CMK on, every repository is KMS-encrypted with the deployment key", () => {
        const key = deploymentKeys(on)[0];
        const offenders: string[] = [];
        for (const repository of repositories(on)) {
            const encryption = repository.properties.EncryptionConfiguration;
            if (encryption?.EncryptionType !== "KMS") {
                offenders.push(`${repository.logicalId}: ${JSON.stringify(encryption)}`);
            } else if (
                storageKeyReferencedBy(on, repository, encryption.KmsKey) !== key.logicalId
            ) {
                offenders.push(
                    `${repository.logicalId}: KmsKey ${JSON.stringify(encryption.KmsKey)} is not ` +
                        `the storage stack's ${key.logicalId}`
                );
            }
        }
        expect(offenders).toEqual([]);
    });

    it("with the CMK off, no repository has an EncryptionConfiguration (AES-256)", () => {
        // A KMS configuration without the shared key would name the AWS managed `aws/ecr` key.
        expectAbsent(
            "an EncryptionConfiguration on a CodeBuild repository with the CMK off",
            repositories(off)
                .filter((r) => "EncryptionConfiguration" in r.properties)
                .map((r) => r.logicalId),
            {
                description: "the CMK-off synth emits the CodeBuild repositories",
                count: repositories(off).length,
            }
        );
    });

    it.each(Object.entries(EXPLICITLY_NAMED))(
        "the explicitly named repository %s ends in -kms exactly when the CMK is on",
        (prefix, suffix) => {
            const plain = repositoryByPrefix(off, prefix).properties.RepositoryName;
            expect(plain.endsWith(suffix)).toBe(true);
            expect(repositoryByPrefix(on, prefix).properties.RepositoryName).toBe(`${plain}-kms`);
        }
    );

    it("every other repository is auto-named, so its replacement gets a new generated name", () => {
        const explicit = Object.keys(EXPLICITLY_NAMED);
        for (const s of [off, on]) {
            const named = repositories(s)
                .filter((r) => !explicit.some((prefix) => r.logicalId.startsWith(prefix)))
                .filter((r) => "RepositoryName" in r.properties)
                .map((r) => r.logicalId);
            expect(named).toEqual([]);
            // Control: the explicitly named repositories are in this synth and do carry a name.
            expect(
                repositories(s).filter(
                    (r) =>
                        explicit.some((prefix) => r.logicalId.startsWith(prefix)) &&
                        "RepositoryName" in r.properties
                )
            ).toHaveLength(Object.keys(EXPLICITLY_NAMED).length);
        }
    });

    it("turning the CMK on or off replaces every repository resource", () => {
        for (const prefix of EXPECTED_REPOSITORIES) {
            const before = repositoryByPrefix(off, prefix);
            const after = repositoryByPrefix(on, prefix);
            // The same resource, so this is an update of it rather than a new resource beside it.
            expect(after.logicalId).toBe(before.logicalId);
            expect(physicalIdentity(after)).not.toBe(physicalIdentity(before));
        }
    });

    it("every job definition or runtime that pulls from a repository moves to the replacement", () => {
        const offenders: string[] = [];
        for (const prefix of EXPECTED_REPOSITORIES) {
            const repositoryOn = repositoryByPrefix(on, prefix);
            const consumers = consumersOf(on, repositoryOn);
            if (consumers.length === 0) {
                offenders.push(`${prefix}: no job definition or runtime pulls from it`);
            }
            for (const consumerOn of consumers) {
                const imageOf = CONSUMER_IMAGES[consumerOn.type];
                const consumerOff = off.where(
                    consumerOn.type,
                    (r) => r.stack === consumerOn.stack && r.logicalId === consumerOn.logicalId
                )[0];
                const image = (s: SynthResult, r: Resource) => resolved(s, r.stack, imageOf(r));
                if (!consumerOff || !refersTo(imageOf(consumerOff), repositoryOn)) {
                    offenders.push(
                        `${consumerOn.logicalId}: does not pull from ${prefix} with the CMK off`
                    );
                } else if (image(off, consumerOff) === image(on, consumerOn)) {
                    offenders.push(
                        `${consumerOn.logicalId}: image ${image(on, consumerOn)} is unchanged`
                    );
                }
            }
        }
        expect(offenders).toEqual([]);
    });

    it("each build trigger's properties change, so a build starts for the replacement", () => {
        const offenders: string[] = [];
        for (const prefix of EXPECTED_REPOSITORIES) {
            const repositoryOff = repositoryByPrefix(off, prefix);
            const repositoryOn = repositoryByPrefix(on, prefix);
            const [triggerOff] = triggerOf(off, repositoryOff);
            const [triggerOn] = triggerOf(on, repositoryOn);
            const [projectOn] = projectOf(on, repositoryOn);

            // Ordering: the trigger names the project, so the project's update (its ECR_REPO_URI)
            // completes before the trigger starts the build.
            expect(SynthResult.flatten(triggerOn.properties.ProjectName)).toBe(
                `\${${projectOn.logicalId}}`
            );
            // The source did not change, only where the image has to be built into.
            expect(triggerOn.properties.SourceHash).toBe(triggerOff.properties.SourceHash);

            if (!refersTo(withoutServiceToken(triggerOn.properties), repositoryOn)) {
                offenders.push(
                    `${triggerOn.logicalId}: no property refers to ${repositoryOn.logicalId} ` +
                        `(has ${Object.keys(triggerOn.properties).join(", ")})`
                );
            } else if (
                resolved(off, triggerOff.stack, withoutServiceToken(triggerOff.properties)) ===
                resolved(on, triggerOn.stack, withoutServiceToken(triggerOn.properties))
            ) {
                offenders.push(`${triggerOn.logicalId}: properties are unchanged by the flip`);
            }

            const pushTarget = (s: SynthResult, project: Resource) =>
                resolved(
                    s,
                    project.stack,
                    (project.properties.Environment.EnvironmentVariables as any[]).find(
                        (v) => v.Name === "ECR_REPO_URI"
                    )?.Value
                );
            if (pushTarget(off, projectOf(off, repositoryOff)[0]) === pushTarget(on, projectOn)) {
                offenders.push(`${projectOn.logicalId}: ECR_REPO_URI is unchanged by the flip`);
            }
        }
        expect(offenders).toEqual([]);
    });

    it("no CodeBuild, trigger or provider role gets a permission on the key", () => {
        // Amazon ECR encrypts on push and decrypts on pull through the grants it creates on the key
        // when the repository is created, so the build and the jobs need no KMS permission.
        const policies = on.where("AWS::IAM::Policy", (p) =>
            repositories(on).some(
                (r) =>
                    p.stack === r.stack &&
                    p.logicalId.startsWith((prefixOf(r) as string).split("EcrRepo")[0])
            )
        );
        expect(policies.length).toBeGreaterThan(0);
        const kmsActions = policies.flatMap((p) =>
            (p.properties.PolicyDocument?.Statement ?? [])
                .flatMap(actionsOf)
                .filter((a: string) => a.toLowerCase().startsWith("kms:"))
                .map((a: string) => `${p.logicalId}: ${a}`)
        );
        expect(kmsActions).toEqual([]);
    });

    it("the storage nested stack takes no parameter fed from a pipeline stack output", () => {
        const stacks = (pattern: RegExp) =>
            on.ofType("AWS::CloudFormation::Stack").filter((r) => pattern.test(r.logicalId));
        const storageStacks = stacks(/StorageResourcesBuilder/);
        const pipelineStacks = stacks(/PipelineBuilder/);
        expect(storageStacks.length).toBeGreaterThan(0);
        // Control: pipelines DO consume storage outputs, the intended direction.
        expect(
            pipelineStacks.filter((stack) =>
                JSON.stringify(stack.properties.Parameters ?? {}).includes(
                    "StorageResourcesBuilder"
                )
            ).length
        ).toBeGreaterThan(0);

        const offenders: string[] = [];
        for (const stack of storageStacks) {
            for (const [param, value] of Object.entries(stack.properties.Parameters ?? {})) {
                if (JSON.stringify(value).includes("PipelineBuilder")) {
                    offenders.push(`${stack.logicalId} <- ${param.slice(0, 80)}`);
                }
            }
        }
        expect(offenders).toEqual([]);
    });
});
