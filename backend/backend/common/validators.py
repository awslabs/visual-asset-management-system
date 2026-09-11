#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import math
import re
import json
import unicodedata
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
# A pipeline output path RELATIVE to the VAMS-owned area of the run I/O bucket, which is the form the
# workflow state machine supplies. The bucket's baseAssetsPrefix is joined on by the end-state handler
# AFTER this check (executionRecords.run_bucket_key), so the anchor stays at 'pipelines/': relaxing it
# to admit a leading prefix would let a direct invocation of that handler name an arbitrary folder to
# ingest from.
asset_path_pipeline_pattern = r'^pipelines\/.+\/.+\/output\/.+\/$'

object_name_pattern = r'^[a-zA-Z0-9\-._\s]{1,256}$'
# `\w` is Unicode-aware on a str pattern, so a user id issued by an external IDP in any script is
# accepted. Two spellings of the same name are reconciled by NFKC (normalize_userid), and a
# cross-script lookalike of an existing user id is refused at user creation (confusable_skeleton).
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


# Cross-script characters that are drawn as a Latin character, mapped to the character they read as.
# The mapping is deliberately partial: the Unicode confusables table is a data file this repository
# does not carry, so it covers the Cyrillic and Greek letters that are visually identical to a Latin
# one. A lookalike from another block — mathematical alphanumerics, Cherokee, Armenian — is NOT
# folded, so the skeleton narrows the impersonation surface rather than closing it. Case is
# preserved and the ASCII digit/letter pairs ('0'/'O', '1'/'l') are deliberately left alone: they are
# distinguishable to a reader, and folding them would refuse ordinary user ids that differ only there.
_CONFUSABLE_CHARACTERS = {
    # Cyrillic
    'а': 'a', 'А': 'A', 'е': 'e', 'Е': 'E', 'о': 'o', 'О': 'O',
    'р': 'p', 'Р': 'P', 'с': 'c', 'С': 'C', 'у': 'y', 'У': 'Y',
    'х': 'x', 'Х': 'X', 'і': 'i', 'І': 'I', 'ј': 'j', 'Ј': 'J',
    'ѕ': 's', 'Ѕ': 'S', 'к': 'k', 'К': 'K', 'М': 'M', 'Н': 'H',
    'В': 'B', 'Т': 'T',
    # Greek
    'α': 'a', 'Α': 'A', 'ο': 'o', 'Ο': 'O', 'ρ': 'p', 'Ρ': 'P',
    'ε': 'e', 'Ε': 'E', 'ι': 'i', 'Ι': 'I', 'κ': 'k', 'Κ': 'K',
    'ν': 'v', 'Ν': 'N', 'τ': 't', 'Τ': 'T', 'υ': 'u', 'Υ': 'Y',
    'χ': 'x', 'Χ': 'X', 'Β': 'B', 'Ζ': 'Z', 'Η': 'H', 'Μ': 'M',
}


def trim_name(value):
    """Trim leading and trailing whitespace from a name or id, before it is validated and stored.

    `object_name_pattern` admits `\\s`, so ' Foo ', 'Foo\\n' and 'Foo' would otherwise be three
    distinct records that render identically in the UI, and a grant written against the clean name
    would not cover the padded one. Interior whitespace is deliberately preserved — 'My Asset' is a
    legitimate name — so only the surrounding run is removed. Wire it as a `pre=True` validator so
    the trimmed value is what the length and regex constraints see and what the model returns:

        _trim_names = validator('tagName', 'tagTypeName', pre=True, allow_reuse=True)(trim_name)

    A non-string passes through: the field's own type check reports it."""
    if not isinstance(value, str):
        return value
    return value.strip()


def normalize_userid(value):
    """Reduce a user id to the single spelling VAMS validates, stores and looks up.

    A user id arrives from several issuers (Cognito, an external OAuth IDP, an API key record) and is
    stored verbatim as a DynamoDB key, so two compatibility spellings of the same name — a fullwidth
    'ａ' for 'a', a decomposed accent for a composed one — would otherwise be two identities that
    render identically. NFKC folds them together. Applied before validation and before storage, so
    the value that was checked is the value that is written and the value a later lookup builds.
    A non-string passes through: the caller's own type check reports it."""
    if not isinstance(value, str):
        return value
    return unicodedata.normalize('NFKC', value)


def normalize_userid_array(values):
    """normalize_userid over a list of user ids, for the USERID_ARRAY fields."""
    if not isinstance(values, list):
        return values
    return [normalize_userid(value) for value in values]


