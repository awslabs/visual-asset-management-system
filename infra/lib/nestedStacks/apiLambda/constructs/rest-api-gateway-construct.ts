/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as apigw from "aws-cdk-lib/aws-apigateway";
import * as iam from "aws-cdk-lib/aws-iam";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as logs from "aws-cdk-lib/aws-logs";
import * as wafv2 from "aws-cdk-lib/aws-wafv2";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../config/config";
import { Service, Partition } from "../../../helper/service-helper";
import { storageResources } from "../../storage/storageBuilder-nestedStack";
import { authResources } from "../../auth/authBuilder-nestedStack";
import { RouteRegistry } from "../apiRouteRegistry";
import { buildOpenApiSpec } from "./buildOpenApiSpec";
import { buildApiGatewayAuthorizerRestFunction } from "../../../lambdaBuilder/authFunctions";
import { generateUniqueNameHash } from "../../../helper/security";
import { AmplifyConfigLambdaConstruct } from "./amplify-config-lambda-construct";
import { VamsVersionLambdaConstruct } from "./vams-version-lambda-construct";
import { samlSettings } from "../../../../config/saml-config";
import { HttpMethod } from "aws-cdk-lib/aws-apigatewayv2";

/**
 * Common surface every API implementation exposes so the API nested stack can treat
 * implementations uniformly and downstream stacks (static web fronting, outputs) stay
 * implementation-agnostic.
 *
 */
export interface IApiImplementation {
    /** Host of the API endpoint (no scheme), e.g. `{id}.execute-api.{region}.amazonaws.com`. */
    readonly apiEndpoint: string;
    /** Full invoke URL including the stage path, e.g. `https://{host}/{stage}`. */
    readonly invokeUrlWithStage: string;
    /** The deployment stage name fronting absorbs (CloudFront originPath / ALB redirect). */
    readonly stageName: string;
}

export interface RestApiGatewayConstructProps {
    config: Config.Config;
    authResources: authResources;
    storageResources: storageResources;
    lambdaAuthorizerLayer: LayerVersion;
    registry: RouteRegistry;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    /** Execute-api interface VPC endpoint id created by VAMS (undefined when not created). */
    vamsCreatedApiGatewayVpcEndpointId?: string;
    /** Resolved WAF web ACL ARN ("" when WAF is disabled). */
    wafArn?: string;
}

/**
 * API Gateway REST implementation of the VAMS backend API.
 *
 * Builds a single `SpecRestApi` from the cross-stack {@link RouteRegistry}: renders one
 * inline OpenAPI document (with the custom REQUEST Lambda authorizer, CORS, and the
 * anonymous amplify-config/version routes), creates an explicit Deployment + Stage whose
 * logical id is hashed against the whole spec (so any cross-stack route change
 * auto-redeploys), grants API Gateway invoke on every registered Lambda, and optionally
 * associates a regional WAF web ACL.
 *
 * The API is fully self-contained: it is publicly addressable on its own and does not
 * require CloudFront or an ALB in front of it. When a front is present it absorbs the
 * stage path, but direct execute-api access works the same.
 *
 */
export class RestApiGatewayConstruct extends Construct implements IApiImplementation {
    public restApi: apigw.SpecRestApi;
    public readonly apiEndpoint: string;
    public readonly stageName: string;
    public readonly invokeUrlWithStage: string;

