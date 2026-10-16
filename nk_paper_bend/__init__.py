# NK Paper Bend 4.0 — procedural paper / card / page / magazine / ribbon motion for Blender 4.2 – 5.2+
# Made by Nihad Jihad — 0750 760 0111 — nsmarketingkrd@gmail.com
#
# One Geometry Nodes engine drives every effect.  A flat sheet (local XY) is measured along a
# bend direction; a 1-D profile of bend angles is built (spiral roll past a moving line, a
# limit that folds flat, sine waves, noise flutter, twist), integrated with Accumulate Field,
# and every vertex is mapped onto it — UVs stay intact.
#
# The controller Empty is "smart": put it on any edge or corner of the sheet and that side is
# the side that moves.

bl_info = {
    "name": "NK Paper Bend",
    "author": "Nihad Jihad",
    "version": (4, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar (N) > NK Bend",
    "description": "Procedural roll, peel, fold, page flip, magazine, ribbons and loops",
    "category": "Object",
}

import math
import re
import bpy
import bmesh
import numpy as np
from bpy.props import (
    EnumProperty, IntProperty, FloatProperty, PointerProperty, BoolProperty,
    StringProperty, CollectionProperty,
)

AUTHOR = "Nihad Jihad"
PHONE = "07507600111"
PHONE_PRETTY = "0750 760 0111"
WHATSAPP = "https://wa.me/9647507600111"
EMAIL = "nsmarketingkrd@gmail.com"
VERSION_STR = "4.0"

NG_NAME = "NK Paper Bend"
NG_VERSION = 5
MOD_NAME = "NK Bend"
SOLID_NAME = "NK Thickness"
D = math.radians


# ==========================================================================
#  Node-building helpers
# ==========================================================================
class _NB:
    def __init__(self, ng):
        self.ng, self.nodes, self.links = ng, ng.nodes, ng.links
        self._x = 0

    def node(self, idname, **props):
        n = self.nodes.new(idname)
        for k, v in props.items():
            setattr(n, k, v)
        n.location = (self._x, 0)
        self._x += 40
        return n

    def feed(self, sock, val):
        if isinstance(val, bpy.types.NodeSocket):
            self.links.new(val, sock)
        elif val is not None:
            sock.default_value = val

    def math(self, op, a, b=None, c=None, clamp=False):
        n = self.node("ShaderNodeMath", operation=op, use_clamp=clamp)
        self.feed(n.inputs[0], a)
        if b is not None:
            self.feed(n.inputs[1], b)
        if c is not None:
            self.feed(n.inputs[2], c)
        return n.outputs[0]

    def add(self, a, b): return self.math("ADD", a, b)
    def sub(self, a, b): return self.math("SUBTRACT", a, b)
    def mul(self, a, b): return self.math("MULTIPLY", a, b)
    def div(self, a, b): return self.math("DIVIDE", a, b)
    def mad(self, a, b, c): return self.math("MULTIPLY_ADD", a, b, c)       # a*b + c
    def lerp(self, a, b, t): return self.mad(self.sub(b, a), t, a)
    def clamp01(self, a): return self.math("ADD", a, 0.0, clamp=True)
    def smoothstep(self, x): return self.mul(self.mul(x, x), self.mad(x, -2.0, 3.0))

    def combine(self, x, y, z):
        n = self.node("ShaderNodeCombineXYZ")
        for i, v in enumerate((x, y, z)):
            self.feed(n.inputs[i], v)
        return n.outputs[0]

    def separate(self, v):
        n = self.node("ShaderNodeSeparateXYZ")
        self.feed(n.inputs[0], v)
        return n.outputs[0], n.outputs[1], n.outputs[2]

    def vmath(self, op, a, b=None, scale=None):
        n = self.node("ShaderNodeVectorMath", operation=op)
        self.feed(n.inputs[0], a)
        if b is not None:
            self.feed(n.inputs[1], b)
        if scale is not None:
            self.feed(n.inputs["Scale"], scale)
        return n.outputs[0]

    def random(self, rid, seed, lo=-1.0, hi=1.0):
        n = self.node("FunctionNodeRandomValue", data_type="FLOAT")
        ins = [i for i in n.inputs if i.enabled]
        by = {}
        for i in ins:
            by.setdefault(i.name, i)
        by["Min"].default_value = lo
        by["Max"].default_value = hi
        self.feed(by["ID"], rid)
        self.feed(by["Seed"], seed)
        return [o for o in n.outputs if o.enabled][0]


def _new_input(ng, name, stype, default=None, vmin=None, vmax=None, subtype=None,
               parent=None, desc=""):
    kw = dict(name=name, in_out="INPUT", socket_type=stype)
    if parent is not None:
        kw["parent"] = parent
    s = ng.interface.new_socket(**kw)
    if subtype:
        s.subtype = subtype
    if default is not None:
        s.default_value = default
    if vmin is not None:
        s.min_value = vmin
    if vmax is not None:
        s.max_value = vmax
    s.description = desc
    return s


# ==========================================================================
#  The engine
# ==========================================================================
def build_node_group():
    name = f"{NG_NAME} v{NG_VERSION}"
    ng = bpy.data.node_groups.get(name)
    if ng and ng.get("nk_version") == NG_VERSION:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    else:
        ng.nodes.clear()
        ng.interface.clear()
    if hasattr(ng, "is_modifier"):
        ng.is_modifier = True

    I = ng.interface
    F, INT, B, OBJ = "NodeSocketFloat", "NodeSocketInt", "NodeSocketBool", "NodeSocketObject"
    I.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    I.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    p = I.new_panel("Animation")
    _new_input(ng, "Progress", F, 0.5, 0.0, 1.0, "FACTOR", p, "Keyframe this 0 → 1 to animate")
    _new_input(ng, "Ease", F, 0.0, 0.0, 1.0, "FACTOR", p,
               "Built-in smooth ease per sheet (0 = linear; shape it in the Graph Editor instead)")
    _new_input(ng, "Use Page Number", B, False, None, None, None, p,
               "Drive page turning with a page number instead of Progress: Page 3 = three pages turned, "
               "3.5 = the fourth page halfway")
    _new_input(ng, "Page", F, 0.0, 0.0, 512.0, None, p, "How many pages are turned (keyframe it!)")
    _new_input(ng, "Page Overlap", F, 0.0, 0.0, 3.0, None, p,
               "How early the next page starts lifting (0 = one by one and every spread rests perfectly "
               "flat; higher = riffle, but the next page already lifts a little on whole numbers)")

    p = I.new_panel("Controller")
    _new_input(ng, "Controller", OBJ, None, None, None, None, p,
               "Empty that decides which side moves. Put it on an edge or corner of the sheet")
    _new_input(ng, "Auto Direction", B, True, None, None, None, p,
               "The side of the sheet where the Empty sits is the side that moves (ignores its rotation)")
    _new_input(ng, "Snap Direction", B, True, None, None, None, p,
               "Snap to the 4 edges and 4 corners (45° corners) — off = exact angle to the Empty")
    _new_input(ng, "Follow Controller", B, True, None, None, None, p,
               "On: the bend line starts at the Empty.  Off: the line is measured from the far edge "
               "(0 = far edge / spine, 1 = the Empty's edge)")
    _new_input(ng, "Direction", F, 0.0, None, None, "ANGLE", p, "Extra rotation of the bend direction")

    p = I.new_panel("Bend")
    _new_input(ng, "Line Start", F, 0.0, -2.0, 2.0, "FACTOR", p,
               "Bend line at Progress 0, in sheet lengths (see Follow Controller)")
    _new_input(ng, "Line End", F, 0.0, -2.0, 2.0, "FACTOR", p, "Bend line at Progress 1")
    _new_input(ng, "Limit Start", F, D(3600), 0.0, D(36000), "ANGLE", p,
               "Max bend at Progress 0 (180° = folded flat, big = full roll)")
    _new_input(ng, "Limit End", F, D(3600), 0.0, D(36000), "ANGLE", p, "Max bend at Progress 1")
    _new_input(ng, "Radius", F, 0.08, 0.0005, None, "DISTANCE", p, "Radius of the curl")
    _new_input(ng, "Roll Gap", F, 0.006, 0.0, None, "DISTANCE", p,
               "Spiral spacing per turn — keep ≥ thickness so rolled layers never intersect")
    _new_input(ng, "Land Flat", B, False, None, None, None, p,
               "Tighten the radius near 180° so a flipped page lands flat")
    _new_input(ng, "Curl Down", B, False, None, None, None, p, "Curl below the sheet instead of above")
    _new_input(ng, "Page Turn", B, False, None, None, None, p,
               "Real page-turn motion: the root leads, the free edge follows, and the page lands perfectly "
               "flat (uses Limit as the turn angle, ignores Radius)")
    _new_input(ng, "Page Bend", F, 1.4, -4.0, 4.0, None, p,
               "How much a page curves while it turns. 0 = stiff board, higher = softer paper, "
               "negative = the edge leads (page pushed by the fingertip)")
    _new_input(ng, "Bend at Edge", F, 0.45, 0.0, 1.0, "FACTOR", p,
               "Where the curve happens: 0 = near the spine, 1 = only near the free edge")

    p = I.new_panel("Wave")
    _new_input(ng, "Wave Start", F, 0.0, D(-360), D(360), "ANGLE", p,
               "Wave strength at Progress 0 (90°–125° = ribbon-candy loops; more self-intersects)")
    _new_input(ng, "Wave End", F, 0.0, D(-360), D(360), "ANGLE", p, "Wave strength at Progress 1")
    _new_input(ng, "Wave Length", F, 0.6, 0.001, None, "DISTANCE", p, "Length of one wave on the flat sheet")
    _new_input(ng, "Wave Phase", F, 0.0, None, None, "ANGLE", p, "Wave offset")
    _new_input(ng, "Wave Travel", F, 0.0, None, None, "ANGLE", p,
               "Phase added over the animation — 360° = seamless loop")
    _new_input(ng, "Wave Grow", F, 0.0, 0.0, 1.0, "FACTOR", p,
               "1 = loops grow outward from the Empty as Progress runs, 0 = everywhere at once")
    _new_input(ng, "Wave Softness", F, 1.0, 0.001, None, "DISTANCE", p, "Length of the growing wave front")
    _new_input(ng, "Side Wave Start", F, 0.0, D(-180), D(180), "ANGLE", p,
               "Sideways wave at Progress 0 — combine with Wave for a 3D silk ribbon")
    _new_input(ng, "Side Wave End", F, 0.0, D(-180), D(180), "ANGLE", p, "Sideways wave at Progress 1")
    _new_input(ng, "Side Wave Length", F, 1.0, 0.001, None, "DISTANCE", p, "Length of one sideways wave")
    _new_input(ng, "Side Phase", F, D(90), None, None, "ANGLE", p,
               "Offset between the up/down and sideways waves (90° = spiral / helix motion)")
    _new_input(ng, "Anchor Flat", B, False, None, None, None, p,
               "Keep the sheet flat exactly at the Empty (otherwise only its position is pinned)")

    p = I.new_panel("Twist")
    _new_input(ng, "Twist Start", F, 0.0, None, None, "ANGLE", p, "Twist along the whole length at Progress 0")
    _new_input(ng, "Twist End", F, 0.0, None, None, "ANGLE", p, "Twist at Progress 1")

    p = I.new_panel("Flutter")
    _new_input(ng, "Flutter", F, 0.0, 0.0, D(90), "ANGLE", p, "Organic wobble (paper feels alive)")
    _new_input(ng, "Flutter Scale", F, 2.0, 0.0, None, None, p, "Wobble frequency along the sheet")
    _new_input(ng, "Flutter Speed", F, 3.0, 0.0, None, None, p, "How much the wobble changes over one Progress cycle")
    _new_input(ng, "Flutter At Rest", F, 0.0, 0.0, 1.0, "FACTOR", p,
               "0 = wobble only while moving, 1 = always (floating paper). Loops seamlessly with Progress")
    _new_input(ng, "Seed", F, 0.0, None, None, None, p, "Changes flutter and per-sheet randomness")

    p = I.new_panel("Sheets")
    _new_input(ng, "Sheets", INT, 1, 1, 256, None, p, "Stacked copies (page fan, magazine, stack of paper)")
    _new_input(ng, "Sheet Gap", F, 0.004, 0.0, None, "DISTANCE", p, "Distance between stacked sheets")
    _new_input(ng, "Stagger", F, 0.2, 0.0, 1.0, "FACTOR", p,
               "Delay between sheets (0 = together, 1 = one after another)")
    _new_input(ng, "Flip Count", INT, 0, 0, 256, None, p,
               "How many sheets move (0 = all). Magazine: half the pages = opens to the middle spread")
    _new_input(ng, "Random", F, 0.0, 0.0, 1.0, "FACTOR", p, "Per-sheet timing and angle variation")
    _new_input(ng, "Cover", B, False, None, None, None, p,
               "First and last sheet use the cover materials (slots 4–6)")
    _new_input(ng, "Page Materials", B, False, None, None, None, p,
               "Every sheet uses its own materials (slots 3·sheet … ) — set automatically by Load Page Images")
    _new_input(ng, "Cover Stiffness", F, 1.0, 0.0, 10.0, None, p, "Covers bend less than pages")
    _new_input(ng, "Binding", B, False, None, None, None, p,
               "All sheets are bound at the spine (magazine / book): they meet in one spine and curve "
               "into it through the gutter")
    _new_input(ng, "Gutter", F, 0.08, 0.001, None, "DISTANCE", p,
               "Length over which the pages curve into the spine")
    _new_input(ng, "Spine Height", F, 0.3, 0.0, 1.0, "FACTOR", p,
               "Where the pages meet at the spine: 0 = bottom (deep valley), 1 = top")

    p = I.new_panel("Quality")
    _new_input(ng, "Subdivide", INT, 0, 0, 5, None, p, "Extra subdivision for low-poly meshes")
    _new_input(ng, "Samples", INT, 800, 32, 8192, None, p, "Profile resolution along the bend direction")
    _new_input(ng, "Smooth Shading", B, True, None, None, None, p, "")

    nb = _NB(ng)
    gin = nb.node("NodeGroupInput")
    gout = nb.node("NodeGroupOutput")
    S = gin.outputs
    PI = math.pi

    # ---- geometry: subdivide, duplicate into sheets ----------------------
    sub = nb.node("GeometryNodeSubdivideMesh")
    nb.feed(sub.inputs["Mesh"], S["Geometry"])
    nb.feed(sub.inputs["Level"], S["Subdivide"])
    base_geo = sub.outputs[0]
    g2i = nb.node("GeometryNodeGeometryToInstance")
    nb.links.new(base_geo, g2i.inputs[0])
    dup = nb.node("GeometryNodeDuplicateElements", domain="INSTANCE")
    nb.links.new(g2i.outputs[0], dup.inputs["Geometry"])
    nb.feed(dup.inputs["Amount"], S["Sheets"])
    st_sheet = nb.node("GeometryNodeStoreNamedAttribute", data_type="INT", domain="INSTANCE")
    nb.links.new(dup.outputs["Geometry"], st_sheet.inputs["Geometry"])
    st_sheet.inputs["Name"].default_value = "nk_sheet"
    nb.links.new(dup.outputs["Duplicate Index"], st_sheet.inputs["Value"])
    real = nb.node("GeometryNodeRealizeInstances")
    nb.links.new(st_sheet.outputs[0], real.inputs[0])
    paper = real.outputs[0]

    # ---- smart controller → bend direction --------------------------------
    oinfo = nb.node("GeometryNodeObjectInfo", transform_space="RELATIVE")
    nb.feed(oinfo.inputs["Object"], S["Controller"])
    cx, cy, _ = nb.separate(oinfo.outputs["Location"])

    bb = nb.node("GeometryNodeBoundBox")
    nb.links.new(base_geo, bb.inputs["Geometry"])
    mnx, mny, _ = nb.separate(bb.outputs["Min"])
    mxx, mxy, _ = nb.separate(bb.outputs["Max"])
    ex = nb.sub(cx, nb.mul(nb.add(mnx, mxx), 0.5))
    ey = nb.sub(cy, nb.mul(nb.add(mny, mxy), 0.5))
    hx = nb.math("MAXIMUM", nb.mul(nb.sub(mxx, mnx), 0.5), 1e-6)
    hy = nb.math("MAXIMUM", nb.mul(nb.sub(mxy, mny), 0.5), 1e-6)
    a_norm = nb.math("ARCTAN2", nb.div(ey, hy), nb.div(ex, hx))
    a_snap = nb.mul(nb.math("ROUND", nb.div(a_norm, PI / 4)), PI / 4)
    a_real = nb.math("ARCTAN2", ey, ex)
    a_auto = nb.lerp(a_real, a_snap, S["Snap Direction"])

    rot = nb.node("FunctionNodeRotateVector")
    rot.inputs["Vector"].default_value = (1.0, 0.0, 0.0)
    nb.links.new(oinfo.outputs["Rotation"], rot.inputs["Rotation"])
    rx, ry, _ = nb.separate(rot.outputs[0])
    a_rot = nb.math("ARCTAN2", ry, rx)
    ang = nb.add(nb.lerp(a_rot, a_auto, S["Auto Direction"]), S["Direction"])
    ca, sa = nb.math("COSINE", ang), nb.math("SINE", ang)
    origin_s = nb.add(nb.mul(cx, ca), nb.mul(cy, sa))

    pos = nb.node("GeometryNodeInputPosition").outputs[0]
    px, py, pz = nb.separate(pos)
    s_f = nb.add(nb.mul(px, ca), nb.mul(py, sa))
    t_f = nb.sub(nb.mul(py, ca), nb.mul(px, sa))

    def stat(field):
        n = nb.node("GeometryNodeAttributeStatistic", data_type="FLOAT", domain="POINT")
        nb.links.new(base_geo, n.inputs["Geometry"])
        nb.feed(n.inputs["Attribute"], field)
        return n.outputs["Min"], n.outputs["Max"]

    smin, smax = stat(s_f)
    tmin, tmax = stat(t_f)
    t_mid = nb.mul(nb.add(tmin, tmax), 0.5)
    length = nb.math("MAXIMUM", nb.sub(smax, smin), 1e-6)
    M = S["Samples"]
    ds = nb.div(length, nb.sub(M, 1.0))
    N = S["Sheets"]

    # ---- 1-D profile: one run of samples per sheet -------------------------
    line = nb.node("GeometryNodeMeshLine")
    nb.feed(line.inputs["Count"], nb.mul(N, M))
    line_geo = line.outputs[0]
    idx = nb.node("GeometryNodeInputIndex").outputs[0]
    sheet_l = nb.math("FLOOR", nb.div(nb.add(idx, 0.5), M))
    j = nb.sub(idx, nb.mul(sheet_l, M))
    s_j = nb.mad(j, ds, smin)

    # per-sheet timing: stagger, random delay, flip count
    fc = S["Flip Count"]
    n_flip = nb.lerp(N, nb.math("MINIMUM", fc, N), nb.math("GREATER_THAN", fc, 0.5))
    r1 = nb.random(sheet_l, nb.add(S["Seed"], 11.0))
    r2 = nb.random(sheet_l, nb.add(S["Seed"], 23.0))
    rnd_delay = nb.mul(S["Random"], 0.8)
    span = nb.mad(nb.add(nb.sub(n_flip, 1.0), rnd_delay), S["Stagger"], 1.0)
    tot = nb.mul(S["Progress"], span)
    start = nb.mul(nb.mad(nb.mul(nb.mad(r1, 0.5, 0.5), rnd_delay), 1.0, sheet_l), S["Stagger"])
    pr = nb.math("SUBTRACT", tot, start, clamp=True)
    pr = nb.mul(pr, nb.math("LESS_THAN", sheet_l, n_flip))
    ov = S["Page Overlap"]
    pr_page = nb.clamp01(nb.div(nb.add(nb.sub(S["Page"], sheet_l), ov), nb.add(ov, 1.0)))
    pr = nb.lerp(pr, pr_page, S["Use Page Number"])
    pp = nb.lerp(pr, nb.smoothstep(pr), S["Ease"])
    moving = nb.math("SINE", nb.mul(pp, PI))

    is_cover_l = nb.mul(S["Cover"], nb.math("MAXIMUM",
                        nb.math("COMPARE", sheet_l, 0.0, 0.1),
                        nb.math("COMPARE", sheet_l, nb.sub(N, 1.0), 0.1)))

    base = nb.lerp(smin, origin_s, S["Follow Controller"])
    line_pos = nb.mad(nb.lerp(S["Line Start"], S["Line End"], pp), length, base)
    limit = nb.lerp(S["Limit Start"], S["Limit End"], pp)
    limit = nb.math("MAXIMUM", nb.mul(limit, nb.mad(nb.mul(r2, S["Random"]), nb.mul(moving, 0.25), 1.0)), 0.0)
    w_amp = nb.lerp(S["Wave Start"], S["Wave End"], pp)
    phase = nb.mad(pp, S["Wave Travel"], S["Wave Phase"])

    # anchor = the point that never moves (snapped to a sample so flat parts stay exact)
    anchor_src = nb.lerp(line_pos, origin_s, S["Follow Controller"])
    anchor_raw = nb.math("MINIMUM", anchor_src, line_pos)
    ja = nb.math("MINIMUM", nb.math("MAXIMUM",
                 nb.math("ROUND", nb.div(nb.sub(anchor_raw, smin), ds)), 0.0), nb.sub(M, 1.0))
    anchor_s = nb.mad(ja, ds, smin)
    anchor_idx = nb.mad(sheet_l, M, ja)

    # bend: Archimedean spiral past the line; "land flat" tightens it near 180°
    u = nb.sub(s_j, line_pos)
    past = nb.math("GREATER_THAN", u, 0.0)
    sin_lim = nb.math("MAXIMUM", nb.math("SINE", nb.math("MINIMUM", limit, PI)), 0.0)
    flat_fac = nb.math("MAXIMUM", nb.math("POWER", sin_lim, 0.5), 0.03)
    R = nb.mul(S["Radius"], nb.lerp(1.0, flat_fac, S["Land Flat"]))
    R = nb.mul(R, nb.mad(is_cover_l, S["Cover Stiffness"], 1.0))
    rr2 = nb.sub(nb.mul(R, R), nb.div(nb.mul(S["Roll Gap"], u), PI))
    rr2 = nb.math("MAXIMUM", rr2, nb.mul(nb.mul(R, R), 0.0064))
    kappa = nb.div(past, nb.math("SQRT", rr2))

    def accumulate(value):
        n = nb.node("GeometryNodeAccumulateField", data_type="FLOAT", domain="POINT")
        nb.feed(n.inputs["Value"], value)
        nb.feed(n.inputs["Group ID"], sheet_l)
        return n.outputs["Trailing"]

    theta_curl = nb.math("MINIMUM", accumulate(nb.mul(kappa, ds)), limit)

    # page turn: the turn travels from the root to the free edge, lands exactly flat
    # every point of the page has its own progress q^e (e grows toward the free edge), so:
    #  · the page never dips below the pile (q^e ≥ 0) and always lands flat (1^e = 1)
    #  · two pages can never cross: for the same point, more progress = more angle
    bend = nb.div(S["Page Bend"], nb.mad(is_cover_l, S["Cover Stiffness"], 1.0))
    focus = nb.lerp(0.6, 3.0, S["Bend at Edge"])
    along = nb.math("POWER", nb.clamp01(nb.div(u, length)), focus)
    expo = nb.math("POWER", 2.0, nb.mul(bend, along))
    q_s = nb.math("POWER", nb.math("MAXIMUM", pp, 0.0), expo)
    theta_page = nb.mul(past, nb.lerp(S["Limit Start"], S["Limit End"], nb.smoothstep(nb.clamp01(q_s))))
    theta_b = nb.mul(nb.lerp(theta_curl, theta_page, S["Page Turn"]), nb.mad(S["Curl Down"], -2.0, 1.0))

    # waves (up/down + sideways), optionally growing outward from the anchor
    dist = nb.math("ABSOLUTE", nb.sub(s_j, anchor_s))
    reach = nb.math("MAXIMUM", nb.sub(smax, anchor_s), nb.sub(anchor_s, smin))
    front = nb.mul(pp, nb.add(reach, S["Wave Softness"]))
    env = nb.smoothstep(nb.math("DIVIDE", nb.sub(front, dist), S["Wave Softness"], clamp=True))
    env = nb.lerp(1.0, env, S["Wave Grow"])
    rel = nb.sub(s_j, anchor_s)
    wave_arg = nb.mad(nb.div(rel, S["Wave Length"]), 2 * PI, phase)
    theta_w = nb.mul(nb.mul(w_amp, env), nb.math("SINE", wave_arg))
    side_amp = nb.lerp(S["Side Wave Start"], S["Side Wave End"], pp)
    side_arg = nb.add(nb.mad(nb.div(rel, S["Side Wave Length"]), 2 * PI, phase), S["Side Phase"])
    psi = nb.mul(nb.mul(side_amp, env), nb.math("SINE", side_arg))

    # flutter: loop-safe 4D noise (Progress walks a circle through noise space)
    loop_r = nb.div(S["Flutter Speed"], 2 * PI)
    ang_p = nb.mul(pp, 2 * PI)
    noise = nb.node("ShaderNodeTexNoise", noise_dimensions="4D")
    nb.feed(noise.inputs["Vector"], nb.combine(
        nb.mul(nb.div(nb.sub(s_j, smin), length), S["Flutter Scale"]),
        nb.mul(S["Seed"], 3.1),
        nb.mul(nb.math("COSINE", ang_p), loop_r)))
    nb.feed(noise.inputs["W"], nb.mad(nb.math("SINE", ang_p), loop_r, S["Seed"]))
    noise.inputs["Scale"].default_value = 1.0
    noise.inputs["Detail"].default_value = 1.0
    bend_mask = nb.smoothstep(nb.math("DIVIDE", u, nb.mul(length, 0.25), clamp=True))
    rest_mask = nb.lerp(bend_mask, 1.0, S["Flutter At Rest"])
    f_env = nb.lerp(moving, 1.0, S["Flutter At Rest"])
    theta_f = nb.mul(nb.mul(nb.mul(nb.mad(noise.outputs[0], 2.0, -1.0), S["Flutter"]), rest_mask), f_env)

    theta = nb.add(nb.add(theta_b, theta_w), theta_f)

    def sample_line(value, index):
        n = nb.node("GeometryNodeSampleIndex", data_type="FLOAT", domain="POINT", clamp=True)
        nb.links.new(line_geo, n.inputs["Geometry"])
        nb.feed(n.inputs["Value"], value)
        nb.feed(n.inputs["Index"], index)
        return n.outputs[0]

    theta = nb.sub(theta, nb.mul(sample_line(theta, anchor_idx), S["Anchor Flat"]))

    # integrate the 3-D centre line  T = (cosψ·cosθ, sinψ·cosθ, sinθ)
    cth = nb.math("COSINE", theta)
    ax_ = accumulate(nb.mul(nb.mul(nb.math("COSINE", psi), cth), ds))
    ay_ = accumulate(nb.mul(nb.mul(nb.math("SINE", psi), cth), ds))
    az_ = accumulate(nb.mul(nb.math("SINE", theta), ds))
    prof_x = nb.add(nb.sub(ax_, sample_line(ax_, anchor_idx)), anchor_s)
    prof_y = nb.sub(ay_, sample_line(ay_, anchor_idx))
    prof_z = nb.sub(az_, sample_line(az_, anchor_idx))
    twist = nb.mul(nb.lerp(S["Twist Start"], S["Twist End"], pp), nb.div(rel, length))

    # stacking offset of every sheet: free stack, or bound at a spine with a gutter curve
    gap = S["Sheet Gap"]
    nm1 = nb.sub(N, 1.0)
    off_free = nb.mul(nb.mul(sheet_l, gap), -1.0)
    b_far = nb.lerp(off_free, nb.mul(nb.sub(nm1, sheet_l), gap),
                    nb.mul(nb.sub(1.0, cth), 0.5))
    gut = nb.smoothstep(nb.math("DIVIDE", u, S["Gutter"], clamp=True))
    spine_z = nb.mul(nb.mul(nm1, gap), nb.sub(S["Spine Height"], 1.0))
    off = nb.lerp(off_free, nb.mul(gut, b_far), S["Binding"])
    prof_z = nb.add(prof_z, nb.mul(nb.mul(nb.sub(1.0, gut), spine_z), S["Binding"]))

    st_prof = nb.node("GeometryNodeStoreNamedAttribute", data_type="FLOAT_VECTOR", domain="POINT")
    nb.links.new(line_geo, st_prof.inputs["Geometry"])
    st_prof.inputs["Name"].default_value = "nk_profile"
    nb.feed(st_prof.inputs["Value"], nb.combine(prof_x, prof_y, prof_z))
    st_ang = nb.node("GeometryNodeStoreNamedAttribute", data_type="FLOAT_VECTOR", domain="POINT")
    nb.links.new(st_prof.outputs[0], st_ang.inputs["Geometry"])
    st_ang.inputs["Name"].default_value = "nk_angles"
    nb.feed(st_ang.inputs["Value"], nb.combine(theta, psi, twist))
    st_off = nb.node("GeometryNodeStoreNamedAttribute", data_type="FLOAT", domain="POINT")
    nb.links.new(st_ang.outputs[0], st_off.inputs["Geometry"])
    st_off.inputs["Name"].default_value = "nk_off"
    nb.feed(st_off.inputs["Value"], off)
    profile_geo = st_off.outputs[0]

    # ---- map every vertex onto its sheet's profile ------------------------
    na_sheet = nb.node("GeometryNodeInputNamedAttribute", data_type="INT")
    na_sheet.inputs["Name"].default_value = "nk_sheet"
    sheet_p = na_sheet.outputs["Attribute"]

    f = nb.div(nb.sub(s_f, smin), ds)
    j0 = nb.math("MINIMUM", nb.math("MAXIMUM", nb.math("FLOOR", f), 0.0), nb.sub(M, 2.0))
    w = nb.math("SUBTRACT", f, j0, clamp=True)
    i0 = nb.math("ROUND", nb.mad(sheet_p, M, j0))
    i1 = nb.add(i0, 1.0)

    def sample_attr(name, dtype):
        na = nb.node("GeometryNodeInputNamedAttribute", data_type=dtype)
        na.inputs["Name"].default_value = name
        out = []
        for index in (i0, i1):
            n = nb.node("GeometryNodeSampleIndex", data_type=dtype, domain="POINT", clamp=True)
            nb.links.new(profile_geo, n.inputs["Geometry"])
            nb.links.new(na.outputs["Attribute"], n.inputs["Value"])
            nb.feed(n.inputs["Index"], index)
            out.append(n.outputs[0])
        if dtype == "FLOAT":
            return nb.lerp(out[0], out[1], w)
        return nb.vmath("ADD", out[0], nb.vmath("SCALE", nb.vmath("SUBTRACT", out[1], out[0]), scale=w))

    C = sample_attr("nk_profile", "FLOAT_VECTOR")
    TH, PS, PHI = nb.separate(sample_attr("nk_angles", "FLOAT_VECTOR"))
    b_off = nb.add(pz, sample_attr("nk_off", "FLOAT"))
    a_off = nb.sub(t_f, t_mid)

    cth_v, sth_v = nb.math("COSINE", TH), nb.math("SINE", TH)
    cps_v, sps_v = nb.math("COSINE", PS), nb.math("SINE", PS)
    T = nb.combine(nb.mul(cps_v, cth_v), nb.mul(sps_v, cth_v), sth_v)
    side0 = nb.combine(nb.mul(sps_v, -1.0), cps_v, 0.0)
    N0 = nb.vmath("CROSS_PRODUCT", T, side0)
    cphi, sphi = nb.math("COSINE", PHI), nb.math("SINE", PHI)
    side = nb.vmath("ADD", nb.vmath("SCALE", side0, scale=cphi), nb.vmath("SCALE", N0, scale=sphi))
    nrm = nb.vmath("SUBTRACT", nb.vmath("SCALE", N0, scale=cphi), nb.vmath("SCALE", side0, scale=sphi))
    P = nb.vmath("ADD", nb.vmath("ADD", C, nb.combine(0.0, t_mid, 0.0)),
                 nb.vmath("ADD", nb.vmath("SCALE", side, scale=a_off), nb.vmath("SCALE", nrm, scale=b_off)))
    Pd, Pt, Pz = nb.separate(P)
    new_x = nb.sub(nb.mul(Pd, ca), nb.mul(Pt, sa))
    new_y = nb.add(nb.mul(Pd, sa), nb.mul(Pt, ca))

    setp = nb.node("GeometryNodeSetPosition")
    nb.links.new(paper, setp.inputs["Geometry"])
    nb.feed(setp.inputs["Position"], nb.combine(new_x, new_y, Pz))

    # covers → material slot 4 (index 3); Solidify offsets give 5 (inside) and 6 (edge)
    is_cover_p = nb.mul(S["Cover"], nb.math("MAXIMUM",
                        nb.math("COMPARE", sheet_p, 0.0, 0.1),
                        nb.math("COMPARE", sheet_p, nb.sub(N, 1.0), 0.1)))
    setm = nb.node("GeometryNodeSetMaterialIndex")
    nb.links.new(setp.outputs[0], setm.inputs["Geometry"])
    pm = S["Page Materials"]
    nb.feed(setm.inputs["Selection"], nb.math("GREATER_THAN", nb.math("MAXIMUM", is_cover_p, pm), 0.5))
    nb.feed(setm.inputs["Material Index"], nb.math("ROUND", nb.lerp(3.0, nb.mul(sheet_p, 3.0), pm)))

    smooth_n = nb.node("GeometryNodeSetShadeSmooth")
    nb.links.new(setm.outputs[0], smooth_n.inputs[0])
    nb.feed(smooth_n.inputs["Shade Smooth"], S["Smooth Shading"])
    nb.links.new(smooth_n.outputs[0], gout.inputs[0])

    gin.location = (-600, 0)
    gout.location = (nb._x + 300, 0)
    ng["nk_version"] = NG_VERSION
    return ng


# ==========================================================================
#  Formats, presets, materials
# ==========================================================================
FORMATS = {                      # key: (label, height / width)
    "CUSTOM": ("Custom", None),
    "A4": ("A4 / A-series  1:1.414", 1.4142),
    "LETTER": ("US Letter  8.5×11", 1.2941),
    "MAGAZINE": ("Magazine  8.375×10.875", 1.2985),
    "BOOK": ("Book  6×9", 1.5),
    "SQUARE": ("Square  1:1", 1.0),
    "PORTRAIT": ("Portrait  4:5", 1.25),
    "STORY": ("Story  9:16", 1.7778),
    "WIDE": ("Wide  16:9", 0.5625),
    "CARD": ("Card  85.6×54", 0.6308),
    "POSTCARD": ("Postcard  6×4", 0.6667),
    "RIBBON": ("Ribbon strip", 0.06),
}
FORMAT_ITEMS = [(k, v[0], "") for k, v in FORMATS.items()]

# ctrl = where the Empty goes, as a fraction of the sheet (-0.5 … 0.5): the side that moves.
PRESETS = {
    # ---------------- Paper
    "ROLL": dict(label="Roll", icon="MOD_SCREW", group="Paper",
                 format="A4", width=1.0, res=220, corner=0.0, thickness=0.002, material="PAPER",
                 ctrl=(0.0, 0.5),
                 inputs={"Line Start": 0.0, "Line End": -0.98, "Radius": 0.07, "Roll Gap": 0.005}),
    "SCROLL": dict(label="Scroll Reveal", icon="SEQ_STRIP_DUPLICATE", group="Paper",
                   format="WIDE", width=1.6, res=260, corner=0.0, thickness=0.002, material="PAPER",
                   ctrl=(0.5, 0.0),
                   inputs={"Line Start": -1.0, "Line End": 0.0, "Radius": 0.045, "Roll Gap": 0.004}),
    "FOLD": dict(label="Fold in Half", icon="MOD_MIRROR", group="Paper",
                 format="A4", width=1.0, res=200, corner=0.0, thickness=0.002, material="PAPER",
                 ctrl=(0.5, 0.0),
                 inputs={"Follow Controller": False, "Line Start": 0.5, "Line End": 0.5,
                         "Limit Start": D(0), "Limit End": D(178), "Radius": 0.01, "Roll Gap": 0.0}),
    "PEEL": dict(label="Card Peel", icon="MOD_SIMPLEDEFORM", group="Paper",
                 format="CARD", width=0.856, res=160, corner=0.03, thickness=0.004, material="CARD",
                 ctrl=(0.5, 0.5),
                 inputs={"Line Start": 0.0, "Line End": -0.65, "Limit Start": D(180), "Limit End": D(180),
                         "Radius": 0.035, "Roll Gap": 0.0}),
    "STICKER": dict(label="Sticker Peel", icon="BRUSH_DATA", group="Paper",
                    format="SQUARE", width=0.6, res=150, corner=0.09, thickness=0.002, material="STICKER",
                    ctrl=(0.5, 0.5),
                    inputs={"Line Start": 0.0, "Line End": -0.92, "Limit Start": D(150), "Limit End": D(165),
                            "Radius": 0.05, "Roll Gap": 0.0, "Flutter": D(5)}),
    # ---------------- Pages
    "FLIP": dict(label="Page Flip", icon="FILE_BLANK", group="Pages",
                 format="A4", width=1.0, res=140, corner=0.0, thickness=0.0015, material="PAPER",
                 ctrl=(0.5, 0.0),
                 inputs={"Follow Controller": False, "Limit Start": D(0), "Limit End": D(180),
                         "Page Turn": True, "Page Bend": 1.4, "Roll Gap": 0.0, "Flutter": D(3)}),
    "FAN": dict(label="Page Fan", icon="DOCUMENTS", group="Pages",
                format="A4", width=1.0, res=72, corner=0.0, thickness=0.0015, material="PAPER",
                ctrl=(0.5, 0.0),
                inputs={"Follow Controller": False, "Limit Start": D(0), "Limit End": D(180),
                        "Page Turn": True, "Page Bend": 1.8, "Roll Gap": 0.0,
                        "Sheets": 16, "Sheet Gap": 0.004, "Stagger": 0.12, "Random": 0.3,
                        "Binding": True, "Gutter": 0.06, "Spine Height": 0.4, "Samples": 300}),
    "MAGAZINE": dict(label="Magazine", icon="FILE_IMAGE", group="Pages",
                     format="MAGAZINE", width=1.0, res=76, corner=0.0, thickness=0.0015, material="MAGAZINE",
                     ctrl=(0.5, 0.0),
                     inputs={"Follow Controller": False, "Limit Start": D(0), "Limit End": D(180),
                             "Page Turn": True, "Page Bend": 1.4, "Roll Gap": 0.0,
                             "Use Page Number": True, "Page": 2.5, "Page Overlap": 0.0,
                             "Sheets": 14, "Sheet Gap": 0.004, "Stagger": 0.22, "Flip Count": 7,
                             "Random": 0.35, "Cover": True, "Cover Stiffness": 1.5,
                             "Binding": True, "Gutter": 0.08, "Spine Height": 0.3, "Samples": 300}),
    # ---------------- Ribbons & loops
    "WAVE": dict(label="Ribbon Candy", icon="MOD_WAVE", group="Ribbon & Loops",
                 format="RIBBON", width=6.0, res=900, corner=0.0, thickness=0.003, material="GLOSSY",
                 ctrl=(-0.5, 0.0),
                 inputs={"Limit Start": D(0), "Limit End": D(0), "Wave Start": D(115), "Wave End": D(115),
                         "Wave Length": 0.7, "Wave Grow": 1.0, "Wave Softness": 1.2, "Samples": 2000}),
    "FLOW": dict(label="Ribbon Flow ∞", icon="FORCE_HARMONIC", group="Ribbon & Loops", loop=True,
                 format="RIBBON", width=6.0, res=900, corner=0.0, thickness=0.003, material="GLOSSY",
                 ctrl=(0.0, 0.0),
                 inputs={"Limit Start": D(0), "Limit End": D(0), "Wave Start": D(38), "Wave End": D(38),
                         "Wave Length": 1.8, "Wave Travel": D(360), "Side Wave Start": D(60),
                         "Side Wave End": D(60), "Side Wave Length": 1.8, "Side Phase": D(90),
                         "Twist Start": D(120), "Twist End": D(120), "Samples": 2000}),
    "BANNER": dict(label="Banner Wave ∞", icon="MOD_OCEAN", group="Ribbon & Loops", loop=True,
                   format="WIDE", width=1.6, res=200, corner=0.0, thickness=0.002, material="PAPER",
                   ctrl=(-0.5, 0.0),
                   inputs={"Limit Start": D(0), "Limit End": D(0), "Wave Start": D(16), "Wave End": D(16),
                           "Wave Length": 0.9, "Wave Travel": D(360), "Anchor Flat": True,
                           "Side Wave Start": D(4), "Side Wave End": D(4), "Side Wave Length": 1.4,
                           "Flutter": D(4), "Flutter At Rest": 1.0, "Flutter Speed": 1.5, "Samples": 1000}),
    "TWIST": dict(label="Twist Ribbon", icon="MOD_SCREW", group="Ribbon & Loops",
                  format="RIBBON", width=5.0, res=700, corner=0.0, thickness=0.003, material="GLOSSY",
                  ctrl=(-0.5, 0.0),
                  inputs={"Limit Start": D(0), "Limit End": D(0), "Twist Start": D(0), "Twist End": D(540),
                          "Wave Start": D(15), "Wave End": D(15), "Wave Length": 1.2,
                          "Wave Travel": D(180), "Samples": 1600}),
    "FLOAT": dict(label="Floating Paper ∞", icon="OUTLINER_OB_FORCE_FIELD", group="Ribbon & Loops", loop=True,
                  format="A4", width=1.0, res=140, corner=0.0, thickness=0.0015, material="PAPER",
                  ctrl=(0.0, 0.0),
                  inputs={"Limit Start": D(0), "Limit End": D(0), "Wave Start": D(22), "Wave End": D(22),
                          "Wave Length": 1.5, "Wave Travel": D(360), "Twist Start": D(25), "Twist End": D(25),
                          "Flutter": D(10), "Flutter At Rest": 1.0, "Flutter Speed": 2.0}),
}
PRESET_ITEMS = [(k, v["label"], "", v["icon"], i) for i, (k, v) in enumerate(PRESETS.items())]
PRESET_GROUPS = ["Paper", "Pages", "Ribbon & Loops"]

# name: [(suffix, color, roughness, metallic, thin film), ...]  3 slots, or 6 with covers
MATERIAL_STYLES = {
    "PAPER": [("Front", (0.92, 0.91, 0.88, 1), 0.6, 0.0, 0.0),
              ("Back", (0.95, 0.35, 0.12, 1), 0.6, 0.0, 0.0),
              ("Edge", (0.85, 0.84, 0.8, 1), 0.7, 0.0, 0.0)],
    "CARD": [("Front", (0.05, 0.03, 0.03, 1), 0.25, 0.6, 0.0),
             ("Back", (0.75, 1.0, 0.55, 1), 0.35, 0.0, 0.0),
             ("Edge", (0.8, 0.8, 0.8, 1), 0.5, 0.0, 0.0)],
    "STICKER": [("Front", (0.98, 0.78, 0.2, 1), 0.15, 0.0, 0.0),
                ("Back", (0.93, 0.93, 0.93, 1), 0.5, 0.0, 0.0),
                ("Edge", (0.95, 0.95, 0.95, 1), 0.5, 0.0, 0.0)],
    "GLOSSY": [("Front", (0.55, 0.45, 0.95, 1), 0.12, 0.0, 450.0),
               ("Back", (0.9, 0.5, 0.8, 1), 0.12, 0.0, 450.0),
               ("Edge", (0.9, 0.9, 1.0, 1), 0.3, 0.0, 0.0)],
    "MAGAZINE": [("Page Front", (0.94, 0.93, 0.91, 1), 0.45, 0.0, 0.0),
                 ("Page Back", (0.9, 0.89, 0.87, 1), 0.45, 0.0, 0.0),
                 ("Page Edge", (0.88, 0.87, 0.85, 1), 0.6, 0.0, 0.0),
                 ("Cover", (0.85, 0.1, 0.12, 1), 0.18, 0.0, 0.0),
                 ("Cover Inside", (0.95, 0.94, 0.92, 1), 0.3, 0.0, 0.0),
                 ("Cover Edge", (0.9, 0.9, 0.9, 1), 0.4, 0.0, 0.0)],
}


def _make_material(name, color, rough, metal, film):
    mat = bpy.data.materials.get(name)
    if mat:
        return mat
    mat = bpy.data.materials.new(name)
    if hasattr(mat, "use_nodes") and not mat.node_tree:
        mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = rough
        bsdf.inputs["Metallic"].default_value = metal
        if film and "Thin Film Thickness" in bsdf.inputs:
            bsdf.inputs["Thin Film Thickness"].default_value = film
    mat.diffuse_color = color
    return mat


def _assign_materials(obj, style):
    obj.data.materials.clear()
    for suffix, color, rough, metal, film in MATERIAL_STYLES[style]:
        obj.data.materials.append(_make_material(f"NK {style.title()} {suffix}", color, rough, metal, film))


# ==========================================================================
#  Modifier input access (Blender 5.2 moved GN inputs to mod.properties.inputs)
# ==========================================================================
def _socket_ids(ng):
    return {it.name: it.identifier for it in ng.interface.items_tree
            if getattr(it, "item_type", "") == "SOCKET" and it.in_out == "INPUT"}


def _input_ref(mod, ident):
    props = getattr(mod, "properties", None)
    ins = getattr(props, "inputs", None) if props is not None else None
    if ins is not None:
        item = getattr(ins, ident, None)
        if item is not None and hasattr(item, "value"):
            return item, "value"
    return mod, f'["{ident}"]'


def set_input(mod, ident, val):
    owner, prop = _input_ref(mod, ident)
    if prop == "value":
        owner.value = val
    else:
        mod[ident] = val


def get_input(mod, ident):
    owner, prop = _input_ref(mod, ident)
    return owner.value if prop == "value" else mod[ident]


def draw_input(layout, mod, ident, text, **kw):
    owner, prop = _input_ref(mod, ident)
    layout.prop(owner, prop, text=text, **kw)


def get_bend_mod(obj):
    if obj is None:
        return None
    for m in obj.modifiers:
        if m.type == "NODES" and m.node_group and m.node_group.name.startswith(NG_NAME):
            return m
    return None


def get_controller(mod):
    if mod is None:
        return None
    ids = _socket_ids(mod.node_group)
    if "Controller" not in ids:
        return None
    try:
        return get_input(mod, ids["Controller"])
    except Exception:
        return None


# ==========================================================================
#  Sheet mesh (size, format, resolution, rounded corners)
# ==========================================================================
def make_sheet_mesh(name, w, h, res, corner=0.0):
    long_side = max(w, h)
    nx = max(2, round(res * w / long_side))
    ny = max(2, round(res * h / long_side))
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    bmesh.ops.create_grid(bm, x_segments=nx, y_segments=ny, size=1.0)
    xs = [v.co.x for v in bm.verts]
    ys = [v.co.y for v in bm.verts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    hw, hh = w / 2, h / 2
    r = max(0.0, min(corner, hw, hh))
    cx, cy = hw - r, hh - r
    for v in bm.verts:
        x = ((v.co.x - x0) / (x1 - x0) - 0.5) * w
        y = ((v.co.y - y0) / (y1 - y0) - 0.5) * h
        if r > 1e-6 and abs(x) > cx and abs(y) > cy:
            # squircle map: corner square → quarter disc (keeps the grid connected)
            uu, vv = (abs(x) - cx) / r, (abs(y) - cy) / r
            x = math.copysign(cx + r * uu * math.sqrt(max(0.0, 1 - vv * vv / 2)), x)
            y = math.copysign(cy + r * vv * math.sqrt(max(0.0, 1 - uu * uu / 2)), y)
        v.co = (x, y, 0.0)
    for f in bm.faces:
        for loop in f.loops:
            co = loop.vert.co
            loop[uv].uv = (co.x / w + 0.5, co.y / h + 0.5)   # planar UV → artwork is clipped, not stretched
    bm.to_mesh(me)
    bm.free()
    return me


def _local_bounds(obj):
    xs = [v.co.x for v in obj.data.vertices] or [0.0]
    ys = [v.co.y for v in obj.data.vertices] or [0.0]
    return min(xs), max(xs), min(ys), max(ys)


_SHEET_LOCK = [False]


def rebuild_sheet(obj):
    """Regenerate a generated sheet's mesh from its NK sheet settings, keeping the Empty in place."""
    sp = obj.nk_sheet
    if not sp.generated or obj.type != "MESH":
        return
    old_b = _local_bounds(obj)
    ctrl = get_controller(get_bend_mod(obj))
    rel = None
    if ctrl is not None and ctrl.parent == obj:
        x0, x1, y0, y1 = old_b
        rel = ((ctrl.location.x - (x0 + x1) / 2) / max(x1 - x0, 1e-6),
               (ctrl.location.y - (y0 + y1) / 2) / max(y1 - y0, 1e-6))
    old = obj.data
    new = make_sheet_mesh(old.name, sp.width, sp.height, sp.resolution, sp.corner)
    if sp.cut_alpha and sp.front_image is not None:
        _cut_by_alpha(new, sp.front_image)
    for m in old.materials:
        new.materials.append(m)
    obj.data = new
    if old.users == 0:
        bpy.data.meshes.remove(old)
    if rel is not None:
        ctrl.location.x = rel[0] * sp.width
        ctrl.location.y = rel[1] * sp.height
    apply_images(obj)
    obj.update_tag()


def _upd_size(self, context):
    if _SHEET_LOCK[0]:
        return
    _SHEET_LOCK[0] = True
    try:
        ratio = FORMATS.get(self.format, (None, None))[1]
        if ratio:
            self.height = self.width * ratio
    finally:
        _SHEET_LOCK[0] = False
    rebuild_sheet(self.id_data)


def _upd_height(self, context):
    if _SHEET_LOCK[0]:
        return
    ratio = FORMATS.get(self.format, (None, None))[1]
    if ratio and abs(self.height - self.width * ratio) > 1e-5:
        _SHEET_LOCK[0] = True
        self.format = "CUSTOM"          # typing a height means a custom size
        _SHEET_LOCK[0] = False
    rebuild_sheet(self.id_data)


def _upd_mesh(self, context):
    if not _SHEET_LOCK[0]:
        rebuild_sheet(self.id_data)


class NKImageItem(bpy.types.PropertyGroup):
    image: PointerProperty(type=bpy.types.Image)


class NKSheetProps(bpy.types.PropertyGroup):
    generated: BoolProperty(default=False)
    format: EnumProperty(name="Format", items=FORMAT_ITEMS, default="A4", update=_upd_size)
    width: FloatProperty(name="Width", default=1.0, min=0.01, soft_max=20.0, unit="LENGTH",
                         update=_upd_size)
    height: FloatProperty(name="Height", default=1.414, min=0.01, soft_max=20.0, unit="LENGTH",
                          update=_upd_height)
    resolution: IntProperty(name="Resolution", default=160, min=8, max=3000,
                            description="Segments along the longest side", update=_upd_mesh)
    corner: FloatProperty(name="Corner Radius", default=0.0, min=0.0, soft_max=1.0, unit="LENGTH",
                          update=_upd_mesh)
    front_image: PointerProperty(type=bpy.types.Image)
    back_image: PointerProperty(type=bpy.types.Image)
    pages: CollectionProperty(type=NKImageItem)
    cut_alpha: BoolProperty(name="Cut Shape from Transparency", default=False,
                            description="Die-cut the sheet to the front image's alpha (PNG stickers, logos)",
                            update=_upd_mesh)
    back_mirror: EnumProperty(name="Back Image", items=[
        ("X", "Mirror ↔", "Correct for pages that flip sideways (book, magazine, page flip)"),
        ("Y", "Mirror ↕", "Correct for sheets that flip up / down (roll from the top)"),
        ("NONE", "As Is", ""),
    ], default="X", update=lambda self, ctx: apply_images(self.id_data))


def _set_sheet_props(obj, fmt, w, h, res, corner):
    _SHEET_LOCK[0] = True
    try:
        sp = obj.nk_sheet
        sp.generated = True
        sp.format, sp.width, sp.height, sp.resolution, sp.corner = fmt, w, h, res, corner
    finally:
        _SHEET_LOCK[0] = False


# ==========================================================================
#  Images (optional): front / back artwork, die-cut from alpha, per-page magazine atlas
# ==========================================================================
IMG = "NK_IMG"


def _pixels(img):
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    return px.reshape(h, w, 4)


def _cut_by_alpha(me, img, threshold=0.5):
    """Delete faces whose UV centre falls on transparent pixels — a real die-cut edge."""
    w, h = img.size
    if w == 0 or h == 0:
        return
    alpha = _pixels(img)[:, :, 3]
    bm = bmesh.new()
    bm.from_mesh(me)
    uv = bm.loops.layers.uv.active
    kill = []
    for f in bm.faces:
        n = len(f.loops)
        cu = sum(l[uv].uv.x for l in f.loops) / n
        cv = sum(l[uv].uv.y for l in f.loops) / n
        x = min(w - 1, max(0, int(cu * w)))
        y = min(h - 1, max(0, int(cv * h)))
        if alpha[y, x] < threshold:
            kill.append(f)
    if kill and len(kill) < len(bm.faces):
        bmesh.ops.delete(bm, geom=kill, context="FACES")
        bm.to_mesh(me)
    bm.free()


def _unique_materials(obj):
    for slot in obj.material_slots:
        m = slot.material
        if m is not None and m.get("nk_owner") != obj.name_full:
            new = m.copy()
            new.name = f"{obj.name} · {m.name.replace('NK ', '')}"
            new["nk_owner"] = obj.name_full
            slot.material = new


def _clear_img_nodes(mat):
    if mat is None or mat.node_tree is None:
        return
    nt = mat.node_tree
    for n in [n for n in nt.nodes if n.name.startswith(IMG)]:
        nt.nodes.remove(n)


def _setup_img(mat, image, cell=None, cols=1, rows=1, mirror="NONE", use_alpha=False):
    _clear_img_nodes(mat)
    if image is None or mat is None or mat.node_tree is None:
        return
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return
    x0, y0 = bsdf.location.x - 1100, bsdf.location.y

    def node(idname, name, dx, dy=0, **props):
        n = nt.nodes.new(idname)
        n.name = f"{IMG}_{name}"
        n.label = name
        n.location = (x0 + dx, y0 + dy)
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def math_n(name, op, a, b, dx, dy):
        n = node("ShaderNodeMath", name, dx, dy, operation=op)
        for i, v in enumerate((a, b)):
            if isinstance(v, bpy.types.NodeSocket):
                nt.links.new(v, n.inputs[i])
            else:
                n.inputs[i].default_value = v
        return n.outputs[0]

    vec = node("ShaderNodeTexCoord", "UV", 0).outputs["UV"]
    if mirror in {"X", "Y"}:
        mp = node("ShaderNodeMapping", "Mirror", 200)
        mp.inputs["Location"].default_value = (1.0, 0.0, 0.0) if mirror == "X" else (0.0, 1.0, 0.0)
        mp.inputs["Scale"].default_value = (-1.0, 1.0, 1.0) if mirror == "X" else (1.0, -1.0, 1.0)
        nt.links.new(vec, mp.inputs["Vector"])
        vec = mp.outputs[0]
    if cell is not None:
        at = node("ShaderNodeAttribute", "Sheet", 200, -300, attribute_name="nk_sheet",
                  attribute_type="GEOMETRY")
        fac = next(o for o in at.outputs if o.name in {"Fac", "Factor"})
        k = math_n("Cell", "MULTIPLY_ADD", fac, 2.0, 400, -300)     # sheet*2 + side
        k.node.inputs[2].default_value = float(cell)
        rowi = math_n("RowIdx", "FLOOR", math_n("Div", "DIVIDE", k, float(cols), 550, -300), 0.0, 700, -300)
        col = math_n("Col", "SUBTRACT", k, math_n("RowMul", "MULTIPLY", rowi, float(cols), 850, -400), 1000, -300)
        row = math_n("Row", "SUBTRACT", float(rows - 1), rowi, 850, -250)
        sep = node("ShaderNodeSeparateXYZ", "Split", 400)
        nt.links.new(vec, sep.inputs[0])
        uu = math_n("U", "DIVIDE", math_n("UC", "ADD", sep.outputs[0], col, 1150, 0), float(cols), 1300, 0)
        vv = math_n("V", "DIVIDE", math_n("VR", "ADD", sep.outputs[1], row, 1150, -150), float(rows), 1300, -150)
        cmb = node("ShaderNodeCombineXYZ", "Join", 1450)
        nt.links.new(uu, cmb.inputs[0])
        nt.links.new(vv, cmb.inputs[1])
        vec = cmb.outputs[0]
    tex = node("ShaderNodeTexImage", "Image", 1600, image=image, extension="EXTEND")
    tex.location = (bsdf.location.x - 350, bsdf.location.y)
    nt.links.new(vec, tex.inputs["Vector"])
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    if use_alpha:
        nt.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])


