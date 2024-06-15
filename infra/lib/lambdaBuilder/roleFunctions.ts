import * as lambda from "aws-cdk-lib/aws-lambda";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as path from "path";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as kms from "aws-cdk-lib/aws-kms";
import { kmsKeyLambdaPermissionAddToResourcePolicy } from "../helper/security";
import * as Config from "../../config/config";

export function buildRoleService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    rolesStorageTable: dynamodb.Table,
    authEntitiesStorageTable: dynamodb.Table,
    userRolesStorageTable: dynamodb.Table,
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "roleService";
    const roleService = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.roles.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        environment: {
            ROLES_STORAGE_TABLE_NAME: rolesStorageTable.tableName,
            AUTH_TABLE_NAME: authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: userRolesStorageTable.tableName,
        },
    });

    rolesStorageTable.grantReadWriteData(roleService);
    authEntitiesStorageTable.grantReadWriteData(roleService);
    userRolesStorageTable.grantReadWriteData(roleService);
    kmsKeyLambdaPermissionAddToResourcePolicy(roleService, kmsKey);
    return roleService;
}

export function buildCreateRoleFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    rolesStorageTable: dynamodb.Table,
    authEntitiesStorageTable: dynamodb.Table,
    userRolesStorageTable: dynamodb.Table,
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "createRole";
    const createRoleFunction = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.roles.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        environment: {
            ROLES_STORAGE_TABLE_NAME: rolesStorageTable.tableName,
            AUTH_TABLE_NAME: authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: userRolesStorageTable.tableName,
        },
    });

    rolesStorageTable.grantReadWriteData(createRoleFunction);
    authEntitiesStorageTable.grantReadWriteData(createRoleFunction);
    userRolesStorageTable.grantReadWriteData(createRoleFunction);
    kmsKeyLambdaPermissionAddToResourcePolicy(createRoleFunction, kmsKey);
    return createRoleFunction;
}
