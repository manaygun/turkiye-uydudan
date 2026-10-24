"""Blender script: build a dark-studio 3D scene for an explainer and render it frame by frame.

Run by explain.py, not by hand:
  blender -b --factory-startup -P scene3d.py -- PLAN.json OUT_DIR FIRST_FRAME LAST_FRAME [STEP]

PLAN.json: {"fps": 24, "total": 18.2, "width": 540, "height": 960, "samples": 6,
            "beats": [{"start": 0.0, "dur": 2.4, "show": {...}, "camera": "snap", "light": "warning"}, ...],
            "icons": {"📱": "icons/0.png", ...}}          (icon paths relative to PLAN.json)

Look: a matte charcoal void (#0B0E14) with a glowing wireframe grid on the floor. Every beat
gets a "station" along the X axis; the camera glides between them in one continuous shot.
  light   calm (cyan) | warning (red) | amber   -- grid, neon rims and rim lights follow it
  camera  glide | snap (fast push-in) | orbit (90°) | orbit180 | top (tilts to top-down)
The last beat of type "loop" flies the camera back to exactly the Frame 0 pose, so the Reel
loops seamlessly; station 1 is fully built from Frame 0 for the same reason.
"""

import json
import math
import random
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

SPACING = 12.0          # distance between stations along X
VOID = (0.0034, 0.0044, 0.0070)     # #0B0E14 in linear light
MOODS = {
    "calm": (0.08, 0.72, 1.0),
    "warning": (1.0, 0.1, 0.06),
    "amber": (1.0, 0.5, 0.04),
}


# ---------------------------------------------------------------- small helpers

def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease_out(x):
    x = clamp(x)
    return 1 - (1 - x) ** 3


def ease_in_out(x):
    x = clamp(x)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def pop(t, delay=0.0, d=0.45):
    """0 -> 1 with a slight overshoot, for objects appearing."""
    x = clamp((t - delay) / d)
    if x <= 0:
        return 0.0
    return 1 + 0.12 * math.sin(x * math.pi) if x < 1 else 1.0


def lerp(a, b, t):
    return a + (b - a) * t


def set_input(node, names, value):
    for n in names if isinstance(names, (list, tuple)) else [names]:
        if n in node.inputs:
            node.inputs[n].default_value = value
            return


_materials = {}


def mat(name, color=(0.8, 0.8, 0.8), rough=0.5, metal=0.0, emit=None, emit_strength=0.0, transmission=0.0):
    key = (name, color, rough, metal, emit, emit_strength, transmission)
    if key in _materials:
        return _materials[key]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    set_input(bsdf, "Base Color", (*color, 1))
    set_input(bsdf, "Roughness", rough)
    set_input(bsdf, "Metallic", metal)
    if emit:
        set_input(bsdf, ["Emission Color", "Emission"], (*emit, 1))
        set_input(bsdf, "Emission Strength", emit_strength)
    if transmission:
        set_input(bsdf, ["Transmission Weight", "Transmission"], transmission)
        set_input(bsdf, "IOR", 1.45)
    _materials[key] = m
    return m


def assign(obj, material):
    obj.data.materials.clear()
    obj.data.materials.append(material)
    return obj


def smooth(obj):
    for p in getattr(obj.data, "polygons", []):
        p.use_smooth = True
    return obj


def bevel(obj, width=0.04, segments=3):
    mod = obj.modifiers.new("bevel", "BEVEL")
    mod.width = width
    mod.segments = segments
    return obj


def cube(size, loc, material, parent=None, bev=0.04):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.object
    # scale the mesh itself; transform_apply would also bake the scale of every other selected object
    o.data.transform(Matrix.Diagonal((*size, 1.0)))
    if bev:
        bevel(o, bev)
    assign(o, material)
    if parent:
        o.parent = parent
    return o


def cylinder(r, depth, loc, material, parent=None, verts=32, rot=(0, 0, 0)):
    bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=depth, location=loc, vertices=verts, rotation=rot)
    o = smooth(assign(bpy.context.object, material))
    if parent:
        o.parent = parent
    return o


def sphere(r, loc, material, parent=None, seg=32):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=loc, segments=seg, ring_count=seg // 2)
    o = smooth(assign(bpy.context.object, material))
    if parent:
        o.parent = parent
    return o


def torus(major, minor, loc, material, parent=None, rot=(0, 0, 0)):
    bpy.ops.mesh.primitive_torus_add(major_radius=major, minor_radius=minor, location=loc, rotation=rot,
                                     major_segments=48, minor_segments=8)
    o = smooth(assign(bpy.context.object, material))
    if parent:
        o.parent = parent
    return o


def empty(name, loc):
    o = bpy.data.objects.new(name, None)
    o.location = loc
    bpy.context.collection.objects.link(o)
    return o


def tube(points, radius, material, name="cable"):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = radius
    cu.bevel_resolution = 4
    sp = cu.splines.new("POLY")
    sp.points.add(len(points) - 1)
    for p, co in zip(sp.points, points):
        p.co = (*co, 1)
    o = bpy.data.objects.new(name, cu)
    bpy.context.collection.objects.link(o)
    o.data.materials.append(material)
    return o


