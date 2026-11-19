# Audit Log Search

Audit Log Search finds records in the audit logs of your VAMS deployment from the web interface. Use it to answer who did what to which data, when a request was denied and why, and what else happened during one request. For the compliance history of schemas, quarantine and cascades, use the **Compliance Audit Log** page instead. The two pages read different trails.

**Where to find it:** **Admin - Auth** > **Audit Log Search** in the left sidebar. The item appears only when Audit Log Search is enabled on your deployment and your roles allow the page. Administrators have it by default. For other roles, import the Audit Reader permission template (see [Permissions](permissions.md)) or add an `auditLog` constraint to a role.

**Availability.** Amazon CloudWatch Logs Insights is not currently supported in the AWS European Sovereign Cloud, so Audit Log Search is off there by default, and the deployment configuration rejects turning it on. Audit recording is unaffected: VAMS keeps recording audit events, so the logs stay available in Amazon CloudWatch Logs for your own tools. Everywhere else it is on by default, and an administrator can turn it off with `app.auditLogging.search.enabled`. When it is off, the menu item and the link from the Compliance Audit Log page are hidden, a direct link to the page shows "Audit Log Search is not enabled on this deployment", and the `vamscli audit` commands and the MCP audit tools say the same.

## What you can search

Audit records are grouped into audit log groups, for example Actions and Authorization. A role grants access per group, so the groups you can search are the ones your roles allow. The page lists no other group and never names one you cannot search. If your roles allow no group, the page says so.

The Authentication group exists but is empty: VAMS does not record sign-ins itself. Amazon Cognito or your identity provider records sign-ins. The API authorizer's check of the token on each API request, allowed and denied, is in the Authorization group. The page shows a short note when you choose the Authentication group.

The log groups, actions and entity types you can choose come from the deployment itself, so a group or action added later appears without an update of the web interface.

A record becomes searchable about a minute after the action it describes, because CloudWatch Logs indexes it first. A search run right after an action may not show that action yet.

## Run a search

1. Open **Audit Log Search**.
2. Add filters in the **Filters** box. Choose a property, then a value. Values of one property are alternatives (user A or user B). Different properties all apply.
3. Choose a **Time range**. Times are in UTC. The range can span at most the number of days the deployment allows (31 by default).
4. Choose **Search**.

