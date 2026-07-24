"""Runs inside Blender: a .gltf plus its sidecars in, one self-contained GLB out.

    blender --background --python blender_convert.py -- <in.gltf> <out.glb>

Poly Haven ships glTF with separate .bin and texture files. A GLB with the
buffers and images embedded is one file the preview layer lists, the rig layer
accepts and a browser loads in a single request.
"""

import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
source, target = argv[0], argv[1]

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=source)
if not any(o.type == "MESH" for o in bpy.context.scene.objects):
    raise SystemExit("the glTF holds no mesh")
bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.gltf(filepath=target, export_format="GLB", export_animations=True)
