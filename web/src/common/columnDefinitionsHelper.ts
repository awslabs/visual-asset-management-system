/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

export const addToColumnDefinitions = (columnDefinitions: any, propertyName: any, columns: any) =>
    columnDefinitions.map((colDef: any) => {
        const column = (columns || []).find((col: any) => col.id === colDef.id);
        return {
            ...colDef,
            [propertyName]: (column && column[propertyName]) || colDef[propertyName],
        };
    });

export const mapWithColumnDefinitionIds = (columnDefinitions: any, propertyName: any, items: any) =>
    columnDefinitions.map(({ id }: { id: any }, i: any) => ({
        id,
        [propertyName]: items[i],
    }));
