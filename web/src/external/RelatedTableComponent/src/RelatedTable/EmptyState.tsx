/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import Box from '@cloudscape-design/components/box';
import * as React from 'react';

export interface EmptyStateProps extends React.ReactPortal {
  header?: string;
  description?: string;

}

/**
 * Empty state for tables component.
 *
 * https://polaris.a2z.com/patterns/design_patterns/empty_states/
 */
const EmptyState = ({ header, description }: EmptyStateProps) => {
  return (
    <Box textAlign="center" color="inherit">
      {header && (
        <Box variant="strong" textAlign="center" color="inherit">
          {header}
        </Box>
      )}
      {description && (
        <Box variant="p" padding={{ bottom: 's' }} color="inherit">
          {description}
        </Box>
      )}
    </Box>
  );
};

export default EmptyState;
