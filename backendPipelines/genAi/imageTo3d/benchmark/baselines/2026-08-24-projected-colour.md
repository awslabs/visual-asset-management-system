# Image to 3D -- recorded round

colour projected from the photograph, live: mug and person, a same-round no-projection control, and one print-feature configuration

Container built from `ff07df88c422` (Give a geometry-only mesh colour by projecting the photograph onto it).

## Outcome

| run                | model       | instance   | texture                 | ready | bodies/exp | fused | fill   | fpAspect | h/w    | faces  |
| ------------------ | ----------- | ---------- | ----------------------- | ----- | ---------- | ----- | ------ | -------- | ------ | ------ |
| mug-projected      | stable3dgen | g5.8xlarge | projectedFromPhotograph | True  | 1/1        | 1     | 0.2166 | 1.224    | 0.7577 | 188244 |
| person-projected   | stable3dgen | g5.8xlarge | projectedFromPhotograph | True  | 1/1        | 0     | 0.5782 | 1.4078   | 4.8494 | 135598 |
| person-plain       | stable3dgen | g5.8xlarge | none                    | True  | 1/1        | 0     | 0.5782 | 1.4076   | 4.8484 | 135568 |
| mug-projected-loop | stable3dgen | g5.8xlarge | projectedFromPhotograph | True  | 3/3        | 1     | 0.2038 | 1.0      | 0.6296 | 191778 |

## Stage seconds

| run                | weightCache | inputDownload | preprocess | generate | meshLoad | cleanup | projectColour | printFeatures | export | upload | total |
| ------------------ | ----------- | ------------- | ---------- | -------- | -------- | ------- | ------------- | ------------- | ------ | ------ | ----- |
| mug-projected      | 0.0         | 0.1           | 60.6       | 44.4     | 0.3      | 24.6    | 0.6           | 0.0           | 1.3    | 0.0    | 132.6 |
| person-projected   | 0.0         | 0.1           | 59.2       | 42.6     | 0.1      | 13.7    | 0.7           | 0.0           | 1.0    | 0.0    | 118.1 |
| person-plain       | 0.0         | 0.2           | 60.1       | 42.2     | 0.1      | 15.2    | 0.0           | 0.0           | 0.4    | 0.0    | 118.6 |
| mug-projected-loop | 0.0         | 0.1           | 60.4       | 44.6     | 0.3      | 22.5    | 0.6           | 2.8           | 1.4    | 0.0    | 133.3 |
