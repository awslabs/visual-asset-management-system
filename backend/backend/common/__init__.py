# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3


def get_ssm_parameter_value(env_var_name, region, env=os.environ):
    ssm = boto3.client('ssm', region_name=region)
    param = ssm.get_parameter(
        Name=env.get(env_var_name),
        WithDecryption=False
    )
    return param.get("Parameter", {}).get("Value")