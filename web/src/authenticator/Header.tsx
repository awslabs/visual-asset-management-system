/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import loginBgImageSrc from "../resources/img/login_bg.webp";
import styles from "../FedAuth/loginbox.module.css";

export function Header() {
    return (
        <>
            <div
                style={{
                    position: "fixed",
                    backgroundColor: "#16191f",
                    top: 0,
                    left: 0,
                    width: "100vw",
                    height: "100vh",
                    zIndex: "-200",
                }}
            />
            <img
                alt=""
                aria-hidden="true"
                src={loginBgImageSrc}
                className={styles.loginBackground}
            />
        </>
    );
}
