# Tags and Subscriptions

VAMS provides a tagging system for organizing and categorizing assets, along with a subscription system for receiving email notifications when assets change. Together, these features enable structured asset classification and proactive change monitoring.

---

## Tags

Tags are labels that you assign to assets to classify and organize them. Every tag belongs to a **tag type**, which acts as a named category. For example, a tag type called `Region` might contain tags such as `us-east`, `eu-west`, and `ap-southeast`.

### Tag types

A tag type defines a named category for grouping related tags. Tag types have the following properties:

| Property    | Description                                                                                                 | Required |
| ----------- | ----------------------------------------------------------------------------------------------------------- | -------- |
| Name        | A unique identifier for the tag type. Must be 3--64 characters, alphanumeric with `-` and `_` allowed.      | Yes      |
| Description | A human-readable description of the tag type's purpose. Must be 4--256 characters.                          | Yes      |
| Required    | When enabled, assets must have at least one tag of this type assigned during creation or modification.       | No       |

:::info[Required tag types]
When a tag type is marked as **Required**, it appears with an `[R]` indicator in the Tags list. Users are expected to assign at least one tag from each required tag type when creating or modifying assets.
:::


#### Creating a tag type

1. Navigate to **Admin - Tags** in the left sidebar.
2. In the **Tag Types** section at the bottom of the page, choose **Create Tag Type**.
3. Complete the form fields:
    - **Name** -- Enter a unique name for the tag type (3--64 characters, alphanumeric with `-` and `_`).
    - **Description** -- Provide a description (4--256 characters).
    - **Require tag of this tag type on asset modification** -- Select the checkbox if assets must include a tag of this type.
4. Choose **Create Tag Type**.

<!-- Screenshot needed: Create Tag Type modal with fields filled in -->

#### Editing a tag type

1. In the **Tag Types** section, select the tag type you want to edit.
2. Choose **Edit**.
3. Update the **Description** or **Required** setting. The tag type name cannot be changed after creation.
4. Choose **Update Tag Type**.

#### Deleting a tag type

A tag type can only be deleted if no tags are currently assigned to it. Remove all associated tags before deleting the tag type.

1. In the **Tag Types** section, select the tag type you want to delete.
2. Choose **Delete**.
3. Confirm the deletion.

:::warning[Deletion constraint]
You cannot delete a tag type that has tags associated with it. Delete or reassign all tags under the tag type first.
:::


---

### Tags

Tags are individual labels that belong to a tag type. Each tag has the following properties:

| Property      | Description                                                                                       | Required |
| ------------- | ------------------------------------------------------------------------------------------------- | -------- |
| Name          | A unique identifier across all tag types. Must be 3--64 characters, alphanumeric with `-` and `_`. | Yes      |
| Description   | A human-readable description of the tag. Must be 4--256 characters.                               | Yes      |
| Tag Type      | The tag type category this tag belongs to.                                                        | Yes      |

:::note[Tag names are globally unique]
Tag names must be unique across all tag types. You cannot create two tags with the same name, even if they belong to different tag types.
:::


#### Creating a tag

1. Navigate to **Admin - Tags** in the left sidebar.
2. In the **Manage Tags** section at the top of the page, choose **Create Tag**.
3. Complete the form fields:
    - **Name** -- Enter a unique tag name (3--64 characters).
    - **Description** -- Provide a description (4--256 characters).
    - **Tag Type** -- Select the tag type this tag belongs to from the dropdown. The dropdown is populated from existing tag types.
4. Choose **Create Tag**.

<!-- Screenshot needed: Create Tag modal with tag type dropdown expanded -->

#### Editing a tag

1. In the **Manage Tags** section, select the tag you want to edit.
2. Choose **Edit**.
3. Update the **Description** or **Tag Type** assignment. The tag name cannot be changed after creation.
4. Choose **Update Tag**.

#### Deleting a tag

1. In the **Manage Tags** section, select the tag to delete.
2. Choose **Delete**.
3. Confirm the deletion.

---

### Assigning tags to assets

Tags can be assigned to assets during the upload process or by editing an existing asset. When a tag type is marked as required, the system expects at least one tag from that tag type to be present on the asset.

For details on uploading assets with tags, see [Upload Your First Asset](upload-first-asset.md). For editing asset metadata including tags, see [Asset Management](asset-management.md).

### Using tags for access control

Tags integrate with the VAMS permission system. Administrators can create access control constraints that restrict users to assets with specific tags. For example, a constraint can allow a user role to access only assets tagged with `confidential` under the `Classification` tag type.

For more information on configuring tag-based permissions, see [Permissions](permissions.md).

### Filtering by tags

The Tags list supports filtering by tag name. Enter a search term in the filter field at the top of the table to narrow the displayed tags. Tag-based filtering is also available in the [Search and Discovery](search-and-discovery.md) interface.

---

## Subscriptions

Subscriptions allow users to receive email notifications when changes occur to specific assets. When a subscribed asset is updated (for example, a new version is uploaded), all subscribers receive an email notification through Amazon Simple Notification Service (Amazon SNS).

### How subscriptions work

Subscriptions operate through the following mechanism:

1. A user creates a subscription for an asset, specifying one or more subscriber user IDs or email addresses.
2. VAMS creates an Amazon SNS topic for the asset (if one does not already exist) and subscribes each user's email address to the topic.
3. Subscribers receive a confirmation email from Amazon SNS that they must confirm before receiving notifications.
4. When the asset changes (for example, a new version is uploaded), Amazon SNS delivers an email notification to all confirmed subscribers.

:::tip[Confirm your subscription]
After being added as a subscriber, check your email for a confirmation message from Amazon SNS. You must confirm the subscription before you will receive notifications.
:::


### Subscribing to an asset

1. Navigate to **Admin - Tags** in the left sidebar, then choose **Subscription Management**.
2. Choose **Create Subscription**.
3. Complete the form fields:
    - **Event Type** -- Select **Asset Version Change**. This is currently the only supported event type.
    - **Entity Type** -- Select **Asset**. This is currently the only supported entity type.
    - **Entity Name** -- Search for the asset by name. Enter a search term and press **Enter** to search. Select the asset from the results table.
    - **Subscribers** -- Enter the user IDs or email addresses of the subscribers, separated by commas. User IDs must be at least 3 characters and can include alphanumeric characters and the special characters `. + - @`.
4. Choose **Create Subscription**.

![Asset Search](/img/assets.png)

:::info[Subscriber email resolution]
VAMS resolves subscriber user IDs to email addresses using the user's profile. If a user ID does not have an associated email, the user ID itself is used as the email address (it must be in valid email format). You can also enter direct email addresses for non-user recipients such as resource accounts.
:::


### Managing subscriptions

The **Subscription Management** page displays all subscriptions you have permission to view. The table shows the following columns:

| Column       | Description                                              |
| ------------ | -------------------------------------------------------- |
| Entity Name  | The name of the subscribed asset, linked to its detail page. |
| Entity Type  | The type of entity (currently **Asset**).                |
| Event Name   | The type of event being monitored (currently **Asset Version Change**). |
| Subscribers  | The list of subscribed user IDs or email addresses.      |

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
