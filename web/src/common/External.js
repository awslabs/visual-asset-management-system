/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as React from "react";

function loadError(onError) {
    console.error(`Failed ${onError.target.src} didn't load correctly`);
}

function External(url) {
    React.useEffect(() => {
        const LoadExternalScript = () => {
            const externalScript = document.createElement("script");
            externalScript.onerror = loadError;
            externalScript.id = "external";
            externalScript.async = true;
            externalScript.type = "text/javascript";
            externalScript.setAttribute("crossorigin", "anonymous");
            document.body.appendChild(externalScript);
            externalScript.src = url;
        };
        LoadExternalScript();
    }, []);

    return <></>;
}

export default External;
