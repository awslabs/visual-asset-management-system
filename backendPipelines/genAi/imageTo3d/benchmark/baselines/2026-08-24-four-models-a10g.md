# Image to 3D -- recorded round

Four models on mug.png plus the two print-feature configurations, and three runs each of TRELLIS.2 and SAM 3D. Every run on an NVIDIA A10G with weights warm on EFS; the six smoke runs on g5.8xlarge and two of the repeats on g5.2xlarge, which Batch chose -- same GPU, different instance size. The repeats isolate run-to-run variance from the GPU, and they show TRELLIS.2 is not reproducible on identical hardware while SAM 3D is.

Container built from `51292681cec4` (Replay the post-generation chain offline instead of on a GPU).

## Outcome

| run              | model       | instance   | texture          | ready | bodies/exp | fused | fill   | fpAspect | h/w    | faces  |
| ---------------- | ----------- | ---------- | ---------------- | ----- | ---------- | ----- | ------ | -------- | ------ | ------ |
| bare-triposr     | triposr     | g5.8xlarge | reprojected      | True  | 1/1        | 1     | 0.936  | 0.9639   | 0.89   | 190930 |
| bare-trellis2    | trellis2    | g5.8xlarge | reprojected      | True  | 1/1        | 0     | 0.7619 | 1.0051   | 0.7986 | 105918 |
| bare-stable3dgen | stable3dgen | g5.8xlarge | none             | True  | 1/1        | 1     | 0.2164 | 1.2247   | 0.7579 | 189942 |
| bare-sam3d       | sam3d       | g5.8xlarge | fromVertexColors | True  | 1/1        | 0     | 0.1704 | 1.3261   | 1.3335 | 199972 |
| feat-sam3d       | sam3d       | g5.8xlarge | fromVertexColors | True  | 3/3        | 0     | 0.1964 | 1.4555   | 1.6161 | 202162 |
| feat-triposr     | triposr     | g5.8xlarge | reprojected      | True  | 3/3        | 1     | 0.8751 | 1.0129   | 1.0695 | 193106 |
| bare-trellis2-r2 | trellis2    | g5.2xlarge | reprojected      | True  | 1/1        | 0     | 0.4197 | 1.0051   | 0.7985 | 78570  |
| bare-trellis2-r3 | trellis2    | g5.8xlarge | reprojected      | True  | 1/1        | 0     | 0.4197 | 1.0051   | 0.7986 | 80692  |
| bare-sam3d-r2    | sam3d       | g5.2xlarge | fromVertexColors | True  | 1/1        | 0     | 0.1704 | 1.3266   | 1.3339 | 199972 |
| bare-sam3d-r3    | sam3d       | g5.8xlarge | fromVertexColors | True  | 1/1        | 0     | 0.1704 | 1.3262   | 1.3338 | 199986 |

## Stage seconds

| run              | weightCache | inputDownload | preprocess | generate | meshLoad | cleanup | printFeatures | export | upload | total  |
| ---------------- | ----------- | ------------- | ---------- | -------- | -------- | ------- | ------------- | ------ | ------ | ------ |
| bare-triposr     | 14.5        | 0.2           | 41.0       | 176.4    | 2.0      | 119.5   | 0.0           | 2.1    | 0.0    | 356.1  |
| bare-trellis2    | 243.4       | 0.2           | 38.4       | 630.7    | 0.7      | 31.0    | 0.0           | 4.5    | 0.0    | 949.2  |
| bare-stable3dgen | 105.6       | 0.2           | 59.5       | 44.8     | 0.3      | 20.5    | 0.0           | 0.5    | 0.0    | 231.8  |
| bare-sam3d       | 191.5       | 0.1           | 69.4       | 188.3    | 1.0      | 7.5     | 0.0           | 0.8    | 0.0    | 459.1  |
| feat-sam3d       | 194.9       | 0.1           | 68.6       | 208.6    | 1.0      | 7.5     | 2.5           | 0.8    | 0.0    | 484.7  |
| feat-triposr     | 0.0         | 0.1           | 40.9       | 178.7    | 2.0      | 118.7   | 2.6           | 2.1    | 0.0    | 345.4  |
| bare-trellis2-r2 | 242.4       | 0.2           | 40.6       | 668.7    | 0.6      | 26.4    | 0.0           | 4.3    | 0.0    | 983.7  |
| bare-trellis2-r3 | 239.5       | 0.1           | 38.4       | 716.9    | 0.6      | 30.9    | 0.0           | 4.4    | 0.0    | 1031.2 |
| bare-sam3d-r2    | 200.9       | 0.2           | 79.7       | 225.9    | 1.0      | 7.4     | 0.0           | 0.8    | 0.0    | 516.5  |
| bare-sam3d-r3    | 0.0         | 0.1           | 68.1       | 147.7    | 1.0      | 7.5     | 0.0           | 0.8    | 0.0    | 225.8  |
