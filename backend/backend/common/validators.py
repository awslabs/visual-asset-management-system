#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import re
import json
from datetime import datetime

from common.s3PathPatterns import PIPELINES_PREFIX, PIPELINE_OUTPUT_PREFIX

#Define patterns as global constants
id_pattern = r'^[-_a-zA-Z0-9]{3,63}$'
uuid_pattern = r'^[0-9a-fA-F]{8}\b\-[0-9a-fA-F]{4}\b\-[0-9a-fA-F]{4}\b\-[0-9a-fA-F]{4}\b\-[0-9a-fA-F]{12}$'
# Execution identifiers, in either shape a stored execution id can carry: the undashed 32-hex form
# produced by common.workflows.executionRecords.new_guid() (uuid.uuid4().hex), and the dashed
# 8-4-4-4-12 uuid Step Functions generates as the execution name when StartExecution is called
# without one, which is the id an execution row keeps for its whole life. Covers
# workflow-execution, pipeline-execution, and execution-group ids. The undashed alternative is
# lowercase only, because .hex emits lowercase and these values are compared as exact DynamoDB key
# values, where an uppercase variant would simply match nothing.
execution_id_pattern = (
    r'^(?:[0-9a-f]{32}'
    r'|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$')

sagemaker_notebook_name_pattern = '^[a-zA-Z0-9](-*[a-zA-Z0-9])*'
email_pattern = r'^[\w\-\.\+]+@([\w-]+\.)+[\w-]{2,4}$'

file_type_pattern = '^[\\.]([a-zA-Z0-9]){1,7}$'
filename_pattern = r'^(?!.*[<>:"\/\\|?*])(?!.*[.\s]$)[\w\s.,\'-]{1,254}[^.\s]$'

relative_file_path_pattern = r'^\/.*$'
bucket_existing_key_pattern = r'^[a-zA-Z0-9._\-/]{1,1024}$'
# S3 bucket name: 3-63 chars, lowercase letters/digits/hyphens/dots, must start
# and end with a letter or digit.
s3_bucket_name_pattern = r'^[a-z0-9][a-z0-9\.\-]{1,61}[a-z0-9]$'
asset_path_pattern = r'^.+\/.+$'
asset_folder_path_pattern = r'^.+\/.+\/$'
asset_auxiliarypreview_path_pattern = r'^.+\/preview\/.+$'
asset_path_pipeline_pattern = r'^pipelines\/.+\/.+\/output\/.+\/$'

object_name_pattern = r'^[a-zA-Z0-9\-._\s]{1,256}$'
userid_pattern = r'^[\w\-\.\+\@]{3,256}$'

# UTC timestamp in the canonical form VAMS stores execution dates in ('%Y-%m-%dT%H:%M:%SZ').
# Execution listings compare a caller-supplied bound against these values as a DynamoDB sort key,
# which is a lexicographic string compare — so a value in any other shape silently widens or empties
# the window rather than failing. Fractional seconds and a '+00:00' offset in place of 'Z' are
# accepted for tolerance, but they do NOT sort as equal to the stored form ('.' and '+' both order
# before 'Z'), so a caller supplying one must normalize before using it as a key bound.
iso8601_utc_pattern = r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|\+00:00)$'

# AWS resource patterns - partition-aware. Must accept every partition the CDK layer can deploy into,
# which is the authoritative list in infra/lib/helper/const.ts (SERVICE_LOOKUP): aws, aws-us-gov,
# aws-cn, aws-iso, aws-iso-b, aws-iso-e, aws-iso-f, aws-eusc. A partition missing here rejects a
# well-formed ARN that the deployment itself produced, so a pipeline registering its own sub-process
# fails validation in that partition only — invisible in a commercial test.
# `aws-eusc` (EU Sovereign Cloud) is spelled out because it does NOT fit the -iso family shape.
aws_partition_group = r'aws(?:-us-gov|-cn|-eusc|-iso(?:-[a-z])?)?'
# The DNS suffixes those partitions serve regional endpoints from, for URL-shaped values. Partitions
# do not share one suffix: commercial/GovCloud use amazonaws.com, China amazonaws.com.cn, EU Sovereign
# amazonaws.eu, and the ISO partitions use their own non-amazonaws domains.
aws_dns_suffix_group = (r'(?:amazonaws\.com(?:\.cn)?|amazonaws\.eu|c2s\.ic\.gov|sc2s\.sgov\.gov'
                        r'|cloud\.adc-e\.uk|csp\.hci\.ic\.gov)')
