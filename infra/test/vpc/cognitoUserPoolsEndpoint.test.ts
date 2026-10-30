/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Amazon Cognito user pools (`cognito-idp`) interface endpoint and the authorizer MFA check that
 * depends on it.
 *
 * With `useGlobalVpc.useForAllLambdas` the API Gateway authorizer runs in the VPC's isolated subnets and
 * verifies every Amazon Cognito ID token against the user pool's JSON Web Key Set on
 * `cognito-idp.<region>.amazonaws.com`. In a VPC that VAMS creates those subnets have no route out of the
 * VPC, so without this endpoint the key download times out and every signed-in API request fails.
 *
 * The service is offered in only some Availability Zones of a Region (three of the six in us-east-1, and
 * which three differs per account), and an endpoint requested in a zone the service does not offer fails
 * the deployment. The endpoint is therefore placed through the endpoint-service Availability Zone lookup;
 * the zone tests below feed that lookup through CDK context the way `cdk synth` caches it.
 *
 * Every "not created" case is paired with a count of the other endpoints in the same VPC, so a builder
 * that emitted no endpoints at all cannot satisfy it.
 */

import * as cdk from "aws-cdk-lib";
import * as cxschema from "aws-cdk-lib/cloud-assembly-schema";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import { isCognitoMfaCheckEnabled } from "../../lib/helper/security";
import { VPCBuilderNestedStack } from "../../lib/nestedStacks/vpc/vpcBuilder-nestedStack";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const ACCOUNT = "123456789012";

/**
 * The configuration the defect was found on: Amazon Cognito sign-in, every Lambda in a VPC that VAMS
 * creates, VAMS-created endpoints, a customer managed key, no search and no pipelines.
 */
const allLambdasInVpcConfig = (region = "us-east-1", partition = "aws"): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = ACCOUNT;
    config.env.region = region;
    config.env.partition = partition;
    config.env.coreStackName = `vams-test-${region}`;
    config.env.loadContextIgnoreVPCStacks = false;
    config.app.baseStackName = "vams-test";
    config.app.authProvider.useCognito.enabled = true;
    config.app.authProvider.useCognito.useSaml = false;
    config.app.authProvider.useCognito.useOidc = false;
    config.app.authProvider.useExternalOAuthIdp.enabled = false;
    config.app.useGlobalVpc.enabled = true;
    config.app.useGlobalVpc.useForAllLambdas = true;
    config.app.useGlobalVpc.addVpcEndpoints = true;
    config.app.useKmsCmkEncryption.enabled = true;
    config.app.openSearch.useServerless.enabled = false;
    config.app.openSearch.useProvisioned.enabled = false;
    config.app.useLocationService.enabled = false;
    config.app.useWaf = false;
    config.app.addStackCloudTrailLogs = false;
    config.enableCdkNag = false;
    config.dockerDefaultPlatform = "";
    config.resourceNamesSSMParamPrefix = `/vams-test-${region}/resourceNames`;
    return config;
};

interface Endpoint {
    serviceName: string;
    privateDns: boolean;
    subnetIds: unknown[];
}

/** Synthesizes the VPC builder alone and returns its interface endpoints, plus the app's assembly. */
const synthVpcBuilder = (config: Config.Config, context: Record<string, unknown> = {}) => {
    Service.SetConfig(config);
    const app = newTestApp({ context });
    const parent = new cdk.Stack(app, "ParentStack", {
        env: { account: config.env.account, region: config.env.region },
    });
    const vpcStack = new VPCBuilderNestedStack(parent, "VPCBuilder", { config });
    const template = Template.fromStack(vpcStack);
    const endpoints: Endpoint[] = Object.values(
        template.findResources("AWS::EC2::VPCEndpoint", {
            Properties: { VpcEndpointType: "Interface" },
        })
    ).map((resource: any) => ({
        serviceName: JSON.stringify(resource.Properties.ServiceName),
        privateDns: resource.Properties.PrivateDnsEnabled === true,
        subnetIds: resource.Properties.SubnetIds ?? [],
    }));
    return { app, vpcStack, template, endpoints };
};

// Service names are JSON-stringified, so the closing quote pins the exact suffix: `.cognito-idp"`
// does not match `cognito-idp-fips` or `cognito-identity`.
const cognitoIdp = (endpoints: Endpoint[]): Endpoint[] =>
    endpoints.filter((e) => e.serviceName.includes('.cognito-idp"'));

