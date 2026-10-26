"""3D-модель для версии 3: Барсик, рыжий британский кот-тэбби.

Инструмент подготовки картинок, программе Blender не нужен.

    blender -b -P tools/3d/v3_cat.py -- <вид> [папка] [сэмплы] [шерсть 0/1]

виды: hero (3/4 во весь рост), face (морда почти анфас), turn (облёт),
      walk (цикл шага, кадры для «дорожки»),
      sprites (все кадры и meta.json для версии 3), poses (по кадру на позу),
      icon (морда 1024x1024 для иконки приложения)

Цвета сняты с фото при дневном свете: оттенок и насыщенность оттуда,
яркость подобрана так, чтобы под нашим светом выглядело как на снимках.

Что делает его узнаваемым и на чём легко промахнуться:
  - светлое на морде — только круглые пятна (подбородок, подушечки усов,
    горло); прямоугольные маски по осям давали белую «бородку», а маска
    грудки без передней границы заливала кремом пол-лица;
  - веки — элементы самой головы, а не отдельные сферы: иначе на них нет
    шерсти, и глаза выглядят как лётные очки;
  - зрачок должен выступать над радужкой, иначе от него видна точка;
  - глаза открытые и круглые (так на всех новых фото). Сонным взгляд делали
    три вещи: высокое веко, шерсть с пухлых щёк, закрывавшая низ радужки,
    и тень от века на её верхней половине. Отсюда короткая шерсть вокруг
    глаз, глаз чуть навыкате и слабое свечение радужки.

Пропорции — по снимкам в профиль: Барсик молодой, у него длинные лапы,
крупные уши торчком и длинный пушистый хвост, а не «валенки» и обрубок.

Форма лепится метаболами, потом запекается в постоянную сетку со скелетом:
у метаболов сетка пересобирается каждый кадр, и шерсть на ходу прыгала бы.
Узор привязан к позе покоя, поэтому полоски едут вместе с лапой.

На ходу хвост трубой с загнутым кончиком, голова поднята — как на видео.
"""
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
VIEW = args[0] if args else "hero"
OUT = args[1] if len(args) > 1 else "render3d"
SAMPLES = int(args[2]) if len(args) > 2 else 64
FUR_ON = len(args) > 3 and args[3] == "1"
TURN_FRAMES = 36
WALK_FRAMES = 24
WALK_AZ = 25           # сбоку и чуть спереди — так кот идёт по столу

# --- цвета (линейные, как их ждёт Blender) --------------------------------
BASE = (0.70, 0.36, 0.13, 1)        # рыжая шерсть, абрикосовая, как на дневном фото
STRIPE = (0.52, 0.22, 0.065, 1)     # полоски — тот же рыжий, чуть темнее
CREAM = (0.855, 0.730, 0.610, 1)    # подбородок, мордочка, грудка
IRIS_C = (0.500, 0.330, 0.055, 1)   # ореховые: золото с оливковым, как на видео вблизи
NOSE_C = (0.716, 0.283, 0.205, 1)
EAR_PINK = (0.791, 0.456, 0.402, 1)
WHISKER_C = (0.92, 0.90, 0.86, 1)

LID_C = BASE                        # гладкие части (уши) — без подъёма яркости
EAR_C = (0.72, 0.30, 0.09, 1)       # уши гладкие — свой рыжий, без подъёма яркости
EYE_RIM = (0.05, 0.025, 0.015, 1)

# На видео голова поднята над грудью заметно выше, чем у коренастой модели.
# Всё, что привязано к морде (пятна крема, полоски на лбу, нос, усы), едет
# вместе с ней — поэтому одна поправка на всех.
HEAD_UP = 0.08

if FUR_ON:
    # волоски затеняют друг друга, и с шерстью те же краски выходят
    # кирпичными — поднимаем яркость, чтобы итог совпал с фото
    def _lift(c, k):
        return tuple(min(1.0, v * k) for v in c[:3]) + (1,)
    BASE, STRIPE, CREAM = _lift(BASE, 1.35), _lift(STRIPE, 1.3), _lift(CREAM, 1.1)

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
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
world.node_tree.nodes["Background"].inputs[1].default_value = 0.0


# --- материалы ------------------------------------------------------------

