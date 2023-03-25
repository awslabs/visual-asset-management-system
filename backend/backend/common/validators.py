#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import re
id_regex = re.compile('^[a-z]([-_a-z0-9]){3,63}$')
file_type_regex = re.compile('^[\\.]([a-z0-9]){1,7}')
sagemaker_notebook_name_regex = re.compile('^[a-zA-Z0-9](-*[a-zA-Z0-9])*')

def validate_id(name, value):
    if not id_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp ^[a-z]([-_a-z0-9]){3,63}$")
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

def validate_string_max_length(name, value, max_length):
    if len(value) > max_length:
        return (False, name + " must be lower than " + str(max_length) + " characters")
    return (True, '')

def validate_string_fileType(name, value):
    if not file_type_regex.fullmatch(value):
        return (False, name + " is invalid. Must follow the regexp ^[\\.]([a-z]){1,7}")
    return (True, '')

def validate(values):
    for k, v in values.items():
        if v['validator'] == 'ID':
            (valid, message) = validate_id(k, v['value'])
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
        if v['validator'] == 'STRING_256':
            (valid, message) = validate_string_max_length(k, v['value'], 256)
            if not valid:
                return (valid, message)

        if v['validator'] == 'FILE_EXTENSION':
            (valid, message) = validate_string_fileType(k, v['value'])
            if not valid:
                return (valid, message)

    return (True, "")