def _style_of(obj):
    return PRESETS.get(obj.get("nk_preset", ""), {}).get("material", "PAPER")


def _build_page_materials(obj):
    """One front + one back material per sheet, each with its own full-resolution image."""
    sp = obj.nk_sheet
    mod = get_bend_mod(obj)
    if mod is None:
        return
    ids = _socket_ids(mod.node_group)
    sheets = int(get_input(mod, ids["Sheets"]))
    style = MATERIAL_STYLES[_style_of(obj)]
    imgs = [it.image for it in sp.pages]
    mats = []
    for k in range(sheets):
        for side in (0, 1):
            idx = 2 * k + side
            name = f"{obj.name} · page {idx + 1:02d}"
            m = bpy.data.materials.get(name) or _make_material(name, *style[side][1:])
            m["nk_owner"] = obj.name_full
            img = imgs[idx] if idx < len(imgs) else None
            _setup_img(m, img, mirror="NONE" if side == 0 else sp.back_mirror)
            mats.append(m)
        # every slot must be a different material: Realize Instances de-duplicates the material
        # list, so a repeated edge material would shift every page index after it
        edge = _make_material(f"{obj.name} · edge {k + 1:02d}", *style[2][1:])
        edge["nk_owner"] = obj.name_full
        mats.append(edge)
    obj.data.materials.clear()
    for m in mats:
        obj.data.materials.append(m)
    if "Page Materials" in ids:
        set_input(mod, ids["Page Materials"], True)


