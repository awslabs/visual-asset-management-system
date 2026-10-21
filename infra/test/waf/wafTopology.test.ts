/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Where the deployment's web ACLs attach, and the routing premises a scoped rule exclusion on the
 * regional web ACL rests on, in full synths of the fronting modes.
 *
 * An add-on's scoped rule exclusion lives on the regional web ACL alone, under stage-prefixed route
 * paths. That is correct only while those routes reach AWS WAF at the REST API stage and nowhere
 * else, so what is pinned here:
 *   * associations: the regional ACL on the stage in every mode and on the ALB when there is one,
 *     the CloudFront ACL on the distribution only, and each ARN nowhere else in any template,
 *     outputs, parameters and conditions included;
 *   * CloudFront forwards `/api/*` to the stage under its own `/api` prefix, so it reaches only the
 *     spec paths under `/api/`, which are the two anonymous routes;
 *   * the ALB redirects `/api*` and `/secure-config*` to the stage with a 302, ahead of every forward
 *     rule, so it proxies nothing to API Gateway;
 *   * the gateway responses, WAF_FILTERED included, carry the CORS headers, ride on the deployment and
 *     are in the API definition as well;
 *   * with WAF off, no template holds a web ACL, an association or a WAF ARN, and the gateway
 *     responses are the ones a deployment with WAF on has.
 *
 * The WAF ARNs are sentinels, so each one can be traced through the templates.
 */

import { ACCOUNT, Resource, SynthResult, synthTemplate } from "../support/templateSynth";

jest.setTimeout(600_000);

const sentinels = (partition: string, region: string) => ({
    regional: `arn:${partition}:wafv2:${region}:${ACCOUNT}:regional/webacl/vams-t1-sentinel-regional/00000000-0000-4000-8000-000000000001`,
    cloudfront: `arn:${partition}:wafv2:us-east-1:${ACCOUNT}:global/webacl/vams-t1-sentinel-cloudfront/00000000-0000-4000-8000-000000000002`,
});

const TARGET: Record<"commercial" | "govcloud", { partition: string; region: string }> = {
    commercial: { partition: "aws", region: "us-east-1" },
    govcloud: { partition: "aws-us-gov", region: "us-gov-west-1" },
};

interface Topology {
    title: string;
    template: "commercial" | "govcloud";
    mutate: (c: any) => void;
    mutateKey: string;
}

const CLOUDFRONT: Topology = {
    title: "commercial, CloudFront",
    template: "commercial",
    mutate: () => {
        // The shipped commercial template fronts the app with CloudFront.
    },
    mutateKey: "waf-topology-cloudfront",
};
const ALB: Topology = {
    title: "govcloud, ALB",
    template: "govcloud",
    mutate: () => {
        // The shipped govcloud template fronts the app with the ALB.
    },
    mutateKey: "waf-topology-alb",
};
const ALB_PRIVATE: Topology = {
    title: "govcloud, ALB, PRIVATE API",
    template: "govcloud",
    mutate: (c) => {
        c.app.api.apiGatewayRest.endpointType = "PRIVATE";
    },
    mutateKey: "waf-topology-alb-private",
};
const API_ONLY: Topology = {
    title: "commercial, API only",
    template: "commercial",
    mutate: (c) => {
        c.app.useCloudFront.enabled = false;
        c.app.useAlb.enabled = false;
    },
    mutateKey: "waf-topology-api-only",
};

const sentinelsOf = (t: Topology) =>
    sentinels(TARGET[t.template].partition, TARGET[t.template].region);

const synth = (t: Topology): SynthResult =>
    synthTemplate(t.template, {
        mutate: t.mutate,
        mutateKey: t.mutateKey,
        wafArns: sentinelsOf(t),
    });

const where = (r: { stack: string; logicalId: string }) => `${r.stack}/${r.logicalId}`;

