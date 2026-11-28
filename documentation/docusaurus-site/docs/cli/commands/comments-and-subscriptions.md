---
sidebar_label: Comments and Subscriptions
title: Comment and Subscription Commands
---

# Comment and Subscription Commands

Manage review comments on an asset, and the event subscriptions that notify users when an asset changes.

Two databases may hold assets with the same asset ID, so every comment and asset subscription command names the asset's database with `-d`, `--database-id`.

---

## Comment Commands

Comments belong to one asset and are addressed by database ID, asset ID, and comment ID. The API generates the comment ID when the comment is added; each comment also records the asset version it is about. A comment belongs to the user who created it, and only that user can change or delete it.

---

## comment list

List comments on an asset. By default this returns every comment on the asset across all of its versions; adding `-v`, `--asset-version-id` narrows the listing to one version.

```bash
vamscli comment list [OPTIONS]
```

| Option                     | Type    | Required | Description                               |
| -------------------------- | ------- | -------- | ----------------------------------------- |
| `-d`, `--database-id`      | TEXT    | Yes      | Database of the asset                     |
| `-a`, `--asset-id`         | TEXT    | Yes      | Asset to list comments for                |
| `-v`, `--asset-version-id` | TEXT    | No       | Restrict the listing to one asset version |
| `--page-size`              | INTEGER | No       | Number of items read per page             |
| `--max-items`              | INTEGER | No       | Maximum total items to return             |
| `--starting-token`         | TEXT    | No       | Token to resume from                      |
| `--show-archived`          | FLAG    | No       | Also find an archived asset               |
| `--json-output`            | FLAG    | No       | Output raw JSON response                  |

```bash
vamscli comment list -d my-database -a my-asset
vamscli comment list -d my-database -a my-asset -v 1
vamscli comment list -d my-database -a my-asset --max-items 50 --json-output
vamscli comment list -d my-database -a my-archived-asset --show-archived
```

:::note[The listing returns no continuation token]
`--page-size` and `--max-items` bound how much is read, and a token supplied to `--starting-token` is honoured, but the listing returns no token in its response. There is therefore no value to pass back to `--starting-token`, and a listing that stops at `--max-items` cannot be resumed from where it ended. Raise `--max-items` to read further.

Soft-deleted comments are not listed.
:::

---

## comment get

Get a single comment by database, asset, and comment ID.

```bash
vamscli comment get [OPTIONS]
```

| Option                | Type | Required | Description              |
| --------------------- | ---- | -------- | ------------------------ |
| `-d`, `--database-id` | TEXT | Yes      | Database of the asset    |
| `-a`, `--asset-id`    | TEXT | Yes      | Asset the comment is on  |
| `-c`, `--comment-id`  | TEXT | Yes      | Comment ID               |
| `--json-output`       | FLAG | No       | Output raw JSON response |

```bash
vamscli comment get -d my-database -a my-asset -c 8d65af7c-5412-404b-a494-fbd2cdb62442
```

---

## comment add

Add a comment about an asset version. The API generates the comment ID and the command reports it (`commentId` in the `--json-output` result), so a script can address the comment afterwards.

```bash
vamscli comment add [OPTIONS]
```

| Option                     | Type | Required | Description                        |
| -------------------------- | ---- | -------- | ---------------------------------- |
| `-d`, `--database-id`      | TEXT | Yes      | Database of the asset              |
| `-a`, `--asset-id`         | TEXT | Yes      | Asset to comment on                |
| `-v`, `--asset-version-id` | TEXT | Yes      | Asset version the comment is about |
| `-b`, `--comment-body`     | TEXT | Yes      | Text of the comment                |
| `--json-output`            | FLAG | No       | Output raw JSON response           |

```bash
vamscli comment add -d my-database -a my-asset -v 1 -b "Geometry looks correct at this revision."
vamscli comment add -d my-database -a my-asset -v 1 -b "Approved." --json-output
```

---

## comment update

Replace the body of an existing comment.

```bash
vamscli comment update [OPTIONS]
```

| Option                 | Type | Required | Description              |
| ---------------------- | ---- | -------- | ------------------------ |
| `-d`, `--database-id`  | TEXT | Yes      | Database of the asset    |
| `-a`, `--asset-id`     | TEXT | Yes      | Asset the comment is on  |
| `-c`, `--comment-id`   | TEXT | Yes      | Comment ID               |
| `-b`, `--comment-body` | TEXT | Yes      | Replacement comment text |
| `--json-output`        | FLAG | No       | Output raw JSON response |

```bash
vamscli comment update -d my-database -a my-asset -c 8d65af7c-5412-404b-a494-fbd2cdb62442 -b "Approved with notes."
```

