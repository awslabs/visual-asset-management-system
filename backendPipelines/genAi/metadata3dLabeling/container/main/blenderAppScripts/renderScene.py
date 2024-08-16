# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: GPLv3

import os
import bpy
from math import *
from mathutils import *
#import bmesh
import os
import sys
#import numpy as np
import time

# Clean default scene
def clear_scene():
    scene = bpy.context.scene

    # delete all objects in scene
    for deleteObject in bpy.data.objects:
        bpy.data.objects.remove(deleteObject, do_unlink=True)
        
    # Delete all materials in scene
    for material in bpy.data.materials:
        material.user_clear()
        bpy.data.materials.remove(material)

    # delete unused cameras from data
    for camera_test in bpy.data.cameras:
        if camera_test.users == 0:
            bpy.data.cameras.remove(camera_test)
    
    return scene

#Point an object at another object (such as pointing a cmera towards an object)
def point_at(obj, target, roll=0):
    """
    Rotate obj to look at target

    :arg obj: the object to be rotated. Usually the camera
    :arg target: the location (3-tuple or Vector) to be looked at
    :arg roll: The angle of rotation about the axis from obj to target in radians.   
    """
    if not isinstance(target, Vector):
        target = Vector(target)
    loc = obj.location
    # direction points from the object to the target
    direction = target - loc
    tracker, rotator = (('-Z', 'Y'),'Z') if obj.type=='CAMERA' else (('X', 'Z'),'Y') #because new cameras points down(-Z), usually meshes point (-Y)
    quat = direction.to_track_quat(*tracker)

    # /usr/share/blender/scripts/addons/add_advanced_objects_menu/arrange_on_curve.py
    quat = quat.to_matrix().to_4x4()
    rollMatrix = Matrix.Rotation(roll, 4, rotator)

    # remember the current location, since assigning to obj.matrix_world changes it
    loc = loc.to_tuple()
    #obj.matrix_world = quat * rollMatrix
    # in blender 2.8 and above @ is used to multiply matrices
    # using * still works but results in unexpected behaviour!
    obj.matrix_world = quat @ rollMatrix
    obj.location = loc
    
#Set mesh origin to bottom of it's bounding box
def origin_to_bottom(ob, matrix=Matrix()):
    me = ob.data
    mw = ob.matrix_world
    local_verts = [matrix @ Vector(v[:]) for v in ob.bound_box]
    o = sum(local_verts, Vector()) / 8
    o.z = min(v.z for v in local_verts)
    o = matrix.inverted() @ o
    me.transform(Matrix.Translation(-o))
    mw.translation = mw @ o

#Rotate and render around the scene
def rotate_and_render(output_dir, output_file_pattern_string = 'render%d.jpg', rotation_steps = 4, rotation_angle = 360.0, subject = bpy.context.object):
    
    #Start with the default starting position render and move camera so all selected objects are in squarely in viewport
    subject.rotation_euler = (0,0,0)
    bpy.ops.view3d.camera_to_view_selected()
    bpy.context.scene.render.filepath = os.path.join(output_dir, (output_file_pattern_string % 0 + '-Origin'))
    bpy.ops.render.render(write_still = True)
    
    adjustViewFirstStep = False
    for step in range(1, rotation_steps): #start range from 1 as the zero degree was already covered by the first stage
        subject.rotation_euler[2] = radians(step * (rotation_angle / rotation_steps))
        if adjustViewFirstStep == False:
            bpy.ops.view3d.camera_to_view_selected() #reposition camera to have subject in view
            adjustViewFirstStep == True
        bpy.context.scene.render.filepath = os.path.join(output_dir, (output_file_pattern_string % step + '-ZStep'))
        bpy.ops.render.render(write_still = True)
    subject.rotation_euler = (0,0,0)

    adjustViewFirstStep = False
    for step in range(1, rotation_steps): #start range from 1 as the zero degree was already covered by the first stage
        subject.rotation_euler[0] = radians(step * (rotation_angle / rotation_steps))
        if adjustViewFirstStep == False:
            bpy.ops.view3d.camera_to_view_selected() #reposition camera to have subject in view
            adjustViewFirstStep == True
        bpy.context.scene.render.filepath = os.path.join(output_dir, (output_file_pattern_string % step + '-XStep'))
        bpy.ops.render.render(write_still = True)
    subject.rotation_euler = (0,0,0)

    adjustViewFirstStep = False
    for step in range(1, rotation_steps):
        subject.rotation_euler[1] = radians(step * (rotation_angle / rotation_steps)) #start range from 1 as the zero degree was already covered by the first stage
        if adjustViewFirstStep == False:
            bpy.ops.view3d.camera_to_view_selected() #reposition camera to have subject in view
            adjustViewFirstStep == True
        bpy.context.scene.render.filepath = os.path.join(output_dir, (output_file_pattern_string % step + '-YStep'))
        bpy.ops.render.render(write_still = True)
    subject.rotation_euler = (0,0,0)


