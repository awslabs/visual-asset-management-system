#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import re
id_regex = re.compile('^[a-z]([-_a-z0-9]){3,63}$')
uuid_regex = re.compile(r'^[0-9a-fA-F]{8}\b\-[0-9a-fA-F]{4}\b\-[0-9a-fA-F]{4}\b\-[0-9a-fA-F]{4}\b\-[0-9a-fA-F]{12}$')
file_type_regex = re.compile('^[\\.]([a-z0-9]){1,7}$')
filename_pattern = re.compile(r'^[a-zA-Z0-9_\-.\s]+$')
sagemaker_notebook_name_regex = re.compile('^[a-zA-Z0-9](-*[a-zA-Z0-9])*')
email_pattern = re.compile(r'^[\w\-\.\+]+@([\w-]+\.)+[\w-]{2,4}$')
asset_path_pattern = re.compile(r'^[a-z]([-_a-z0-9]){3,63}(\/[a-zA-Z0-9_\-.\s]+){1,63}$')
object_name_pattern = re.compile(r'^[a-zA-Z0-9\-._\s]{1,256}$')

def validate_id(name, value):
    if not id_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp ^[a-z]([-_a-z0-9]){3,63}$")
    return (True, '')

def validate_uuid(name, value):
    if not uuid_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp ^[0-9a-fA-F]{8}\b\-[0-9a-fA-F]{4}\b\-[0-9a-fA-F]{4}\b\-[0-9a-fA-F]{4}\b\-[0-9a-fA-F]{12}$")
    return (True, '')

def validate_asset_path(name, value):
    if not asset_path_pattern.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp ^[a-z]([-_a-z0-9]){3,63}(\/[a-zA-Z0-9_\-.\s]+){1,63}+$")
    elif value.count('..') > 0:
            return (False, name + " is invalid. Cannot contain more than one '.' in sequence.")
    return (True, '')

def validate_filename(name, value):
    if not filename_pattern.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp ^[a-zA-Z0-9_\-.\s]+$")
    return (True, '')

def validate_sagemaker_notebook_id(name, value):
    if not sagemaker_notebook_name_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp ^[a-zA-Z0-9](-*[a-zA-Z0-9])* ")
    return (True, '')

def validate_id_array(name, values):
    for val in values:
        (valid, message) = validate_id(name, val)
        if not valid:
            return (valid, message)
    return (True, '')

def validate_uuid_array(name, values):
    for val in values:
        (valid, message) = validate_uuid(name, val)
        if not valid:
            return (valid, message)
    return (True, '')

def validate_objectName(name, value):
    if not object_name_pattern.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp ^[a-zA-Z0-9\-._\s]{1,256}$")
    return (True, '')

def validate_objectName_array(name, values):
    for val in values:
        (valid, message) = validate_objectName(name, val)
        if not valid:
            return (valid, message)
    return (True, '')

def validate_email_array(name, values):
    for val in values:
        (valid, message) = validate_email(name, val)
        if not valid:
            return (valid, message)
    return (True, '')

def validate_string_max_length(name, value, max_length):
    if len(value) > max_length:
        return (False, name + " must be lower than " + str(max_length) + " characters")
    return (True, '')

def validate_string_max_length_array(name, values, max_length):
    for val in values:
        (valid, message) = validate_string_max_length(name, val, max_length)
        if not valid:
            return (valid, message)
    return (True, '')

def validate_string_fileType(name, value):
    if not file_type_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp ^[\\.]([a-z0-9]){1,7}$")
    return (True, '')

def validate_email(name, value):
    if not bool(re.match(email_pattern, value)):
        return (False, name + " is invalid. Must follow the regexp ^[\w\-\.\+]+@([\w-]+\.)+[\w-]{2,4}$")
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
        if isinstance(v['value'], str) and v['value'] == '':
            if optional:
                return (True, "")
            else:
                return (False, k + " is a required field.")
        if isinstance(v['value'], (list, dict)) and len(v['value']) == 0:
            if optional:
                return (True, "")
            else:
                return (False, k + " is a required field.")

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
        if v['validator'] == 'STRING_256':
            (valid, message) = validate_string_max_length(k, v['value'], 256)
            if not valid:
                return (valid, message)
        if v['validator'] == 'STRING_256_ARRAY':
            (valid, message) = validate_string_max_length_array(k, v['value'], 256)
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
        if v['validator'] == 'ASSET_PATH':
            (valid, message) = validate_asset_path(k, v['value'])
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
        if v['validator'] == 'REGEX':
            (valid, message) = validate_regex(k, v['value'])
            if not valid:
                return (valid, message)
        if v['validator'] == 'NUMBER':
            (valid, message) = validate_number(k, v['value'])
            if not valid:
                return (valid, message)

    return (True, "")