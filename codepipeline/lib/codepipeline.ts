import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { Bucket, BlockPublicAccess } from 'aws-cdk-lib/aws-s3';
import { RemovalPolicy } from 'aws-cdk-lib';
import { CodeBuildAction, CodeCommitSourceAction, CodeStarConnectionsSourceAction, GitHubSourceAction } from 'aws-cdk-lib/aws-codepipeline-actions'
import { Artifact, Pipeline, } from 'aws-cdk-lib/aws-codepipeline';
import { BuildSpec, LinuxBuildImage, PipelineProject, ComputeType } from 'aws-cdk-lib/aws-codebuild';
import { PolicyStatement, AccountRootPrincipal, PolicyDocument, Effect, AnyPrincipal } from 'aws-cdk-lib/aws-iam';
import { NagSuppressions } from "cdk-nag/lib/nag-suppressions";
import { Key } from 'aws-cdk-lib/aws-kms';
import { Duration } from 'aws-cdk-lib';


export class CodePipelineStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const region = props?.env?.region || "us-east-1";
    const stackName = process.env.STACK_NAME || this.node.tryGetContext("stack-name");
    const pipelineName = props?.stackName;
    const dockerDefaultPlatform = process.env.DOCKER_DEFAULT_PLATFORM || "linux/amd64";
    const adminEmailAddress =
        process.env.VAMS_ADMIN_EMAIL || this.node.tryGetContext("adminEmailAddress");
    const repositoryOwner = process.env.REPO_OWNER || this.node.tryGetContext("repo-owner");
    const connectionArn =
        process.env.CONNECTION_ARN || this.node.tryGetContext("connection-arn");
    const branch = process.env.BRANCH_NAME || "main";

    console.log(region);
    console.log(stackName);
    console.log(pipelineName);
    console.log(adminEmailAddress);
    console.log(repositoryOwner);
    console.log(branch);

    // const backendRepo = codecommit.Repository.fromRepositoryName(this, 'backend-repo', 'sample-lambda');

    const kms = new Key(this, 'VamsCodePipelineEncryptionKey', {
        alias: 'vams-code-pipeline-encryption-key',
        enableKeyRotation: true,
        policy: new PolicyDocument(
            {
                statements: [
                        new PolicyStatement({
                        actions: ['kms:*'],
                        resources: ['*'],
                        principals: [new AccountRootPrincipal()],
                    })
                ]
            }
        )
    })

    const accessLogsBucket = new Bucket(this, "AccessLogsBucket", {
        bucketName: `${stackName}-access-logs-bucket`,
        encryptionKey: kms,
        blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
        enforceSSL: true,
        removalPolicy: RemovalPolicy.RETAIN,
        lifecycleRules: [
            {
                enabled: true,
                expiration: Duration.days(1),
                noncurrentVersionExpiration: Duration.days(1),
            },
        ],
    });

    // requireTLSAddToResourcePolicy(accessLogsBucket);

    const artifactBucket = new Bucket(this, "ArtifactBucket", {
        bucketName: `${stackName}-artifact-bucket`,
        encryptionKey: kms,
        blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
        enforceSSL: true,
        versioned: false,
        removalPolicy: RemovalPolicy.RETAIN,
        serverAccessLogsBucket: accessLogsBucket,
        serverAccessLogsPrefix: "asset-bucket-logs/",
    });
    // requireTLSAddToResourcePolicy(artifactBucket);

    const pipeline = new Pipeline(this, "Pipeline", {
      pipelineName: "ModularAppPipeline",
      artifactBucket: artifactBucket
    });

    // CodeCommit Repository for Backend Developer
    const backendSourceArtifact = new Artifact('BackendStack');
    const backendSourceAction = new CodeStarConnectionsSourceAction({
        branch: branch,
        connectionArn: connectionArn,
        output: backendSourceArtifact,
        owner: repositoryOwner,
        repo: "visual-asset-management-system",
        actionName: 'Download_Backend'
    });

    pipeline.addStage({
      stageName: "SourceStage",
      actions : [
        backendSourceAction
      ]
    });

    // Setup environment for Backend application
    // Import Lambda repository into CodePipeline and run build scripts on CodeBuild
    const prepareBackendProject = new PipelineProject(
      this,
      "PrepareBackendProject",
      {
        buildSpec: BuildSpec.fromObject({
          version: "0.2",
          phases: {
            install: {
              commands: ["cd web", "yarn install", "cd ../infra", "npm install",]
            },
            build: {
              commands : ["ls", "cd ../web", "npm run build",  "cd ../infra", "npm run build", "cdk synth"]
            },
          },
          artifacts: {
            // store the entire Cloud Assembly as the output artifact
            "base-directory": "infra/cdk.out",
            files: "**/*",
          },
        }),
        environment: {
          buildImage: LinuxBuildImage.STANDARD_7_0,
          privileged: true,
          computeType: ComputeType.MEDIUM,
        },
        environmentVariables: {
            "VAMS_ADMIN_EMAIL": { value: adminEmailAddress },
            "DOCKER_DEFAULT_PLATFORM": { value: "linux/amd64" },
            "STACK_NAME": { value: stackName },
            "REGION": { value: region },
          }
      }
    );

    // Build CodeBuild artifact for API (Backend)
    const prepareApiOutput = new Artifact("API2");
    const prepareApiAction = new CodeBuildAction({
      actionName: "UpdateAPIStack",
      project: prepareBackendProject,
      input: backendSourceArtifact,
      outputs: [prepareApiOutput],
      runOrder: 1,
    });

    // Deploy API using CDK on CodeBuild Project
    const deployApiProject = new PipelineProject(
      this,
      "DeployApiProject",
      {
        buildSpec: BuildSpec.fromObject({
          version: "0.2",
          phases: {
            install: {
              commands: ["cd infra", "npm install -g aws-cdk"]
            },
            build: {
              commands: [
                `cdk deploy --all`,
              ],
            },
          },
          artifacts: {
            files: "config.json",
          },
        }),
        environment: {
            buildImage:  LinuxBuildImage.STANDARD_7_0,
            privileged: true,
            computeType: ComputeType.MEDIUM
        },
      }
    );

    // Give permission to assume Action Role
    empowerProject(deployApiProject);

    const deployApiOutput = new Artifact("CONFIG2");
    const deployApiAction = new CodeBuildAction({
      actionName: "DeployAPIStack",
      input: prepareApiOutput,
      project: deployApiProject,
      runOrder: 2,
      outputs: [deployApiOutput],
      environmentVariables: {
        "VAMS_ADMIN_EMAIL": { value: adminEmailAddress },
        "DOCKER_DEFAULT_PLATFORM": { value: "linux/amd64" },
        "STACK_NAME": { value: stackName },
        "REGION": { value: region },
      }
    });

    // Add API actions into pipeline
    pipeline.addStage({
      stageName: "Backend",
      actions : [
        prepareApiAction,
        deployApiAction
      ]
    });

    NagSuppressions.addStackSuppressions(this, [
        {
            id: "AwsSolutions-IAM5",
            reason: "Using service roles created with required permissions by CDK for code pipeline with required permissions.",
        },
        {
            id: "AwsSolutions-CB3",
            reason: "The project requires access to Docker for building VAMS application and hence privileged mode is required.",
        },
    ]);
  }
}

function empowerProject(project: PipelineProject) {
  // allow the self-mutating project permissions to assume the bootstrap Action role
  project.addToRolePolicy(
    new PolicyStatement({
      actions: ["sts:AssumeRole"],
      resources: [
        "arn:*:iam::*:role/*-deploy-role-*",
        "arn:*:iam::*:role/*-publishing-role-*",
      ],
    })
  );
  project.addToRolePolicy(
    new PolicyStatement({
      actions: ["cloudformation:DescribeStacks"],
      resources: ["*"], // this is needed to check the status of the bootstrap stack when doing `cdk deploy`
    })
  );
}

function requireTLSAddToResourcePolicy(bucket: Bucket) {
    bucket.addToResourcePolicy(
        new PolicyStatement({
            effect: Effect.DENY,
            principals: [new AnyPrincipal()],
            actions: ["s3:*"],
            resources: [`${bucket.bucketArn}/*`, bucket.bucketArn],
            conditions: {
                Bool: { "aws:SecureTransport": "false" },
            },
        })
    );
}