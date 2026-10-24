"""Blender script: build a 3D cut-away diorama for an explainer and render it frame by frame.

Run by explain.py, not by hand:
  blender -b --factory-startup -P scene3d.py -- PLAN.json OUT_DIR

  blender -b --factory-startup -P scene3d.py -- PLAN.json OUT_DIR FIRST_FRAME LAST_FRAME [STEP]

PLAN.json: {"fps": 24, "total": 41.2, "width": 540, "height": 960, "samples": 6,
            "beats": [{"start": 0.4, "dur": 4.1, "show": {...}}, ...],
            "icons": {"📱": "icons/0.png", ...}}          (icon paths relative to PLAN.json)

Every beat gets its own "station" along a long cut-away ground block (grass on top, soil
layers in cross-section at the front). The camera glides from station to station in one
continuous shot, timed to the narration; earlier stations stay in place behind it.
"""

import json
import math
import random
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

SPACING = 12.0          # distance between stations along X
DEPTH = 9.0             # thickness of the ground block (the soil cross-section fills the lower frame)
HALF_Y = 6.0            # ground block depth behind the objects
FRONT_Y = -3.2          # the cut face, between the objects and the camera


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
        set_input(bsdf, "IOR", 1.33)
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


# ---------------------------------------------------------------- palette

M = {}


def build_materials():
    M["grass"] = mat("grass", (0.12, 0.42, 0.08), 0.9)
    M["dark"] = mat("dark", (0.04, 0.045, 0.05), 0.35)
    M["white"] = mat("white", (0.9, 0.9, 0.88), 0.45)
    M["grey"] = mat("grey", (0.45, 0.47, 0.5), 0.5)
    M["steel"] = mat("steel", (0.7, 0.72, 0.75), 0.3, 0.9)
    M["copper"] = mat("copper", (0.85, 0.45, 0.25), 0.3, 1.0)
    M["gold"] = mat("gold", (0.9, 0.7, 0.25), 0.3, 1.0)
    M["screen"] = mat("screen", (0.1, 0.3, 0.6), 0.2, emit=(0.25, 0.55, 1.0), emit_strength=2.5)
    M["window"] = mat("window", (0.9, 0.8, 0.5), 0.2, emit=(1.0, 0.85, 0.5), emit_strength=1.5)
    M["cyan"] = mat("cyan", (0.2, 0.8, 1.0), 0.3, emit=(0.3, 0.85, 1.0), emit_strength=0.8)
    M["yellow"] = mat("yellow", (1.0, 0.8, 0.3), 0.3, emit=(1.0, 0.8, 0.3), emit_strength=0.8)
    M["green_led"] = mat("led", (0.2, 1.0, 0.4), 0.3, emit=(0.2, 1.0, 0.4), emit_strength=8)
    M["red"] = mat("red", (0.8, 0.12, 0.1), 0.4)
    M["blue_panel"] = mat("panel", (0.08, 0.18, 0.45), 0.25, 0.4)
    M["sea"] = mat("sea", (0.1, 0.4, 0.6), 0.05, transmission=0.85)
    M["seabed"] = mat("seabed", (0.55, 0.48, 0.35), 0.9)
    M["sun"] = mat("sun", (1, 0.85, 0.4), 0.5, emit=(1.0, 0.8, 0.35), emit_strength=12)
    M["cloud"] = mat("cloud", (0.95, 0.95, 0.97), 0.9)
    M["storm"] = mat("storm", (0.35, 0.37, 0.42), 0.9)
    M["pedestal"] = mat("pedestal", (0.82, 0.78, 0.7), 0.6)
    M["number"] = mat("number", (1.0, 0.8, 0.3), 0.3, emit=(1.0, 0.75, 0.25), emit_strength=1.5)
    M["soil"] = soil_material()
    M["grass"] = grass_material()
    for i, c in enumerate([(0.3, 0.85, 1.0), (1.0, 0.8, 0.3), (1.0, 0.45, 0.55), (0.5, 0.9, 0.6), (0.75, 0.6, 1.0)]):
        M[f"bit{i}"] = mat(f"bit{i}", c, 0.3, emit=c, emit_strength=0.35)


