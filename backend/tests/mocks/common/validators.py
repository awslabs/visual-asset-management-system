# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import re
from datetime import datetime

# Pattern constants (match real validators.py exports)
id_pattern = r'^[-_a-zA-Z0-9]{3,63}$'
uuid_pattern = r'^[0-9a-fA-F]{8}\b\-[0-9a-fA-F]{4}\b\-[0-9a-fA-F]{4}\b\-[0-9a-fA-F]{4}\b\-[0-9a-fA-F]{12}$'
object_name_pattern = r'^[a-zA-Z0-9\-._\s]{1,256}$'
filename_pattern = r'^(?!.*[<>:"\/\\|?*])(?!.*[.\s]$)[\w\s.,\'-]{1,254}[^.\s]$'
relative_file_path_pattern = r'^\/.*$'
bucket_existing_key_pattern = r'^[a-zA-Z0-9._\-/]{1,1024}$'
userid_pattern = r'^[\w\-\.\+\@]{3,256}$'
email_pattern = r'^[\w\-\.\+]+@([\w-]+\.)+[\w-]{2,4}$'

s3_bucket_name_pattern = r'^[a-z0-9][a-z0-9\.\-]{1,61}[a-z0-9]$'
iso8601_utc_pattern = r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|\+00:00)$'

id_regex = re.compile(id_pattern)
userid_regex = re.compile(userid_pattern)
object_name_regex = re.compile(object_name_pattern)
email_regex = re.compile(email_pattern)
filename_regex = re.compile(filename_pattern)
s3_bucket_name_regex = re.compile(s3_bucket_name_pattern)

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

        # Empty checks across types. If optional, skip THIS field and keep validating
        # the rest (`continue`, matching the real dispatcher — a `return` here would
        # report the whole request valid and silently skip every field ordered after an
        # empty optional one). Otherwise error on empty.
        if value is None:
            if optional:
                continue
            else:
                return (False, f"{k} is a required field.")
        if isinstance(value, str) and value == '':
            if optional:
                continue
            else:
                return (False, f"{k} is a required field.")
        if isinstance(value, list) and len(value) == 0:
            if optional:
                continue
            else:
                return (False, f"{k} is a required field.")

        # Type-specific validation
        if validator == 'ID':
            allow_global = v.get('allowGlobalKeyword', False)
            if allow_global and value == 'GLOBAL':
                continue
            if not id_regex.fullmatch(value):
                return (False, f"{k} is invalid. Must follow the regexp {id_pattern}")
        elif validator == 'USERID':
            if not userid_regex.fullmatch(value):
                return (False, f"{k} is invalid. Must follow the regexp {userid_pattern}")
        elif validator == 'STRING_256':
            if len(value) > 256:
                return (False, f"{k} must be lower than 256 characters")
        elif validator == 'ASSET_ID':
            # Mirror the real ASSET_ID rule: the filename pattern, max 256 chars.
            # A loose length-only check here would let a traversal asset id pass
            # in tests while the real validator rejects it.
            if not value or len(value) > 256:
                return (False, f"{k} is invalid asset id")
            if not filename_regex.fullmatch(value):
                return (False, f"{k} is invalid. Must follow the regexp {filename_pattern}")
        elif validator == 'S3_BUCKET_NAME':
            if not s3_bucket_name_regex.fullmatch(value):
                return (False, f"{k} is invalid. Must be a valid S3 bucket name")
            if '..' in value:
                return (False, f"{k} is invalid. Cannot contain consecutive dots.")
        elif validator == 'DOWNLOAD_KEY_ARRAY':
            # Mirror the real validator: accepts both asset-relative ('/dir/f')
            # and asset-prefixed ('assetId/dir/f') keys; rejects non-strings,
            # empties, and '..' traversal.
            if not isinstance(value, list):
                return (False, f"{k} must be an array of file keys")
            for entry in value:
                if not isinstance(entry, str) or not entry.strip():
                    return (False, f"{k} entries must be non-empty strings")
                if '..' in entry:
                    return (False, f"{k} is invalid. Cannot contain '..' path segments.")
        elif validator == 'STRING_256_ARRAY':
            if not isinstance(value, list):
                return (False, f"{k} must be an array.")
            for entry in value:
                if len(entry) > 256:
                    return (False, f"{k} must be lower than 256 characters")
        elif validator == 'RELATIVE_FILE_PATH':
            # Mirror real validator: must start with "/", no "..", min length 3.
            if not re.fullmatch(relative_file_path_pattern, value):
                return (False, f"{k} is invalid. Must follow the regexp {relative_file_path_pattern}")
            if value.count('..') > 0:
                return (False, f"{k} cannot contain '..'")
            if len(value) < 3:
                return (False, f"{k} must be at least 3 characters long")
        elif validator == 'ARN':
            # Mirror real ARN validator: 6 colon-delimited segments, "arn" prefix, non-empty service.
            segs = value.split(':')
            if len(segs) < 6 or segs[0] != 'arn' or not segs[2]:
                return (False, f"{k} is not a valid ARN")
        elif validator == 'EMAIL':
            # fullmatch, as in the real validator, so a trailing newline is rejected.
            if not email_regex.fullmatch(value):
                return (False, f"{k} is invalid. Must follow the regexp {email_pattern}")
        elif validator == 'OBJECT_NAME':
            allow_global = v.get('allowGlobalKeyword', False)
            if allow_global and value == 'GLOBAL':
                continue
            if not object_name_regex.fullmatch(value):
                return (False, f"{k} is invalid. Must follow the regexp {object_name_pattern}")
        elif validator == 'BOOL':
            # Mirror the real BOOL rule: an explicit allow-list of boolean literals.
            # A permissive check here would let a non-boolean flag pass in tests
            # while the real validator rejects it.
            if isinstance(value, bool):
                continue
            if not (isinstance(value, str) and value.strip().lower() in ('true', 'false')):
                return (False, f"{k} is invalid. Must be a boolean string of 'true'/'false'.")
        elif validator == 'ISO8601_UTC':
            # Mirror the real rule exactly, including the impossible-date check: a listing bound
            # reaches a DynamoDB sort key as a plain string, so a lenient mock here would hide a
            # widened/emptied query window that the real validator rejects.
            if not isinstance(value, str) or not re.fullmatch(iso8601_utc_pattern, value):
                return (False, f"{k} is invalid. Must be a UTC timestamp of the form"
                               f" YYYY-MM-DDTHH:MM:SSZ.")
            try:
                datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                return (False, f"{k} is invalid. Must be a UTC timestamp of the form"
                               f" YYYY-MM-DDTHH:MM:SSZ.")
        elif validator == 'REGEX':
            allow_global = v.get('allowGlobalKeyword', False)
            if allow_global and value == 'GLOBAL':
                continue
            try:
                re.compile(value)
            except re.error:
                return (False, f"{k} is invalid. Must be a properly formatted regex expression.")

    return (True, "")


def normalize_iso8601_utc(value):
    """Reduce a validated UTC timestamp to the canonical stored form (mirrors the real helper)."""
    return value[:19] + "Z"