def confusable_skeleton(value):
    """A canonical representative of how a user id LOOKS, for comparing two ids at creation.

    NFKC does not fold a Cyrillic or Greek letter onto its Latin lookalike, so 'аdmin' (Cyrillic
    U+0430) stays a distinct key that reads as 'admin' in the constraint editor, the user-roles
    listing and every audit line. Two ids with the same skeleton are indistinguishable to a reader."""
    normalized = normalize_userid(value)
    if not isinstance(normalized, str):
        return normalized
    return ''.join(_CONFUSABLE_CHARACTERS.get(character, character) for character in normalized)


def find_confusable_userid(candidate, existing_userids):
    """The first existing user id that reads the same as candidate, or None.

    An id equal to the candidate is not reported: an exact duplicate is a separate condition the
    caller already handles (Cognito answers it with UsernameExistsException)."""
    skeleton = confusable_skeleton(candidate)
    for existing in existing_userids:
        if not isinstance(existing, str) or existing == candidate:
            continue
        if confusable_skeleton(existing) == skeleton:
            return existing
    return None


# A pattern that compiles is not necessarily safe to evaluate. A REGEX value becomes part of a
# Casbin `regexMatch(...)` clause that re.match re-evaluates for every policy line on every
# authorization decision, with no runtime bound other than the Lambda timeout. Three shapes backtrack
# ruinously against a long qualifying subject and are rejected at write time: a repeating quantifier
# applied to a group that itself repeats or alternates ('(a+)+', '(a|a)*'), a backreference, and more
# quantifier ambiguity than one evaluation can afford ('.*.*.*.*z', 'a*a*a*a*a*b'). Literals,
# character classes, anchors, '.*', '|' and un-quantified groups — every operator the shipped
# permission templates use — are unaffected.
backreference_pattern = r'\\[1-9]|\\g<|\(\?P='
backreference_regex = re.compile(backreference_pattern)

# The longest subject a stored rule is matched against: OBJECT_NAME permits 256 characters, and the
# entity name / category fields the Casbin rule builder compares against are bounded by it.
MAX_REGEX_SUBJECT_LENGTH = 256
# Ceiling on the estimated worst-case backtracking search space of a criterion value, measured rather
# than guessed. Against a 256-character subject, three unbounded quantifiers separated by literals
# ('.*a.*a.*az') estimate 2.8e6 and take 16 ms; a fourth estimates 1.7e8 and takes 0.97 s; a fifth
# does not finish in 25 s. A chain of optionals costs less per quantifier and crosses the line where
# it starts to matter: 23 of them estimate 8.4e6 and take ~80 ms, 24 estimate 1.7e7 and take 0.17 s.
# The clause is re-evaluated per policy line per authorization decision, so the budget is set where
# one evaluation stays in the tens of milliseconds.
MAX_REGEX_SEARCH_SPACE = 10 * 1000 * 1000
# comb() peaks at half the subject length and falls away after it, so the quantifier count is clamped
# there — an even longer chain must not estimate LOWER than the peak.
_MAX_COUNTED_QUANTIFIERS = MAX_REGEX_SUBJECT_LENGTH // 2

_QUANTIFIER_BRACE = re.compile(r'\{(\d*)(,?)(\d*)\}')


def _quantifier_at(pattern, index):
    """(token width, repetition range) for a quantifier starting at `index`, else None.

    The range is how many distinct repetition counts the quantifier admits, or None for an unbounded
    one ('*', '+', '{m,}') whose cost is shared with every other unbounded quantifier over the same
    subject. An exact '{m}' admits one count and so adds no ambiguity. A brace that is not a valid
    quantifier is a literal to `re` and is reported as one here."""
    character = pattern[index]
    if character in ('*', '+'):
        return 1, None
    if character == '?':
        return 1, 2
    if character != '{':
        return None
    match = _QUANTIFIER_BRACE.match(pattern, index)
    if not match:
        return None
    low, comma, high = match.group(1), match.group(2), match.group(3)
    if not low and not high:
        return None
    width = match.end() - index
    if not comma:
        return width, 1
    if not high:
        return width, None
    return width, max(1, int(high) - int(low or 0) + 1)