def grass_material():
    m = bpy.data.materials.new("grass")
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    set_input(bsdf, "Roughness", 0.9)
    noise = nt.nodes.new("ShaderNodeTexNoise")
    set_input(noise, "Scale", 3.0)
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.35, (0.09, 0.33, 0.06, 1)
    els[1].position, els[1].color = 0.7, (0.2, 0.5, 0.1, 1)
    nt.links.new(noise.outputs[0], ramp.inputs[0])
    nt.links.new(ramp.outputs[0], bsdf.inputs["Base Color"])
    return m


def soil_material():
    """Layered soil for the cut face: wavy horizontal bands of browns and ochres, like a cross-section."""
    m = bpy.data.materials.new("soil")
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    set_input(bsdf, "Roughness", 0.95)
    coord = nt.nodes.new("ShaderNodeTexCoord")
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (0.35, 0.35, 0.35)
    wave = nt.nodes.new("ShaderNodeTexWave")
    wave.wave_type = "BANDS"
    wave.bands_direction = "Z"
    set_input(wave, "Scale", 1.0)
    set_input(wave, "Distortion", 5.0)
    set_input(wave, "Detail", 3.0)
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.0, (0.30, 0.13, 0.05, 1)
    els[1].position, els[1].color = 1.0, (0.62, 0.32, 0.12, 1)
    for pos, col in ((0.3, (0.48, 0.22, 0.08, 1)), (0.55, (0.36, 0.16, 0.06, 1)), (0.8, (0.55, 0.28, 0.1, 1))):
        e = els.new(pos)
        e.color = col
    nt.links.new(coord.outputs["Object"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], wave.inputs["Vector"])
    nt.links.new(wave.outputs["Fac"], ramp.inputs[0])
    nt.links.new(ramp.outputs[0], bsdf.inputs["Base Color"])
    return m


# ---------------------------------------------------------------- objects (icon -> 3D model)

def obj_phone(root):
    body = cube((0.9, 0.12, 1.8), (0, 0, 0.95), M["dark"], root, bev=0.12)
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


def obj_globe(root):
    g = sphere(1.3, (0, 0, 1.5), M["sea"], root, seg=48)
    land = globe_material()
    assign(g, land)
    return 2.8


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


def obj_sun(root):
    sphere(1.0, (0, 0, 4.5), M["sun"], root, seg=32)
    return 5.5


def obj_cloud(root, storm=False):
    rnd = random.Random(7)
    for i in range(7):
        sphere(0.55 + rnd.random() * 0.4, (rnd.uniform(-1.2, 1.2), rnd.uniform(-0.4, 0.4), 3.6 + rnd.uniform(-0.3, 0.4)),
               M["storm" if storm else "cloud"], root, seg=16)
    if storm:
        pts = [(0, -0.3, 3.2), (0.35, -0.3, 2.5), (-0.1, -0.3, 2.2), (0.3, -0.3, 1.3), (0.0, -0.3, 0.4)]
        tube(pts, 0.07, M["yellow"], "bolt").parent = root
    return 4.4


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
    bpy.ops.mesh.primitive_torus_add(major_radius=0.8, minor_radius=0.25, location=(0, 0, 1.4), rotation=(math.radians(90), 0, 0))
    t = assign(bpy.context.object, M["red"])
    t.parent = root
    cube((1.6, 0.6, 0.7), (0, 0, 0.6), M["grey"], root)
    return 2.4


def obj_battery(root):
    cylinder(0.6, 2.0, (0, 0, 1.0), M["dark"], root)
    cylinder(0.61, 0.6, (0, 0, 0.4), M["green_led"], root)
    cylinder(0.2, 0.2, (0, 0, 2.1), M["steel"], root)
    return 2.2


def obj_plane(root):
    cylinder(0.45, 4.0, (0, 0, 2.0), M["white"], root, rot=(0, math.radians(90), 0))
    sphere(0.45, (2.0, 0, 2.0), M["white"], root)
    cube((1.0, 4.6, 0.1), (0, 0, 1.9), M["grey"], root, bev=0.03)
    cube((0.6, 0.08, 1.0), (-1.8, 0, 2.6), M["red"], root, bev=0.03)
    return 3.0