def print_line_break():
    print('--------------------')


startTime = time.monotonic()

## Store arguments
argv = sys.argv
argv = argv[argv.index("--") + 1:]  # get all args after "--"

# import path for model file
inputFilePath = argv[0]

# directory path for export images
imageDirectoryOutputDir = argv[1]

## Clean default scene (remove cube)
# scene ref
scene = clear_scene()

print_line_break()


# import photogrammetry data
print("Import model data:")
importExtension = inputFilePath.split('.').pop()
if importExtension.lower() == 'fbx':
    # import fbx to scene
    bpy.ops.import_scene.fbx(filepath=inputFilePath)
elif importExtension.lower() == 'glb':
    # import glb to scene
    bpy.ops.import_scene.gltf(filepath=inputFilePath)
elif importExtension.lower() == 'obj':
    # import obj to scene
    bpy.ops.wm.obj_import(filepath=inputFilePath)
elif importExtension.lower() == 'stl':
    # import stl to scene
    bpy.ops.wm.stl_import(filepath=inputFilePath)
elif importExtension.lower() == 'ply':
    # import ply to scene
    bpy.ops.wm.ply_import(filepath=inputFilePath)
elif importExtension.lower() == 'usd':
    # import usd to scene
    bpy.ops.wm.bpy.ops.wm.usd_import(filepath=inputFilePath)
elif importExtension.lower() == 'dae':
    # import dae to scene
    bpy.ops.wm.bpy.ops.wm.collada_import(filepath=inputFilePath)
elif importExtension.lower() == 'abc':
    # import abc to scene
    bpy.ops.wm.bpy.ops.wm.alembic_import(filepath=inputFilePath)

# cache list of imported objects
importedMeshObjects = []
# deselect all objects
bpy.ops.object.select_all(action='DESELECT')

for importedObject in bpy.data.objects:
    # link objects to the scene
    if importedObject.type == 'MESH':
        print(importedObject.name)
        importedMeshObjects.append(importedObject.name) 
        # select objects with meshes
        importedObject.select_set(True)  
        bpy.context.view_layer.objects.active = importedObject

#Save Selected imported object
obj_object = bpy.context.selected_objects[-1]

#create and setup 6 plane meshes around origin that will act as light sources
planes = []
bpy.ops.mesh.primitive_plane_add(size=5, scale=(2,2,2), location=(0, -20, 1), rotation=(radians(90),radians(270),0))
planes.append(bpy.context.active_object)
bpy.ops.mesh.primitive_plane_add(size=5, scale=(2,2,2), location=(-20, 0, 1), rotation=(0,radians(270),0))
planes.append(bpy.context.active_object)
bpy.ops.mesh.primitive_plane_add(size=5, scale=(2,2,2), location=(0, 20, 1), rotation=(radians(-90),radians(90),0))
planes.append(bpy.context.active_object)
bpy.ops.mesh.primitive_plane_add(size=5, scale=(2,2,2), location=(20, 0, 1), rotation=(0,radians(90),0))
planes.append(bpy.context.active_object)
bpy.ops.mesh.primitive_plane_add(size=5, scale=(2,2,2), location=(0, 0, 20), rotation=(0,0,0))
planes.append(bpy.context.active_object)
bpy.ops.mesh.primitive_plane_add(size=5, scale=(2,2,2), location=(0, 0, -20), rotation=(0,radians(180),0))
planes.append(bpy.context.active_object)

