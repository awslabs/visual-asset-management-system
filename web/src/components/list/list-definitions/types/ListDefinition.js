/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import PropTypes from "prop-types";
import ColumnDefinition from "./ColumnDefinition";
import FilterDefinition from "./FilterDefinition";

export default function ListDefinition(props) {
    const {
        columnDefinitions,
        visibleColumns,
        filterColumns,
        pluralName,
        pluralNameTitleCase,
        //@todo find better way to handle delete logic
        elementId,
        deleteRoute,
    } = props;
    this.columnDefinitions = columnDefinitions;
    this.visibleColumns = visibleColumns;
    this.filterColumns = filterColumns;
    this.pluralName = pluralName;
    this.pluralNameTitleCase = pluralNameTitleCase;
    this.elementId = elementId;
    this.deleteRoute = deleteRoute;
}

ListDefinition.propTypes = {
    columnDefinitions: PropTypes.arrayOf(ColumnDefinition).isRequired,
    visibleColumns: PropTypes.arrayOf(PropTypes.string).isRequired,
    filterColumns: PropTypes.arrayOf(FilterDefinition).isRequired,
    pluralName: PropTypes.string.isRequired,
    pluralNameTitleCase: PropTypes.string.isRequired,
    elementId: PropTypes.string.isRequired,
    deleteRoute: PropTypes.string.isRequired,
};
