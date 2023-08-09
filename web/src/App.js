/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from "react";
import ReactDOM from "react-dom";
import { Cache } from "aws-amplify";
import { Authenticator } from "@aws-amplify/ui-react";
import { BrowserRouter } from "react-router-dom";
import { TopNavigation } from "@cloudscape-design/components";
import { AppRoutes } from "./routes";
import logoWhite from "./resources/veerum/img/logo_white.png";
import "@aws-amplify/ui-react/styles.css";

import { Header } from "./authenticator/Header";
import { Footer } from "./authenticator/Footer";
import { SignInHeader } from "./authenticator/SignInHeader";
import { SignInFooter } from "./authenticator/SignInFooter";

const components = {
    Header,
    SignIn: {
        Header: SignInHeader,
        Footer: SignInFooter,
    },
    Footer,
};

const HeaderPortal = ({ children }) => {
    const domNode = document.querySelector("#headerWrapper");
    return ReactDOM.createPortal(children, domNode);
};

function App() {
    const [navigationOpen, setNavigationOpen] = useState(true);

    useEffect(() => {
        const cachedNavigationOpen = Cache.getItem("navigationOpen");
        if (cachedNavigationOpen !== undefined && cachedNavigationOpen !== null) {
            setNavigationOpen(cachedNavigationOpen);
        }
    }, []);

    useEffect(() => {
        const cachedNavigationOpen = Cache.getItem("navigationOpen");
        if (
            navigationOpen !== cachedNavigationOpen &&
            cachedNavigationOpen !== undefined &&
            cachedNavigationOpen !== null
        ) {
            Cache.setItem("navigationOpen", navigationOpen);
            console.log("set navigation open in cache ", navigationOpen);
        }
    }, [navigationOpen]);

    return (
        <Authenticator components={components} loginMechanisms={["email"]} hideSignUp={true}>
            {({ signOut, user }) => {
                const menuText =
                    user.signInUserSession?.idToken?.payload?.name || user.username || user.email;
                return (
                    <>
                        <HeaderPortal>
                            <TopNavigation
                                identity={{
                                    href: "/",
                                    logo: {
                                        src: logoWhite,
                                        alt: "Visual Asset Management System",
                                    },
                                }}
                                utilities={[
                                    {
                                        type: "menu-dropdown",
                                        text: menuText,
                                        description: menuText,
                                        iconName: "user-profile",
                                        onItemClick: (e) => {
                                            if (e?.detail?.id === "signout") signOut();
                                        },
                                        items: [{ id: "signout", text: "Sign out" }],
                                    },
                                ]}
                                i18nStrings={{
                                    searchIconAriaLabel: "Search",
                                    searchDismissIconAriaLabel: "Close search",
                                    overflowMenuTriggerText: "More",
                                }}
                            />
                        </HeaderPortal>
                        <BrowserRouter>
                            <AppRoutes
                                navigationOpen={navigationOpen}
                                user={user}
                                setNavigationOpen={setNavigationOpen}
                            />
                        </BrowserRouter>
                    </>
                );
            }}
        </Authenticator>
    );
}

export default App;
