import * as lambda from "@aws-cdk/aws-lambda";
import * as cdk from "@aws-cdk/core";
import * as s3 from "@aws-cdk/aws-s3";
import * as path from "path";
export function buildConfigService(
    scope: cdk.Construct,
    assetStorageBucket: s3.Bucket
): lambda.Function {
    const name = "configService";
    const assetService = new lambda.Function(scope, name, {
        runtime: lambda.Runtime.PYTHON_3_8,
        handler: `${name}.lambda_handler`,
        code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/config/`)),
        environment: {
            ASSET_STORAGE_BUCKET: assetStorageBucket.bucketName
        },
    });
    return assetService;
}