class Graph:
    """Мелкие помощники, чтобы граф шерсти читался как формула."""

    def __init__(self, mat):
        self.nt = mat.node_tree
        self.n, self.L = self.nt.nodes, self.nt.links
        # Координаты идут через узел сдвига: после запекания сетки их
        # переключают на «позу покоя» (см. pin_pattern), чтобы полоски не
        # ползли по лапам, пока кот шагает.
        tc = self.n.new("ShaderNodeTexCoord")
        tc.name = "координаты"
        mp = self.n.new("ShaderNodeMapping")
        mp.name = "в объект"
        self.L.new(tc.outputs["Object"], mp.inputs["Vector"])
        self.coord = mp.outputs["Vector"]
        sep = self.n.new("ShaderNodeSeparateXYZ")
        self.L.new(self.coord, sep.inputs["Vector"])
        self.x, self.y, self.z = sep.outputs["X"], sep.outputs["Y"], sep.outputs["Z"]

    def ramp(self, value, lo, hi):
        """0 при value=lo, 1 при value=hi, с обрезкой — плавная маска."""
        m = self.n.new("ShaderNodeMapRange")
        m.inputs["From Min"].default_value = lo
        m.inputs["From Max"].default_value = hi
        self.L.new(value, m.inputs["Value"])
        return m.outputs["Result"]

    def math(self, op, a, b=None):
        m = self.n.new("ShaderNodeMath")
        m.operation = op
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                m.inputs[i].default_value = v
            else:
                self.L.new(v, m.inputs[i])
        return m.outputs["Value"]

    def blob(self, center, inner, outer):
        """Мягкое круглое пятно: 1 внутри inner, 0 снаружи outer.
        Прямые маски по осям давали прямоугольники с жёсткими краями."""
        d = self.n.new("ShaderNodeVectorMath")
        d.operation = "DISTANCE"
        self.L.new(self.coord, d.inputs[0])
        d.inputs[1].default_value = center
        return self.ramp(d.outputs["Value"], outer, inner)

    def mix(self, fac, a, b):
        m = self.n.new("ShaderNodeMix")
        m.data_type = "FLOAT"
        self.L.new(fac, m.inputs["Factor"])
        fa = [s for s in m.inputs if s.name == "A" and s.type == "VALUE"][0]
        fb = [s for s in m.inputs if s.name == "B" and s.type == "VALUE"][0]
        self.L.new(a, fa)
        self.L.new(b, fb)
        return [s for s in m.outputs if s.name == "Result" and s.type == "VALUE"][0]

    def color_mix(self, fac, a, b):
        m = self.n.new("ShaderNodeMix")
        m.data_type = "RGBA"
        self.L.new(fac, m.inputs["Factor"])
        ca = [s for s in m.inputs if s.name == "A" and s.type == "RGBA"][0]
        cb = [s for s in m.inputs if s.name == "B" and s.type == "RGBA"][0]
        for sock, v in ((ca, a), (cb, b)):
            if isinstance(v, tuple):
                sock.default_value = v
            else:
                self.L.new(v, sock)
        return [s for s in m.outputs if s.name == "Result" and s.type == "RGBA"][0]

    def stripes(self, direction, scale, distortion, vector=None, kind="BANDS", detail=1.5):
        w = self.n.new("ShaderNodeTexWave")
        w.wave_type = kind
        if kind == "BANDS":
            w.bands_direction = direction
        else:
            w.rings_direction = "SPHERICAL"
        w.inputs["Scale"].default_value = scale
        w.inputs["Distortion"].default_value = distortion
        w.inputs["Detail"].default_value = detail
        self.L.new(vector or self.coord, w.inputs["Vector"])
        return self.ramp(w.outputs["Fac"], 0.52, 0.86)


