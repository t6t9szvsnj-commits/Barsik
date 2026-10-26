"""Гортензия, которую Барсик иногда приносит в зубах. Для версии 3.

    blender -b -P tools/3d/hydrangea.py -- <carry|gift> <папка> [сэмплы] [white|blue]

carry — боком, стебель влево, к пасти (кадр ставится у рта Барсика);
gift  — лежит на столе с тенью (её оставляют рядом с панелью).
Рядом с картинкой пишется json: где в кадре кончик стебля (за него держит
Барсик) и где цветок касается стола.

Белая по умолчанию; голубая осталась вариантом: `blue` последним аргументом.
Листья и стебель тёмно-тёмно-зелёные.
"""
import json
import math
import os
import random
import sys

import bpy
from mathutils import Matrix, Vector

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
MODE = args[0] if args else "carry"
OUT = args[1] if len(args) > 1 else "render3d"
SAMPLES = int(args[2]) if len(args) > 2 else 64
COLOUR = args[3] if len(args) > 3 else "white"
rng = random.Random(7)

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = SAMPLES
scene.cycles.use_denoising = False
scene.render.film_transparent = True
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
scene.view_settings.view_transform = "Standard"
world = bpy.data.worlds.new("мир")
scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[1].default_value = 0.0

# --- материалы ------------------------------------------------------------------


PETAL_COLOURS = {
    "white": ((0.70, 0.70, 0.66, 1), (0.74, 0.73, 0.68, 1), (0.60, 0.66, 0.50, 1)),
    "blue": ((0.16, 0.42, 0.85, 1), (0.30, 0.58, 0.92, 1), (0.36, 0.30, 0.78, 1)),
}
UNDER = {"white": (0.30, 0.33, 0.26, 1), "blue": (0.10, 0.25, 0.55, 1)}
EYES = {"white": (0.55, 0.60, 0.35, 1), "blue": (0.85, 0.88, 0.95, 1)}


def petal_material():
    """Каждый цветок чуть своего оттенка, как у настоящей гортензии: белая
    от молочного к чуть зеленоватому (молодые цветки), голубая — от
    голубого к сиреневатому. Белый не чисто белый: под тремя лампами он
    выгорел бы в плоское пятно без цветочков."""
    mat = bpy.data.materials.new("лепесток")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    info = nt.nodes.new("ShaderNodeObjectInfo")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    first, mid_c, last = PETAL_COLOURS[COLOUR]
    ramp.color_ramp.elements[0].color = first
    ramp.color_ramp.elements[1].color = last
    mid = ramp.color_ramp.elements.new(0.65)
    mid.color = mid_c
    nt.links.new(info.outputs["Random"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.55
    bsdf.inputs["Sheen Weight"].default_value = 0.4
    bsdf.inputs["Subsurface Weight"].default_value = 0.15
    return mat


def simple(name, color, rough=0.5):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    b = mat.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = color
    b.inputs["Roughness"].default_value = rough
    return mat


PETAL = petal_material()
EYE = simple("серединка", EYES[COLOUR], 0.4)
LEAF = simple("лист", (0.010, 0.045, 0.022, 1), 0.45)    # тёмно-тёмно-зелёный
STEM = simple("стебель", (0.018, 0.06, 0.03, 1), 0.6)

# --- цветок -------------------------------------------------------------------------

root = bpy.data.objects.new("гортензия", None)
scene.collection.objects.link(root)


def floret_mesh():
    """Один цветочек: четыре лепестка крестом и серединка — одна сетка на всех."""
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1, segments=16, ring_count=8)
    petal = bpy.context.active_object
    parts = []
    for k in range(4):
        a = k * math.pi / 2 + math.pi / 4
        ob = petal.copy()
        ob.data = petal.data.copy()
        scene.collection.objects.link(ob)
        ob.scale = (0.17, 0.12, 0.035)
        ob.rotation_euler = (0, 0, a)
        ob.location = (math.cos(a) * 0.13, math.sin(a) * 0.13, 0)
        parts.append(ob)
    bpy.data.objects.remove(petal)
    bpy.ops.object.select_all(action="DESELECT")
    for ob in parts:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.ops.object.join()
    ob = bpy.context.active_object
    ob.data.materials.append(PETAL)
    bpy.ops.object.shade_smooth()
    mesh = ob.data
    bpy.data.objects.remove(ob)
    return mesh


FLORET = floret_mesh()
N = 70
for i in range(N):
    # точки по «шапке» — почти вся сфера, кроме низа у стебля (спираль Фибоначчи)
    z = 1 - (i + 0.5) / N * 1.7
    r = math.sqrt(max(0.0, 1 - z * z))
    t = i * math.pi * (3 - math.sqrt(5))
    n = Vector((math.cos(t) * r, math.sin(t) * r, z)).normalized()
    ob = bpy.data.objects.new("цветочек", FLORET)
    scene.collection.objects.link(ob)
    s = rng.uniform(1.1, 1.4)
    # не идеальный шар: цветочки чуть на разной высоте и чуть вразнобой
    tilt = Matrix.Rotation(rng.uniform(-0.35, 0.35), 4, "X") @ Matrix.Rotation(rng.uniform(-0.35, 0.35), 4, "Y")
    lift = rng.uniform(0.92, 1.07)
    ob.matrix_world = (Matrix.Translation(n * lift)
                       @ n.to_track_quat("Z", "Y").to_matrix().to_4x4() @ tilt
                       @ Matrix.Rotation(rng.uniform(0, math.pi), 4, "Z")
                       @ Matrix.Scale(s, 4))
    ob.parent = root
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.035, location=n * (lift + 0.04), segments=8, ring_count=6)
    dot = bpy.context.active_object
    dot.data.materials.append(EYE)
    dot.parent = root