def apply_images(obj):
    """(Re)build the image nodes of this object's materials from its NK sheet image settings."""
    if obj is None or obj.type != "MESH":
        return
    sp = obj.nk_sheet
    if len(sp.pages):
        _build_page_materials(obj)
        return
    mats = [s.material for s in obj.material_slots]
    fronts = [mats[i] for i in (0, 3) if i < len(mats)]
    backs = [mats[i] for i in (1, 4) if i < len(mats)]
    for m in fronts:
        _setup_img(m, sp.front_image, use_alpha=sp.cut_alpha)
    for m in backs:
        _setup_img(m, sp.back_image, mirror=sp.back_mirror, use_alpha=sp.cut_alpha)


def _natural_key(name):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


# ==========================================================================
#  Applying a preset
# ==========================================================================
def place_controller(obj, ctrl, fx, fy):
    x0, x1, y0, y1 = _local_bounds(obj)
    ctrl.location = ((x0 + x1) / 2 + fx * (x1 - x0), (y0 + y1) / 2 + fy * (y1 - y0), 0.0)
    ctrl.rotation_euler = (0.0, 0.0, math.atan2(fy, fx) if (fx or fy) else 0.0)


def ensure_controller(obj, mod, preset):
    ids = _socket_ids(mod.node_group)
    ctrl = get_controller(mod)
    x0, x1, y0, y1 = _local_bounds(obj)
    if ctrl is None or ctrl.name not in bpy.data.objects:
        ctrl = bpy.data.objects.new(f"{obj.name} Ctrl", None)
        for coll in obj.users_collection:
            coll.objects.link(ctrl)
        ctrl.empty_display_type = "SINGLE_ARROW"
        ctrl.parent = obj
        ctrl.matrix_parent_inverse.identity()
        ctrl.show_in_front = True
    ctrl.empty_display_size = 0.2 * max(min(x1 - x0, y1 - y0), 0.1)
    place_controller(obj, ctrl, *preset["ctrl"])
    set_input(mod, ids["Controller"], ctrl)
    return ctrl


