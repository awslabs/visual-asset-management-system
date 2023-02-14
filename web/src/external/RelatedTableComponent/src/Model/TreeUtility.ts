/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { TableProps } from '@cloudscape-design/components';
import { ExpandableTableNodeStatus, ITreeNode, TreeMap, TreeNode } from './TreeNode';

export class TreeUtility {
  public static buildTreeNodes = <T>(
    items: T[],
    treeMap: TreeMap<T>,
    keyPropertyName: string,
    parentKeyPropertyName: string
  ): ITreeNode<T>[] => {
    const staleNodeKeys = new Set<string>(Array.from(treeMap.keys()));
    const treeNodes = items
      .map((item) => {
        const key = (item as any)[keyPropertyName];
        staleNodeKeys.delete(key);
        return TreeUtility.createNode(item, treeMap, keyPropertyName, parentKeyPropertyName);
      })
      .map((node) => TreeUtility.prepareNode(node, treeMap, keyPropertyName))
      .filter((node) => typeof node.getParent() === 'undefined');

    TreeUtility.cleanupTree(keyPropertyName, treeMap, staleNodeKeys);
    return treeNodes;
  };

  public static createNode = <T>(
    item: T,
    treeMap: TreeMap<T>,
    keyPropertyName: string,
    parentKeyPropertyName: string
  ): ITreeNode<T> => {
    const key = (item as any)[keyPropertyName];
    let node = treeMap.get(key);
    if (node) {
      // in case exists just updates
      TreeUtility.updateNode(node, item);
    } else {
      node = new TreeNode(item);
    }

    TreeUtility.createOrSetParentNode(node, treeMap, keyPropertyName, parentKeyPropertyName);
    treeMap.set(key, node);
    return node;
  };

  public static createOrSetParentNode = <T>(
    node: ITreeNode<T>,
    treeMap: TreeMap<T>,
    keyPropertyName: string,
    parentKeyPropertyName: string
  ) => {
    const parentKey = (node as any)[parentKeyPropertyName];
    if (parentKey) {
      const parentNode =
        treeMap.get(parentKey) || new TreeNode({ [keyPropertyName]: parentKey } as T);
      if (parentNode.getChildren().length === 0 || node.getParent() !== parentNode) {
        node.setParentNode(parentNode);
        parentNode.addChild(node);
      }
      treeMap.set(parentKey, parentNode);
    }
  };

  public static prepareNode = <T>(
    node: ITreeNode<T>,
    treeMap: TreeMap<T>,
    keyPropertyName: string
  ): ITreeNode<T> => {
    const key = (node as any)[keyPropertyName];
    const isVisible = node.getParent()
      ? node.getParent()!.isExpanded() && node.getParent()!.isVisible()
      : true;
    node.setVisible(isVisible);
    node.setStatus(
      node.hasChildren || node.getChildren().length > 0
        ? ExpandableTableNodeStatus.normal
        : ExpandableTableNodeStatus.emptyChildren
    );
    treeMap.set(key, node);
    return node;
  };

  public static cleanupTree = <T>(
    keyPropertyName: string,
    treeMap: TreeMap<T>,
    staleNodeKeys: Set<string>
  ) => {
    staleNodeKeys.forEach((key) => {
      const node = treeMap.get(key);
      if (node) {
        TreeUtility.removeNode(node, keyPropertyName, treeMap);
      }
    });
  };

  public static removeNode = <T>(
    node: ITreeNode<T>,
    keyPropertyName: string,
    treeMap: TreeMap<T>
  ) => {
    const key = (node as any)[keyPropertyName];

    if (node.getParent()) {
      const parentChildren = node.getParent()!.getChildren();
      const childIndex = parentChildren.findIndex((child) => child === node);
      parentChildren.splice(childIndex, 1);
      node.setParentNode(undefined);
    }

    node.getChildren().forEach((child) => TreeUtility.removeNode(child, keyPropertyName, treeMap));
    node.removeAllChildren();
    treeMap.delete(key);
  };

  public static buildTreePrefix = <T>(tree: ITreeNode<T>[]) => {
    return tree.map((node, index) => {
      return TreeUtility.recursiveBuildTreePrefix(node, index, []);
    });
  };

