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
};
