import * as lambda from "aws-cdk-lib/aws-lambda";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as path from "path";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as kms from "aws-cdk-lib/aws-kms";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
} from "../helper/security";
import * as Config from "../../config/config";

export function buildUserRolesService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    rolesStorageTable: dynamodb.Table,
    userRolesStorageTable: dynamodb.Table,
    authEntitiesStorageTable: dynamodb.Table,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "userRolesService";
    const userRolesService = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.userRoles.${name}.lambda_handler`,
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
            ROLES_TABLE_NAME: rolesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: userRolesStorageTable.tableName,
            AUTH_TABLE_NAME: authEntitiesStorageTable.tableName,
        },
    });

    rolesStorageTable.grantReadWriteData(userRolesService);
    userRolesStorageTable.grantReadWriteData(userRolesService);
    authEntitiesStorageTable.grantReadWriteData(userRolesService);
    kmsKeyLambdaPermissionAddToResourcePolicy(userRolesService, kmsKey);
    globalLambdaEnvironmentsAndPermissions(userRolesService, config);
    return userRolesService;
}