    constructor(parent: Construct, name: string, props: RestApiGatewayConstructProps) {
        super(parent, name);
        const { config, storageResources, registry } = props;
        const apiGatewayRest = config.app.api.apiGatewayRest;
        // The deployment stage name is a fixed constant (shared with the VamsCLI endpoint
        // constants and the web /api/* fronting), not a per-deployment config option.
        this.stageName = Config.API_GATEWAY_STAGE_NAME;

        // Implementation-specific decision 1: which execute-api VPC endpoint id (if any)
        // this REST API should use. PRIVATE uses VAMS' endpoint when addVpcEndpoints created
        // one, otherwise the operator-supplied external endpoint id (config validation
        // requires one of the two). REGIONAL is a public endpoint and never routes through a
        // VPC endpoint, so this resolves to undefined for REGIONAL.
        const apiGatewayVpcEndpointId = resolveApiGatewayVpcEndpointId(
            config,
            props.vamsCreatedApiGatewayVpcEndpointId
        );

        // Implementation-specific decision 2: attach the regional-scoped WAF web ACL to the
        // REST API stage whenever WAF is enabled. The ACL passed in (props.wafArn) is always
        // the regional-scoped ACL created in the core Region, which is what API Gateway
        // requires — for both REGIONAL and PRIVATE endpoint types, and independently of
        // whether CloudFront or an ALB fronts the app. (The CloudFront-scoped ACL is a
        // separate us-east-1 ACL attached to the distribution, not here.) This protects the
        // API's direct execute-api endpoint, which stays reachable regardless of fronting.
        const wafEnabled = config.app.useWaf;

        // 1) REST authorizer Lambda (custom; reuses shared auth core)
        const authorizerFn = buildApiGatewayAuthorizerRestFunction(
            this,
            props.lambdaAuthorizerLayer,
            storageResources,
            config,
            props.vpc,
            props.subnets
        );
        if (config.app.authProvider.useCognito.enabled) {
            authorizerFn.addEnvironment("USER_POOL_ID", props.authResources.cognito.userPoolId);
            authorizerFn.addEnvironment("APP_CLIENT_ID", props.authResources.cognito.webClientId);
            // The authorizer resolves the user's MFA preference (AdminGetUser) and passes it
            // to handler Lambdas through the authorizer context
            authorizerFn.addToRolePolicy(
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["cognito-idp:AdminGetUser"],
                    resources: [props.authResources.cognito.userPool.userPoolArn],
                })
            );
        }

        // 2) Role API Gateway assumes to invoke the authorizer
        const authInvokeRole = new iam.Role(this, "RestAuthorizerInvokeRole", {
            assumedBy: Service("APIGATEWAY").Principal,
        });
        authorizerFn.grantInvoke(authInvokeRole);

        // Cognito hosted UI domain for federated (SAML) sign-in. Amplify's oauth.domain
        // expects a bare hostname (it prepends https:// itself), and the suffix is
        // partition-specific (GovCloud uses auth-fips; EU Sovereign uses its own TLD).
        const cognitoHostedUiDomain = config.app.authProvider.useCognito.useSaml
            ? `${samlSettings.cognitoDomainPrefix}.${Service("COGNITO_HOSTED_UI").Endpoint}`
            : "";
        const amplifyConfig = new AmplifyConfigLambdaConstruct(this, "AmplifyConfig", {
            config,
            authResources: props.authResources,
            region: config.env.region,
            apiUrl: "", // derived at runtime from the request context (see construct)
            ...(config.app.authProvider.useCognito.useSaml
                ? {
                      cognitoFederatedConfig: {
                          customCognitoAuthDomain: cognitoHostedUiDomain,
                          customFederatedIdentityProviderName: samlSettings.name,
                      },
                  }
                : {}),
        });
        const versionFn = new VamsVersionLambdaConstruct(this, "Version", { config });
        registry.register({
            path: "/api/amplify-config",
            method: HttpMethod.GET,
            lambdaFn: amplifyConfig.lambdaFn,
            allowAnonymous: true,
        });
        registry.register({
            path: "/api/version",
            method: HttpMethod.GET,
            lambdaFn: versionFn.lambdaFn,
            allowAnonymous: true,
        });

        // 3) Build the OpenAPI document from the full registry
        const spec = buildOpenApiSpec(registry.list(), {
            authorizerFnArn: authorizerFn.functionArn,
            authorizerRole: authInvokeRole.roleArn,
            region: config.env.region,
            partition: Partition(),
            cors: {
                allowOrigins: "*",
                allowHeaders:
                    "Authorization,Content-Type,Origin,Range,X-Amz-Date,X-Api-Key,X-Amz-Security-Token,X-Amz-User-Agent,Access-Control-Allow-Origin",
                allowMethods: "GET,POST,PUT,PATCH,DELETE,OPTIONS,HEAD",
            },
            endpointType: apiGatewayRest.endpointType,
            vpcEndpointIds: apiGatewayVpcEndpointId ? [apiGatewayVpcEndpointId] : undefined,
            title: `${config.env.coreStackName}Api`,
        });

        // 4) SpecRestApi with explicit deployment control
        this.restApi = new apigw.SpecRestApi(this, "Api", {
            apiDefinition: apigw.ApiDefinition.fromInline(spec),
            endpointTypes: [
                apiGatewayRest.endpointType === "PRIVATE"
                    ? apigw.EndpointType.PRIVATE
                    : apigw.EndpointType.REGIONAL,
            ],
            deploy: false,
            // Provision the account-level API Gateway CloudWatch role (required for the
            // stage's execution logging) and tear it down with the stack. DESTROY avoids
            // an orphaned, fixed-named role colliding on a later redeploy after a rollback.
            cloudWatchRole: true,
            cloudWatchRoleRemovalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        // CORS headers on gateway-level responses (authorizer denials and errors).
        // Responses produced by API Gateway itself — the custom authorizer returning
        // Unauthorized (401) or Access Denied (403), a missing authentication token, and 4XX/5XX
        // errors — never reach a Lambda, so the per-handler `commonHeaders()` cannot add the
        // Access-Control-Allow-Origin header to them. Without it, the browser CORS-blocks these
        // responses and a benign token-expiry 401 surfaces as an unexplained CORS/network error
        // in the web app. Inject the allow-origin/headers on the default 4XX and 5XX gateway
        // responses (these cover UNAUTHORIZED, ACCESS_DENIED, MISSING_AUTHENTICATION_TOKEN, etc.)
        // so cross-origin callers can read the real status.
        const gatewayResponseCorsHeaders = {
            "Access-Control-Allow-Origin": "'*'",
            "Access-Control-Allow-Headers":
                "'Authorization,Content-Type,Origin,Range,X-Amz-Date,X-Api-Key,X-Amz-Security-Token,X-Amz-User-Agent,Access-Control-Allow-Origin'",
        };
        new apigw.GatewayResponse(this, "GatewayResponseDefault4XX", {
            restApi: this.restApi,
            type: apigw.ResponseType.DEFAULT_4XX,
            responseHeaders: gatewayResponseCorsHeaders,
        });
        new apigw.GatewayResponse(this, "GatewayResponseDefault5XX", {
            restApi: this.restApi,
            type: apigw.ResponseType.DEFAULT_5XX,
            responseHeaders: gatewayResponseCorsHeaders,
        });

        // 5) Grant API Gateway permission to invoke every registered Lambda
        const sourceArn = `arn:${Partition()}:execute-api:${config.env.region}:${
            config.env.account
        }:${this.restApi.restApiId}/*/*/*`;
        const invokedFns = new Set<string>();
        for (const r of registry.list()) {
            if (invokedFns.has(r.lambdaFn.functionArn)) continue;
            invokedFns.add(r.lambdaFn.functionArn);
            new cdk.aws_lambda.CfnPermission(
                this,
                `Invoke-${generateUniqueNameHash(
                    config.env.coreStackName,
                    config.env.account,
                    r.lambdaFn.functionArn,
                    10
                )}`,
                {
                    action: "lambda:InvokeFunction",
                    functionName: r.lambdaFn.functionArn,
                    principal: Service("APIGATEWAY").PrincipalString,
                    sourceArn: sourceArn,
                }
            );
        }

        const accessLogs = new logs.LogGroup(this, "VAMS-REST-API-AccessLogs", {
            retention: logs.RetentionDays.ONE_YEAR,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        const deployment = new apigw.Deployment(this, "Deployment", { api: this.restApi });
        deployment.addToLogicalId(spec); // any spec change -> new deployment -> auto redeploy

        const stage = new apigw.Stage(this, "Stage", {
            deployment,
            stageName: this.stageName,
            throttlingBurstLimit: apiGatewayRest.globalBurstLimit,
            throttlingRateLimit: apiGatewayRest.globalRateLimit,
            accessLogDestination: new apigw.LogGroupLogDestination(accessLogs),
            accessLogFormat: apigw.AccessLogFormat.jsonWithStandardFields(),
            loggingLevel: apigw.MethodLoggingLevel.INFO,
        });
        this.restApi.deploymentStage = stage;

        // 6.5) WAF association for the REST API stage (regional-scoped ACL — see above).
        if (props.wafArn && props.wafArn !== "" && wafEnabled) {
            const wafAssociation = new wafv2.CfnWebACLAssociation(this, "RestApiWafAssociation", {
                resourceArn: `arn:${Partition()}:apigateway:${config.env.region}::/restapis/${
                    this.restApi.restApiId
                }/stages/${this.stageName}`,
                webAclArn: props.wafArn,
            });
            // The resource ARN is a hand-built string, so CloudFormation cannot infer
            // that the stage must exist before the association is created.
            wafAssociation.node.addDependency(stage);
        }

        // 7) Endpoint outputs (non-FIPS URL in non-GovCloud, as today)
        this.apiEndpoint = `${this.restApi.restApiId}.${Service("EXECUTE_API", false).Endpoint}`;
        this.invokeUrlWithStage = `https://${this.apiEndpoint}/${this.stageName}`;

        new cdk.CfnOutput(this, "GatewayUrl", { value: `${this.invokeUrlWithStage}/` });

        // 8) Justified CDK Nag suppressions (spec-defined methods/authorizer)
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-APIG2",
                    reason: "Request validation is performed in Lambda handlers via Pydantic models; the REST API uses Lambda proxy integrations defined in the OpenAPI spec.",
                },
                {
                    id: "AwsSolutions-APIG4",
                    reason: "Authorization is enforced by the custom REQUEST Lambda authorizer declared as the OpenAPI security scheme on all non-anonymous routes; cdk-nag cannot introspect spec-defined methods.",
                },
                {
                    id: "AwsSolutions-COG4",
                    reason: "VAMS intentionally uses a custom Lambda authorizer (Cognito + external OAuth + API key + IP), not a Cognito user pool authorizer, per project Rule 5.",
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "API Gateway invoke permission scoped to this REST API's execute-api source ARN; Lambda execution roles use standard managed policies.",
                },
                {
                    id: "AwsSolutions-IAM4",
                    reason: "API Gateway's account-level CloudWatch role requires the AWS-managed AmazonAPIGatewayPushToCloudWatchLogs policy to deliver REST API execution/access logs; this role is auto-created by the SpecRestApi construct and cannot use a customer-managed policy.",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs",
                    ],
                },
            ],
            true
        );
    }
}

