/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom";
import { Cache } from "aws-amplify";
import { Authenticator } from "@aws-amplify/ui-react";
import { BrowserRouter } from "react-router-dom";
import { TopNavigation } from "@cloudscape-design/components";
import { AppRoutes } from "./routes";
import logoWhite from "./resources/img/logo_white.png";
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
    setNavigationOpen(cachedNavigationOpen);
  }, [null]);

  useEffect(() => {
    const cachedNavigationOpen = Cache.getItem("navigationOpen");
    if (navigationOpen !== cachedNavigationOpen) {
      Cache.setItem("navigationOpen", navigationOpen);
    }
  }, [navigationOpen]);

  return (
    <Authenticator components={components} loginMechanisms={["email"]}>
      {({ signOut, user }) => (
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
                  text: user.username || user.email,
                  description: user.username,
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
              setNavigationOpen={setNavigationOpen}
            />
          </BrowserRouter>
        </>
      )}
    </Authenticator>
  );
}

export default App;
