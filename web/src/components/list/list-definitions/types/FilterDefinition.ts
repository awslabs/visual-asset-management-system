/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import PropTypes from "prop-types";

interface FilterDefinitionType {
    name: any;
    placeholder: any;
}

interface FilterDefinitionConstructor {
    new (props: any): FilterDefinitionType;
    (props: any): void;
    propTypes: any;
}

const FilterDefinition = function (this: any, props: any) {
    const { name, placeholder } = props;
    this.name = name;
    this.placeholder = placeholder;
} as unknown as FilterDefinitionConstructor;

export default FilterDefinition;

FilterDefinition.propTypes = {
    name: PropTypes.string.isRequired,
    placeholder: PropTypes.string.isRequired,
};
