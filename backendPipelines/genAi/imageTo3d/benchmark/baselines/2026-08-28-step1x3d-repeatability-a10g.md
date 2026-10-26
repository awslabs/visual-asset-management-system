# Image to 3D -- recorded round

Step1X-3D repeatability: three textured runs at one seed on one instance type, plus TripoSR at failClosed for contrast

Container built from `475a004f70d8` (Give the execution the report as data, not the metadata writer's envelope).

## Outcome

| run           | model    | instance   | texture     | ready | bodies/exp | fused | fill   | fpAspect | h/w    | faces |
| ------------- | -------- | ---------- | ----------- | ----- | ---------- | ----- | ------ | -------- | ------ | ----- |
| triposr       | triposr  | g5.4xlarge | reprojected | False | 1/1        | 0     | 0.619  | 0.8238   | 4.4427 | 82935 |
| step1x3d-rep1 | step1x3d | g5.4xlarge | preserved   | True  | 1/1        | 0     | 0.5771 | 1.3517   | 4.6119 | 49572 |
| step1x3d-rep2 | step1x3d | g5.4xlarge | preserved   | True  | 1/1        | 0     | 0.5771 | 1.3517   | 4.6119 | 49572 |
| step1x3d-rep3 | step1x3d | g5.4xlarge | preserved   | True  | 1/1        | 0     | 0.5771 | 1.3517   | 4.6119 | 49572 |

## Stage seconds

| run           | weightCache | inputDownload | preprocess | generate | meshLoad | cleanup | projectColour | printFeatures | export | upload | total |
| ------------- | ----------- | ------------- | ---------- | -------- | -------- | ------- | ------------- | ------------- | ------ | ------ | ----- |
| triposr       | 0.0         | 0.5           | 40.8       | 45.7     | 1.1      | 29.4    | 0.0           | 0.0           | 0.0    | 0.0    | 117.9 |
| step1x3d-rep1 | 0.0         | 0.1           | 40.7       | 328.4    | 0.1      | 1.1     | 0.0           | 0.0           | 2.7    | 0.0    | 373.5 |
| step1x3d-rep2 | 0.0         | 0.2           | 40.6       | 331.8    | 0.1      | 1.1     | 0.0           | 0.0           | 2.7    | 0.0    | 376.9 |
| step1x3d-rep3 | 0.0         | 0.2           | 40.7       | 349.9    | 0.1      | 1.1     | 0.0           | 0.0           | 2.6    | 0.0    | 395.0 |
