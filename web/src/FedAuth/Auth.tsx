/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { PropsWithChildren, Suspense, useEffect, useState } from "react";
import { API, Cache, Hub, Amplify, Auth as AmplifyAuth } from "aws-amplify";
import { OAuth2Client, OAuth2Token, generateCodeVerifier } from "@badgateway/oauth2-client";
import { getSecureConfig, getAmplifyConfig } from "../services/APIService";
import { webRoutes } from "../services/APIService";
import { routeTable } from "../routes";
import { default as vamsConfig } from "../config";

import { Authenticator } from "@aws-amplify/ui-react";
import Button from "@cloudscape-design/components/button";
import loginBgImageSrc from "../resources/img/login_bg.png";
import logoDarkImageSrc from "../resources/img/logo_dark.svg";
import { Heading, useTheme } from "@aws-amplify/ui-react";

import LoadingScreen from "../components/loading/LoadingScreen";
import { Alert } from "@cloudscape-design/components";

import { GlobalHeader } from "./../common/GlobalHeader";

import { Header } from "./../authenticator/Header";
import { Footer } from "./../authenticator/Footer";
import { SignInHeader } from "./../authenticator/SignInHeader";
import { SignInFooter } from "./../authenticator/SignInFooter";
import styles from "./loginbox.module.css";
import "@aws-amplify/ui-react/styles.css";

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
     * The ApiGatewayV2 HttpApi to attach the lambda
     */
    api: string;
    /**
     * region
     */
    region: string;
    /**
     * The Cognito UserPoolId to authenticate users in the front-end
     */
    cognitoUserPoolId: string;
    /**
     * The Cognito AppClientId to authenticate users in the front-end
     */
    cognitoAppClientId: string;
    /**
     * The Cognito IdentityPoolId to authenticate users in the front-end
     */
    cognitoIdentityPoolId: string;

    /**
     * Additional configuration needed for cognito federated auth
     */
    cognitoFederatedConfig?: AmplifyConfigFederatedIdentityProps;

    /**
     * External OAUTH IDP URL Configuration
     */
    externalOAuthIdpURL?: string;

    /**
     * External OAUTH IDP ClientID Configuration
     */
    externalOAuthIdpClientId?: string;

    // /**
    //  * External OAUTH IDP ClientSecret Configuration
    //  */
    // externalOAuthIdpClientSecret?: string;

    /**
     * External OAUTH IDP Scope Configuration
     */
    externalOAuthIdpScope?: string;

    /**
     * External OAUTH IDP Token Endpoint Configuration
     */
    externalOAuthIdpTokenEndpoint?: string;

    /**
     * External OAUTH IDP Authorization Endpoint Configuration
     */
    externalOAuthIdpAuthorizationEndpoint?: string;

    /**
     * External OAUTH IDP Discovery Endpoint Configuration
     */
    externalOAuthIdpDiscoveryEndpoint?: string;

    /**
     * S3 Asset bucket
     */
    bucket?: string;

    /**
     * VAMS Features that are enabled
     */
    featuresEnabled?: string;

    /**
     * VAMS Backend Stackname
     */
    stackName: string;

    /**
     * Content Security Policy to apply (generally for ALB deployment where CSP is not injected)
     */
    contentSecurityPolicy?: string;

    /**
     * HTML banner message to be displayed at the top of all web UI pages
     */
    bannerHtmlMessage?: string;
}