/**
 * Resolve the execute-api VPC interface endpoint id the REST API should use, or undefined
 * when no VPC endpoint applies.
 *
 * - REGIONAL: a public endpoint — never routes through a VPC endpoint. Always undefined,
 *   even when a VPC and endpoints are enabled (any created/external endpoint is ignored).
 * - PRIVATE: reachable only through an execute-api endpoint. Uses the VAMS-created endpoint
 *   when `useGlobalVpc.addVpcEndpoints` created one, otherwise the operator-supplied
 *   `optionalExternalPrivateApigVPCEId`. Config validation guarantees one of the two is set.
 */
export function resolveApiGatewayVpcEndpointId(
    config: Config.Config,
    vamsCreatedApiGatewayVpcEndpointId?: string
): string | undefined {
    const apiGatewayRest = config.app.api.apiGatewayRest;

    if (apiGatewayRest.endpointType !== "PRIVATE") {
        // REGIONAL is public; ignore any created or external endpoint.
        return undefined;
    }

    const vamsEndpointAvailable =
        config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.addVpcEndpoints;
    if (vamsEndpointAvailable && vamsCreatedApiGatewayVpcEndpointId) {
        return vamsCreatedApiGatewayVpcEndpointId;
    }
    const external = apiGatewayRest.optionalExternalPrivateApigVPCEId;
    return external && external !== "" ? external : undefined;
}