def text3d(body, loc, size, material, extrude=0.12):
    cu = bpy.data.curves.new("txt", "FONT")
    cu.body = body
    cu.size = size
    cu.extrude = extrude
    cu.bevel_depth = 0.02
    cu.align_x = "CENTER"
    cu.align_y = "CENTER"
    o = bpy.data.objects.new("txt", cu)
    bpy.context.collection.objects.link(o)
    o.location = loc
    o.rotation_euler = (math.radians(90), 0, 0)
    o.data.materials.append(material)
    return o


# ---------------------------------------------------------------- materials

M = {}
MOOD_NODES = []         # RGB nodes recoloured every frame with the current mood colour


def mood_material(name, strength, base=(0.0, 0.0, 0.0)):
    """Emissive material whose colour follows the lighting mood."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    set_input(bsdf, "Base Color", (*base, 1))
    rgb = nt.nodes.new("ShaderNodeRGB")
    rgb.outputs[0].default_value = (*MOODS["calm"], 1)
    nt.links.new(rgb.outputs[0], bsdf.inputs["Emission Color"] if "Emission Color" in bsdf.inputs else bsdf.inputs["Emission"])
    set_input(bsdf, "Emission Strength", strength)
    MOOD_NODES.append(rgb)
    return m


def grid_material():
    """Matte charcoal floor with thin glowing grid lines, fading into the distance."""
    m = bpy.data.materials.new("grid")
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    set_input(bsdf, "Base Color", (*VOID, 1))
    set_input(bsdf, "Roughness", 0.6)
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(geo.outputs["Position"], sep.inputs[0])

    def line(axis, width=0.014):
        fr = nt.nodes.new("ShaderNodeMath")
        fr.operation = "FRACT"
        nt.links.new(sep.outputs[axis], fr.inputs[0])
        sub = nt.nodes.new("ShaderNodeMath")
        sub.operation = "SUBTRACT"
        sub.inputs[1].default_value = 0.5
        nt.links.new(fr.outputs[0], sub.inputs[0])
        ab = nt.nodes.new("ShaderNodeMath")
        ab.operation = "ABSOLUTE"
        nt.links.new(sub.outputs[0], ab.inputs[0])
        gt = nt.nodes.new("ShaderNodeMath")
        gt.operation = "GREATER_THAN"
        gt.inputs[1].default_value = 0.5 - width
        nt.links.new(ab.outputs[0], gt.inputs[0])
        return gt

    mx = nt.nodes.new("ShaderNodeMath")
    mx.operation = "MAXIMUM"
    nt.links.new(line("X").outputs[0], mx.inputs[0])
    nt.links.new(line("Y").outputs[0], mx.inputs[1])
    fade = nt.nodes.new("ShaderNodeMapRange")          # dimmer further back
    set_input(fade, "From Min", 2.0)
    set_input(fade, "From Max", 40.0)
    set_input(fade, "To Min", 1.0)
    set_input(fade, "To Max", 0.0)
    nt.links.new(sep.outputs["Y"], fade.inputs["Value"])
    strength = nt.nodes.new("ShaderNodeMath")
    strength.operation = "MULTIPLY"
    nt.links.new(mx.outputs[0], strength.inputs[0])
    nt.links.new(fade.outputs[0], strength.inputs[1])
    scale = nt.nodes.new("ShaderNodeMath")
    scale.operation = "MULTIPLY"
    scale.inputs[1].default_value = 1.3
    nt.links.new(strength.outputs[0], scale.inputs[0])
    rgb = nt.nodes.new("ShaderNodeRGB")
    rgb.outputs[0].default_value = (*MOODS["calm"], 1)
    MOOD_NODES.append(rgb)
    nt.links.new(rgb.outputs[0], bsdf.inputs["Emission Color"] if "Emission Color" in bsdf.inputs else bsdf.inputs["Emission"])
    nt.links.new(scale.outputs[0], bsdf.inputs["Emission Strength"])
    return m


def build_materials():
    M["dark"] = mat("dark", (0.025, 0.028, 0.035), 0.3)
    M["white"] = mat("white", (0.85, 0.87, 0.9), 0.35)
    M["grey"] = mat("grey", (0.4, 0.43, 0.48), 0.4)
    M["steel"] = mat("steel", (0.7, 0.72, 0.75), 0.25, 0.9)
    M["copper"] = mat("copper", (0.85, 0.45, 0.25), 0.3, 1.0)
    M["gold"] = mat("gold", (0.9, 0.7, 0.25), 0.3, 1.0)
    M["screen"] = mat("screen", (0.1, 0.3, 0.6), 0.2, emit=(0.25, 0.55, 1.0), emit_strength=2.0)
    M["window"] = mat("window", (0.9, 0.8, 0.5), 0.2, emit=(1.0, 0.85, 0.5), emit_strength=1.5)
    M["cyan"] = mat("cyan", (0.2, 0.8, 1.0), 0.3, emit=(0.3, 0.85, 1.0), emit_strength=3.0)
    M["yellow"] = mat("yellow", (1.0, 0.8, 0.3), 0.3, emit=(1.0, 0.8, 0.3), emit_strength=3.0)
    M["green_led"] = mat("led", (0.2, 1.0, 0.4), 0.3, emit=(0.2, 1.0, 0.4), emit_strength=6)
    M["red"] = mat("red", (0.75, 0.1, 0.08), 0.35)
    M["blue_panel"] = mat("panel", (0.08, 0.18, 0.45), 0.25, 0.4)
    M["water"] = mat("water", (0.05, 0.3, 0.45), 0.05, transmission=0.8)
    M["sun"] = mat("sun", (1, 0.85, 0.4), 0.5, emit=(1.0, 0.8, 0.35), emit_strength=8)
    M["cloud"] = mat("cloud", (0.8, 0.82, 0.86), 0.9)
    M["storm"] = mat("storm", (0.25, 0.27, 0.32), 0.9)
    M["pedestal"] = mat("pedestal", (0.03, 0.035, 0.045), 0.2, 0.3)
    M["number"] = mat("number", (1.0, 0.8, 0.3), 0.3, emit=(1.0, 0.75, 0.25), emit_strength=2.5)
    M["glass"] = mat("glass", (0.03, 0.04, 0.06), 0.3, transmission=0.45)      # dark frosted glass
    M["rim"] = mood_material("rim", 6.0)                                        # neon edges, follow the mood
    M["grid"] = grid_material()
    for i, c in enumerate([(0.3, 0.85, 1.0), (1.0, 0.8, 0.3), (1.0, 0.45, 0.55), (0.5, 0.9, 0.6), (0.75, 0.6, 1.0)]):
        M[f"bit{i}"] = mat(f"bit{i}", c, 0.3, emit=c, emit_strength=1.5)


# ---------------------------------------------------------------- objects (icon -> 3D model)

def obj_phone(root):
    cube((0.9, 0.12, 1.8), (0, 0, 0.95), M["dark"], root, bev=0.12)
    cube((0.78, 0.02, 1.6), (0, -0.07, 0.95), M["screen"], root, bev=0.0)
    return 2.0


def obj_router(root):
    cube((1.6, 1.0, 0.35), (0, 0, 0.25), M["white"], root, bev=0.08)
    for x in (-0.55, 0, 0.55):
        cylinder(0.05, 1.0, (x, 0.35, 0.85), M["dark"], root, verts=12)
    for x in (-0.4, -0.2, 0.0, 0.2):
        sphere(0.04, (x, -0.51, 0.3), M["green_led"], root, seg=8)
    return 1.4


def obj_building(root):
    cube((2.2, 2.0, 4.2), (0, 0, 2.1), M["grey"], root, bev=0.05)
    for row in range(5):
        for col in range(3):
            cube((0.42, 0.04, 0.5), (-0.65 + col * 0.65, -1.01, 0.7 + row * 0.75), M["window"], root, bev=0)
    return 4.3


def obj_house(root):
    cube((2.0, 1.8, 1.4), (0, 0, 0.7), M["white"], root, bev=0.04)
    bpy.ops.mesh.primitive_cone_add(vertices=4, radius1=1.65, depth=1.0, location=(0, 0, 1.9), rotation=(0, 0, math.radians(45)))
    roof = assign(bpy.context.object, M["red"])
    roof.parent = root
    cube((0.45, 0.04, 0.45), (-0.5, -0.91, 0.8), M["window"], root, bev=0)
    cube((0.4, 0.04, 0.8), (0.45, -0.91, 0.4), M["dark"], root, bev=0)
    return 2.5


def obj_server(root):
    for x in (-0.7, 0.7):
        cube((1.1, 1.0, 3.0), (x, 0, 1.5), M["dark"], root, bev=0.05)
        for k in range(9):
            cube((0.8, 0.03, 0.05), (x, -0.51, 0.35 + k * 0.3), M["cyan" if k % 3 else "green_led"], root, bev=0)
    return 3.1


def obj_tower(root):
    """Lattice mast: four tapering legs, rings and diagonal bracing, antennas and a beacon."""
    def corner(z, k):
        w = 0.85 - 0.7 * z / 5.0
        return ((w if k in (0, 3) else -w), (w if k in (0, 1) else -w), z)
    for k in range(4):
        tube([corner(0, k), corner(5.0, k)], 0.05, M["steel"], "leg").parent = root
    levels = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    for a, b in zip(levels, levels[1:]):
        for k in range(4):
            tube([corner(b, k), corner(b, (k + 1) % 4)], 0.03, M["steel"], "ring").parent = root
            tube([corner(a, k), corner(b, (k + 1) % 4)], 0.025, M["steel"], "brace").parent = root
    for z, ang in ((4.4, 0), (3.8, 120), (3.2, 240)):
        a = math.radians(ang)
        cube((0.2, 0.12, 0.75), (0.4 * math.cos(a), 0.4 * math.sin(a), z), M["white"], root, bev=0.03)
    sphere(0.1, (0, 0, 5.15), M["red"], root, seg=12)
    return 5.3


def obj_satellite(root):
    cube((0.9, 0.9, 1.0), (0, 0, 1.4), M["gold"], root, bev=0.05)
    for side in (-1, 1):
        cube((2.0, 0.05, 0.8), (side * 1.55, 0, 1.4), M["blue_panel"], root, bev=0.01)
        cylinder(0.04, 0.6, (side * 0.6, 0, 1.4), M["steel"], root, rot=(0, math.radians(90), 0), verts=8)
    sphere(0.25, (0, -0.55, 1.9), M["white"], root, seg=16)
    return 2.2


def globe_material():
    if "globe" in _materials:
        return _materials["globe"]
    m = bpy.data.materials.new("globe")
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    set_input(bsdf, "Roughness", 0.6)
    noise = nt.nodes.new("ShaderNodeTexNoise")
    set_input(noise, "Scale", 2.2)
    set_input(noise, "Detail", 6)
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.52, (0.05, 0.25, 0.6, 1)
    els[1].position, els[1].color = 0.56, (0.2, 0.55, 0.2, 1)
    nt.links.new(noise.outputs[0], ramp.inputs[0])
    nt.links.new(ramp.outputs[0], bsdf.inputs["Base Color"])
    _materials["globe"] = m
    return m


def obj_globe(root):
    assign(sphere(1.3, (0, 0, 1.5), M["white"], root, seg=48), globe_material())
    return 2.8


def obj_sun(root):
    sphere(1.0, (0, 0, 2.5), M["sun"], root, seg=32)
    return 3.5


def obj_cloud(root, storm=False):
    rnd = random.Random(7)
    for _ in range(7):
        sphere(0.55 + rnd.random() * 0.4, (rnd.uniform(-1.2, 1.2), rnd.uniform(-0.4, 0.4), 3.0 + rnd.uniform(-0.3, 0.4)),
               M["storm" if storm else "cloud"], root, seg=16)
    if storm:
        pts = [(0, -0.3, 2.6), (0.35, -0.3, 1.9), (-0.1, -0.3, 1.6), (0.3, -0.3, 0.8), (0.0, -0.3, 0.1)]
        tube(pts, 0.07, M["yellow"], "bolt").parent = root
    return 3.8


def obj_bolt(root):
    pts = [(0, 0, 3.6), (0.5, 0, 2.6), (-0.15, 0, 2.2), (0.45, 0, 1.0), (0.0, 0, 0.1)]
    tube(pts, 0.12, M["yellow"], "bolt").parent = root
    return 3.7


def obj_person(root):
    cylinder(0.35, 1.2, (0, 0, 0.9), M["blue_panel"], root)
    sphere(0.32, (0, 0, 1.85), mat("skin", (0.85, 0.65, 0.5), 0.6), root)
    for x in (-0.15, 0.15):
        cylinder(0.12, 0.6, (x, 0, 0.3), M["dark"], root, verts=12)
    return 2.2


def obj_card(root):
    c = cube((1.7, 0.05, 1.08), (0, 0, 1.2), mat("cardblue", (0.1, 0.3, 0.8), 0.3), root, bev=0.05)
    c.rotation_euler = (0, math.radians(-12), 0)
    cube((0.3, 0.02, 0.24), (-0.5, -0.04, 1.3), M["gold"], root, bev=0.01)
    return 1.8


def obj_terminal(root):
    cube((1.0, 1.2, 0.5), (0, 0, 0.25), M["dark"], root, bev=0.06)
    s = cube((0.8, 0.04, 0.5), (0, -0.2, 0.62), M["screen"], root, bev=0.01)
    s.rotation_euler = (math.radians(-30), 0, 0)
    return 1.0


def obj_coil(root):
    pts = []
    for i in range(160):
        a = i / 160 * math.pi * 2 * 8
        pts.append((0.9 * math.cos(a), 0.9 * math.sin(a), 0.2 + i / 160 * 1.6))
    tube(pts, 0.07, M["copper"], "coil").parent = root
    return 2.0


def obj_magnet(root):
    torus(0.8, 0.25, (0, 0, 1.4), M["red"], root, rot=(math.radians(90), 0, 0))
    cube((1.6, 0.6, 0.7), (0, 0, 0.6), M["grey"], root)
    return 2.4


def obj_battery(root):
    cylinder(0.6, 2.0, (0, 0, 1.0), M["dark"], root)
    cylinder(0.61, 0.6, (0, 0, 0.4), M["green_led"], root)
    cylinder(0.2, 0.2, (0, 0, 2.1), M["steel"], root)
    return 2.2


def obj_plane(root):
    cylinder(0.32, 2.5, (0, 0, 0.42), M["white"], root, rot=(0, math.radians(90), 0))
    sphere(0.32, (1.25, 0, 0.42), M["white"], root)
    cube((0.7, 2.8, 0.07), (0.05, 0, 0.38), M["grey"], root, bev=0.03)
    cube((0.42, 0.06, 0.65), (-1.1, 0, 0.85), M["red"], root, bev=0.03)
    cube((0.38, 1.1, 0.05), (-1.08, 0, 0.48), M["grey"], root, bev=0.02)
    return 1.2


def obj_car(root):
    cube((2.2, 1.1, 0.6), (0, 0, 0.55), M["red"], root, bev=0.15)
    cube((1.2, 1.0, 0.5), (-0.1, 0, 1.05), M["red"], root, bev=0.15)
    for x in (-0.7, 0.7):
        for y in (-0.55, 0.55):
            cylinder(0.28, 0.2, (x, y, 0.28), M["dark"], root, rot=(math.radians(90), 0, 0), verts=16)
    return 1.4


def obj_ice(root):
    for x, z, r in ((-0.45, 0.5, 0.2), (0.45, 0.45, -0.3), (0.0, 1.3, 0.5)):
        c = cube((0.8, 0.8, 0.8), (x, 0, z), mat("ice", (0.7, 0.9, 1.0), 0.05, transmission=0.9), root, bev=0.12)
        c.rotation_euler = (0, 0, r)
    return 1.8


def obj_microwave(root):
    cube((1.8, 1.2, 1.1), (0, 0, 0.55), M["white"], root, bev=0.06)
    cube((1.1, 0.04, 0.75), (-0.2, -0.6, 0.55), M["dark"], root, bev=0.03)
    return 1.2


def obj_bulb(root):
    sphere(0.7, (0, 0, 1.9), M["yellow"], root)
    cylinder(0.32, 0.6, (0, 0, 1.0), M["steel"], root)
    return 2.6


def obj_tile(root, png):
    """Any other emoji: a dark frosted-glass badge with a neon edge cage and a glowing emoji."""
    cube((1.8, 0.4, 1.8), (0, 0, 1.25), M["glass"], root, bev=0.22)
    cage = cube((1.9, 0.5, 1.9), (0, 0, 1.25), M["rim"], root, bev=0)
    wf = cage.modifiers.new("cage", "WIREFRAME")
    wf.thickness = 0.035
    m = bpy.data.materials.new("icon")
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    img = nt.nodes.new("ShaderNodeTexImage")
    img.image = bpy.data.images.load(png)
    set_input(bsdf, "Base Color", (0, 0, 0, 1))
    nt.links.new(img.outputs["Color"], bsdf.inputs["Emission Color"] if "Emission Color" in bsdf.inputs else bsdf.inputs["Emission"])
    set_input(bsdf, "Emission Strength", 1.6)
    nt.links.new(img.outputs["Alpha"], bsdf.inputs["Alpha"])
    bpy.ops.mesh.primitive_plane_add(size=1.35, location=(0, -0.22, 1.25), rotation=(math.radians(90), 0, 0))
    face = bpy.context.object
    face.data.materials.append(m)
    face.parent = root
    return 2.2


ICON_MODELS = {
    "📱": obj_phone, "📶": obj_router, "🏢": obj_building, "🏠": obj_house, "🖥": obj_server,
    "💾": obj_server, "🗄": obj_server, "💻": obj_server, "📡": obj_tower, "🛰": obj_satellite,
    "🌍": obj_globe, "🌐": obj_globe, "☀": obj_sun, "⛈": lambda r: obj_cloud(r, storm=True),
    "☁": obj_cloud, "⚡": obj_bolt, "🙂": obj_person, "👀": obj_person,
    "💳": obj_card, "📟": obj_terminal, "🌀": obj_coil, "🧲": obj_magnet, "🔋": obj_battery,
    "✈": obj_plane, "🛫": obj_plane, "🚗": obj_car, "🧊": obj_ice, "🍲": obj_microwave,
    "💡": obj_bulb,
}
SEA = "🌊"


def make_object(icon, loc, icons_png, name="obj"):
    """Build the model for an icon at loc. Returns (root, height)."""
    root = empty(name, loc)
    key = icon.replace("️", "")
    builder = ICON_MODELS.get(key)
    if builder:
        h = builder(root)
    elif key in icons_png:
        h = obj_tile(root, icons_png[key])
    else:
        h = obj_tile(root, next(iter(icons_png.values()))) if icons_png else 1.0
    root.scale = (0.001, 0.001, 0.001)
    return root, h


def pedestal(x, r=1.1, h=0.5):
    """Glossy black pedestal with a neon ring that follows the mood."""
    cylinder(r, h, (x, 0, h / 2), M["pedestal"])
    torus(r, 0.03, (x, 0, h), M["rim"])


# ---------------------------------------------------------------- world

def build_world(stations):
    n = len(stations)
    length = SPACING * (n + 6)
    bpy.ops.mesh.primitive_plane_add(size=1, location=(SPACING * n / 2, 30, 0))
    floor = bpy.context.object
    floor.scale = (length, 120, 1)
    assign(floor, M["grid"])
    for s in stations:                          # a dark glass "water" slab where the scene is the sea
        if s.has_sea:
            cube((4.2, 3.5, 0.35), (s.cx, -0.4, 0.17), M["water"], bev=0.02)
    world = bpy.data.worlds.new("void")
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    set_input(bg, "Color", (*VOID, 1))
    set_input(bg, "Strength", 1.0)


class Lights:
    """A soft white key light plus two mood-coloured rim lights that follow the camera's station."""

    def __init__(self):
        self.key = self._area("key", 900, 8, (1.0, 0.97, 0.94))
        self.rims = [self._area("rim", 700, 5, MOODS["calm"]) for _ in range(2)]

    @staticmethod
    def _area(name, watts, size, color):
        data = bpy.data.lights.new(name, "AREA")
        data.energy = watts
        data.size = size
        data.color = color
        o = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(o)
        return o

    def update(self, x, color):
        self.key.location = (x - 4, -7, 9)
        look_at(self.key, Vector((x, 0, 1)))
        for o, side in zip(self.rims, (-1, 1)):
            o.location = (x + side * 5, 5, 4)
            look_at(o, Vector((x, 0, 1.5)))
            o.data.color = color
        for node in MOOD_NODES:
            node.outputs[0].default_value = (*color, 1)