# SQS Queue URL: https://sqs[-fips].{region}.{dns-suffix}/{account}/{queue-name}
# Also supports VPC endpoint URLs: https://vpce-xxx.sqs.{region}.vpce.{dns-suffix}/{account}/{queue-name}
sqs_queue_url_pattern = (r'^https://(vpce-[a-z0-9\-]+\.)?sqs[\-a-z]*\.[a-z0-9\-]+\.(vpce\.)?'
                         + aws_dns_suffix_group + r'/[0-9]{12}/[a-zA-Z0-9_\-\.]+$')
# EventBridge Bus ARN: arn:{partition}:events:{region}:{account}:event-bus/{bus-name}
eventbridge_bus_arn_pattern = r'^arn:(' + aws_partition_group + r'):events:[a-z0-9\-]+:[0-9]{12}:event-bus/[a-zA-Z0-9_\-\./]+$'
# EventBridge source: reverse-DNS style, 1-256 chars, no aws. prefix (reserved)
eventbridge_source_pattern = r'^(?!aws\.)[a-zA-Z0-9\-\.\_]{1,256}$'
# EventBridge detail type: free-form string, 1-256 chars
eventbridge_detail_type_pattern = r'^.{1,256}$'
# Generic AWS ARN (partition-aware): arn:{partition}:{service}:{region}:{account}:{resource}.
# region and account may be empty (e.g. IAM/S3 ARNs); resource is required and may contain
# ':' or '/' separators. Bounded to keep a malformed value from being stored. ~1-2048 chars.
arn_pattern = (r'^arn:(' + aws_partition_group +
               r'):[a-z0-9\-]{1,63}:[a-z0-9\-]*:[0-9]{0,12}:[a-zA-Z0-9\-\._:/]{1,1700}$')
# CloudWatch Logs log-group ARN: arn:{partition}:logs:{region}:{account}:log-group:{name}
# optionally followed by ':*' or a ':log-stream:{stream}' suffix.
cloudwatch_log_group_arn_pattern = (r'^arn:(' + aws_partition_group +
                                     r'):logs:[a-z0-9\-]+:[0-9]{12}:log-group:[a-zA-Z0-9\-\._/#]{1,512}'
                                     r'(:\*)?(:log-stream:[^:*]{1,512})?$')
# CloudWatch log group name: 1-512 chars of [.-_/#A-Za-z0-9].
cloudwatch_log_group_name_pattern = r'^[a-zA-Z0-9\-\._/#]{1,512}$'
# CloudWatch log stream name / prefix: 1-512 chars; ':' and '*' are not allowed by CloudWatch.
log_stream_name_pattern = r'^[^:*]{1,512}$'

#Define local regexes that use the patterns
id_regex = re.compile(id_pattern)
uuid_regex = re.compile(uuid_pattern)
execution_id_regex = re.compile(execution_id_pattern)


sagemaker_notebook_name_regex = re.compile(sagemaker_notebook_name_pattern)
email_regex = re.compile(email_pattern)

file_type_regex = re.compile(file_type_pattern)
filename_regex = re.compile(filename_pattern)
asset_id_regex = re.compile(filename_pattern)

