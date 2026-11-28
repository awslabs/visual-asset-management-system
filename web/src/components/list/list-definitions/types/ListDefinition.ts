/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import PropTypes from "prop-types";
import ColumnDefinition from "./ColumnDefinition";
import FilterDefinition from "./FilterDefinition";
import { deleteElement } from "../../../../services/APIService";

interface ListDefinitionType {
    columnDefinitions: any;
    visibleColumns: any;
    filterColumns: any;
    pluralName: any;
    pluralNameTitleCase: any;
    singularNameTitleCase: any;
    elementId: any;
    deleteRoute: any;
    createAction: any;
    deleteFunction: any;
    wrapLines?: boolean;
    hiddenColumns?: string[];
}

interface ListDefinitionConstructor {
    new (props: any): ListDefinitionType;
    (props: any): void;
    propTypes: any;
}

const ListDefinition = function (this: any, props: any) {
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
        createAction,
        wrapLines,
        hiddenColumns,
    } = props;
    this.columnDefinitions = columnDefinitions;
    this.visibleColumns = visibleColumns;
    this.filterColumns = filterColumns;
    this.pluralName = pluralName;
    this.pluralNameTitleCase = pluralNameTitleCase;
    this.singularNameTitleCase = singularNameTitleCase;
    this.elementId = elementId;
    this.deleteRoute = deleteRoute;
    this.createAction = createAction;
    // Wrap long cell text instead of widening the table past its container.
    this.wrapLines = wrapLines === true;
    // Columns of visibleColumns that start hidden. Setting it gives the table a column
    // preferences control, where the user can show them again.
    if (Array.isArray(hiddenColumns)) this.hiddenColumns = hiddenColumns;
    if (props.deleteFunction !== null && props.deleteFunction !== undefined) {
        this.deleteFunction = props.deleteFunction;
    } else {
        this.deleteFunction = async function (item: any) {
            return deleteElement({
                deleteRoute: deleteRoute,
                elementId: elementId,
                item: item,
            });
        };
    }
} as unknown as ListDefinitionConstructor;

export default ListDefinition;

ListDefinition.propTypes = {
    columnDefinitions: PropTypes.arrayOf(ColumnDefinition as any).isRequired,
    visibleColumns: PropTypes.arrayOf(PropTypes.string).isRequired,
    filterColumns: PropTypes.arrayOf(FilterDefinition as any).isRequired,
    pluralName: PropTypes.string.isRequired,
    pluralNameTitleCase: PropTypes.string.isRequired,
    singularNameTitleCase: PropTypes.string,
    elementId: PropTypes.string.isRequired,
    deleteRoute: PropTypes.string.isRequired,
    deleteFunction: PropTypes.func,
    createAction: PropTypes.bool,
    wrapLines: PropTypes.bool,
    hiddenColumns: PropTypes.arrayOf(PropTypes.string),
};