# ---------------------------------------------------------------- stations (beat -> staging)

class Station:
    def __init__(self, i, beat, icons_png):
        self.i = i
        self.cx = i * SPACING
        self.start = beat["start"]
        self.dur = beat["dur"]
        self.show = beat["show"]
        self.camera = beat.get("camera", "glide")
        self.kind = self.show.get("type", "focus")
        self.items = []          # (root, appear_delay, base_z, scale)
        self.extra = []          # per-frame animators: fn(t)
        self.focus_h = 2.0
        self.hidden = []         # objects to keep invisible until the beat starts
        self.always = i == 0     # station 1 is fully built from Frame 0, so the loop frame-matches
        self.has_sea = SEA in json.dumps(self.show, ensure_ascii=False)
        getattr(self, f"build_{self.kind}", self.build_focus)(icons_png)

    # staging --------------------------------------------------------
    def add(self, icon, x, icons_png, delay=0.0, z=0.05, scale=1.0):
        if icon.replace("\ufe0f", "") == SEA:          # the sea is the glass slab under the scene
            return empty("sea", (self.cx + x, 0, z)), 1.0
        root, h = make_object(icon, (self.cx + x, 0, z), icons_png)
        self.items.append((root, delay, z, scale))
        self.focus_h = max(self.focus_h, h * scale)
        return root, h

    def build_title(self, icons_png):
        icons = self.show.get("icons", [])[:3]
        xs = spread(len(icons), 2.2)
        for k, (ic, x) in enumerate(zip(icons, xs)):
            self.add(ic, x, icons_png, delay=0.15 * k)
        if len(icons) > 1:
            self.add_packets(xs[0], xs[-1], "cyan", n=3)

    def build_focus(self, icons_png):
        root, _ = self.add(self.show.get("icon", "💡"), 0, icons_png, z=0.55)
        pedestal(self.cx, 1.4)
        self.extra.append(lambda t, r=root: setattr(r, "rotation_euler", (0, 0, math.sin(t * 0.6) * 0.5)))

    def build_flow(self, icons_png):
        nodes = self.show.get("nodes", [])[:3]
        xs = spread(len(nodes), 2.3)
        for k, (node, x) in enumerate(zip(nodes, xs)):
            self.add(node[0], x, icons_png, delay=0.15 * k)
        light = self.show.get("style") == "light"
        if len(xs) > 1:
            tube([(self.cx + xs[0], -1.2, 0.08), (self.cx + xs[-1], -1.2, 0.08)], 0.05, M["yellow" if light else "rim"])
            self.add_packets(xs[0], xs[-1], "yellow" if light else "cyan", n=6, y=-1.2, z=0.3)

    def build_split(self, icons_png, reverse=False):
        root, h = self.add(self.show.get("icon", "🖼"), 0, icons_png)
        keep = self.show.get("keep", False)                 # the object stays while cubes burst out of it
        down = self.show.get("direction") == "down"         # cubes blast downward into the floor
        cubes = []
        rnd = random.Random(self.i)
        start = Vector((self.cx, 0, 1.2))
        for k in range(30):
            c = cube((0.3, 0.3, 0.3), start, M[f"bit{k % 5}"], bev=0.04)
            c.scale = (0.001,) * 3
            self.hidden.append(c)
            if down:
                target = Vector((self.cx + rnd.uniform(-2.6, 2.6), rnd.uniform(-1.8, 1.2), rnd.uniform(0.15, 0.6)))
            else:
                target = Vector((self.cx + rnd.uniform(-2.8, 2.8), rnd.uniform(-2.0, 1.5), rnd.uniform(0.8, 4.5)))
            cubes.append((c, target, rnd.uniform(0, 0.4)))

        def anim(t, root=root, cubes=cubes, reverse=reverse, dur=self.dur):
            if keep:
                s_obj = pop(t)
                # cubes stream out again and again, like air being pushed
                ps = [((t - 0.3 - d) % 1.1) / 1.1 if t > 0.3 + d else 0 for _, _, d in cubes]
            elif not reverse:
                s_obj = pop(t) * (1 - ease_in_out((t - 0.5) / 0.6))
                ps = [ease_in_out((t - 0.6 - d) / 0.9) for _, _, d in cubes]
            else:   # cubes start scattered, fly home over ~1.6 s, and the object forms as they arrive
                s_obj = ease_in_out((t - 1.4) / 0.4)
                ps = [1 - ease_in_out((t - 0.1 - d) / 1.3) for _, _, d in cubes]
            root.scale = (max(s_obj, 0.001),) * 3
            for (c, target, d), p in zip(cubes, ps):
                c.location = start.lerp(target, p) + Vector((0, 0, math.sin(t * 2 + d * 10) * 0.15 * p))
                c.rotation_euler = (t * 0.8 + d, t * 0.6, 0)
                v = 0.001 if p < 0.02 else 1.0
                c.scale = (v, v, v)
        self.extra.append(anim)

    def build_merge(self, icons_png):
        self.build_split(icons_png, reverse=True)

    def build_stat(self, icons_png):
        if self.show.get("icon"):
            self.add(self.show["icon"], 0, icons_png, z=2.0, scale=0.5)
        value = str(self.show.get("value", ""))
        txt = text3d(value, (self.cx, -0.5, 1.15), 1.7, M["number"])
        self.focus_h = 2.6
        txt.scale = (0.001,) * 3
        self.hidden.append(txt)

        def anim(t, txt=txt, value=value, dur=self.dur):
            s = pop(t, 0.1) * (1.0 if len(value) <= 6 else 6 / len(value))
            txt.scale = (max(s, 0.001),) * 3
            txt.data.body = count_up(value, clamp((t - 0.1) / max(0.5, dur * 0.55)))
        self.extra.append(anim)

    def build_list(self, icons_png):
        items = self.show.get("items", [])[:4]
        xs = spread(len(items), 2.4)
        step = max(0.35, (self.dur - 0.4) / max(1, len(items)))
        for k, (item, x) in enumerate(zip(items, xs)):
            self.add(item[0], x, icons_png, delay=0.15 + k * step, z=0.35)
            pedestal(self.cx + x, 0.75, 0.3)

    def build_compare(self, icons_png):
        for side, x, delay in (("left", -1.7, 0.0), ("right", 1.7, 0.25)):
            item = self.show.get(side, ["❓"])
            self.add(item[0], x, icons_png, delay=delay, z=0.55, scale=0.8)
            pedestal(self.cx + x, 0.95)
        txt = text3d("vs", (self.cx, -0.5, 1.6), 0.9, M["number"])
        txt.scale = (0.001,) * 3
        self.hidden.append(txt)
        self.extra.append(lambda t, txt=txt: setattr(txt, "scale", (max(pop(t, 0.15), 0.001),) * 3))

    def add_packets(self, xa, xb, color, n=5, y=-1.0, z=0.6):
        packets = [cube((0.22, 0.22, 0.22), (self.cx + xa, y, z), M[color], bev=0.03) for _ in range(n)]
        for pk in packets:
            pk.scale = (0.001,) * 3
            self.hidden.append(pk)

        def anim(t, packets=packets):
            for k, pk in enumerate(packets):
                if t < 0.3 and not self.always:
                    pk.scale = (0.001,) * 3
                    continue
                p = (t * 0.5 + k / len(packets)) % 1
                pk.location.x = self.cx + xa + (xb - xa) * p
                pk.location.z = z + math.sin(p * math.pi) * 0.25
                pk.rotation_euler = (t * 2, t * 1.5, 0)
                pk.scale = (1, 1, 1)
        self.extra.append(anim)

    # per frame ------------------------------------------------------
    def update(self, now):
        t = now - self.start
        if t < 0 and not self.always:               # not reached yet: keep everything hidden
            for root, *_ in self.items:
                root.scale = (0.001,) * 3
            for o in self.hidden:
                o.scale = (0.001,) * 3
            return
        for root, delay, base_z, scale in self.items:
            if self.kind not in ("split", "merge"):  # those animate their own scale
                s = 1.0 if self.always else pop(t, delay)
                root.scale = (max(s * scale, 0.001),) * 3
            root.location.z = base_z + 0.06 * math.sin(now * 1.6 + self.i)
        for fn in self.extra:
            fn(max(t, 0.0))


