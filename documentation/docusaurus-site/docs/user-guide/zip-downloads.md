# Download as Zip and the Download Queue

Download as Zip packages files, folders, or a whole asset into a single zip, tar, or tar.gz file. VAMS builds the archive in the background, so large selections do not have to finish while the browser waits, and the archive can reach 5 TiB. When the archive is ready, you download it from your **Download Queue**.

Download as Zip is available when your administrator enables it for the deployment (`app.zipDownloads.enabled`). The controls appear only when the deployment has the feature and your role allows the zip download routes.

## What you can download

-   Any mix of files and folders from one asset, or the whole asset.
-   The current files, or the file versions recorded in an asset version.
-   Only assets that are distributable and not archived.

VAMS refuses the whole archive, rather than leaving files out, when a selected file is archived, a deleted version, in an archive storage class (Amazon S3 Glacier Flexible Retrieval or Glacier Deep Archive), or has a blocked executable file type, and when the selection exceeds the deployment's limits on file count or total size. The reason names the file, so you can adjust the selection and try again.

## Creating a zip download

1. Open the asset and select the **File Manager** tab.
2. Select the files and folders to include. Select the asset root to include the whole asset.
3. Open the **Export** menu in the file details toolbar and choose **Download as Zip**.
4. In the **Download as Zip** dialog, review the **Selection** summary and choose:
    - **Contents** -- **Current files**, or **An asset version** to archive the file versions recorded in the asset version you pick.
    - **Format** -- **ZIP (.zip)** (default), **TAR (.tar)**, or **Compressed TAR (.tar.gz)**.
    - **Compression** -- for ZIP, **No compression** (default) or **Compress (deflate)**. Most 3D formats are already compressed, so no compression is usually as small and faster.
    - **Archive name** -- optional. VAMS adds the extension for the format and replaces characters that are not allowed in file names.
5. Choose **Create zip download**. The archive job appears with the status **Queued** in the asset's **Download Queue** tab and in **My Download Queue**. The confirmation message's **View Download Queue** switches this asset page to its **Download Queue** tab (the page address gains `?tab=zipDownloads`, so the link can be bookmarked or shared).

**Download as Zip** is hidden for an asset that is not distributable or is archived.

When the selection includes an archived or deleted file or folder, the dialog lists it and **Create zip download** stays unavailable; remove it from the selection, or restore it first. A selection can hold up to 1,000 files and folders; files inside a selected folder count as that folder, so select folders for larger selections.

## The Download Queue

The Download Queue lists your download jobs. Archive jobs from Download as Zip are the only kind today. Each asset has a **Download Queue** tab that lists your jobs for that asset. The **My Download Queue** page under **User** in the side navigation (`#/zipdownloads`) lists them across every asset, and shows only your own jobs.

### Your jobs and every user's jobs