relative_file_path_regex = re.compile(relative_file_path_pattern)
asset_path_regex = re.compile(asset_path_pattern)
asset_folder_path_regex = re.compile(asset_folder_path_pattern)
asset_auxiliarypreview_path_regex = re.compile(asset_auxiliarypreview_path_pattern)
asset_path_pipeline_regex = re.compile(asset_path_pipeline_pattern)
object_name_regex = re.compile(object_name_pattern)
userid_regex = re.compile(userid_pattern)
s3_bucket_name_regex = re.compile(s3_bucket_name_pattern)

sqs_queue_url_regex = re.compile(sqs_queue_url_pattern)
eventbridge_bus_arn_regex = re.compile(eventbridge_bus_arn_pattern)
eventbridge_source_regex = re.compile(eventbridge_source_pattern)
eventbridge_detail_type_regex = re.compile(eventbridge_detail_type_pattern, re.DOTALL)
arn_regex = re.compile(arn_pattern)
cloudwatch_log_group_arn_regex = re.compile(cloudwatch_log_group_arn_pattern)
cloudwatch_log_group_name_regex = re.compile(cloudwatch_log_group_name_pattern)
log_stream_name_regex = re.compile(log_stream_name_pattern)


def validate_id(name, value):
    if not id_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+id_pattern)
    return (True, '')

def validate_asset_id(name, value):
    if len(value) > 256: #Currently at 256 but S3 can handle up to 1024 characters per object
        return (False, name + " exceeds maximum length of 256 characters")
    if not asset_id_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+filename_pattern)
    return (True, '')

def validate_uuid(name, value):
    if not uuid_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+uuid_pattern)
    return (True, '')

def validate_guid(name, value):
    if not execution_id_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+execution_id_pattern)
    return (True, '')

def validate_relative_file_path(name, value):
    if not relative_file_path_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+relative_file_path_pattern)
    elif value.count('..') > 0:
        return (False, name + " is invalid. Cannot contain more than one '.' in sequence.")
    elif len(value) < 3:
        return (False, name + " is invalid. Must be at least 3 characters long.")
    return (True, '')

def validate_relative_file_path_array(name, values):
    if not isinstance(values, list):
        return (False, name + " must be an array of relative file paths")
    for value in values:
        (valid, message) = validate_relative_file_path(name, value)
        if not valid:
            return (valid, message)
    return (True, '')

def validate_download_key_array(name, values):
    """Validate bulk download file keys.

    Accepts both asset-relative keys (leading '/', e.g. '/dir/file.txt') and
    full asset-prefixed keys (e.g. 'assetId/dir/file.txt'), matching the forms
    the single-file download key accepts. Rejects empty keys, non-strings, and
    '..' path traversal.
    """
    if not isinstance(values, list):
        return (False, name + " must be an array of file keys")
    for value in values:
        if not isinstance(value, str) or not value.strip():
            return (False, name + " entries must be non-empty strings")
        if '..' in value:
            return (False, name + " is invalid. Cannot contain '..' path segments.")
    return (True, '')

def validate_asset_path(name, value, isFolder):
    if isFolder and not asset_folder_path_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+asset_folder_path_pattern)
    elif not isFolder and not asset_path_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+asset_path_pattern)
    elif value.count('..') > 0:
        return (False, name + " is invalid. Cannot contain more than one '.' in sequence.")
    elif not isFolder and len(value) < 4:
        return (False, name + " is invalid. Must be at least 4 characters long.")
    elif isFolder and len(value) < 4:
        return (False, name + " is invalid. Must be at least 4 characters long.")
    elif isFolder and '//' in value:
        return (False, name + " is invalid. Cannot contain consecutive forward slashes (//).")
    return (True, '')

def validate_asset_auxiliarypreview_path(name, value):
    if not asset_auxiliarypreview_path_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+asset_auxiliarypreview_path_pattern)
    elif value.count('..') > 0:
        return (False, name + " is invalid. Cannot contain more than one '.' in sequence.")
    elif '//' in value:
        return (False, name + " is invalid. Cannot contain consecutive forward slashes (//).")
    
    # Check for minimum length requirements
    preview_parts = value.split('/preview/', 1)
    if len(preview_parts) != 2:
        return (False, name + " is invalid. Must contain '/preview/' exactly once.")
    
    prefix = preview_parts[0]
    suffix = preview_parts[1]
    
    if len(prefix) < 4:
        return (False, name + " is invalid. Path before '/preview/' must be at least 4 characters long.")
    if len(suffix) < 2:
        return (False, name + " is invalid. Path after '/preview/' must be at least 2 characters long.")
    
    return (True, '')

