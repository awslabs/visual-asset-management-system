/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCollection, UseCollectionOptions, UseCollectionResult } from '@cloudscape-design/collection-hooks';
import { useEffect, useState } from 'react';
import { TableProps } from '@cloudscape-design/components/table';
import { ITreeNode, TreeMap } from '../Model/TreeNode';
import { TreeUtility } from '../Model/TreeUtility';

export interface UseTreeCollection<T> extends UseCollectionOptions<ITreeNode<T>> {
  keyPropertyName: string;
  parentKeyPropertyName: string;
  columnDefinitions: ReadonlyArray<TableProps.ColumnDefinition<T>>;
}

export interface UseTreeCollectionResult<T> extends UseCollectionResult<ITreeNode<T>> {
  expandNode: (node: ITreeNode<T>) => void;
  reset: () => void;
}

export const useTreeCollection = <T>(
  items: T[],
  props: UseTreeCollection<T>
): UseTreeCollectionResult<T> => {
  const { keyPropertyName, parentKeyPropertyName, columnDefinitions, ...collectionProps } = props;
  const [treeMap, setTreeMap] = useState<TreeMap<T>>(new Map());
  const [nodes, setNodes] = useState<ITreeNode<T>[]>([]);
  const [sortState, setSortState] = useState<TableProps.SortingState<T>>({
    ...(collectionProps.sorting?.defaultState || {}),
  } as TableProps.SortingState<T>);
  const [columnsDefinitions] = useState(columnDefinitions);

  useEffect(() => {
    const treeNodes = TreeUtility.buildTreeNodes(
      items,
      treeMap,
      keyPropertyName,
      parentKeyPropertyName
    );
    TreeUtility.sortTree(treeNodes, sortState, columnsDefinitions);
    // only builds prefix after building and sorting the tree
    const tree = TreeUtility.buildTreePrefix(treeNodes);
    setNodes(TreeUtility.flatTree(tree));
  }, [items, keyPropertyName, parentKeyPropertyName, sortState, columnsDefinitions, treeMap]);

  const expandNode = (node: ITreeNode<T>) => {
    if (node) {
      const key = (node as any)[keyPropertyName];
      const internalNode = nodes.find((n) => (n as any)[keyPropertyName] === key)!;
      internalNode.toggleExpandCollapse();
      TreeUtility.expandOrCollapseChildren(internalNode, treeMap, keyPropertyName);
      treeMap.set(key, internalNode);
      const updatedNodes = nodes.concat([]);
      setNodes(updatedNodes);
      setTreeMap(treeMap);
    }
  };

  const reset = () => {
    setNodes([]);
    setTreeMap(new Map());
  };

  const internalCollectionProps = {
    ...collectionProps,
    sorting: undefined, // disable useCollection sort in favor of TreeUtility.sortTree
    filtering: {
      ...(collectionProps.filtering || {}),
      filteringFunction: (item: ITreeNode<T>, filteringText: string, filteringFields?: string[]) =>
        TreeUtility.filteringFunction(
          item,
          filteringText,
          filteringFields,
          collectionProps.filtering?.filteringFunction
        ),
    },
  };

  const collectionResult = useCollection(nodes, internalCollectionProps);
  const useCollectionResult = {
    ...collectionResult,
    collectionProps: {
      ...collectionResult.collectionProps,
      sortingColumn: sortState.sortingColumn,
      sortingDescending: sortState.isDescending,
      onSortingChange: (event: CustomEvent<TableProps.SortingState<T>>) => {
        setSortState(event.detail);
        const customOnSortingChange = collectionResult.collectionProps.onSortingChange;
        if (customOnSortingChange) {
          customOnSortingChange(event);
        }
      },
    },
  } as UseCollectionResult<ITreeNode<T>>;

  return {
    expandNode,
    reset,
    ...useCollectionResult,
  };
};
