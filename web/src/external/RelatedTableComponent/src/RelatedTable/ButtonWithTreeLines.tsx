/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import Button from '@cloudscape-design/components/button';
import React from 'react';
import { EmptySpace, LeftPad, Wrapper, ButtonWrapper } from './Common/StyledComponents';
import { ExpandableTableNodeStatus, ITreeNode } from '../Model/TreeNode';
import { createPrefixLines, Theme } from './Common/TreeLines';
import { THEME } from '../config';

const TABLE_ROW_HEIGHT_PERCENT = 100;
const WRAPPER_EXTRA_HEIGHT_PERCENT = 25;
export const MARGIN_LEFT_REM_MULTIPLICATOR = 2;

const noAction = () => {};
const theme = THEME as Theme;
const emptySpaceHeight = theme === Theme.POLARIS_OPEN_SOURCE ? 2 : 3;
const emptySpaceWidth = theme === Theme.POLARIS_OPEN_SOURCE ? 0.4 : 0.5;

export interface ButtonWithTreeLinesProps<T> {
  node: ITreeNode<T>;
  content: React.ReactNode;
  onClick?: () => void;
  alwaysExpanded: boolean;
}

function createToggleButton<T>(props: ButtonWithTreeLinesProps<T>) {
  const { node, onClick, alwaysExpanded } = props;
  const icon = node.isExpanded() || alwaysExpanded ? 'treeview-collapse' : 'treeview-expand';
  return node.getChildren().length > 0 || node.hasChildren ? (
    <ButtonWrapper>
      <Button
        disabled={node.getStatus() !== ExpandableTableNodeStatus.normal}
        variant="icon"
        iconName={icon}
        onClick={alwaysExpanded ? noAction : onClick}
      />
    </ButtonWrapper>
  ) : (
    <EmptySpace height={emptySpaceHeight} width={emptySpaceWidth} />
  );
}

export const ButtonWithTreeLines = React.memo(function ButtonWithTreeLinesComp<T>(
  props: ButtonWithTreeLinesProps<T>
) {
  const { node, content, alwaysExpanded } = props;
  const leftPadLength = node.getPrefix().length
    ? MARGIN_LEFT_REM_MULTIPLICATOR * (node.getPrefix().length - 1)
    : 0;
  return (
    <Wrapper height={TABLE_ROW_HEIGHT_PERCENT + WRAPPER_EXTRA_HEIGHT_PERCENT}>
      <LeftPad length={leftPadLength}>
        {createPrefixLines(node, theme, alwaysExpanded)}
        {createToggleButton(props)}
        {content}
      </LeftPad>
    </Wrapper>
  );
});