def validate_asset_path_pipeline(name, value):
    if not asset_path_pipeline_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+asset_path_pipeline_pattern)
    elif value.count('..') > 0:
        return (False, name + " is invalid. Cannot contain more than one '.' in sequence.")
    elif '//' in value:
        return (False, name + " is invalid. Cannot contain consecutive forward slashes (//).")
    
    # Check for the required structure and minimum lengths
    if not value.startswith(PIPELINES_PREFIX):
        return (False, name + f" is invalid. Must start with '{PIPELINES_PREFIX}'.")

    # Split the path into sections
    remaining = value[len(PIPELINES_PREFIX):]
    outputs_parts = remaining.split(PIPELINE_OUTPUT_PREFIX, 1)

    if len(outputs_parts) != 2:
        return (False, name + f" is invalid. Must contain '{PIPELINE_OUTPUT_PREFIX}' exactly once.")

    middle_section = outputs_parts[0]
    end_section = outputs_parts[1]

    # Check middle section has at least one forward slash and is at least 4 characters
    if '/' not in middle_section or len(middle_section) < 4:
        return (False, name + f" is invalid. Section between '{PIPELINES_PREFIX}' and '{PIPELINE_OUTPUT_PREFIX}' must contain at least one forward slash and be at least 4 characters long.")
    
    # Check end section is at least 2 characters (not counting the trailing slash)
    if not end_section.endswith('/') or len(end_section.rstrip('/')) < 2:
        return (False, name + f" is invalid. Section after '{PIPELINE_OUTPUT_PREFIX}' must be at least 2 characters long and end with a forward slash.")
    
    return (True, '')

def validate_filename(name, value):
    if not filename_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+filename_pattern)
    return (True, '')

def validate_sagemaker_notebook_id(name, value):
    if not sagemaker_notebook_name_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+sagemaker_notebook_name_pattern)
    return (True, '')

def validate_id_array(name, values):
    if not isinstance(values, list):
        return (False, name + " must be an array.")
    for val in values:
        (valid, message) = validate_id(name, val)
        if not valid:
            return (valid, message)
    return (True, '')

def validate_uuid_array(name, values):
    if not isinstance(values, list):
        return (False, name + " must be an array.")
    for val in values:
        (valid, message) = validate_uuid(name, val)
        if not valid:
            return (valid, message)
    return (True, '')

def validate_objectName(name, value):
    if not object_name_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+object_name_pattern)
    return (True, '')

def validate_objectName_array(name, values):
    if not isinstance(values, list):
        return (False, name + " must be an array.")
    for val in values:
        (valid, message) = validate_objectName(name, val)
        if not valid:
            return (valid, message)
    return (True, '')

def validate_email_array(name, values):
    if not isinstance(values, list):
        return (False, name + " must be an array.")
    for val in values:
        (valid, message) = validate_email(name, val)
        if not valid:
            return (valid, message)
    return (True, '')

def validate_userid_array(name, values):
    if not isinstance(values, list):
        return (False, name + " must be an array.")
    for val in values:
        (valid, message) = validate_userid(name, val)
        if not valid:
            return (valid, message)
    return (True, '')

def validate_string_max_length(name, value, max_length):
    if len(value) > max_length:
        return (False, name + " must be lower than " + str(max_length) + " characters")
    return (True, '')

def validate_string_max_length_30(name, value):
    return validate_string_max_length(name, value, 30)

def validate_string_json(name, value):
    try:
        json.loads(value)
        return (True, '')
    except ValueError:
        return (False, name + " is invalid. Must be a valid json string.")

