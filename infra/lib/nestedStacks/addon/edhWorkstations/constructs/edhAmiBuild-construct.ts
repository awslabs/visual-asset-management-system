/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as crypto from "crypto";
import * as fs from "fs";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3deployment from "aws-cdk-lib/aws-s3-deployment";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../../config/config";
import {
    LoadedEdhAmiRecipe,
    resolveEdhAmiRecipes,
} from "../../../../../config/addon/edhWorkstationsConfig";
import { EdhAmiBuildResources } from "../lambdaBuilder/edhWorkstationsFunctions";
import { Service } from "../../../../helper/service-helper";

const REPO_ROOT = path.join(__dirname, "..", "..", "..", "..", "..", "..");
/** Artefacts bucket prefix of the recipe bundles and asset trees, under the add-on stacks' `addon/` tree. */
export const EDH_AMI_RECIPES_ARTEFACTS_PREFIX = "addon/edh/amiRecipes";
/** The cdk-nag `appliesTo` pattern of a read grant on that prefix. */
export const EDH_AMI_RECIPES_ARTEFACTS_NAG_RESOURCE = {
    regex: "/^Resource::<.*>\\/addon\\/edh\\/amiRecipes\\/\\*$/g",
};
/** Leading bytes in which a NUL byte marks a file binary, the rule of CDK's asset fingerprint. */
const HASH_BINARY_SNIFF_BYTES = 8 * 1024;

/** Directories a source tree carries that are rebuilt rather than shipped. */
export const ASSET_EXCLUDE_DIRS = [
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "temp",
];
/** Directory name suffixes with the same standing (`vamscli.egg-info`). */
export const ASSET_EXCLUDE_DIR_SUFFIXES = [".egg-info"];
/** File name suffixes that are compilation output. */
export const ASSET_EXCLUDE_FILE_SUFFIXES = [".pyc", ".pyo"];
/**
 * Credential file names, each excluded as written and followed by a dot and any suffix
 * (`.env.local`).
 */
export const ASSET_EXCLUDE_CREDENTIAL_FILES = [".env"];
/**
 * The credential file exclusions as `Source.asset` globs. The recipe directory upload applies these
 * alone, because a manifest may name a component file under any directory of its recipe.
 */
export const CREDENTIAL_EXCLUDE_GLOBS = ASSET_EXCLUDE_CREDENTIAL_FILES.flatMap((file) => [
    file,
    `${file}.*`,
]);
/**
 * The same exclusions as `Source.asset` globs. The hash walker and the upload read one list, so a
 * file in the bundle hash is a file in the bucket and a rebuilt directory changes neither. No glob
 * carries a slash, so each is matched against a path's base name at every depth, a dot directory
 * included.
 */
export const ASSET_EXCLUDE_GLOBS = [
    ...ASSET_EXCLUDE_DIRS,
    ...ASSET_EXCLUDE_DIR_SUFFIXES.map((suffix) => `*${suffix}`),
    ...ASSET_EXCLUDE_FILE_SUFFIXES.map((suffix) => `*${suffix}`),
    ...CREDENTIAL_EXCLUDE_GLOBS,
];

function isExcludedName(name: string): boolean {
    return (
        ASSET_EXCLUDE_DIRS.includes(name) ||
        [...ASSET_EXCLUDE_DIR_SUFFIXES, ...ASSET_EXCLUDE_FILE_SUFFIXES].some((s) =>
            name.endsWith(s)
        ) ||
        ASSET_EXCLUDE_CREDENTIAL_FILES.some((file) => name === file || name.startsWith(`${file}.`))
    );
}

/**
 * Every file under `dir` as a sorted POSIX-relative path, excluding rebuilt directories,
 * compilation output and credential files. Paths are separator-normalized and the bundle hash reads
 * each file through `hashedBytes`, so the same tree hashes the same on Windows and Linux whatever
 * the checkout's separators and line endings, as CDK's asset hash does.
 */
export function walkFilesSorted(dir: string): string[] {
    const found: string[] = [];
    const walk = (current: string, relative: string) => {
        for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
            const childRelative = relative ? `${relative}/${entry.name}` : entry.name;
            if (entry.isSymbolicLink()) {
                throw new Error(
                    `EDH AMI recipe asset trees may not contain symbolic links: ${childRelative}`
                );
            }
            if (entry.isDirectory()) {
                if (!isExcludedName(entry.name)) {
                    walk(path.join(current, entry.name), childRelative);
                }
            } else if (entry.isFile() && !isExcludedName(entry.name)) {
                found.push(childRelative);
            }
        }
    };
    walk(dir, "");
    return found.sort();
}

