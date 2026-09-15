/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * No Kubernetes manifest or container definition in the tree may reference a moving image tag.
 *
 * The case that prompted this is the Container Insights DaemonSet: it runs on every EKS node with the host
 * filesystem and the container-runtime socket mounted, so `:latest` means what holds that access can change
 * without any change to this repository — and without a redeploy, since a DaemonSet pod restart re-pulls.
 *
 * Asserted against the SOURCE rather than a synthesized template. The manifests are `cluster.addManifest`
 * payloads that CDK serialises into a custom-resource property, and the EKS pipeline is disabled in every
 * shipped config template, so a template assertion would inspect nothing.
 */

import * as fs from "fs";
import * as path from "path";

const PIPELINES_DIR = path.join(__dirname, "..", "..", "lib", "nestedStacks", "pipelines");

function typescriptFiles(dir: string): string[] {
    const out: string[] = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) out.push(...typescriptFiles(full));
        else if (entry.name.endsWith(".ts")) out.push(full);
    }
    return out;
}

describe("container images are pinned", () => {
    const files = typescriptFiles(PIPELINES_DIR);

    it("finds the pipeline construct sources to scan", () => {
        // Control: an empty file list would make every assertion below pass vacuously.
        expect(files.length).toBeGreaterThan(10);
    });

    it("no image reference uses a moving tag", () => {
        // `:latest` and a bare `:main`/`:master` are all mutable. A digest or a version tag is not.
        const offenders: string[] = [];
        for (const file of files) {
            const text = fs.readFileSync(file, "utf-8");
            for (const line of text.split(/\r?\n/)) {
                if (/^\s*(\/\/|\*)/.test(line)) continue; // a comment mentioning :latest is not a reference
                if (/image:\s*["'`][^"'`]*:(latest|main|master)["'`]/.test(line)) {
                    offenders.push(`${path.basename(file)}: ${line.trim().slice(0, 100)}`);
                }
            }
        }
        expect(offenders).toEqual([]);
    });

    it("the Container Insights agent is pinned to a version tag", () => {
        // The specific case, asserted by name so a regression is diagnosable rather than just red.
        const eksConstruct = files.find((f) => f.endsWith("rapidPipelineEKS-construct.ts"));
        expect(eksConstruct).toBeDefined();
        const text = fs.readFileSync(eksConstruct as string, "utf-8");
        const match = text.match(/cloudwatch-agent\/cloudwatch-agent:([^"'`]+)/);
        expect(match).not.toBeNull();
        // A version tag, not a moving one. The bare version is the multi-arch manifest; the
        // -amd64/-arm64 suffixed tags are single-architecture and would break on a different node type.
        expect(match?.[1]).toMatch(/^\d+\.\d+/);
        expect(match?.[1]).not.toMatch(/-(amd64|arm64|windowsservercore)/);
    });
});
