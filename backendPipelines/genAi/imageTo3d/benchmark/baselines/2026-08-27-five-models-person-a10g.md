# Image to 3D -- recorded round

five models on the person photograph, one A10G fleet; TripoSR failed the print gate and wrote no mesh; judged blind for likeness

Container built from `aa3ff22cf794` (Let a scarce fleet fall back, and switch the test subject to the person).

## Outcome

| run                     | model       | instance   | texture                 | ready | bodies/exp | fused | fill   | fpAspect | h/w    | faces  |
| ----------------------- | ----------- | ---------- | ----------------------- | ----- | ---------- | ----- | ------ | -------- | ------ | ------ |
| projected               | stable3dgen | g5.4xlarge | projectedFromPhotograph | True  | 1/1        | 0     | 0.5782 | 1.4079   | 4.8492 | 135580 |
| sam3d                   | sam3d       | g5.4xlarge | fromVertexColors        | True  | 1/1        | 0     | 0.2008 | 1.1363   | 3.539  | 195656 |
| trellis2                | trellis2    | g5.4xlarge | reprojected             | True  | 1/1        | 0     | 0.0416 | 1.2712   | 4.7579 | 199290 |
| triposr                 | triposr     | g5.4xlarge | reprojected             | False | 1/1        | 0     | 0.619  | 0.8238   | 4.4427 | 82935  |
| step1x3d-geometry-print | step1x3d    | g5.4xlarge | none                    | True  | 1/1        | 0     | 0.5772 | 0.2931   | 0.7403 | 199284 |

## Stage seconds

| run                     | weightCache | inputDownload | preprocess | generate | meshLoad | cleanup | projectColour | printFeatures | export | upload | total |
| ----------------------- | ----------- | ------------- | ---------- | -------- | -------- | ------- | ------------- | ------------- | ------ | ------ | ----- |
| projected               | 107.1       | 0.2           | 60.1       | 43.0     | 0.1      | 15.1    | 0.7           | 0.0           | 1.0    | 0.0    | 227.8 |
| sam3d                   | 204.8       | 0.2           | 71.9       | 141.5    | 0.1      | 15.0    | 0.0           | 0.0           | 0.8    | 0.0    | 434.9 |
| trellis2                | 243.3       | 0.2           | 38.5       | 343.7    | 0.7      | 76.9    | 0.0           | 0.0           | 6.0    | 0.0    | 709.7 |
| triposr                 | 14.9        | 0.1           | 42.0       | 43.1     | 1.1      | 30.0    | 0.0           | 0.0           | 0.0    | 0.0    | 131.6 |
| step1x3d-geometry-print | 477.2       | 0.2           | 40.9       | 83.0     | 0.3      | 3.6     | 0.0           | 0.0           | 0.6    | 0.0    | 606.1 |