function configureAmplify(config: Config, setAmpInit: (x: boolean) => void) {
    //console.log('configureAmplify', config, vamsConfig);

    let api_path = vamsConfig.DEV_API_ENDPOINT === "" ? config.api : vamsConfig.DEV_API_ENDPOINT;

    //if API path doesn't end in a /, add one
    if (api_path != undefined && api_path.length > 0 && api_path[api_path.length - 1] !== "/") {
        api_path = api_path + "/";
    }

    localStorage.setItem("api_path", api_path);
    //console.log('apiPath', localStorage.getItem('api_path'));

    console.log("DISABLE COGNITO ENV", window.DISABLE_COGNITO);
    console.log("COGNITO_FEDERATED ENV", window.COGNITO_FEDERATED);

    Amplify.configure({
        Auth: {
            mandatorySignIn: window["DISABLE_COGNITO"] ? false : true,
            region: config.region,
            userPoolId: window.DISABLE_COGNITO ? "XX-XXXX-X_abcd1234" : config.cognitoUserPoolId,
            userPoolWebClientId: window.DISABLE_COGNITO ? "1" : config.cognitoAppClientId,
            identityPoolId: window.DISABLE_COGNITO ? undefined : config.cognitoIdentityPoolId,
            cookieStorage: {
                domain: " ", // process.env.REACT_APP_COOKIE_DOMAIN, // Use a single space ' ' for host-only cookies
                expires: null, // null means session cookies
                path: "/",
                secure: vamsConfig.DEV_API_ENDPOINT === "" ? true : false, // for developing on localhost over http: set to false
                sameSite: "lax",
            },
            oauth: {
                domain: config.cognitoFederatedConfig?.customCognitoAuthDomain,
                scope: ["openid", "email", "profile"], //  process.env.REACT_APP_USER_POOL_SCOPES.split(','),
                redirectSignIn: window.location.origin, // config.cognitoFederatedConfig?.redirectSignIn,
                redirectSignOut: window.location.origin, // config.cognitoFederatedConfig?.redirectSignOut,
                responseType: "code",
            },
        },
        Storage: {
            region: config.region,
            identityPoolId: window.DISABLE_COGNITO ? undefined : config.cognitoIdentityPoolId,
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
                    endpoint: api_path,
                    region: config.region,
                    custom_header: async () => {
                        if (window.DISABLE_COGNITO) {
                            const accessToken = localStorage.getItem("idp_access_token");
                            return { Authorization: `Bearer ${accessToken}` };
                        } else {
                            return {
                                Authorization: `Bearer ${(await AmplifyAuth.currentSession())
                                    .getAccessToken()
                                    .getJwtToken()}`,
                            };
                        }
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
                //     items: ['vams-index'], // REQUIRED - Amazon Location Service Place Index name
                //     default: 'vams-index', // REQUIRED - Amazon Location Service Place Index name to set as default
                // },
                // geofenceCollections: {
                //     items: ['XXXXXXXXX', 'XXXXXXXXX'], // REQUIRED - Amazon Location Service Geofence Collection name
                //     default: 'XXXXXXXXX', // REQUIRED - Amazon Location Service Geofence Collection name to set as default
                // },
                region: config.region, // REQUIRED - Amazon Location Service Region
            },
        },
    });

    setAmpInit(true);
}

type AuthProps = {};

//Cognito components
const CenteredBox: React.FC<PropsWithChildren<{}>> = ({ children }) => {
    return (
        <div className={styles.container}>
            <div className={styles.centeredBox}>{children}</div>
        </div>
    );
};

const cognitoAuthenticatorComponents = {
    Header,
    SignIn: {
        Header: SignInHeader,
        Footer: SignInFooter,
    },
    Footer,
};

interface CognitoFederatedLoginProps {
    onLogin: () => void;
}

