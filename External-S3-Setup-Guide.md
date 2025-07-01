# External S3 Bucket Access Setup Guide for VAMS

This guide is for configuring external and cross-account S3 bucket access for the VAMS application. It covers all necessary permissions, IAM roles, and CORS configurations needed to allow the application to import external S3 buckets, add notifications to buckets for cross-account SNS, call on buckets cross-account in lambdas via BOTO3/AWS SDK, and generate presigned URLs.

## Terminology

- **Account A**: The AWS account where VAMS is deployed
- **Account B**: The external AWS account containing the S3 bucket(s) to be accessed

## 1. S3 Bucket Configuration Options

The VAMS application supports both creating new asset buckets and using existing external buckets:

- New buckets can be created with `app.assetBuckets.createNewBucket` configuration
- External buckets can be defined in `app.assetBuckets.externalAssetBuckets[]` configuration
- Each external bucket requires:
  - `bucketArn`
  - `baseAssetsPrefix`
  - `defaultSyncDatabaseId`

## 2. S3 Bucket Policy Requirements

DO THIS BEFORE DEPLOYING VAMS INFRASTRUCTURE CDK!!!
DO THIS BEFORE DEPLOYING VAMS INFRASTRUCTURE CDK!!!
DO THIS BEFORE DEPLOYING VAMS INFRASTRUCTURE CDK!!!

For external buckets (same account or cross-account (account B)), add this comprehensive policy that includes all necessary permissions:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "allow-vams",
            "Effect": "Allow",
            "Principal": {
                "AWS": "arn:aws:iam::<ACCOUNT ID VAMS>:root"
            },
            "Action": "s3:*",
            "Resource": [
                "arn:aws:s3:::<BUCKET NAME>/*",
                "arn:aws:s3:::<BUCKET NAME>"
            ]
        }
    ]
}
```

Replace:
- `<BUCKET NAME>` with the name of the external bucket
- `<ACCOUNT ID VAMS>` with the AWS account ID where VAMS is deployed (ACCOUNT A)


If you are looking to add more restrictions to just VAMS roles in the account for the S3 account, add under `Resource`:

```json
            "Condition": {
                "ArnEquals": {
                    "aws:PrincipalArn": [
                        "arn:aws:iam::<ACCOUNT ID VAMS>:role/<APPLICATION NAME>*",
                        "arn:aws:sts::<ACCOUNT ID VAMS>:assumed-role/<APPLICATION NAME>*"
                    ]
                }
            }
```  

Replace:
- `<APPLICATION NAME>` the name of the application configured in the VAMS CDK config file under `name` field. Default is `vams`.   

## 3. CORS Configuration for Cross-Account Access

For cross-account S3 buckets, this CORS policy is essential for browser-based operations including presigned URL access:

```json
[
    {
        "AllowedHeaders": [
            "*"
        ],
        "AllowedMethods": [
            "GET",
            "PUT",
            "POST",
            "HEAD",
            "OPTIONS",
        ],
        "AllowedOrigins": [
            "*"  // For production, restrict to specific origins (Cloudfront/ALB website domain)
        ],
        "ExposeHeaders": [
            "ETag",
            "x-amz-server-side-encryption",
            "x-amz-request-id",
            "x-amz-id-2"
        ],
        "MaxAgeSeconds": 0
    }
]
```

**Important**: For production deployments, the `AllowedOrigins` should be restricted to the specific origins needed rather than using the wildcard "*" such as the cloudfront/ALB website domain.


## 4. Additional Setup Handled During Deployment

- The system will attempt to add other resource policies to both created and external buckets during CDK deployment
- The S3 Assets Buckets DynamoDB table will be populated with available buckets
- Assets store which bucket and prefix they're assigned to upon creation
- Changes made directly to S3 buckets will sync back to DynamoDB tables and OpenSearch indexes

## 5. Testing Cross-Account Access

After setting up the permissions, you should test:

1. Direct S3 operations from VAMS Lambda functions to the external bucket
2. Presigned URL generation and access
3. S3 event notifications triggering SNS topics
4. Multipart upload operations

## 6. Troubleshooting

If you encounter issues with cross-account access:

1. Check CloudTrail logs for access denied errors
2. Verify that all required permissions are included in the bucket policy
3. Ensure CORS is properly configured for browser-based operations
4. Confirm that SNS topic policies allow publishing from the S3 bucket
5. Verify that the correct region is being used in all API calls
