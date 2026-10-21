/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The containers feature of the EDH Workstations add-on: the container service Lambda and its four
 * route registrations, one image build per declared image, the job-definition list handed to the
 * catalog registration resource, the property that no add-on Lambda holds ECR, Batch or EKS IAM,
 * and the Batch interface endpoint the feature supplies when no pipeline does.
 *
 * Three hybrids: the add-on alone (`enableEdh`), the add-on with the containers feature on and every
 * container list empty (`enableEdhContainers`), and the same with one image and one job definition.
 * The first two discriminate the feature gate; the third is where the image build exists.
 *
 * The IAM assertion resolves policies through each Lambda's Role reference rather than by logical id,
 * and requires that at least one add-on policy was examined: a filter that happened to match nothing
 * would otherwise pass while inspecting no permissions at all.
 */

import * as fs from "fs";
import * as path from "path";
import { API_GATEWAY_DEFAULT_TIMEOUT_SECONDS } from "../../../config/config";
import { IMAGE_TAG_LENGTH } from "../../../lib/helper/containerImageTag";
import {
    ACCOUNT,
    Resource,
    SynthResult,
    TemplateName,
    actionsOf,
    expectAbsent,
    handlerOf,
    lambdaByHandler,
    policiesAttachedTo,
    resourcesOf,
    restApiPairs,
    roleIdOf,
    statementsOf,
    synthTemplate,
} from "../../support/templateSynth";
import { edhFunctions, enableEdh, enableEdhContainers, inEdhStack } from "./support/edhSynth";

// A full-app synth is ~20 s and this file needs six of them (two templates, three hybrids).
jest.setTimeout(600_000);

const EDH_TEMPLATES: TemplateName[] = ["commercial", "govcloud"];
const BACKEND_ROOT = path.join(__dirname, "..", "..", "..", "..", "backend", "backend");
const CONTAINER_HANDLER = "handlers.addon.edh.edhContainerService.lambda_handler";

/** Strings identical to the container members of `EDH_ROUTES` in apiRoutes.py. */
const CONTAINER_PAIRS = [
    "GET /addon/edh/containers/catalog",
    "GET /addon/edh/containers/jobs",
    "POST /addon/edh/containers/jobs",
    "DELETE /addon/edh/containers/jobs/{jobId}",
];

/** A tracked Dockerfile directory, so the source asset stages on a fresh checkout. */
const EDH_IMAGE = {
    name: "sim-worker",
    dockerfileDir: "backendPipelines/conversion/coordinateTransform/container",
};
const EDH_JOB_DEFINITION = {
    name: "edh-sim-train",
    imageRef: "sim-worker",
    vcpus: 4,
    memory: 16384,
};

const CONTENT_TAG = new RegExp(`^[0-9a-f]{${IMAGE_TAG_LENGTH}}$`);
/** The EDH Batch execution role of the template's partition, as an operator would list it. */
const pullRoleArn = (partition: string) =>
    `arn:${partition}:iam::${ACCOUNT}:role/edh-batch-execution`;

function withContainerImage(c: any) {
    enableEdhContainers(c);
    const containers = c.app.addons.useEdhWorkstations.containers;
    containers.buildAndPushImages = [EDH_IMAGE];
    containers.jobDefinitions = [EDH_JOB_DEFINITION];
    containers.eksEnabled = true;
    containers.imagePullRoleArns = [pullRoleArn(c.env.partition)];
}

const edhOnlySynth = (name: TemplateName): SynthResult =>
    synthTemplate(name, { mutate: enableEdh, mutateKey: "edhWorkstationsEnabled" });
const containersSynth = (name: TemplateName): SynthResult =>
    synthTemplate(name, { mutate: enableEdhContainers, mutateKey: "edhContainersEnabled" });
const imageSynth = (name: TemplateName): SynthResult =>
    synthTemplate(name, { mutate: withContainerImage, mutateKey: "edhContainersImage" });

const containerLambdas = (s: SynthResult): Resource[] =>
    s.where("AWS::Lambda::Function", (r) => handlerOf(r) === CONTAINER_HANDLER);

const isContainerPair = (pair: string) => pair.includes(" /addon/edh/containers/");

/** The catalog registration resource: the one custom resource carrying the predefined-image lists. */
function registrationResource(s: SynthResult): Resource {
    const found = s
        .ofType("AWS::CloudFormation::CustomResource")
        .filter((r) => "predefinedVdiImages" in r.properties);
    expect(found).toHaveLength(1);
    return found[0];
}

