/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier — the optional REST API custom domain (`app.api.apiGatewayRest.customDomain`).
 *
 * When enabled, the REST API gets a Regional `AWS::ApiGateway::DomainName` with base path `api`
 * mapped to the `api` stage, so `https://{domainHost}/api/{route}` has the shape of the execute-api
 * invoke URL and the clients' stored base URLs work unchanged. The domain uses the REST API's own TLS
 * policy and endpoint access mode outside the GovCloud mode, and the TLS 1.2 policy inside it.
 * amplify-config returns the domain's URL to the web app and to `vamscli setup`, the web CSP allows
 * the domain next to the execute-api host, and two root outputs give the operator the URL and the DNS
 * target. An optional hosted zone gets A and AAAA alias records. Every Lambda configured by
 * `globalLambdaEnvironmentsAndPermissions` (the API handlers and the REST authorizer) gets
 * `API_CUSTOM_DOMAIN_NAME` and `API_CUSTOM_DOMAIN_BASE_PATH`, from which the backend makes a
 * custom-domain request's path stage-relative.
 *
 * With the block disabled — every shipped template — none of this is emitted, and the amplify-config
 * function, CSP and Lambda environments are unchanged: that is the T1 assertion across all three
 * templates.
 *
 * Durable (root CLAUDE.md Rule 13): every resource and value here can be written differently again.
 */

import { Resource, SynthResult, synthTemplate, TemplateName } from "../support/templateSynth";

jest.setTimeout(600_000);

const HOST = "api.vams-t1.example.com";
const ZONE_ID = "Z0123456789ABCDEFGHIJ";
const ACCOUNT = "123456789012";
const certArn = (partition: string, region: string) =>
    `arn:${partition}:acm:${region}:${ACCOUNT}:certificate/11111111-2222-3333-4444-555555555555`;

const enabled =
    (partition: string, region: string, hostedZoneId = ZONE_ID, domainHost = HOST) =>
    (c: any) => {
        c.app.api.apiGatewayRest.customDomain = {
            enabled: true,
            domainHost,
            certificateArn: certArn(partition, region),
            optionalHostedZoneId: hostedZoneId,
        };
    };

const commercialOn = () =>
    synthTemplate("commercial", {
        mutate: enabled("aws", "us-east-1"),
        mutateKey: "api-custom-domain-commercial",
    });
const commercialOnNoZone = () =>
    synthTemplate("commercial", {
        mutate: enabled("aws", "us-east-1", ""),
        mutateKey: "api-custom-domain-commercial-nozone",
    });
const govcloudOn = () =>
    synthTemplate("govcloud", {
        mutate: enabled("aws-us-gov", "us-gov-west-1"),
        mutateKey: "api-custom-domain-govcloud",
    });

function single(s: SynthResult, type: string): Resource {
    const found = s.ofType(type);
    expect(found).toHaveLength(1);
    return found[0];
}

/** Root-stack outputs of the core stack. */
function rootOutputs(s: SynthResult): Record<string, any> {
    const root = Object.entries(s.templates).find(([key]) => key === `vams-t1-${s.name}`);
    expect(root).toBeDefined();
    return root![1].Outputs ?? {};
}

/** The amplify-config Lambda's inline source. */
function amplifyConfigSource(s: SynthResult): string {
    const fns = s
        .ofType("AWS::Lambda::Function")
        .filter((r) => /AmplifyConfigLambda/.test(r.logicalId));
    expect(fns).toHaveLength(1);
    return SynthResult.flatten(fns[0].properties.Code.ZipFile);
}

/** The connect-src sources of the CloudFront CSP. */
function cloudFrontConnectSrc(s: SynthResult): string[] {
    const policy = single(s, "AWS::CloudFront::ResponseHeadersPolicy");
    const csp = SynthResult.flatten(
        policy.properties.ResponseHeadersPolicyConfig.SecurityHeadersConfig.ContentSecurityPolicy
            .ContentSecurityPolicy
    );
    return (/connect-src ([^;]*);/.exec(csp) ?? ["", ""])[1].split(" ");
}

/** The ALB listener's CSP attribute, flattened. */
function albCsp(s: SynthResult): string {
    const listener = s
        .ofType("AWS::ElasticLoadBalancingV2::Listener")
        .find((l) => (l.properties.ListenerAttributes ?? []).length > 0);
    expect(listener).toBeDefined();
    const attr = (listener!.properties.ListenerAttributes as any[]).find(
        (a) => a.Key === "routing.http.response.content_security_policy.header_value"
    );
    return SynthResult.flatten(attr.Value);
}

/** Deployed byte length, with each unresolved token at a representative resolved width. */
const deployedBytes = (value: string): number =>
    Buffer.byteLength(value.replace(/\$\{[^}]*\}/g, "x".repeat(10)), "utf8");

const CUSTOM_DOMAIN_ENV = ["API_CUSTOM_DOMAIN_NAME", "API_CUSTOM_DOMAIN_BASE_PATH"];