def apply_bend(obj, preset_key, set_materials=True):
    preset = PRESETS[preset_key]
    ng = build_node_group()
    mod = get_bend_mod(obj)
    if mod is None:
        mod = obj.modifiers.new(MOD_NAME, "NODES")
    if mod.node_group != ng:
        mod.node_group = ng
    ids = _socket_ids(ng)
    for it in ng.interface.items_tree:
        if getattr(it, "item_type", "") == "SOCKET" and it.in_out == "INPUT" \
                and it.name != "Controller" and hasattr(it, "default_value"):
            try:
                set_input(mod, it.identifier, it.default_value)
            except Exception as e:
                print("NK Bend: could not reset", it.name, e)
    for name, val in preset["inputs"].items():
        set_input(mod, ids[name], val)
    set_input(mod, ids["Progress"], 0.25 if preset.get("loop") else 0.6)
    ensure_controller(obj, mod, preset)

    sol = obj.modifiers.get(SOLID_NAME)
    if sol is None:
        sol = obj.modifiers.new(SOLID_NAME, "SOLIDIFY")
    sol.thickness = preset["thickness"]
    sol.offset = -1.0
    sol.use_even_offset = True
    sol.use_quality_normals = True
    sol.material_offset = 1
    sol.material_offset_rim = 2
    if set_materials:
        _assign_materials(obj, preset["material"])
    obj["nk_preset"] = preset_key
    obj.update_tag()
    return mod


