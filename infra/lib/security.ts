/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";

import { Construct, IConstruct } from "constructs";
import { NagSuppressions, RegexAppliesTo } from "cdk-nag";


    /*

    from https://aws.amazon.com/premiumsupport/knowledge-center/s3-bucket-policy-for-config-rule/

    {
      "Id": "ExamplePolicy",
      "Version": "2012-10-17",
      "Statement": [
        {
          "Sid": "AllowSSLRequestsOnly",
          "Action": "s3:*",
          "Effect": "Deny",
          "Resource": [
            "arn:aws:s3:::DOC-EXAMPLE-BUCKET",
            "arn:aws:s3:::DOC-EXAMPLE-BUCKET/*"
          ],
          "Condition": {
            "Bool": {
              "aws:SecureTransport": "false"
            }
          },
          "Principal": "*"
        }
      ]
    }

    */
export function requireTLSAddToResourcePolicy(bucket: s3.Bucket) {
    bucket.addToResourcePolicy(new iam.PolicyStatement({
        effect: iam.Effect.DENY,
        principals: [new iam.AnyPrincipal()],
        actions: ['s3:*'],
        resources: [`${bucket.bucketArn}/*`, bucket.bucketArn],
        conditions: {
            Bool: { "aws:SecureTransport": "false" }
        },
    }));
}

export function suppressCdkNagErrorsByGrantReadWrite(scope: Construct ) {
    const reason = "This lambda owns the data in this bucket and should have full access to control its assets."
    NagSuppressions.addResourceSuppressions(
        scope,
        [
            {
                id: 'AwsSolutions-IAM5',
                reason: reason,
                appliesTo: [
                  {
                    regex: "/Action::s3:.*/g"
                  }
                ],
            },
            {
              id: "AwsSolutions-IAM5",
              reason: reason,
              appliesTo: [ {
                // https://github.com/cdklabs/cdk-nag#suppressing-a-rule
                regex: "/^Resource::.*/g"
              } ],
            }
        ],
        true
    );

}