describe("Amazon Cognito user pools interface endpoint", () => {
    test("is created with private DNS when the authorizer runs in a VPC that VAMS creates", () => {
        const { endpoints } = synthVpcBuilder(allLambdasInVpcConfig());
        const created = cognitoIdp(endpoints);
        expect(created).toHaveLength(1);
        expect(created[0].serviceName).toContain("com.amazonaws.us-east-1.cognito-idp");
        // Private DNS is what points the authorizer's cognito-idp.<region>.amazonaws.com requests
        // at the endpoint; without it the endpoint exists and the requests still leave the VPC.
        expect(created[0].privateDns).toBe(true);
    });

    test("no other Amazon Cognito endpoint is created", () => {
        // No in-VPC function calls Amazon Cognito identity pools or the FIPS host.
        const { endpoints } = synthVpcBuilder(allLambdasInVpcConfig());
        expect(endpoints.filter((e) => /cognito/i.test(e.serviceName))).toHaveLength(1);
    });

    test.each<[string, (c: Config.Config) => void]>([
        [
            "Lambda functions run outside the VPC",
            (c) => {
                c.app.useGlobalVpc.useForAllLambdas = false;
            },
        ],
        [
            "Amazon Cognito is not the authentication provider",
            (c) => {
                c.app.authProvider.useCognito.enabled = false;
                c.app.authProvider.useExternalOAuthIdp.enabled = true;
            },
        ],
        [
            "the user pool federates through SAML (it has a domain)",
            (c) => {
                c.app.authProvider.useCognito.useSaml = true;
            },
        ],
        [
            "the user pool federates through OIDC (it has a domain)",
            (c) => {
                c.app.authProvider.useCognito.useOidc = true;
            },
        ],
        [
            "VAMS does not create the VPC endpoints",
            (c) => {
                c.app.useGlobalVpc.addVpcEndpoints = false;
            },
        ],
    ])("is not created when %s", (_name, mutate) => {
        const config = allLambdasInVpcConfig();
        mutate(config);
        const { endpoints, template } = synthVpcBuilder(config);
        expect(cognitoIdp(endpoints)).toHaveLength(0);
        // Control: the VPC itself is still built, so the absence is a decision, not an empty synth.
        template.resourceCountIs("AWS::EC2::VPC", 1);
        if (config.app.useGlobalVpc.addVpcEndpoints) {
            expect(endpoints.length).toBeGreaterThan(5);
        }
    });

    test.each([
        ["us-gov-west-1", "aws-us-gov"],
        ["eusc-de-east-1", "aws-eusc"],
    ])(
        "is not created in %s (%s), which has no Amazon Cognito PrivateLink",
        (region, partition) => {
            const { endpoints } = synthVpcBuilder(allLambdasInVpcConfig(region, partition));
            expect(cognitoIdp(endpoints)).toHaveLength(0);
            expect(endpoints.length).toBeGreaterThan(5);
        }
    );

    test("is created in the AWS China partition, which offers Amazon Cognito PrivateLink", () => {
        const { endpoints } = synthVpcBuilder(allLambdasInVpcConfig("cn-north-1", "aws-cn"));
        const created = cognitoIdp(endpoints);
        expect(created).toHaveLength(1);
        // The service-name prefix for the China Regions is CDK's own mapping.
        expect(created[0].serviceName).toContain(".cn-north-1.cognito-idp");
    });
});