def _backtracking_search_space(pattern):
    """Estimated worst-case number of ways a subject can be divided among a pattern's quantifiers.

    Unbounded quantifiers share one subject, so k of them contribute comb(subject_length, k) — the
    ways to choose where each hands off to the next. A bounded quantifier contributes its own
    repetition range. Walks the pattern rather than matching it, so an escaped metacharacter, a
    character class, a group-extension marker and a lazy or possessive modifier are not read as
    structure."""
    unbounded = 0
    bounded_product = 1
    ceiling = MAX_REGEX_SEARCH_SPACE + 1
    in_class = False
    i = 0
    while i < len(pattern):
        character = pattern[i]
        if character == '\\':
            i += 2
            continue
        if in_class:
            if character == ']':
                in_class = False
            i += 1
            continue
        if character == '[':
            in_class = True
            i += 1
            continue
        if character == '(' and i + 1 < len(pattern) and pattern[i + 1] == '?':
            i += 2
            continue
        span = _quantifier_at(pattern, i)
        if span is None:
            i += 1
            continue
        width, repetitions = span
        if repetitions is None:
            unbounded += 1
        else:
            bounded_product = min(bounded_product * repetitions, ceiling)
        i += width
        if i < len(pattern) and pattern[i] in ('?', '+'):
            i += 1
        if bounded_product > MAX_REGEX_SEARCH_SPACE:
            return ceiling
    shared = math.comb(MAX_REGEX_SUBJECT_LENGTH, min(unbounded, _MAX_COUNTED_QUANTIFIERS))
    return min(shared * bounded_product, ceiling)


def _repeats_a_repeating_group(pattern):
    """True when a repeating quantifier is applied to a group whose body repeats or alternates.

    Walks the pattern instead of matching it, so an escaped metacharacter and a character class are
    not mistaken for structure. '?' is not treated as repeating: an at-most-once outer quantifier
    cannot produce the exponential blow-up."""
    open_groups = []    # per open group: [body repeats, body alternates]
    just_closed = None  # flags of the group whose ')' was the previous token
    in_class = False
    i = 0
    while i < len(pattern):
        character = pattern[i]
        if character == '\\':
            i += 2
            just_closed = None
            continue
        if in_class:
            if character == ']':
                in_class = False
            i += 1
            continue
        if character == '[':
            in_class = True
            just_closed = None
        elif character in ('*', '+', '{'):
            if just_closed is not None and any(just_closed):
                return True
            for flags in open_groups:
                flags[0] = True
            just_closed = None
        elif character == '(':
            open_groups.append([False, False])
            just_closed = None
            # Skip a group-extension marker so its '?' is not read as a quantifier.
            if i + 1 < len(pattern) and pattern[i + 1] == '?':
                i += 1
        elif character == ')':
            just_closed = open_groups.pop() if open_groups else [False, False]
        elif character == '|':
            for flags in open_groups:
                flags[1] = True
            just_closed = None
        else:
            just_closed = None
        i += 1
    return False


def validate_regex(name, value):
    try:
        re.compile(value)
    except re.error:
        return (False, name + " is invalid. Must be a properly formatted regex expression.")
    if backreference_regex.search(value) or _repeats_a_repeating_group(value):
        return (False, name + " is invalid. Cannot repeat a group that itself repeats or"
                              " alternates, and cannot use a backreference.")
    if _backtracking_search_space(value) > MAX_REGEX_SEARCH_SPACE:
        return (False, name + " is invalid. Too many open-ended quantifiers (*, +, ?, {n,}) to"
                              " evaluate safely; simplify the expression.")
    return (True, '')


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


