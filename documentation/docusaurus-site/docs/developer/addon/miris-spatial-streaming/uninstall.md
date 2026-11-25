---
sidebar_label: Uninstall
---

# Uninstall the Miris Add-on

Uninstalling VAMS, or disabling the add-on, leaves these behind. Remove them as needed.

-   **The viewer key table.** In `perView` mode, `MirisViewerKeyStorageTable` is retained like every VAMS table. Its cleanup function is deleted with the stack, so keys whose rows TTL had not yet removed stay in the Miris workspace; they have already expired or expire within `viewerKeyTtlMinutes`. Delete them in the Miris Portal or with `miris viewerkey delete` if the workspace should not keep them, then delete the table.
-   **Objects in the auxiliary bucket.** The upload pipeline's claims under `locks/miris-upload/`, its dependency manifests under `pipelines/miris-upload/manifests/` and its runs' `nested-locations.json` objects under `pipelines/miris-upload/` stay in the auxiliary bucket until it is emptied.
-   **What is not a VAMS resource.** The Miris assets the upload pipeline created, a static viewer key's material in Miris, and the Integration Key secret named in `apiKeySecretArn` are not removed by uninstalling VAMS.

The GLOBAL **Miris stream** metadata schema record is stored in the metadata schema table, so deleting that table removes it. The add-on's log groups are destroyed with the stack.
