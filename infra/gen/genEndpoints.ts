/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/*
 * Regenerates lib/helper/const.ts (the partition-aware SERVICE_LOOKUP table) from the
 * authoritative botocore endpoints.json "master endpoints" file.
 *
 * This generator performs a NON-DESTRUCTIVE MERGE against the existing const.ts:
 *   - Existing services and partition entries are PRESERVED (including hand-tuned values and
 *     services that botocore no longer publishes — "stale" services).
 *   - Missing partition entries for an existing service are ADDED from the master file
 *     (this is how new partitions like aws-eusc, and services added to aws-cn/aws-us-gov/
 *     aws-iso over time, get filled in).
 *   - Brand-new services present in the master file but not yet in const.ts are ADDED.
 *
 * Run with:  npm run gen     (from the infra/ directory)
 *
 * Notes:
 *   - "es" is surfaced as the OpenSearch service: the principal/hostname use the
 *     "opensearchservice" prefix while the ARN keeps the "es" service token.
 *   - sagemaker / execute-api / ecs-tasks / ecr-dkr are injected into every partition because
 *     botocore omits them from the endpoints services map.
 *   - Some partitions only publish a dnsSuffix and not a usable per-service endpoint; in those
 *     cases the generated hostname/fipsHostname may need to be blanked manually for a given
 *     service. The merge never overwrites an existing entry, so manual corrections are retained.
 */

const https = require("https");
const fs = require("fs");
const path = require("path");

const url = "https://raw.githubusercontent.com/boto/botocore/master/botocore/data/endpoints.json";

const CONST_PATH = path.join(__dirname, "../lib/helper/const.ts");
const REPORT_PATH = path.join(__dirname, "../../const-endpoints-change-report.md");

interface IServiceInfo {
    arn: string;
    principal: string;
    hostname: string;
    fipsHostname: string;
}

type PartitionMap = { [partition: string]: IServiceInfo };
type ServiceLookup = { [serviceKey: string]: PartitionMap };

// Services botocore omits from its services map but VAMS still needs in every partition.
const INJECTED_SERVICES = ["sagemaker", "execute-api", "ecs-tasks", "ecr-dkr"];

/**
 * Build a fresh SERVICE_LOOKUP straight from the botocore master file. This is the "ideal"
 * generated table that the existing const.ts is merged on top of.
 */
function buildMasterLookup(json: any): ServiceLookup {
    const allServices = new Set<string>();
    const perPartition: { [partition: string]: { [service: string]: IServiceInfo } } = {};

    json["partitions"].forEach((v: any) => {
        const partitionName = v["partition"];
        const dnsSuffix = v["dnsSuffix"];

        // Inject services missing from botocore's services map.
        for (const injected of INJECTED_SERVICES) {
            v["services"][injected] = v["services"][injected] || {};
        }

        if (!perPartition[partitionName]) perPartition[partitionName] = {};

        for (const serviceKey in v["services"]) {
            allServices.add(serviceKey);

            // "es" is published as OpenSearch: principal/hostname use opensearchservice prefix,
            // ARN keeps the es service token.
            let principalPrefix = serviceKey;
            if (serviceKey === "es") principalPrefix = "opensearchservice";

            perPartition[partitionName][serviceKey] = {
                arn: `arn:${partitionName}:${serviceKey}:{region}:{account-id}:{resource-id}`,
                principal: `${principalPrefix}.${dnsSuffix}`,
                hostname: `${principalPrefix}.{region}.${dnsSuffix}`,
                fipsHostname: `${principalPrefix}-fips.{region}.${dnsSuffix}`,
            };
        }
    });

    const master: ServiceLookup = {};
    for (const serviceKey of [...allServices]) {
        const partitions: PartitionMap = {};
        for (const partitionName in perPartition) {
            const svc = perPartition[partitionName][serviceKey];
            if (svc) partitions[partitionName] = svc;
        }
        master[serviceKey] = partitions;
    }
    return master;
}

/**
 * Read the existing SERVICE_LOOKUP object literal out of const.ts so we can merge onto it.
 * The literal is repo-owned generated code; we evaluate only the object slice after the '='.
 */