/** Every Lambda function's environment variables, keyed by logical id. */
function lambdaEnvironments(s: SynthResult): Record<string, Record<string, unknown>> {
    const out: Record<string, Record<string, unknown>> = {};
    for (const fn of s.ofType("AWS::Lambda::Function")) {
        out[`${fn.stack}/${fn.logicalId}`] = fn.properties.Environment?.Variables ?? {};
    }
    return out;
}

/**
 * The functions configured by `globalLambdaEnvironmentsAndPermissions` — every API handler and the
 * REST authorizer among them. The helper always sets `VAMS_RESOURCE_PARAM_PREFIX`, so that variable
 * identifies them.
 */
function helperConfiguredEnvironments(s: SynthResult): Record<string, Record<string, unknown>> {
    return Object.fromEntries(
        Object.entries(lambdaEnvironments(s)).filter(
            ([, env]) => env.VAMS_RESOURCE_PARAM_PREFIX !== undefined
        )
    );
}

describe.each(["commercial", "govcloud", "eusovereign"] as TemplateName[])(
    "%s with the custom domain off (shipped template)",
    (name) => {
        const s = () => synthTemplate(name);

        test("the REST API and its stage are emitted (control)", () => {
            expect(s().countOfType("AWS::ApiGateway::RestApi")).toBe(1);
            expect(s().countOfType("AWS::ApiGateway::Stage")).toBe(1);
        });

        test("no domain name, base path mapping, alias record or custom-domain output", () => {
            expect(s().countOfType("AWS::ApiGateway::DomainName")).toBe(0);
            expect(s().countOfType("AWS::ApiGateway::BasePathMapping")).toBe(0);
            expect(s().countOfType("AWS::Route53::RecordSet")).toBe(0);
            const outputs = Object.keys(rootOutputs(s()));
            expect(outputs).toContain("APIGatewayEndpointOutput");
            expect(outputs).not.toContain("ApiCustomDomainUrl");
            expect(outputs).not.toContain("ApiCustomDomainTarget");
        });

        test("amplify-config derives the API URL from the request", () => {
            const source = amplifyConfigSource(s());
            expect(source).toContain('"api":""');
            expect(source).toContain('config.api = "https://" + host + stageSeg + "/";');
        });

        test("no Lambda function carries the custom-domain base-path environment", () => {
            const environments = lambdaEnvironments(s());
            expect(Object.keys(environments).length).toBeGreaterThan(0);
            for (const [fn, env] of Object.entries(environments)) {
                for (const key of CUSTOM_DOMAIN_ENV) {
                    expect({ fn, key, value: env[key] }).toEqual({ fn, key, value: undefined });
                }
            }
        });
    }
);