# ==========================================================================
#  Animation helpers
# ==========================================================================
def _find_fcurve(obj, path):
    ad = obj.animation_data
    if not ad or not ad.action:
        return None
    try:
        from bpy_extras import anim_utils
        cb = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
        if cb:
            fc = cb.fcurves.find(path)
            if fc:
                return fc
    except Exception:
        pass
    try:
        return ad.action.fcurves.find(path)
    except Exception:
        return None


def _bend_owner(context):
    """The sheet being edited — also when its controller Empty is the active object."""
    obj = context.active_object
    if get_bend_mod(obj):
        return obj
    if obj is not None and obj.parent is not None and get_bend_mod(obj.parent):
        return obj.parent
    return None


# ==========================================================================
#  Operators
# ==========================================================================
class NKBEND_OT_add(bpy.types.Operator):
    bl_idname = "nk_bend.add"
    bl_label = "Add Bend Sheet"
    bl_description = "Create a new sheet with this preset at the 3D cursor"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(items=PRESET_ITEMS)

    def execute(self, context):
        p = PRESETS[self.preset]
        ratio = FORMATS[p["format"]][1] or 1.0
        w, h = p["width"], p["width"] * ratio
        me = make_sheet_mesh(f"NK {p['label']}", w, h, p["res"], p["corner"])
        obj = bpy.data.objects.new(f"NK {p['label']}", me)
        context.collection.objects.link(obj)
        obj.location = context.scene.cursor.location
        _set_sheet_props(obj, p["format"], w, h, p["res"], p["corner"])
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        apply_bend(obj, self.preset)
        if p.get("loop"):
            context.scene.nk_bend.loop = True
            context.scene.nk_bend.interpolation = "LINEAR"
        return {"FINISHED"}