/** `Name` -> flattened `Value` of a CodeBuild project's environment variables. */
function environmentVariables(project: Resource): Record<string, string> {
    const out: Record<string, string> = {};
    for (const variable of (project.properties.Environment?.EnvironmentVariables ?? []) as any[]) {
        out[String(variable.Name)] = SynthResult.flatten(variable.Value);
    }
    return out;
}

/** The CodeBuild projects the add-on emits: they carry ECR_REPO_URI like every image build. */
const edhProjects = (s: SynthResult): Resource[] =>
    s.where(
        "AWS::CodeBuild::Project",
        (r) => inEdhStack(r) && "ECR_REPO_URI" in environmentVariables(r)
    );

function actionsIn(policies: Resource[]): string[] {
    return policies.flatMap((p) =>
        ((p.properties.PolicyDocument?.Statement ?? []) as any[]).flatMap(actionsOf)
    );
}

describe("the container service Lambda", () => {
    test.each(EDH_TEMPLATES)(
        "%s: is in the VPC behind the add-on security group, fits the API Gateway timeout and resolves its module",
        (name) => {
            const fns = containerLambdas(containersSynth(name));
            expect(fns).toHaveLength(1);
            const [fn] = fns;
            expect(inEdhStack(fn)).toBe(true);
            expect(fn.properties.Timeout).toBeLessThanOrEqual(API_GATEWAY_DEFAULT_TIMEOUT_SECONDS);
            expect(fn.properties.VpcConfig).toBeDefined();
            expect(fn.properties.VpcConfig.SubnetIds.length).toBeGreaterThan(0);
            expect(SynthResult.flatten(fn.properties.VpcConfig.SecurityGroupIds)).toContain(
                "EdhAddonLambdaSecurityGroup"
            );
            const segments = CONTAINER_HANDLER.split(".");
            segments.pop();
            expect(fs.existsSync(path.join(BACKEND_ROOT, ...segments) + ".py")).toBe(true);
            // Set by globalLambdaEnvironmentsAndPermissions: proves the shared helpers ran on it.
            expect(fn.properties.Environment.Variables.VAMS_RESOURCE_PARAM_PREFIX).toBe(
                `/vams-t1-${name}/resourceNames`
            );
        }
    );

    test.each(EDH_TEMPLATES)("%s: carries the EKS switch as configured", (name) => {
        const off = containerLambdas(containersSynth(name))[0].properties.Environment.Variables;
        expect(off.EDH_CONTAINERS_EKS_ENABLED).toBe("false");
        const on = containerLambdas(imageSynth(name))[0].properties.Environment.Variables;
        expect(on.EDH_CONTAINERS_EKS_ENABLED).toBe("true");
    });

    test.each(EDH_TEMPLATES)(
        "%s: the containers-off hybrid and the stock template emit none",
        (name) => {
            const control = {
                description: `the ${name} containers hybrid emits the container service Lambda`,
                count: containerLambdas(containersSynth(name)).length,
            };
            expectAbsent(
                `the container service Lambda with containers off (${name})`,
                containerLambdas(edhOnlySynth(name)),
                control
            );
            expectAbsent(
                `the container service Lambda in the stock ${name} template`,
                containerLambdas(synthTemplate(name)),
                control
            );
        }
    );
});

describe("the container routes", () => {
    test.each(EDH_TEMPLATES)(
        "%s: every container pair is registered only with containers on",
        (name) => {
            const enabled = restApiPairs(containersSynth(name));
            // Control: the earlier surface is present too, so an empty body cannot pass this test.
            expect(enabled.has("GET /addon/edh/workstations")).toBe(true);
            expect(CONTAINER_PAIRS.filter((pair) => !enabled.has(pair))).toEqual([]);

            expectAbsent(
                `container routes with containers off (${name})`,
                [...restApiPairs(edhOnlySynth(name))].filter(isContainerPair),
                {
                    description: `the ${name} containers hybrid registers container routes`,
                    count: [...enabled].filter(isContainerPair).length,
                }
            );
        }
    );
});

