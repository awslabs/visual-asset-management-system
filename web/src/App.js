/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from "react";
import ReactDOM from "react-dom";
import { Cache, Hub } from "aws-amplify";
//import { Authenticator } from "@aws-amplify/ui-react";
import { HashRouter } from "react-router-dom";
import { TopNavigation } from "@cloudscape-design/components";
import { AppRoutes } from "./routes";
import logoWhite from "./resources/img/logo_white.png";
import "@aws-amplify/ui-react/styles.css";
import { Auth } from "@aws-amplify/auth";
import {default as vamsConfig} from "./config";

import { GlobalHeader } from "./common/GlobalHeader";
import { Header } from "./authenticator/Header";
import { Footer } from "./authenticator/Footer";
import { SignInHeader } from "./authenticator/SignInHeader";
import { SignInFooter } from "./authenticator/SignInFooter";

const components = {
    GlobalHeader,
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
    const [loginProfile, setLoginProfile] = useState(Cache.getItem("loginProfile"));

    const user = localStorage.getItem('user') ?
        JSON.parse(localStorage.getItem('user')) : undefined;

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

    console.log('current user is', user);

    const menuText = user?.username;

    const signOut = () => {
        localStorage.clear();
        window.location.href = '/';

        // TODO amplify clear session?
    }

    localStorage.setItem("email", user?.username);
    return (
        <>
                <GlobalHeader />
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
            <HashRouter>
                <AppRoutes
                    navigationOpen={navigationOpen}
                    user={user}
                    setNavigationOpen={setNavigationOpen}
                />
            </HashRouter>
        </>
    );
}

export default App;
