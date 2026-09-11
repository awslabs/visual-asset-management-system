# VAMS CDK Templates

Copy-paste scaffolds for common CDK additions in `infra/`. For the full patterns and rules, see `infra/CLAUDE.md`. Gold-standard reference files: `lib/lambdaBuilder/assetFunctions.ts`, `lib/nestedStacks/apiLambda/apiBuilder-nestedStack.ts`.

---

## New Lambda Builder Function

```typescript
export function buildMyNewFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "myNewFunction";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.myCategory.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            // Handler-specific env vars only (resource names resolved from SSM)
            // OPTIONAL_HANDLER_SPECIFIC_VAR: "value",
        },
    });

    // Grant DynamoDB permissions
    storageResources.dynamo.myTable.grantReadWriteData(fun);

    // Required security calls
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config); // Injects VAMS_RESOURCE_PARAM_PREFIX + SSM grant
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}
```

---

## New API Route Wiring (in apiBuilder-nestedStack.ts)

```typescript
// Build the function
const myFunction = buildMyNewFunction(
    this,
    lambdaCommonBaseLayer,
    storageResources,
    config,
    vpc,
    subnets
);

// Wire to API Gateway
attachFunctionToApi(this, myFunction, {
    routePath: "/my-resource/{resourceId}",
    method: apigateway.HttpMethod.GET,
    api: api,
});
attachFunctionToApi(this, myFunction, {
    routePath: "/my-resource",
    method: apigateway.HttpMethod.POST,
    api: api,
});
```

---

## New Nested Stack

```typescript
import { NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";
import * as Config from "../../../config/config";
import { storageResources } from "../storage/storageBuilder-nestedStack";

export interface MyBuilderNestedStackProps {
    config: Config.Config;
    storageResources: storageResources;
    // Add other required resources
}

export class MyBuilderNestedStack extends NestedStack {
    constructor(parent: Construct, name: string, props: MyBuilderNestedStackProps) {
        super(parent, name);
        // Build resources here
    }
}
```

---

## New Config Property (with backward compatibility)

```typescript
// 1. Add to ConfigPublic interface
app: {
    myNewFeature: {
        enabled: boolean;
        someOption: string;
    }
}

// 2. Add backward-compatibility check in getConfig()
if (config.app.myNewFeature == undefined) {
    config.app.myNewFeature = {
        enabled: false,
        someOption: "",
    };
}

// 3. Add validation if needed
if (config.app.myNewFeature.enabled && !config.app.myNewFeature.someOption) {
    throw new Error("Configuration Error: myNewFeature requires someOption when enabled");
}
```

Also mirror new config properties into the docs-site ConfigBuilder (`documentation/docusaurus-site/src/components/ConfigBuilder/` — `schema.ts`, `defaults.ts`, `validation.ts` per its `README.md`), then run `infra/test/configBuilderSync.test.ts` (`cd infra && npm test`).