:::note[Only the creator can update a comment]
The API compares the comment's recorded owner against the calling user and answers `403` when they differ. Administrative permission on the asset does not extend to editing another user's comment.
:::

---

## comment delete

Delete a comment.

```bash
vamscli comment delete [OPTIONS]
```

| Option                | Type | Required | Description              |
| --------------------- | ---- | -------- | ------------------------ |
| `-d`, `--database-id` | TEXT | Yes      | Database of the asset    |
| `-a`, `--asset-id`    | TEXT | Yes      | Asset the comment is on  |
| `-c`, `--comment-id`  | TEXT | Yes      | Comment ID               |
| `--confirm`           | FLAG | Yes      | Confirm the deletion     |
| `--json-output`       | FLAG | No       | Output raw JSON response |

```bash
vamscli comment delete -d my-database -a my-asset -c 8d65af7c-5412-404b-a494-fbd2cdb62442 --confirm
```

:::note[Creator-only, and a soft delete]
As with `comment update`, only the user who created the comment can delete it. The delete is a soft delete: the comment is marked deleted and drops out of the listing, and its record is retained.
:::

---

:::note[`--confirm` is required, not a prompt override]
The flag is the whole confirmation: there is no interactive prompt to skip. Without `--confirm` the command refuses and exits non-zero, reporting `Confirmation required`, and nothing is deleted. Supply the flag on every invocation, including interactive ones. This keeps a destructive command a single non-interactive call, which is what `file delete`, `metadata-schema delete`, `tag delete`, and `tag-type delete` also do.
:::

## Subscription Commands

A subscription notifies a set of subscribers when an event occurs on an entity. Each subscription is identified by the entity's database and ID together with the event name and entity name, which default to `Asset Version Change` and `Asset`.

---

## subscription list

List all subscriptions the caller can read. Each item carries the asset's `databaseId`.

```bash
vamscli subscription list [OPTIONS]
```

| Option             | Type    | Required | Description                   |
| ------------------ | ------- | -------- | ----------------------------- |
| `--page-size`      | INTEGER | No       | Number of items read per page |
| `--max-items`      | INTEGER | No       | Maximum total items to return |
| `--starting-token` | TEXT    | No       | Token to resume from          |
| `--json-output`    | FLAG    | No       | Output raw JSON response      |

```bash
vamscli subscription list
vamscli subscription list --max-items 100 --json-output
```

---

## subscription create

Create a subscription for one entity with one or more subscribers. Repeat `-s`, `--subscriber` to name several.

```bash
vamscli subscription create [OPTIONS]
```

| Option                | Type | Required | Description                                                  |
| --------------------- | ---- | -------- | ------------------------------------------------------------ |
| `-d`, `--database-id` | TEXT | Yes      | Database of the entity's asset                               |
| `-i`, `--entity-id`   | TEXT | Yes      | ID of the entity to subscribe to                             |
| `-s`, `--subscriber`  | TEXT | Yes      | User to subscribe; repeat the option for several subscribers |
| `--event-name`        | TEXT | No       | Event to subscribe to (default: `Asset Version Change`)      |
| `--entity-name`       | TEXT | No       | Entity type the ID refers to (default: `Asset`)              |
| `--json-output`       | FLAG | No       | Output raw JSON response                                     |

```bash
vamscli subscription create -d my-database -i my-asset -s reviewer@example.com
vamscli subscription create -d my-database -i my-asset -s reviewer@example.com -s lead@example.com
```

Each subscriber must have a usable email address on their user profile; a subscriber without one is rejected.

---

## subscription update

Set the subscriber list for a subscription.

```bash
vamscli subscription update [OPTIONS]
```

| Option                | Type | Required | Description                                                      |
| --------------------- | ---- | -------- | ---------------------------------------------------------------- |
| `-d`, `--database-id` | TEXT | Yes      | Database of the entity's asset                                   |
| `-i`, `--entity-id`   | TEXT | Yes      | ID of the subscribed entity                                      |
| `-s`, `--subscriber`  | TEXT | Yes      | Subscriber to keep; repeat the option for several subscribers    |
| `--event-name`        | TEXT | No       | Event name of the subscription (default: `Asset Version Change`) |
| `--entity-name`       | TEXT | No       | Entity type the ID refers to (default: `Asset`)                  |
| `--json-output`       | FLAG | No       | Output raw JSON response                                         |

```bash
vamscli subscription update -d my-database -i my-asset -s reviewer@example.com -s lead@example.com
```

:::warning[The subscriber list is replaced, not extended]
`--subscriber` supplies the complete list the subscription will have afterwards. A subscriber who is currently subscribed and is not named in the call is unsubscribed: the confirmed notification subscription for their current profile email is removed. An address subscribed before a profile email change keeps receiving notifications until its recipient uses the unsubscribe link in the email or the subscription is deleted. To add someone while keeping the existing subscribers, name all of them, including the ones already present. Use `vamscli subscription list` first to read the current list.
:::