/** Templates holding `needle` outside their resources, which `SynthResult.grep` does not search. */
const outsideResources = (s: SynthResult, needle: string): string[] =>
    Object.entries<any>(s.templates)
        .filter(([, template]) =>
            Object.entries<any>(template).some(
                ([section, body]) =>
                    section !== "Resources" && JSON.stringify(body).includes(needle)
            )
        )
        .map(([stack]) => stack);

const associations = (s: SynthResult) => s.ofType("AWS::WAFv2::WebACLAssociation");

const stageAssociations = (s: SynthResult) =>
    associations(s).filter((a) =>
        SynthResult.flatten(a.properties.ResourceArn).endsWith("/stages/api")
    );

/** Associations whose resource is a `Ref` to a load balancer of their own template. */
const loadBalancerAssociations = (s: SynthResult) => {
    const loadBalancers = s.ofType("AWS::ElasticLoadBalancingV2::LoadBalancer");
    return associations(s).filter((a) =>
        loadBalancers.some(
            (lb) => lb.stack === a.stack && a.properties.ResourceArn?.Ref === lb.logicalId
        )
    );
};

const restApi = (s: SynthResult): Resource => {
    const apis = s.ofType("AWS::ApiGateway::RestApi");
    expect(apis).toHaveLength(1);
    return apis[0];
};

const specPaths = (s: SynthResult): string[] => Object.keys(restApi(s).properties.Body.paths);

/**
 * Every gateway response resource of the REST API is in its definition too, with the same status
 * code, headers and body: an import of the definition resets each response it omits.
 */
const expectGatewayResponsesInDefinition = (s: SynthResult) => {
    const inDefinition = restApi(s).properties.Body["x-amazon-apigateway-gateway-responses"] ?? {};
    const responses = s.ofType("AWS::ApiGateway::GatewayResponse");
    expect(responses).toHaveLength(3);
    expect(Object.keys(inDefinition).sort()).toEqual(
        responses.map((r) => r.properties.ResponseType).sort()
    );
    for (const r of responses) {
        expect(inDefinition[r.properties.ResponseType]).toEqual({
            ...(r.properties.StatusCode ? { statusCode: r.properties.StatusCode } : {}),
            responseParameters: r.properties.ResponseParameters,
            ...(r.properties.ResponseTemplates
                ? { responseTemplates: r.properties.ResponseTemplates }
                : {}),
        });
    }
};

/** Values of every `path-pattern` condition of a listener rule. */
const pathPatterns = (rule: Resource): string[] =>
    ((rule.properties.Conditions ?? []) as any[])
        .filter((c) => c.Field === "path-pattern")
        .flatMap((c) => c.PathPatternConfig?.Values ?? c.Values ?? []);

const actionTypes = (rule: Resource): string[] =>
    ((rule.properties.Actions ?? []) as any[]).map((a) => a.Type);

