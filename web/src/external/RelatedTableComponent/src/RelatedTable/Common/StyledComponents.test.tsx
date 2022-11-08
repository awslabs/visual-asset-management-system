import { render } from '@testing-library/react';
import React from 'react';
import { LeftPad, Wrapper } from './StyledComponents';

describe('StyledComponents', () => {
  describe('LeftPad', () => {
    it('renders correctly', () => {
      const { container } = render(<LeftPad length={1} />);
      expect(container).toMatchInlineSnapshot(`
<div>
  <div
    class="sc-gsTEea byGSpt"
  />
</div>
`);
    });
  });

  describe('Wrapper', () => {
    it('renders correctly', () => {
      const { container } = render(<Wrapper height={1} />);
      expect(container).toMatchInlineSnapshot(`
<div>
  <div
    class="sc-bdfBQB jKctqW"
    height="1"
  />
</div>
`);
    });
  });
});