describe("one image build per declared image", () => {
    test.each(EDH_TEMPLATES)(
        "%s: the repository is explicitly named, EDH-tagged and built by an out-of-VPC project",
        (name) => {
            const s = imageSynth(name);
            const repos = s.where("AWS::ECR::Repository", inEdhStack);
            expect(repos).toHaveLength(1);
            // govcloud turns useKmsCmkEncryption on, so its repository carries the -kms suffix
            // (an encryption change replaces an ECR repository, so the name changes with it).
            const kmsSuffix = name === "govcloud" ? "-kms" : "";
            expect(SynthResult.flatten(repos[0].properties.RepositoryName)).toBe(
                `vams-t1-${name}-edh-sim-worker${kmsSuffix}`
            );
            expect(repos[0].properties.Tags).toEqual(
                expect.arrayContaining([{ Key: "edh:visibility:edh-t1", Value: "true" }])
            );
            const projects = edhProjects(s);
            expect(projects).toHaveLength(1);
            expect(projects[0].properties.VpcConfig).toBeUndefined();
            expect(environmentVariables(projects[0]).IMAGE_TAG).toMatch(CONTENT_TAG);
        }
    );

    test.each(EDH_TEMPLATES)("%s: the containers-off hybrids build no image", (name) => {
        const control = {
            description: `the ${name} image hybrid emits a CodeBuild project`,
            count: edhProjects(imageSynth(name)).length,
        };
        expectAbsent(
            `image builds with containers on but no image (${name})`,
            edhProjects(containersSynth(name)),
            control
        );
        expectAbsent(
            `image builds with containers off (${name})`,
            edhProjects(edhOnlySynth(name)),
            control
        );
    });

    test("the project name is exported under an EdhImageBuildProject output", () => {
        const s = imageSynth("commercial");
        const outputs = Object.values(s.templates).flatMap((t: any) =>
            Object.entries<any>(t.Outputs ?? {}).filter(([key]) =>
                key.startsWith("EdhImageBuildProject")
            )
        );
        expect(outputs).toHaveLength(1);
        const [key, output] = outputs[0];
        expect(key).toBe("EdhImageBuildProjectsimworker");
        expect(JSON.stringify(output.Value)).toContain(edhProjects(s)[0].logicalId);
    });
});

/**
 * AWS Batch and Amazon ECS cap a container image reference at 255 characters over
 * `<account>.dkr.ecr.<region>.amazonaws.com/<repository>:<tag>`
 * (test/pipelines/containerImageReferenceLength.test.ts states the measured failure). The EDH image
 * repository sits a nested stack deeper than coordinateTransform's, whose auto-generated URI already
 * measured 237 characters, so the construct names it `{config.name}-{baseStackName}-edh-{image}`.
 */
describe("the image repository name fits an AWS Batch image reference", () => {
    /** Longest registry host: a 12-digit account and the longest ECR regional hostname. */
    const REGISTRY_HOST_BUDGET = "123456789012.dkr.ecr.ap-southeast-4.amazonaws.com/".length;
    /** `:` plus the truncated content tag. */
    const TAG_BUDGET = 1 + IMAGE_TAG_LENGTH;
    const MAX_IMAGE_REFERENCE_LENGTH = 255;

    test.each(EDH_TEMPLATES)(
        "%s: the explicit name leaves room for the host and the tag and is a legal ECR name",
        (name) => {
            const repos = imageSynth(name).where("AWS::ECR::Repository", inEdhStack);
            expect(repos).toHaveLength(1);
            const repositoryName = SynthResult.flatten(repos[0].properties.RepositoryName);
            expect(repositoryName.length).toBeGreaterThan(0);
            expect(REGISTRY_HOST_BUDGET + repositoryName.length + TAG_BUDGET).toBeLessThanOrEqual(
                MAX_IMAGE_REFERENCE_LENGTH
            );
            expect(repositoryName).toMatch(/^[a-z0-9][a-z0-9._/-]{1,255}$/);
        }
    );

    test.each(EDH_TEMPLATES)(
        "%s: the build trigger carries the repository, so a rename re-fires the build",
        (name) => {
            // RepositoryName is a replacement property: a rename creates an empty repository, and the
            // build custom resource re-runs only when one of its own properties changes.
            const s = imageSynth(name);
            const [repo] = s.where("AWS::ECR::Repository", inEdhStack);
            const triggers = s
                .ofType("AWS::CloudFormation::CustomResource")
                .concat(s.ofType("Custom::AWS"))
                .filter((c) => c.stack === repo.stack && "ProjectName" in (c.properties ?? {}));
            expect(triggers).toHaveLength(1);
            expect(Object.keys(triggers[0].properties).some((p) => /repositor/i.test(p))).toBe(
                true
            );
        }
    );
});

