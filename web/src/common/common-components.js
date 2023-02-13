/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import {
  Alert,
  AppLayout,
  Box,
  Link,
  Button,
  Header,
  SpaceBetween,
  SideNavigation,
  Badge,
  Icon,
} from "@cloudscape-design/components";
import { appLayoutLabels, externalLinkProps } from "./helpers/labels";
import {
  getHeaderCounterText,
  getServerHeaderCounterText,
} from "./helpers/tableCounterStrings";

export const EmptyState = ({ title, subtitle, action }) => {
  return (
    <Box textAlign="center" color="inherit">
      <Box variant="strong" textAlign="center" color="inherit">
        {title}
      </Box>
      <Box variant="p" padding={{ bottom: "s" }} color="inherit">
        {subtitle}
      </Box>
      {action}
    </Box>
  );
};

export const InfoLink = ({ id, onFollow }) => (
  <Link variant="info" id={id} onFollow={onFollow}>
    Info
  </Link>
);

// a special case of external link, to be used within a link group, where all of them are external
// and we do not repeat the icon
export const ExternalLinkItem = ({ href, text }) => (
  <Link
    href={href}
    ariaLabel={`${text} ${externalLinkProps.externalIconAriaLabel}`}
    target="_blank"
  >
    {text}
  </Link>
);

export const TableNoMatchState = (props) => (
  <Box margin={{ vertical: "xs" }} textAlign="center" color="inherit">
    <SpaceBetween size="xxs">
      <div>
        <b>No matches</b>
        <Box variant="p" color="inherit">
          We can't find a match.
        </Box>
      </div>
      <Button onClick={props.onClearFilter}>Clear filter</Button>
    </SpaceBetween>
  </Box>
);

export const TableEmptyState = ({ resourceName }) => (
  <Box margin={{ vertical: "xs" }} textAlign="center" color="inherit">
    <SpaceBetween size="xxs">
      <div>
        <b>No {resourceName.toLowerCase()}s</b>
        <Box variant="p" color="inherit">
          No {resourceName.toLowerCase()}s associated with this resource.
        </Box>
      </div>
      <Button>Create {resourceName.toLowerCase()}</Button>
    </SpaceBetween>
  </Box>
);

function getCounter(props) {
  if (props.counter) {
    return props.counter;
  }
  if (!props.totalItems) {
    return null;
  }
  if (props.serverSide) {
    return getServerHeaderCounterText(props.totalItems, props.selectedItems);
  }
  return getHeaderCounterText(props.totalItems, props.selectedItems);
}

export const TableHeader = (props) => {
  return (
    <Header
      counter={getCounter(props)}
      info={props.updateTools && <InfoLink onFollow={props.updateTools} />}
      description={props.description}
      actions={props.actionButtons}
    >
      {props.title}
    </Header>
  );
};

export function DevSameOriginWarning() {
  const { hostname, protocol } = document.location;
  const amazonSubdomain =
    /.amazon.com$/.test(hostname) || /.a2z.com$/.test(hostname);
  const sameOrigin = protocol === "https:" && amazonSubdomain;

  if (!sameOrigin) {
    return (
      <Alert
        header="You need to host this page in compliance with same-origin policy"
        type="error"
      >
        <span>
          The dashboard will not work properly unless the page is hosted:
          <ul>
            <li>over https</li>
            <li>on amazon.com or a2z.com subdomains</li>
          </ul>
          Use startHttps script{" "}
          <Box variant="code">sudo npm run startHttps</Box> from examples
          package to achieve this
        </span>
      </Alert>
    );
  }
  return null;
}

export function CustomAppLayout(props) {
  return (
    <AppLayout
      {...props}
      ariaLabels={appLayoutLabels}
      onNavigationChange={(event) => {
        if (props.onNavigationChange) {
          props.onNavigationChange(event);
        }
      }}
      onToolsChange={(event) => {
        if (props.onToolsChange) {
          props.onToolsChange(event);
        }
      }}
    />
  );
}