def spread(n, half):
    if n <= 1:
        return [0.0]
    return [-half + 2 * half * k / (n - 1) for k in range(n)]


def count_up(value, p):
    import re
    m = re.match(r"^([^\d]*)([\d.,]+)(.*)$", value)
    if not m:
        return value
    pre, num, post = m.groups()
    digits = num.replace(",", "")
    try:
        target = float(digits)
    except ValueError:
        return value
    dec = len(digits.split(".")[1]) if "." in digits else 0
    cur = target * ease_out(p)
    s = f"{cur:,.{dec}f}" if "," in num else f"{cur:.{dec}f}"
    return pre + s + post


# ---------------------------------------------------------------- camera

def station_pose(s, t):
    """Camera position and target at time t into station s's beat, with its camera move."""
    h = s.focus_h
    dist = 6.6 + h * 0.65
    elev = 1.6 + h * 0.5
    azim = math.radians(-14) + 0.03 * math.sin(t * 0.25)        # slight angle, gentle drift
    target_z = -0.55 + h * 0.3                                   # aim low so the scene sits high in frame
    if s.camera == "snap":                                        # fast push-in at the start of the beat
        dist *= lerp(1.55, 0.85, ease_out(t / 0.35))
    elif s.camera == "orbit":
        azim += math.radians(90) * ease_in_out(t / max(s.dur, 0.1))
    elif s.camera == "orbit180":
        azim += math.radians(180) * ease_in_out(t / max(s.dur, 0.1))
    elif s.camera == "top":                                       # tilt up to look straight down
        k = ease_in_out(t / 0.8)
        elev = lerp(elev, dist * 1.15, k)
        dist = lerp(dist, dist * 0.18, k)
    cam = Vector((s.cx + dist * math.sin(azim), -dist * math.cos(azim), elev))
    return cam, Vector((s.cx, 0, target_z))


