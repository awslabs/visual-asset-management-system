/* eslint-disable react/forbid-foreign-prop-types */
/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

//@todo improve code reuse with decorators or pipe

export const verifyIsBool = (value: any) => {
    if (typeof value === "boolean" || value instanceof Boolean) return true;
    else return false;
};

export const verifyIsString = (value: any) => {
    if (typeof value === "string" || value instanceof String) return true;
    else return false;
};

export const verifyIsNumber = (value: any) => {
    if (typeof value === "number" && !isNaN(value)) return true;
    else return false;
};

export const verifyIsObject = (value: any) => {
    if (typeof value === "object" || value instanceof Object) return true;
    else return false;
};

export const verifyIsNotEmpty = (value: any) => {
    if (value !== "") return true;
    else return false;
};

export const verifyStringMaxLength = (value: any, maxLength: number) => {
    if (value.length <= maxLength) return true;
    else return false;
};

export const verifyJsonParsable = (value: any) => {
    //If JSON.parse does not throw error, return true.
    //Otherwise return false
    try {
        JSON.parse(value);
        return true;
    } catch (e) {
        return false;
    }
};

/*
Starts with . followed by 1 to 7 lowercase letters
 */
export const validateFileType = (value: any) => {
    const regex = /^[\\.]([a-z0-9]){1,7}$/g;
    const result = String(value).match(regex);
    if (result === null) {
        return false;
    }
    return result[0] === String(value);
};

export const validateContainerUri = (value: any) => {
    const regex = /^[0-9]{12}.dkr.(?:ecr|ecr-fips).+?amazonaws.com\/.+/g;
    const result = String(value).match(regex);
    if (result === null) {
        return false;
    }
    return result[0] === String(value);
};

export const fileTypePropType = function (props: any, propName: string) {
    //check if prop is supplied by option element
    let testValue = props[propName];
    if (testValue && testValue.hasOwnProperty("value")) testValue = testValue.value;

    if (testValue === null || !verifyIsNotEmpty(testValue)) {
        return new Error(`Invalid value for ${propName}. Value cannot be empty.`);
    }

    if (!verifyIsString(testValue)) {
        return new Error(`Invalid value for ${propName}. Expected a string.`);
    }

    if (!validateFileType(testValue)) {
        return new Error(`Invalid value for ${propName}. Expected a valid filetype.`);
    }

    return null;
};

/*
All lower case, no special chars or spaces except - and _ only letters for first character min 4 and max 64
 */
export const validateEntityId = (value: any) => {
    // ^[-_a-zA-Z0-9]{3,63}$ means the string should end with 3~63 chars -_a-zA-Z0-9
    const regex = /^[-_a-zA-Z0-9]{3,63}$/g;
    const result = String(value).match(regex);
    if (result === null) {
        return false;
    }
    return result[0] === String(value);
};

export const entityIdPropType = function (props: any, propName: string) {
    //check if prop is supplied by option element
    let testValue = props[propName];
    if (testValue && testValue.hasOwnProperty("value")) testValue = testValue.value;

    if (testValue === undefined) {
        return null;
    }

    if (testValue === null || !verifyIsNotEmpty(testValue)) {
        return new Error(`Invalid value for ${propName}. Value cannot be empty.`);
    }

    if (!verifyIsString(testValue)) {
        return new Error(`Invalid value for ${propName}. Expected a string. Received ${testValue}`);
    }

    if (!validateEntityId(testValue)) {
        return new Error(`Invalid value for ${propName}. Expected a valid entity id.`);
    }

    return null;
};

export const formatEntityId = (s: string) => {
    return s
        .replace(/[\s+-]/g, "-")
        .replace(/[^\w-]/g, "")
        .toLowerCase();
};

export const entityIdArrayPropType = function (props: any, propName: string) {
    const testValues = props[propName];
    if (!Array.isArray(testValues)) {
        return new Error(`Invalid value for ${propName}. Value must be an array.`);
    }
    for (let i = 0; i < testValues.length; i++) {
        let testValue = testValues[i];
        if (testValue && testValue.hasOwnProperty("value")) testValue = testValue.value;

        if (testValue === null || !verifyIsNotEmpty(testValue)) {
            return new Error(`Invalid value for ${propName}. Value cannot be empty.`);
        }

        if (!verifyIsString(testValue)) {
            return new Error(`Invalid value for ${propName}. Expected a string.`);
        }

        if (!validateEntityId(testValue)) {
            return new Error(`Invalid value for ${propName}. Expected a valid entity id.`);
        }
    }

    return null;
};

