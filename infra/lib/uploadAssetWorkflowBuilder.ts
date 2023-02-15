import { Construct } from "constructs";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as logs from 'aws-cdk-lib/aws-logs';
import { Duration } from "aws-cdk-lib";
import { State, TaskInput } from "aws-cdk-lib/aws-stepfunctions";

export function buildUploadAssetWorkflow(
    scope: Construct,
    uploadAssetFunction: lambda.Function, 
    updateMetadataFunction: lambda.Function,
    executeWorkflowFunction: lambda.Function,
): sfn.StateMachine {
    const callUploadAssetLambdaTask = new tasks.LambdaInvoke(scope, "Upload Asset Task", {
        lambdaFunction: uploadAssetFunction,
        payload: TaskInput.fromJsonPathAt("$.uploadAssetBody"),
    });
    const callUpdateMetadataTask = new tasks.LambdaInvoke(scope, "Update Metadata Task", {
        lambdaFunction: updateMetadataFunction,
        payload: TaskInput.fromJsonPathAt("$.updateMetadataBody"),
    });
    const map = new sfn.Map(scope, 'Map State', {
        maxConcurrency: 1,
        itemsPath: sfn.JsonPath.stringAt('$.executeWorkflowBody'),
      });
    const callExecuteWorkflowTask = new tasks.LambdaInvoke(scope, "Call Execute Workflows Task", {
        lambdaFunction: executeWorkflowFunction
    });
    map.iterator(callExecuteWorkflowTask);

    const logGroup = new logs.LogGroup(scope, 'UploadAssetWorkflowLogs');
    const definition = callUploadAssetLambdaTask.next(callUpdateMetadataTask).next(map);
    return new sfn.StateMachine(scope, "UploadAssetWorkflow", {
        definition: definition,
        timeout: Duration.minutes(10),
        logs: {
            level: sfn.LogLevel.ALL,
            destination: logGroup
        },
        tracingEnabled: true //This was pointed out by CDK-Nag
    });
}