def make_fur_material():
    mat = bpy.data.materials.new("шерсть")
    mat.use_nodes = True
    g = Graph(mat)
    bsdf = g.n["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.65
    bsdf.inputs["Sheen Weight"].default_value = 0.3
    bsdf.inputs["Sheen Roughness"].default_value = 0.4
    bsdf.inputs["Subsurface Weight"].default_value = 0.06

    # Полоски по зонам, как у Барсика: дуги на боках, кольца на лапах и хвосте,
    # вертикальные линии на лбу.
    body = g.stripes("X", 1.7, 8.0, detail=3.0)
    legs = g.math("MULTIPLY", g.stripes("Z", 4.0, 1.5, detail=2.0), 0.7)   # кольца бледнее
    vm = g.n.new("ShaderNodeVectorMath")
    vm.operation = "SUBTRACT"
    g.L.new(g.coord, vm.inputs[0])
    vm.inputs[1].default_value = (-0.5, 0.0, 0.66)           # корень хвоста
    tail = g.stripes(None, 4.2, 0.6, vector=vm.outputs["Vector"], kind="RINGS")
    brow = g.stripes("Y", 8.0, 0.4)

    s = g.mix(g.ramp(g.z, 0.44, 0.34), body, legs)
    s = g.mix(g.ramp(g.x, -0.52, -0.62), s, tail)
    head_zone = g.ramp(g.x, 0.5, 0.62)
    brow_only = g.math("MULTIPLY", brow, g.ramp(g.z, 1.12 + HEAD_UP, 1.24 + HEAD_UP))
    s = g.mix(head_zone, s, brow_only)
    s = g.math("MULTIPLY", s, 0.36)          # на спине и боках полоски еле видны

    fur = g.color_mix(s, BASE, STRIPE)

    # кремовое: подбородок и мордочка, манишка на груди, чуть светлее живот
    # светлое на морде — круглыми мягкими пятнами: подбородок, подушечки
    # усов у носа и горло, переходящее в грудку. Щёки остаются рыжими.
    absy = g.math("ABSOLUTE", g.y)
    chin = g.blob((0.95, 0, 0.83 + HEAD_UP), 0.07, 0.19)
    for side in (-1, 1):
        # подушечки усов на видео почти белые и крупнее, чем казалось по фото
        pad = g.blob((1.03, 0.08 * side, 0.93 + HEAD_UP), 0.05, 0.15)
        chin = g.math("MAXIMUM", chin, pad)
    throat = g.math("MULTIPLY", g.blob((0.8, 0, 0.72 + HEAD_UP / 2), 0.06, 0.2), 0.75)
    chin = g.math("MAXIMUM", chin, throat)
    bib = g.math("MULTIPLY", g.ramp(g.x, 0.42, 0.56), g.ramp(g.x, 0.78, 0.68))
    bib = g.math("MULTIPLY", bib, g.ramp(g.z, 0.96 + HEAD_UP, 0.86 + HEAD_UP))
    bib = g.math("MULTIPLY", bib, g.ramp(g.z, 0.5, 0.62))
    bib = g.math("MULTIPLY", bib, g.ramp(absy, 0.26, 0.12))
    belly = g.math("MULTIPLY", g.ramp(g.z, 0.47, 0.4), g.ramp(g.z, 0.3, 0.37))
    belly = g.math("MULTIPLY", belly, 0.2)
    cream = g.math("MAXIMUM", g.math("MAXIMUM", chin, bib), belly)

    g.L.new(g.color_mix(cream, fur, CREAM), bsdf.inputs["Base Color"])
    return mat


def simple(name, color, rough=0.5, coat=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    b = mat.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = color
    b.inputs["Roughness"].default_value = rough
    b.inputs["Coat Weight"].default_value = coat
    return mat


FUR = make_fur_material()
IRIS = simple("радужка", IRIS_C, rough=0.12, coat=1.0)
# Настоящая радужка просвечивает, и верхняя половина под веком у кота не
# тёмная. Без подсветки тень от века читалась как полуприкрытый глаз.
IRIS.node_tree.nodes["Principled BSDF"].inputs["Emission Color"].default_value = IRIS_C
IRIS.node_tree.nodes["Principled BSDF"].inputs["Emission Strength"].default_value = 0.22
PUPIL = simple("зрачок", (0.005, 0.005, 0.005, 1), rough=0.05, coat=1.0)
NOSE = simple("нос", NOSE_C, rough=0.45)
EAR_IN = simple("ухо внутри", EAR_PINK, rough=0.6)
EAR_OUT = simple("ухо", EAR_C, rough=0.7)
WHISK = simple("усы", WHISKER_C, rough=0.35)
LID = simple("веко", LID_C, rough=0.7)
RIM = simple("кайма", EYE_RIM, rough=0.4)

# --- тело: коренастая «булка» почти без шеи --------------------------------

mb = bpy.data.metaballs.new("кот")
mb.resolution = 0.04
mb.render_resolution = 0.016
mb.threshold = 0.6
cat = bpy.data.objects.new("кот", mb)
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


# (x, z, радиус, ширина, высота). Одинаковые шары давали ровную трубу —
# «колбасу». У кота грудь и бёдра шире, а посередине талия и подтянутый
# живот, поэтому средний шар уже, чуть выше и меньше.
SPINE = [(-0.28, 0.63, 0.51, 0.75, 1.0),     # бёдра
         (-0.08, 0.67, 0.49, 0.63, 0.93),    # талия
         (0.12, 0.66, 0.52, 0.76, 1.0),      # грудь
         (0.30, 0.70, 0.49, 0.80, 1.0),      # плечи
         (0.46, 0.84 + HEAD_UP, 0.38, 0.92, 1.0)]
BODY = [(ball((x, 0, z), r, "ELLIPSOID", (1.0, w, h)), Vector((x, 0, z)))
        for x, z, r, w, h in SPINE]
HEAD_C = Vector((0.75, 0, 1.06 + HEAD_UP))
HEAD = [(ball(HEAD_C, 0.59), HEAD_C.copy())]
for co, r in (((0.87, -0.16, 0.96), 0.34), ((0.87, 0.16, 0.96), 0.34),    # щёки
              ((1.0, 0, 0.97), 0.2), ((0.95, 0, 0.87), 0.17)):              # нос, подбородок
    co = Vector(co) + Vector((0, 0, HEAD_UP))
    HEAD.append((ball(co, r), co))

LEGS = [{"hip": Vector((0.30, -0.15, 0.52)), "off": 0.0},
        {"hip": Vector((0.30, 0.15, 0.52)), "off": math.pi},
        {"hip": Vector((-0.24, -0.15, 0.52)), "off": math.pi},
        {"hip": Vector((-0.24, 0.15, 0.52)), "off": 0.0}]
SEG = 5
# Пол ниже тела, чем у коренастого британца: Барсик молодой, лапы у него
# заметно длиннее (видно на фото в профиль из машины).
FLOOR = -0.1
for leg in LEGS:
    leg["els"] = [ball((0, 0, 0), 0.2) for _ in range(SEG)]
    leg["paw"] = ball((0, 0, 0), 0.17)
TAIL_N = 16
TAIL = [ball((0, 0, 0), 0.15) for _ in range(TAIL_N)]

head_ctrl = bpy.data.objects.new("голова", None)
scene.collection.objects.link(head_ctrl)


def paw_position(hip_x, phase, stride, lift):
    return hip_x - math.cos(phase) * stride, max(0.0, math.sin(phase)) * lift


TAIL_BASE = Vector((-0.5, 0, 0.66))
TAIL_LEN = 0.95


def tail_points(wave=0.0, n=TAIL_N, start=140.0, end=60.0):
    """Хвост трубой, как на видео с дорожки: от корня вверх и чуть назад,
    кончик загнут вперёд вопросительным знаком. Угол наклона плавно меняется
    вдоль хвоста; wave качает кончик вбок."""
    pts, p, steps = [], TAIL_BASE.copy(), 240
    marks = {round(i * (steps - 1) / (n - 1)) for i in range(n)}
    for k in range(steps):
        s = k / (steps - 1)
        if k in marks:
            pts.append(p + Vector((0, wave * s * s, 0)))
        a = math.radians(start + (end - start) * s ** 1.5)
        p = p + Vector((math.cos(a), 0, math.sin(a))) * TAIL_LEN / (steps - 1)
    return pts


def pose(phase=0.0, walking=False, tail_wave=0.0):
    bob = 0.012 * math.cos(2 * phase) if walking else 0.0
    for el, rest in BODY:
        el.co = rest + Vector((0, 0, bob))
    for el, rest in HEAD:
        el.co = rest + Vector((0, 0, bob))
    head_ctrl.location = (0, 0, bob)
    for leg in LEGS:
        hip = leg["hip"] + Vector((0, 0, bob))
        px, lift = (paw_position(hip.x, phase + leg["off"], 0.11, 0.07)
                    if walking else (hip.x, 0.0))
        paw = Vector((px, hip.y, FLOOR + 0.1 + lift))
        mid = (hip + paw) / 2 + Vector((-0.03 if hip.x > 0 else 0.04, 0, 0))
        for i, el in enumerate(leg["els"]):
            t = i / (SEG - 1)
            el.co = (1 - t) ** 2 * hip + 2 * (1 - t) * t * mid + t ** 2 * paw
            el.radius = 0.23 - 0.09 * t
        leg["paw"].co = paw + Vector((0.03, 0, 0))
    for i, (el, co) in enumerate(zip(TAIL, tail_points(tail_wave))):
        el.co = co + Vector((0, 0, bob))
        el.radius = 0.18 - 0.05 * i / (TAIL_N - 1)


pose()

# --- детали головы: встают точно на поверхность ---------------------------

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


def attach(ob):
    ob.parent = head_ctrl
    return ob


def sphere(loc, radius, mat, scale=(1, 1, 1)):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, location=loc,
                                         segments=40, ring_count=20)
    ob = bpy.context.active_object
    ob.scale = scale
    ob.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    return attach(ob)


# глаза крупные, круглые, широко посаженные — янтарные
EYES = []
EYE_OPEN = []          # радужка, зрачок, кайма — прячутся, когда глаза закрыты
EYE_SPOTS = []
for side in (-1, 1):
    hit, nrm = surface(HEAD_C, (1.0, 0.5 * side, 0.2))
    EYES.append(hit.copy())
    EYE_OPEN.append(sphere(hit - nrm * 0.07, 0.125, IRIS))
    # зрачок должен выступать над радужкой (её передний край — +0.03 по
    # нормали), иначе наружу торчит только точка
    EYE_OPEN.append(sphere(hit + nrm * 0.021, 0.04, PUPIL, scale=(1.0, 0.72, 1.12)))
    bpy.ops.mesh.primitive_torus_add(major_radius=0.081, minor_radius=0.0042,
                                     major_segments=48, minor_segments=12,
                                     location=hit + nrm * 0.025)   # там радужка уже 0.081
    rim = bpy.context.active_object
    rim.rotation_mode = "QUATERNION"
    rim.rotation_quaternion = nrm.to_track_quat("Z", "Y")
    rim.data.materials.append(RIM)
    bpy.ops.object.shade_smooth()
    EYE_OPEN.append(attach(rim))
    # Верхнее веко — часть самой головы: так оно плавно в неё переходит и
    # на нём растёт шерсть. Отдельной сферой оно выходило лысой нашлёпкой,
    # и вместе с каймой глаза смотрелись как лётные очки.
    # на новых фото глаза открыты и круглые — веко лишь чуть касается радужки
    lid_co = hit - nrm * 0.1 + Vector((0, 0, 0.104))
    HEAD.append((ball(lid_co, 0.16, "ELLIPSOID", (1.0, 1.0, 0.6)), lid_co.copy()))
    EYE_SPOTS.append((hit.copy(), nrm.copy()))

hit, _ = surface((1.0, 0, 0.97 + HEAD_UP), (1, 0, 0.25))
sphere(hit, 0.036, NOSE, scale=(0.75, 1.35, 0.8))


def ear_rotation(d):
    z = d.normalized()
    x = (Vector((1, 0, 0)) - z * z.x).normalized()
    return Matrix((x, z.cross(x), z)).transposed().to_quaternion(), x


def cone(loc, rot, r, depth, mat, scale):
    bpy.ops.mesh.primitive_cone_add(radius1=r, depth=depth, vertices=32, location=loc)
    ob = bpy.context.active_object
    ob.rotation_mode = "QUATERNION"
    ob.rotation_quaternion = rot
    ob.scale = scale
    ob.data.materials.append(mat)
    sub = ob.modifiers.new("скругление", "SUBSURF")   # у британцев кончики круглые
    sub.levels = sub.render_levels = 2
    bpy.ops.object.shade_smooth()
    return attach(ob)


# уши крупные и торчком — у Барсика они заметно больше, чем у взрослого британца
for side in (-1, 1):
    d = Vector((0.08, 0.62 * side, 1.0)).normalized()
    hit, _ = surface(HEAD_C, d)
    rot, fwd = ear_rotation(d)
    cone(hit + d * 0.08, rot, 0.23, 0.36, EAR_OUT, (0.5, 1.0, 1.0))
    cone(hit + d * 0.085 + fwd * 0.05, rot, 0.165, 0.25, EAR_IN, (0.22, 0.75, 0.85))


def whisker(root, direction, length, droop):
    cu = bpy.data.curves.new("ус", "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = 0.0028
    cu.bevel_resolution = 2
    sp = cu.splines.new("POLY")
    pts = 7
    sp.points.add(pts - 1)
    d = Vector(direction).normalized()
    for i in range(pts):
        t = i / (pts - 1)
        p = root + d * length * t + Vector((-0.12 * length * t * t, 0, -droop * t * t))
        sp.points[i].co = (p.x, p.y, p.z, 1)
        sp.points[i].radius = 1.0 - 0.8 * t
    ob = bpy.data.objects.new("ус", cu)
    ob.data.materials.append(WHISK)
    scene.collection.objects.link(ob)
    return attach(ob)


# длинные белые усы — у Барсика они видны на каждом фото
for side in (-1, 1):
    for k, tilt in enumerate((0.16, 0.06, -0.04, -0.13)):
        root, _ = surface((0.99, 0, 0.95 + HEAD_UP), (0.55, 0.9 * side, tilt * 0.6))
        whisker(root, (0.35, 1.0 * side, tilt), 0.42 - 0.03 * k, 0.05 + 0.02 * k)

# Закрытые глаза — тёмная дужка «улыбкой» поверх века. Радужку при этом
# прячем: под ней гладкое место без шерсти, оно и читается как веко.
EYE_SHUT = []
for hit, nrm in EYE_SPOTS:
    across = nrm.cross(Vector((0, 0, 1))).normalized()
    up = across.cross(nrm).normalized()
    if up.z < 0:
        up = -up
    cu = bpy.data.curves.new("сомкнутый глаз", "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = 0.009
    cu.bevel_resolution = 2
    sp = cu.splines.new("POLY")
    sp.points.add(8)
    for i in range(9):
        u = i / 8 * 2 - 1
        pt = hit + nrm * 0.012 + across * u * 0.085 - up * 0.028 * (1 - u * u)
        sp.points[i].co = (pt.x, pt.y, pt.z, 1)
    ob = bpy.data.objects.new("сомкнутый глаз", cu)
    ob.data.materials.append(RIM)
    scene.collection.objects.link(ob)
    ob.hide_render = True
    EYE_SHUT.append(attach(ob))


def eyes(open_):
    for ob in EYE_OPEN:
        ob.hide_render = not open_
    for ob in EYE_SHUT:
        ob.hide_render = open_

# --- пол-невидимка для тени, свет, камера ---------------------------------

bpy.ops.mesh.primitive_plane_add(size=14, location=(0, 0, FLOOR))
bpy.context.active_object.is_shadow_catcher = True

target = bpy.data.objects.new("цель", None)
scene.collection.objects.link(target)
cam_data = bpy.data.cameras.new("камера")
cam_data.type = "ORTHO"
cam = bpy.data.objects.new("камера", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam


def track(ob):
    c = ob.constraints.new("TRACK_TO")
    c.target = target
    c.track_axis = "TRACK_NEGATIVE_Z"
    c.up_axis = "UP_Y"


track(cam)


def sun(name, energy, color, angle, shadow):
    data = bpy.data.lights.new(name, "SUN")
    data.energy = energy
    data.color = color
    data.angle = angle
    if not shadow:
        data.use_shadow = False
        data.cycles.cast_shadow = False
    ob = bpy.data.objects.new(name, data)
    scene.collection.objects.link(ob)
    track(ob)
    return ob


# свет нейтральный — иначе рыжий «уплывёт», а цвета сняты с фото
RIG = [(sun("ключ", 3.1, (1, 1, 1), 0.3, True), 25, 55),
       (sun("контур", 2.0, (0.95, 0.97, 1.0), 0.2, False), 175, 35),
       (sun("заполнение", 1.3, (1, 1, 1), 0.6, False), -40, 20)]


def point_from(center, az_deg, el_deg, dist):
    a, e = math.radians(az_deg), math.radians(el_deg)
    return center + Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e),
                            math.sin(e))) * dist


def camera(az, el, look_at, ortho):
    target.location = look_at
    cam_data.ortho_scale = ortho
    cam.location = point_from(target.location, az, el, 8)
    for ob, daz, elev in RIG:
        ob.location = point_from(target.location, az + daz, elev, 10)


# --- постоянная сетка и скелет ---------------------------------------------
#
# Метаболы удобны, чтобы вылепить форму, но их сетка пересобирается в каждом
# кадре, и шерсть на ней прыгала бы. Поэтому форма запекается один раз, а
# двигает её скелет: лапы ставятся на пол через IK, хвост и голова качаются.

# Шаг шире, чем в первой пробе: с 0.12 он будто крался, а на видео с
# дорожки лапы выносит далеко.
STRIDE, LIFT = 0.16, 0.09
# Колено согнуто уже в покое: так IK знает, в какую сторону гнуть сустав,
# и длины хватает на весь шаг (лапа на вылете дальше от бедра, чем в покое).
KNEE_OUT = 0.07
WALK_SINK = 0.03           # на ходу корпус чуть ниже, чем в стойке


def bake_mesh():
    mb.resolution = mb.render_resolution       # иначе запечётся грубая сетка
    dg = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(cat.evaluated_get(dg))
    if not mesh.materials:
        mesh.materials.append(FUR)
    for p in mesh.polygons:
        p.use_smooth = True
    body = bpy.data.objects.new("тело", mesh)
    scene.collection.objects.link(body)
    bpy.data.objects.remove(cat)
    return body


def pin_pattern(body):
    """Узор считается от вершин в позе покоя («Generated» — это они же,
    только сжатые в куб 0..1), а не от текущих: иначе кольца на лапе стоят
    на месте, а лапа уезжает сквозь них."""
    lo = Vector([min(v.co[i] for v in body.data.vertices) for i in range(3)])
    hi = Vector([max(v.co[i] for v in body.data.vertices) for i in range(3)])
    nt = FUR.node_tree
    tc, mp = nt.nodes["координаты"], nt.nodes["в объект"]
    nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"])
    mp.inputs["Location"].default_value = lo
    mp.inputs["Scale"].default_value = hi - lo


def bone_layout():
    """Имя кости -> (начало, конец, родитель)."""
    up = Vector((0, 0, HEAD_UP))
    bones = {
        "таз": (Vector((-0.30, 0, 0.53)), Vector((0.05, 0, 0.54)), None),
        "грудь": (Vector((0.05, 0, 0.54)), Vector((0.40, 0, 0.60)), "таз"),
        "шея": (Vector((0.40, 0, 0.60)), Vector((0.62, 0, 0.88)) + up / 2, "грудь"),
        "голова": (Vector((0.62, 0, 0.88)) + up / 2, HEAD_C + Vector((0.1, 0, 0.3)), "шея"),
    }
    for i, leg in enumerate(LEGS):
        hip = leg["hip"]
        ankle = Vector((hip.x, hip.y, FLOOR + 0.1))
        # передние гнутся локтем назад, задние коленом вперёд
        knee = (hip + ankle) / 2 + Vector((-KNEE_OUT if hip.x > 0 else KNEE_OUT, 0, 0))
        parent = "грудь" if hip.x > 0 else "таз"
        bones["бедро.%d" % i] = (hip, knee, parent)
        bones["голень.%d" % i] = (knee, ankle, "бедро.%d" % i)
    pts = tail_points(n=7)
    for k in range(6):
        bones["хвост.%d" % k] = (pts[k], pts[k + 1], "таз" if k == 0 else "хвост.%d" % (k - 1))
    return bones


def seg_dist(p, a, b):
    ab = b - a
    t = max(0.0, min(1.0, (p - a).dot(ab) / ab.length_squared))
    return (p - (a + ab * t)).length


def smooth(edge0, edge1, x):
    t = max(0.0, min(1.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3 - 2 * t)


def skin(body, bones):
    """Веса вершин вручную, а не «автоматически»: так левая и правая лапы
    никогда не тянут одну и ту же вершину (внутренние стороны передних лап
    почти касаются, и общий вес рвал бы их при шаге).

    Все границы зон — плавные. С жёсткой («выше 0.62 лапа уже не влияет»)
    на боку при шаге открывалась щель ровно по этой высоте."""
    groups = {name: body.vertex_groups.new(name=name) for name in bones}
    for v in body.data.vertices:
        co = v.co
        ws = []
        for name, (a, b, _) in bones.items():
            gate = 1.0
            if name.startswith(("бедро", "голень")):
                side = co.y if a.y > 0 else -co.y
                gate = smooth(0.66, 0.40, co.z) * smooth(-0.01, 0.07, side)
            elif name.startswith("хвост"):
                gate = smooth(-0.36, -0.5, co.x)
            if gate > 0:
                ws.append((gate / (seg_dist(co, a, b) + 0.02) ** 4, name))
        total = sum(w for w, _ in ws)
        for w, n in ws:
            if w / total > 0.01:
                groups[n].add([v.index], w / total, "REPLACE")


def build_rig(body):
    bones = bone_layout()
    data = bpy.data.armatures.new("скелет")
    arm = bpy.data.objects.new("скелет", data)
    scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for name, (a, b, parent) in bones.items():
        eb = data.edit_bones.new(name)
        eb.head, eb.tail = a, b
        if parent:
            eb.parent = data.edit_bones[parent]
            eb.use_connect = (eb.head - eb.parent.tail).length < 1e-4
    bpy.ops.object.mode_set(mode="OBJECT")

    skin(body, bones)
    mod = body.modifiers.new("скелет", "ARMATURE")
    mod.object = arm
    body.parent = arm

    # мелочи головы (глаза, уши, усы) едут за костью головы
    head_ctrl.parent = arm
    head_ctrl.parent_type = "BONE"
    head_ctrl.parent_bone = "голова"
    rest = arm.data.bones["голова"].matrix_local @ Matrix.Translation(
        (0, arm.data.bones["голова"].length, 0))
    head_ctrl.matrix_parent_inverse = rest.inverted()

    targets = []
    for i, leg in enumerate(LEGS):
        t = bpy.data.objects.new("лапа.%d" % i, None)
        scene.collection.objects.link(t)
        t.location = bones["голень.%d" % i][1]
        ik = arm.pose.bones["голень.%d" % i].constraints.new("IK")
        ik.target = t
        ik.chain_count = 2
        targets.append(t)
    return arm, targets


def side_axis(arm, name, world_axis):
    """Индекс локальной оси кости, ближайшей к нужной мировой оси: вокруг
    неё и крутим. У костей в плоскости XZ ось бокового качания — та, что
    лежит в этой плоскости, а ось кивка — та, что смотрит вдоль Y."""
    m = arm.data.bones[name].matrix_local.to_3x3()
    return max((0, 2), key=lambda i: abs(m.col[i].dot(world_axis)))


def walk_pose(arm, targets, phase):
    arm.location.z = -WALK_SINK + 0.012 * math.cos(2 * phase)
    for leg, t in zip(LEGS, targets):
        px, lift = paw_position(leg["hip"].x, phase + leg["off"], STRIDE, LIFT)
        t.location = (px, leg["hip"].y, FLOOR + 0.1 + lift)
    for k in range(6):
        name = "хвост.%d" % k
        pb = arm.pose.bones[name]
        pb.rotation_mode = "XYZ"
        rot = [0.0, 0.0, 0.0]
        # бегущая волна: кончик опаздывает за корнем
        rot[side_axis(arm, name, Vector((1, 0, 0)))] = 0.07 * math.sin(phase - 0.55 * k)
        pb.rotation_euler = rot
    pb = arm.pose.bones["голова"]
    pb.rotation_mode = "XYZ"
    rot = [0.0, 0.0, 0.0]
    rot[side_axis(arm, "голова", Vector((0, 1, 0)))] = 0.03 * math.sin(2 * phase + 0.6)
    pb.rotation_euler = rot


# --- позы для версии 3 -----------------------------------------------------
#
# Все повороты — вокруг мировых осей через начало кости: так проще думать
# («поднять перед на 38°»), чем в локальных осях каждой кости. Скелет сам
# не вращается, только сдвигается, поэтому его пространство = мировое минус
# сдвиг.

Y_AXIS = Vector((0, 1, 0))
Z_AXIS = Vector((0, 0, 1))


def rest_ankle(i):
    hip = LEGS[i]["hip"]
    return Vector((hip.x, hip.y, FLOOR + 0.1))


def reset_pose(arm, targets):
    arm.location = (0, 0, 0)
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.rotation_euler = (0, 0, 0)
        pb.location = (0, 0, 0)
    for i, t in enumerate(targets):
        t.location = rest_ankle(i)
    eyes(True)
    bpy.context.view_layer.update()


def turn(arm, name, axis, angle):
    """Повернуть кость (а с ней всё, что на ней висит) вокруг мировой оси,
    проходящей через начало кости."""
    bpy.context.view_layer.update()
    pb = arm.pose.bones[name]
    head = pb.head.copy()
    rot = Matrix.Rotation(angle, 4, axis)
    pb.matrix = Matrix.Translation(head) @ rot @ Matrix.Translation(-head) @ pb.matrix
    bpy.context.view_layer.update()


def aim(arm, name, world_point):
    """Развернуть кость концом на точку (длина кости не меняется)."""
    bpy.context.view_layer.update()
    pb = arm.pose.bones[name]
    want = world_point - arm.location - pb.head
    q = (pb.tail - pb.head).rotation_difference(want)
    head = pb.head.copy()
    pb.matrix = (Matrix.Translation(head) @ q.to_matrix().to_4x4()
                 @ Matrix.Translation(-head) @ pb.matrix)
    bpy.context.view_layer.update()


def bone_world(arm, name, end="head"):
    bpy.context.view_layer.update()
    return arm.location + getattr(arm.pose.bones[name], end)


def lay_tail(arm, path):
    """Уложить хвост вдоль ломаной (мировые точки): каждая кость смотрит
    туда, куда на пути приходится её конец."""
    lengths = [arm.data.bones["хвост.%d" % k].length for k in range(6)]
    cum = []
    total = 0.0
    for seg_a, seg_b in zip(path, path[1:]):
        total += (seg_b - seg_a).length
        cum.append(total)

    def at(dist):
        prev = 0.0
        for (seg_a, seg_b), c in zip(zip(path, path[1:]), cum):
            if dist <= c:
                return seg_a.lerp(seg_b, (dist - prev) / max(1e-6, c - prev))
            prev = c
        return path[-1]

    walked = 0.0
    for k in range(6):
        walked += lengths[k]
        aim(arm, "хвост.%d" % k, at(walked))


def tail_along_side(arm, front_x):
    """Хвост спускается на пол у корня и идёт вдоль ближнего к камере бока
    (минус Y) вперёд, к передним лапам, — как кот сидит в позе «сфинкса». Дугой
    вокруг всего тела не выходит: хвоста хватает только на заднюю половину,
    и он прятался за корпусом."""
    root = bone_world(arm, "хвост.0")
    z = FLOOR + 0.07
    lay_tail(arm, [root,
                   Vector((root.x + 0.02, -0.16, z)),
                   Vector((root.x + 0.24, -0.38, z)),
                   Vector((front_x, -0.40, z)),
                   Vector((front_x + 0.22, -0.18, z))])


def stand_pose(arm, targets, phase=0.0, tail_swish=0.0):
    """Стоит на месте; хвост ходит волной — им он «подметает»."""
    reset_pose(arm, targets)
    arm.location.z = -WALK_SINK
    for k in range(6):
        turn(arm, "хвост.%d" % k, Vector((1, 0, 0)),
             tail_swish * math.sin(phase - 0.6 * k))


def sit_pose(arm, targets, breath=0.0, flick=0.0):
    """Сидит, хвост обвит вокруг передних лап (фото из машины)."""
    reset_pose(arm, targets)
    # при 40° передние лапы не доставали до пола и он будто вставал на дыбы
    arm.location = Vector((0.0, 0, -0.27 + 0.006 * breath))
    turn(arm, "таз", Y_AXIS, -math.radians(30))       # перед вверх
    turn(arm, "шея", Y_AXIS, math.radians(6))
    turn(arm, "голова", Y_AXIS, math.radians(22))     # морда смотрит вперёд
    for i, t in enumerate(targets):
        hip = bone_world(arm, "бедро.%d" % i)
        if LEGS[i]["hip"].x > 0:                     # передние — столбиками
            t.location = (hip.x + 0.03, hip.y, FLOOR + 0.1)
        else:                                        # задние — под себя
            t.location = (hip.x + 0.34, hip.y * 1.25, FLOOR + 0.1)
    tail_along_side(arm, bone_world(arm, "голень.0", "tail").x - 0.1)
    if flick:
        for k in (4, 5):
            turn(arm, "хвост.%d" % k, Z_AXIS, flick)


def sleep_pose(arm, targets, breath=0.0):
    """Спит «буханкой», лапы под собой, глаза закрыты."""
    reset_pose(arm, targets)
    arm.location = Vector((0, 0, -0.29 + 0.008 * breath))
    turn(arm, "шея", Y_AXIS, math.radians(22))        # голова опущена
    turn(arm, "голова", Y_AXIS, math.radians(8))
    for i, t in enumerate(targets):
        hip = bone_world(arm, "бедро.%d" % i)
        t.location = (hip.x + 0.1, hip.y * 1.1, FLOOR + 0.12)
    tail_along_side(arm, 0.05)
    eyes(False)


def pick_pose(arm, targets, s):
    """Тянется ближней передней лапой к файлу: s от 0 до 1 — замах и шлёп."""
    stand_pose(arm, targets)
    turn(arm, "шея", Y_AXIS, math.radians(14 * min(1.0, s * 2)))
    t = targets[0]
    t.location = rest_ankle(0) + Vector((0.3 * s, 0, 0.17 * math.sin(math.pi * s)))


def hop_pose(arm, targets, s):
    """Подпрыгивает от радости: лапы поджаты, тень остаётся на полу."""
    stand_pose(arm, targets)
    h = 0.32 * math.sin(math.pi * s)
    arm.location.z += h
    for i, t in enumerate(targets):
        t.location = rest_ankle(i) + Vector((0, 0, h + 0.06 * math.sin(math.pi * s)))
    turn(arm, "таз", Y_AXIS, -math.radians(6) * math.sin(math.pi * s))


# --- шерсть ---------------------------------------------------------------

def grow_fur(body):
    """Волоски на запечённой сетке. Длина по зонам: на лапах коротко,
    иначе они срастаются в «валенок» (так было в первой пробе), на груди
    и хвосте — пушисто. Модификатор шерсти идёт после скелета, поэтому
    волоски едут вместе с телом, а не остаются в позе покоя."""
    mesh = body.data
    vg = body.vertex_groups.new(name="длина")
    # Там, где глаз, волоски не растут вовсе: короткие всё равно торчат
    # сквозь радужку, и она выходит мутной, как за стеклом.
    dens = body.vertex_groups.new(name="густота")
    for v in mesh.vertices:
        near = min((v.co - e).length for e in EYES)
        dens.add([v.index], 0.0 if near < 0.13 else 1.0, "REPLACE")
    for v in mesh.vertices:
        x, y, z = v.co
        w = 0.8
        if z < 0.42:
            w = 0.38
        if x > 0.95:
            w = 0.35
        if x > 0.3 and 0.55 < z < 0.95 and abs(y) < 0.28:
            w = 0.95
        if x < -0.55:
            w = 1.0            # хвост на видео — пушистый султан
        # вокруг глаз шерсть короткая: с пухлых щёк она торчит к камере,
        # закрывает низ радужки, и взгляд опять выходит сонным
        near = min((v.co - e).length for e in EYES)
        if near < 0.28:
            w = min(w, 0.05 + 0.3 * max(0.0, near - 0.13) / 0.15)
        vg.add([v.index], w, "REPLACE")

    mod = body.modifiers.new("шерсть", "PARTICLE_SYSTEM")
    psys = mod.particle_system
    psys.vertex_group_length = "длина"
    psys.vertex_group_density = "густота"
    ps = psys.settings
    ps.type = "HAIR"
    ps.use_advanced_hair = True
    ps.count = 8000
    ps.emit_from = "FACE"
    ps.use_even_distribution = True
    # в расширенном режиме длину задаёт скорость вылета, а не hair_length
    ps.normal_factor = 0.016
    ps.object_align_factor = (-0.012, 0.0, -0.008)
    ps.child_type = "INTERPOLATED"
    ps.rendered_child_count = 30
    ps.roughness_1 = 0.015
    ps.roughness_2 = 0.02
    ps.clump_factor = 0.15
    ps.root_radius, ps.tip_radius, ps.radius_scale = 1.0, 0.0, 0.0045


os.makedirs(OUT, exist_ok=True)
suffix = "_fur" if FUR_ON else ""


def shoot(name):
    scene.render.filepath = os.path.join(os.path.abspath(OUT), name)
    bpy.ops.render.render(write_still=True)


# --- кадры для программы ------------------------------------------------------
#
# Масштаб — SPRITE_PPU пикселей на единицу модели. От него зависит, сколько
# пикселей проходит один цикл шага, и программа берёт это число из meta.json,
# чтобы лапы не скользили по столу.

SPRITE_W, SPRITE_H = 280, 200
SPRITE_PPU = 80.0
SPRITE_LOOK = Vector((0.05, 0, 0.92))
ANCHOR = Vector((0.03, 0, FLOOR))       # точка между лапами на полу
MOUTH = Vector((1.02, 0, 0.86 + HEAD_UP))
CROWN = HEAD_C + Vector((0, 0, 0.62))

ANIMS = [
    ("walk", WALK_FRAMES, lambda a, t, i, n: walk_pose(a, t, 2 * math.pi * i / n)),
    ("stand", 12, lambda a, t, i, n: stand_pose(a, t, 2 * math.pi * i / n, 0.22)),
    ("sit", 12, lambda a, t, i, n: sit_pose(a, t, math.sin(2 * math.pi * i / n),
                                             0.12 * math.sin(4 * math.pi * i / n))),
    ("blink", 1, lambda a, t, i, n: (sit_pose(a, t), eyes(False))),
    ("sleep", 8, lambda a, t, i, n: sleep_pose(a, t, math.sin(2 * math.pi * i / n))),
    ("pick", 8, lambda a, t, i, n: pick_pose(a, t, i / (n - 1))),
    ("hop", 8, lambda a, t, i, n: hop_pose(a, t, i / n)),
]


def to_pixels(co):
    from bpy_extras.object_utils import world_to_camera_view
    u, v, _ = world_to_camera_view(scene, cam, co)
    return [round(u * SPRITE_W, 1), round((1 - v) * SPRITE_H, 1)]


def export_sprites(arm, targets, only_first=False):
    import json
    scene.render.resolution_x, scene.render.resolution_y = SPRITE_W, SPRITE_H
    camera(WALK_AZ, 10, SPRITE_LOOK, SPRITE_W / SPRITE_PPU)
    bpy.context.view_layer.update()
    meta = {
        "size": [SPRITE_W, SPRITE_H],
        "anchor": to_pixels(ANCHOR),
        # опорная лапа за полцикла уходит назад на 2 шага — за цикл пол
        # сдвигается на 4 шага (в кадре — с поправкой на угол камеры)
        "cycle_px": round(4 * STRIDE * math.cos(math.radians(WALK_AZ)) * SPRITE_PPU, 2),
        "anims": {},
    }
    for name, count, fn in ANIMS:
        frames = []
        for i in ([count // 2] if only_first else range(count)):
            fn(arm, targets, i, count)
            bpy.context.view_layer.update()
            frames.append({"mouth": to_pixels(head_ctrl.matrix_world @ MOUTH),
                           "crown": to_pixels(head_ctrl.matrix_world @ CROWN)})
            shoot("%s_%02d.png" % (name, i))
        meta["anims"][name] = frames
    with open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)


pose()
body = bake_mesh()
pin_pattern(body)
arm, targets = build_rig(body)
if FUR_ON:
    grow_fur(body)

if VIEW == "hero":
    camera(32, 12, Vector((0.0, 0, 0.7)), 3.3)
    shoot("hero%s.png" % suffix)
elif VIEW == "face":
    camera(74, 6, Vector((0.82, 0, 1.08 + HEAD_UP)), 1.55)
    shoot("face%s.png" % suffix)
elif VIEW == "turn":
    for i in range(TURN_FRAMES):
        camera(360.0 * i / TURN_FRAMES, 12, Vector((0.0, 0, 0.7)), 3.4)
        shoot("turn%s_%02d.png" % (suffix, i))
elif VIEW == "walk":
    camera(WALK_AZ, 10, Vector((0.0, 0, 0.7)), 3.3)
    for i in range(WALK_FRAMES):
        walk_pose(arm, targets, 2 * math.pi * i / WALK_FRAMES)
        shoot("walk%s_%02d.png" % (suffix, i))
elif VIEW == "icon":
    # морда для иконки приложения: квадрат, крупно, чуть в три четверти
    scene.render.resolution_x = scene.render.resolution_y = 1024
    sit_pose(arm, targets)
    bpy.context.view_layer.update()
    head = head_ctrl.matrix_world @ (HEAD_C + Vector((0.0, 0, 0.12)))
    camera(58, 6, head, 1.45)
    shoot("icon%s.png" % suffix)
elif VIEW in ("sprites", "poses"):
    export_sprites(arm, targets, only_first=(VIEW == "poses"))
