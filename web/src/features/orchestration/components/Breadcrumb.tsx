/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { Link } from "react-router-dom";

export interface Crumb {
    label: string;
    /** Link target (HashRouter path). Omit for the current (last) crumb. */
    to?: string;
}

interface BreadcrumbProps {
    items: Crumb[];
}

/**
 * A simple breadcrumb trail for the orchestration pages (list → create/edit). The last item is the
 * current page (rendered as plain text); earlier items link back (e.g. to the Workflows/Pipelines
 * list).
 */
// Mirrors the Cloudscape BreadcrumbGroup look used on the Cloudscape pages: 14px body text
// (font-size-body-m), muted color, "/" separators, link-blue anchors, current page in default text
// color. No Cloudscape dependency — plain elements styled to match so the page doesn't look out of
// place. Font size (text-sm = 14px) and the leading match the Cloudscape breadcrumb.
const Breadcrumb: React.FC<BreadcrumbProps> = ({ items }) => (
    <nav aria-label="Breadcrumb" className="text-sm leading-normal text-text-secondary">
        {/* list-none: Tailwind preflight is disabled in this module, so an <ol> would otherwise
            render default "1." markers. */}
        <ol className="flex flex-wrap items-center list-none p-0 m-0">
            {items.map((item, idx) => {
                const isLast = idx === items.length - 1;
                return (
                    <li key={idx} className="flex items-center">
                        {idx > 0 && (
                            <span aria-hidden className="mx-2 text-text-secondary">
                                /
                            </span>
                        )}
                        {item.to && !isLast ? (
                            <Link
                                to={item.to}
                                className="text-blue-600 dark:text-blue-400 hover:underline"
                            >
                                {item.label}
                            </Link>
                        ) : (
                            <span
                                className={isLast ? "text-text-primary" : undefined}
                                aria-current={isLast ? "page" : undefined}
                            >
                                {item.label}
                            </span>
                        )}
                    </li>
                );
            })}
        </ol>
    </nav>
);

export default Breadcrumb;
