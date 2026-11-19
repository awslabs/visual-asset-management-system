/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The RapidPipeline EKS Kubernetes layer and the VAMS base layer never ship two versions of one package.
 *
 * The pipeline Lambda attaches both layers, and Lambda extracts both into `/opt/python`, the later
 * layer's files over the earlier one's. A package both layers carry at different versions therefore
 * ends up as a mix of two releases: the overwritten files are the later layer's, the files only the
 * earlier release has stay beside them, and both `*.dist-info` directories are present. For the AWS
 * SDK, the version the function runs is then decided by layer order, and `importlib.metadata` can
 * report the other copy. For `charset-normalizer` the mix does not import at all: a newer release's
 * compiled `cd` module stays and reads a constant an older release's `constant.py` does not define.
 *
 * Three claims are asserted:
 *
 *   1. the Kubernetes layer's `pyproject.toml`, `poetry.lock` and committed `requirements.txt` name none
 *      of the SDK packages, so the base layer is the function's only SDK (the layer build installs the
 *      committed export of the lock, so the lock is what ships);
 *   2. every other package both layers' locks carry is locked at the same version;
 *   3. in the synthesized template, every Lambda function that lists the Kubernetes layer also lists
 *      the base layer, which (1) depends on.
 *
 * Positive controls, because each claim is negative or universal and an empty input satisfies it: the
 * locks do carry `kubernetes` and the shared HTTP packages, the comparison reports a drifted pin, and
 * the synth does contain a function with the Kubernetes layer.
 */

import * as fs from "fs";
import * as path from "path";
import { Resource, SynthResult, synthTemplate } from "../support/templateSynth";

const VAMS_ROOT = path.resolve(__dirname, "../../..");
const KUBERNETES_LAYER_DIR = path.join(
    VAMS_ROOT,
    "backendPipelines/multi/rapidPipelineEKS/lambdaLayer"
);
const BASE_LAYER_LOCK = path.join(VAMS_ROOT, "backend/lambdaLayers/base/uv.lock");
const SDK_PACKAGES = ["boto3", "botocore", "s3transfer"];

/** PEP 503 normalized project name, so `typing_extensions` and `typing-extensions` match. */
const normalize = (name: string): string => name.replace(/[-_.]+/g, "-").toLowerCase();

const readLayerFile = (file: string): string =>
    fs.readFileSync(path.join(KUBERNETES_LAYER_DIR, file), "utf8");

/** {normalized name: version} for every `[[package]]` table in a poetry.lock or uv.lock. */
function lockedVersions(lock: string): Map<string, string> {
    const versions = new Map<string, string>();
    for (const m of lock.matchAll(
        /^\[\[package\]\]\s*\r?\nname = "([^"]+)"\s*\r?\nversion = "([^"]+)"/gm
    )) {
        versions.set(normalize(m[1]), m[2]);
    }
    return versions;
}

/** Every `name==version` pin in a Poetry-exported requirements file. */
function pinnedPackages(requirements: string): string[] {
    return requirements
        .split(/\r?\n/)
        .map((line) => /^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==/.exec(line.trim()))
        .filter((m): m is RegExpExecArray => m !== null)
        .map((m) => normalize(m[1]));
}

