#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import re
import json

#Define patterns as global constants
id_pattern = '^[A-Za-z]([-_A-Za-z0-9]){3,63}$'
uuid_pattern = r'^[0-9a-fA-F]{8}\b\-[0-9a-fA-F]{4}\b\-[0-9a-fA-F]{4}\b\-[0-9a-fA-F]{4}\b\-[0-9a-fA-F]{12}$'
file_type_pattern = '^[\\.]([a-z0-9]){1,7}$'
filename_pattern = r'^[a-zA-Z0-9_\-.\s]+$'
sagemaker_notebook_name_pattern = '^[a-zA-Z0-9](-*[a-zA-Z0-9])*'
email_pattern = r'^[\w\-\.\+]+@([\w-]+\.)+[\w-]{2,4}$'
relative_file_path_pattern = r'^(\/[a-zA-Z0-9_\-.\s]+){1,63}$'
asset_path_pattern = r'^[a-z]([-_a-z0-9]){3,63}(\/[a-zA-Z0-9_\-.\s]+){1,63}$'
asset_folder_path_pattern = r'^[a-z]([-_a-z0-9]){3,63}(\/[a-zA-Z0-9_\-.\s]*){0,63}(\/)$'
asset_auxiliarypreview_path_pattern = r'^[a-z]([-_a-z0-9]){3,63}(\/[a-zA-Z0-9_\-.\s]+){1,63}\/preview(\/[a-zA-Z0-9_\-.\s]+){1,63}(\/?)$'
asset_path_pipeline_pattern = r'^pipelines\/([a-zA-Z0-9_\-.\s]){1,63}\/([a-zA-Z0-9_\-.\s]){1,63}\/output(\/[a-zA-Z0-9_\-.\s]+){1,63}(\/)$'
object_name_pattern = r'^[a-zA-Z0-9\-._\s]{1,256}$'
userid_pattern = r'^[\w\-\.\+\@]{3,256}$'

#Define local regexes that use the patterns
id_regex = re.compile(id_pattern)
uuid_regex = re.compile(uuid_pattern)
file_type_regex = re.compile(file_type_pattern)
filename_regex = re.compile(filename_pattern)
sagemaker_notebook_name_regex = re.compile(sagemaker_notebook_name_pattern)
email_regex = re.compile(email_pattern)
relative_file_path_regex = re.compile(relative_file_path_pattern)
asset_path_regex = re.compile(asset_path_pattern)
asset_folder_path_regex = re.compile(asset_folder_path_pattern)
asset_auxiliarypreview_path_regex = re.compile(asset_auxiliarypreview_path_pattern)
asset_path_pipeline_regex = re.compile(asset_path_pipeline_pattern)
object_name_regex = re.compile(object_name_pattern)
userid_regex = re.compile(userid_pattern)

def validate_id(name, value):
    if not id_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+id_pattern)
    return (True, '')

def validate_uuid(name, value):
    if not uuid_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+uuid_pattern)
    return (True, '')

def validate_relative_file_path(name, value):
    if not relative_file_path_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+relative_file_path_pattern)
    elif value.count('..') > 0:
        return (False, name + " is invalid. Cannot contain more than one '.' in sequence.")
    return (True, '')

def validate_asset_path(name, value, isFolder):
    if isFolder and not asset_folder_path_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+asset_folder_path_pattern)
    elif not isFolder and not asset_path_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+asset_path_pattern)
    elif value.count('..') > 0:
        return (False, name + " is invalid. Cannot contain more than one '.' in sequence.")
    return (True, '')

def validate_asset_auxiliarypreview_path(name, value):
    if not asset_auxiliarypreview_path_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+asset_auxiliarypreview_path_pattern)
    elif value.count('..') > 0:
            return (False, name + " is invalid. Cannot contain more than one '.' in sequence.")
    return (True, '')

def validate_asset_path_pipeline(name, value):
    if not asset_path_pipeline_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp "+asset_path_pipeline_pattern)
    elif value.count('..') > 0:
            return (False, name + " is invalid. Cannot contain more than one '.' in sequence.")
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
    if not bool(re.match(email_regex, value)):
        return (False, name + " is invalid. Must follow the regexp "+email_pattern)
    return (True, '')

def validate_userid(name, value):
    if not bool(re.match(userid_regex, value)):
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
    try:
        bool(value)
        return (True, '')
    except ValueError:
        return (False, name + " is invalid. Must be a boolean string of 'true'/'false'.")


def validate(values):
    for k, v in values.items():

        optional = False
        if 'optional' in v:
            if isinstance(v['optional'], bool) and v['optional'] == True:
                optional = True
            if not isinstance(v['optional'], bool):
                raise Exception("The optional field in validator for " + k + " field must be of type bool")

        #Empty checks across types. If optional, return success. Otherwise error on empty. 
        if v['value'] is None:
            if optional:
                return (True, "")
            else:
                return (False, k + " is a required field.")
        if not "_ARRAY" in v['validator'] and isinstance(v['value'], str) and v['value'] == '':
            if optional:
                return (True, "")
            else:
                return (False, k + " is a required field.")
        if "_ARRAY" in v['validator'] and isinstance(v['value'], (list)) and len(v['value']) == 0:
            if optional:
                return (True, "")
            else:
                return (False, k + " is a required field.")
            
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
        if v['validator'] == 'UUID':
            (valid, message) = validate_uuid(k, v['value'])
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

    return (True, "")