/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from "react";
import ReactDOM from "react-dom";
import { Cache, Auth as AmplifyAuth} from "aws-amplify";
import { HashRouter } from "react-router-dom";
import { TopNavigation } from "@cloudscape-design/components";
import { AppRoutes } from "./routes";
import logoWhite from "./resources/img/logo_white.png";
import "@aws-amplify/ui-react/styles.css";

const HeaderPortal = ({ children }) => {
    const domNode = document.querySelector("#headerWrapper");
    return ReactDOM.createPortal(children, domNode);
};

function App() {
    const [navigationOpen, setNavigationOpen] = useState(true);

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
    localStorage.setItem("email", user?.username);

    const signOut = () => {
        localStorage.clear();
        AmplifyAuth.signOut().then(() => {
            console.log("User signed out - signout button clicked");
        }).catch((error) => {
            console.log("User sign out error - signout button clicked", error);
        })

        window.location.href = '/';
    }

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