/** The keys of `[tool.poetry.dependencies]` in a pyproject.toml. */
function declaredDependencies(pyproject: string): string[] {
    const section = /^\[tool\.poetry\.dependencies\]\s*$([\s\S]*?)(?=^\[|(?![\s\S]))/m.exec(
        pyproject
    );
    if (!section) return [];
    return section[1]
        .split(/\r?\n/)
        .map((line) => /^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*=/.exec(line))
        .filter((m): m is RegExpExecArray => m !== null)
        .map((m) => normalize(m[1]));
}

/** [name, Kubernetes layer version, base layer version] for each shared package at different versions. */
function versionDrift(
    kubernetesLayer: Map<string, string>,
    baseLayer: Map<string, string>
): [string, string, string][] {
    const drift: [string, string, string][] = [];
    for (const [name, version] of kubernetesLayer) {
        const baseVersion = baseLayer.get(name);
        if (baseVersion !== undefined && baseVersion !== version) {
            drift.push([name, version, baseVersion]);
        }
    }
    return drift.sort(([a], [b]) => a.localeCompare(b));
}

describe("RapidPipeline EKS Kubernetes layer: packages it shares with the base layer", () => {
    const kubernetesLock = (): Map<string, string> => lockedVersions(readLayerFile("poetry.lock"));
    const baseLock = (): Map<string, string> =>
        lockedVersions(fs.readFileSync(BASE_LAYER_LOCK, "utf8"));

    test("the locks, the export and the pyproject are read (control)", () => {
        expect(kubernetesLock().get("kubernetes")).toBeDefined();
        expect(baseLock().get("boto3")).toBeDefined();
        expect(pinnedPackages(readLayerFile("requirements.txt"))).toContain("kubernetes");
        expect(declaredDependencies(readLayerFile("pyproject.toml"))).toContain("kubernetes");
        // The packages the Kubernetes client pulls in that the base layer also ships; an empty
        // intersection would make the parity check below compare nothing.
        const shared = Array.from(kubernetesLock().keys()).filter((name) => baseLock().has(name));
        expect(shared).toEqual(expect.arrayContaining(["requests", "urllib3", "certifi"]));
    });

    test("pyproject.toml declares no AWS SDK package", () => {
        const declared = declaredDependencies(readLayerFile("pyproject.toml"));
        expect(SDK_PACKAGES.filter((p) => declared.includes(p))).toEqual([]);
    });

    test("poetry.lock locks no AWS SDK package", () => {
        const locked = kubernetesLock();
        expect(SDK_PACKAGES.filter((p) => locked.has(p))).toEqual([]);
    });

    test("the exported requirements.txt pins no AWS SDK package", () => {
        const pinned = pinnedPackages(readLayerFile("requirements.txt"));
        expect(SDK_PACKAGES.filter((p) => pinned.includes(p))).toEqual([]);
    });

    test("the comparison reports a drifted pin (control)", () => {
        const drift = versionDrift(
            new Map([
                ["charset-normalizer", "3.4.6"],
                ["requests", "2.34.2"],
                ["kubernetes", "36.0.3"],
            ]),
            new Map([
                ["charset-normalizer", "3.3.2"],
                ["requests", "2.34.2"],
                ["casbin", "1.36.0"],
            ])
        );
        expect(drift).toEqual([["charset-normalizer", "3.4.6", "3.3.2"]]);
    });

    test("every package both layers lock is at the base layer's version", () => {
        // On a failure, re-lock the Kubernetes layer to the base layer's version: add a temporary
        // exact constraint to its pyproject.toml, `poetry lock`, remove it, `poetry lock` again, and
        // re-export (backend/CLAUDE.md "Updating Python Dependencies"). The base layer's lock is a uv.lock.
        expect(versionDrift(kubernetesLock(), baseLock())).toEqual([]);
    });
});

describe("RapidPipeline EKS Kubernetes layer: attached only with the base layer", () => {
    let synth: SynthResult;

    beforeAll(() => {
        // The same configuration eksLeastPrivilege.test.ts synthesizes, so a full run shares one synth.
        synth = synthTemplate("commercial", {
            mutate: (c) => {
                c.app.useGlobalVpc.enabled = true;
                c.app.useKmsCmkEncryption.enabled = true;
                c.app.pipelines.useRapidPipeline.useEks.enabled = true;
                c.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI =
                    "709825985650.dkr.ecr.us-east-1.amazonaws.com/vendor/product:0.0.1";
            },
            mutateKey: "eks-pipeline-cmk-enabled",
        });
    });

    const layersOf = (fn: Resource): string[] =>
        ((fn.properties.Layers ?? []) as unknown[]).map((layer) => SynthResult.flatten(layer));

    test("every function with the Kubernetes layer also has the base layer", () => {
        const kubernetesLayers = synth
            .ofType("AWS::Lambda::LayerVersion")
            .filter((r) => /KubernetesLambdaLayer/.test(r.logicalId));
        expect(kubernetesLayers).toHaveLength(1);
        const kubernetesRef = `\${${kubernetesLayers[0].logicalId}}`;

        // In the stack that defines the layer a function references it by its logical ID; in any other
        // stack it would arrive as a nested-stack parameter whose name carries the same construct ID.
        // Matching the construct ID finds both forms, in every stack.
        const listsKubernetesLayer = (fn: Resource): boolean =>
            layersOf(fn).some((layer) => /KubernetesLambdaLayer/.test(layer));
        const withKubernetesLayer = synth
            .ofType("AWS::Lambda::Function")
            .filter(listsKubernetesLayer);
        // Control: the pipeline Lambda is in this synth and lists the layer.
        expect(withKubernetesLayer.map((fn) => fn.logicalId)).toEqual(
            expect.arrayContaining([expect.stringMatching(/ConsolidatedHandler/)])
        );
        // Control: the match above finds the reference form the template actually emits.
        expect(withKubernetesLayer.some((fn) => layersOf(fn).includes(kubernetesRef))).toBe(true);

        const missingBaseLayer = withKubernetesLayer
            .filter((fn) => !layersOf(fn).some((layer) => /VAMSLayerBase/.test(layer)))
            .map((fn) => ({ function: fn.logicalId, layers: layersOf(fn) }));
        expect(missingBaseLayer).toEqual([]);
    });
});