class NKBEND_OT_apply(bpy.types.Operator):
    bl_idname = "nk_bend.apply"
    bl_label = "Bend Selected"
    bl_description = ("Add the preset to the selected flat meshes (lying in their local XY plane). "
                      "Keeps their own materials")
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(items=PRESET_ITEMS)

    def execute(self, context):
        done = 0
        for obj in list(context.selected_objects):
            if obj.type != "MESH":
                continue
            zs = [v.co.z for v in obj.data.vertices]
            if zs and (max(zs) - min(zs)) > 0.05 * max(obj.dimensions.x, obj.dimensions.y, 1e-6):
                self.report({"WARNING"}, f"{obj.name} is not flat in local XY — results may look odd")
            apply_bend(obj, self.preset, set_materials=len(obj.data.materials) == 0)
            done += 1
        if not done:
            self.report({"ERROR"}, "Select at least one mesh")
            return {"CANCELLED"}
        return {"FINISHED"}


class NKBEND_OT_place_ctrl(bpy.types.Operator):
    bl_idname = "nk_bend.place_ctrl"
    bl_label = "Place Controller"
    bl_description = "Move the controller to this edge / corner — that side of the sheet will move"
    bl_options = {"REGISTER", "UNDO"}

    fx: FloatProperty()
    fy: FloatProperty()

    def execute(self, context):
        obj = _bend_owner(context)
        ctrl = get_controller(get_bend_mod(obj))
        if ctrl is None:
            self.report({"ERROR"}, "No controller — press a preset button first")
            return {"CANCELLED"}
        place_controller(obj, ctrl, self.fx, self.fy)
        obj.update_tag()
        return {"FINISHED"}


class NKBEND_OT_select_ctrl(bpy.types.Operator):
    bl_idname = "nk_bend.select_ctrl"
    bl_label = "Select Controller"
    bl_description = "Select the controller Empty to move / animate it"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ctrl = get_controller(get_bend_mod(_bend_owner(context)))
        if ctrl is None:
            self.report({"ERROR"}, "No controller — press a preset button first")
            return {"CANCELLED"}
        for o in context.selected_objects:
            o.select_set(False)
        ctrl.hide_set(False)
        ctrl.select_set(True)
        context.view_layer.objects.active = ctrl
        return {"FINISHED"}


class NKBEND_OT_keyframe(bpy.types.Operator):
    bl_idname = "nk_bend.keyframe"
    bl_label = "Keyframe Progress"
    bl_description = "Insert Progress keyframes from the current frame over the chosen duration"
    bl_options = {"REGISTER", "UNDO"}

    reverse: BoolProperty(default=False)

    def execute(self, context):
        obj = _bend_owner(context)
        mod = get_bend_mod(obj)
        if mod is None:
            self.report({"ERROR"}, "Select an NK Bend sheet")
            return {"CANCELLED"}
        st = context.scene.nk_bend
        ident = _socket_ids(mod.node_group)["Progress"]
        owner, prop = _input_ref(mod, ident)
        path = owner.path_from_id(prop)
        f0 = context.scene.frame_current
        f1 = f0 + st.duration
        a, b = (1.0, 0.0) if self.reverse else (0.0, 1.0)
        set_input(mod, ident, a)
        obj.keyframe_insert(data_path=path, frame=f0)
        set_input(mod, ident, b)
        obj.keyframe_insert(data_path=path, frame=f1)
        fc = _find_fcurve(obj, path)
        if fc:
            for kp in fc.keyframe_points:
                if round(kp.co.x) in (f0, f1):
                    if st.loop:
                        kp.interpolation = "LINEAR"
                    elif st.interpolation == "SMOOTH":
                        kp.interpolation = "BEZIER"
                        kp.handle_left_type = kp.handle_right_type = "AUTO_CLAMPED"
                    else:
                        kp.interpolation = st.interpolation
                        kp.easing = st.easing
            if st.loop and not any(m.type == "CYCLES" for m in fc.modifiers):
                fc.modifiers.new("CYCLES")
            fc.update()
        obj.update_tag()
        context.scene.frame_set(f0)
        return {"FINISHED"}


