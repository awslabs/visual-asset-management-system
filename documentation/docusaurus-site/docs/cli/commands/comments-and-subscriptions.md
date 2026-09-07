---
sidebar_label: Comments and Subscriptions
title: Comment and Subscription Commands
---

# Comment and Subscription Commands

Manage review comments attached to an asset version, and the event subscriptions that notify users when an asset changes.

---

## Comment Commands

Comments are attached to a specific version of an asset and are addressed by asset ID, asset version ID, and comment ID. A comment belongs to the user who created it, and only that user can change or delete it.

---

## comment list

List comments on an asset. By default this returns every comment on the asset across all of its versions; adding `-v`, `--asset-version-id` narrows the listing to one version.

```bash
vamscli comment list [OPTIONS]
```

| Option                     | Type    | Required | Description                               |
| -------------------------- | ------- | -------- | ----------------------------------------- |
| `-a`, `--asset-id`         | TEXT    | Yes      | Asset to list comments for                |
| `-v`, `--asset-version-id` | TEXT    | No       | Restrict the listing to one asset version |
| `--page-size`              | INTEGER | No       | Number of items read per page             |
| `--max-items`              | INTEGER | No       | Maximum total items to return             |
| `--starting-token`         | TEXT    | No       | Token to resume from                      |
| `--json-output`            | FLAG    | No       | Output raw JSON response                  |

```bash
vamscli comment list -a my-asset
vamscli comment list -a my-asset -v version-001
vamscli comment list -a my-asset --max-items 50 --json-output
```

:::note[The listing returns no continuation token]
`--page-size` and `--max-items` bound how much is read, and a token supplied to `--starting-token` is honoured, but neither listing route returns a token in its response. There is therefore no value to pass back to `--starting-token`, and a listing that stops at `--max-items` cannot be resumed from where it ended. Raise `--max-items` to read further.

The listing also has no `--show-deleted` option: both listing routes ignore that parameter, so soft-deleted comments cannot be listed back.
:::

---

## comment get

Get a single comment by asset, asset version, and comment ID.

```bash
vamscli comment get [OPTIONS]
```

| Option                     | Type | Required | Description              |
| -------------------------- | ---- | -------- | ------------------------ |
| `-a`, `--asset-id`         | TEXT | Yes      | Asset the comment is on  |
| `-v`, `--asset-version-id` | TEXT | Yes      | Asset version            |
| `-c`, `--comment-id`       | TEXT | Yes      | Comment ID               |
| `--json-output`            | FLAG | No       | Output raw JSON response |

```bash
vamscli comment get -a my-asset -v version-001 -c 8d65af7c-5412-404b-a494-fbd2cdb62442
```

---

## comment add

Add a comment to an asset version. When `-c`, `--comment-id` is omitted the CLI generates a random ID for the new comment.

```bash
vamscli comment add [OPTIONS]
```

| Option                     | Type | Required | Description                                  |
| -------------------------- | ---- | -------- | -------------------------------------------- |
| `-a`, `--asset-id`         | TEXT | Yes      | Asset to comment on                          |
| `-v`, `--asset-version-id` | TEXT | Yes      | Asset version to comment on                  |
| `-b`, `--comment-body`     | TEXT | Yes      | Text of the comment                          |
| `-c`, `--comment-id`       | TEXT | No       | Explicit comment ID (generated when omitted) |
| `--json-output`            | FLAG | No       | Output raw JSON response                     |

```bash
vamscli comment add -a my-asset -v version-001 -b "Geometry looks correct at this revision."
vamscli comment add -a my-asset -v version-001 -b "Approved." -c review-signoff
```

:::warning[Reusing a comment ID overwrites that comment]
The write is unconditional, so supplying `-c`, `--comment-id` with the ID of a comment that already exists on the same asset version replaces its body and recorded author rather than being rejected as a duplicate. Let the ID be generated unless you specifically intend to write to a known ID.
:::

---

## comment update

Replace the body of an existing comment.

```bash
vamscli comment update [OPTIONS]
```