def camera_pose(now, stations, loop):
    """Glide between stations; during the loop beat, fly back to the exact Frame 0 pose."""
    first_pose = station_pose(stations[0], 0.0)
    if loop and now >= loop["start"]:
        last = stations[-1]
        a = station_pose(last, last.dur)
        k = ease_in_out((now - loop["start"]) / max(loop["dur"] - loop["frame"], 0.1))
        return a[0].lerp(first_pose[0], k), a[1].lerp(first_pose[1], k)
    idx = 0
    for k, s in enumerate(stations):
        if now >= s.start - 0.45:
            idx = k
    s = stations[idx]
    t = now - s.start
    cur = station_pose(s, max(t, 0.0))
    if idx == 0:
        return cur if now > 0 else first_pose
    prev = stations[idx - 1]
    move = ease_in_out((now - (s.start - 0.45)) / 0.9)
    if move >= 1:
        return cur
    p = station_pose(prev, prev.dur)
    return p[0].lerp(cur[0], move), p[1].lerp(cur[1], move)


def mood_at(now, beats):
    """Current mood colour, blending over 0.35 s when a beat changes it. The loop beat returns to beat 1's."""
    def name(b):
        return beats[0].get("light", "calm") if b["show"].get("type") == "loop" else b.get("light", "calm")
    colors = [MOODS.get(name(b), MOODS["calm"]) for b in beats]
    cur = 0
    for i, b in enumerate(beats):
        if now >= b["start"]:
            cur = i
    if cur == 0:
        return colors[0]
    k = ease_in_out((now - beats[cur]["start"]) / 0.35)
    return tuple(lerp(p, c, k) for p, c in zip(colors[cur - 1], colors[cur]))


