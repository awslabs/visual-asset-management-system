/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Pre-deploy gate: the deployment Region's account quotas must actually admit what config.json asks for.
 *
 * WHY THIS IS A GATE AND NOT A `getConfig()` VALIDATION. Answering the question needs a live
 * `service-quotas` call, and `cdk synth` has to work offline and against a placeholder account — so the
 * check cannot live there. Baking a static per-Region table into `config.ts` instead would go stale as
 * quotas are raised and start REJECTING valid configurations, which is worse than the failure it prevents:
 * a wrong rejection blocks a correct deployment with no workaround but editing VAMS. A gate run
 * immediately before `cdk deploy` has neither problem — it asks AWS, and it costs seconds.
 *
 * MEASURED, which is why it exists. `app.api.apiGatewayRest.apiGatewayTimeoutTime` was 87 (seconds), and
 * the account's `L-E5AE38E3` quota is 87000 ms in us-west-2 but the untouched default 29000 ms in
 * us-east-1. The deploy ran for roughly twenty minutes, created over a thousand resources, and then failed
 * importing the OpenAPI document with one line per route:
 *
 *     Unable to put integration on 'GET' for resource at path '/tags':
 *       Timeout should be between 50 ms and 29000 ms
 *
 * repeated for every one of ~100 paths, and rolled the whole core stack back. The message never names the
 * configured value or the quota, so the ~100 identical lines read as a VAMS defect rather than as one
 * account limit.
 *
 * Exit codes: 0 pass, 1 a configured value exceeds the Region's quota, 2 the check could not be performed.
 * A check that could not run is NOT a pass — the caller must be able to tell the difference.
 */

import { execFileSync } from "child_process";
import * as fs from "fs";
import * as path from "path";

/** One quota that a config value must fit inside. */
interface QuotaCheck {
    /** Where the value comes from, for the message. */
    configPath: string;
    serviceCode: string;
    quotaCode: string;
    quotaName: string;
    /** The configured value, already converted to the quota's units. */
    configured: number | undefined;
    units: string;
    /** What the operator has to do when it does not fit. */
    remedy: string;
}

function readConfig(): any {
    const p = path.join(__dirname, "..", "config", "config.json");
    if (!fs.existsSync(p)) {
        console.log(`assertDeployQuotas: no config.json at ${p} — nothing to check.`);
        process.exit(2);
    }
    return JSON.parse(fs.readFileSync(p, "utf8"));
}

function appliedQuota(region: string, serviceCode: string, quotaCode: string): number | undefined {
    // The applied value is what the account actually has. `get-service-quota` returns the applied value
    // when an increase was granted and the default otherwise, which is exactly the number that matters.
    try {
        const out = execFileSync(
            "aws",
            [
                "service-quotas",
                "get-service-quota",
                "--region",
                region,
                "--service-code",
                serviceCode,
                "--quota-code",
                quotaCode,
                "--query",
                "Quota.Value",
                "--output",
                "text",
            ],
            { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }
        ).trim();
        const value = Number(out);
        return Number.isFinite(value) ? value : undefined;
    } catch {
        return undefined;
    }
}

function main(): void {
    const config = readConfig();
    const region: string | undefined =
        config?.env?.region || process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION;
    if (!region) {
        console.log("assertDeployQuotas: no region in config.env.region or the environment.");
        process.exit(2);
    }

    const timeoutSeconds = config?.app?.api?.apiGatewayRest?.apiGatewayTimeoutTime;

    const checks: QuotaCheck[] = [
        {
            configPath: "app.api.apiGatewayRest.apiGatewayTimeoutTime",
            serviceCode: "apigateway",
            quotaCode: "L-E5AE38E3",
            quotaName: "Maximum integration timeout in milliseconds",
            configured: typeof timeoutSeconds === "number" ? timeoutSeconds * 1000 : undefined,
            units: "ms",
            remedy:
                "request an increase to the Amazon API Gateway 'Maximum integration timeout' quota " +
                "(L-E5AE38E3) in this Region and wait for approval, or lower the configured value to " +
                "the Region's current limit",
        },
    ];

    console.log(`assertDeployQuotas: region ${region}`);
    const failures: string[] = [];
    let checked = 0;

    for (const check of checks) {
        if (check.configured === undefined) {
            console.log(`  SKIP ${check.configPath} — not set in config.json`);
            continue;
        }
        const limit = appliedQuota(region, check.serviceCode, check.quotaCode);
        if (limit === undefined) {
            // Not a pass. An unreadable quota is reported and exits 2, so a missing permission cannot
            // masquerade as a clean gate.
            console.log(
                `  UNKNOWN ${check.quotaName} (${check.quotaCode}) — could not read the quota. ` +
                    `Grant servicequotas:GetServiceQuota or check manually.`
            );
            process.exitCode = 2;
            continue;
        }
        checked++;
        const ok = check.configured <= limit;
        console.log(
            `  ${ok ? "OK  " : "FAIL"} ${check.configPath} = ${check.configured}${check.units}, ` +
                `quota ${check.quotaCode} = ${limit}${check.units}`
        );
        if (!ok) {
            failures.push(
                `${check.configPath} is ${check.configured}${check.units} but this Region's ` +
                    `"${check.quotaName}" quota is ${limit}${check.units}. ${check.remedy}.`
            );
        }
    }

    if (failures.length > 0) {
        console.log("\nassertDeployQuotas: FAILED — the deploy would be rejected:");
        for (const f of failures) console.log(`  - ${f}`);
        process.exit(1);
    }
    if (process.exitCode === 2) {
        console.log("\nassertDeployQuotas: could not verify every quota (see UNKNOWN above).");
        return;
    }
    console.log(`assertDeployQuotas: OK — ${checked} quota check(s) passed for ${region}.`);
}

main();
