/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { ViewerPluginProps } from "../../core/types";
import ThreeJSViewerComponent from "./ThreeJSViewerComponent";

const ThreeJSViewerPlugin: React.FC<ViewerPluginProps> = (props) => {
    return <ThreeJSViewerComponent {...props} />;
};

export default ThreeJSViewerPlugin;
