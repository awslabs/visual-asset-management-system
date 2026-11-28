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
 *   * the bucket-wide incomplete-multipart abort rule, which names no prefix, covers it too;
 *   * the rules are on in every deployment shape, with no pipeline or add-on enabled, in the
 *     GovCloud and EU Sovereign templates and with the hardened options on;
 *   * the prefix cannot be a database ID. The bucket keys each database's preview and viewer data
 *     under `{databaseId}/`, so a prefix whose first segment a database ID could take would let
 *     the expiry delete that database's data. The database ID pattern is read from
 *     backend/backend/common/validators.py.
 *
 * The Python handler keys the payload under the same literal, which the last test reads from source.
 * Asserted on the emitted templates; the control is the bucket's own multipart-upload rule.
 */

import * as fs from "fs";
import * as path from "path";
import * as Config from "../../config/config";
import { PREPARED_DOWNLOADS_PREFIX } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
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
const VALIDATORS = path.resolve(__dirname, "../../../backend/backend/common/validators.py");
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

/** The database ID pattern the backend validates `databaseId` against. */
function databaseIdPattern(): RegExp {
    const source = fs.readFileSync(VALIDATORS, "utf-8");
    const match = /^id_pattern = r'([^']+)'$/m.exec(source);
    expect(match).not.toBeNull();
    return new RegExp((match as RegExpExecArray)[1]);
}

describe("asset export staging expiry", () => {
    test("the staging prefix carries a character no database ID can contain", () => {
        const pattern = databaseIdPattern();
        // Control: the pattern accepts an ordinary ID and the bare word.
        expect(pattern.test("db1")).toBe(true);
        expect(pattern.test("assetExports")).toBe(true);
        expect(PREFIX).toBe("vams.assetExports/");
        const firstSegment = PREFIX.split("/")[0];
        expect(pattern.test(firstSegment)).toBe(false);
        expect(firstSegment).toMatch(/[^-_a-zA-Z0-9]/);
    });

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

        test("the incomplete-multipart abort rule is bucket-wide, so it covers the prefix", () => {
            // The Download as Zip rule (vams.preparedDownloads/) aborts its own uploads after one
            // day; the bucket-wide rule is the one that covers the staged export prefix.
            const abort = rulesOf(bucket).filter(
                (rule: any) =>
                    rule.AbortIncompleteMultipartUpload !== undefined &&
                    rule.Prefix !== PREPARED_DOWNLOADS_PREFIX
            );
            expect(abort).toHaveLength(1);
            expect(abort[0].Status).toBe("Enabled");
            expect(abort[0].Prefix).toBeUndefined();
            expect(abort[0].Filter).toBeUndefined();
        });

        test("no rule names the database-shaped prefix", () => {
            expect(rulesOf(bucket).filter((rule: any) => rule.Prefix === "assetExports/")).toEqual(
                []
            );
        });

        test("the bucket is versioned, so the noncurrent expiry is what reclaims the storage", () => {
            expect(bucket.properties.VersioningConfiguration).toEqual({ Status: "Enabled" });
        });

        test("the prefix carries exactly two rules, both enabled", () => {
            const rules = stagingRules(bucket);
            expect(rules).toHaveLength(2);
            expect(rules.every((rule: any) => rule.Status === "Enabled")).toBe(true);
        });

        test("current staged payloads expire after 8 days", () => {
            const expiry = stagingRules(bucket).filter(
                (rule: any) => rule.ExpirationInDays !== undefined
            );
            expect(expiry).toHaveLength(1);
            expect(expiry[0].ExpirationInDays).toBe(Config.ASSET_EXPORT_STAGING_EXPIRATION_DAYS);
            expect(expiry[0].ExpirationInDays).toBe(8);
        });

        test("noncurrent versions of staged payloads expire a day after they become noncurrent", () => {
            const noncurrent = stagingRules(bucket).filter(
                (rule: any) => rule.NoncurrentVersionExpiration !== undefined
            );
            expect(noncurrent).toHaveLength(1);
            expect(noncurrent[0].NoncurrentVersionExpiration).toEqual({ NoncurrentDays: 1 });
        });

        test("expired delete markers under the prefix are removed", () => {
            const markers = stagingRules(bucket).filter(
                (rule: any) => rule.ExpiredObjectDeleteMarker !== undefined
            );
            expect(markers).toHaveLength(1);
            expect(markers[0]).toEqual({
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