The asset's **Download Queue** tab opens on **My jobs**. When your role allows it, a **My jobs** / **Show all users** toggle at the right end of the row above the table lists every user's jobs for that asset, with a **Requested by** column. Your administrator grants it through the all-users list route (see [Permissions](../developer/permissions.md#zip-download-routes-on-roles-built-from-earlier-templates)); you also need read access to the asset. Without it, the choice is not shown.

-   You can download another user's finished archive when your role lets you download files from that asset. VAMS checks the archive's files again when you choose **Download**: if a file in it has since been archived, deleted, or blocked, the download is refused with the same message a new zip download would get.
-   Only the user who requested a job can cancel or delete it, so another user's job shows no **Cancel** or **Delete**.
-   Another user's job shows its selection summary (counts of files and folders), not the list of selected paths.
-   Jobs created before this view was added appear only to their requester until they expire.
-   Each download of another user's archive is recorded in the audit log with both user IDs.

The search box above each list finds jobs by asset ID, asset name, request ID, archive name, or (with **Show all users**) the requester, and the refresh button beside it reloads the list. The search covers the jobs already loaded; when more jobs exist than one load holds, choose **Load more** to add older jobs, which the search then includes.

| Status        | Meaning                                                                                                     |
| ------------- | ----------------------------------------------------------------------------------------------------------- |
| **Queued**    | The job is starting.                                                                                        |
| **Running**   | The archive is being written. The progress bar shows files and bytes written.                               |
| **Succeeded** | The archive is ready to download.                                                                           |
| **Failed**    | No archive was produced. The reason is shown with the job, for example the path of a file that was refused. |
| **Cancelled** | You cancelled the job before it finished.                                                                   |

If a job fails with **The archive job was stopped** or **The archive job stopped responding**, its archive job ended before it finished, for example because it was stopped by an administrator or ran out of memory. Create the job again; see [Zip Archive troubleshooting](../pipelines/zip-archive.md#troubleshooting) if it recurs.

While a job is queued or running, the list refreshes itself every few seconds.

Each job's actions are icon buttons in its **Actions** column, named for screen readers and in their tooltip: a download icon (**Download**), a close icon (**Cancel**) and a remove icon (**Delete**). An action your role does not allow is greyed, with its reason when you hover or focus it.

-   **Download** -- available when the job has succeeded. VAMS issues a short-lived link and the browser downloads the archive under its archive name. Choose **Download** again for each later download.
-   **Cancel** -- available while a job is queued or running. The Zip Archive run stops and nothing is kept. A cancel never deletes a job. If the job finishes while the cancel dialog is open, the dialog says so and no longer offers the cancel; close it and download or delete the job from the list. If the job finishes just before your cancel reaches it, it keeps its outcome and a message says it finished before it could be cancelled.
-   **Delete** -- available when a job has finished. The archive and the job are removed.

## Retention

A zip download is available for 7 days after the job was created; the **Expires** column shows when. After that it is removed from the list and its archive can no longer be downloaded. The archive file itself is deleted automatically by an Amazon S3 lifecycle rule, usually within a day or two after the job expires. An archive of an asset that is later archived or permanently deleted can no longer be downloaded either, and is deleted the same way. Download the archive before it expires, or create a new zip download.

## Limits

Your administrator sets these limits for the deployment:

| Limit                                         | Configuration key                           | Default |
| --------------------------------------------- | ------------------------------------------- | ------- |
| Files in one archive                          | `app.zipDownloads.maxFilesPerRequest`       | 100,000 |
| Total size of one archive                     | `app.zipDownloads.maxTotalBytesPerRequest`  | 5 TiB   |
| Your active (queued or running) zip downloads | `app.zipDownloads.maxActiveRequestsPerUser` | 5       |
| Entries in one selection                      | --                                          | 1,000   |

Select a folder rather than its files one by one to stay within the selection limit; a folder counts as one entry.

The active-job limit is checked when a job is created, so several zip downloads created at the same moment can briefly exceed it.

If you lose access to an asset, its zip downloads disappear from your lists and can no longer be downloaded, cancelled, or deleted. A queued or running one ends with its run (at most 12 hours) and counts toward your active-job limit until then.

## Permissions

Creating a zip download requires the same asset permission as downloading a file, plus access to the zip download routes, which the shipped database user, database administrator, and read-only permission templates include. A role built from a template of an earlier release may reach only some of the routes, for example create a job but not cancel it; your administrator can check it against [Zip download routes on roles built from earlier templates](../developer/permissions.md#zip-download-routes-on-roles-built-from-earlier-templates). Your permission on the asset is checked again each time you list, open, or download a job, so a job on an asset you can no longer read stops appearing. See [Permissions](permissions.md).

## Related topics

-   [Asset Management](asset-management.md#download-and-share-urls) -- Downloading and sharing individual files
-   [Zip Download Commands](../cli/commands/zip-downloads.md) -- Create and download zip downloads with `vamscli`
-   [Zip Downloads API](../api/zip-downloads.md) -- The REST endpoints
-   [Zip Archive Pipeline](../pipelines/zip-archive.md) -- How the archives are built
