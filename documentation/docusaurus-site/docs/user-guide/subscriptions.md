# Subscriptions

Subscriptions allow users to receive email notifications when changes occur to specific assets. When a subscribed asset is updated (for example, a new version is uploaded), all subscribers receive an email notification through Amazon Simple Notification Service (Amazon SNS).

---

## How subscriptions work

Subscriptions operate through the following mechanism:

1. A user creates a subscription for an asset, specifying one or more subscriber user IDs or email addresses.
2. VAMS creates an Amazon SNS topic for the asset (if one does not already exist) and subscribes each user's email address to the topic.
3. Subscribers receive a confirmation email from Amazon SNS that they must confirm before receiving notifications.
4. When the asset changes (for example, a new version is uploaded), Amazon SNS delivers an email notification to all confirmed subscribers.

:::tip[Confirm your subscription]
After being added as a subscriber, check your email for a confirmation message from Amazon SNS. You must confirm the subscription before you will receive notifications.
:::

---

## Subscribing to an asset

There are two ways to subscribe to an asset:

### From the asset detail page

1. On the asset detail page, select the **Subscribe** button in the details pane header.
2. A confirmation message appears indicating that a subscription has been created for the **Asset Version Change** event.
3. Check your email inbox for a subscription confirmation message and confirm the subscription.

The button changes to **Subscribed** (with a bell icon) when you have an active subscription. Select it again to unsubscribe.

### From the subscription management page

1. Navigate to **Admin - Data** in the left sidebar, then choose **Subscription Management**.
2. Choose **Create Subscription**.
3. Complete the form fields:
    - **Event Type** -- Select **Asset Version Change**. This is currently the only supported event type.
    - **Entity Type** -- Select **Asset**. This is currently the only supported entity type.
    - **Entity Name** -- Search for the asset by name. Enter a search term and press **Enter** to search. Select the asset from the results table.
    - **Subscribers** -- Enter the user IDs or email addresses of the subscribers, separated by commas. User IDs must be at least 3 characters and can include alphanumeric characters and the special characters `. + - @`.
4. Choose **Create Subscription**.

:::info[Subscriber email resolution]
VAMS resolves subscriber user IDs to email addresses using the user's profile. If a user ID does not have an associated email, the user ID itself is used as the email address (it must be in valid email format). You can also enter direct email addresses for non-user recipients such as resource accounts.
:::

---

## Managing subscriptions

The **Subscription Management** page displays all subscriptions you have permission to view. The table shows the following columns:

| Column      | Description                                                             |
| ----------- | ----------------------------------------------------------------------- |
| Entity Name | The name of the subscribed asset, linked to its detail page.            |
| Entity Type | The type of entity (currently **Asset**).                               |
| Event Name  | The type of event being monitored (currently **Asset Version Change**). |
| Subscribers | The list of subscribed user IDs or email addresses.                     |

You can edit a subscription to add or remove subscribers. When subscribers are removed, their Amazon SNS subscriptions are also removed.

### Updating a subscription

1. On the **Subscription Management** page, select the subscription to edit.
2. Choose **Edit**.
3. Modify the **Subscribers** field to add or remove user IDs.
4. Choose **Update Subscription**.

### Deleting a subscription

Deleting a subscription removes the subscription record and deletes the associated Amazon SNS topic for the asset.

1. On the **Subscription Management** page, select the subscription to delete.
2. Choose **Delete**.
3. Confirm the deletion.

:::warning[Deleting a subscription removes all subscribers]
When you delete a subscription, all subscribers are unsubscribed and the Amazon SNS topic for the asset is removed. To remove individual subscribers, edit the subscription instead.
:::

:::tip[CLI alternative]
Subscription operations can also be performed via the command line, if subscription commands are available. See the [CLI Command Reference](../cli/command-reference.md) for the latest list of supported commands.
:::