  private static recursiveBuildTreePrefix = <T>(
    node: ITreeNode<T>,
    index: number,
    parentLastChildPath: boolean[]
  ) => {
    const isLastChild = node.getParent()
      ? node.getParent()!.getChildren().length - 1 === index
      : true;
    node.buildPrefix(isLastChild, parentLastChildPath);
    node
      .getChildren()
      .forEach((child: ITreeNode<T>, childIndex) =>
        TreeUtility.recursiveBuildTreePrefix(
          child,
          childIndex,
          parentLastChildPath.concat([isLastChild])
        )
      );
    return node;
  };

  public static flatTree = <T>(tree: ITreeNode<T>[]) => {
    const flattenTree: ITreeNode<T>[] = [];
    TreeUtility.recursiveFlatTree(tree, flattenTree);
    return flattenTree;
  };

  private static recursiveFlatTree = <T>(tree: ITreeNode<T>[], flattenTree: ITreeNode<T>[]) => {
    tree.forEach((node) => {
      flattenTree.push(node);
      if (node.getChildren().length) {
        TreeUtility.recursiveFlatTree(node.getChildren(), flattenTree);
      }
    });
  };

  public static expandOrCollapseChildren = <T>(
    node: ITreeNode<T>,
    treeMap: TreeMap<T>,
    keyPropertyName: string
  ) => {
    node.getChildren().forEach((child: ITreeNode<T>) => {
      const key = (child as any)[keyPropertyName];
      child.setVisible(node.isExpanded() && node.isVisible());
      treeMap.set(key, child);
      TreeUtility.expandOrCollapseChildren(child, treeMap, keyPropertyName);
    });
  };

  public static updateNode = <T>(node: ITreeNode<T>, newData: T) => {
    Object.keys(newData).forEach((prop) => {
      (node as any)[prop] = (newData as any)[prop];
    });
  };

  public static filteringFunction = <T extends Record<string, any>>(
    item: ITreeNode<T>,
    filteringText: string,
    filteringFields?: string[],
    customFilteringFunction?: (
      item: ITreeNode<T>,
      filteringText: string,
      filteringFields?: string[]
    ) => boolean
  ): boolean => {
    if (filteringText.length === 0) {
      return item.isVisible();
    }

    let filterMatched;
    if (customFilteringFunction) {
      filterMatched = customFilteringFunction(item, filteringText, filteringFields);
    } else {
      const fields = filteringFields || Object.keys(item);
      const lowFilteringText = filteringText.toLowerCase();
      filterMatched = fields.some(
        (key) => String(item[key]).toLowerCase().indexOf(lowFilteringText) > -1
      );
    }

    if (!filterMatched) {
      const childrenFiltered = item
        .getChildren()
        .map((child) => TreeUtility.filteringFunction(child, filteringText, filteringFields))
        .find((found) => found);
      return typeof childrenFiltered !== 'undefined';
    }
    return filterMatched;
  };

  public static sortTree = <T>(
    tree: ITreeNode<T>[],
    sortState: TableProps.SortingState<T>,
    columnsDefinitions: ReadonlyArray<TableProps.ColumnDefinition<T>>
  ) => {
    const { sortingColumn } = sortState;
    if (sortingColumn && sortingColumn.sortingField) {
      const columnDefinition = columnsDefinitions.find(
        (column) => column.sortingField === sortingColumn.sortingField
      )!;
      const direction = sortState.isDescending ? -1 : 1;
      const comparator =
        columnDefinition.sortingComparator ||
        TreeUtility.defaultComparator(sortState.sortingColumn.sortingField as keyof T);

      tree
        .sort((a: T, b: T) => comparator(a, b) * direction)
        .forEach((node) => TreeUtility.sortTree(node.getChildren(), sortState, columnsDefinitions));
    }
  };

  private static defaultComparator = <T>(sortingField: keyof T) => {
    return (row1: T, row2: T) => {
      // Use empty string as a default value, because it works well to compare with both strings and numbers:
      // Every number can be casted to a string, but not every string can be casted to a meaningful number,
      // sometimes it is NaN.
      const value1 = row1[sortingField] ?? '';
      const value2 = row2[sortingField] ?? '';
      if (typeof value1 === 'string' && typeof value2 === 'string') {
        return value1.localeCompare(value2);
      }
      if (value1 < value2) {
        return -1;
      }
      // eslint-disable-next-line eqeqeq
      return value1 == value2 ? 0 : 1;
    };
  };
}