describe.each([CLOUDFRONT, ALB, ALB_PRIVATE, API_ONLY])("WAF topology: $title", (t) => {
    let s: SynthResult;
    const { regional, cloudfront } = sentinelsOf(t);

    beforeAll(() => {
        s = synth(t);
    });

    it("associates the regional web ACL with the REST API stage, once", () => {
        const stage = stageAssociations(s);
        expect(stage).toHaveLength(1);
        expect(stage[0].properties.WebACLArn).toBe(regional);
    });

    it("carries the regional ACL ARN on the associations alone", () => {
        expect(s.grep(regional).map(where).sort()).toEqual(associations(s).map(where).sort());
        expect(outsideResources(s, regional)).toEqual([]);
    });

    it("carries the CloudFront ACL ARN on the distribution alone, and only with CloudFront", () => {
        const expected = t === CLOUDFRONT ? s.ofType("AWS::CloudFront::Distribution") : [];
        expect(s.grep(cloudfront).map(where)).toEqual(expected.map(where));
        expect(outsideResources(s, cloudfront)).toEqual([]);
    });

    it("keeps the logical ids of the DEFAULT_4XX and DEFAULT_5XX responses", () => {
        // A new logical id makes CloudFormation create the new response and then delete the old
        // one, which resets that response type on the API to its default, CORS headers included.
        const ids = Object.fromEntries(
            s
                .ofType("AWS::ApiGateway::GatewayResponse")
                .map((r) => [r.properties.ResponseType, r.logicalId])
        );
        expect(ids.DEFAULT_4XX).toBe("RestApiGatewayGatewayResponseDefault4XX2DDDF540");
        expect(ids.DEFAULT_5XX).toBe("RestApiGatewayGatewayResponseDefault5XXE69C23CE");
    });

    it("builds the gateway responses on the REST API and ties them to its deployment", () => {
        const api = restApi(s);
        const responses = s.ofType("AWS::ApiGateway::GatewayResponse");
        expect(responses.map((r) => r.properties.ResponseType).sort()).toEqual([
            "DEFAULT_4XX",
            "DEFAULT_5XX",
            "WAF_FILTERED",
        ]);
        for (const r of responses) {
            expect(r.stack).toBe(api.stack);
            expect(r.properties.RestApiId).toEqual({ Ref: api.logicalId });
        }
        const waf = responses.find((r) => r.properties.ResponseType === "WAF_FILTERED");
        expect(waf?.properties.StatusCode).toBe("403");
        const body = JSON.parse(waf?.properties.ResponseTemplates?.["application/json"] ?? "{}");
        expect(body.blockedBy).toBe("waf");
        expect(body.requestId).toBe("$context.extendedRequestId");

        const deployments = s.ofType("AWS::ApiGateway::Deployment");
        expect(deployments).toHaveLength(1);
        expect(deployments[0].raw.DependsOn ?? []).toEqual(
            expect.arrayContaining(responses.map((r) => r.logicalId))
        );
    });

    it("carries the gateway responses in the REST API definition, as the resources have them", () => {
        expectGatewayResponsesInDefinition(s);
    });

    it("puts the CORS headers on every gateway response, allowing the preflight's headers", () => {
        // A browser reads a cross-origin response only when it carries Access-Control-Allow-Origin,
        // so without it the web app never sees the WAF_FILTERED body.
        const allowHeaders = Array.from(
            new Set(
                Object.values<any>(restApi(s).properties.Body.paths).map(
                    (operations) =>
                        operations.options?.["x-amazon-apigateway-integration"]?.responses?.default
                            ?.responseParameters?.[
                            "method.response.header.Access-Control-Allow-Headers"
                        ]
                )
            )
        );
        expect(allowHeaders).toHaveLength(1);
        expect(allowHeaders[0]).toContain("Authorization");
        const responses = s.ofType("AWS::ApiGateway::GatewayResponse");
        expect(responses).toHaveLength(3);
        for (const r of responses) {
            expect(r.properties.ResponseParameters).toEqual({
                "gatewayresponse.header.Access-Control-Allow-Origin": "'*'",
                "gatewayresponse.header.Access-Control-Allow-Headers": allowHeaders[0],
            });
        }
    });
});

