# Costs

The costs of this solution can be understood as fixed storage costs and variable costs of the pipelines that you configure. Storage cost is proportional to the amount of data you upload to VAMS including new data you create using VAMS pipelines.

You are responsible for the cost of the AWS services used while running this solution. Ensure that you have [billing alarms](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/monitor_estimated_charges_with_cloudwatch.html) set within the constraints of your budget.

Configuration Options:

0. C-0: Deploy VPC with variable endpoints based on below configuration needs (Optional). Option to import existing VPC/Subnets w/ Endpoints.
1. C-1: Deploy Static Webpage with Cloudfront (Default) or ALB. ALB requires VPC with 2 AZ
2. C-2: Deploy OpenSearch with Serverless (Default), Provisioned, or No Open Search. Provisioned requires VPC with 3 AZ
3. C-3: Deploy all Lambdas in VPC (Optional). Requires VPC with 1 AZ
4. C-4: Deploy with location services (Default).
5. C-5: Deploy use-case specific pipelines [i.e. PotreeViewer Pipelines, GenAI Metadata generation] (Optional). Requires VPC with 1 AZ.

An approximate monthly cost breakdown is below (excluding some free tier inclusions):

| Service                                       | Quantity                                                            | Cost (Commercial) | Cost (GovCloud) |
| :-------------------------------------------- | :------------------------------------------------------------------ | :---------------- | :-------------- |
| VPC (C-0 + C-1/C-2/C-3/C-5,Optional)          | 1-11x Endpoints per AZ (up to 3 AZ) - based on config options       | $<240.91          | $<311.13        |
| Amazon Cloudfront (C-1,Default)               | First 1TB - Included in free tier                                   | $0.00             | N/A             |
| Amazon ALB (C-1,Optional)                     | 1 ALB, 1TB Processed                                                | $24.43            | $52.56          |
| Amazon API Gateway                            | 150000 requests                                                     | $0.16             | $0.19           |
| Amazon DynamoDB                               | 750000 writes, 146250 reads, 0.30 GB storage                        | $1.18             | $2.36           |
| AWS Lambda                                    | 12000 invocations, 2-minute avg. duration, 256 MB memory            | $6                | $6              |
| AWS Step Functions                            | 92400 state transitions                                             | $2.21             | $2.65           |
| Amazon S3                                     | 10 GB storage, 4000 PUT requests, 4000 GET requests                 | $0.26             | $0.41           |
| Amazon Rekognition                            | 9000 Image analysis, 3 Custom Label inference units                 | $22.32            | N/A             |
| Amazon Elastic Container Registry             | ECR (In region) 40GB                                                | $4                | $4              |
| Amazon Open Search Serverless (C-2,Default)   | 2x Index OCU, 2x Search OCU, 100GB Data                             | $703.20           | N/A             |
| Amazon Open Search Provisioned (C-2,Optional) | 3x Data (r6g.large.search), 3x Master (r6g.large.search), 240GB EBS | $743.66           | $915.52         |
| Amazon Location Service (C-4,Default)         | 1000 Map tiles Retrieved                                            | $40.00            | N/A             |

Below are the additional costs for including use-case specific pipeline features in your deployment (C-5, Optional):

| Service            | Quantity                                     | Cost (Commercial) | Cost (GovCloud) |
| :----------------- | :------------------------------------------- | :---------------- | :-------------- |
| Batch Fargate      | 10 hours of processing                       | $3.56             | $4.88           |
| Amazon S3          | 300 GB storage, 30GB transfer out            | $9.60             | $16.34          |
| Amazon Cloudwatch  | 1GB logs - VPC Flowlogs/API Gateway/Pipeline | $3.28             | $4.12           |
| Amazon Bedrock     | 1M Tokens - Claude Sonnet                    | $18               | $NA             |
| Amazon Rekognition | 10k Image Processing                         | $7.50             | $9              |