| Option                     | Type | Required | Description              |
| -------------------------- | ---- | -------- | ------------------------ |
| `-a`, `--asset-id`         | TEXT | Yes      | Asset the comment is on  |
| `-v`, `--asset-version-id` | TEXT | Yes      | Asset version            |
| `-c`, `--comment-id`       | TEXT | Yes      | Comment ID               |
| `-b`, `--comment-body`     | TEXT | Yes      | Replacement comment text |
| `--json-output`            | FLAG | No       | Output raw JSON response |

```bash
vamscli comment update -a my-asset -v version-001 -c review-signoff -b "Approved with notes."
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

| Option                     | Type | Required | Description                       |
| -------------------------- | ---- | -------- | --------------------------------- |
| `-a`, `--asset-id`         | TEXT | Yes      | Asset the comment is on           |
| `-v`, `--asset-version-id` | TEXT | Yes      | Asset version                     |
| `-c`, `--comment-id`       | TEXT | Yes      | Comment ID                        |
| `--confirm`                | FLAG | Yes      | Confirm the deletion              |
| `--json-output`            | FLAG | No       | Output raw JSON response          |

```bash
vamscli comment delete -a my-asset -v version-001 -c review-signoff --confirm
```

:::note[Creator-only, and a soft delete]
As with `comment update`, only the user who created the comment can delete it. The delete is a soft delete: the comment is marked deleted and drops out of the default listing, and its record is retained.
:::

---

:::note[`--confirm` is required, not a prompt override]
The flag is the whole confirmation: there is no interactive prompt to skip. Without `--confirm` the command refuses and exits non-zero, reporting `Confirmation required`, and nothing is deleted. Supply the flag on every invocation, including interactive ones. This keeps a destructive command a single non-interactive call, which is what `file delete`, `metadata-schema delete`, `tag delete`, and `tag-type delete` also do.
:::

## Subscription Commands

A subscription notifies a set of subscribers when an event occurs on an entity. Each subscription is identified by its entity ID together with the event name and entity name, which default to `Asset Version Change` and `Asset`.

---

## subscription list

List all subscriptions the caller can read.

```bash
vamscli subscription list [OPTIONS]
```

| Option             | Type    | Required | Description                   |
| ------------------ | ------- | -------- | ----------------------------- |
| `--page-size`      | INTEGER | No       | Number of items read per page  |
| `--max-items`      | INTEGER | No       | Maximum total items to return  |
| `--starting-token` | TEXT    | No       | Token to resume from           |
| `--json-output`    | FLAG    | No       | Output raw JSON response       |

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

| Option                | Type | Required | Description                                                     |
| --------------------- | ---- | -------- | --------------------------------------------------------------- |
| `-i`, `--entity-id`   | TEXT | Yes      | ID of the entity to subscribe to                                |
| `-s`, `--subscriber`  | TEXT | Yes      | User to subscribe; repeat the option for several subscribers     |
| `--event-name`        | TEXT | No       | Event to subscribe to (default: `Asset Version Change`)          |
| `--entity-name`       | TEXT | No       | Entity type the ID refers to (default: `Asset`)                  |
| `--json-output`       | FLAG | No       | Output raw JSON response                                        |

```bash
vamscli subscription create -i my-asset -s reviewer@example.com
vamscli subscription create -i my-asset -s reviewer@example.com -s lead@example.com
```

Each subscriber must have a usable email address on their user profile; a subscriber without one is rejected.

---

## subscription update

Set the subscriber list for a subscription.

```bash
vamscli subscription update [OPTIONS]
```

| Option                | Type | Required | Description                                                    |
| --------------------- | ---- | -------- | -------------------------------------------------------------- |
| `-i`, `--entity-id`   | TEXT | Yes      | ID of the subscribed entity                                    |
| `-s`, `--subscriber`  | TEXT | Yes      | Subscriber to keep; repeat the option for several subscribers   |
| `--event-name`        | TEXT | No       | Event name of the subscription (default: `Asset Version Change`) |
| `--entity-name`       | TEXT | No       | Entity type the ID refers to (default: `Asset`)                 |
| `--json-output`       | FLAG | No       | Output raw JSON response                                       |

```bash
vamscli subscription update -i my-asset -s reviewer@example.com -s lead@example.com
```

:::warning[The subscriber list is replaced, not extended]
`--subscriber` supplies the complete list the subscription will have afterwards. A subscriber who is currently subscribed and is not named in the call is unsubscribed, and their notification subscription is removed. To add someone while keeping the existing subscribers, name all of them, including the ones already present. Use `vamscli subscription list` first to read the current list.
:::

---

## subscription delete

Delete a subscription entirely.

```bash
vamscli subscription delete [OPTIONS]
```

| Option                | Type | Required | Description                                                      |
| --------------------- | ---- | -------- | ---------------------------------------------------------------- |
| `-i`, `--entity-id`   | TEXT | Yes      | ID of the subscribed entity                                      |
| `-s`, `--subscriber`  | TEXT | Yes      | Required by the API, and ignored: every subscriber is removed      |
| `--event-name`        | TEXT | No       | Event name of the subscription (default: `Asset Version Change`)  |
| `--entity-name`       | TEXT | No       | Entity type the ID refers to (default: `Asset`)                   |
| `--confirm`           | FLAG | Yes      | Confirm the deletion                                             |
| `--json-output`       | FLAG | No       | Output raw JSON response                                         |

```bash
vamscli subscription delete -i my-asset -s reviewer@example.com --confirm
```

:::danger[Deletes the whole subscription and unsubscribes everyone]
This removes the subscription record and, for an asset, the asset's notification topic, so every subscriber is unsubscribed regardless of which subscribers the call names. `-s`, `--subscriber` is required by the API but plays no part in what is deleted, so a call naming one subscriber removes them all.

The endpoint still validates what it ignores: an empty or malformed subscriber list is rejected with `400`, and that error names only `eventName`, `entityName` and `entityId`, so it does not mention the field that failed.

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
| `-i`, `--entity-id`   | TEXT | Yes      | ID of the subscribed entity                                      |
| `-s`, `--subscriber`  | TEXT | Yes      | The single subscriber to remove                                  |
| `--event-name`        | TEXT | No       | Event name of the subscription (default: `Asset Version Change`)  |
| `--entity-name`       | TEXT | No       | Entity type the ID refers to (default: `Asset`)                   |
| `--confirm`           | FLAG | Yes      | Confirm the unsubscribe                                          |
| `--json-output`       | FLAG | No       | Output raw JSON response                                         |

```bash
vamscli subscription unsubscribe -i my-asset -s reviewer@example.com --confirm
```

Unlike `subscription create`, `update`, and `delete`, this command takes a single `-s`, `--subscriber`; the option cannot be repeated.

---

## subscription check

Report whether a user is subscribed to an asset.

```bash
vamscli subscription check [OPTIONS]
```

| Option              | Type | Required | Description              |
| ------------------- | ---- | -------- | ------------------------ |
| `-a`, `--asset-id`  | TEXT | Yes      | Asset to check           |
| `-u`, `--user-id`   | TEXT | Yes      | User to check            |
| `--json-output`     | FLAG | No       | Output raw JSON response |

```bash
vamscli subscription check -a my-asset -u reviewer@example.com
vamscli subscription check -a my-asset -u reviewer@example.com --json-output
```

:::note[Both answers are a success]
The API answers `200` whether or not the subscription exists, and reports the result in the response message: `success` when the user is subscribed, and `Subscription doesn't exists.` when they are not. A script must read the message, because the status code and the exit code are the same either way.
:::

---

## Workflow Examples

### Review a version and record the outcome

```bash
vamscli comment add -a turbine-housing -v version-004 -b "Wall thickness verified against spec." --json-output
vamscli comment list -a turbine-housing -v version-004 --json-output
```

### Add a subscriber without dropping the existing ones

```bash
# Read the current list first: update replaces it.
vamscli subscription list --json-output
vamscli subscription update -i turbine-housing -s reviewer@example.com -s lead@example.com -s newjoiner@example.com
```

### Remove one subscriber

```bash
vamscli subscription unsubscribe -i turbine-housing -s reviewer@example.com --confirm
```

---

## Related Pages

-   [Asset Commands](assets.md)
-   [Metadata and Schema Commands](metadata.md)
-   [Command Reference](../command-reference.md)
