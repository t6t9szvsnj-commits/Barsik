"""3D-кошка: модель, свет и рендер кадров с прозрачным фоном.

Это инструмент для подготовки картинок, а не часть программы: программе
Blender не нужен, она получает готовые PNG. Нужен Blender 4.x.

    blender -b -P tools/3d/cat3d.py -- <режим> [папка] [сэмплы]
    python3 tools/3d/compose3d.py [папка]

режимы: still (один кадр), walk (цикл ходьбы), turn (облёт камерой),
       fur (один кадр с настоящей шерстью, пробный)

Кошка собрана из метаболов, ходьба — та же формула лап, что в
desktop_cat.paw_position. Свет вращается вместе с камерой, чтобы тень всегда
уходила назад, а не растягивалась вбок.
"""
import math
import os
import sys

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
MODE = args[0] if args else "still"
OUT = args[1] if len(args) > 1 else "render3d"
SAMPLES = int(args[2]) if len(args) > 2 else 96

WALK_FRAMES = 24
TURN_FRAMES = 36
STRIDE, LIFT = 0.13, 0.085

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

# --- рендер ---------------------------------------------------------------
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = SAMPLES
scene.cycles.use_denoising = False
scene.cycles.use_adaptive_sampling = True
scene.cycles.adaptive_threshold = 0.02
scene.render.film_transparent = True
scene.render.resolution_x, scene.render.resolution_y = 400, 320
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
scene.view_settings.view_transform = "Standard"

world = bpy.data.worlds.new("мир")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs[0].default_value = (0.95, 0.92, 0.9, 1)
bg.inputs[1].default_value = 0.0


# --- материалы ------------------------------------------------------------

def rgba_socket(sockets, name):
    return [s for s in sockets if s.name == name and s.type == "RGBA"][0]