const FedLoginBox: React.FC<CognitoFederatedLoginProps> = ({ onLogin }) => {
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

//External OAUTH components
let oauth2Client: OAuth2Client;

const Auth: React.FC<AuthProps> = (props) => {
    //External Oauth Configuration Function
    function configureOAuthClient(config: Config) {
        if (
            config.externalOAuthIdpURL &&
            config.externalOAuthIdpClientId &&
            config.externalOAuthIdpTokenEndpoint &&
            config.externalOAuthIdpAuthorizationEndpoint &&
            config.externalOAuthIdpDiscoveryEndpoint &&
            config.externalOAuthIdpScope
        ) {
            oauth2Client = new OAuth2Client({
                server: config.externalOAuthIdpURL,
                clientId: config.externalOAuthIdpClientId,
                tokenEndpoint: config.externalOAuthIdpTokenEndpoint,
                authorizationEndpoint: config.externalOAuthIdpAuthorizationEndpoint,
                discoveryEndpoint: config.externalOAuthIdpDiscoveryEndpoint,
            });
        } else {
            localStorage.setItem(
                "auth_error",
                "Failed to configure OAuthClient. Missing externalOAuthIdp values in config."
            );
            setauthError(localStorage.getItem("auth_error"));
        }
    }

    const [state, setState] = useState<any>({
        email: undefined,
        username: undefined,
        authenticated: false,
    });

    const [config, setConfig] = useState(Cache.getItem("config"));
    let [authError, setauthError] = useState<string | null>(() =>
        localStorage.getItem("auth_error")
    );

    const [ampInit, setAmpInit] = useState(false);

    const [isLoggedIn, setisLoggedIn] = useState(false);
    const [isLoading, setIsLoading] = useState(false);

    const accessToken = localStorage.getItem("idp_access_token");

    //Both Affect
    //Fetch && Setup Initial global configurations
    useEffect(() => {
        if (config) {
            //Set global variables for cognito mode or external OAUTH.
            //If config.config.cognitoUserPoolId is undefined or empty, then disable cognito and setup external oauth
            if (config.cognitoUserPoolId === undefined || config.cognitoUserPoolId === "") {
                window.DISABLE_COGNITO = true;
                configureOAuthClient(config);
            } else {
                window.DISABLE_COGNITO = false;
            }

            if (config.cognitoFederatedConfig === undefined) {
                window.COGNITO_FEDERATED = false;
            } else {
                window.COGNITO_FEDERATED = true;
            }

            //Configure Amplify
            configureAmplify(config, setAmpInit);
        } else {
            getAmplifyConfig().then(async (config) => {
                Cache.setItem("config", config);
                setConfig(config);
            });
        }
    }, [ampInit, config, setConfig]);

    //Both Effect
    //Auth session checks
    useEffect(() => {
        if (!ampInit) return;

        AmplifyAuth.currentAuthenticatedUser()
            .then((currentUser) => {
                if (!localStorage.getItem("user")) {
                    // No valid user session state
                    AmplifyAuth.signOut()
                        .then(() => {
                            console.log("User signed out - invalid session state");
                        })
                        .catch((error) => {
                            console.log("User sign out error - invalid session state", error);
                        });
                    setisLoggedIn(false);
                    resetSession();
                }
            })
            .catch((error) => {});
    }, [ampInit]);

    //External OAUTH Effect
    //Set user session once oauth access key login confirmed, start refresh interval
    useEffect(() => {
        if (window.DISABLE_COGNITO === true) {
            // Try set logged in
            if (accessToken && accessToken.length !== 0) {
                // Have access token, decoding...
                const jwt = parseJwt(accessToken);
                setIsLoading(true);

                // validating access token works to make api requests...
                accessTokenValid()
                    .then((valid) => {
                        if (!valid) {
                            // invalid access token, setisLoggedIn, false
                            setisLoggedIn(false);
                            signOutWithError("auth_error", "Access token invalid");

                            return;
                        }

                        // valid access token, setisLoggedIn, true
                        setisLoggedIn(true);
                        localStorage.setItem("user", JSON.stringify({ username: jwt.sub }));

                        // @ts-expect-error
                        AmplifyAuth.setUserSession({ username: jwt.sub }, accessToken);
                        Hub.dispatch("auth", {
                            event: "signIn",
                            data: { username: jwt.sub },
                            message: "User was signed in",
                        });
                        clearPreviousLoginErrors(); // On successful login, remove old error keys stored in local storage
                        setIsLoading(false);
                    })
                    .catch(() => {
                        // invalid access token, setisLoggedIn, false
                        setisLoggedIn(false);
                        signOutWithError("auth_error", "Access token invalid");
                    });
            } else {
                // no access token, setisLoggedIn, false)
                setisLoggedIn(false);
                resetSession();
            }
        }
    }, [accessToken]);

    //Cognito Effect
    // Sets the user on login/logout (Authenticator UI Callbacks)
    useEffect(() => {
        if (!ampInit) return;

        if (window.DISABLE_COGNITO === false) {
            const amplifyHubLIstener = Hub.listen("auth", ({ payload: { event, data } }) => {
                switch (event) {
                    case "signIn":
                        localStorage.setItem("user", JSON.stringify({ username: data.username }));
                        setisLoggedIn(true);
                        break;
                    case "signOut":
                        setisLoggedIn(false);
                        resetSession();
                        break;
                }
            });

            //Check/set for being logged for subsequent page loads
            AmplifyAuth.currentAuthenticatedUser()
                .then((currentUser) => {
                    localStorage.setItem(
                        "user",
                        JSON.stringify({ username: currentUser.getUsername() })
                    );
                    setisLoggedIn(true);
                    console.log("Cognito Page Load - Login set for Page Load");
                })
                .catch((error) =>
                    console.log("Cognito Page Load - Not signed in... sending to login - ", error)
                );

            return amplifyHubLIstener;
        }
    }, [ampInit]);

    //External OAUTH Effect
    //Try to get access token at this point after redirect
    useEffect(() => {
        if (window.DISABLE_COGNITO === true) {
            // Try request access token
            if (accessToken) {
                //already have access token, accessToken
                return;
            }

            if (!localStorage.getItem("auth_state") || !localStorage.getItem("code_verifier")) {
                // no auth_state or code_verifier in local storage
                return;
            }

            // request access token
            setIsLoading(true);
            const redirectUri = window.location.href.split(/[?#]/)[0];

            oauth2Client.authorizationCode
                .getTokenFromCodeRedirect(document.location.toString(), {
                    redirectUri,
                    state: localStorage.getItem("auth_state")!,
                    codeVerifier: localStorage.getItem("code_verifier")!,
                })
                .then((oauth2Token) => {
                    setOauth2Token(oauth2Token);
                    document.location = localStorage.getItem("auth_state")!;
                })
                .catch((error) => {
                    console.error(error);
                    signOutWithError("auth_error", "Failed to get access token"); // Catching error when redirect code is missing and start new sign in process.
                });
        }
    }, [accessToken]);

    //Both Effect
    //Once logged in, get/set other configuration and profile information
    useEffect(() => {
        //Secure Config Fetch
        if (config && (!config.bucket || !config.featuresEnabled) && isLoggedIn) {
            getSecureConfig().then((value) => {
                config.bucket = value.bucket;
                config.featuresEnabled = value.featuresEnabled;
                Cache.setItem("config", config);
                setConfig(config);
            });
            console.log("Fetched secure config");
        }

        //Hit login profile end-point to fetch and update latest backend login profiles
        //This could also update roles behind the scenes if configured to synchronize with external systems
        let loginProfile = Cache.getItem("loginProfile");
        if (isLoggedIn && !loginProfile) {
            const user = JSON.parse(localStorage.getItem("user")!);
            API.post("api", `auth/loginProfile/${user.username}`, {}).then((value) => {
                loginProfile = {};
                loginProfile.userId = value.message.Items[0].userId;
                loginProfile.email = value.message.Items[0].email;
                Cache.setItem("loginProfile", loginProfile);
            });
            console.log("Pinged LoginProfile API");
        }
    }, [config, isLoggedIn]);

    //External OAUTH Function for handling sign-in
    const handleExternalOauthSignIn = async () => {
        // Sign in
        setIsLoading(true);

        const accessToken = localStorage.getItem("idp_access_token");

        if (!accessToken) {
            // No access token, will start oauth2 flow'

            // Use the URL as the oauth state.
            localStorage.setItem("auth_state", document.location.href);

            // This generates a security code that must be passed to the various steps.
            // This is used for 'PKCE' which is an advanced security feature.
            const codeVerifier = await generateCodeVerifier();
            localStorage.setItem("code_verifier", codeVerifier);

            try {
                if (config && config.externalOAuthIdpScope) {
                    const authorizeUri = await oauth2Client.authorizationCode.getAuthorizeUri({
                        redirectUri: window.location.href,
                        state: document.location.href,
                        codeVerifier,
                        scope: [config.externalOAuthIdpScope],
                    });

                    document.location = authorizeUri;
                } else {
                    localStorage.setItem(
                        "auth_error",
                        "Failed to initialize authorization flow. Missing scope in config."
                    );
                    setauthError(localStorage.getItem("auth_error"));
                }
            } catch (error) {
                localStorage.setItem("auth_error", "Failed to initialize authorization flow.");
                setauthError(localStorage.getItem("auth_error"));
            }
        }
    };

    //Both Auth Effect
    //Check access token validity, expirations, and refreshs
    useEffect(() => {
        if (!ampInit || !localStorage.getItem("user")) return;

        // Trying to set amplify session
        const currentUser = localStorage.getItem("user")
            ? JSON.parse(localStorage.getItem("user")!)
            : undefined;

        if (!currentUser) {
            setState({ authenticated: false });

            return;
        }

        if (window.DISABLE_COGNITO === false) {
            //Cognito Checks
            AmplifyAuth.currentSession()
                .then((user) => {
                    setState({
                        email: user.getIdToken().decodePayload().sub,
                        username: user.getIdToken().decodePayload().sub,
                        authenticated: true,
                    });
                })
                .catch((e) => {
                    setState({ authenticated: false });
                });

            // Schedule check and refresh (when needed) of JWT's every 5 min:
            const i = setInterval(() => AmplifyAuth.currentSession(), 5 * 60 * 1000);
            return () => clearInterval(i);
        } else {
            //External Oauth checks
            AmplifyAuth.currentSession()
                .then((user) => {
                    setState({
                        email: currentUser.username,
                        authenticated: true,
                    });
                })
                .catch((e) => {
                    // Check if the access token has expired and initiate the oauth flow
                    const accessToken = localStorage.getItem("idp_access_token");
                    if (accessToken) {
                        const decodedToken = parseJwt(accessToken);
                        const expirationTime = decodedToken.exp * 1000; // the 'exp' property is in seconds, the 'expiresAt' property is in milliseconds, so need to multiply by 1000 here

                        if (Date.now() > expirationTime) {
                            // Access Token Expired - Starting New Session
                            handleExternalOauthSignIn();
                        } else {
                            // Access Token Not Expired - Starting Refresh Interval
                            startAccessTokenRefreshInterval();
                        }
                    } else {
                        setState({ authenticated: false });
                    }
                });
        }
    }, [ampInit]);

    //Initial loading screen when going through config init
    if (config === null || !ampInit) {
        return (
            <CenteredBox>
                <p className="explanation">One moment please ...</p>
            </CenteredBox>
        );
    }

    //Show loading after login (mostly for External OAUTH Process)
    if (isLoading) {
        return <LoadingScreen />;
    }

    //External OAUTH Login Page Return
    if (window.DISABLE_COGNITO === true && !isLoggedIn && ampInit) {
        return (
            <>
                <GlobalHeader authorizationHeader={true} />
                <div
                    style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                    }}
                >
                    <div className={styles.container}>
                        <div className={styles.centeredBox}>
                            <Heading level={3}>
                                <img
                                    style={{ width: "100%" }}
                                    src={logoDarkImageSrc}
                                    alt="Visual Asset Management System Logo"
                                />
                            </Heading>
                            <Button
                                variant="primary"
                                className={styles.button}
                                onClick={handleExternalOauthSignIn}
                            >
                                Login
                            </Button>
                        </div>
                        {authError ? (
                            <div className={styles.alertError}>
                                <Alert type="error" statusIconAriaLabel="Error" header="Error">
                                    Failed to login or session timed out. Please try again.
                                </Alert>
                            </div>
                        ) : null}
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
                </div>
            </>
        );
    }

    //Cognito Login Page Return
    if (window.DISABLE_COGNITO === false && !isLoggedIn && ampInit) {
        if (window.COGNITO_FEDERATED === false) {
            //Non-federated login
            return (
                <>
                    <GlobalHeader authorizationHeader={true} />
                    <Authenticator
                        components={cognitoAuthenticatorComponents}
                        loginMechanisms={["username"]}
                        hideSignUp={true}
                    />
                </>
            );
        } else {
            //Federated Login
            return (
                <>
                    <GlobalHeader authorizationHeader={true} />
                    <FedLoginBox
                        onLogin={() =>
                            AmplifyAuth.federatedSignIn({
                                customProvider:
                                    config.cognitoFederatedConfig
                                        ?.customFederatedIdentityProviderName,
                            })
                        }
                    />
                </>
            );
        }
    }

    //Show rest of app, we are all logged in.
    if (isLoggedIn && ampInit) {
        return (
            <>
                <GlobalHeader authorizationHeader={false} />
                <Suspense fallback={<LoadingScreen />}>{props.children}</Suspense>
            </>
        );
    }

    //Blank return if other options not available (fallback)
    console.log("Auth.tsx Error - No Valid Workflow Detected");
    return <></>;
};

/////////////////////////////////////////////////
//External OAUTH Helper Functions
const parseJwt = (
    accessToken: string
): {
    exp: number;
    sub: string;
} => {
    var base64Url = accessToken.split(".")[1];
    var base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
    var jsonPayload = decodeURIComponent(
        window
            .atob(base64)
            .split("")
            .map(function (c) {
                return "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2);
            })
            .join("")
    );

    return JSON.parse(jsonPayload);
};

const accessTokenValid = async (): Promise<boolean> => {
    let allRoutes = [];
    for (let route of routeTable) {
        if (route.path) {
            allRoutes.push({
                method: "GET",
                route__path: route.path,
            });
        }
    }

    try {
        const canPostWebRoutes = await webRoutes({ routes: allRoutes });

        if (
            canPostWebRoutes?.message === "Unauthorized" ||
            (Array.isArray(canPostWebRoutes) && canPostWebRoutes[0] === false)
        ) {
            // postRoutes returns either an object with a message if it is successful or unauthorized or an array where the first item is false for other errors
            // canPostRoutes[0] is undefined if it is successful, so checking if it's an array then doing the strict equality check for false
            return false;
        }

        startAccessTokenRefreshInterval();
        return true;
    } catch (error) {
        return false;
    }
};

const startAccessTokenRefreshInterval = () => {
    // Schedule check and refresh (when needed) of JWT's every 5 min:
    const i = setInterval(async () => {
        // attempt of refresh token
        const accessToken = localStorage.getItem("idp_access_token");
        const oauth2Token = localStorage.getItem("oauth2_token");

        if (!accessToken || !oauth2Token) {
            // no access token found when trying to refresh
            signOutWithError("auth_error", "No access token found when refreshing");
            return;
        }

        try {
            const oauth2TokenResponse = await oauth2Client.refreshToken(
                JSON.parse(oauth2Token) as OAuth2Token
            );
            setOauth2Token(oauth2TokenResponse);
        } catch (error) {
            clearInterval(i);
            console.error("error: ", error);
            signOutWithError("auth_error", "Failed to refresh access token.");
        }
    }, 5 * 60 * 1000);
};

const signOutWithError = (key: string = "auth_error", value: string = "Unauthorized") => {
    localStorage.clear();
    localStorage.setItem(key, value);
    window.location.href = "/";
};

const resetSession = () => {
    localStorage.removeItem("idp_access_token");
    localStorage.removeItem("oauth2_token");
    localStorage.removeItem("user");
};

const setOauth2Token = (oauth2Token: OAuth2Token) => {
    localStorage.setItem("oauth2_token", JSON.stringify(oauth2Token));
    localStorage.setItem("idp_access_token", oauth2Token.accessToken);
};

function clearPreviousLoginErrors() {
    localStorage.removeItem("auth_error");
}

export default Auth;
