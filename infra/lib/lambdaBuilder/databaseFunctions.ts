import * as lambda from "@aws-cdk/aws-lambda";
import * as cdk from "@aws-cdk/core";
import * as path from "path";
import * as dynamodb from "@aws-cdk/aws-dynamodb";

export function buildCreateDatabaseLambdaFunction(
    scope: cdk.Construct,
    databaseStorageTable: dynamodb.Table
): lambda.Function {
    const name = "createDatabase";
    const createDatabaseFunction = new lambda.Function(scope, name, {
        runtime: lambda.Runtime.PYTHON_3_8,
        handler: `${name}.lambda_handler`,
        code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/databases/`)),
        environment: {
            DATABASE_STORAGE_TABLE_NAME: databaseStorageTable.tableName
        },
    });
    databaseStorageTable.grantReadWriteData(createDatabaseFunction);
    return createDatabaseFunction;
}

export function buildDatabaseService(
    scope: cdk.Construct,
    databaseStorageTable: dynamodb.Table,
    workflowStorageTable: dynamodb.Table,
    pipelineStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table
): lambda.Function {
    const name = "databaseService";
    const databaseService = new lambda.Function(scope, name, {
        runtime: lambda.Runtime.PYTHON_3_8,
        handler: `${name}.lambda_handler`,
        code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/databases/`)),
        environment: {
            DATABASE_STORAGE_TABLE_NAME: databaseStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
            PIPELINE_STORAGE_TABLE_NAME: pipelineStorageTable.tableName,
            WORKFLOW_STORAGE_TABLE_NAME: workflowStorageTable.tableName
        },
    });
    databaseStorageTable.grantReadWriteData(databaseService);
    workflowStorageTable.grantReadData(databaseService);
    pipelineStorageTable.grantReadData(databaseService);
    assetStorageTable.grantReadData(databaseService);

    return databaseService;
}