describe("WAF topology: commercial, CloudFront", () => {
    let s: SynthResult;
    const { regional, cloudfront } = sentinelsOf(CLOUDFRONT);

    beforeAll(() => {
        s = synth(CLOUDFRONT);
    });

    it("attaches the CloudFront web ACL to the one distribution", () => {
        const distributions = s.ofType("AWS::CloudFront::Distribution");
        expect(distributions).toHaveLength(1);
        expect(distributions[0].properties.DistributionConfig.WebACLId).toBe(cloudfront);
    });

    it("has the stage association alone, and none on a load balancer", () => {
        expect(loadBalancerAssociations(s)).toEqual([]);
        expect(s.ofType("AWS::ElasticLoadBalancingV2::LoadBalancer")).toEqual([]);
        expect(associations(s).map(where)).toEqual(stageAssociations(s).map(where));
        expect(associations(s)).toHaveLength(1);
        expect(associations(s)[0].properties.WebACLArn).toBe(regional);
    });

    it("forwards /api/* to the stage under its /api prefix, and nothing else to that origin", () => {
        const config = s.ofType("AWS::CloudFront::Distribution")[0].properties.DistributionConfig;
        const behaviors: any[] = config.CacheBehaviors ?? [];
        const api = behaviors.filter((b) => b.PathPattern === "/api/*");
        expect(api).toHaveLength(1);
        const origin = (config.Origins as any[]).find((o) => o.Id === api[0].TargetOriginId);
        expect(origin).toBeDefined();
        expect(origin.OriginPath).toBe("/api");
        expect(origin.CustomOriginConfig).toBeDefined();
        const others = [config.DefaultCacheBehavior, ...behaviors.filter((b) => b !== api[0])];
        expect(others.filter((b) => b.TargetOriginId === origin.Id)).toEqual([]);
    });

    it("has only the two anonymous routes under /api/ in the spec, and no greedy path there", () => {
        const paths = specPaths(s);
        // Control: the full spec is present (the commercial template emits 100 paths).
        expect(paths.length).toBeGreaterThan(90);
        expect(paths.filter((p) => p.startsWith("/api/")).sort()).toEqual([
            "/api/amplify-config",
            "/api/version",
        ]);
        expect(paths.filter((p) => p.startsWith("/{"))).toEqual([]);
        expect(paths.filter((p) => p.startsWith("/api/") && p.includes("+}"))).toEqual([]);
    });
});

describe.each([ALB, ALB_PRIVATE])("WAF topology: $title", (t) => {
    let s: SynthResult;
    const { regional } = sentinelsOf(t);

    beforeAll(() => {
        s = synth(t);
    });

    it("builds the REST API with the endpoint type under test", () => {
        expect(restApi(s).properties.EndpointConfiguration.Types).toEqual([
            t === ALB_PRIVATE ? "PRIVATE" : "REGIONAL",
        ]);
    });

    it("associates the regional web ACL with the stage and the load balancer, and nothing else", () => {
        const loadBalancers = s.ofType("AWS::ElasticLoadBalancingV2::LoadBalancer");
        expect(loadBalancers).toHaveLength(1);
        const onLoadBalancer = loadBalancerAssociations(s);
        expect(onLoadBalancer).toHaveLength(1);
        expect(onLoadBalancer[0].properties.ResourceArn).toEqual({
            Ref: loadBalancers[0].logicalId,
        });
        expect(onLoadBalancer[0].properties.WebACLArn).toBe(regional);
        expect(associations(s)).toHaveLength(2);
        expect(associations(s).map(where).sort()).toEqual(
            [...stageAssociations(s), ...onLoadBalancer].map(where).sort()
        );
    });

    it("has no CloudFront distribution", () => {
        expect(s.ofType("AWS::CloudFront::Distribution")).toEqual([]);
    });
});

describe("WAF topology: govcloud, ALB listener rules", () => {
    let s: SynthResult;

    beforeAll(() => {
        s = synth(ALB);
    });

    it("redirects /api* and /secure-config* to the stage with a 302, ahead of every forward rule", () => {
        const rules = s.ofType("AWS::ElasticLoadBalancingV2::ListenerRule");
        const redirects = rules.filter((r) =>
            pathPatterns(r).some((v) => v === "/api*" || v === "/secure-config*")
        );
        expect(redirects.flatMap(pathPatterns).sort()).toEqual(["/api*", "/secure-config*"]);

        for (const rule of redirects) {
            const actions: any[] = rule.properties.Actions;
            expect(actions).toHaveLength(1);
            expect(actions[0].Type).toBe("redirect");
            expect(actions[0].RedirectConfig.StatusCode).toBe("HTTP_302");
            expect(actions[0].RedirectConfig.Path).toBe("/api/#{path}");
        }

        // Rules are evaluated by priority on their listener, so the comparison is on that listener.
        const listener = JSON.stringify(redirects[0].properties.ListenerArn);
        expect(redirects.every((r) => JSON.stringify(r.properties.ListenerArn) === listener)).toBe(
            true
        );
        const forwards = rules.filter(
            (r) =>
                JSON.stringify(r.properties.ListenerArn) === listener &&
                actionTypes(r).includes("forward")
        );
        expect(forwards.length).toBeGreaterThan(0);
        const firstForward = Math.min(...forwards.map((r) => Number(r.properties.Priority)));
        for (const rule of redirects) {
            expect(Number(rule.properties.Priority)).toBeLessThan(firstForward);
        }
    });
});