# ==========================================================================
#  UI
# ==========================================================================
class NKBEND_OT_load_image(bpy.types.Operator):
    bl_idname = "nk_bend.load_image"
    bl_label = "Load Image"
    bl_description = "Put an image on this side of the sheet (optional)"
    bl_options = {"REGISTER", "UNDO"}

    side: EnumProperty(items=[("FRONT", "Front", ""), ("BACK", "Back", "")])
    filepath: StringProperty(subtype="FILE_PATH")
    filter_image: BoolProperty(default=True, options={"HIDDEN", "SKIP_SAVE"})
    filter_folder: BoolProperty(default=True, options={"HIDDEN", "SKIP_SAVE"})
    fit: BoolProperty(name="Fit Sheet to Image", default=True,
                      description="Resize the sheet to the image's proportions (front image, generated sheets)")
    cut: BoolProperty(name="Cut Shape from Transparency", default=False,
                      description="Die-cut the sheet to the image's alpha — for PNG stickers and logos")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        obj = _bend_owner(context) or context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "Select a sheet first")
            return {"CANCELLED"}
        try:
            img = bpy.data.images.load(self.filepath, check_existing=True)
        except Exception as e:
            self.report({"ERROR"}, f"Could not load image: {e}")
            return {"CANCELLED"}
        sp = obj.nk_sheet
        if len(sp.pages):
            _clear_pages(obj)
        _unique_materials(obj)
        if self.side == "FRONT":
            sp.front_image = img
            w, h = img.size
            if sp.generated and w and h and (self.fit or self.cut):
                _SHEET_LOCK[0] = True
                try:
                    if self.fit:
                        sp.format = "CUSTOM"
                        sp.height = sp.width * h / w
                    sp.cut_alpha = self.cut
                    if self.cut and sp.resolution < 200:
                        sp.resolution = 260            # finer grid = cleaner die-cut edge
                finally:
                    _SHEET_LOCK[0] = False
                rebuild_sheet(obj)
            elif self.cut and not sp.generated:
                self.report({"WARNING"}, "Cut from transparency works on sheets made by NK Bend")
        else:
            sp.back_image = img
        apply_images(obj)
        return {"FINISHED"}


def _clear_pages(obj):
    sp = obj.nk_sheet
    sp.pages.clear()
    mod = get_bend_mod(obj)
    if mod is not None:
        ids = _socket_ids(mod.node_group)
        if "Page Materials" in ids:
            set_input(mod, ids["Page Materials"], False)
    _assign_materials(obj, _style_of(obj))