describe("commercial with the custom domain on", () => {
    test("a Regional domain name with the REST API's TLS policy and access mode", () => {
        const domain = single(commercialOn(), "AWS::ApiGateway::DomainName");
        expect(domain.properties.DomainName).toBe(HOST);
        expect(domain.properties.EndpointConfiguration.Types).toEqual(["REGIONAL"]);
        expect(domain.properties.RegionalCertificateArn).toBe(certArn("aws", "us-east-1"));
        expect(domain.properties.CertificateArn).toBeUndefined();
        expect(domain.properties.SecurityPolicy).toBe("SecurityPolicy_TLS13_1_2_2021_06");
        expect(domain.properties.EndpointAccessMode).toBe("BASIC");

        // Same policy as the REST API, so the two endpoints accept the same clients.
        const api = single(commercialOn(), "AWS::ApiGateway::RestApi");
        expect(api.properties.SecurityPolicy).toBe(domain.properties.SecurityPolicy);
    });

    test("base path api maps to the api stage", () => {
        const s = commercialOn();
        const mapping = single(s, "AWS::ApiGateway::BasePathMapping");
        const domain = single(s, "AWS::ApiGateway::DomainName");
        const stage = single(s, "AWS::ApiGateway::Stage");
        const api = single(s, "AWS::ApiGateway::RestApi");
        expect(mapping.properties.BasePath).toBe("api");
        expect(mapping.properties.DomainName).toEqual({ Ref: domain.logicalId });
        expect(mapping.properties.RestApiId).toEqual({ Ref: api.logicalId });
        expect(mapping.properties.Stage).toEqual({ Ref: stage.logicalId });
        expect(stage.properties.StageName).toBe("api");
    });

    test("A and AAAA alias records for the host in the hosted zone", () => {
        const s = commercialOn();
        const records = s.ofType("AWS::Route53::RecordSet");
        expect(records.map((r) => r.properties.Type).sort()).toEqual(["A", "AAAA"]);
        const domain = single(s, "AWS::ApiGateway::DomainName");
        for (const record of records) {
            expect(record.properties.Name).toBe(`${HOST}.`);
            expect(record.properties.HostedZoneId).toBe(ZONE_ID);
            expect(record.properties.AliasTarget.DNSName).toEqual({
                "Fn::GetAtt": [domain.logicalId, "RegionalDomainName"],
            });
        }
    });

    test("no alias records without a hosted zone id", () => {
        const s = commercialOnNoZone();
        expect(s.countOfType("AWS::ApiGateway::DomainName")).toBe(1);
        expect(s.countOfType("AWS::Route53::RecordSet")).toBe(0);
    });

    test("root outputs give the stage-inclusive URL and the DNS target", () => {
        const outputs = rootOutputs(commercialOn());
        expect(outputs.ApiCustomDomainUrl.Value).toBe(`https://${HOST}/api/`);
        expect(outputs.ApiCustomDomainTarget.Value).toBeDefined();
        // The execute-api invoke URL output is unchanged.
        expect(SynthResult.flatten(outputs.APIGatewayEndpointOutput.Value)).toMatch(
            /\.execute-api\..*\/api$/
        );
    });

    test("amplify-config returns the domain URL and does not derive it", () => {
        const source = amplifyConfigSource(commercialOn());
        expect(source).toContain(`"api":"https://${HOST}/api/"`);
        expect(source).not.toContain("rc.domainName");
    });

    test("the CloudFront CSP allows the domain and still the execute-api host", () => {
        const connectSrc = cloudFrontConnectSrc(commercialOn());
        expect(connectSrc).toContain(`https://${HOST}`);
        expect(connectSrc.some((src) => /\.execute-api\./.test(src))).toBe(true);
        expect(cloudFrontConnectSrc(synthTemplate("commercial"))).not.toContain(`https://${HOST}`);
    });

    test("the CloudFront behaviors are the same as with the domain off", () => {
        const patterns = (s: SynthResult) =>
            s
                .ofType("AWS::CloudFront::Distribution")[0]
                .properties.DistributionConfig.CacheBehaviors.map((b: any) => b.PathPattern);
        expect(patterns(commercialOn())).toEqual(patterns(synthTemplate("commercial")));
    });

    test("API Lambdas get the domain and base path so they see the stage-relative path", () => {
        // Through the domain a proxy event's `path` keeps the base path; common/auth/apiEvent.py
        // strips it for events routed through this host, read from these two variables.
        const s = commercialOn();
        const environments = helperConfiguredEnvironments(s);
        const names = Object.keys(environments);
        expect(names.some((n) => /apiGatewayAuthorizerRest/.test(n))).toBe(true);
        expect(names.some((n) => /databaseService/.test(n))).toBe(true);
        for (const [fn, env] of Object.entries(environments)) {
            expect({ fn, value: env.API_CUSTOM_DOMAIN_NAME }).toEqual({ fn, value: HOST });
            expect({ fn, value: env.API_CUSTOM_DOMAIN_BASE_PATH }).toEqual({ fn, value: "api" });
        }
        // The base path is the one the mapping routes to the stage.
        const mapping = single(s, "AWS::ApiGateway::BasePathMapping");
        expect(mapping.properties.BasePath).toBe("api");
    });
});

describe("govcloud with the custom domain on", () => {
    test("the domain uses the TLS 1.2 policy and no endpoint access mode", () => {
        const domain = single(govcloudOn(), "AWS::ApiGateway::DomainName");
        expect(domain.properties.DomainName).toBe(HOST);
        expect(domain.properties.EndpointConfiguration.Types).toEqual(["REGIONAL"]);
        expect(domain.properties.RegionalCertificateArn).toBe(
            certArn("aws-us-gov", "us-gov-west-1")
        );
        expect(domain.properties.SecurityPolicy).toBe("TLS_1_2");
        expect(domain.properties.EndpointAccessMode).toBeUndefined();
    });

    test("API Lambdas get the domain and base path", () => {
        const environments = helperConfiguredEnvironments(govcloudOn());
        expect(Object.keys(environments).some((n) => /apiGatewayAuthorizerRest/.test(n))).toBe(
            true
        );
        for (const [fn, env] of Object.entries(environments)) {
            expect({ fn, value: env.API_CUSTOM_DOMAIN_NAME }).toEqual({ fn, value: HOST });
            expect({ fn, value: env.API_CUSTOM_DOMAIN_BASE_PATH }).toEqual({ fn, value: "api" });
        }
    });

    test("the ALB CSP allows the domain and still fits the 1 KB listener attribute", () => {
        const csp = albCsp(govcloudOn());
        expect(csp).toContain(`https://${HOST}`);
        expect(deployedBytes(csp)).toBeLessThan(1024);
    });

    test("a 60-character host still fits the ALB CSP", () => {
        const longHost = `vams-api-${"a".repeat(32)}.example-agency.gov`;
        expect(longHost.length).toBe(60);
        const s = synthTemplate("govcloud", {
            mutate: enabled("aws-us-gov", "us-gov-west-1", "", longHost),
            mutateKey: "api-custom-domain-govcloud-long-host",
        });
        const csp = albCsp(s);
        expect(csp).toContain(`https://${longHost}`);
        const bytes = deployedBytes(csp);
        // eslint-disable-next-line no-console
        console.log(`[alb csp] govcloud with a 60-character API domain: ${bytes} bytes of 1024`);
        expect(bytes).toBeLessThan(1024);
    });
});
