/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import PropTypes from "prop-types";

interface ColumnDefinitionType {
    id: any;
    header: any;
    CellWrapper: any;
    sortingField: any;
}

interface ColumnDefinitionConstructor {
    new (props: any): ColumnDefinitionType;
    (props: any): void;
    propTypes: any;
}

const ColumnDefinition = function (this: any, props: any) {
    const { id, header, cellWrapper, sortingField } = props;
    this.id = id;
    this.header = header;
    this.CellWrapper = cellWrapper;
    this.sortingField = sortingField;
} as unknown as ColumnDefinitionConstructor;

export default ColumnDefinition;

ColumnDefinition.propTypes = {
    id: PropTypes.string.isRequired,
    header: PropTypes.string.isRequired,
    CellWrapper: PropTypes.element.isRequired,
    sortingField: PropTypes.string,
};
