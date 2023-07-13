import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as path from "path";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { suppressCdkNagErrorsByGrantReadWrite } from "../security";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";

export function buildAddCommentLambdaFunction(
    scope: Construct,
    commentStorageTable: dynamodb.Table,
): lambda.Function {
    const name = "addComment";
    const addCommentFunction = new lambda.DockerImageFunction(scope, name, {
        code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, `../../../backend/`), {
            cmd: ["backend.handlers.comments.addComment.lambda_handler"],
        }),
        timeout: Duration.minutes(15),
        memorySize: 3008,
        environment: {
            COMMENT_STORAGE_TABLE_NAME: commentStorageTable.tableName,
        },
    });
    commentStorageTable.grantReadWriteData(addCommentFunction);
    return addCommentFunction;
}

export function buildCommentService(
    scope: Construct,
    commentStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table,
): lambda.Function {
    const name = "commentService";
    const commentService = new lambda.DockerImageFunction(scope, name, {
        code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, `../../../backend/`), {
            cmd: [`backend.handlers.comments.${name}.lambda_handler`],
        }),
        timeout: Duration.minutes(15),
        memorySize: 3008,
        environment: {
            COMMENT_STORAGE_TABLE_NAME: commentStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
        },
    });
    assetStorageTable.grantReadWriteData(commentService);
    commentStorageTable.grantReadWriteData(commentService);

    suppressCdkNagErrorsByGrantReadWrite(scope);

    return commentService;
}