/**
 * One POSIX-separated relative path per file of a recipe directory: `recipe.json` and every
 * component file the manifest names. Separator-normalized, so a Windows synth and a Linux synth
 * of the same content produce the same path list.
 */
export function recipeFilesOf(recipe: LoadedEdhAmiRecipe): string[] {
    const componentFiles = recipe.componentFiles.map((f) =>
        path.relative(recipe.recipeDir, f).replace(/\\/g, "/")
    );
    return ["recipe.json", ...componentFiles];
}

/**
 * A file's bytes as the bundle hash reads them: every CRLF folded to LF, or the bytes as they are
 * when a NUL byte in the first 8 KiB marks the file binary. This is the rule CDK's asset fingerprint
 * applies, so a CRLF and an LF checkout of one file read the same.
 */
export function hashedBytes(file: string): Buffer {
    const bytes = fs.readFileSync(file);
    if (bytes.subarray(0, HASH_BINARY_SNIFF_BYTES).includes(0)) {
        return bytes;
    }
    // Each segment ends before the CR of a CRLF; the next one starts at its LF.
    const segments: Buffer[] = [];
    let start = 0;
    for (let crlf = bytes.indexOf("\r\n"); crlf !== -1; crlf = bytes.indexOf("\r\n", start)) {
        segments.push(bytes.subarray(start, crlf));
        start = crlf + 1;
    }
    segments.push(bytes.subarray(start));
    return Buffer.concat(segments);
}

/**
 * The content digest of every seeded recipe: its id, whether it builds on deploy, each recipe file
 * and each declared asset tree. The bundles are uploaded under a prefix carrying this digest, so
 * identical content reaches the same keys and changed content moves them. Paths are
 * POSIX-separated and each file is read through `hashedBytes`, so the digest, like CDK's asset
 * hash, is independent of the checkout's path separators and line endings.
 */
export function bundleHashOf(recipes: LoadedEdhAmiRecipe[], repoRoot: string): string {
    const hash = crypto.createHash("sha256");
    for (const recipe of recipes) {
        hash.update(recipe.recipeId).update(String(recipe.buildOnDeploy));
        for (const file of recipeFilesOf(recipe)) {
            hash.update(file).update(hashedBytes(path.join(repoRoot, recipe.recipeDir, file)));
        }
        for (const asset of recipe.assets) {
            hash.update(`asset:${asset.id}:${asset.repoPath}`);
            const assetRoot = path.join(repoRoot, asset.repoPath);
            for (const relative of walkFilesSorted(assetRoot)) {
                hash.update(relative).update(hashedBytes(path.join(assetRoot, relative)));
            }
        }
    }
    return hash.digest("hex").slice(0, 16);
}

/** One seeded recipe directory as the catalog registration reads it back from the artefacts bucket. */
export interface EdhAmiRecipeBundle {
    recipeId: string;
    /** Key prefix under which the directory is uploaded. */
    prefix: string;
    /** Keys under the prefix: `recipe.json` and every component file the manifest names. */
    files: string[];
    /** Declared assets and the prefix each tree was uploaded to; empty when the recipe declares none. */
    assets: { id: string; prefix: string }[];
    buildOnDeploy: boolean;
}

export interface EdhAmiBuildConstructProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    privateSubnets: ec2.ISubnet[];
    isolatedSubnets: ec2.ISubnet[];
    artefactsBucket: s3.IBucket;
}

/**
 * The stable half of AMI builds: the instance profile Image Builder launches build instances with, the
 * egress-only security group they run behind, and the upload of every seeded recipe directory. The
 * Image Builder objects themselves are created at runtime from recipe content.
 */
export class EdhAmiBuildConstruct extends Construct {
    public readonly resources: EdhAmiBuildResources;
    public readonly bundles: EdhAmiRecipeBundle[] = [];
    public readonly bundleHash: string;
    public readonly deployments: s3deployment.BucketDeployment[] = [];

    constructor(parent: Construct, name: string, props: EdhAmiBuildConstructProps) {
        super(parent, name);
        const edh = props.config.app.addons.useEdhWorkstations;
        const prefix = [props.config.name, props.config.app.baseStackName, "edh"]
            .join("-")
            .toLowerCase();

        const role = new iam.Role(this, "EdhAmiBuildInstanceRole", {
            assumedBy: Service("EC2").Principal,
            description: "EDH AMI recipe build instances",
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName("EC2InstanceProfileForImageBuilder"),
                iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonSSMManagedInstanceCore"),
            ],
        });
        // Build components fetch their recipe's asset trees with `aws s3 cp --recursive`.
        props.artefactsBucket.grantRead(role, `${EDH_AMI_RECIPES_ARTEFACTS_PREFIX}/*`);
        const instanceProfile = new iam.InstanceProfile(this, "EdhAmiBuildInstanceProfile", {
            role,
        });