describe("the image repository follows the deployment KMS key", () => {
    // The commercial template leaves useKmsCmkEncryption off and the govcloud template turns it on,
    // so the pair covers both sides of the flip with the same image configuration.

    /** The deployment's shared VAMS key in the storage nested stack: one with the CMK on, none off. */
    const deploymentKeys = (s: SynthResult): Resource[] =>
        s.where(
            "AWS::KMS::Key",
            (r) =>
                /StorageResourcesBuilder/.test(r.stack) &&
                r.logicalId.startsWith("VAMSEncryptionKMSKey")
        );

    /**
     * The storage-stack key a nested-stack value refers to, or undefined: the key reaches the add-on
     * stack as a `referenceto…Outputs<output name>` parameter, and that storage output must be the
     * key's `Arn`.
     */
    const storageKeyReferencedBy = (s: SynthResult, of: Resource, value: any) => {
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

    test("[control] only the govcloud synth has the deployment key", () => {
        expect(deploymentKeys(imageSynth("commercial"))).toHaveLength(0);
        expect(deploymentKeys(imageSynth("govcloud"))).toHaveLength(1);
    });

    test("commercial (CMK off): AES-256, and the name carries no -kms", () => {
        const repos = imageSynth("commercial").where("AWS::ECR::Repository", inEdhStack);
        expect(repos).toHaveLength(1);
        const config = repos[0].properties.EncryptionConfiguration;
        expect(config === undefined || config.EncryptionType === "AES256").toBe(true);
        expect(SynthResult.flatten(repos[0].properties.RepositoryName)).toMatch(/-edh-sim-worker$/);
    });

    test("govcloud (CMK on): KMS with the deployment key, and the name ends in -kms", () => {
        const s = imageSynth("govcloud");
        const repos = s.where("AWS::ECR::Repository", inEdhStack);
        expect(repos).toHaveLength(1);
        const config = repos[0].properties.EncryptionConfiguration;
        expect(config?.EncryptionType).toBe("KMS");
        expect(storageKeyReferencedBy(s, repos[0], config?.KmsKey)).toBe(
            deploymentKeys(s)[0].logicalId
        );
        expect(SynthResult.flatten(repos[0].properties.RepositoryName)).toMatch(
            /-edh-sim-worker-kms$/
        );
    });
});

describe("the job-definition list handed to the catalog registration", () => {
    test("is empty while no job definition is configured", () => {
        const props = registrationResource(containersSynth("commercial")).properties;
        expect(JSON.parse(props.jobDefinitions)).toEqual([]);
    });

    test("names the built image under the tag the build pushes", () => {
        // The equality that keeps the registered definition and the pushed image on one tag.
        const s = imageSynth("commercial");
        const pushed = environmentVariables(edhProjects(s)[0]).IMAGE_TAG;
        const entries = JSON.parse(
            SynthResult.flatten(registrationResource(s).properties.jobDefinitions)
        );
        expect(entries).toHaveLength(1);
        expect(entries[0]).toMatchObject({ name: "edh-sim-train", vcpus: 4, memory: 16384 });
        expect(entries[0].imageUri.endsWith(`:${pushed}`)).toBe(true);
        expect(entries[0].imageUri).toContain("EcrRepoEdhsimworker");
    });

    test("the content hash follows the configured job-definition list", () => {
        const base = registrationResource(containersSynth("commercial")).properties.contentHash;
        const withDefinition = registrationResource(imageSynth("commercial")).properties
            .contentHash;
        expect(base).toMatch(/^[0-9a-f]{64}$/);
        expect(withDefinition).not.toBe(base);
    });

    test("the registration depends on the build trigger", () => {
        const dependsOn: string[] =
            registrationResource(imageSynth("commercial")).raw.DependsOn ?? [];
        expect(dependsOn.some((id) => /BuildTriggerCREdhsimworker/.test(id))).toBe(true);
    });
});

describe("Batch IAM sits on the catalog registration Lambda alone, and only with containers on", () => {
    const REGISTRATION_HANDLER = "handlers.addon.edh.edhCatalogRegistration.lambda_handler";
    const BATCH_WRITE_ACTIONS = [
        "batch:DeregisterJobDefinition",
        "batch:RegisterJobDefinition",
        "batch:TagResource",
    ];
    const BATCH_ACTIONS = [...BATCH_WRITE_ACTIONS, "batch:DescribeJobDefinitions"].sort();

    /** `<handler>: <action>` for every ecr/batch/eks action any add-on Lambda's role may perform. */
    function containerActions(s: SynthResult): string[] {
        const fns = edhFunctions(s);
        expect(fns.length).toBeGreaterThan(0);
        const out: string[] = [];
        let examined = 0;
        for (const fn of fns) {
            const policies = policiesAttachedTo(s, roleIdOf(fn), fn.stack);
            // Every add-on Lambda is granted at least the root-key read, so an empty set here means
            // the role was not resolved and the assertion would inspect nothing.
            expect(policies.length).toBeGreaterThan(0);
            examined += policies.length;
            for (const action of actionsIn(policies)) {
                if (/^(ecr|batch|eks):/.test(action)) out.push(`${handlerOf(fn)}: ${action}`);
            }
        }
        expect(examined).toBeGreaterThan(0);
        return out.sort();
    }

    test.each(EDH_TEMPLATES)(
        "%s: with containers on, exactly the four Batch actions, on the registration Lambda",
        (name) => {
            const expected = BATCH_ACTIONS.map((action) => `${REGISTRATION_HANDLER}: ${action}`);
            expect(containerActions(imageSynth(name))).toEqual(expected);
            // With no job definition configured there is no name to scope a write to: only the listing.
            expect(containerActions(containersSynth(name))).toEqual([
                `${REGISTRATION_HANDLER}: batch:DescribeJobDefinitions`,
            ]);
        }
    );

    test.each(EDH_TEMPLATES)(
        "%s: the writes are scoped to the configured job definitions; only the listing is unscoped",
        (name) => {
            const s = imageSynth(name);
            const [fn] = edhFunctions(s).filter((r) => handlerOf(r) === REGISTRATION_HANDLER);
            expect(fn).toBeDefined();
            const statements = statementsOf(s, fn);
            const writes = statements.filter((st) =>
                actionsOf(st).includes("batch:RegisterJobDefinition")
            );
            expect(writes).toHaveLength(1);
            expect(
                actionsOf(writes[0])
                    .filter((a) => a.startsWith("batch:"))
                    .sort()
            ).toEqual(BATCH_WRITE_ACTIONS);
            // The stack is environment-bound, so region and account are inlined; the partition stays the
            // pseudo-parameter, which is what keeps a literal `arn:aws:` out of the template.
            expect(resourcesOf(writes[0])).toEqual([
                `arn:\${AWS::Partition}:batch:${s.region}:${ACCOUNT}:job-definition/edh-sim-train`,
                `arn:\${AWS::Partition}:batch:${s.region}:${ACCOUNT}:job-definition/edh-sim-train:*`,
            ]);
            const reads = statements.filter((st) =>
                actionsOf(st).includes("batch:DescribeJobDefinitions")
            );
            expect(reads).toHaveLength(1);
            expect(actionsOf(reads[0]).filter((a) => a.startsWith("batch:"))).toEqual([
                "batch:DescribeJobDefinitions",
            ]);
            expect(resourcesOf(reads[0])).toEqual(["*"]);
        }
    );

    test.each(EDH_TEMPLATES)(
        "%s: with containers off, no add-on Lambda holds any ECR, Batch or EKS action",
        (name) => {
            expectAbsent(
                `container IAM with containers off (${name})`,
                containerActions(edhOnlySynth(name)),
                {
                    description: `the ${name} containers hybrid grants Batch actions`,
                    count: containerActions(containersSynth(name)).length,
                }
            );
            // Positive control on the detector: the CodeBuild project's role does carry ECR actions, so
            // "none granted" is distinguished from "none visible".
            const s = imageSynth(name);
            const [project] = edhProjects(s);
            const projectActions = actionsIn(
                policiesAttachedTo(s, roleIdOf(project, "ServiceRole"), project.stack)
            );
            expect(projectActions.some((action) => /^ecr:/.test(action))).toBe(true);
        }
    );

    test.each(EDH_TEMPLATES)(
        "%s: the container service holds no grant on the session table",
        (name) => {
            const s = containersSynth(name);
            const onSessionTable = (fn: Resource) =>
                statementsOf(s, fn).filter(
                    (st) =>
                        actionsOf(st).some((a) => a.startsWith("dynamodb:")) &&
                        /EdhSessionStorage/i.test(resourcesOf(st).join(" "))
                );
            expectAbsent(
                `session-table statements on the container service (${name})`,
                onSessionTable(containerLambdas(s)[0]),
                {
                    description: "the workstation service in the same synth holds them",
                    count: onSessionTable(
                        lambdaByHandler(
                            s,
                            "handlers.addon.edh.edhWorkstationService.lambda_handler"
                        )
                    ).length,
                }
            );
        }
    );
});

/** Every pipeline off, so no pipeline flag can supply the endpoints under test. */
function withNoPipelines(c: any) {
    c.app.useGlobalVpc.addVpcEndpoints = true;
    for (const name of Object.keys(c.app.pipelines)) {
        const entry = c.app.pipelines[name];
        if (entry && typeof entry === "object" && "enabled" in entry) entry.enabled = false;
    }
    if (c.app.pipelines.useRapidPipeline) {
        c.app.pipelines.useRapidPipeline.useEcs.enabled = false;
        c.app.pipelines.useRapidPipeline.useEks.enabled = false;
    }
}

const endpointServices = (s: SynthResult): string[] =>
    s.ofType("AWS::EC2::VPCEndpoint").map((e) => SynthResult.flatten(e.properties.ServiceName));

describe("the Batch interface endpoint follows the containers feature", () => {
    const containersNoPipelines = (name: TemplateName) =>
        synthTemplate(name, {
            mutate: (c: any) => {
                enableEdhContainers(c);
                withNoPipelines(c);
            },
            mutateKey: "edhContainersNoPipelines",
        });
    const edhNoPipelines = (name: TemplateName) =>
        synthTemplate(name, {
            mutate: (c: any) => {
                enableEdh(c);
                withNoPipelines(c);
            },
            mutateKey: "edhWorkstationsNoPipelines",
        });
    const batchEndpoints = (services: string[]) => services.filter((s) => /\.batch$/.test(s));

    test.each(EDH_TEMPLATES)("%s: containers on creates the Batch endpoint alone", (name) => {
        const services = endpointServices(containersNoPipelines(name));
        const found = batchEndpoints(services);
        expect(found).toHaveLength(1);
        // Only Batch: the ECR endpoints stay pipeline-only (image pulls happen on EDH compute and the
        // image build runs outside the VPC).
        const control = {
            description: `the ${name} containers hybrid emits the Batch endpoint`,
            count: found.length,
        };
        expectAbsent(
            `an ECR API endpoint (${name})`,
            services.filter((s) => /\.ecr\.api$/.test(s)),
            control
        );
        expectAbsent(
            `an ECR Docker endpoint (${name})`,
            services.filter((s) => /\.ecr\.dkr$/.test(s)),
            control
        );
    });

    test.each(EDH_TEMPLATES)(
        "%s: the add-on without containers creates no Batch endpoint",
        (name) => {
            expectAbsent(
                `a Batch endpoint with containers off (${name})`,
                batchEndpoints(endpointServices(edhNoPipelines(name))),
                {
                    description: `the ${name} containers hybrid emits the Batch endpoint`,
                    count: batchEndpoints(endpointServices(containersNoPipelines(name))).length,
                }
            );
        }
    );
});

describe("the image repository grants pull to the configured roles", () => {
    test.each(EDH_TEMPLATES)(
        "%s: the repository policy names the role in the template's partition and nothing else",
        (name) => {
            const s = imageSynth(name);
            const repos = s.where("AWS::ECR::Repository", inEdhStack);
            expect(repos).toHaveLength(1);
            const statements = (repos[0].properties.RepositoryPolicyText?.Statement ?? []) as any[];
            expect(statements).toHaveLength(1);
            expect(([] as string[]).concat(statements[0].Principal.AWS)).toEqual([
                pullRoleArn(s.partition),
            ]);
            expect(([] as string[]).concat(statements[0].Action).sort()).toEqual([
                "ecr:BatchCheckLayerAvailability",
                "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer",
            ]);
            expect(JSON.stringify(statements[0].Principal)).not.toContain('"*"');
        }
    );
});
