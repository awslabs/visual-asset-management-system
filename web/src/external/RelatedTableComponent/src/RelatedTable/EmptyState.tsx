import Box from '@awsui/components-react/box';
import * as React from 'react';

export interface EmptyStateProps {
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