def look_at(obj, target):
    direction = target - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


# ---------------------------------------------------------------- main

def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    plan = json.loads(Path(argv[0]).read_text())
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    first = int(argv[2]) if len(argv) > 2 else 0
    last = int(argv[3]) if len(argv) > 3 else None
    step = int(argv[4]) if len(argv) > 4 else 1

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    build_materials()
    fps = plan.get("fps", 24)
    beats = plan["beats"]
    staged = [b for b in beats if b["show"].get("type") != "loop"]
    loop_beat = next((b for b in beats if b["show"].get("type") == "loop"), None)
    loop = dict(start=loop_beat["start"], dur=loop_beat["dur"], frame=1 / fps) if loop_beat else None
    plan_dir = Path(argv[0]).parent
    icons = {k: str(plan_dir / v) for k, v in plan.get("icons", {}).items()}
    stations = [Station(i, b, icons) for i, b in enumerate(staged)]
    build_world(stations)
    lights = Lights()

    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = 28
    cam_data.sensor_fit = "VERTICAL"
    cam_data.sensor_height = 36
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    r = scene.render
    r.engine = "CYCLES"
    r.resolution_x, r.resolution_y = plan.get("width", 540), plan.get("height", 960)
    r.resolution_percentage = 100
    r.use_persistent_data = True
    r.image_settings.file_format = "JPEG"
    r.image_settings.quality = 92
    if plan.get("threads"):
        r.threads_mode = "FIXED"
        r.threads = plan["threads"]
    c = scene.cycles
    c.device = "CPU"
    c.samples = plan.get("samples", 6)
    c.use_denoising = True
    c.use_adaptive_sampling = True
    c.adaptive_threshold = plan.get("noise_threshold", 0.05)
    c.max_bounces = 3
    c.diffuse_bounces = 2
    c.glossy_bounces = 2
    c.transmission_bounces = 3
    c.caustics_reflective = False
    c.caustics_refractive = False
    try:
        scene.view_settings.view_transform = "AgX"
        scene.view_settings.look = "AgX - Punchy"
    except TypeError:
        pass

    n_frames = int(plan["total"] * fps)
    last = n_frames if last is None else min(last, n_frames)
    for f in range(first, last, step):
        now = f / fps
        for st in stations:
            st.update(now)
        cam.location, target = camera_pose(now, stations, loop)
        if f == n_frames - 1 and loop:                     # land exactly on Frame 0's pose
            cam.location, target = station_pose(stations[0], 0.0)
        look_at(cam, target)
        lights.update(target.x, mood_at(now, beats))
        r.filepath = str(out / f"{f:05d}.jpg")
        bpy.ops.render.render(write_still=True)
        print(f"frame {f + 1}/{last}", flush=True)


main()