def obj_car(root):
    cube((2.2, 1.1, 0.6), (0, 0, 0.55), M["red"], root, bev=0.15)
    cube((1.2, 1.0, 0.5), (-0.1, 0, 1.05), M["red"], root, bev=0.15)
    for x in (-0.7, 0.7):
        for y in (-0.55, 0.55):
            cylinder(0.28, 0.2, (x, y, 0.28), M["dark"], root, rot=(math.radians(90), 0, 0), verts=16)
    return 1.4


def obj_fridge(root):
    cube((1.3, 1.2, 2.8), (0, 0, 1.4), M["white"], root, bev=0.1)
    for z in (1.0, 2.2):
        cube((0.06, 0.06, 0.5), (0.45, -0.62, z), M["steel"], root, bev=0)
    return 2.9


def obj_microwave(root):
    cube((1.8, 1.2, 1.1), (0, 0, 0.55), M["white"], root, bev=0.06)
    cube((1.1, 0.04, 0.75), (-0.2, -0.6, 0.55), M["dark"], root, bev=0.03)
    return 1.2


def obj_bulb(root):
    sphere(0.7, (0, 0, 1.9), M["yellow"], root)
    cylinder(0.32, 0.6, (0, 0, 1.0), M["steel"], root)
    return 2.6


def obj_tile(root, png):
    """Anything else: a chunky rounded 3D tile with the emoji printed on its front."""
    cube((1.8, 0.4, 1.8), (0, 0, 1.25), M["white"], root, bev=0.25)
    m = bpy.data.materials.new("icon")
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    img = nt.nodes.new("ShaderNodeTexImage")
    img.image = bpy.data.images.load(png)
    nt.links.new(img.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(img.outputs["Alpha"], bsdf.inputs["Alpha"])
    bpy.ops.mesh.primitive_plane_add(size=1.4, location=(0, -0.21, 1.25), rotation=(math.radians(90), 0, 0))
    face = bpy.context.object
    face.data.materials.append(m)
    face.parent = root
    return 2.2


ICON_MODELS = {
    "📱": obj_phone, "📶": obj_router, "🏢": obj_building, "🏠": obj_house, "🖥": obj_server,
    "💾": obj_server, "🗄": obj_server, "💻": obj_server, "📡": obj_tower, "🛰": obj_satellite,
    "🌍": obj_globe, "🌐": obj_globe, "☀": obj_sun, "⛈": lambda r: obj_cloud(r, storm=True),
    "☁": obj_cloud, "⚡": obj_bolt, "🙂": obj_person, "👀": obj_person, "👆": obj_person,
    "💳": obj_card, "📟": obj_terminal, "🌀": obj_coil, "🧲": obj_magnet, "🔋": obj_battery,
    "✈": obj_plane, "🛫": obj_plane, "🚗": obj_car, "🧊": obj_fridge, "🍲": obj_microwave,
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


# ---------------------------------------------------------------- world

def build_world(stations):
    n_stations = len(stations)
    length = SPACING * (n_stations + 1)
    x0 = -SPACING / 2
    sea_at = {s.i for s in stations if s.has_sea}
    for k in range(-1, n_stations + 1):
        cx = k * SPACING
        if k in sea_at:
            # a strait in the middle: shores on both sides, water over a sandy floor, cable along the bottom
            gap = 4.2
            side = (SPACING - gap) / 2
            for sx in (cx - gap / 2 - side / 2, cx + gap / 2 + side / 2):
                cube((side, HALF_Y - FRONT_Y, DEPTH), (sx, (HALF_Y + FRONT_Y) / 2, -DEPTH / 2), M["soil"], bev=0.0)
                cube((side, HALF_Y - FRONT_Y, 0.25), (sx, (HALF_Y + FRONT_Y) / 2, 0.0), M["grass"], bev=0.0)
            cube((gap, HALF_Y - FRONT_Y, DEPTH - 2.0), (cx, (HALF_Y + FRONT_Y) / 2, -(DEPTH - 2.0) / 2 - 0.1), M["sea"], bev=0)
            cube((gap, HALF_Y - FRONT_Y, 2.0), (cx, (HALF_Y + FRONT_Y) / 2, -DEPTH + 1.0), M["seabed"], bev=0)
            floor = -DEPTH + 2.15
            tube([(cx - gap / 2 - 0.6, -1.2, 0.15), (cx - gap / 2 + 0.2, -1.2, floor),
                  (cx + gap / 2 - 0.2, -1.2, floor), (cx + gap / 2 + 0.6, -1.2, 0.15)], 0.1, M["dark"])
        else:
            cube((SPACING, HALF_Y - FRONT_Y, DEPTH), (cx, (HALF_Y + FRONT_Y) / 2, -DEPTH / 2), M["soil"], bev=0.0)
            cube((SPACING, HALF_Y - FRONT_Y, 0.25), (cx, (HALF_Y + FRONT_Y) / 2, 0.0), M["grass"], bev=0.0)

    # far background: a flat plain and soft hills
    cube((length * 3, 200, 0.2), (x0 + length / 2, 100 + HALF_Y + 2, -1.0), mat("plain", (0.22, 0.4, 0.13), 1.0), bev=0)
    rnd = random.Random(3)
    for i in range(int(length / 10) + 6):
        sphere(rnd.uniform(6, 12), (x0 - 20 + i * 10 + rnd.uniform(-3, 3), 60 + rnd.uniform(0, 30), -6),
               mat("hill", (0.16, 0.34, 0.12), 1.0), seg=24)
    # clouds in the sky
    for i in range(int(length / 12) + 4):
        root = empty("cloudroot", (x0 - 10 + i * 12 + rnd.uniform(-4, 4), 40 + rnd.uniform(0, 25), 14 + rnd.uniform(0, 8)))
        root.scale = (2.2, 2.2, 1.2)
        obj_cloud(root)
    # sun + sky
    bpy.ops.object.light_add(type="SUN", rotation=(math.radians(38), math.radians(8), math.radians(28)))
    sun = bpy.context.object
    sun.data.energy = 2.4
    sun.data.angle = math.radians(8)
    world = bpy.data.worlds.new("sky")
    bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes.get("Background")
    coord = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.0, (0.62, 0.74, 0.86, 1)     # hazy horizon
    els[1].position, els[1].color = 0.45, (0.22, 0.42, 0.75, 1)    # blue overhead
    nt.links.new(coord.outputs["Generated"], sep.inputs[0])
    nt.links.new(sep.outputs["Z"], ramp.inputs[0])
    nt.links.new(ramp.outputs[0], bg.inputs["Color"])
    set_input(bg, "Strength", 0.9)


# ---------------------------------------------------------------- stations (beat -> staging)

class Station:
    def __init__(self, i, beat, icons_png):
        self.i = i
        self.cx = i * SPACING
        self.start = beat["start"]
        self.dur = beat["dur"]
        self.show = beat["show"]
        self.kind = self.show.get("type", "focus")
        self.items = []          # (root, appear_delay, base_z)
        self.extra = []          # per-frame animators: fn(t)
        self.focus_h = 2.0
        self.hidden = []         # objects to keep invisible until the beat starts
        self.has_sea = SEA in json.dumps(self.show, ensure_ascii=False)
        build = getattr(self, f"build_{self.kind}", self.build_focus)
        build(icons_png)

    # staging --------------------------------------------------------
    def add(self, icon, x, icons_png, delay=0.0, z=0.12):
        if icon.replace("\ufe0f", "") == SEA:          # the sea is part of the ground here
            return empty("sea", (self.cx + x, 0, z)), 1.0
        root, h = make_object(icon, (self.cx + x, 0, z), icons_png)
        self.items.append((root, delay, z))
        self.focus_h = max(self.focus_h, h)
        return root, h

    def build_title(self, icons_png):
        icons = self.show.get("icons", [])[:3]
        xs = spread(len(icons), 2.5)
        for k, (ic, x) in enumerate(zip(icons, xs)):
            self.add(ic, x, icons_png, delay=0.15 * k)
        if len(icons) > 1:
            self.add_packets(xs[0], xs[-1], "cyan", n=3)

    def build_focus(self, icons_png):
        root, h = self.add(self.show.get("icon", "💡"), 0, icons_png, z=0.6)
        cylinder(1.6, 0.5, (self.cx, 0, 0.35), M["pedestal"])
        self.extra.append(lambda t, r=root: setattr(r, "rotation_euler", (0, 0, math.sin(t * 0.6) * 0.5)))

    def build_flow(self, icons_png):
        nodes = self.show.get("nodes", [])[:3]
        xs = spread(len(nodes), 2.6)
        for k, (node, x) in enumerate(zip(nodes, xs)):
            self.add(node[0], x, icons_png, delay=0.15 * k)
        light = self.show.get("style") == "light"
        if len(xs) > 1 and not self.has_sea:
            pts = [(self.cx + xs[0], -1.2, 0.2), (self.cx + xs[-1], -1.2, 0.2)]
            tube(pts, 0.06, M["yellow" if light else "grey"])
        if len(xs) > 1:
            self.add_packets(xs[0], xs[-1], "yellow" if light else "cyan", n=6, y=-1.2, z=0.32,
                             dip=-DEPTH + 2.4 if self.has_sea else None)

    def build_split(self, icons_png, reverse=False):
        root, h = self.add(self.show.get("icon", "🖼"), 0, icons_png)
        cubes = []
        rnd = random.Random(self.i)
        start = Vector((self.cx, 0, 1.2))
        for k in range(30):
            c = cube((0.32, 0.32, 0.32), start, M[f"bit{k % 5}"], bev=0.04)
            c.scale = (0.001,) * 3
            self.hidden.append(c)
            target = Vector((self.cx + rnd.uniform(-2.8, 2.8), rnd.uniform(-2.0, 1.5), rnd.uniform(0.8, 4.5)))
            cubes.append((c, target, rnd.uniform(0, 0.4)))

        def anim(t, root=root, cubes=cubes, reverse=reverse, dur=self.dur):
            if not reverse:
                s_obj = pop(t) * (1 - ease_in_out((t - 0.5) / 0.6))
                ps = [ease_in_out((t - 0.6 - d) / 0.9) for _, _, d in cubes]
            else:   # cubes start scattered, fly home over ~2 s, and the object forms as they arrive
                s_obj = ease_in_out((t - 1.7) / 0.5)
                ps = [1 - ease_in_out((t - 0.2 - d) / 1.5) for _, _, d in cubes]
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
            self.add(self.show["icon"], 0, icons_png, z=3.2)
        value = str(self.show.get("value", ""))
        txt = text3d(value, (self.cx, -0.5, 1.6), 1.3, M["number"])
        txt.scale = (0.001, 0.001, 0.001)
        self.hidden.append(txt)

        def anim(t, txt=txt, value=value, dur=self.dur):
            s = pop(t, 0.2) * (1.0 if len(value) <= 6 else 6 / len(value))
            txt.scale = (max(s, 0.001),) * 3
            txt.data.body = count_up(value, clamp((t - 0.2) / max(0.6, dur * 0.55)))
        self.extra.append(anim)

    def build_list(self, icons_png):
        items = self.show.get("items", [])[:4]
        xs = spread(len(items), 2.7)
        step = max(0.45, (self.dur - 0.5) / max(1, len(items)))
        for k, (item, x) in enumerate(zip(items, xs)):
            self.add(item[0], x, icons_png, delay=0.2 + k * step)
            cylinder(0.75, 0.3, (self.cx + x, 0, 0.25), M["pedestal"])

    def build_compare(self, icons_png):
        for side, x, delay in (("left", -2.0, 0.0), ("right", 2.0, 0.35)):
            item = self.show.get(side, ["❓"])
            self.add(item[0], x, icons_png, delay=delay, z=0.6)
            cylinder(1.1, 0.5, (self.cx + x, 0, 0.35), M["pedestal"])
        txt = text3d("vs", (self.cx, -0.5, 1.6), 0.9, M["number"])
        txt.scale = (0.001,) * 3
        self.hidden.append(txt)
        self.extra.append(lambda t, txt=txt: setattr(txt, "scale", (max(pop(t, 0.2), 0.001),) * 3))

    def build_outro(self, icons_png):
        pass

    def add_packets(self, xa, xb, color, n=5, y=-1.0, z=0.6, dip=None):
        packets = [cube((0.22, 0.22, 0.22), (self.cx + xa, y, z), M[color], bev=0.03) for _ in range(n)]
        for pk in packets:
            pk.scale = (0.001,) * 3

        def anim(t, packets=packets):
            for k, pk in enumerate(packets):
                if t < 0.5:
                    pk.scale = (0.001,) * 3
                    continue
                p = (t * 0.45 + k / len(packets)) % 1
                pk.location.x = self.cx + xa + (xb - xa) * p
                if dip is not None:      # down the shore, along the sea floor, back up
                    rel = abs(pk.location.x - self.cx)
                    depth = clamp((2.6 - rel) / 0.8)
                    pk.location.z = z + (dip - z) * depth
                else:
                    pk.location.z = z + math.sin(p * math.pi) * 0.25
                pk.rotation_euler = (t * 2, t * 1.5, 0)
                pk.scale = (1, 1, 1)
        self.extra.append(anim)

    # per frame ------------------------------------------------------
    def update(self, now):
        t = now - self.start
        if t < 0:                                   # not reached yet: keep everything hidden
            for root, _, _ in self.items:
                root.scale = (0.001,) * 3
            for o in self.hidden:
                o.scale = (0.001,) * 3
            return
        for root, delay, base_z in self.items:
            if self.kind not in ("split", "merge"):  # those animate their own scale
                root.scale = (max(pop(t, delay), 0.001),) * 3
            root.location.z = base_z + 0.08 * math.sin(now * 1.6 + self.i)
        for fn in self.extra:
            fn(t)


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

def camera_pose(now, stations):
    """Glide to each station as its beat starts; drift gently while it plays."""
    idx = 0
    for k, s in enumerate(stations):
        if now >= s.start - 0.6:
            idx = k
    s = stations[idx]
    prev = stations[idx - 1] if idx > 0 else s
    move = ease_in_out((now - (s.start - 0.6)) / 1.1)
    x = prev.cx + (s.cx - prev.cx) * move
    h = prev.focus_h + (s.focus_h - prev.focus_h) * move
    drift = 0.5 * math.sin(now * 0.25)
    dist = 8.0 + h * 0.75
    cam = Vector((x - 2.0 + drift, -dist, 1.6 + h * 0.45))
    target = Vector((x, 0, -1.2 + h * 0.4))
    return cam, target


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
    beats = [b for b in plan["beats"] if b["show"].get("type") != "outro"]
    plan_dir = Path(argv[0]).parent
    icons = {k: str(plan_dir / v) for k, v in plan.get("icons", {}).items()}
    stations = [Station(i, b, icons) for i, b in enumerate(beats)]
    build_world(stations)

    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = 26
    cam_data.sensor_fit = "VERTICAL"
    cam_data.sensor_height = 36
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    r = scene.render
    r.engine = "CYCLES"
    r.resolution_x, r.resolution_y = plan.get("width", 720), plan.get("height", 1280)
    r.resolution_percentage = 100
    r.use_persistent_data = True
    r.image_settings.file_format = "JPEG"
    r.image_settings.quality = 92
    if plan.get("threads"):
        r.threads_mode = "FIXED"
        r.threads = plan["threads"]
    scene.cycles.device = "CPU"
    scene.cycles.samples = plan.get("samples", 20)
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = plan.get("bounces", 3)
    scene.cycles.diffuse_bounces = 2
    scene.cycles.glossy_bounces = 2
    scene.cycles.transmission_bounces = 4
    scene.cycles.caustics_reflective = False
    scene.cycles.caustics_refractive = False
    scene.cycles.adaptive_threshold = plan.get("noise_threshold", 0.05)
    scene.cycles.use_adaptive_sampling = True
    try:
        scene.view_settings.view_transform = "Standard"     # saturated, cartoon-like colours
        scene.view_settings.look = "None"
    except TypeError:
        pass

    fps = plan.get("fps", 24)
    n_frames = int(plan["total"] * fps)
    last = n_frames if last is None else min(last, n_frames)
    outro_start = next((b["start"] for b in plan["beats"] if b["show"].get("type") == "outro"), plan["total"])
    for f in range(first, last, step):
        now = f / fps
        if now >= outro_start:
            break                                   # the outro is drawn in 2D by explain.py
        for st in stations:
            st.update(now)
        cam.location, target = camera_pose(now, stations)
        look_at(cam, target)
        r.filepath = str(out / f"{f:05d}.jpg")
        bpy.ops.render.render(write_still=True)
        print(f"frame {f + 1}/{last}", flush=True)


main()