def validate_string_max_length_array(name, values, max_length):
    if not isinstance(values, list):
        return (False, name + " must be an array.")
    for val in values:
        (valid, message) = validate_string_max_length(name, val, max_length)
        if not valid:
            return (valid, message)
    return (True, '')

def validate_string_fileType(name, value):
    if not file_type_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+file_type_pattern)
    return (True, '')

def validate_email(name, value):
    # fullmatch, not match: '$' also matches just before a trailing newline, so
    # re.match would accept "user@example.com\n" and let a newline reach a stored
    # value or a log line.
    if not email_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+email_pattern)
    return (True, '')

def validate_userid(name, value):
    if not userid_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+userid_pattern)
    return (True, '')

def validate_regex(name, value):
    try:
        re.compile(value)
        return (True, '')
    except re.error:
        return (False, name + " is invalid. Must be a properly formatted regex expression.")
    
def validate_number(name, value):
    try:
        float(value)
        return (True, '')
    except ValueError:
        return (False, name + " is invalid. Must be a number.")
    
def validate_bool(name, value):
    # bool(str(value)) is truthy for any non-empty string, so it never rejects — check
    # against an explicit allow-list of boolean literals instead.
    if isinstance(value, bool):
        return (True, '')
    if isinstance(value, str) and value.strip().lower() in ('true', 'false'):
        return (True, '')
    return (False, name + " is invalid. Must be a boolean string of 'true'/'false'.")


def validate_iso8601_utc(name, value):
    # Rejects a shape mismatch and a syntactically well-formed but impossible date alike
    # (e.g. month 13), since both reach a listing's sort-key comparison as an ordinary string.
    if not isinstance(value, str) or not re.fullmatch(iso8601_utc_pattern, value):
        return (False, name + " is invalid. Must be a UTC timestamp of the form"
                              " YYYY-MM-DDTHH:MM:SSZ.")
    try:
        datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return (False, name + " is invalid. Must be a UTC timestamp of the form"
                              " YYYY-MM-DDTHH:MM:SSZ.")
    return (True, '')


def normalize_iso8601_utc(value):
    """Reduce a validated UTC timestamp to the canonical stored form ('YYYY-MM-DDTHH:MM:SSZ').

    A listing bound is compared against stored dates as a DynamoDB sort key, which is a plain
    lexicographic compare: '.500Z' and '+00:00' both order BEFORE 'Z', so an un-normalized bound
    shifts the window by up to a second. Assumes the value already passed validate_iso8601_utc."""
    return value[:19] + "Z"


def validate_sqs_queue_url(name, value):
    if not sqs_queue_url_regex.fullmatch(value):
        return (False, name + " is invalid. Must be a valid SQS queue URL (e.g., https://sqs.us-east-1.amazonaws.com/123456789012/my-queue). Supports all AWS partitions including GovCloud, China, EU Sovereign Cloud, and ISO regions.")
    return (True, '')

def validate_eventbridge_bus_arn(name, value):
    if not eventbridge_bus_arn_regex.fullmatch(value):
        return (False, name + " is invalid. Must be a valid EventBridge bus ARN (e.g., arn:aws:events:us-east-1:123456789012:event-bus/my-bus). Supports all AWS partitions including GovCloud (arn:aws-us-gov), China (arn:aws-cn), EU Sovereign Cloud (arn:aws-eusc), and ISO partitions.")
    return (True, '')

def validate_eventbridge_source(name, value):
    if not eventbridge_source_regex.fullmatch(value):
        return (False, name + " is invalid. Must be 1-256 characters, alphanumeric with dots/hyphens/underscores. Cannot start with 'aws.' (reserved prefix).")
    return (True, '')

def validate_eventbridge_detail_type(name, value):
    if not eventbridge_detail_type_regex.fullmatch(value):
        return (False, name + " is invalid. Must be 1-256 characters.")
    return (True, '')

