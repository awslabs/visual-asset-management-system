/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** The web Content-Security-Policy sources the Miris add-on contributes. */

import * as fs from "fs";
import * as path from "path";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import { generateContentSecurityPolicy } from "../../lib/helper/security";

const SECRET_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:test-miris-api-key-AbCdEf";

function enableMiris(c: any, mirisApiBaseUrl = "https://app.miris.com") {
    c.app.webUi.allowUnsafeEvalFeatures = true;
    const miris = c.app.addons.useMirisStreaming;
    miris.enabled = true;
    miris.viewerKeyMode = "static";
    miris.viewerKey = "test-placeholder-viewer-key-000000";
    miris.apiKeySecretArn = SECRET_ARN;
    miris.mirisApiBaseUrl = mirisApiBaseUrl;
}

function commercialConfig(mutate?: (c: any) => void): Config.Config {
    const config = JSON.parse(
        fs.readFileSync(
            path.join(__dirname, "..", "..", "config", "config.template.commercial.json"),
            "utf8"
        )
    );
    config.env.account = "123456789012";
    config.env.region = "us-east-1";
    config.env.partition = "aws";
    mutate?.(config);
    return config as Config.Config;
}

function policy(config: Config.Config): string {
    Service.SetConfig(config);
    return generateContentSecurityPolicy(
        {} as any,
        "https://auth.example.com",
        "api.example.com",
        config
    );
}

function directive(csp: string, name: string): string[] {
    const found = csp
        .split(";")
        .map((part) => part.trim())
        .find((part) => part.startsWith(`${name} `));
    if (!found) throw new Error(`no ${name} directive in: ${csp}`);
    return found.split(/\s+/).slice(1);
}

test("the add-on adds the Miris hosts to connect-src and blob: to script-src only", () => {
    const off = policy(commercialConfig((c) => (c.app.webUi.allowUnsafeEvalFeatures = true)));
    // The Miris API base URL is called only by the add-on's Lambdas, so it adds no browser source.
    const on = policy(
        commercialConfig((c) => enableMiris(c, "https://miris.staging.example.com/api/"))
    );
    const added = (name: string) =>
        directive(on, name).filter((source) => !directive(off, name).includes(source));
    expect(added("connect-src")).toEqual(["https://*.miris.com", "wss://*.miris.com"]);
    expect(added("script-src")).toEqual(["blob:"]);
    for (const name of ["worker-src", "img-src", "media-src", "frame-src", "font-src"]) {
        expect(directive(on, name)).toEqual(directive(off, name));
    }
    // Control: the script-src the add-on extends is the real one.
    expect(directive(on, "script-src")).toContain("'unsafe-eval'");
});

test("on the ALB path the policy fits the 1 KB listener attribute", () => {
    const config = commercialConfig((c) => {
        enableMiris(c);
        c.app.useCloudFront.enabled = false;
        c.app.useAlb.enabled = true;
    });
    Service.SetConfig(config);
    const csp = generateContentSecurityPolicy(
        {} as any,
        "",
        "abcdefghij.execute-api.us-east-1.amazonaws.com",
        config
    );
    expect(directive(csp, "script-src")).toContain("blob:");
    expect(Buffer.byteLength(csp, "utf8")).toBeLessThanOrEqual(1024);
});
