/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { PropsWithChildren, useEffect, useState } from "react";
import { API, Amplify, Auth, Cache, Hub } from "aws-amplify";
import styles from "./loginbox.module.css";
import loginBgImageSrc from "../resources/img/login_bg.png";
import logoDarkImageSrc from "../resources/img/logo_dark.svg";
import { Heading, useTheme } from "@aws-amplify/ui-react";

/**
 * Additional configuration needed to use federated identities
 */
export interface AmplifyConfigFederatedIdentityProps {
    /**
     * The name of the federated identity provider.
     */
    customFederatedIdentityProviderName: string;
    /**
     * The cognito auth domain
     */
    customCognitoAuthDomain: string;
    /**
     * redirect signin url
     */
    redirectSignIn: string;
    /**
     * redirect signout url
     */
    redirectSignOut: string;
}

interface Config {
    /**
     * The Cognito UserPoolId to authenticate users in the front-end
     */
    userPoolId: string;
    /**
     * The Cognito AppClientId to authenticate users in the front-end
     */
    appClientId: string;
    /**
     * The Cognito IdentityPoolId to authenticate users in the front-end
     */
    identityPoolId: string;
    /**
     * The ApiGatewayV2 HttpApi to attach the lambda
     */
    api: string;
    /**
     * region
     */
    region: string;
    /**
     * Additional configuration needed for federated auth
     */
    federatedConfig?: AmplifyConfigFederatedIdentityProps;
    /**
     * bucket
     */
    bucket?: string;

    stackName: string;
}

