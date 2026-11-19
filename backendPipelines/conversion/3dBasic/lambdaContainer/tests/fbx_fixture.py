#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A minimal ASCII FBX 7.4 file generated from code: one mesh (a box of the given extents, as quads),
optionally with a UV set and a diffuse texture by relative file name, and a unit scale. Enough for
Assimp's FBX importer; nothing third-party is committed."""


def box_fbx(extents=(2.0, 3.0, 4.0), texture=None, unit_scale_cm=1.0):
    x, y, z = (e / 2.0 for e in extents)
    points = [(-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z),
              (-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    vertices = ",".join(f"{c:g}" for p in points for c in p)
    # FBX marks the last index of each polygon by its bitwise complement.
    indices = ",".join(",".join(str(i) for i in q[:-1]) + f",{-q[-1] - 1}" for q in quads)
    uv_layer = ""
    if texture:
        uvs = ",".join("0,0,1,0,1,1,0,1" for _ in quads)
        uv_index = ",".join(str(i) for i in range(24))
        uv_layer = f"""
        LayerElementUV: 0 {{
            Version: 101
            Name: "UVMap"
            MappingInformationType: "ByPolygonVertex"
            ReferenceInformationType: "IndexToDirect"
            UV: *48 {{
                a: {uvs}
            }}
            UVIndex: *24 {{
                a: {uv_index}
            }}
        }}
        LayerElementMaterial: 0 {{
            Version: 101
            Name: ""
            MappingInformationType: "AllSame"
            ReferenceInformationType: "IndexToDirect"
            Materials: *1 {{
                a: 0
            }}
        }}"""
    layer_uv = """
            LayerElement:  {
                Type: "LayerElementUV"
                TypedIndex: 0
            }
            LayerElement:  {
                Type: "LayerElementMaterial"
                TypedIndex: 0
            }""" if texture else ""
    texture_objects = ""
    texture_connections = ""
    if texture:
        texture_objects = f"""
    Material: 3000, "Material::mat", "" {{
        Version: 102
        ShadingModel: "phong"
        Properties70:  {{
            P: "DiffuseColor", "Color", "", "A",1,1,1
        }}
    }}
    Texture: 4000, "Texture::tex", "" {{
        Type: "TextureVideoClip"
        Version: 202
        TextureName: "Texture::tex"
        FileName: "{texture}"
        RelativeFilename: "{texture}"
    }}"""
        texture_connections = """
    C: "OO",3000,2000
    C: "OP",4000,3000, "DiffuseColor\""""
    return f"""; FBX 7.4.0 project file
FBXHeaderExtension:  {{
    FBXHeaderVersion: 1003
    FBXVersion: 7400
}}
GlobalSettings:  {{
    Version: 1000
    Properties70:  {{
        P: "UpAxis", "int", "Integer", "",1
        P: "UpAxisSign", "int", "Integer", "",1
        P: "FrontAxis", "int", "Integer", "",2
        P: "FrontAxisSign", "int", "Integer", "",1
        P: "CoordAxis", "int", "Integer", "",0
        P: "CoordAxisSign", "int", "Integer", "",1
        P: "UnitScaleFactor", "double", "Number", "",{unit_scale_cm:g}
    }}
}}
Objects:  {{
    Geometry: 1000, "Geometry::box", "Mesh" {{
        Vertices: *24 {{
            a: {vertices}
        }}
        PolygonVertexIndex: *24 {{
            a: {indices}
        }}
        GeometryVersion: 124{uv_layer}
        Layer: 0 {{
            Version: 100{layer_uv}
        }}
    }}
    Model: 2000, "Model::box", "Mesh" {{
        Version: 232
    }}{texture_objects}
}}
Connections:  {{
    C: "OO",2000,0
    C: "OO",1000,2000{texture_connections}
}}
""".encode("utf-8")
