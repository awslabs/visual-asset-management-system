/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import PropTypes from "prop-types";

export default function ColumnDefinition(props) {
    const { id, header, cellWrapper, sortingField } = props;
    this.id = id;
    this.header = header;
    this.CellWrapper = cellWrapper;
    this.sortingField = sortingField;
}

ColumnDefinition.propTypes = {
    id: PropTypes.string.isRequired,
    header: PropTypes.string.isRequired,
    CellWrapper: PropTypes.element.isRequired,
    sortingField: PropTypes.string,
};
