# External S3 Bucket Access Setup Guide for VAMS

This guide is for configuring external and cross-account S3 bucket access for the VAMS application. It covers all necessary permissions, IAM roles, and CORS configurations needed to allow the application to import external S3 buckets, add notifications to buckets for cross-account SNS, call on buckets cross-account in lambdas via BOTO3/AWS SDK, and generate presigned URLs.

## Terminology

-   **Account A**: The AWS account where VAMS is deployed
-   **Account B**: The external AWS account containing the S3 bucket(s) to be accessed

## 1. S3 Bucket Configuration Options

The VAMS application supports both creating new asset buckets and using existing external buckets:

-   New buckets can be created with `app.assetBuckets.createNewBucket` configuration
-   External buckets can be defined in `app.assetBuckets.externalAssetBuckets[]` configuration
-   Each external bucket requires:
    -   `bucketArn`
    -   `baseAssetsPrefix`
    -   `defaultSyncDatabaseId`

## 2. S3 Bucket Policy Requirements

DO THIS BEFORE DEPLOYING VAMS INFRASTRUCTURE CDK!!!
DO THIS BEFORE DEPLOYING VAMS INFRASTRUCTURE CDK!!!
DO THIS BEFORE DEPLOYING VAMS INFRASTRUCTURE CDK!!!

For external buckets (same account or cross-account (account B)), add this comprehensive policy that includes all necessary permissions.

Note: Update with your respective partition in your ARN as needed (ie. commercial vs GovCloud)

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "allow-vams",
            "Effect": "Allow",
            "Principal": {
                "AWS": "arn:aws:iam::<ACCOUNT ID VAMS - ACCOUNT A>:root"
            },
            "Action": "s3:*",
            "Resource": ["arn:aws:s3:::<BUCKET NAME>/*", "arn:aws:s3:::<BUCKET NAME>"]
        }
    ]
}
```

Replace:

-   `<BUCKET NAME>` with the name of the external bucket
-   `<ACCOUNT ID VAMS - ACCOUNT A>` with the AWS account ID where VAMS is deployed (ACCOUNT A)

If you are looking to add more restrictions to just VAMS roles in the account for the S3 account, add under `Resource`:

```json
            "Condition": {
                "ArnEquals": {
                    "aws:PrincipalArn": [
                        "arn:aws:iam::<ACCOUNT ID VAMS - ACCOUNT A>:role/<APPLICATION NAME>*",
                        "arn:aws:sts::<ACCOUNT ID VAMS - ACCOUNT A>:assumed-role/<APPLICATION NAME>*"
                    ]
                }
            }
```

Replace:

-   `<APPLICATION NAME>` the name of the application configured in the VAMS CDK config file under `name` field. Default is `vams`.

## 3. CORS Configuration for Cross-Account Access

For cross-account S3 buckets, this CORS policy is essential for browser-based operations including presigned URL access:

```json
[
    {
        "AllowedHeaders": ["*"],
        "AllowedMethods": ["GET", "PUT", "POST", "HEAD", "OPTIONS"],
        "AllowedOrigins": [
            "*" // For production, restrict to specific origins (Cloudfront/ALB website domain)
        ],
        "ExposeHeaders": ["ETag", "x-amz-server-side-encryption", "x-amz-request-id", "x-amz-id-2"],
        "MaxAgeSeconds": 0
    }
]
```

**Important**: For production deployments, the `AllowedOrigins` should be restricted to the specific origins needed rather than using the wildcard "\*" such as the cloudfront/ALB website domain.

## 4. Cross-account IAM role

The cross-account role in account B (the bucket account) only says that _any_ identity from account A (VAMS solution stack) granted access to the bucket can access the bucket if an entity with permission to grant them access does so, not that they _do_ have access.

### 4.1 - Account B Role / Policy

Configure the trust relationship on a new role:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "AWS": "arn:aws:iam::<ACCOUNT ID VAMS - ACCOUNT A>:root" // Account A.
            },
            "Action": "sts:AssumeRole",
            "Condition": {}
        }
    ]
}
```

Configure the policy:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": ["s3:*"],
            "Resource": ["arn:aws:s3:::<BUCKET NAME>", "arn:aws:s3:::<BUCKET NAME>/*"]
        },
        {
            "Effect": "Allow", //optional depending on KMS keys are involved
            "Action": ["kms:Decrypt", "kms:GenerateDataKey"],
            "Resource": "*"
        }
    ]
}
```

Replace:

-   `<BUCKET NAME>` with the name of the external bucket
-   `<ACCOUNT ID VAMS - ACCOUNT A>` with the AWS account ID where VAMS is deployed (ACCOUNT A)

### 4.2 - Account A Policy

Make sure to give the identity you're deploying VAMS with access to the external bucket in account B. Add this policy to the deploy user in account A:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": ["s3:*"],
            "Resource": "arn:aws:s3:::<BUCKET NAME>/*"
        }
    ]
}
```

Then provision AWS credentials in account A for that deploy user and configured them as an AWS CLI profile named `vams`.

Replace:

-   `<BUCKET NAME>` with the name of the external bucket

### 4.3 Deployment

When deploying VAMS, make sure to pass the AWS CLI profile for the identity with access to the bucket:

```
npm run build; cd infra; npx cdk deploy --all --require-approval never --profile vams
```

## 5. Additional Setup Handled During Deployment

-   The system will attempt to add other resource policies to both created and external buckets during CDK deployment
-   The S3 Assets Buckets DynamoDB table will be populated with available buckets
-   Assets store which bucket and prefix they're assigned to upon creation
-   Changes made directly to S3 buckets will sync back to DynamoDB tables and OpenSearch indexes

## 6. Testing Cross-Account Access

After setting up the permissions, you should test:

1. Direct S3 operations from VAMS Lambda functions to the external bucket
2. Presigned URL generation and access
3. S3 event notifications triggering SNS topics
4. Multipart upload operations

## 7. Troubleshooting

If you encounter issues with cross-account access:

1. Check CloudTrail logs for access denied errors
2. Verify that all required permissions are included in the bucket policy
3. Ensure CORS is properly configured for browser-based operations
4. Confirm that SNS topic policies allow publishing from the S3 bucket
5. Verify that the correct region is being used in all API calls
