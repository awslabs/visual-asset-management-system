# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from aws_lambda_powertools.utilities.parser import BaseModel


class SecureConfigResponseModel(BaseModel, extra='ignore'):
    """Response model for the runtime secure configuration"""
    featuresEnabled: str = ""
    locationServiceApiUrl: str = ""
    webDeployedUrl: str = ""