def make_fur():
    mat = bpy.data.materials.new("шерсть")
    mat.use_nodes = True
    nt = mat.node_tree
    n, L = nt.nodes, nt.links
    bsdf = n["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.62
    bsdf.inputs["Sheen Weight"].default_value = 0.6
    bsdf.inputs["Sheen Roughness"].default_value = 0.4
    bsdf.inputs["Subsurface Weight"].default_value = 0.08

    coord = n.new("ShaderNodeTexCoord")

    # полоски: волны вдоль тела, чуть искривлённые — как у полосатой кошки
    wave = n.new("ShaderNodeTexWave")
    wave.wave_type = "BANDS"
    wave.bands_direction = "X"
    wave.inputs["Scale"].default_value = 3.2
    wave.inputs["Distortion"].default_value = 4.0
    wave.inputs["Detail"].default_value = 2.0
    L.new(coord.outputs["Object"], wave.inputs["Vector"])
    sharp = n.new("ShaderNodeMapRange")
    sharp.inputs["From Min"].default_value = 0.62
    sharp.inputs["From Max"].default_value = 0.8
    L.new(wave.outputs["Fac"], sharp.inputs["Value"])

    stripes = n.new("ShaderNodeMix")
    stripes.data_type = "RGBA"
    rgba_socket(stripes.inputs, "A").default_value = (0.78, 0.42, 0.15, 1)
    rgba_socket(stripes.inputs, "B").default_value = (0.42, 0.19, 0.06, 1)
    L.new(sharp.outputs["Result"], stripes.inputs["Factor"])

    # светлое брюшко и подбородок — по высоте в координатах объекта
    sep = n.new("ShaderNodeSeparateXYZ")
    L.new(coord.outputs["Object"], sep.inputs["Vector"])
    belly = n.new("ShaderNodeMapRange")
    belly.inputs["From Min"].default_value = 0.56
    belly.inputs["From Max"].default_value = 0.47
    L.new(sep.outputs["Z"], belly.inputs["Value"])
    below = n.new("ShaderNodeMapRange")               # ниже брюха — снова шерсть
    below.inputs["From Min"].default_value = 0.33
    below.inputs["From Max"].default_value = 0.41
    L.new(sep.outputs["Z"], below.inputs["Value"])
    band = n.new("ShaderNodeMath"); band.operation = "MULTIPLY"
    L.new(belly.outputs["Result"], band.inputs[0])
    L.new(below.outputs["Result"], band.inputs[1])
    socks = n.new("ShaderNodeMapRange")
    socks.inputs["From Min"].default_value = 0.13
    socks.inputs["From Max"].default_value = 0.08
    L.new(sep.outputs["Z"], socks.inputs["Value"])
    cream = n.new("ShaderNodeMath"); cream.operation = "MAXIMUM"
    L.new(band.outputs["Value"], cream.inputs[0])
    L.new(socks.outputs["Result"], cream.inputs[1])

    mix = n.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    L.new(rgba_socket(stripes.outputs, "Result"), rgba_socket(mix.inputs, "A"))
    rgba_socket(mix.inputs, "B").default_value = (0.96, 0.87, 0.72, 1)
    L.new(cream.outputs["Value"], mix.inputs["Factor"])
    L.new(rgba_socket(mix.outputs, "Result"), bsdf.inputs["Base Color"])
    return mat


def make_simple(name, color, rough=0.5, coat=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    b = mat.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = color
    b.inputs["Roughness"].default_value = rough
    b.inputs["Coat Weight"].default_value = coat
    return mat


FUR = make_fur()
IRIS = make_simple("радужка", (0.36, 0.62, 0.1, 1), rough=0.15, coat=1.0)
PUPIL = make_simple("зрачок", (0.01, 0.01, 0.01, 1), rough=0.05, coat=1.0)
PINK = make_simple("нос", (0.86, 0.42, 0.45, 1), rough=0.4)
EAR_IN = make_simple("ухо", (0.93, 0.62, 0.6, 1), rough=0.6)
EAR_FUR = make_simple("уши", (0.72, 0.38, 0.13, 1), rough=0.6)

# --- тело из метаболов ----------------------------------------------------

mb = bpy.data.metaballs.new("кошка")
mb.resolution = 0.04
mb.render_resolution = 0.018
mb.threshold = 0.6
cat = bpy.data.objects.new("кошка", mb)
scene.collection.objects.link(cat)
mb.materials.append(FUR)


def ball(co, r, kind="BALL", size=None):
    el = mb.elements.new()
    el.type = kind
    el.co = co
    el.radius = r
    if size:
        el.size_x, el.size_y, el.size_z = size
    return el


# У шара с жёсткостью 2 и порогом 0.6 видимая поверхность — примерно 0.58
# от радиуса, так что радиусы здесь заметно больше видимых размеров.
SPINE = [(-0.36, 0.68, 0.40), (-0.14, 0.67, 0.40), (0.08, 0.68, 0.40),
         (0.28, 0.71, 0.41), (0.46, 0.86, 0.24)]
BODY = []
for x, z, r in SPINE:
    el = ball((x, 0, z), r, "ELLIPSOID", (1.0, 0.82, 1.0))
    BODY.append((el, Vector((x, 0, z))))
HEAD_C = Vector((0.76, 0, 1.13))
HEAD = [
    (ball(HEAD_C, 0.54), HEAD_C.copy()),
    (ball((0.85, -0.12, 1.04), 0.28), Vector((0.85, -0.12, 1.04))),  # щёки
    (ball((0.85, 0.12, 1.04), 0.28), Vector((0.85, 0.12, 1.04))),
    (ball((0.99, 0, 1.04), 0.2), Vector((0.99, 0, 1.04))),           # мордочка
]

LEGS = [  # бедро/плечо, сдвиг фазы (диагональные пары в фазе)
    {"hip": Vector((0.30, -0.14, 0.60)), "off": 0.0},
    {"hip": Vector((0.30, 0.14, 0.60)), "off": math.pi},
    {"hip": Vector((-0.30, -0.14, 0.60)), "off": math.pi},
    {"hip": Vector((-0.30, 0.14, 0.60)), "off": 0.0},
]
SEG = 5
for leg in LEGS:
    leg["els"] = [ball((0, 0, 0), 0.12) for _ in range(SEG)]
    leg["paw"] = ball((0, 0, 0), 0.17)

TAIL_N = 10
TAIL = [ball((0, 0, 0), 0.1) for _ in range(TAIL_N)]


def paw_position(hip_x, phase, stride, lift):
    """Та же формула, что в desktop_cat.py: на земле лапа едет назад."""
    return hip_x - math.cos(phase) * stride, max(0.0, math.sin(phase)) * lift


def pose(phase, walking=True, bob_on=True):
    bob = 0.014 * math.cos(2 * phase) if bob_on else 0.0
    head_bob = 0.018 * math.cos(2 * phase - 0.5) if bob_on else 0.0

    for el, rest in BODY:
        el.co = (rest[0], rest[1], rest[2] + bob)
    for el, rest in HEAD:
        el.co = (rest[0], rest[1], rest[2] + head_bob)
    head_ctrl.location = (0, 0, head_bob)

    for leg in LEGS:
        hip = leg["hip"] + Vector((0, 0, bob))
        if walking:
            px, lift = paw_position(hip.x, phase + leg["off"], STRIDE, LIFT)
        else:
            px, lift = hip.x, 0.0
        paw = Vector((px, hip.y, 0.085 + lift))
        # колено: чуть назад у передних, чуть вперёд у задних
        bend = -0.05 if hip.x > 0 else 0.06
        mid = (hip + paw) / 2 + Vector((bend, 0, 0))
        for i, el in enumerate(leg["els"]):
            t = i / (SEG - 1)
            p = (1 - t) ** 2 * hip + 2 * (1 - t) * t * mid + t ** 2 * paw
            el.co = p
            el.radius = 0.2 - 0.05 * t
        leg["paw"].co = paw + Vector((0.025, 0, 0.005))

    sway = math.sin(phase) * 0.07 if walking else math.sin(phase) * 0.03
    base = Vector((-0.58, 0, 0.78 + bob))
    for i, el in enumerate(TAIL):
        s = i / (TAIL_N - 1)
        el.co = base + Vector((-0.30 * s + 0.12 * s * s,
                               sway * s * s,
                               0.62 * s - 0.08 * s * s))
        el.radius = 0.13 - 0.05 * s


# --- детали головы: глаза, нос, уши — ставятся точно на поверхность -------

head_ctrl = bpy.data.objects.new("голова", None)
scene.collection.objects.link(head_ctrl)
pose(0.0, walking=False, bob_on=False)

dg = bpy.context.evaluated_depsgraph_get()
ev = cat.evaluated_get(dg)
me = ev.to_mesh()
bvh = BVHTree.FromPolygons([v.co.copy() for v in me.vertices],
                           [tuple(p.vertices) for p in me.polygons])
ev.to_mesh_clear()


def surface(center, direction):
    d = Vector(direction).normalized()
    hit, normal, _, _ = bvh.ray_cast(Vector(center) + d * 3, -d)
    return hit, normal


def sphere(name, loc, radius, mat, scale=(1, 1, 1), segs=32):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, location=loc,
                                         segments=segs, ring_count=segs // 2)
    ob = bpy.context.active_object
    ob.name = name
    ob.scale = scale
    ob.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    ob.parent = head_ctrl
    return ob


for side in (-1, 1):
    hit, nrm = surface(HEAD_C, (1.0, 0.5 * side, 0.3))
    sphere("глаз", hit - nrm * 0.045, 0.078, IRIS)
    sphere("зрачок", hit + nrm * 0.006, 0.042, PUPIL, scale=(0.8, 0.8, 1.2))

hit, nrm = surface((0.99, 0, 1.04), (1, 0, 0.35))
sphere("нос", hit, 0.04, PINK, scale=(0.8, 1.3, 0.8))

def ear_rotation(d):
    """Ось Z уха — наружу из головы, плоскость уха смотрит вперёд."""
    from mathutils import Matrix
    z = d.normalized()
    x = (Vector((1, 0, 0)) - z * z.x).normalized()
    y = z.cross(x)
    return Matrix((x, y, z)).transposed().to_quaternion(), x


def cone(loc, rot, r, depth, mat, scale):
    bpy.ops.mesh.primitive_cone_add(radius1=r, depth=depth, vertices=32,
                                    location=loc)
    ob = bpy.context.active_object
    ob.rotation_mode = "QUATERNION"
    ob.rotation_quaternion = rot
    ob.scale = scale
    ob.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    ob.parent = head_ctrl
    return ob


for side in (-1, 1):
    d = Vector((0.22, 0.6 * side, 1.0)).normalized()
    hit, nrm = surface(HEAD_C, d)
    rot, fwd = ear_rotation(d)
    cone(hit + d * 0.06, rot, 0.15, 0.28, EAR_FUR, (0.45, 1.0, 1.0))
    cone(hit + d * 0.07 + fwd * 0.04, rot, 0.1, 0.19, EAR_IN, (0.2, 0.75, 0.85))

# --- пол-невидимка, который ловит только тень -----------------------------

bpy.ops.mesh.primitive_plane_add(size=12, location=(0, 0, 0))
bpy.context.active_object.is_shadow_catcher = True

# --- свет -----------------------------------------------------------------


def light(name, kind, energy, rot, color=(1, 1, 1), size=None, shadow=True):
    data = bpy.data.lights.new(name, kind)
    data.energy = energy
    data.color = color
    if size is not None:
        if kind == "SUN":
            data.angle = size
        else:
            data.size = size
    if not shadow:
        # Cycles в 4.0 смотрит на свой флаг, общий use_shadow он игнорирует
        data.use_shadow = False
        data.cycles.cast_shadow = False
    ob = bpy.data.objects.new(name, data)
    ob.rotation_euler = [math.radians(a) for a in rot]
    scene.collection.objects.link(ob)
    return ob


# --- камера ---------------------------------------------------------------

target = bpy.data.objects.new("цель", None)
target.location = (0.1, 0, 0.68)
scene.collection.objects.link(target)
cam_data = bpy.data.cameras.new("камера")
cam_data.type = "ORTHO"
cam_data.ortho_scale = 2.85
cam = bpy.data.objects.new("камера", cam_data)
scene.collection.objects.link(cam)
tc = cam.constraints.new("TRACK_TO")
tc.target = target
tc.track_axis = "TRACK_NEGATIVE_Z"
tc.up_axis = "UP_Y"
scene.camera = cam




def aimed(name, energy, color, size, shadow):
    ob = light(name, "SUN", energy, (0, 0, 0), color, size=size, shadow=shadow)
    c = ob.constraints.new("TRACK_TO")
    c.target = target
    c.track_axis = "TRACK_NEGATIVE_Z"
    c.up_axis = "UP_Y"
    return ob


# Свет крутится вместе с камерой: тень всегда уходит назад-влево от
# зрителя. Если бросать её вбок, камера под пологим углом растягивает её
# на полэкрана — так и было в первых пробах.
KEY = aimed("ключ", 3.6, (1.0, 0.96, 0.9), 0.3, True)
RIM = aimed("контур", 2.4, (0.85, 0.9, 1.0), 0.2, False)
FILL = aimed("заполнение", 1.3, (0.97, 0.97, 1.0), 0.6, False)
LIGHT_RIG = [(KEY, 25, 55), (RIM, 175, 35), (FILL, -40, 20)]   # азимут, высота


def place_camera(azimuth_deg, elevation_deg=14, dist=8):
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    cam.location = target.location + Vector((math.sin(az) * math.cos(el),
                                             -math.cos(az) * math.cos(el),
                                             math.sin(el))) * dist
    for ob, daz, elev in LIGHT_RIG:
        a, e = math.radians(azimuth_deg + daz), math.radians(elev)
        ob.location = target.location + Vector((math.sin(a) * math.cos(e),
                                                -math.cos(a) * math.cos(e),
                                                math.sin(e))) * 10


# --- поехали --------------------------------------------------------------

os.makedirs(bpy.path.abspath(OUT), exist_ok=True)


def shoot(name):
    scene.render.filepath = os.path.join(bpy.path.abspath(OUT), name)
    bpy.ops.render.render(write_still=True)


if MODE == "still":
    place_camera(30)
    pose(0.9)
    shoot("still.png")
elif MODE == "walk":
    place_camera(30)
    for i in range(WALK_FRAMES):
        pose(2 * math.pi * i / WALK_FRAMES)
        shoot("walk_%02d.png" % i)
elif MODE == "turn":
    for i in range(TURN_FRAMES):
        place_camera(360.0 * i / TURN_FRAMES)
        pose(2 * math.pi * i / TURN_FRAMES * 2, walking=False)
        shoot("turn_%02d.png" % i)

if MODE == "fur":
    # Пробный режим: один кадр с настоящей шерстью (~30 с на кадр).
    # Для анимации не годится: форма из метаболов пересчитывается каждый
    # кадр, и шерсть на ней прыгала бы. Для ходьбы с шерстью нужна одна
    # постоянная сетка со скелетом. Ещё не решено: лапы зарастают в общий
    # «валенок», уши без шерсти.
    place_camera(30)
    pose(0.9)
    # шерсть держится только на неизменной сетке, поэтому снимаем «слепок»
    # с метаболов и растим волоски на нём
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(cat.evaluated_get(dg))
    if not me.materials:
        me.materials.append(FUR)
    for p in me.polygons:
        p.use_smooth = True
    body = bpy.data.objects.new("тело", me)
    scene.collection.objects.link(body)
    cat.hide_render = True

    mod = body.modifiers.new("шерсть", "PARTICLE_SYSTEM")
    ps = mod.particle_system.settings
    ps.type = "HAIR"
    ps.use_advanced_hair = True
    ps.count = 5000
    ps.hair_length = 0.055
    ps.emit_from = "FACE"
    ps.use_even_distribution = True
    # в «расширенном» режиме длину волоса задаёт скорость вылета, а не
    # hair_length: единица здесь — это волосы длиной во всю кошку
    ps.normal_factor = 0.035
    ps.object_align_factor = (-0.035, 0.0, -0.022)   # приглажена назад и вниз
    ps.child_type = "INTERPOLATED"
    ps.rendered_child_count = 30
    ps.roughness_1 = 0.02
    ps.roughness_2 = 0.03
    ps.clump_factor = 0.2
    ps.root_radius, ps.tip_radius, ps.radius_scale = 1.0, 0.0, 0.005
    shoot("fur.png")