function readExistingLookup(): ServiceLookup {
    if (!fs.existsSync(CONST_PATH)) return {};
    const ts = fs.readFileSync(CONST_PATH, "utf8");
    const marker = "export const SERVICE_LOOKUP";
    const idx = ts.indexOf(marker);
    if (idx === -1) return {};
    const eq = ts.indexOf("=", idx);
    const braceStart = ts.indexOf("{", eq);
    let depth = 0;
    let end = -1;
    for (let i = braceStart; i < ts.length; i++) {
        const c = ts[i];
        if (c === "{") depth++;
        else if (c === "}") {
            depth--;
            if (depth === 0) {
                end = i;
                break;
            }
        }
    }
    const objText = ts.slice(braceStart, end + 1);
    // eslint-disable-next-line no-eval
    return eval("(" + objText + ")") as ServiceLookup;
}

interface MergeReport {
    euscAdditions: { service: string }[];
    gapFills: { [partition: string]: string[] };
    newServices: string[];
    staleServices: string[];
}

/**
 * Merge master onto existing (existing wins on conflicts). Returns the merged table and a
 * structured report of what changed.
 */
function mergeLookups(
    existing: ServiceLookup,
    master: ServiceLookup
): { merged: ServiceLookup; report: MergeReport } {
    const merged: ServiceLookup = {};
    const report: MergeReport = {
        euscAdditions: [],
        gapFills: {},
        newServices: [],
        staleServices: [],
    };

    const existingKeys = Object.keys(existing);
    const masterKeys = Object.keys(master);

    // Carry over every existing service first (preserves stale services + hand-tuned values).
    for (const serviceKey of existingKeys) {
        merged[serviceKey] = { ...existing[serviceKey] };
    }

    // Track stale services (present in const, no longer in master).
    for (const serviceKey of existingKeys) {
        if (!master[serviceKey]) report.staleServices.push(serviceKey);
    }

    // Gap-fill existing services with partition entries they are missing from the master.
    for (const serviceKey of existingKeys) {
        const masterPartitions = master[serviceKey];
        if (!masterPartitions) continue; // stale service, nothing to fill
        for (const partitionName of Object.keys(masterPartitions)) {
            if (!merged[serviceKey][partitionName]) {
                merged[serviceKey][partitionName] = masterPartitions[partitionName];
                if (partitionName === "aws-eusc") {
                    report.euscAdditions.push({ service: serviceKey });
                } else {
                    if (!report.gapFills[partitionName]) report.gapFills[partitionName] = [];
                    report.gapFills[partitionName].push(serviceKey);
                }
            }
        }
    }

    // Add brand-new services present in master but missing from const.
    for (const serviceKey of masterKeys) {
        if (!existing[serviceKey]) {
            merged[serviceKey] = { ...master[serviceKey] };
            report.newServices.push(serviceKey);
        }
    }

    return { merged, report };
}

function writeConstFile(merged: ServiceLookup) {
    // Sort service keys for stable diffs.
    const sortedKeys = Object.keys(merged).sort();
    const serviceLookup: ServiceLookup = {};
    const typeToKeyLookup: { [typedKey: string]: string } = {};
    for (const key of sortedKeys) {
        serviceLookup[key] = merged[key];
        typeToKeyLookup[key.replace(/[-.]/gi, "_").toUpperCase()] = key;
    }

    const serviceType = `export type SERVICE = ${sortedKeys
        .map((k) => `'${k.replace(/[-.]/gi, "_").toUpperCase()}'`)
        .sort()
        .join(" | \n\t")};`;

    const serviceKeyLookup = `export const TYPE_SERVICE_LOOKUP = ${JSON.stringify(
        typeToKeyLookup,
        null,
        3
    )};`;

    const serviceLookupInterface = `export interface IServiceInfo {
    arn: string,
    principal: string,
    hostname: string,
    fipsHostname: string,
};`;

    const serviceLookupOut = `export const SERVICE_LOOKUP : {[key: string] : { [partition: string]: IServiceInfo }} = ${JSON.stringify(
        serviceLookup,
        null,
        3
    )};`;

    const header = `/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
`;

    fs.writeFileSync(
        CONST_PATH,
        `${header}${[serviceType, serviceKeyLookup, serviceLookupInterface, serviceLookupOut].join(
            "\n"
        )}\n`
    );
}