describe("Amazon Cognito user pools endpoint Availability Zone placement", () => {
    const serviceName = "com.amazonaws.us-east-1.cognito-idp";
    // The VPC builder takes the first two zones of the Region; in us-east-1 those are us-east-1a and
    // us-east-1b, and the service is offered in only one of them in the account measured.
    const regionZones = [
        "us-east-1a",
        "us-east-1b",
        "us-east-1c",
        "us-east-1d",
        "us-east-1e",
        "us-east-1f",
    ];
    const serviceZones = ["us-east-1b", "us-east-1c", "us-east-1d"];

    const lookupKey = (): string => {
        // The key the CDK CLI caches the lookup under, read from the synthesized assembly's list of
        // missing context so the test follows the CLI rather than restating its key format.
        const { app } = synthVpcBuilder(allLambdasInVpcConfig(), {
            [`availability-zones:account=${ACCOUNT}:region=us-east-1`]: regionZones,
        });
        const missing = app.synth().manifest.missing ?? [];
        const entry = missing.find(
            (m) =>
                m.provider ===
                    cxschema.ContextProvider.ENDPOINT_SERVICE_AVAILABILITY_ZONE_PROVIDER &&
                (m.props as any).serviceName === serviceName
        );
        expect(entry).toBeDefined();
        return entry?.key ?? "";
    };

    test("the synth asks the CDK CLI to look up the zones the service is offered in", () => {
        expect(lookupKey()).toContain(serviceName);
    });

    test("the endpoint is placed only in the subnets whose zone the service offers", () => {
        const context = {
            [`availability-zones:account=${ACCOUNT}:region=us-east-1`]: regionZones,
            [lookupKey()]: serviceZones,
        };
        const { endpoints, template } = synthVpcBuilder(allLambdasInVpcConfig(), context);
        const created = cognitoIdp(endpoints);
        expect(created).toHaveLength(1);

        const subnets = template.findResources("AWS::EC2::Subnet", {
            Properties: { AvailabilityZone: "us-east-1b" },
        });
        const supportedSubnet = Object.keys(subnets);
        expect(supportedSubnet).toHaveLength(1);
        expect(created[0].subnetIds).toEqual([{ Ref: supportedSubnet[0] }]);

        // Control: an endpoint without the lookup still takes both isolated subnets, so the single
        // subnet above comes from the lookup and not from a one-zone VPC.
        const ssm = endpoints.filter((e) => e.serviceName.includes('.ssm"'));
        expect(ssm).toHaveLength(1);
        expect(ssm[0].subnetIds).toHaveLength(2);
    });

    test("a VPC whose zones the service does not offer fails at synth, not at deployment", () => {
        const context = {
            [`availability-zones:account=${ACCOUNT}:region=us-east-1`]: regionZones,
            [lookupKey()]: ["us-east-1c", "us-east-1d"],
        };
        expect(() => synthVpcBuilder(allLambdasInVpcConfig(), context)).toThrow(
            /lookupSupportedAzs returned/
        );
    });
});

describe("authorizer MFA check follows the authorizer's path to Amazon Cognito", () => {
    test("is enabled when the authorizer runs outside the VPC", () => {
        const config = allLambdasInVpcConfig();
        config.app.useGlobalVpc.useForAllLambdas = false;
        expect(isCognitoMfaCheckEnabled(config)).toBe(true);
    });

    test("is enabled when the authorizer runs in the VPC and VAMS creates the endpoint", () => {
        const config = allLambdasInVpcConfig();
        expect(Config.createsCognitoUserPoolsVpcEndpoint(config)).toBe(true);
        expect(isCognitoMfaCheckEnabled(config)).toBe(true);
    });

    test.each<[string, (c: Config.Config) => void]>([
        [
            "the VPC endpoints are operator-managed",
            (c) => {
                c.app.useGlobalVpc.addVpcEndpoints = false;
            },
        ],
        [
            "the partition has no Amazon Cognito PrivateLink",
            (c) => {
                c.env.region = "us-gov-west-1";
                c.env.partition = "aws-us-gov";
            },
        ],
        [
            "the user pool has a SAML domain",
            (c) => {
                c.app.authProvider.useCognito.useSaml = true;
            },
        ],
    ])("is disabled in the VPC when %s", (_name, mutate) => {
        const config = allLambdasInVpcConfig();
        mutate(config);
        expect(Config.createsCognitoUserPoolsVpcEndpoint(config)).toBe(false);
        expect(isCognitoMfaCheckEnabled(config)).toBe(false);
    });

    test("is disabled when Amazon Cognito is not the authentication provider", () => {
        const config = allLambdasInVpcConfig();
        config.app.authProvider.useCognito.enabled = false;
        config.app.useGlobalVpc.useForAllLambdas = false;
        expect(isCognitoMfaCheckEnabled(config)).toBe(false);
    });
});

describe("cognitoPrivateLinkAvailable", () => {
    test.each([
        ["aws", true],
        ["aws-cn", true],
        ["aws-us-gov", false],
        ["aws-eusc", false],
        ["aws-iso", false],
        ["aws-iso-b", false],
        ["aws-iso-f", false],
    ])("%s -> %s", (partition, expected) => {
        expect(Config.cognitoPrivateLinkAvailable(partition)).toBe(expected);
    });
});
