import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { suppressCdkNagErrorsByGrantReadWrite } from "../helper/security";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as kms from "aws-cdk-lib/aws-kms";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
} from "../helper/security";

export function buildAddCommentLambdaFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    commentStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table,
    userRolesStorageTable: dynamodb.Table,
    authEntitiesStorageTable: dynamodb.Table,
    rolesStorageTable: dynamodb.Table,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "addComment";
    const addCommentFunction = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.comments.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            COMMENT_STORAGE_TABLE_NAME: commentStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
            AUTH_TABLE_NAME: authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: rolesStorageTable.tableName,
        },
    });
    commentStorageTable.grantReadWriteData(addCommentFunction);
    assetStorageTable.grantReadWriteData(addCommentFunction);
    authEntitiesStorageTable.grantReadWriteData(addCommentFunction);
    userRolesStorageTable.grantReadWriteData(addCommentFunction);
    rolesStorageTable.grantReadData(addCommentFunction);
    kmsKeyLambdaPermissionAddToResourcePolicy(addCommentFunction, kmsKey);
    globalLambdaEnvironmentsAndPermissions(addCommentFunction, config);
    return addCommentFunction;
}

export function buildEditCommentLambdaFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    commentStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table,
    userRolesStorageTable: dynamodb.Table,
    authEntitiesStorageTable: dynamodb.Table,
    rolesStorageTable: dynamodb.Table,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "editComment";
    const editCommentFunction = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.comments.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            COMMENT_STORAGE_TABLE_NAME: commentStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
            AUTH_TABLE_NAME: authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: rolesStorageTable.tableName,
        },
    });
    commentStorageTable.grantReadWriteData(editCommentFunction);
    assetStorageTable.grantReadWriteData(editCommentFunction);
    authEntitiesStorageTable.grantReadWriteData(editCommentFunction);
    userRolesStorageTable.grantReadWriteData(editCommentFunction);
    rolesStorageTable.grantReadData(editCommentFunction);
    kmsKeyLambdaPermissionAddToResourcePolicy(editCommentFunction, kmsKey);
    globalLambdaEnvironmentsAndPermissions(editCommentFunction, config);
    return editCommentFunction;
}

export function buildCommentService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    commentStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table,
    userRolesStorageTable: dynamodb.Table,
    authEntitiesStorageTable: dynamodb.Table,
    rolesStorageTable: dynamodb.Table,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "commentService";
    const commentService = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.comments.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            COMMENT_STORAGE_TABLE_NAME: commentStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
            AUTH_TABLE_NAME: authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: rolesStorageTable.tableName,
        },
    });
    assetStorageTable.grantReadWriteData(commentService);
    authEntitiesStorageTable.grantReadWriteData(commentService);
    userRolesStorageTable.grantReadWriteData(commentService);
    commentStorageTable.grantReadWriteData(commentService);
    rolesStorageTable.grantReadData(commentService);
    kmsKeyLambdaPermissionAddToResourcePolicy(commentService, kmsKey);
    globalLambdaEnvironmentsAndPermissions(commentService, config);

    suppressCdkNagErrorsByGrantReadWrite(scope);

    return commentService;
}