---

## subscription delete

Delete a subscription entirely.

```bash
vamscli subscription delete [OPTIONS]
```

| Option                | Type | Required | Description                                                      |
| --------------------- | ---- | -------- | ---------------------------------------------------------------- |
| `-d`, `--database-id` | TEXT | Yes      | Database of the entity's asset                                   |
| `-i`, `--entity-id`   | TEXT | Yes      | ID of the subscribed entity                                      |
| `-s`, `--subscriber`  | TEXT | Yes      | Required by the API, and ignored: every subscriber is removed    |
| `--event-name`        | TEXT | No       | Event name of the subscription (default: `Asset Version Change`) |
| `--entity-name`       | TEXT | No       | Entity type the ID refers to (default: `Asset`)                  |
| `--confirm`           | FLAG | Yes      | Confirm the deletion                                             |
| `--json-output`       | FLAG | No       | Output raw JSON response                                         |

```bash
vamscli subscription delete -d my-database -i my-asset -s reviewer@example.com --confirm
```

:::danger[Deletes the whole subscription and unsubscribes everyone]
This removes the subscription record and, for an asset, the asset's notification topic, so every subscriber is unsubscribed regardless of which subscribers the call names. `-s`, `--subscriber` is required by the API but plays no part in what is deleted, so a call naming one subscriber removes them all. Only the named database's subscription is deleted; an asset with the same ID in another database keeps its own.

The endpoint still validates what it ignores: an empty or malformed subscriber list is rejected with `400`.

To remove a single subscriber and leave the rest subscribed, use [`subscription unsubscribe`](#subscription-unsubscribe), which is a different operation.
:::

---

## subscription unsubscribe

Remove one subscriber from a subscription, leaving the subscription and its other subscribers in place.

```bash
vamscli subscription unsubscribe [OPTIONS]
```

| Option                | Type | Required | Description                                                      |
| --------------------- | ---- | -------- | ---------------------------------------------------------------- |
| `-d`, `--database-id` | TEXT | Yes      | Database of the entity's asset                                   |
| `-i`, `--entity-id`   | TEXT | Yes      | ID of the subscribed entity                                      |
| `-s`, `--subscriber`  | TEXT | Yes      | The single subscriber to remove                                  |
| `--event-name`        | TEXT | No       | Event name of the subscription (default: `Asset Version Change`) |
| `--entity-name`       | TEXT | No       | Entity type the ID refers to (default: `Asset`)                  |
| `--confirm`           | FLAG | Yes      | Confirm the unsubscribe                                          |
| `--json-output`       | FLAG | No       | Output raw JSON response                                         |

```bash
vamscli subscription unsubscribe -d my-database -i my-asset -s reviewer@example.com --confirm
```

Unlike `subscription create`, `update`, and `delete`, this command takes a single `-s`, `--subscriber`; the option cannot be repeated.

---

## subscription check

Report whether a user is subscribed to an asset.

```bash
vamscli subscription check [OPTIONS]
```

| Option                | Type | Required | Description              |
| --------------------- | ---- | -------- | ------------------------ |
| `-d`, `--database-id` | TEXT | Yes      | Database of the asset    |
| `-a`, `--asset-id`    | TEXT | Yes      | Asset to check           |
| `-u`, `--user-id`     | TEXT | Yes      | User to check            |
| `--json-output`       | FLAG | No       | Output raw JSON response |

```bash
vamscli subscription check -d my-database -a my-asset -u reviewer@example.com
vamscli subscription check -d my-database -a my-asset -u reviewer@example.com --json-output
```

:::note[Both answers are a success]
The API answers `200` whether or not the subscription exists, and reports the result in the response message: `success` when the user is subscribed, and `Subscription doesn't exists.` when they are not. A script must read the message, because the status code and the exit code are the same either way.
:::

---

## Workflow Examples

### Review a version and record the outcome

```bash
vamscli comment add -d engineering -a turbine-housing -v 4 -b "Wall thickness verified against spec." --json-output
vamscli comment list -d engineering -a turbine-housing -v 4 --json-output
```

### Add a subscriber without dropping the existing ones

```bash
# Read the current list first: update replaces it.
vamscli subscription list --json-output
vamscli subscription update -d engineering -i turbine-housing -s reviewer@example.com -s lead@example.com -s newjoiner@example.com
```

### Remove one subscriber

```bash
vamscli subscription unsubscribe -d engineering -i turbine-housing -s reviewer@example.com --confirm
```

---

## Related Pages

-   [Asset Commands](assets.md)
-   [Metadata and Schema Commands](metadata.md)
-   [Command Reference](../command-reference.md)
