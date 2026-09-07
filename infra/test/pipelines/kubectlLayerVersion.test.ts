/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The kubectl binary in the EKS pipeline's Lambda layer must track the configured cluster version.
 *
 * kubectl supports one minor version of skew either side of the API server. The layer previously
 * downloaded a fixed 1.28.1 while `eksClusterVersion` defaulted to 1.31 — three minors out, and outside
 * that window.
 *
 * The download path cannot be derived from the version: the EKS S3 bucket publishes each release under
 * `<version>/<release-date>/`, and the date differs per release, so an interpolated date answers 404 and
 * fails inside a Docker bundling step. Hence a table of verified pairs, and hence these tests, which check
 * the table's internal consistency rather than making a network call.
 */

import * as fs from "fs";
import * as path from "path";
import {
    KUBECTL_RELEASE_PATHS,
    kubectlDownloadUrl,
} from "../../lib/nestedStacks/pipelines/multi/rapidPipelineEKS/constructs/kubectl-layer-construct";

describe("kubectl layer version tracking", () => {
    it("the release table is populated", () => {
        // Control: every assertion below iterates the table, so an empty one would pass vacuously.
        expect(Object.keys(KUBECTL_RELEASE_PATHS).length).toBeGreaterThan(2);
    });

    it("each key matches the version its path downloads", () => {
        // Catches the mis-pairing that a table invites: "1.31" pointing at a 1.30 binary reintroduces the
        // skew this fix removes, and nothing else would notice.
        for (const [minor, releasePath] of Object.entries(KUBECTL_RELEASE_PATHS)) {
            expect(releasePath.startsWith(`${minor}.`)).toBe(true);
        }
    });

    it("each path has the published <version>/<release-date> shape", () => {
        for (const releasePath of Object.values(KUBECTL_RELEASE_PATHS)) {
            expect(releasePath).toMatch(/^\d+\.\d+\.\d+\/\d{4}-\d{2}-\d{2}$/);
        }
    });

    it("builds a download URL for a supported version", () => {
        const url = kubectlDownloadUrl("1.31");
        expect(url).toContain("amazon-eks/1.31.0/");
        expect(url).toMatch(/\/bin\/linux\/amd64\/kubectl$/);
    });

    it("throws for an unmapped version, naming the config field", () => {
        // Failing at synth is the point: a 404 inside Docker bundling gives no indication of the cause.
        expect(() => kubectlDownloadUrl("1.99")).toThrow(/eksClusterVersion/);
        expect(() => kubectlDownloadUrl("1.99")).toThrow(/KUBECTL_RELEASE_PATHS/);
    });

    it("covers the version the shipped config templates request", () => {
        // Ties the table to the configuration rather than leaving it a free-floating list.
        for (const name of ["commercial", "govcloud", "eusovereign"]) {
            const template = JSON.parse(
                fs.readFileSync(
                    path.join(__dirname, "..", "..", "config", `config.template.${name}.json`),
                    "utf-8"
                )
            );
            const version = template?.app?.pipelines?.useRapidPipeline?.useEks?.eksClusterVersion;
            if (!version) continue;
            expect(Object.keys(KUBECTL_RELEASE_PATHS)).toContain(String(version));
        }
    });
});