        // No ingress: Image Builder reaches the instance through Systems Manager. Egress on 443 for AWS
        // endpoints and 80 for package mirrors that still serve plain HTTP.
        const securityGroup = new ec2.SecurityGroup(this, "EdhAmiBuildSecurityGroup", {
            vpc: props.vpc,
            allowAllOutbound: false,
            description: "EDH AMI recipe build instances (egress only)",
        });
        securityGroup.addEgressRule(
            ec2.Peer.anyIpv4(),
            ec2.Port.tcp(443),
            "AWS endpoints and HTTPS mirrors"
        );
        securityGroup.addEgressRule(
            ec2.Peer.anyIpv4(),
            ec2.Port.tcp(80),
            "Package mirrors over HTTP"
        );

        this.resources = {
            prefix,
            // The shipped recipes install packages from the internet, so a subnet with egress
            // is the default; a VPC built without one leaves the isolated subnets.
            subnetId:
                edh.amiBuilds.buildSubnetId ||
                (props.privateSubnets[0] ?? props.isolatedSubnets[0]).subnetId,
            securityGroup,
            instanceProfileName: instanceProfile.instanceProfileName,
            instanceRole: role,
            artefactsBucketName: props.artefactsBucket.bucketName,
        };

        const recipes = resolveEdhAmiRecipes(edh, REPO_ROOT);
        // The hash, the file reads and the bundle list read one path list per recipe.
        const recipeFiles = recipes.map(recipeFilesOf);
        this.bundleHash = bundleHashOf(recipes, REPO_ROOT);

        recipes.forEach((recipe, index) => {
            const keyPrefix = `${EDH_AMI_RECIPES_ARTEFACTS_PREFIX}/${this.bundleHash}/${recipe.recipeId}`;
            this.deployments.push(
                new s3deployment.BucketDeployment(this, `RecipeBundle-${recipe.recipeId}`, {
                    sources: [
                        s3deployment.Source.asset(path.join(REPO_ROOT, recipe.recipeDir), {
                            exclude: CREDENTIAL_EXCLUDE_GLOBS,
                        }),
                    ],
                    destinationBucket: props.artefactsBucket,
                    destinationKeyPrefix: keyPrefix,
                    prune: false,
                })
            );
            // One deployment per asset: the tree lands under the recipe's own prefix, so the build
            // instance's read grant and the registration's read both stay scoped to the recipes prefix.
            // An underscore separates the two ids in the construct id because both admit hyphens,
            // which would let two different recipe-and-asset pairs spell the same id.
            const assetBundles = recipe.assets.map((asset) => {
                const assetPrefix = `${keyPrefix}/assets/${asset.id}`;
                this.deployments.push(
                    new s3deployment.BucketDeployment(
                        this,
                        `RecipeAsset_${recipe.recipeId}_${asset.id}`,
                        {
                            sources: [
                                s3deployment.Source.asset(path.join(REPO_ROOT, asset.repoPath), {
                                    exclude: ASSET_EXCLUDE_GLOBS,
                                }),
                            ],
                            destinationBucket: props.artefactsBucket,
                            destinationKeyPrefix: assetPrefix,
                            prune: false,
                            memoryLimit: 1024,
                        }
                    )
                );
                return { id: asset.id, prefix: assetPrefix };
            });
            this.bundles.push({
                recipeId: recipe.recipeId,
                prefix: keyPrefix,
                files: recipeFiles[index],
                assets: assetBundles,
                buildOnDeploy: recipe.buildOnDeploy,
            });
        });

        NagSuppressions.addResourceSuppressions(
            role,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "EC2InstanceProfileForImageBuilder and AmazonSSMManagedInstanceCore are the two policies EC2 Image Builder documents for its build instances; the instance runs only during a build and is terminated on completion or failure.",
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Build instances read the recipe bundles and asset trees this deployment uploaded under the artefacts bucket's addon/edh/amiRecipes/ prefix. The object wildcard is scoped to that prefix, the instance exists only for the duration of one build, and no write action is granted.",
                    appliesTo: [
                        "Action::s3:GetObject*",
                        "Action::s3:GetBucket*",
                        "Action::s3:List*",
                        EDH_AMI_RECIPES_ARTEFACTS_NAG_RESOURCE,
                    ],
                },
            ],
            true
        );
        new cdk.CfnOutput(this, "EdhAmiBuildSecurityGroupId", {
            value: securityGroup.securityGroupId,
            description: "Security group of the EDH AMI recipe build instances",
        });
    }
}