def validate_arn(name, value):
    if not arn_regex.fullmatch(value):
        return (False, name + " is invalid. Must be a valid AWS ARN (e.g., arn:aws:states:us-east-1:123456789012:execution:sm:exec). Supports all AWS partitions including GovCloud (arn:aws-us-gov), China (arn:aws-cn), EU Sovereign Cloud (arn:aws-eusc), and ISO partitions.")
    return (True, '')

def validate_cloudwatch_log_group_arn(name, value):
    if not cloudwatch_log_group_arn_regex.fullmatch(value):
        return (False, name + " is invalid. Must be a valid CloudWatch Logs log-group ARN (e.g., arn:aws:logs:us-east-1:123456789012:log-group:/aws/my-group). Supports all AWS partitions.")
    return (True, '')

def validate_cloudwatch_log_group_name(name, value):
    if not cloudwatch_log_group_name_regex.fullmatch(value):
        return (False, name + " is invalid. Must be a valid CloudWatch log group name (1-512 characters: letters, digits, and -_./#).")
    return (True, '')

def validate_log_stream_name(name, value):
    if not log_stream_name_regex.fullmatch(value):
        return (False, name + " is invalid. Must be 1-512 characters and may not contain ':' or '*'.")
    return (True, '')

def validate_s3_bucket_name(name, value):
    if not s3_bucket_name_regex.fullmatch(value):
        return (False, name + " is invalid. Must be a valid S3 bucket name (3-63 lowercase letters, digits, hyphens or dots, starting and ending with a letter or digit).")
    if '..' in value:
        return (False, name + " is invalid. Cannot contain consecutive dots.")
    return (True, '')


