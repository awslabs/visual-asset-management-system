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
-   **Cancel** -- available while a job is queued or running. The archive run stops and nothing is kept.
-   **Delete** -- available when a job has finished. The archive and the job are removed.

## Retention

A zip download and its archive are removed automatically a set number of days after the job was created (`app.zipDownloads.retentionDays`, 7 days by default). The **Download as Zip** dialog shows the retention for your deployment. Download the archive before it expires, or create a new zip download.

## Limits

Your administrator sets these limits for the deployment:

| Limit                                         | Configuration key                           | Default |
| --------------------------------------------- | ------------------------------------------- | ------- |
| Files in one archive                          | `app.zipDownloads.maxFilesPerRequest`       | 100,000 |
| Total size of one archive                     | `app.zipDownloads.maxTotalBytesPerRequest`  | 5 TiB   |
| Your active (queued or running) zip downloads | `app.zipDownloads.maxActiveRequestsPerUser` | 5       |
| Entries in one selection                      | --                                          | 1,000   |

Select a folder rather than its files one by one to stay within the selection limit; a folder counts as one entry.

## Permissions

Creating a zip download requires the same asset permission as downloading a file, plus access to the zip download routes, which the shipped database user, database administrator, and read-only permission templates include. Your permission on the asset is checked again each time you list, open, or download a job, so a job on an asset you can no longer read stops appearing. See [Permissions](permissions.md).

## Related topics

-   [Asset Management](asset-management.md#download-and-share-urls) -- Downloading and sharing individual files
-   [Zip Download Commands](../cli/commands/zip-downloads.md) -- Create and download zip downloads with `vamscli`
-   [Zip Downloads API](../api/zip-downloads.md) -- The REST endpoints
-   [Archive Pipeline](../pipelines/archive.md) -- How the archives are built
