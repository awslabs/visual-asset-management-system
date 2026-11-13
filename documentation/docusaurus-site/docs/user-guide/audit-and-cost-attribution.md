# Audit and Cost Attribution

VAMS writes an audit record for every action a user, an API key or the system takes: each create, update and delete, each read, search, download and export, and each pipeline run. You can use these records to answer who did what to which data, and to attribute storage, transfer and pipeline use to users, databases and assets.

One exception remains for now: the reads of the API key, role, user role, constraint and Amazon Cognito user pages are not yet recorded, and changes to those items keep their earlier record format. Both move to the format described here with [#1434](https://github.com/awslabs/visual-asset-management-system/issues/1434).

---

## What is recorded

Each action record names:

-   **Who** -- the user, how they signed in (Amazon Cognito, an external identity provider or an API key, with the key's ID), and for an action the system ran for a user (a pipeline run, a queued upload) the user it ran for.
-   **What** -- the action, such as `assetUpdate`, `fileDelete` or `search`, and its outcome: `success`, `failed` with a generic reason such as `notFound` or `validation`, or `denied`.
-   **Which data** -- the database, asset, file, version, pipeline, workflow or execution the action touched.
-   **How much** -- a size or count when VAMS already knows it: the bytes of a file written directly to the bucket, the number of files a folder operation or export touched, the number of items a list or search returned, and the duration of a pipeline run.

What a write wrote or changed is recorded with its new values, and with the values it replaced when VAMS had already read them. A read, search or download records what was asked, never what was returned. Credentials, tokens, keys and pipeline template bodies are never recorded.

:::note[Your administrator decides whether reads are recorded]
Reads are recorded by default. An administrator can turn read records off with the `app.auditLogging.recordReads` setting; writes, denials, failures, downloads and exports are recorded either way. See the [configuration reference](../deployment/configuration-reference.md#audit-logging-appauditlogging).
:::

---

## Where the records are

The records are written to the Amazon CloudWatch Logs audit log groups of the deployment, one group per kind of event (actions, file uploads, file downloads, authorization decisions, errors and changes to roles and access). Who can read them is decided by the AWS account's IAM permissions, not by VAMS roles. Records are kept for one year by default.

Ask your administrator for read access to the audit log groups, or for a report.

---

## Attributing cost

Common questions and how the records answer them:

| Question                                          | Records to use                                                                                      |
| ------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| How many actions does each user take?             | The actions log group, counted by user and action                                                   |
| Which databases and assets see the most activity? | The actions log group, counted by database and asset                                                |
| What does each API key or integration do?         | Records whose actor carries an API key ID                                                           |
| How much data lands in each database each day?    | The bytes on bucket sync records (files written directly to the bucket), summed by database and day |
| Who runs pipelines, and for how long?             | The `workflowExecutionEnd` records: the user the run was for, its outcome and its duration          |
| What fails, and why?                              | Records with `outcome` `failed`, counted by action and reason                                       |

Ready-to-run Amazon CloudWatch Logs Insights queries for each of these are in [Audit Logging](../developer/audit-logging.md#cost-attribution). For prices in your Region, use the [Amazon CloudWatch pricing page](https://aws.amazon.com/cloudwatch/pricing/) or the AWS Pricing Calculator.
