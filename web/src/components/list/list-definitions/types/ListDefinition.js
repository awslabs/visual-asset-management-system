/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import PropTypes from "prop-types";
import ColumnDefinition from "./ColumnDefinition";
import FilterDefinition from "./FilterDefinition";
import { deleteElement } from "../../../../services/APIService";

export default function ListDefinition(props) {
    const {
        columnDefinitions,
        visibleColumns,
        filterColumns,
        pluralName,
        pluralNameTitleCase,
        singularNameTitleCase,
        //@todo find better way to handle delete logic
        elementId,
        deleteRoute,
    } = props;
    this.columnDefinitions = columnDefinitions;
    this.visibleColumns = visibleColumns;
    this.filterColumns = filterColumns;
    this.pluralName = pluralName;
    this.pluralNameTitleCase = pluralNameTitleCase;
    this.singularNameTitleCase = singularNameTitleCase;
    this.elementId = elementId;
    this.deleteRoute = deleteRoute;
    if (props.deleteFunction !== null && props.deleteFunction !== undefined) {
        this.deleteFunction = props.deleteFunction;
    } else {
        this.deleteFunction = async function (item) {
            return deleteElement({
                deleteRoute: deleteRoute,
                elementId: elementId,
                item: item,
            });
        };
    }
}

ListDefinition.propTypes = {
    columnDefinitions: PropTypes.arrayOf(ColumnDefinition).isRequired,
    visibleColumns: PropTypes.arrayOf(PropTypes.string).isRequired,
    filterColumns: PropTypes.arrayOf(FilterDefinition).isRequired,
    pluralName: PropTypes.string.isRequired,
    pluralNameTitleCase: PropTypes.string.isRequired,
    singularNameTitleCase: PropTypes.string,
    elementId: PropTypes.string.isRequired,
    deleteRoute: PropTypes.string.isRequired,
    deleteFunction: PropTypes.func,
};