# Every validator name the dispatcher implements, mapped to the check it applies. This mapping IS
# the set of known names -- there is no second list to keep in step with it. A name absent from it
# has no rule, so validate() raises rather than reporting the field valid having checked nothing,
# which is what a flat chain of `if` comparisons did when a name matched none of them.
#
# Each entry takes the field name and the whole validator spec, not just the value, because a few
# checks read another key off the spec (ASSET_PATH reads isFolder).
_VALIDATOR_DISPATCH = {
    'ID': lambda k, v: validate_id(k, v['value']),
    'ASSET_ID': lambda k, v: validate_asset_id(k, v['value']),
    'UUID': lambda k, v: validate_uuid(k, v['value']),
    'GUID': lambda k, v: validate_guid(k, v['value']),
    'SAGEMAKER_NOTEBOOK_ID': lambda k, v: validate_sagemaker_notebook_id(k, v['value']),
    'ID_ARRAY': lambda k, v: validate_id_array(k, v['value']),
    'UUID_ARRAY': lambda k, v: validate_uuid_array(k, v['value']),
    'EMAIL_ARRAY': lambda k, v: validate_email_array(k, v['value']),
    'USERID_ARRAY': lambda k, v: validate_userid_array(k, v['value']),
    'STRING_16384': lambda k, v: validate_string_max_length(k, v['value'], 16384),
    'STRING_30': lambda k, v: validate_string_max_length_30(k, v['value']),
    'STRING_256': lambda k, v: validate_string_max_length(k, v['value'], 256),
    'STRING_256_ARRAY': lambda k, v: validate_string_max_length_array(k, v['value'], 256),
    'STRING_JSON': lambda k, v: validate_string_json(k, v['value']),
    'FILE_NAME': lambda k, v: validate_filename(k, v['value']),
    'FILE_EXTENSION': lambda k, v: validate_string_fileType(k, v['value']),
    'RELATIVE_FILE_PATH': lambda k, v: validate_relative_file_path(k, v['value']),
    'RELATIVE_FILE_PATH_ARRAY': lambda k, v: validate_relative_file_path_array(k, v['value']),
    'DOWNLOAD_KEY_ARRAY': lambda k, v: validate_download_key_array(k, v['value']),
    'ASSET_PATH_PIPELINE': lambda k, v: validate_asset_path_pipeline(k, v['value']),
    'ASSET_AUXILIARYPREVIEW_PATH': lambda k, v: validate_asset_auxiliarypreview_path(k, v['value']),
    'OBJECT_NAME': lambda k, v: validate_objectName(k, v['value']),
    'OBJECT_NAME_ARRAY': lambda k, v: validate_objectName_array(k, v['value']),
    'EMAIL': lambda k, v: validate_email(k, v['value']),
    'USERID': lambda k, v: validate_userid(k, v['value']),
    'REGEX': lambda k, v: validate_regex(k, v['value']),
    'NUMBER': lambda k, v: validate_number(k, v['value']),
    'BOOL': lambda k, v: validate_bool(k, v['value']),
    'ISO8601_UTC': lambda k, v: validate_iso8601_utc(k, v['value']),
    'SQS_QUEUE_URL': lambda k, v: validate_sqs_queue_url(k, v['value']),
    'EVENTBRIDGE_BUS_ARN': lambda k, v: validate_eventbridge_bus_arn(k, v['value']),
    'EVENTBRIDGE_SOURCE': lambda k, v: validate_eventbridge_source(k, v['value']),
    'EVENTBRIDGE_DETAIL_TYPE': lambda k, v: validate_eventbridge_detail_type(k, v['value']),
    'ARN': lambda k, v: validate_arn(k, v['value']),
    'CLOUDWATCH_LOG_GROUP_ARN': lambda k, v: validate_cloudwatch_log_group_arn(k, v['value']),
    'CLOUDWATCH_LOG_GROUP_NAME': lambda k, v: validate_cloudwatch_log_group_name(k, v['value']),
    'LOG_STREAM_NAME': lambda k, v: validate_log_stream_name(k, v['value']),
    'S3_BUCKET_NAME': lambda k, v: validate_s3_bucket_name(k, v['value']),
    'ASSET_PATH': lambda k, v: validate_asset_path(k, v['value'],
                                                   _spec_bool_flag(v, 'isFolder', k)),
}


def _spec_bool_flag(spec, flag, name):
    """Read a boolean modifier off a validator spec, rejecting a non-bool the way validate() does."""
    if flag not in spec:
        return False
    if not isinstance(spec[flag], bool):
        raise Exception("The " + flag + " field in validator for " + name + " field must be of type bool")
    return spec[flag]


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
        #The name is read defensively here so an absent or non-string one reaches the membership
        #check below with its own message, rather than raising a TypeError out of an "_ARRAY" test.
        validator_name = v.get('validator')
        is_array_validator = isinstance(validator_name, str) and "_ARRAY" in validator_name
        if v['value'] is None:
            if optional:
                continue
            else:
                return (False, k + " is a required field.")
        if not is_array_validator and isinstance(v['value'], str) and v['value'] == '':
            if optional:
                continue
            else:
                return (False, k + " is a required field.")
        if is_array_validator and isinstance(v['value'], (list)) and len(v['value']) == 0:
            if optional:
                continue
            else:
                return (False, k + " is a required field.")

        #Resolve the check now, so an unimplemented name is refused before the type and GLOBAL
        #rules below can report it as invalid caller input instead. The lookup IS the membership
        #test: a name with no entry has no rule. Resolved AFTER the empty checks on purpose:
        #an optional field with nothing in it is skipped before its name is ever consulted, so
        #naming a validator is not required to say "there is nothing here to check".
        check = _VALIDATOR_DISPATCH.get(validator_name) if isinstance(validator_name, str) else None
        if check is None:
            raise Exception("The validator named for the " + k + " field is not a known validator")

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
        #Apply the field's check, resolved above.
        (valid, message) = check(k, v)
        if not valid:
            return (valid, message)

    return (True, "")