class NKBEND_OT_load_pages(bpy.types.Operator):
    bl_idname = "nk_bend.load_pages"
    bl_label = "Load Page Images"
    bl_description = ("Select all page images at once, in reading order (cover, inside cover, page 1, page 2 …). "
                      "Every page side gets its own full-resolution image")
    bl_options = {"REGISTER", "UNDO"}

    files: CollectionProperty(type=bpy.types.OperatorFileListElement)
    directory: StringProperty(subtype="DIR_PATH")
    filter_image: BoolProperty(default=True, options={"HIDDEN", "SKIP_SAVE"})
    filter_folder: BoolProperty(default=True, options={"HIDDEN", "SKIP_SAVE"})
    match_sheets: BoolProperty(name="Match Sheet Count", default=True,
                               description="Set the number of sheets to fit the images (2 page sides per sheet)")
    fit_size: BoolProperty(name="Fit Page Size to Images", default=True,
                           description="Give the pages the same proportions as the first image (no stretching)")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        import os
        obj = _bend_owner(context)
        mod = get_bend_mod(obj)
        if mod is None:
            self.report({"ERROR"}, "Select an NK Bend sheet first")
            return {"CANCELLED"}
        names = sorted([f.name for f in self.files if f.name], key=_natural_key)
        imgs = []
        for n in names:
            try:
                imgs.append(bpy.data.images.load(os.path.join(self.directory, n), check_existing=True))
            except Exception:
                self.report({"WARNING"}, f"Skipped {n}")
        if not imgs:
            self.report({"ERROR"}, "No images loaded")
            return {"CANCELLED"}
        ids = _socket_ids(mod.node_group)
        if self.match_sheets:
            sheets = max(1, math.ceil(len(imgs) / 2))
            set_input(mod, ids["Sheets"], sheets)
            if int(get_input(mod, ids["Flip Count"])) > sheets:
                set_input(mod, ids["Flip Count"], sheets // 2)
        sp = obj.nk_sheet
        w, h = imgs[0].size
        if self.fit_size and sp.generated and w and h:
            _SHEET_LOCK[0] = True
            try:
                sp.format = "CUSTOM"
                sp.height = sp.width * h / w
            finally:
                _SHEET_LOCK[0] = False
            rebuild_sheet(obj)
        sp.front_image = sp.back_image = None
        sp.pages.clear()
        for im in imgs:
            sp.pages.add().image = im
        apply_images(obj)
        sheets = int(get_input(mod, ids["Sheets"]))
        self.report({"INFO"}, f"{min(len(imgs), sheets * 2)} images on {sheets} sheets")
        return {"FINISHED"}


class NKBEND_OT_clear_images(bpy.types.Operator):
    bl_idname = "nk_bend.clear_images"
    bl_label = "Remove Images"
    bl_description = "Back to plain colours"
    bl_options = {"REGISTER", "UNDO"}

    side: EnumProperty(items=[("ALL", "All", ""), ("FRONT", "Front", ""), ("BACK", "Back", ""),
                              ("PAGES", "Pages", "")], default="ALL")

    def execute(self, context):
        obj = _bend_owner(context) or context.active_object
        if obj is None or obj.type != "MESH":
            return {"CANCELLED"}
        sp = obj.nk_sheet
        was_cut = sp.cut_alpha
        if self.side in {"ALL", "FRONT"}:
            sp.front_image = None
            _SHEET_LOCK[0] = True
            sp.cut_alpha = False
            _SHEET_LOCK[0] = False
        if self.side in {"ALL", "BACK"}:
            sp.back_image = None
        if self.side in {"ALL", "PAGES"} and len(sp.pages):
            _clear_pages(obj)
        if was_cut and not sp.cut_alpha:
            rebuild_sheet(obj)
        apply_images(obj)
        return {"FINISHED"}


class NKBEND_OT_contact(bpy.types.Operator):
    bl_idname = "nk_bend.contact"
    bl_label = "Contact"
    bl_description = f"Questions or ideas? Contact {AUTHOR}"

    action: EnumProperty(items=[("PHONE", "Copy Phone", ""), ("WHATSAPP", "WhatsApp", ""),
                                ("EMAIL", "Email", "")])

    def execute(self, context):
        if self.action == "PHONE":
            context.window_manager.clipboard = PHONE
            self.report({"INFO"}, f"Copied {PHONE_PRETTY}")
        elif self.action == "WHATSAPP":
            bpy.ops.wm.url_open(url=WHATSAPP)
        else:
            bpy.ops.wm.url_open(url=f"mailto:{EMAIL}?subject=NK%20Paper%20Bend")
        return {"FINISHED"}


class NKBEND_OT_refresh_pages(bpy.types.Operator):
    bl_idname = "nk_bend.refresh_pages"
    bl_label = "Refresh Pages"
    bl_description = "Rebuild the per-page materials after changing the number of sheets"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        apply_images(_bend_owner(context))
        return {"FINISHED"}


def _set_key_interp(fc, frames, st):
    for kp in fc.keyframe_points:
        if round(kp.co.x) in frames:
            if st.interpolation == "SMOOTH":
                kp.interpolation = "BEZIER"
                kp.handle_left_type = kp.handle_right_type = "AUTO_CLAMPED"
            else:
                kp.interpolation = st.interpolation
                kp.easing = st.easing
    fc.update()


class NKBEND_OT_page_step(bpy.types.Operator):
    bl_idname = "nk_bend.page_step"
    bl_label = "Turn Page"
    bl_description = ("Animate one page turn from the current frame (keys Page), then hold, "
                      "and move the playhead to the end — press again for the next page")
    bl_options = {"REGISTER", "UNDO"}

    step: IntProperty(default=1)

    def execute(self, context):
        obj = _bend_owner(context)
        mod = get_bend_mod(obj)
        if mod is None:
            return {"CANCELLED"}
        ids = _socket_ids(mod.node_group)
        if "Page" not in ids:
            self.report({"ERROR"}, "Recreate this sheet with the latest NK Bend to use pages")
            return {"CANCELLED"}
        st, scene = context.scene.nk_bend, context.scene
        set_input(mod, ids["Use Page Number"], True)
        owner, prop = _input_ref(mod, ids["Page"])
        path = owner.path_from_id(prop)
        sheets = int(get_input(mod, ids["Sheets"]))
        f0 = scene.frame_current
        f1 = f0 + st.turn_frames
        cur = float(get_input(mod, ids["Page"]))
        target = max(0, min(sheets, round(cur) + self.step))
        obj.keyframe_insert(data_path=path, frame=f0)
        set_input(mod, ids["Page"], target)
        obj.keyframe_insert(data_path=path, frame=f1)
        if st.hold > 0:
            obj.keyframe_insert(data_path=path, frame=f1 + st.hold)
        fc = _find_fcurve(obj, path)
        if fc:
            _set_key_interp(fc, {f0}, st)
        scene.frame_set(f1 + st.hold)
        obj.update_tag()
        return {"FINISHED"}


class NKBEND_OT_clear_page_keys(bpy.types.Operator):
    bl_idname = "nk_bend.clear_page_keys"
    bl_label = "Clear Page Animation"
    bl_description = "Delete all Page keyframes of this sheet"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _bend_owner(context)
        mod = get_bend_mod(obj)
        if mod is None:
            return {"CANCELLED"}
        owner, prop = _input_ref(mod, _socket_ids(mod.node_group)["Page"])
        fc = _find_fcurve(obj, owner.path_from_id(prop))
        if fc:
            while len(fc.keyframe_points):
                fc.keyframe_points.remove(fc.keyframe_points[0])
            fc.update()
        obj.update_tag()
        return {"FINISHED"}


class NKBEND_OT_spine_side(bpy.types.Operator):
    bl_idname = "nk_bend.spine_side"
    bl_label = "Spine Side"
    bl_description = "Put the spine (binding) on this edge — pages turn away from it"
    bl_options = {"REGISTER", "UNDO"}

    side: EnumProperty(items=[("LEFT", "Left", ""), ("RIGHT", "Right", ""),
                              ("TOP", "Top", ""), ("BOTTOM", "Bottom", "")])

    def execute(self, context):
        obj = _bend_owner(context)
        ctrl = get_controller(get_bend_mod(obj))
        if ctrl is None:
            return {"CANCELLED"}
        fx, fy = {"LEFT": (.5, 0), "RIGHT": (-.5, 0), "TOP": (0, -.5), "BOTTOM": (0, .5)}[self.side]
        place_controller(obj, ctrl, fx, fy)
        obj.update_tag()
        return {"FINISHED"}


class NKBendSettings(bpy.types.PropertyGroup):
    preset: EnumProperty(name="Preset", items=PRESET_ITEMS)
    duration: IntProperty(name="Frames", default=48, min=1)
    interpolation: EnumProperty(name="Curve", items=[
        ("SMOOTH", "Smooth", "Bezier ease in/out"),
        ("LINEAR", "Linear", ""),
        ("QUART", "Quart", ""),
        ("EXPO", "Expo", ""),
        ("BACK", "Back", "Overshoot"),
        ("ELASTIC", "Elastic", ""),
        ("BOUNCE", "Bounce", ""),
    ], default="SMOOTH")
    easing: EnumProperty(name="Easing", items=[
        ("EASE_IN", "In", ""), ("EASE_OUT", "Out", ""), ("EASE_IN_OUT", "In Out", ""),
    ], default="EASE_IN_OUT")
    turn_frames: IntProperty(name="Turn", default=20, min=1, description="Frames per page turn")
    hold: IntProperty(name="Hold", default=24, min=0, description="Frames to stay on the spread after a turn")
    loop: BoolProperty(name="Loop", default=False,
                       description="Linear keys + Cycles modifier: repeats forever (for ∞ presets)")


def _section(layout, idname, title, icon="NONE", closed=False):
    if hasattr(layout, "panel"):
        header, body = layout.panel(idname, default_closed=closed)
        header.label(text=title, icon=icon)
        return body
    box = layout.box()
    box.label(text=title, icon=icon)
    return box


class NKBEND_PT_main(bpy.types.Panel):
    bl_label = "NK Paper Bend"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "NK Bend"

    def draw(self, context):
        layout = self.layout
        st = context.scene.nk_bend

        body = _section(layout, "nk_new", "New Sheet", "ADD")
        if body:
            for group in PRESET_GROUPS:
                col = body.column(align=True)
                col.label(text=group)
                grid = col.grid_flow(columns=2, even_columns=True, align=True)
                for key, p in PRESETS.items():
                    if p["group"] == group:
                        grid.operator("nk_bend.add", text=p["label"], icon=p["icon"]).preset = key

        body = _section(layout, "nk_apply", "Bend Selected Mesh", "MODIFIER", closed=True)
        if body:
            row = body.row(align=True)
            row.prop(st, "preset", text="")
            row.operator("nk_bend.apply", text="Apply").preset = st.preset

        obj = _bend_owner(context)
        mod = get_bend_mod(obj)
        if mod is None:
            layout.label(text="Select an NK Bend sheet to edit it", icon="INFO")
            return
        ids = _socket_ids(mod.node_group)

        # --- sheet size
        sp = obj.nk_sheet
        sol = obj.modifiers.get(SOLID_NAME)
        body = _section(layout, "nk_sheet", f"Sheet — {obj.name}", "MESH_PLANE")
        if body:
            col = body.column(align=True)
            if sp.generated:
                col.prop(sp, "format", text="")
                row = col.row(align=True)
                row.prop(sp, "width")
                sub = row.row(align=True)
                sub.prop(sp, "height")
                col.prop(sp, "resolution")
                col.prop(sp, "corner")
            else:
                col.label(text="Your own mesh — size comes from the mesh", icon="INFO")
            if sol:
                col.prop(sol, "thickness", text="Thickness")

        # --- controller
        body = _section(layout, "nk_ctrl", "Controller", "EMPTY_SINGLE_ARROW")
        if body and "Controller" in ids:
            draw_input(body, mod, ids["Controller"], "")
            rows = [[(-.5, .5, "TL", "NONE"), (0, .5, "", "TRIA_UP"), (.5, .5, "TR", "NONE")],
                    [(-.5, 0, "", "TRIA_LEFT"), (0, 0, "", "RADIOBUT_ON"), (.5, 0, "", "TRIA_RIGHT")],
                    [(-.5, -.5, "BL", "NONE"), (0, -.5, "", "TRIA_DOWN"), (.5, -.5, "BR", "NONE")]]
            grid = body.column(align=True)
            for cells in rows:
                r = grid.row(align=True)
                for fx, fy, txt, icon in cells:
                    op = r.operator("nk_bend.place_ctrl", text=txt, icon=icon)
                    op.fx, op.fy = fx, fy
            col = body.column(align=True)
            for name in ("Auto Direction", "Snap Direction", "Follow Controller", "Direction"):
                if name in ids:
                    draw_input(col, mod, ids[name], name)
            body.operator("nk_bend.select_ctrl", icon="RESTRICT_SELECT_OFF")

        page_mode = "Use Page Number" in ids and bool(get_input(mod, ids["Use Page Number"]))
        page_turn = "Page Turn" in ids and bool(get_input(mod, ids["Page Turn"]))

        # --- pages (magazine / book / page flip)
        if page_turn and "Page Bend" in ids:
            body = _section(layout, "nk_pages", "Pages", "DOCUMENTS")
            if body:
                draw_input(body, mod, ids["Use Page Number"], "Drive by Page Number")
                if page_mode:
                    draw_input(body, mod, ids["Page"], "Page")
                    r = body.row(align=True)
                    r.scale_y = 1.4
                    r.operator("nk_bend.page_step", text="Prev", icon="TRIA_LEFT").step = -1
                    r.operator("nk_bend.page_step", text="Next Page", icon="TRIA_RIGHT").step = 1
                    r = body.row(align=True)
                    r.prop(st, "turn_frames")
                    r.prop(st, "hold")
                    body.prop(st, "interpolation", text="Curve")
                    if st.interpolation not in {"SMOOTH", "LINEAR"}:
                        body.prop(st, "easing", expand=True)
                    body.operator("nk_bend.clear_page_keys", icon="TRASH")
                col = body.column(align=True)
                col.label(text="How the page bends")
                draw_input(col, mod, ids["Page Bend"], "Bend", slider=True)
                draw_input(col, mod, ids["Bend at Edge"], "Bend at Edge", slider=True)
                draw_input(col, mod, ids["Limit End"], "Turn Angle")
                draw_input(col, mod, ids["Page Overlap" if page_mode else "Stagger"],
                           "Overlap" if page_mode else "Stagger")
                col = body.column(align=True)
                col.label(text="Spine")
                r = col.row(align=True)
                for side, icon in (("LEFT", "TRIA_LEFT"), ("TOP", "TRIA_UP"),
                                   ("BOTTOM", "TRIA_DOWN"), ("RIGHT", "TRIA_RIGHT")):
                    r.operator("nk_bend.spine_side", text="", icon=icon).side = side

        # --- animate
        body = _section(layout, "nk_anim", "Animate", "ANIM")
        if body and page_mode:
            body.label(text="Pages are driven by the Page number (see Pages)", icon="INFO")
        elif body:
            draw_input(body, mod, ids["Progress"], "Progress", slider=True)
            row = body.row(align=True)
            row.prop(st, "duration")
            row.prop(st, "loop", toggle=True, icon="FILE_REFRESH")
            if not st.loop:
                body.prop(st, "interpolation", text="Curve")
                if st.interpolation not in {"SMOOTH", "LINEAR"}:
                    body.prop(st, "easing", expand=True)
            row = body.row(align=True)
            row.operator("nk_bend.keyframe", text="Key 0 → 1", icon="PLAY").reverse = False
            row.operator("nk_bend.keyframe", text="Key 1 → 0", icon="PLAY_REVERSE").reverse = True

        # --- images (optional)
        body = _section(layout, "nk_img", "Images (optional)", "IMAGE_DATA", closed=True)
        if body:
            sheets = int(get_input(mod, ids["Sheets"])) if "Sheets" in ids else 1
            col = body.column(align=True)
            for side, img in (("FRONT", sp.front_image), ("BACK", sp.back_image)):
                r = col.row(align=True)
                r.label(text=f"{side.title()}: {img.name if img else '—'}")
                op = r.operator("nk_bend.load_image", text="", icon="FILEBROWSER")
                op.side = side
                if img:
                    r.operator("nk_bend.clear_images", text="", icon="X").side = side
            body.prop(sp, "back_mirror")
            if sp.generated and sp.front_image:
                body.prop(sp, "cut_alpha")
            if sheets > 1:
                box = body.box()
                box.label(text="Every page its own image", icon="DOCUMENTS")
                box.label(text="Select all in reading order: cover, inside, p1…")
                r = box.row(align=True)
                r.operator("nk_bend.load_pages", text="Load Page Images…", icon="FILEBROWSER")
                if len(sp.pages):
                    r.operator("nk_bend.clear_images", text="", icon="X").side = "PAGES"
                    box.label(text=f"{min(len(sp.pages), sheets * 2)} / {sheets * 2} page sides filled")
                    if len(obj.data.materials) != sheets * 3:
                        box.operator("nk_bend.refresh_pages", text="Sheets changed — Refresh Pages",
                                     icon="ERROR")
            if sp.front_image or sp.back_image or len(sp.pages):
                body.operator("nk_bend.clear_images", text="Remove All Images", icon="TRASH").side = "ALL"

        # --- about
        body = _section(layout, "nk_about", "About", "INFO", closed=True)
        if body:
            col = body.column(align=True)
            col.label(text=f"NK Paper Bend {VERSION_STR}")
            col.label(text=f"Made by {AUTHOR}", icon="USER")
            r = body.row(align=True)
            r.operator("nk_bend.contact", text=PHONE_PRETTY, icon="COPYDOWN").action = "PHONE"
            r.operator("nk_bend.contact", text="WhatsApp", icon="URL").action = "WHATSAPP"
            body.operator("nk_bend.contact", text=EMAIL, icon="URL").action = "EMAIL"


class NKBEND_PT_params(bpy.types.Panel):
    bl_label = "Parameters"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "NK Bend"
    bl_parent_id = "NKBEND_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    OPEN = {"Bend"}

    @classmethod
    def poll(cls, context):
        return _bend_owner(context) is not None

    def draw(self, context):
        mod = get_bend_mod(_bend_owner(context))
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = True
        col = None
        for it in mod.node_group.interface.items_tree:
            if getattr(it, "item_type", "") == "PANEL":
                if it.name in {"Animation", "Controller"}:
                    col = None
                    continue
                col = _section(layout, f"nk_p_{it.name}", it.name, closed=it.name not in self.OPEN)
                continue
            if col is None or it.in_out != "INPUT" or it.socket_type == "NodeSocketGeometry":
                continue
            draw_input(col, mod, it.identifier, it.name)


classes = (
    NKImageItem,
    NKSheetProps,
    NKBendSettings,
    NKBEND_OT_add,
    NKBEND_OT_apply,
    NKBEND_OT_place_ctrl,
    NKBEND_OT_select_ctrl,
    NKBEND_OT_keyframe,
    NKBEND_OT_load_image,
    NKBEND_OT_load_pages,
    NKBEND_OT_clear_images,
    NKBEND_OT_contact,
    NKBEND_OT_refresh_pages,
    NKBEND_OT_page_step,
    NKBEND_OT_clear_page_keys,
    NKBEND_OT_spine_side,
    NKBEND_PT_main,
    NKBEND_PT_params,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.nk_bend = PointerProperty(type=NKBendSettings)
    bpy.types.Object.nk_sheet = PointerProperty(type=NKSheetProps)


def unregister():
    del bpy.types.Object.nk_sheet
    del bpy.types.Scene.nk_bend
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
