/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as React from 'react';
import TextFilter from '@cloudscape-design/components/text-filter';
import Pagination from '@cloudscape-design/components/pagination';
import { TableProps, TextFilterProps } from '@cloudscape-design/components';
import EmptyState, { EmptyStateProps } from '../RelatedTable/EmptyState';
import { RelatedTableProps } from '../RelatedTable/RelatedTableComponent';
import { useTreeCollection, UseTreeCollection } from '../Hooks/useTreeCollection';
import { ITreeNode } from '../Model/TreeNode';

interface SortingColumn<T> {
  sortingField?: string;
  sortingComparator?: (a: T, b: T) => number;
}
interface SortingState<T> {
  isDescending?: boolean;
  sortingColumn: SortingColumn<T>;
}

export interface RelatedTableActions<T> {
  setFiltering(filteringText: string): void;
  setCurrentPage(pageNumber: number): void;
  setSorting(state: SortingState<T>): void;
  setSelectedItems(selectedItems: ReadonlyArray<T>): void;
  reset(): void;
}

export interface RelatedTableExtendedProps<T> extends RelatedTableProps<T> {
  items: T[];
  collectionOptions: UseTreeCollection<T>;
  expandChildren: (node: T) => void;
  empty: EmptyStateProps;
  onReady?: (actions: RelatedTableActions<T>) => void;
}

export const withUseTreeCollection = (RelatedTableComp: React.FC<any>) => {
  return React.forwardRef(<T extends unknown>(props: RelatedTableExtendedProps<T>, ref: any) => {
    const {
      items,
      columnDefinitions,
      collectionOptions,
      expandChildren,
      empty,
      filter,
      selectedItems,
      onSortingChange,
      onSelectionChange,
      onReady,
    } = props;

    const {
      items: nodes,
      collectionProps,
      filterProps,
      paginationProps,
      expandNode,
      actions,
      reset,
    } = useTreeCollection(items, {
      ...collectionOptions,
      columnDefinitions,
    });

    const renderEmpty = () => {
      if (empty) {
        return <EmptyState {...empty} />;
      }
      return null;
    };

    const renderFilter = () => {
      if (filter) {
        return <TextFilter {...filterProps} />;
      }
      return null;
    };

    const renderPagination = () => {
      if (collectionOptions.pagination) {
        return <Pagination {...collectionOptions.pagination} {...paginationProps} />;
      }
      return null;
    };

    if (onReady) {
      onReady({
        ...actions,
        reset,
      });
    }

    return (
      <RelatedTableComp
        {...props}
        {...filterProps}
        {...collectionProps}
        items={nodes || []}
        columnDefinitions={columnDefinitions}
        trackBy={collectionOptions.keyPropertyName}
        expandChildren={(node: ITreeNode<T>) => {
          expandNode(node);
          expandChildren(node);
        }}
        empty={renderEmpty()}
        selectedItems={selectedItems}
        onSelectionChange={onSelectionChange}
        onSortingChange={(event: any) => {
          if (onSortingChange) {
            onSortingChange(event);
          }
          if (collectionProps.onSortingChange) {
            collectionProps.onSortingChange(event);
          }
        }}
        filter={renderFilter()}
        pagination={renderPagination()}
        ref={ref}
      />
    );
  });
};