def validate(values):
    for k, v in values.items():

        optional = False
        if 'optional' in v:
            if isinstance(v['optional'], bool) and v['optional'] == True:
                optional = True
            if not isinstance(v['optional'], bool):
                raise Exception("The optional field in validator for " + k + " field must be of type bool")
            
        allowGlobalKeyword = False
        if 'allowGlobalKeyword' in v:
            if isinstance(v['allowGlobalKeyword'], bool) and v['allowGlobalKeyword'] == True:
                allowGlobalKeyword = True
            if not isinstance(v['allowGlobalKeyword'], bool):
                raise Exception("The allowGlobalKeyword field in validator for " + k + " field must be of type bool")

        #Empty checks across types. If optional, skip THIS field and keep validating the
        #rest (use `continue`, not `return` — a `return` here would report the whole
        #request valid and silently skip every field ordered after an empty optional one).
        #Otherwise error on empty.
        if v['value'] is None:
            if optional:
                continue
            else:
                return (False, k + " is a required field.")
        if not "_ARRAY" in v['validator'] and isinstance(v['value'], str) and v['value'] == '':
            if optional:
                continue
            else:
                return (False, k + " is a required field.")
        if "_ARRAY" in v['validator'] and isinstance(v['value'], (list)) and len(v['value']) == 0:
            if optional:
                continue
            else:
                return (False, k + " is a required field.")
            
        #Check and allow for global keyword (initially case insensitive). Accepting the keyword
        #skips THIS field's type check only (`continue`, not `return` — a `return` here would
        #report the whole request valid and silently skip every field ordered after it).
        if isinstance(v['value'], str):
            if allowGlobalKeyword and v['value'].lower().strip() == 'global':
                #additional check to make sure final value is capitalized or not
                if v['value'] == 'GLOBAL':
                    continue
                else:
                    return (False, k + " is invalid. GLOBAL must be capitalized for this field is used.")
            elif not allowGlobalKeyword and v['value'].lower().strip()  == 'global':
                return (False, k + " is invalid. GLOBAL is not allowed for this field.")
            
        #Check input types first. If not string or array for respective validator, error.
        if isinstance(v['value'], dict):
            return (False, k + " is invalid. Must be a string or an array of strings for validator, not a dict.")
        elif "_ARRAY" in v['validator'] and not isinstance(v['value'], list):
            return (False, k + " is invalid. Must be a list for array validators, not a " + str(type(v['value'])))
        elif not "_ARRAY" in v['validator'] and not isinstance(v['value'], str):
            return (False, k + " is invalid. Must be a string for non-array validators, not a " + str(type(v['value'])))

        #Type checks after we check for empties.
        if v['validator'] == 'ID':
            (valid, message) = validate_id(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'ASSET_ID':
            (valid, message) = validate_asset_id(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'UUID':
            (valid, message) = validate_uuid(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'GUID':
            (valid, message) = validate_guid(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'SAGEMAKER_NOTEBOOK_ID':
            (valid, message) = validate_sagemaker_notebook_id(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'ID_ARRAY':
            (valid, message) = validate_id_array(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'UUID_ARRAY':
            (valid, message) = validate_uuid_array(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'EMAIL_ARRAY':
            (valid, message) = validate_email_array(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'USERID_ARRAY':
            (valid, message) = validate_userid_array(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'STRING_30':
            (valid, message) = validate_string_max_length_30(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'STRING_256':
            (valid, message) = validate_string_max_length(k, v['value'], 256)
            if not valid:
                return (valid, message)
        if v['validator'] == 'STRING_256_ARRAY':
            (valid, message) = validate_string_max_length_array(k, v['value'], 256)
            if not valid:
                return (valid, message)
        if v['validator'] == 'STRING_JSON':
            (valid, message) = validate_string_json(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'FILE_NAME':
            (valid, message) = validate_filename(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'FILE_EXTENSION':
            (valid, message) = validate_string_fileType(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'RELATIVE_FILE_PATH':
            (valid, message) = validate_relative_file_path(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'RELATIVE_FILE_PATH_ARRAY':
            (valid, message) = validate_relative_file_path_array(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'DOWNLOAD_KEY_ARRAY':
            (valid, message) = validate_download_key_array(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'ASSET_PATH':
            isFolder = False
            if 'isFolder' in v:
                if isinstance(v['isFolder'], bool) and v['isFolder'] == True:
                    isFolder = True
                if not isinstance(v['isFolder'], bool):
                    raise Exception("The isFolder field in validator for " + k + " field must be of type bool")
            (valid, message) = validate_asset_path(k, v['value'], isFolder)
            if not valid:
                return (valid, message)
        if v['validator'] == 'ASSET_PATH_PIPELINE':
            (valid, message) = validate_asset_path_pipeline(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'ASSET_AUXILIARYPREVIEW_PATH':
            (valid, message) = validate_asset_auxiliarypreview_path(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'OBJECT_NAME':
            (valid, message) = validate_objectName(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'OBJECT_NAME_ARRAY':
            (valid, message) = validate_objectName_array(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'EMAIL':
            (valid, message) = validate_email(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'USERID':
            (valid, message) = validate_userid(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'REGEX':
            (valid, message) = validate_regex(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'NUMBER':
            (valid, message) = validate_number(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'BOOL':
            (valid, message) = validate_bool(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'ISO8601_UTC':
            (valid, message) = validate_iso8601_utc(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'SQS_QUEUE_URL':
            (valid, message) = validate_sqs_queue_url(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'EVENTBRIDGE_BUS_ARN':
            (valid, message) = validate_eventbridge_bus_arn(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'EVENTBRIDGE_SOURCE':
            (valid, message) = validate_eventbridge_source(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'EVENTBRIDGE_DETAIL_TYPE':
            (valid, message) = validate_eventbridge_detail_type(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'ARN':
            (valid, message) = validate_arn(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'CLOUDWATCH_LOG_GROUP_ARN':
            (valid, message) = validate_cloudwatch_log_group_arn(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'CLOUDWATCH_LOG_GROUP_NAME':
            (valid, message) = validate_cloudwatch_log_group_name(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'LOG_STREAM_NAME':
            (valid, message) = validate_log_stream_name(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'S3_BUCKET_NAME':
            (valid, message) = validate_s3_bucket_name(k, v['value'])
            if not valid:
                return (valid, message)

    return (True, "")
