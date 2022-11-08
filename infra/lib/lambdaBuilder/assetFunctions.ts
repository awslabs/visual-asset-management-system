import * as lambda from "@aws-cdk/aws-lambda";
import * as cdk from "@aws-cdk/core";
import * as s3 from "@aws-cdk/aws-s3";
import * as path from "path";
import * as dynamodb from "@aws-cdk/aws-dynamodb";

export function buildAssetService(
    scope: cdk.Construct,
    assetStorageTable: dynamodb.Table,
    assetStorageBucket: s3.Bucket
): lambda.Function {
    const name = "assetService";
    const assetService = new lambda.Function(scope, name, {
        runtime: lambda.Runtime.PYTHON_3_8,
        handler: `${name}.lambda_handler`,
        code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/assets/`)),
        environment: {
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName
        },
    });
    assetStorageTable.grantReadWriteData(assetService);
    assetStorageBucket.grantReadWrite(assetService);
    return assetService;
}

export function buildUploadAssetFunction(
    scope: cdk.Construct,
    assetStorageBucket: s3.Bucket,
    databaseStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table
): lambda.Function {
    const name = "uploadAsset";
    const uploadAssetFunction = new lambda.Function(scope, name, {
        runtime: lambda.Runtime.PYTHON_3_8,
        handler: `${name}.lambda_handler`,
        code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/assets/`)),
        environment: {
            DATABASE_STORAGE_TABLE_NAME: databaseStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
        },
    });
    assetStorageBucket.grantReadWrite(uploadAssetFunction);
    databaseStorageTable.grantReadWriteData(uploadAssetFunction);
    assetStorageTable.grantReadWriteData(uploadAssetFunction);
    return uploadAssetFunction;
}

export function buildUploadAllAssetsFunction(
    scope: cdk.Construct,
    assetStorageBucket: s3.Bucket,
    databaseStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table,
    workflowExecutionTable: dynamodb.Table,
    uploadAssetLambdaFunction: lambda.Function
): lambda.Function {
    const name = "uploadAllAssets";
    const uploadAllAssetFunction = new lambda.Function(scope, name, {
        runtime: lambda.Runtime.PYTHON_3_8,
        handler: `${name}.lambda_handler`,
        code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/assets/`)),
        environment: {
            DATABASE_STORAGE_TABLE_NAME: databaseStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
            WORKFLOW_EXECUTION_STORAGE_TABLE_NAME: workflowExecutionTable.tableName,
            UPLOAD_LAMBDA_FUNCTION_NAME: uploadAssetLambdaFunction.functionName,
        },
    });
    uploadAssetLambdaFunction.grantInvoke(uploadAllAssetFunction);
    assetStorageBucket.grantReadWrite(uploadAllAssetFunction);
    databaseStorageTable.grantReadWriteData(uploadAllAssetFunction);
    assetStorageTable.grantReadData(uploadAllAssetFunction);
    workflowExecutionTable.grantReadWriteData(uploadAllAssetFunction);
    return uploadAllAssetFunction;
}

export function buildAssetMetadataFunction(
    scope: cdk.Construct,
    assetStorageBucket: s3.Bucket,
    assetStorageTable: dynamodb.Table
) {
    const name = "metadata";
    const assetMetadataFunction = new lambda.Function(scope, name, {
        runtime: lambda.Runtime.PYTHON_3_8,
        handler: `${name}.lambda_handler`,
        code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/assets/`)),
        environment: {
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
        },
    });
    assetStorageBucket.grantRead(assetMetadataFunction);
    assetStorageTable.grantReadData(assetMetadataFunction);

    return assetMetadataFunction;
}

export function buildAssetColumnsFunction(
    scope: cdk.Construct,
    assetStorageBucket: s3.Bucket,
    assetStorageTable: dynamodb.Table
) {
    const name = "assetColumns";
    const assetColumnsFunction = new lambda.Function(scope, name, {
        runtime: lambda.Runtime.PYTHON_3_8,
        handler: `${name}.lambda_handler`,
        code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/assets/`)),
        environment: {
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName
        },
    });
    assetStorageBucket.grantRead(assetColumnsFunction);
    assetStorageTable.grantReadData(assetColumnsFunction);

    return assetColumnsFunction;
}

export function downloadAssetFunction(
    scope: cdk.Construct,
    assetStorageBucket: s3.Bucket,
    assetStorageTable: dynamodb.Table
) {
    const name = "downloadAsset";
    const downloadAssetFunction = new lambda.Function(scope, name, {
        runtime: lambda.Runtime.PYTHON_3_8,
        handler: `${name}.lambda_handler`,
        code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/assets/`)),
        environment: {
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
        },
    });
    assetStorageBucket.grantRead(downloadAssetFunction);
    assetStorageTable.grantReadData(downloadAssetFunction);

    return downloadAssetFunction;
}

export function buildRevertAssetFunction(
    scope: cdk.Construct,
    assetStorageBucket: s3.Bucket,
    databaseStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table
): lambda.Function {
    const name = "revertAsset";
    const revertAssetFunction = new lambda.Function(scope, name, {
        runtime: lambda.Runtime.PYTHON_3_8,
        handler: `${name}.lambda_handler`,
        code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/assets/`)),
        environment: {
            DATABASE_STORAGE_TABLE_NAME: databaseStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
        },
    });
    assetStorageBucket.grantReadWrite(revertAssetFunction);
    databaseStorageTable.grantReadData(revertAssetFunction);
    assetStorageTable.grantReadWriteData(revertAssetFunction);
    return revertAssetFunction;
}

