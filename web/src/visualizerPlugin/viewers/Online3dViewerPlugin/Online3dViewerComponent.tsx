/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { ViewerPluginProps } from "../../core/types";
import Online3DViewerContainer from "./components/core/Online3DViewerContainer";

const Online3dViewerComponent: React.FC<ViewerPluginProps> = (props) => {
    return <Online3DViewerContainer {...props} />;
};

export default Online3dViewerComponent;
