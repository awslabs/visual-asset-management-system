/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as awsui from '@cloudscape-design/design-tokens';
import React from 'react';
import { ITreeNode } from '../../Model/TreeNode';
import { TreeLineModes } from '../../Model/TreeLineModes';

const TABLE_ROW_HEIGHT_PERCENT = 100;
const WRAPPER_EXTRA_HEIGHT_PERCENT = 25;
const WRAPPER_VERTICAL_MARGIN_DIFF = 0.5;
const POLARIS_BUTTON_PADDING_DIFF = 0.3;
const ICON_BUTTON_WIDTH_PERCENT = 100;
const ICON_BUTTON_MIDPOINT_X = ICON_BUTTON_WIDTH_PERCENT / 2;
const ICON_BUTTON_MIDPOINT_Y = TABLE_ROW_HEIGHT_PERCENT / 2;
const SVG_WIDTH_IN_REM = 2.0;

const POLARIS_OPEN_SOURCE_TOP_MARGIN_DIFF = 0.2;
const POLARIS_OPEN_SOURCE_SVG_WIDTH_IN_REM = 1.7;

export enum Theme {
  POLARIS_INTERNAL = 'INTERNAL',
  POLARIS_OPEN_SOURCE = 'OPEN_SOURCE',
}

enum Dir {
  Top,
  Bottom,
  LittleBottom,
  Right,
}

const getLines = (directions: Dir[]): JSX.Element[] =>
  directions.map((dir, index) => {
    let y1 = TABLE_ROW_HEIGHT_PERCENT;
    let y2 = ICON_BUTTON_MIDPOINT_Y;

    if (dir === Dir.Top) {
      y1 = 0;
    }
    if (dir === Dir.Right) {
      y1 = ICON_BUTTON_MIDPOINT_Y;
    }
    if (dir === Dir.LittleBottom) {
      y2 = 0;
    }

    let x2 = ICON_BUTTON_MIDPOINT_X;
    if (dir === Dir.Right) {
      x2 = ICON_BUTTON_WIDTH_PERCENT;
    }

    return (
      <line
        style={{
          stroke: awsui.colorBorderDividerDefault,
          strokeWidth: 2,
          vectorEffect: 'non-scaling-stroke',
        }}
        key={`Line${index}`}
        x1={`${ICON_BUTTON_MIDPOINT_X}%`}
        x2={`${x2}%`}
        y1={`${y1}%`}
        y2={`${y2}%`}
      />
    );
  });

const getTopMargin = (isLittleBottom: boolean, theme: Theme) => {
  const topMarginPolarisInternal = isLittleBottom
    ? 1.5 + WRAPPER_VERTICAL_MARGIN_DIFF
    : `-${WRAPPER_VERTICAL_MARGIN_DIFF}`;
  const topMarginPolarisOpenSource = isLittleBottom
    ? 1.3 + POLARIS_OPEN_SOURCE_TOP_MARGIN_DIFF
    : `-${POLARIS_OPEN_SOURCE_TOP_MARGIN_DIFF}`;
  return theme === Theme.POLARIS_INTERNAL ? topMarginPolarisInternal : topMarginPolarisOpenSource;
};

const getHeight = (width: number, hasRightLine: boolean, theme: Theme) => {
  const heightPolarisInternal = hasRightLine
    ? `${width * 2}rem`
    : `${TABLE_ROW_HEIGHT_PERCENT + WRAPPER_EXTRA_HEIGHT_PERCENT}%`;
  const heightPolarisOpenSource = hasRightLine
    ? `${width + 0.7}rem`
    : `${TABLE_ROW_HEIGHT_PERCENT + WRAPPER_EXTRA_HEIGHT_PERCENT}%`;
  return theme === Theme.POLARIS_INTERNAL ? heightPolarisInternal : heightPolarisOpenSource;
};

const createLinesSvg = (directions: Dir[], theme: Theme, index: number) => {
  const width =
    theme === Theme.POLARIS_INTERNAL ? SVG_WIDTH_IN_REM : POLARIS_OPEN_SOURCE_SVG_WIDTH_IN_REM;
  const leftPos = (index - 1) * 2;
  const rightPos = leftPos + width;
  const rightLine = directions.find((dir) => dir === Dir.Right);
  const lines = [directions.filter((dir) => dir !== Dir.Right)];

  if (rightLine) {
    lines.push([rightLine]);
  }

  return lines.map((lineDirections) => {
    const isLittleBottom = lineDirections.length === 1 && lineDirections[0] === Dir.LittleBottom;
    const isRightLineOnly = lineDirections.length === 1 && lineDirections[0] === Dir.Right;
    const isTopWithRightLine =
      lineDirections.length === 1 && lineDirections[0] === Dir.Top && !!rightLine;

    const paddingLeft = theme === Theme.POLARIS_INTERNAL ? `${POLARIS_BUTTON_PADDING_DIFF}rem` : '';
    const topMargin = getTopMargin(isLittleBottom, theme);
    const height = getHeight(width, isRightLineOnly || isTopWithRightLine, theme);
    const viewBox =
      isRightLineOnly || isTopWithRightLine
        ? `0 0 ${width} ${width * 2}`
        : `0 0 ${width} ${TABLE_ROW_HEIGHT_PERCENT}`;
    return (
      <svg
        key={`${leftPos}${lineDirections.join('_')}${index}`}
        className="related-table-tree-line"
        style={{
          margin: `${topMargin}rem 0 0 0`,
          paddingLeft,
          position: 'absolute',
          top: 0,
          left: `${leftPos}rem`,
          right: `${rightPos}rem`,
          bottom: 0,
          width: `${width}rem`,
          height,
        }}
        viewBox={viewBox}
        preserveAspectRatio="none"
      >
        {getLines(lineDirections)}
      </svg>
    );
  });
};

export function createPrefixLines<T>(
  node: ITreeNode<T>,
  theme: Theme,
  alwaysExpanded: boolean = false
) {
  const prefixSequence: JSX.Element[] = [];
  node.getPrefix().forEach((prefix, index) => {
    switch (prefix) {
      case TreeLineModes.Indentation: // Fallthrough, used for external readability
      case TreeLineModes.ChildOfLastChild:
        break;
      case TreeLineModes.LastChild:
        prefixSequence.splice(index, 0, ...createLinesSvg([Dir.Top, Dir.Right], theme, index));
        break;
      case TreeLineModes.ChildOfMiddleChild:
        prefixSequence.splice(index, 0, ...createLinesSvg([Dir.Top, Dir.Bottom], theme, index));
        break;
      case TreeLineModes.MiddleChild:
        prefixSequence.splice(
          index,
          0,
          ...createLinesSvg([Dir.Top, Dir.Bottom, Dir.Right], theme, index)
        );
        break;
      default:
        prefixSequence.push(<span key="empty" />);
    }
  });
  if ((node.isExpanded() || alwaysExpanded) && node.getChildren().length > 0) {
    prefixSequence.splice(
      prefixSequence.length - 1,
      0,
      ...createLinesSvg([Dir.LittleBottom], theme, node.getPrefix().length || 1)
    );
  }
  return prefixSequence;
}
