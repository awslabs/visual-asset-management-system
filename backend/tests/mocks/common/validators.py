# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import re

# Pattern constants (match real validators.py exports)
id_pattern = r'^[-_a-zA-Z0-9]{3,63}$'
uuid_pattern = r'^[0-9a-fA-F]{8}\b\-[0-9a-fA-F]{4}\b\-[0-9a-fA-F]{4}\b\-[0-9a-fA-F]{4}\b\-[0-9a-fA-F]{12}$'
object_name_pattern = r'^[a-zA-Z0-9\-._\s]{1,256}$'
filename_pattern = r'^(?!.*[<>:"\/\\|?*])(?!.*[.\s]$)[\w\s.,\'-]{1,254}[^.\s]$'
relative_file_path_pattern = r'^\/.*$'
bucket_existing_key_pattern = r'^[a-zA-Z0-9._\-/]{1,1024}$'
userid_pattern = r'^[\w\-\.\+\@]{3,256}$'

id_regex = re.compile(id_pattern)
userid_regex = re.compile(userid_pattern)

def validate(params):
    """
    Mock implementation of the validate function for testing purposes.
    Implements basic validation for ID, USERID, and STRING_256 validators.

    Args:
        params: Dictionary of parameters to validate

    Returns:
        Tuple of (valid, message)
    """
    for k, v in params.items():
        optional = v.get('optional', False)
        value = v['value']
        validator = v['validator']

        # Empty checks across types. If optional, return success. Otherwise error on empty.
        if value is None:
            if optional:
                return (True, "")
            else:
                return (False, f"{k} is a required field.")
        if isinstance(value, str) and value == '':
            if optional:
                return (True, "")
            else:
                return (False, f"{k} is a required field.")

        # Type-specific validation
        if validator == 'ID':
            if not id_regex.fullmatch(value):
                return (False, f"{k} is invalid. Must follow the regexp {id_pattern}")
        elif validator == 'USERID':
            if not userid_regex.fullmatch(value):
                return (False, f"{k} is invalid. Must follow the regexp {userid_pattern}")
        elif validator == 'STRING_256':
            if len(value) > 256:
                return (False, f"{k} must be lower than 256 characters")

    return (True, "")
