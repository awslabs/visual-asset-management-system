/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Staged asset export payloads expire.
 *
 * An export response too large to return inline is written to the auxiliary bucket under
 * `Config.ASSET_EXPORT_STAGING_PREFIX` and handed back as a presigned URL. The export service never
 * deletes the payload, because the client may read the URL more than once until it expires, so the
 * auxiliary bucket's lifecycle configuration is what removes it:
 *
 *   * a rule on the prefix expires the current version after `ASSET_EXPORT_STAGING_EXPIRATION_DAYS`
 *     and the noncurrent version a day later (the bucket is versioned, so an expiry only writes a
 *     delete marker), and a second rule removes the expired delete markers;
 *   * the expiry outlives the longest lifetime a presigned URL can have, and the presigned URL
 *     lifetime each shipped template configures;
 *   * the rules are on in every deployment shape, with no pipeline or add-on enabled, in the
 *     GovCloud and EU Sovereign templates and with the hardened options on.
 *
 * The Python handler keys the payload under the same literal, which the last test reads from source.
 * Asserted on the emitted templates; the control is the bucket's own multipart-upload rule.
 */

import * as fs from "fs";
import * as path from "path";
import * as Config from "../../config/config";
import {
    buildTemplateConfig,
    Resource,
    SynthResult,
    synthTemplate,
    TemplateName,
} from "../support/templateSynth";

const PREFIX = Config.ASSET_EXPORT_STAGING_PREFIX;
const EXPORT_HANDLER = path.resolve(
    __dirname,
    "../../../backend/backend/handlers/assets/assetExportService.py"
);
const SECONDS_PER_DAY = 86400;

/** Commercial without WAF: CloudFront, no VPC, no customer managed key. */
function minimal(c: any) {
    c.app.useWaf = false;
    c.app.useGlobalVpc.enabled = false;
    c.app.useKmsCmkEncryption.enabled = false;
}

/** Commercial with a KMS CMK, the global VPC for every Lambda, WAF, and the ALB in private subnets. */
function hardened(c: any) {
    c.app.useWaf = true;
    c.app.useKmsCmkEncryption.enabled = true;
    c.app.useGlobalVpc.enabled = true;
    c.app.useGlobalVpc.useForAllLambdas = true;
    c.app.useCloudFront.enabled = false;
    c.app.useAlb.enabled = true;
    c.app.useAlb.usePublicSubnet = false;
}

interface Cell {
    name: string;
    template: TemplateName;
    mutate?: (c: any) => void;
}

const CELLS: Cell[] = [
    { name: "commercial default", template: "commercial" },
    { name: "commercial minimal", template: "commercial", mutate: minimal },
    { name: "commercial hardened", template: "commercial", mutate: hardened },
    { name: "govcloud", template: "govcloud" },
    { name: "eusovereign", template: "eusovereign" },
];

function synthCell(cell: Cell): SynthResult {
    return cell.mutate
        ? synthTemplate(cell.template, {
              mutate: cell.mutate,
              mutateKey: `asset-export-staging-expiry-${cell.name}`,
          })
        : synthTemplate(cell.template);
}

function auxiliaryBucket(synth: SynthResult): Resource {
    const buckets = synth
        .ofType("AWS::S3::Bucket")
        .filter(
            (b) =>
                /StorageResourcesBuilder/.test(b.stack) &&
                b.logicalId.startsWith("AssetAuxiliaryBucket")
        );
    expect(buckets).toHaveLength(1);
    return buckets[0];
}

const rulesOf = (bucket: Resource): any[] => bucket.properties.LifecycleConfiguration?.Rules ?? [];
const stagingRules = (bucket: Resource): any[] =>
    rulesOf(bucket).filter((rule: any) => rule.Prefix === PREFIX);

describe("asset export staging expiry", () => {
    test("the expiry outlives the longest presigned URL lifetime by at least a day", () => {
        const longestUrlDays = Config.PRESIGNED_URL_MAX_TIMEOUT_SECONDS / SECONDS_PER_DAY;
        expect(Number.isInteger(Config.ASSET_EXPORT_STAGING_EXPIRATION_DAYS)).toBe(true);
        expect(Config.ASSET_EXPORT_STAGING_EXPIRATION_DAYS).toBeGreaterThanOrEqual(
            Math.ceil(longestUrlDays) + 1
        );
    });

    describe.each(CELLS)("$name", (cell) => {
        let bucket: Resource;

        beforeAll(() => {
            bucket = auxiliaryBucket(synthCell(cell));
        });

        test("the bucket's own rules are emitted (control)", () => {
            expect(
                rulesOf(bucket).some(
                    (rule: any) => rule.AbortIncompleteMultipartUpload?.DaysAfterInitiation === 14
                )
            ).toBe(true);
        });

        test("the bucket is versioned, so the noncurrent expiry is what reclaims the storage", () => {
            expect(bucket.properties.VersioningConfiguration).toEqual({ Status: "Enabled" });
        });

        test("staged payloads expire, with their noncurrent versions and delete markers", () => {
            const rules = stagingRules(bucket);
            expect(rules).toHaveLength(2);
            const expiry = rules.find((rule: any) => rule.ExpirationInDays !== undefined);
            expect(expiry).toEqual({
                Status: "Enabled",
                Prefix: PREFIX,
                ExpirationInDays: Config.ASSET_EXPORT_STAGING_EXPIRATION_DAYS,
                NoncurrentVersionExpiration: { NoncurrentDays: 1 },
            });
            const markers = rules.find((rule: any) => rule.ExpiredObjectDeleteMarker !== undefined);
            expect(markers).toEqual({
                Status: "Enabled",
                Prefix: PREFIX,
                ExpiredObjectDeleteMarker: true,
            });
        });

        test("the expiry outlives the presigned URL lifetime the template configures", () => {
            const configured = Number(
                buildTemplateConfig(cell.template, cell.mutate).app.authProvider
                    .presignedUrlTimeoutSeconds
            );
            expect(configured).toBeGreaterThan(0);
            const expiry = stagingRules(bucket).find(
                (rule: any) => rule.ExpirationInDays !== undefined
            );
            expect(expiry.ExpirationInDays * SECONDS_PER_DAY).toBeGreaterThan(
                configured + SECONDS_PER_DAY - 1
            );
        });
    });

    test("the export handler stages payloads under the same prefix", () => {
        const source = fs.readFileSync(EXPORT_HANDLER, "utf-8");
        expect(source).toContain(`EXPORT_STAGING_PREFIX = "${PREFIX}"`);
        expect(source).toContain('object_key = f"{EXPORT_STAGING_PREFIX}');
    });
});