# подложка, чтобы не просвечивало. В цвет цветка, а не тёмно-зелёная: в щелях
# между цветочками тёмная подложка делала шапку грязной, особенно в зубах у Барсика
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.92)
core = bpy.context.active_object
core.data.materials.append(simple("подложка", UNDER[COLOUR], 0.7))
core.parent = root


def leaf(angle, tilt, length=0.95):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1, segments=32, ring_count=16)
    ob = bpy.context.active_object
    for v in ob.data.vertices:
        x = v.co.x
        v.co.y *= 0.45 * (1 - max(0.0, x)) ** 0.7 * (1 + 0.25 * min(0.0, x))  # к концу — остриё
        v.co.z *= 0.04
        v.co.z += 0.12 * x * x                                              # чуть выгнут
    ob.scale = (length / 2, length / 2, length / 2)
    ob.location = (0, 0, 0)
    bpy.ops.object.transform_apply(scale=True)
    for v in ob.data.vertices:
        v.co.x += length / 2
    ob.rotation_euler = (0, tilt, angle)
    ob.location = (0, 0, -0.85)
    ob.data.materials.append(LEAF)
    bpy.ops.object.shade_smooth()
    ob.parent = root
    return ob


for k, a in enumerate((0.3, 2.4, 4.3)):
    leaf(a, math.radians(18 + 8 * k))

bpy.ops.mesh.primitive_cylinder_add(radius=0.07, depth=1.6, location=(0, 0, -1.55))
stem = bpy.context.active_object
stem.data.materials.append(STEM)
stem.parent = root
STEM_TIP = Vector((0, 0, -2.35))        # за этот конец держит Барсик
BOTTOM = Vector((0, 0, -1.0))

# --- поза, свет, камера --------------------------------------------------------------

if MODE == "carry":
    # стебель уходит вверх-влево, к пасти; шапка свисает вперёд-вниз,
    # перед грудью — впереди Барсика в окне всего ~65 px
    root.rotation_euler = (0, math.radians(135), 0)
else:
    # лежит на столе: шапка касается пола, стебель уходит назад-влево
    root.rotation_euler = (0, math.radians(-100), math.radians(15))
bpy.context.view_layer.update()
world_pts = [ob.matrix_world @ Vector(c) for ob in scene.objects if ob.type == "MESH"
             for c in ob.bound_box]
low = min(p.z for p in world_pts)
if MODE == "gift":
    root.location.z -= low                                  # на пол
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
    bpy.context.active_object.is_shadow_catcher = True
bpy.context.view_layer.update()

cam_data = bpy.data.cameras.new("камера")
cam_data.type = "ORTHO"
cam = bpy.data.objects.new("камера", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
target = bpy.data.objects.new("цель", None)
scene.collection.objects.link(target)
world_pts = [ob.matrix_world @ Vector(c) for ob in scene.objects
             if ob.type == "MESH" and not getattr(ob, "is_shadow_catcher", False) for c in ob.bound_box]
centre = sum(world_pts, Vector()) / len(world_pts)
target.location = centre
for ob in (cam,):
    c = ob.constraints.new("TRACK_TO")
    c.target = target
    c.track_axis = "TRACK_NEGATIVE_Z"
    c.up_axis = "UP_Y"


def at(az, el, dist):
    a, e = math.radians(az), math.radians(el)
    return centre + Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e))) * dist


# тот же угол, что у кадров Барсика (сбоку и чуть спереди), и тот же свет
AZ, EL = 25, 10
cam.location = at(AZ, EL, 10)
for name, energy, daz, el, shadow in (("ключ", 3.1, 25, 55, True), ("контур", 2.0, 175, 35, False),
                                      ("заполнение", 1.3, -40, 20, False)):
    d = bpy.data.lights.new(name, "SUN")
    d.energy, d.angle = energy, 0.3
    if not shadow:
        d.use_shadow = False
        d.cycles.cast_shadow = False
    ob = bpy.data.objects.new(name, d)
    scene.collection.objects.link(ob)
    ob.location = at(AZ + daz, el, 10)
    c = ob.constraints.new("TRACK_TO")
    c.target = target
    c.track_axis = "TRACK_NEGATIVE_Z"
    c.up_axis = "UP_Y"

span = max((p - centre).length for p in world_pts) * 2.3
W, H = (180, 180) if MODE == "carry" else (240, 180)
scene.render.resolution_x, scene.render.resolution_y = W, H
cam_data.ortho_scale = span * (1.0 if MODE == "carry" else 1.1)
bpy.context.view_layer.update()

os.makedirs(OUT, exist_ok=True)
scene.render.filepath = os.path.join(os.path.abspath(OUT), "flower_%s.png" % MODE)
bpy.ops.render.render(write_still=True)

from bpy_extras.object_utils import world_to_camera_view  # noqa: E402


def px(co):
    u, v, _ = world_to_camera_view(scene, cam, co)
    return [round(u * W, 1), round((1 - v) * H, 1)]


point = root.matrix_world @ (STEM_TIP if MODE == "carry" else BOTTOM)
if MODE == "gift":
    point = Vector((centre.x, centre.y, 0))
with open(os.path.join(OUT, "flower_%s.json" % MODE), "w") as f:
    json.dump({"size": [W, H], ("grip" if MODE == "carry" else "base"): px(point)}, f)
print("готово:", MODE, px(point))
