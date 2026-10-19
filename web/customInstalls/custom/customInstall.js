/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here
// do not conflict on upgrade.
//
// Install or bundle steps for the viewers a fork adds (web/src/visualizerPlugin/viewers/custom/).
// `npm install` in web/ runs this after the core viewer installs (package.json "postinstall"), so a
// fork adds its steps here instead of editing package.json scripts. The scripts beside this
// directory (for example ../vntana/vntanaInstall.js) show the pattern: install into this directory,
// bundle, and copy the output under web/public/viewers/.
