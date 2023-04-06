/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import styled from "styled-components";

export const Wrapper = styled.div<{ height: number }>`
    position: relative;
    margin-top: -1rem;
    margin-bottom: -1rem;
    margin-left: -1rem;
    height: ${(props) => props.height}%;
`;

export const LeftPad = styled.div<{ length: number }>`
    display: flex;
    align-items: center;
    margin-left: ${({ length }) => length || 0}rem;
`;

export const EmptySpace = styled.span<{ width: number; height: number }>`
    position: relative;
    width: ${(props) => props.width}rem;
    height: ${(props) => props.height}rem;
`;

export const ButtonWrapper = styled.div<{}>`
    align-self: flex-start;
`;