mat = bpy.data.materials.new(name="LightPlane")
mat.use_nodes=True
TreeNodes=mat.node_tree
links = TreeNodes.links
mat.node_tree.links.clear()
mat.node_tree.nodes.clear()
shOut = TreeNodes.nodes.new('ShaderNodeOutputMaterial')
emission = TreeNodes.nodes.new('ShaderNodeEmission')
emission.inputs[0].default_value = (1, 1, 1, 1)
emission.inputs[1].default_value = 10
mat.alpha_threshold = 0
mat.roughness = 0.6
links.new(emission.outputs[0], shOut.inputs[0]) 

for plane in planes:
    plane.visible_camera = False
    if plane.data.materials:
        # assign to 1st material slot
        plane.data.materials[0] = mat
    else:
        # no slots
        plane.data.materials.append(mat)
    
# deselect all objects
bpy.ops.object.select_all(action='DESELECT')

#Set objects mesh origin to bottom center of it's bounding box
origin_to_bottom(obj_object)
#obj_object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')

#Adjust object location to origin
obj_object.location = (0, 0, 0)

#Set rotation mode to XYZ euler of object and reset rotation of object
obj_object.rotation_mode = 'XYZ'
obj_object.rotation_euler = (0,0,0)

#Set base color on object in OBJ
#if importExtension.lower() == 'obj':
#    obj_object.active_material.diffuse_color = (.6, .6, .6, 1)
#    obj_object.active_material.roughness = 0.5

#Scale object to 3x3x3 bounds
objBounds = obj_object.matrix_world.to_quaternion() @ obj_object.dimensions
ratio = abs(3) / abs(objBounds.y)
obj_object.scale *= ratio


#Create new camera
camera_data = bpy.data.cameras.new("Camera")
camera = bpy.data.objects.new("Camera", camera_data)
camera_data.clip_start = 0.001
camera.location = (4, 0, 2)
scene.collection.objects.link(camera)

#Set main camera
scene.camera = camera

#Set render settings
bpy.context.scene.render.film_transparent = True
bpy.context.scene.view_settings.view_transform = 'Standard'
bpy.context.scene.cycles.use_denoising = False
bpy.context.scene.cycles.samples = 128
bpy.context.scene.cycles.volume_max_steps = 64
bpy.context.scene.cycles.max_bounces = 1
bpy.context.scene.cycles.sample_clamp_direct = 0
bpy.context.scene.cycles.caustics_reflective = False
bpy.context.scene.cycles.caustics_refractive = False
bpy.context.scene.cycles.debug_use_spatial_splits = True
bpy.context.scene.cycles.use_fast_gi = True
bpy.context.scene.cycles.blur_glossy = 10
bpy.context.scene.render.use_simplify = True
bpy.context.scene.render.resolution_x = 1080
bpy.context.scene.render.resolution_y = 608

#Point camera at object
point_at(camera, obj_object.location)

#Reselect only the imported mesh
for obj in bpy.context.selected_objects:
    obj.select_set(False)
obj_object.select_set(True)  

#Move camera so all selected objects are in squarely in viewport
#bpy.ops.view3d.camera_to_view_selected()

#Back out camera field of view to hopefully get everything in frame
camera_data.lens = 25

#Rotate object and render
rotate_and_render(imageDirectoryOutputDir, 'render%d.jpg', rotation_steps = 4, subject = obj_object)

print_line_break()
elapsedTime = time.monotonic() - startTime

print(f"Done. Elapsed time: {elapsedTime}")