export const boolPropType = (props: any, propName: string) => {
    //check if prop is supplied by option element
    let testValue = props[propName];
    if (testValue && testValue.hasOwnProperty("value")) testValue = testValue.value;

    if (!verifyIsBool(testValue)) {
        return new Error(`Invalid prop ${propName}. Value must be boolean.`);
    }

    return null;
};

export const objectPropType = (props: any, propName: string) => {
    const testValue = props[propName];

    if (!verifyIsObject(testValue)) {
        return new Error(`Invalid prop ${propName}. Value must be object.`);
    }

    return null;
};

export const stringMaxLength = (maxLength: number, props: any, propName: string) => {
    //check if prop is supplied by option element
    let testValue = props[propName];
    if (testValue && testValue.hasOwnProperty("value")) testValue = testValue.value;

    if (testValue === null || !verifyIsNotEmpty(testValue)) {
        return new Error(`Invalid prop ${propName}. Value cannot be empty.`);
    }

    if (!verifyIsString(testValue)) {
        return new Error(`Invalid prop ${propName}. Expected a string.`);
    }

    if (!verifyStringMaxLength(testValue, maxLength)) {
        return new Error(`Invalid prop ${propName}. Value exceeds maximum length of ${maxLength}.`);
    }

    return null;
};

export const jsonString = (props: any, propName: string) => {
    //check if prop is supplied by option element
    let testValue = props[propName];
    if (testValue && testValue.hasOwnProperty("value")) testValue = testValue.value;

    if (!verifyIsString(testValue)) {
        return new Error(`Invalid prop ${propName}. Expected a string.`);
    }

    if (!verifyJsonParsable(testValue)) {
        return new Error(`Invalid prop ${propName}. Value must be JSON parsable.`);
    }

    return null;
};

export const entityPropType = function (props: any, propName: string) {
    if (!props[propName].propTypes) {
        return new Error(`Invalid prop ${propName}. Not valid entity, no PropTypes.`);
    }
    return null;
};

export const typedObjectPropType = function (type: any, props: any, propName: string, object: any) {
    let testValue;
    if (object) {
        testValue = Object.assign({}, object);
    } else {
        testValue = props[propName];
    }

    if (!verifyIsObject(testValue)) {
        return new Error(`Invalid prop ${propName}. Value must be object.`);
    }

    for (const prop in testValue) {
        const results = type.propTypes[prop](type, prop);
        if (results !== null) {
            return results;
        }
    }

    return null;
};

export const typedObjectArrayPropType = function (type: any, props: any, propName: string) {
    const testValues = props[propName];
    if (!Array.isArray(testValues)) {
        return new Error(`Invalid value for ${propName}. Value must be an array.`);
    }
    for (let i = 0; i < testValues.length; i++) {
        const testValue = testValues[i];
        const results = typedObjectPropType(type, null, null as any, testValue);
        if (results !== null) {
            return results;
        }
    }

    return null;
};

export const containerUriPropType = function (props: any, propName: string) {
    //check if prop is supplied by option element
    let testValue = props[propName];
    if (testValue && testValue.hasOwnProperty("value")) testValue = testValue.value;
    console.log(testValue);
    if (!validateContainerUri(testValue)) {
        if (!verifyIsNotEmpty(testValue) || testValue == null) {
            return null;
        }
        return new Error(
            `Invalid value for ${propName}. Enter a valid Amazon ECR image Uri ACCOUNT_NUMBER.dkr.(ecr|ecr-fips).REGION.amazonaws.com/IMAGE_NAME`
        );
    }
    return null;
};

export const validateLambdaName = function (props: any, propName: string) {
    //check if prop is supplied by option element
    let testValue = props[propName];
    if (testValue && testValue.hasOwnProperty("value")) testValue = testValue.value;

    if (!verifyIsNotEmpty(testValue)) {
        return null;
    }

    return stringMaxLength.bind(null, 64);
};

export const EntityPropTypes = {
    ENTITY_ID: entityIdPropType,
    ENTITY_ID_ARRAY: entityIdArrayPropType,
    ENTITY: entityPropType,
    FILE_TYPE: fileTypePropType,
    STRING_32: stringMaxLength.bind(null, 32),
    STRING_64: stringMaxLength.bind(null, 64),
    STRING_128: stringMaxLength.bind(null, 128),
    STRING_256: stringMaxLength.bind(null, 256),
    JSON_STRING: jsonString,
    CONTAINER_URI: containerUriPropType,
    LAMBDA_NAME: validateLambdaName,
    BOOL: boolPropType,
    OBJECT: objectPropType,
    TYPED_OBJECT: typedObjectPropType,
    TYPED_OBJECT_ARRAY: typedObjectArrayPropType,
};
