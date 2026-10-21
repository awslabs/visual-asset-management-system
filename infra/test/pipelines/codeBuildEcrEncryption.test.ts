/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Amazon ECR repositories of the CodeBuild-built pipelines follow `app.useKmsCmkEncryption`.
 *
 * With `useCodeBuild` on, five pipelines build their container image with AWS CodeBuild into a
 * VAMS-owned repository: coordinate transform, Gaussian splat toolbox, NVIDIA Cosmos (one repository per
 * model family), NVIDIA GR00T and Isaac Lab training. With the CMK on, each repository is encrypted with
 * the deployment's shared key; with it off, each stays on the Amazon ECR AES-256 default, which is no
 * `EncryptionConfiguration` at all. Both directions are asserted for every repository.
 *
 * Amazon ECR cannot change a repository's encryption, so `EncryptionConfiguration` is a REPLACEMENT
 * property and turning the CMK on or off replaces every repository. Three things must hold for that
 * update to deploy and leave working pipelines, and none of them is visible in a single synth:
 *
 *   - The explicitly named coordinate transform repository changes its name with the encryption
 *     (`-kms`), because AWS CloudFormation cannot replace a custom-named resource under the same name.
 *   - Every AWS Batch job definition references its repository, so it moves to the replacement.
 *   - The builds are start-only: the deployment does not wait for the image. The build-trigger custom
 *     resource is invoked only when one of its properties changes, so it must carry the repository;
 *     otherwise the replacement stays empty and every job pulls an image that does not exist.
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
];

/** The one repository with an explicit `repositoryName`. */
const COORDINATE_TRANSFORM = EXPECTED_REPOSITORIES[0];

/** Every CodeBuild pipeline on, with one model per Cosmos family so each Cosmos repository exists. */
const allCodeBuildPipelines = (cmkOn: boolean) => (c: any) => {
    // A CodeBuild project runs in the pipeline VPC, which the commercial template ships disabled.
    c.app.useGlobalVpc = { ...(c.app.useGlobalVpc ?? {}), enabled: true };
    c.app.useKmsCmkEncryption = { ...(c.app.useKmsCmkEncryption ?? {}), enabled: cmkOn };
    const p = c.app.pipelines;
    for (const key of [
        "useConversionCoordinateTransform",
        "useSplatToolbox",
        "useNvidiaCosmos",
        "useNvidiaCosmos3",
        "useNvidiaGr00t",
        "useIsaacLabTraining",
    ]) {
        p[key].enabled = true;
        p[key].useCodeBuild = true;
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
};

const synthWith = (name: TemplateName, cmkOn: boolean): SynthResult =>
    synthTemplate(name, {
        mutate: allCodeBuildPipelines(cmkOn),
        mutateKey: `codebuild-ecr-encryption-cmk-${cmkOn ? "on" : "off"}`,
    });

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

/** The AWS Batch job definitions whose container image comes from a repository. */
const jobDefinitionsOf = (s: SynthResult, repository: Resource): Resource[] =>
    s.where(
        "AWS::Batch::JobDefinition",
        (jd) =>
            jd.stack === repository.stack && refersTo(jd.properties.ContainerProperties, repository)
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

    beforeAll(() => {
        off = synthWith(name, false);
        on = synthWith(name, true);
    });

    it("[control] both synths emit every repository with its build, and only CMK-on has the key", () => {
        // Every assertion below is satisfied by a synth that emits no repository, and the shipped
        // templates enable none of these pipelines.
        for (const s of [off, on]) {
            expect(repositories(s).map(prefixOf).sort()).toEqual([...EXPECTED_REPOSITORIES].sort());
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

    it("the coordinate transform repository name ends in -kms exactly when the CMK is on", () => {
        const plain = repositoryByPrefix(off, COORDINATE_TRANSFORM).properties.RepositoryName;
        expect(plain).toMatch(/-coordtransform$/);
        expect(repositoryByPrefix(on, COORDINATE_TRANSFORM).properties.RepositoryName).toBe(
            `${plain}-kms`
        );
    });

    it("every other repository is auto-named, so its replacement gets a new generated name", () => {
        for (const s of [off, on]) {
            const named = repositories(s)
                .filter((r) => !r.logicalId.startsWith(COORDINATE_TRANSFORM))
                .filter((r) => "RepositoryName" in r.properties)
                .map((r) => r.logicalId);
            expect(named).toEqual([]);
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

    it("every job definition that pulls from a repository moves to the replacement", () => {
        const offenders: string[] = [];
        for (const prefix of EXPECTED_REPOSITORIES) {
            const repositoryOn = repositoryByPrefix(on, prefix);
            const jobDefinitions = jobDefinitionsOf(on, repositoryOn);
            if (jobDefinitions.length === 0) {
                offenders.push(`${prefix}: no job definition pulls from it`);
            }
            for (const jdOn of jobDefinitions) {
                const jdOff = off.where(
                    "AWS::Batch::JobDefinition",
                    (r) => r.stack === jdOn.stack && r.logicalId === jdOn.logicalId
                )[0];
                const image = (s: SynthResult, jd: Resource) =>
                    resolved(s, jd.stack, jd.properties.ContainerProperties.Image);
                if (!jdOff || !refersTo(jdOff.properties.ContainerProperties, repositoryOn)) {
                    offenders.push(
                        `${jdOn.logicalId}: does not pull from ${prefix} with the CMK off`
                    );
                } else if (image(off, jdOff) === image(on, jdOn)) {
                    offenders.push(`${jdOn.logicalId}: image ${image(on, jdOn)} is unchanged`);
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
