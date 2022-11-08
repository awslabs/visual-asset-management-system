import * as lambda from "@aws-cdk/aws-lambda";
import * as cdk from "@aws-cdk/core";
import * as path from "path";

export function buildErrorApiFunction(
    scope: cdk.Construct,
): lambda.Function {
    const name = "errorAPI";
    const errorApiFunction = new lambda.Function(scope, name, {
        runtime: lambda.Runtime.PYTHON_3_8,
        handler: `${name}.lambda_handler`,
        code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/errors/`)),
        environment: {
        },
    });

    return errorApiFunction;
}