![Audit Log Search results with a record's details open](/img/audit_log_search_results_20261117_v2.7.png)

| Filter          | Matches                                                                           |
| --------------- | --------------------------------------------------------------------------------- |
| **Log group**   | Only the chosen groups. With none chosen, every group you may search.             |
| **Action**      | The action name, for example `assetUpdate`.                                       |
| **Entity type** | The kind of thing the action touched, for example `asset`.                        |
| **Entity ID**   | The id of that thing.                                                             |
| **User**        | The user who acted, the user acted for, or the API key's owner.                   |
| **API key ID**  | Requests made with that API key.                                                  |
| **Database ID** | Records for that database.                                                        |
| **Asset ID**    | Records for that asset.                                                           |
| **Outcome**     | `success`, `failed` or `denied`.                                                  |
| **Request ID**  | Every record written for one request.                                             |
| **Contains**    | Text inside the record's identifiers, request parameters and details (see below). |

**Contains.** The text is searched in a record's identifiers, request parameters and details. It is not searched in the values a record changed or in its previous values, so an asset name on a create record, which is a changed value, is not found; filter by **Entity ID** or **Asset ID** instead. A term holds 3 to 64 letters, digits, spaces and the characters `. _ : @ + / -`, up to 3 terms (the deployment may lower these limits), and every term must match. The case does not matter. Values the viewer hides, such as secrets, are never searched, so a term cannot be used to guess them. The page tells you before you search when a term is not accepted.

**Permission-probe records.** The web interface asks the API what you may do to decide what to show. Those checks are recorded as denials marked as probes. **Hide permission-probe records** (on by default) leaves them out so real denials stand out. A probe that asked which audit log groups you may search is one row labelled **permission probe (summary)**; it never names a group.

**A link restores a search.** The filters are kept in the page address. Opening a shared link restores the filters and the time range, and never runs the search. Choose **Search** to run it.

## While a search runs

A search reads CloudWatch Logs and can take several seconds. The page waits on the server and shows how many records it has scanned so far, on a line under the **Search** button (the buttons and fields do not move when a search starts). Choose the info icons beside **Filters**, **Time range** and **Search** for the rules of filters, the time range and the search. It shows no count of matches, because the records are checked again on the server when the search finishes. Records appear when the search finishes. Choose **Stop** to end the search early (also right after you chose Search: the search is cancelled as soon as the service has started it); a stopped search shows no records and the table says the search was stopped, so narrow the time range or the filters and search again. A search that passes the deployment's time limit stops with a message that asks you to narrow the range or the filters.

When the service is busy or you already run the most searches allowed at once, the page says so. Wait a moment and search again.

## Read the results

The table shows one row per record, on one line (long values are cut and shown in full when you point at them or open the record; the preferences button turns on wrapped lines): time (your local time), log group, action, outcome, user, database, asset, entity and a one-line summary. **Order** chooses newest or oldest first for the whole result, by the exact time of each record. Time is shown in one format for every record, whichever format it was written in. Column headers sort the page shown only. Use the preferences button to choose the columns.

Warnings and errors appear as notifications in the top right corner of the page. Choose the close button to dismiss one. The warnings about an incomplete or partial result stay until you dismiss them, you start another search, the results are cleared (for example by opening a link with other filters) or you leave the page; they close by themselves after 10 minutes at the latest. Errors close after a few seconds.

A problem with a field you filled in (a **contains** term that is too short, a log group you may not search) is not a notification. It is shown under the filter, **Search** stays disabled until you fix it, and it does not disappear on its own, so you see what to correct.

The notes below are information: choose the info icon beside **Export** to read them.

The notifications and notes explain what the result may not show:

-   The search reached its record limit, so more matching records may exist. Narrow the time range. When a search is large, the notice is not proof that a result without it is complete: narrow the time range whenever the table holds as many rows as the record limit.
-   Checking the records took longer than the time allowed, so only part of the result is shown.
-   A value you gave could not be used to narrow the scan, so more of the log was read. The result is still exact.
-   Some records hold values that were cut short when they were written.

Select a row to open **Record details**: the time in local time and UTC, the user, the request, the entity and the sections that hold the record's request parameters, details, changes and previous values. A record the viewer could not read in full says so and shows only the masked start of the record. Values are shown as text. Control and direction-changing characters are shown as `\uXXXX`.

**Show records of this request** starts a new search for every record written for the same request. **Back** in the browser returns to the earlier search.

![Record details panel](/img/audit_log_search_detail_20261117_v2.7.png)

## Export

Choose **Export** after a search finishes to download every record the search found, not only the page shown, as CSV or NDJSON (one JSON record per line). The dialog says how many records the file holds. In a CSV file, a cell that starts with `=`, `+`, `-` or `@` begins with an apostrophe so a spreadsheet does not run it as a formula.

## Messages

| Message                                              | Meaning                                                                        |
| ---------------------------------------------------- | ------------------------------------------------------------------------------ |
| You do not have access to audit logs.                | Your roles allow no audit log group, or the route. The message names no group. |
| No audit logs available to you                       | The page is allowed, but your roles hold no audit log group.                   |
| These results expired. Run the search again.         | The search is no longer available (results are kept for a limited time).       |
| Too many audit searches are running. Try again later | The deployment's limit of searches at once is reached.                         |

Every search and every export you run is itself recorded in the Actions audit log group, with the filters you used. The text of a **Contains** term is recorded only as its count, length and a hash.