function writeReport(report: MergeReport, master: ServiceLookup, existing: ServiceLookup) {
    const lines: string[] = [];
    lines.push("# const.ts Endpoint Partition Change Report");
    lines.push("");
    lines.push(
        "Generated by `infra/gen/genEndpoints.ts` from the botocore master endpoints.json. " +
            "This report summarizes the non-destructive merge applied to `infra/lib/helper/const.ts`."
    );
    lines.push("");
    lines.push("## Summary");
    lines.push("");
    lines.push(
        `- New \`aws-eusc\` (EU Sovereign Cloud) entries added: **${report.euscAdditions.length}**`
    );
    let gapTotal = 0;
    for (const p of Object.keys(report.gapFills)) gapTotal += report.gapFills[p].length;
    lines.push(
        `- Existing-partition gap-fills (service added to a partition over time): **${gapTotal}**`
    );
    lines.push(`- Brand-new services added: **${report.newServices.length}**`);
    lines.push(
        `- Stale services (in const.ts, no longer in botocore master): **${report.staleServices.length}**`
    );
    lines.push("");

    lines.push("## 1. New aws-eusc (EU Sovereign Cloud) additions");
    lines.push("");
    lines.push(
        "These existing services gained an `aws-eusc` partition entry (dnsSuffix `amazonaws.eu`)."
    );
    lines.push("");
    if (report.euscAdditions.length) {
        for (const a of report.euscAdditions.sort((x, y) => x.service.localeCompare(y.service))) {
            lines.push(`- \`${a.service}\``);
        }
    } else {
        lines.push("_None._");
    }
    lines.push("");

    lines.push("## 2. Existing-partition gap-fills");
    lines.push("");
    lines.push(
        "Partitions VAMS already supported where the master file now carries data the const.ts " +
            "entry was missing (services that were added to these partitions over time)."
    );
    lines.push("");
    const gapPartitions = Object.keys(report.gapFills).sort();
    if (gapPartitions.length) {
        for (const p of gapPartitions) {
            const svcs = report.gapFills[p].sort();
            lines.push(`### ${p} (${svcs.length})`);
            lines.push("");
            for (const s of svcs) lines.push(`- \`${s}\``);
            lines.push("");
        }
    } else {
        lines.push("_None._");
        lines.push("");
    }

    lines.push("## 3. Brand-new services added");
    lines.push("");
    lines.push(
        "Services present in the botocore master file but not previously in const.ts. Added with " +
            "all partitions the master publishes for them."
    );
    lines.push("");
    if (report.newServices.length) {
        for (const s of report.newServices.sort()) {
            const parts = Object.keys(master[s]).sort().join(", ");
            lines.push(`- \`${s}\` (partitions: ${parts})`);
        }
    } else {
        lines.push("_None._");
    }
    lines.push("");

    lines.push("## 4. Stale services — REVIEW / possible bad names");
    lines.push("");
    lines.push(
        "These service keys exist in const.ts but are **not present in the botocore master file " +
            "under any partition**. They were preserved (not removed) by the merge. They may be: " +
            "(a) legitimately retired AWS services still referenced somewhere, or (b) incorrectly " +
            "named entries that should be remediated throughout the CDK solution. Each is listed " +
            "with the partitions currently defined for it in const.ts."
    );
    lines.push("");
    if (report.staleServices.length) {
        for (const s of report.staleServices.sort()) {
            const parts = Object.keys(existing[s]).sort().join(", ");
            lines.push(`- \`${s}\` (defined for: ${parts})`);
        }
    } else {
        lines.push("_None._");
    }
    lines.push("");

    fs.writeFileSync(REPORT_PATH, lines.join("\n"));
    console.log(`Wrote change report to ${REPORT_PATH}`);
}

https
    .get(url, (res: any) => {
        let body = "";
        res.on("data", (chunk: any) => (body += chunk));
        res.on("end", () => {
            try {
                const json = JSON.parse(body);
                const master = buildMasterLookup(json);
                const existing = readExistingLookup();
                const { merged, report } = mergeLookups(existing, master);
                writeConstFile(merged);
                writeReport(report, master, existing);
                console.log(
                    `const.ts regenerated. Services: ${Object.keys(merged).length} ` +
                        `(eusc additions ${report.euscAdditions.length}, new services ${report.newServices.length}, ` +
                        `stale preserved ${report.staleServices.length}).`
                );
            } catch (error: any) {
                console.error(error.message);
                process.exitCode = 1;
            }
        });
    })
    .on("error", (error: any) => {
        console.error(error.message);
        process.exitCode = 1;
    });
