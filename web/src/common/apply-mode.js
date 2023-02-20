/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { applyMode, applyDensity } from "@cloudscape-design/global-styles";
import * as localStorage from "./localStorage";
import { createPropertyStorage } from "./createPropertyStorage";

import "@cloudscape-design/global-styles/index.css";

export const densityLocalStorageKey = "Awsui-Density-Preference";
export const densityStorage = createPropertyStorage(
  densityLocalStorageKey,
  localStorage
);

window.addEventListener("load", function () {
  try {
    const urlParams = new URLSearchParams(window.location.search);

    const mode = urlParams.get("awsui-mode");
    mode !== null ? applyMode(mode) : null;

    const density = urlParams.get("awsui-density");
    density !== null ? applyDensity(density) : null;
  } catch (e) {
    /*URLSearchParams is not supported by some browsers, ignore this*/
  }
});

function setSearchParam(key, value) {
  const url = new URL(window.location.href);
  url.searchParams.set(key, value);
  window.history.replaceState(null, null, url);
}

export function updateMode(mode) {
  const [lightMode, darkMode] = document.getElementsByClassName("mode");

  if (mode === "dark") {
    darkMode.classList.add("selected");
    lightMode.classList.remove("selected");
  } else {
    lightMode.classList.add("selected");
    darkMode.classList.remove("selected");
  }
  setSearchParam("awsui-mode", mode);
  applyMode(mode);
}

export function updateDensity(density) {
  if (!density) {
    return;
  }
  const [comfortable, compact] = document.getElementsByClassName("density");

  if (density === "compact") {
    compact.classList.add("selected");
    comfortable.classList.remove("selected");
  } else {
    comfortable.classList.add("selected");
    compact.classList.remove("selected");
  }

  setSearchParam("awsui-density", density);
  densityStorage.save(density);
  applyDensity(density);
}
