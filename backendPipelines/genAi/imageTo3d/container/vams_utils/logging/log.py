# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import logging

logging.basicConfig(level=logging.INFO)
__logger = logging.getLogger()


def get_logger():
    return __logger


def set_log_level(level=logging.INFO):
    __logger.setLevel(level)