describe("WAF topology: commercial, API only", () => {
    let s: SynthResult;

    beforeAll(() => {
        s = synth(API_ONLY);
    });

    it("has the stage association alone, with no distribution and no load balancer", () => {
        expect(s.ofType("AWS::CloudFront::Distribution")).toEqual([]);
        expect(s.ofType("AWS::ElasticLoadBalancingV2::LoadBalancer")).toEqual([]);
        expect(associations(s)).toHaveLength(1);
        expect(associations(s).map(where)).toEqual(stageAssociations(s).map(where));
    });
});

const WAF_OFF_CLOUDFRONT: Topology = {
    title: "commercial, CloudFront, WAF off",
    template: "commercial",
    mutate: (c) => {
        c.app.useWaf = false;
    },
    mutateKey: "waf-topology-cloudfront-waf-off",
};
const WAF_OFF_ALB: Topology = {
    title: "govcloud, ALB, WAF off",
    template: "govcloud",
    mutate: (c) => {
        c.app.useWaf = false;
    },
    mutateKey: "waf-topology-alb-waf-off",
};

/** The gateway responses by response type, without the stack they are in. */
const gatewayResponsesByType = (s: SynthResult) =>
    Object.fromEntries(
        s
            .ofType("AWS::ApiGateway::GatewayResponse")
            .map((r) => [r.properties.ResponseType, { logicalId: r.logicalId, ...r.properties }])
    );

describe.each([
    { off: WAF_OFF_CLOUDFRONT, on: CLOUDFRONT },
    { off: WAF_OFF_ALB, on: ALB },
])("WAF topology: $off.title", ({ off, on }) => {
    let s: SynthResult;
    let withWaf: SynthResult;

    beforeAll(() => {
        // The core stack gets no ACL ARN with WAF off, as in the no-WAF branch of bin/infra.ts.
        s = synthTemplate(off.template, { mutate: off.mutate, mutateKey: off.mutateKey });
        withWaf = synth(on);
    });

    it("synthesizes the same fronting as with WAF on", () => {
        expect(s.ofType("AWS::ApiGateway::RestApi")).toHaveLength(1);
        for (const type of [
            "AWS::CloudFront::Distribution",
            "AWS::ElasticLoadBalancingV2::LoadBalancer",
        ]) {
            expect(s.ofType(type)).toHaveLength(withWaf.ofType(type).length);
        }
    });

    it("has no web ACL, no association and no WAF ARN in any template", () => {
        expect(associations(withWaf).length).toBeGreaterThan(0);
        expect(s.ofType("AWS::WAFv2::WebACL")).toEqual([]);
        expect(associations(s)).toEqual([]);
        expect(s.grep(":wafv2:").map(where)).toEqual([]);
        expect(outsideResources(s, ":wafv2:")).toEqual([]);
    });

    it("attaches no web ACL to the distribution", () => {
        for (const d of s.ofType("AWS::CloudFront::Distribution")) {
            expect(d.properties.DistributionConfig.WebACLId ?? "").toBe("");
        }
    });

    it("carries the gateway responses in the REST API definition, as the resources have them", () => {
        expectGatewayResponsesInDefinition(s);
    });

    it("keeps every gateway response, with the same logical ids, headers and bodies", () => {
        const responses = gatewayResponsesByType(s);
        expect(Object.keys(responses).sort()).toEqual([
            "DEFAULT_4XX",
            "DEFAULT_5XX",
            "WAF_FILTERED",
        ]);
        expect(responses).toEqual(gatewayResponsesByType(withWaf));
    });
});
