/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import Table, { TableProps } from '@cloudscape-design/components/table';
import React from 'react';
import { ITreeNode } from '../Model/TreeNode';
import { ButtonWithTreeLines } from './ButtonWithTreeLines';

export interface RelatedTableProps<T> extends TableProps<T> {
  expandChildren: (node: T) => void;
  expandColumnPosition?: number;
  filteringText?: string;
}

export function RelatedTable<T>(props: RelatedTableProps<ITreeNode<T>>) {
  const {
    columnDefinitions,
    items,
    expandChildren,
    expandColumnPosition = 1,
    filteringText = '',
  } = props;
  const isFiltering = filteringText !== '';
  const zeroBasedColumnPos = expandColumnPosition - 1;
  const columns = [...columnDefinitions];
  const columnToExpand = columns[zeroBasedColumnPos];
  columns[zeroBasedColumnPos] = {
    ...columnToExpand,
    cell: (node) => {
      const cell = columnToExpand?.cell(node);
      return (
        <ButtonWithTreeLines
          alwaysExpanded={isFiltering}
          node={node}
          content={cell}
          onClick={() => {
            expandChildren(node);
          }}
        />
      );
    },
  };
  return <Table {...props} columnDefinitions={columns} items={items || []} />;
}
