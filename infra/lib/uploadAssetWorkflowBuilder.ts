import { Construct } from "constructs";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as logs from 'aws-cdk-lib/aws-logs';
import { Duration } from "aws-cdk-lib";
import { TaskInput } from "aws-cdk-lib/aws-stepfunctions";

export function buildUploadAssetWorkflow(
    scope: Construct,
    uploadAssetFunction: lambda.Function
): sfn.StateMachine {
    const callUploadAssetLambdaTask = new tasks.LambdaInvoke(scope, "Upload Asset Task", {
        lambdaFunction: uploadAssetFunction,
        payload: TaskInput.fromJsonPathAt("$.uploadAssetBody"),
    });
    const logGroup = new logs.LogGroup(scope, 'UploadAssetWorkflowLogs');
    const definition = callUploadAssetLambdaTask;
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
