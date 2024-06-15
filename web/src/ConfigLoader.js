/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect } from "react";
import { Amplify, Auth, Hub, Cache } from "aws-amplify";

import { API } from "aws-amplify";
/**
 * Checks environment and either uses the URL for the API to get the project
 * resources, or goes with whatever default you've set in your local env.
 */
let basePath;
if (process.env.NODE_ENV === "production") {
    basePath = window.location.origin;
} else {
    basePath = process.env.REACT_APP_API_URL || "";
    console.log(basePath);
}

const config = Cache.getItem("config");

if (config) {
    renderApp(config);
} else {
    fetch(`${basePath}/api/amplify-config`).then(async (response) => {
        const config = await response.json();
        Cache.setItem("config", config);
        renderApp(config);
    });
}

function renderApp(config) {
    Amplify.configure({
        Auth: {
            mandatorySignIn: true,
            region: config.region || "us-east-1",
            userPoolId: config.userPoolId,
            identityPoolId: config.identityPoolId,
            userPoolWebClientId: config.appClientId,
        },
        Storage: {
            region: config.region,
            identityPoolId: config.identityPoolId,
            bucket: config.bucket,
            customPrefix: {
                public: "",
            },
        },
        API: {
            endpoints: [
                {
                    name: "api",
                    endpoint: config.api,
                    region: config.region,
                    custom_header: async () => {
                        return {
                            Authorization: `Bearer ${(await Auth.currentSession())
                                .getIdToken()
                                .getJwtToken()}`,
                        };
                    },
                },
            ],
        },
    });
    if (!config.bucket || !config.featuresEnabled) {
        API.get("api", `secure-config`, {}).then((value) => {
            config.bucket = value.bucket;
            config.featuresEnabled = value.featuresEnabled;
            Amplify.Storage.bucket = value.bucket;
            Cache.setItem("config", config);
        });
    }
}

function ConfigLoader(props) {
    useEffect(() => {
        Hub.listen("auth", ({ payload: { event, data } }) => {
            console.log("auth event", event, data);
        });
    });

    return <>{props.children}</>;
}

export default ConfigLoader;
