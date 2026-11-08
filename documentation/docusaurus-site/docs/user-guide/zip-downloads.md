# Zip Downloads

Download as Zip packages files, folders, or a whole asset into a single zip, tar, or tar.gz file. VAMS builds the archive in the background, so large selections do not have to finish while the browser waits, and the archive can reach 5 TiB. When the archive is ready, you download it from the **My zip downloads** list.

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
5. Choose **Create zip download**. The job appears in **My zip downloads** with the status **Queued**.

**Download as Zip** is hidden for an asset that is not distributable or is archived.

When the selection includes an archived or deleted file or folder, the dialog lists it and **Create zip download** stays unavailable; remove it from the selection, or restore it first. A selection can hold up to 1,000 files and folders; files inside a selected folder count as that folder, so select folders for larger selections.

## Tracking and downloading

Each asset has a **My zip downloads** tab that lists your zip downloads for that asset. The **My zip downloads** page under **User** in the side navigation (`#/zipdownloads`) lists them across every asset. Both lists show only your own downloads.

| Status        | Meaning                                                                                                     |
| ------------- | ----------------------------------------------------------------------------------------------------------- |
| **Queued**    | The job is starting.                                                                                        |
| **Running**   | The archive is being written. The progress bar shows files and bytes written.                               |
| **Succeeded** | The archive is ready to download.                                                                           |
| **Failed**    | No archive was produced. The reason is shown with the job, for example the path of a file that was refused. |
| **Cancelled** | You cancelled the job before it finished.                                                                   |

While a job is queued or running, the list refreshes itself every few seconds.

-   **Download** -- available when the job has succeeded. VAMS issues a short-lived link and the browser downloads the archive under its archive name. Choose **Download** again for each later download.
-   **Cancel** -- available while a job is queued or running. The Zip Archive run stops and nothing is kept. If the job finishes just before your cancel reaches it, it keeps its outcome and a message says it finished before it could be cancelled.
-   **Delete** -- available when a job has finished. The archive and the job are removed.

## Retention

A zip download expires a set number of days after the job was created (`app.zipDownloads.retentionDays`, 7 days by default); the **Expires** column shows when. After that it is no longer listed and its archive can no longer be downloaded. The archive file itself is deleted automatically by an Amazon S3 lifecycle rule, usually within a day or two after the job expires. An archive of an asset that is later archived or permanently deleted can no longer be downloaded either, and is deleted the same way. Download the archive before it expires, or create a new zip download.

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
