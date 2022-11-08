import * as lambda from "@aws-cdk/aws-lambda";
import * as cdk from "@aws-cdk/core";
import * as path from "path";
import * as dynamodb from "@aws-cdk/aws-dynamodb";
import * as iam from "@aws-cdk/aws-iam";

export function buildListLambdasFunction(
    scope: cdk.Construct,
    pipelineStorageTable: dynamodb.Table,
): lambda.Function {
    const name = "listLambdas";
    const listLambdasFunction = new lambda.Function(scope, name, {
        runtime: lambda.Runtime.PYTHON_3_8,
        handler: `${name}.lambda_handler`,
        code: lambda.Code.fromAsset(path.join(__dirname, `../lambdas/transformers/`)),
        environment: {
            PIPELINE_STORAGE_TABLE_NAME: pipelineStorageTable.tableName,
        },
    });
    pipelineStorageTable.grantReadWriteData(listLambdasFunction);
    listLambdasFunction.addToRolePolicy(new iam.PolicyStatement({
        resources: ["*"],
        actions: ['lambda:ListFunctions', 'lambda:ListTags']
    }))
    return listLambdasFunction;
}
