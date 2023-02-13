/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  Grid,
  Header,
  Pagination,
  Select,
  TextFilter,
} from "@cloudscape-design/components";
import {
  RelatedTable,
  useTreeCollection,
} from "../../external/RelatedTableComponent/src";
import { EmptyState } from "../../common/common-components";
import ListDefinition from "./list-definitions/types/ListDefinition";

export default function RelatedTableList(props) {
  //props
  const {
    allItems,
    loading,
    listDefinition,
    databaseId,
    parentId,
    childId,
    setReload,
    HeaderControls,
  } = props;
  const {
    columnDefinitions,
    visibleColumns,
    filterColumns,
    pluralName,
    pluralNameTitleCase,
    elementId,
    deleteRoute,
  } = listDefinition;

  const filteredVisibleColumns = visibleColumns.filter((columnName) => {
    if (!databaseId) return true;
    if (columnName === "databaseId") return false;
    return true;
  });
  const filteredFilterColumns = filterColumns.filter((filterColumn) => {
    if (!databaseId) return true;
    if (filterColumn.name === "databaseId") return false;
    return true;
  });

  //state
  const [activeFilters, setActiveFilters] = useState(
    filteredFilterColumns.reduce((acc, cur) => {
      acc[cur.name] = null;
      return acc;
    }, {})
  );
  const [deleting, setDeleting] = useState(false);

  //private functions
  const getMatchesCountText = (items) => {
    return `Found ${items} ${pluralName}.`;
  };
  const filterTextElement = useRef();
  const highlightMatches = (text, match = "") => {
    let newText = text + "";
    if (match !== "") {
      match = match.split(" ").map((word) => word.toLowerCase());
      for (let i = 0; i < match.length; i++) {
        const regEx = new RegExp(match[i], "ig");
        newText = newText.replaceAll(regEx, ($replace) => `||${$replace}||`);
      }
      return newText.split("||").map((segment, i) => {
        if (match.includes(segment.toLowerCase())) {
          return <strong key={i}>{segment}</strong>;
        }
        return <span key={i}>{segment}</span>;
      });
    }
    return newText;
  };

  //implementation per polaris docs example
  const {
    expandNode,
    items,
    actions,
    filteredItemsCount,
    collectionProps,
    filterProps,
    paginationProps,
  } = useTreeCollection(allItems, {
    expanded: true,
    keyPropertyName: "name",
    parentKeyPropertyName: "parentId",
    filtering: {
      empty: (
        <EmptyState
          title={`No ${pluralName}`}
          subtitle={`No ${pluralName} to display.`}
          action={<Button>Create {pluralName}</Button>}
        />
      ),
      noMatch: (
        <EmptyState
          title="No matches"
          subtitle="We can’t find a match."
          action={
            <Button onClick={() => actions.setFiltering("")}>
              Clear filter
            </Button>
          }
        />
      ),
      filteringFunction: (item, filteringText) => {
        for (let i = 0; i < filteredFilterColumns.length; i++) {
          const filterColumnName = filteredFilterColumns[i].name;
          if (activeFilters[filterColumnName] !== null) {
            if (item[filterColumnName] !== activeFilters[filterColumnName])
              return false;
          }
        }

        const filteringTextLowerCase = filteringText.toLowerCase();
        if (filteringTextLowerCase !== "") {
          for (let i = 0; i < filteredVisibleColumns.length; i++) {
            const visibleColumnName = filteredVisibleColumns[i];
            if (
              item[visibleColumnName]
                ?.toString()
                .toLowerCase()
                .indexOf(filteringTextLowerCase) !== -1
            ) {
              return true;
            }
          }
          return false;
        }
        return true;
      },
    },
    pagination: { pageSize: 15 },
    sorting: {},
    selection: {},
  });

  const handleFilterSelected = (prop, value) => {
    const newActiveFilters = Object.assign({}, activeFilters);
    newActiveFilters[prop] = value;
    setActiveFilters(newActiveFilters);
  };

  return (
    <RelatedTable
      {...collectionProps}
      expandChildren={expandNode}
      trackBy={"name"}
      header={
        <>
          <HeaderControls />
          <Header
            counter={
              items.length !== allItems.length
                ? `(${items.length}/${allItems.length})`
                : `(${allItems.length})`
            }
          >
            {pluralNameTitleCase}
          </Header>
        </>
      }
      columnDefinitions={columnDefinitions.map(
        ({ id, header, CellWrapper, sortingField }) => {
          return {
            id,
            header,
            cell: (e) => (
              <CellWrapper item={e}>
                {highlightMatches(
                  e[id],
                  (() => {
                    const textFilterCaptureElement =
                      document.getElementById("textFilterCapture");
                    const textFilterInputElement =
                      textFilterCaptureElement.querySelectorAll(
                        ":scope input"
                      )[0];
                    return textFilterInputElement?.value;
                  })()
                )}
              </CellWrapper>
            ),
            sortingField,
          };
        }
      )}
      visibleColumns={filteredVisibleColumns}
      items={items}
      loading={loading}
      // selectionType={!isExpandable ? "multi" : ""}
      //@todo add aria pagination label
      pagination={<Pagination {...paginationProps} />}
      filter={
        <Grid
          gridDefinition={[
            { colspan: { default: "7" } },
            { colspan: { default: "5" } },
          ]}
        >
          <div id="textFilterCapture">
            <TextFilter
              id={"test"}
              {...filterProps}
              countText={getMatchesCountText(filteredItemsCount)}
              filteringAriaLabel={`Filter ${pluralName}`}
            />
          </div>
          <div style={{ float: "right" }}>
            <Grid
              gridDefinition={filteredFilterColumns.map((filterColumn, i) => {
                return {
                  colspan: {
                    default: String(Math.floor(12 / (filterColumn.length + 1))),
                  },
                };
              })}
            >
              {filteredFilterColumns.map((filterColumn, i) => {
                const selectedValue = activeFilters[filterColumn.name];
                return (
                  <Select
                    key={i}
                    selectedOption={
                      !selectedValue
                        ? null
                        : {
                            label: selectedValue,
                            value: selectedValue,
                          }
                    }
                    onChange={({ detail }) => {
                      handleFilterSelected(
                        filterColumn.name,
                        detail?.selectedOption?.value
                      );
                    }}
                    options={[
                      {
                        label: <em>all</em>,
                        value: null,
                      },
                    ].concat(
                      [
                        ...new Set(
                          allItems.map((row) => row[filterColumn.name])
                        ),
                      ].map((cellValue) => {
                        return { label: cellValue, value: cellValue };
                      })
                    )}
                    placeholder={filterColumn.placeholder}
                    selectedAriaLabel="Selected"
                  />
                );
              })}
            </Grid>
          </div>
        </Grid>
      }
    />
  );
}

RelatedTableList.propTypes = {
  allItems: PropTypes.array.isRequired,
  loading: PropTypes.bool.isRequired,
  setReload: PropTypes.func.isRequired,
  listDefinition: PropTypes.instanceOf(ListDefinition).isRequired,
  databaseId: PropTypes.string,
  parentId: PropTypes.string,
  childId: PropTypes.string,
};