function configureAmplify(config: Config, setAmpInit: (x: boolean) => void) {
    Amplify.configure({
        Auth: {
            mandatorySignIn: true,
            region: config.region,
            userPoolId: config.userPoolId,
            userPoolWebClientId: config.appClientId,
            identityPoolId: config.identityPoolId,
            cookieStorage: {
                domain: " ", // process.env.REACT_APP_COOKIE_DOMAIN, // Use a single space " " for host-only cookies
                expires: null, // null means session cookies
                path: "/",
                secure: false, // for developing on localhost over http: set to false
                sameSite: "lax",
            },
            oauth: {
                domain: config.federatedConfig?.customCognitoAuthDomain,
                scope: ["openid", "email", "profile"], //  process.env.REACT_APP_USER_POOL_SCOPES.split(","),
                redirectSignIn: window.location.origin, // config.federatedConfig?.redirectSignIn,
                redirectSignOut: window.location.origin, // config.federatedConfig?.redirectSignOut,
                responseType: "code",
            },
        },
        Storage: {
            region: config.region,
            identityPoolId: config.identityPoolId,
            bucket: config.bucket,
            customPrefix: {
                public: "",
                private: "",
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
        geo: {
            AmazonLocationService: {
                maps: {
                    items: {
                        [`vams-map-raster-${config.region}-${config.stackName}`]: {
                            // REQUIRED - Amazon Location Service Map resource name
                            style: "RasterEsriImagery", // REQUIRED - String representing the style of map resource
                        },
                        [`vams-map-streets-${config.region}-${config.stackName}`]: {
                            style: "VectorEsriStreets",
                        },
                    },
                    default: `vams-map-raster-${config.region}-${config.stackName}`, // REQUIRED - Amazon Location Service Map resource name to set as default
                },
                // search_indices: {
                //     items: ["vams-index"], // REQUIRED - Amazon Location Service Place Index name
                //     default: "vams-index", // REQUIRED - Amazon Location Service Place Index name to set as default
                // },
                // geofenceCollections: {
                //     items: ["XXXXXXXXX", "XXXXXXXXX"], // REQUIRED - Amazon Location Service Geofence Collection name
                //     default: "XXXXXXXXX", // REQUIRED - Amazon Location Service Geofence Collection name to set as default
                // },
                region: config.region, // REQUIRED - Amazon Location Service Region
            },
        },
    });

    setAmpInit(true);
}

/**
 * Checks environment and either uses the URL for the API to get the project
 * resources, or goes with whatever default you've set in your local env.
 */
let basePath: string;
if (process.env.NODE_ENV === "production") {
    basePath = window.location.origin;
} else {
    basePath = process.env.REACT_APP_API_URL || "";
}

const CenteredBox: React.FC<PropsWithChildren<{}>> = ({ children }) => {
    return (
        <div className={styles.container}>
            <div className={styles.centeredBox}>{children}</div>
        </div>
    );
};

interface LoginProps {
    onLogin: () => void;
    onLocal: () => void;
}

const FedLoginBox: React.FC<LoginProps> = ({ onLogin, onLocal }) => {
    const { tokens } = useTheme();

    return (
        <>
            <div className={styles.container}>
                <div className={styles.centeredBox}>
                    <Heading level={3} padding={`${tokens.space.xl} ${tokens.space.xl} 0`}>
                        <img
                            style={{ width: "100%" }}
                            src={logoDarkImageSrc}
                            alt="Visual Asset Management System Logo"
                        />
                    </Heading>
                    <button
                        className={styles.button}
                        onClick={onLogin}
                        data-testid="federated-login-button"
                    >
                        Login with Federated Identity Provider
                    </button>
                    <p>
                        <small>
                            <a
                                onClick={(e) => {
                                    e.preventDefault();
                                    e.stopPropagation();
                                    console.log("click", e);
                                    onLocal();
                                }}
                                href="#local"
                                className={styles.link}
                                data-testid="local-creds-link"
                            >
                                Login with local credentials
                            </a>
                        </small>
                    </p>
                </div>
            </div>
            <img
                alt="background texture"
                src={loginBgImageSrc}
                style={{
                    position: "fixed",
                    top: 0,
                    width: "100vw",
                    left: 0,
                    zIndex: "-100",
                }}
            />
        </>
    );
};

type AuthProps = {};

const VAMSAuth: React.FC<AuthProps> = (props) => {
    const [state, setState] = useState<any>({
        email: undefined,
        username: undefined,
        authenticated: undefined,
    });

    const [user, setUser] = useState(null);
    const [useLocal, setUseLocal] = useState(false);
    const [userWantsLocal, setUserWantsLocal] = useState(false);

    const [config, setConfig] = useState<any>(Cache.getItem("config"));
    const [ampInit, setAmpInit] = useState(false);

    console.log("useLocal", useLocal);
    useEffect(() => {
        if (config && !config.bucket && user) {
            API.get("api", `secure-config`, {}).then((value) => {
                config.bucket = value.bucket;
                Cache.setItem("config", config);
                setConfig(config);
            });
        }
    }, [config, user]);

    useEffect(() => {
        if (config) {
            setUseLocal(!config.federatedConfig || userWantsLocal);
            configureAmplify(config, setAmpInit);
        } else {
            fetch(`${basePath}/api/amplify-config`).then(async (response) => {
                const config = await response.json();
                Cache.setItem("config", config);
                setConfig(config);
            });
        }
    }, [ampInit, config, setConfig, useLocal, userWantsLocal]);

    useEffect(() => {
        if (!ampInit) return;

        const unsubscribe = Hub.listen("auth", ({ payload: { event, data } }) => {
            switch (event) {
                case "signIn":
                    setUser(data);
                    break;
                case "signOut":
                    setUser(null);
                    break;
            }
        });

        Auth.currentAuthenticatedUser()
            .then((currentUser) => setUser(currentUser))
            .catch(() => console.log("Not signed in"));

        return unsubscribe;
    }, [ampInit]);

    useEffect(() => {
        if (!ampInit) return;

        Auth.currentSession()
            .then((user) =>
                setState({
                    email: user.getIdToken().decodePayload().email,
                    authenticated: true,
                })
            )
            .catch((e) => {
                setState({ authenticated: false });
            });

        // Schedule check and refresh (when needed) of JWT's every 5 min:
        const i = setInterval(() => Auth.currentSession(), 5 * 60 * 1000);
        return () => clearInterval(i);
    }, [ampInit]);

    if ((user !== null || useLocal) && ampInit) {
        return <>{props.children}</>;
    }

    if (state.authenticated === undefined || config === null || !ampInit) {
        return (
            <CenteredBox>
                <p className="explanation">One moment please ...</p>
            </CenteredBox>
        );
    }

    return (
        <FedLoginBox
            onLocal={() => setUserWantsLocal(true)}
            onLogin={() =>
                Auth.federatedSignIn({
                    customProvider: config.federatedConfig?.customFederatedIdentityProviderName,
                })
            }
        />
    );
};

export default VAMSAuth;
