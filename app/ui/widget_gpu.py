"""GPU effects renderer ("Ultra") for the JoyVoice floating pill.

Offscreen OpenGL 3.3 (moderngl standalone context, no window, no focus, nothing on any
monitor).  Two full-canvas passes produce two premultiplied RGBA8888 images that the
QPainter widget composites around its own content:

* ``under`` : glass body (real backdrop blur) + aurora liquid fill, alpha only inside the
  pill signed-distance field.
* ``over``  : travelling edge ring (arc-length parametrised along the true rounded-rect
  perimeter), ring halo, outer bloom and the paste ripple.

The look is the QPainter "Balanced" renderer's, just richer: the three parallax wave layers,
palette maths and ring profile are the same formulas, evaluated per pixel with a domain-warped
aurora curtain, depth gradient, bright meniscus lines, voice-driven caustics and dithering.

Contract (kept stable for the widget):

    fx = GpuFx()                      # raises on any failure -> caller falls back to QPainter
    fx.set_backdrop(qimage_or_None, QPoint, lum)
    under, over = fx.render(params)   # QImage.Format_RGBA8888_Premultiplied x2
    fx.release()

The context belongs to the thread that created it: call ``render`` from that same thread.
"""
from __future__ import annotations

import math
import struct

import moderngl
from PySide6.QtCore import QPoint
from PySide6.QtGui import QImage

__all__ = ["GpuFx"]


# --------------------------------------------------------------------------------------
# GLSL
# --------------------------------------------------------------------------------------

_VERT = """
#version 330
in vec2 in_pos;
void main() { gl_Position = vec4(in_pos, 0.0, 1.0); }
"""

_COMMON = """
#version 330
layout(std140) uniform U {
    vec4 u0;    // cw, ch, resx, resy
    vec4 u1;    // pill x, y, w, h
    vec4 u2;    // radius, rh, aur, ring
    vec4 u3;    // fill, fmul, amp, voice
    vec4 u4;    // swell, sw_head, sw_sig, breath
    vec4 u5;    // ring_a, flare, glow, bloom
    vec4 u6;    // ripple, ripple r, g, b (0..1)
    vec4 u7;    // glass_dark, top_a, bot_a, use_backdrop
    vec4 u8;    // sheen, glint, shade, wave clock t
    vec4 u9;    // backdrop offset px (xy), backdrop size px (zw)
    vec4 pal[6];
};
out vec4 frag;

const float PI = 3.14159265358979;
#define RESV u0.zw
#define PXS  min(u0.z, u0.w)

vec4 over(vec4 dst, vec3 col, float a) {
    a = clamp(a, 0.0, 1.0);
    return vec4(col * a + dst.rgb * (1.0 - a), a + dst.a * (1.0 - a));
}

// cyclic 6-colour palette, smoothstep-interpolated: identical to _aur_rgb
vec3 aur(float u) {
    float x = fract(u) * 6.0;
    float fi = floor(x);
    int i = int(fi);
    float f = x - fi;
    f = f * f * (3.0 - 2.0 * f);
    return mix(pal[i % 6].rgb, pal[(i + 1) % 6].rgb, f);
}

float sdRBox(vec2 p, vec2 c, vec2 hs, float r) {
    vec2 q = abs(p - c) - hs + r;
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}

// interleaved gradient noise, +-0.5 LSB dither (static: no shimmer)
float dither(vec2 fp) {
    return fract(52.9829189 * fract(dot(fp, vec2(0.06711056, 0.00583715)))) - 0.5;
}

vec4 finish(vec4 c, vec2 fp) {
    if (any(isnan(c)) || any(isinf(c))) return vec4(0.0);
    c = clamp(c, 0.0, 1.0);
    if (c.a > 0.004) {
        c = clamp(c + dither(fp) * (1.0 / 255.0), 0.0, 1.0);
    }
    c.rgb = min(c.rgb, vec3(c.a));
    return c;
}

// ---- gradient noise ------------------------------------------------------------
uvec2 pcg2d(uvec2 v) {
    v = v * 1664525u + 1013904223u;
    v.x += v.y * 1664525u; v.y += v.x * 1664525u;
    v ^= v >> 16u;
    v.x += v.y * 1664525u; v.y += v.x * 1664525u;
    v ^= v >> 16u;
    return v;
}
vec2 grad2(vec2 i) {
    uvec2 h = pcg2d(uvec2(ivec2(i) + ivec2(8192)));
    float a = float(h.x >> 8u) * (6.28318530718 / 16777216.0);
    return vec2(cos(a), sin(a));
}
float gnoise(vec2 p) {   // about -1..1
    vec2 i = floor(p);
    vec2 f = p - i;
    vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
    float a = dot(grad2(i), f);
    float b = dot(grad2(i + vec2(1.0, 0.0)), f - vec2(1.0, 0.0));
    float c = dot(grad2(i + vec2(0.0, 1.0)), f - vec2(0.0, 1.0));
    float d = dot(grad2(i + vec2(1.0, 1.0)), f - vec2(1.0, 1.0));
    return mix(mix(a, b, u.x), mix(c, d, u.x), u.y) * 1.45;
}
"""

_UNDER = _COMMON + """
uniform sampler2D uBd;

vec4 glass(vec2 P, vec2 fp) {
    vec4 c = vec4(0.0);
    bool dark = u7.x > 0.5;
    if (u7.w > 0.5) {
        vec2 uv = (fp - u9.xy) / u9.zw;
        c = vec4(texture(uBd, uv).rgb, 1.0);
    }
    float ty = clamp((P.y - u1.y) / max(u1.w, 0.01), 0.0, 1.0);
    vec3 c0 = dark ? vec3(0.078, 0.102, 0.141) : vec3(1.0);
    vec3 c1 = dark ? vec3(0.047, 0.071, 0.102) : vec3(0.953, 0.973, 0.969);
    c = over(c, mix(c0, c1, ty), mix(u7.y, u7.z, ty));
    // top sheen
    float sh = clamp(1.0 - (P.y - u1.y) / max(u1.w * 0.62, 0.01), 0.0, 1.0);
    c = over(c, vec3(1.0), 0.62 * sh * u8.x);
    // specular glint, top-left
    vec2 gc = vec2(u1.x + min(u1.z * 0.22, 44.0), u1.y + 3.0);
    float gr = max(min(u1.z * 0.5, 90.0), 1.0);
    float g = clamp(1.0 - length(P - gc) / gr, 0.0, 1.0);
    c = over(c, vec3(1.0), 0.85 * g * u8.y);
    // soft bottom shade
    float sd = clamp((u1.y + u1.w - P.y) / 16.0, 0.0, 1.0);
    c = over(c, vec3(0.118, 0.275, 0.275), 0.07 * (1.0 - sd) * u8.z);
    return c;
}

// slow aurora curtain: domain-warped fbm stretched vertically.  Returns about -1..1.
float curtain(vec2 P, float t) {
    vec2 q = vec2(P.x * 0.040, P.y * 0.016);
    vec2 w = vec2(gnoise(q * 1.5 + vec2(t * 0.09, 0.0)),
                  gnoise(q * 1.5 + vec2(5.2, 1.3 - t * 0.07)));
    q += 0.60 * w;
    float n = 0.55 * gnoise(q + vec2(t * 0.06, -t * 0.045))
            + 0.30 * gnoise(q * 2.1 + vec2(-t * 0.08, 3.1))
            + 0.15 * gnoise(q * 4.3 + vec2(1.7, t * 0.10));
    return clamp(n * 1.25, -1.0, 1.0);
}

const vec4 LS[3] = vec4[3](vec4(1.4, 150.0, 1.00, 0.26),
                            vec4(-2.2, 104.0, 0.80, 0.38),
                            vec4(3.3, 72.0, 0.62, 0.54));
const vec2 LH[3] = vec2[3](vec2(0.00, 0.11), vec2(0.33, 0.05), vec2(0.66, 0.00));

vec4 liquid(vec4 dst, vec2 P) {
    float fill = u3.x;
    float vis = smoothstep(0.0, 1.0, clamp(fill * 6.0, 0.0, 1.0));
    if (vis < 0.01) return dst;
    float fm = u3.y * vis;
    float mfm = min(1.0, fm);
    float rh = u2.y;
    float rowbot = u1.y + rh;
    float amp = u3.z, voice = clamp(u3.w, 0.0, 1.0);
    float swell = u4.x, head = u4.y, sig = max(u4.z, 1.0), breath = u4.w;
    float t = u8.w;
    float aurp = u2.z;
    float xrel = clamp((P.x - u1.x) / max(u1.z, 0.01), 0.0, 1.0);
    float hump = 0.0;
    float env = 1.0;
    if (swell > 0.01) {
        float dx = P.x - head;
        hump = exp(-(dx * dx) / (sig * sig));
        env = 1.0 - swell * 0.50 * (1.0 - hump);
    }
    float cn = curtain(P, t);                 // -1..1
    float bright = 1.0 + 0.22 * (0.5 + 0.5 * cn);   // 1.0 .. 1.22: only ever lighter than the reference
    float hueShift = 0.045 * cn;
    float px = PXS;
    vec4 lines = vec4(0.0);
    float zFront = 1e3, covFront = 0.0;

    for (int i = 0; i < 3; i++) {
        float spd = LS[i].x, wl = LS[i].y, asc = LS[i].z, alpha = LS[i].w;
        float shift = LH[i].x, lift = LH[i].y;
        float k = 2.0 * PI / wl;
        float ph1 = spd * t;
        float ph2 = -0.8 * spd * t + 1.7;
        float a_px = amp * asc;
        float base = rowbot - (fill * 1.08 + lift) * rh;
        float s1 = sin(k * P.x + ph1), s2 = sin(1.9 * k * P.x + ph2);
        float ys = base + a_px * (s1 + 0.45 * s2) - swell * rh * 0.52 * hump;
        float dys = a_px * (k * cos(k * P.x + ph1) + 0.45 * 1.9 * k * cos(1.9 * k * P.x + ph2))
                  + swell * rh * 0.52 * hump * 2.0 * (P.x - head) / (sig * sig);
        float dv = (P.y - ys) * inversesqrt(1.0 + dys * dys);   // signed distance below the surface
        float fc = clamp(dv * px + 0.5, 0.0, 1.0);
        float z = max(dv, 0.0);

        vec3 rgb = aur(aurp + shift + xrel * 0.82 + hueShift);
        rgb = clamp(rgb * bright, 0.0, 1.0);
        float lum = dot(rgb, vec3(0.299, 0.587, 0.114));
        float surf = exp(-z / 9.0);
        rgb = clamp(vec3(lum) + (rgb - lum) * (1.0 + 0.28 * surf), 0.0, 1.0);   // more saturated near the surface
        float lift_w = 0.13 * exp(-z / 4.0) + 0.20 * swell * hump * exp(-z / 12.0);
        rgb = mix(rgb, vec3(1.0), lift_w);                                       // lit meniscus zone
        float a = alpha * fm * env;
        dst = over(dst, rgb, a * fc);

        // meniscus: crisp bright line + soft glow, per surface
        float adv = abs(dv);
        float core = clamp((0.62 - adv) * px + 0.5, 0.0, 1.0);
        float gl = dv >= 0.0 ? exp(-adv / 2.1) : 0.55 * exp(-adv / 1.7);
        float shim = 0.88 + 0.22 * sin(P.x * 0.085 - t * (1.4 + 0.4 * float(i)) + 2.1 * float(i));
        float lineA = (i == 0) ? (0.10 + 0.8 * alpha) * 0.55 : (0.30 + 1.0 * alpha);
        float la = lineA * mfm * core * shim
                 + (0.07 + 0.28 * alpha) * mfm * gl * (0.75 + 0.5 * breath) * (0.8 + 0.5 * voice) * shim;
        lines = over(lines, mix(vec3(1.0), rgb, 0.10), la);
        if (i == 2) { zFront = z; covFront = fc; }
    }
    // ink-safe luminance floor: dark ink (#164a52) stays >= 4.5:1 on top of any fill colour.
    // Soft knee, lifts toward white only where the composite would be too dark.
    if (u7.x < 0.5) {
        vec3 ce = dst.rgb + (1.0 - dst.a);
        vec3 lin = pow(max(ce, vec3(0.0)), vec3(2.2));
        float L = dot(lin, vec3(0.2126, 0.7152, 0.0722));
        float F = 0.45, e = 0.05;
        float target = 0.5 * (L + F + sqrt((L - F) * (L - F) + e * e));
        float kk = clamp((target - L) / max(1.0 - L, 1e-3), 0.0, 1.0);
        ce = pow(mix(lin, vec3(1.0), kk), vec3(1.0 / 2.2));
        dst.rgb = clamp(ce - (1.0 - dst.a), 0.0, dst.a);
    }
    // voice-driven caustic shimmer inside the fill (only ever lightens)
    vec2 cq = vec2(P.x * 0.075, P.y * 0.10);
    float ca = gnoise(cq + vec2(t * 0.33, t * 0.21));
    float cb = gnoise(cq * 1.35 + vec2(-t * 0.27, t * 0.30) + 7.3);
    float rid = pow(clamp(1.0 - abs(ca + cb) * 1.35, 0.0, 1.0), 5.0);
    float cK = 0.03 + 0.20 * voice + 0.10 * swell * hump;
    float dk = u7.x > 0.5 ? 0.5 : 1.0;
    dst = over(dst, vec3(1.0), rid * cK * dk * exp(-zFront / 17.0) * covFront * mfm);
    // lines last, like the reference polylines
    dst = vec4(lines.rgb + dst.rgb * (1.0 - lines.a), lines.a + dst.a * (1.0 - lines.a));
    return dst;
}

void main() {
    vec2 fp = gl_FragCoord.xy;
    vec2 P = fp / RESV;
    vec2 hs = max(u1.zw * 0.5, vec2(0.01));
    vec2 pc = u1.xy + hs;
    float r = clamp(u2.x, 0.0, min(hs.x, hs.y));
    float d = sdRBox(P, pc, hs, r);
    float cov = clamp(0.5 - d * PXS, 0.0, 1.0);
    if (cov <= 0.0) { frag = vec4(0.0); return; }
    vec4 c = glass(P, fp);
    c = liquid(c, P);
    c *= cov;
    frag = finish(c, fp);
}
"""

_OVER = _COMMON + """
// arc length s (clockwise from the top-left start of the top edge) and distance to the
// ring centre-line, for the rounded-rect ring path inset 1.15 inside the pill.
void ringCoords(vec2 P, out float s, out float dist, out float Ptot) {
    const float inset = 1.15;
    vec2 hs = max(u1.zw * 0.5 - inset, vec2(0.01));
    float rad = clamp(max(1.0, u2.x - inset), 0.01, min(hs.x, hs.y));
    float hx = max(hs.x - rad, 0.0), hy = max(hs.y - rad, 0.0);
    float wl = 2.0 * hx, hl = 2.0 * hy, arc = 0.5 * PI * rad;
    Ptot = max(2.0 * wl + 2.0 * hl + 4.0 * arc, 1e-3);
    vec2 q = P - (u1.xy + u1.zw * 0.5);
    vec2 a = abs(q);
    vec2 e = a - vec2(hx, hy);
    dist = abs(length(max(e, 0.0)) + min(max(e.x, e.y), 0.0) - rad);
    bool rt = q.x >= 0.0, bt = q.y >= 0.0;
    if (e.x > 0.0 && e.y > 0.0) {
        if (!bt && rt)      s = wl + rad * atan(e.x, e.y);
        else if (bt && rt)  s = wl + arc + hl + rad * atan(e.y, e.x);
        else if (bt && !rt) s = 2.0 * wl + 2.0 * arc + hl + rad * atan(e.x, e.y);
        else                s = 2.0 * wl + 3.0 * arc + 2.0 * hl + rad * atan(e.y, e.x);
    } else if (e.x > e.y) {
        if (rt) s = wl + arc + clamp(q.y + hy, 0.0, hl);
        else    s = 2.0 * wl + 3.0 * arc + hl + clamp(hy - q.y, 0.0, hl);
    } else {
        if (!bt) s = clamp(hx + q.x, 0.0, wl);
        else     s = wl + 2.0 * arc + hl + clamp(hx - q.x, 0.0, wl);
    }
}

float edgeFade(vec2 P) {
    float dx = min(P.x, u0.x - P.x), dy = min(P.y, u0.y - P.y);
    return smoothstep(0.0, 6.0, dx) * smoothstep(0.0, 5.0, dy);
}

void main() {
    vec2 fp = gl_FragCoord.xy;
    vec2 P = fp / RESV;
    float px = PXS;
    float A = u5.x, flare = u5.y, glow = u5.z;
    bool bloom = u5.w > 0.5;
    float voice = clamp(u3.w, 0.0, 1.0);
    vec2 hs = max(u1.zw * 0.5, vec2(0.01));
    vec2 pc = u1.xy + hs;
    float r = clamp(u2.x, 0.0, min(hs.x, hs.y));
    float dp = sdRBox(P, pc, hs, r);
    float ef = edgeFade(P);
    vec4 c = vec4(0.0);

    float s, dist, Pt;
    ringCoords(P, s, dist, Pt);
    float aurp = u2.z;

    // ring colour / profile at the nearest perimeter point
    float h1 = u2.w * Pt, h2 = h1 + 0.5 * Pt, half_ = 0.5 * Pt;
    float d1 = mod(s - h1 + half_, Pt) - half_;
    float d2 = mod(s - h2 + half_, Pt) - half_;
    float pr  = d1 < 0.0 ? exp(d1 / (0.26 * Pt)) : exp(-pow(d1 / (0.035 * Pt), 2.0));
    float pr2 = 0.55 * (d2 < 0.0 ? exp(d2 / (0.26 * Pt)) : exp(-pow(d2 / (0.035 * Pt), 2.0)));
    float prof = min(pr + pr2, 1.0);
    float base = 0.44 + 0.26 * voice + 0.30 * flare;
    float sa = A * (base + (1.0 - base) * prof);
    vec3 rgb0 = aur(s / Pt + aurp);
    vec3 rgbW = mix(rgb0, vec3(1.0), clamp(0.50 * prof + 0.30 * flare, 0.0, 1.0));

    // soft coloured bloom spilling mostly outside the pill (Ultra)
    if (bloom && A > 0.004) {
        float win = 1.0 - smoothstep(10.0, 17.0, dist);
        float g = exp(-pow(dist / 6.2, 2.0)) * win;
        float outw = mix(0.38, 1.0, smoothstep(-2.0, 2.5, dp));
        float ba = A * (0.05 + 0.24 * prof + 0.16 * flare) * (0.80 + 0.60 * voice)
                 * g * outw * ef * clamp(0.6 + 0.4 * glow, 0.0, 1.6);
        c = over(c, mix(rgb0, vec3(1.0), 0.25 * prof), ba);
    }

    // paste ripple: one expanding rounded rect leaving the pill
    float q = u6.x;
    if (q > 0.0 && q < 1.0) {
        float spread = 3.0 + 15.0 * (1.0 - exp2(-10.0 * q));
        float ra = 0.62 * pow(1.0 - q, 1.6);
        float dd = abs(dp - spread);
        vec3 rc = u6.yzw;
        float wide = 1.0 - smoothstep(0.0, 3.4, dd);
        c = over(c, rc, ra * 0.30 * wide * ef);
        if (bloom) c = over(c, rc, ra * 0.20 * exp(-pow(dd / 6.0, 2.0)) * ef);
        float core = clamp((0.8 - dd) * px + 0.5, 0.0, 1.0);
        c = over(c, mix(rc, vec3(1.0), 0.25), ra * core);
    }

    if (A > 0.004) {
        // ring halo (soft), then the crisp 2.4 px core
        if (glow > 0.0) {
            float hl = 1.0 - smoothstep(0.4, 4.6, dist);
            c = over(c, rgbW, min(1.0, 0.19 * glow) * sa * hl * (0.65 + 0.35 * prof) * ef);
        }
        float core = clamp((1.2 - dist) * px + 0.5, 0.0, 1.0);
        c = over(c, rgbW, sa * core);
    }
    frag = finish(c, fp);
}
"""

# ---- backdrop blur passes (run only when the backdrop or blur size changes) ----------

_BLUR_DOWN = """
#version 330
uniform sampler2D tex;
uniform vec2 outSize;
uniform float lod;
out vec4 frag;
void main() {
    vec2 uv = gl_FragCoord.xy / outSize;
    frag = vec4(textureLod(tex, uv, lod).bgr, 1.0);   // QImage RGB32 is BGRA in memory
}
"""

_BLUR_AXIS = """
#version 330
uniform sampler2D tex;
uniform vec2 outSize;
uniform vec2 dir;       // (1,0) or (0,1)
uniform float sigma;
uniform int taps;
uniform float sat;
out vec4 frag;
void main() {
    vec2 uv = gl_FragCoord.xy / outSize;
    vec2 step_ = dir / outSize;
    vec3 acc = vec3(0.0);
    float wsum = 0.0;
    for (int i = -taps; i <= taps; i++) {
        float w = exp(-0.5 * float(i * i) / (sigma * sigma));
        acc += texture(tex, uv + step_ * float(i)).rgb * w;
        wsum += w;
    }
    acc /= wsum;
    float g = (acc.r + acc.g + acc.b) / 3.0;
    acc = clamp(vec3(g) + (acc - g) * sat, 0.0, 1.0);
    frag = vec4(acc, 1.0);
}
"""


def _pack(vals) -> bytes:
    return struct.pack("%df" % len(vals), *vals)


class GpuFx:
    """Offscreen GPU renderer for the pill's under/over layers.  See the module docstring."""

    _SIGMA_PER_RADIUS = 13.5 / 22.0     # CPU 3x box blur at radius 22 is about sigma 13.5 px

    def __init__(self) -> None:
        self.ctx = None
        self._released = False
        self.renderer = ""
        ctx = moderngl.create_standalone_context()
        self.ctx = ctx
        try:
            self.renderer = str(ctx.info.get("GL_RENDERER", ""))
            self._build()
        except Exception:
            self.release()
            raise

    # -- construction ----------------------------------------------------------------

    def _build(self) -> None:
        ctx = self.ctx
        self._vbo = ctx.buffer(struct.pack("8f", -1, -1, 1, -1, -1, 1, 1, 1))
        self._prog_under = ctx.program(vertex_shader=_VERT, fragment_shader=_UNDER)
        self._prog_over = ctx.program(vertex_shader=_VERT, fragment_shader=_OVER)
        self._prog_down = ctx.program(vertex_shader=_VERT, fragment_shader=_BLUR_DOWN)
        self._prog_axis = ctx.program(vertex_shader=_VERT, fragment_shader=_BLUR_AXIS)
        self._vao = {}
        for name in ("under", "over", "down", "axis"):
            prog = getattr(self, "_prog_" + name)
            self._vao[name] = ctx.vertex_array(prog, [(self._vbo, "2f", "in_pos")])
        self._ubo = ctx.buffer(reserve=16 * 16, dynamic=True)
        for prog in (self._prog_under, self._prog_over):
            prog["U"].binding = 0
        self._prog_under["uBd"].value = 0
        self._prog_down["tex"].value = 0
        self._prog_axis["tex"].value = 0
        # 1x1 fallback so the sampler is always complete
        self._dummy = ctx.texture((1, 1), 4, b"\xff\xff\xff\xff")
        # output targets (recreated when the pixel size changes)
        self._size = None
        self._rb_under = self._rb_over = None
        self._fbo_under = self._fbo_over = None
        self._bufs = None
        self._flip = 0
        # backdrop state
        self._bd_raw = None
        self._bd_size = (0, 0)
        self._bd_off = (0.0, 0.0)
        self._bd_serial = 0
        self._bd_key = None
        self._blur_tex = None        # final blurred texture (full backdrop area, reduced res)
        self._blur_work = None       # (size, texA, fboA, texB, fboB, texC, fboC)
        self.lum = 0.7

    # -- backdrop ----------------------------------------------------------------------

    def set_backdrop(self, img, offset, lum: float) -> None:
        """Upload the raw (unblurred) desktop pixels behind the canvas; None clears it."""
        self._check_alive()
        if img is None or img.isNull():
            self._drop_backdrop()
            return
        with self.ctx:
            fmt = img.format()
            if fmt not in (QImage.Format_RGB32, QImage.Format_ARGB32, QImage.Format_ARGB32_Premultiplied):
                img = img.convertToFormat(QImage.Format_RGB32)
            w, h = img.width(), img.height()
            if w < 1 or h < 1:
                self._drop_backdrop()
                return
            bpl = img.bytesPerLine()
            raw = bytes(img.constBits())
            if bpl != w * 4:
                raw = b"".join(raw[i * bpl:i * bpl + w * 4] for i in range(h))
            if self._bd_raw is None or self._bd_size != (w, h):
                if self._bd_raw is not None:
                    self._bd_raw.release()
                tex = self.ctx.texture((w, h), 4, raw)
                tex.repeat_x = False
                tex.repeat_y = False
                tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
                self._bd_raw = tex
                self._bd_size = (w, h)
            else:
                self._bd_raw.write(raw)
            self._bd_raw.build_mipmaps()
            self._bd_off = (float(offset.x()), float(offset.y()))
            self._bd_serial += 1
            self.lum = float(lum)

    def _drop_backdrop(self) -> None:
        if self._bd_raw is not None:
            with self.ctx:
                self._bd_raw.release()
            self._bd_raw = None
        self._bd_key = None
        self._bd_serial += 1

    def _ensure_blur(self, res: float, radius: float) -> bool:
        """(Re)compute the blurred backdrop texture when the backdrop or blur size changed."""
        ctx = self.ctx
        w, h = self._bd_size
        sigma_px = max(0.5, self._SIGMA_PER_RADIUS * radius * res)
        f = 1
        while sigma_px / f > 5.0 and f < 16:
            f *= 2
        sigma_low = max(0.5, sigma_px / f)
        lw, lh = max(1, -(-w // f)), max(1, -(-h // f))
        key = (self._bd_serial, lw, lh, round(sigma_low, 2), f)
        if key == self._bd_key and self._blur_tex is not None:
            return True
        if self._blur_work is None or self._blur_work[0] != (lw, lh):
            self._free_blur_work()
            texs, fbos = [], []
            for _ in range(3):
                t = ctx.texture((lw, lh), 4)
                t.repeat_x = False
                t.repeat_y = False
                t.filter = (moderngl.LINEAR, moderngl.LINEAR)
                texs.append(t)
                fbos.append(ctx.framebuffer(color_attachments=[t]))
            self._blur_work = ((lw, lh), texs, fbos)
        _, texs, fbos = self._blur_work
        ctx.disable(moderngl.BLEND)
        # 1: box-downsample the raw backdrop (mip chain) into the low-res texture
        fbos[0].use()
        fbos[0].viewport = (0, 0, lw, lh)
        self._bd_raw.use(0)
        self._prog_down["outSize"].value = (float(lw), float(lh))
        self._prog_down["lod"].value = float(math.log2(f)) if f > 1 else 0.0
        self._vao["down"].render(moderngl.TRIANGLE_STRIP)
        # 2: horizontal gaussian, 3: vertical gaussian + saturation 1.3
        taps = int(min(24, math.ceil(3.0 * sigma_low)))
        for src, dst, d, sat in ((texs[0], fbos[1], (1.0, 0.0), 1.0), (texs[1], fbos[2], (0.0, 1.0), 1.3)):
            dst.use()
            dst.viewport = (0, 0, lw, lh)
            src.use(0)
            pa = self._prog_axis
            pa["outSize"].value = (float(lw), float(lh))
            pa["dir"].value = d
            pa["sigma"].value = float(sigma_low)
            pa["taps"].value = taps
            pa["sat"].value = float(sat)
            self._vao["axis"].render(moderngl.TRIANGLE_STRIP)
        self._blur_tex = texs[2]
        self._bd_key = key
        return True

    def _free_blur_work(self) -> None:
        if self._blur_work is not None:
            _, texs, fbos = self._blur_work
            for o in fbos + texs:
                try:
                    o.release()
                except Exception:
                    pass
            self._blur_work = None
            self._blur_tex = None
            self._bd_key = None

    # -- render ------------------------------------------------------------------------

    def _ensure_targets(self, W: int, H: int) -> None:
        if self._size == (W, H):
            return
        ctx = self.ctx
        for o in (self._fbo_under, self._fbo_over, self._rb_under, self._rb_over):
            if o is not None:
                o.release()
        self._rb_under = ctx.renderbuffer((W, H), 4)
        self._rb_over = ctx.renderbuffer((W, H), 4)
        self._fbo_under = ctx.framebuffer(color_attachments=[self._rb_under])
        self._fbo_over = ctx.framebuffer(color_attachments=[self._rb_over])
        n = W * H * 4
        self._bufs = [(bytearray(n), bytearray(n)) for _ in range(2)]
        self._size = (W, H)

    def render(self, p: dict):
        self._check_alive()
        g = p.get
        cw, ch = float(g("cw", 408.0)), float(g("ch", 110.0))
        res = float(g("res", 1.0))
        W, H = max(1, int(round(cw * res))), max(1, int(round(ch * res)))
        if W > 8192 or H > 8192:
            raise ValueError("canvas too large")
        resx, resy = W / max(cw, 1e-3), H / max(ch, 1e-3)

        pw, ph = max(float(g("w", 64.0)), 0.5), max(float(g("h", 40.0)), 0.5)
        radius = float(g("radius", ph / 2.0))
        pal = g("pal")
        if not pal or len(pal) < 6:
            pal = ((61, 214, 150), (34, 196, 230), (138, 124, 255),
                   (255, 184, 140), (138, 124, 255), (34, 196, 230))
        palv = []
        for i in range(6):
            c = pal[i]
            palv += [c[0] / 255.0, c[1] / 255.0, c[2] / 255.0, 1.0]

        lum = float(g("lum", self.lum))
        dark_bd = max(0.0, min(1.0, 1.0 - lum))
        glass_dark = bool(g("glass_dark", False))
        tint = max(0.0, min(1.0, float(g("tint", 0.5))))
        tk = tint * 2.0
        use_bd = bool(g("blur", True)) and self._bd_raw is not None
        if use_bd:
            if glass_dark:
                top_a, bot_a = 0.56 + 0.18 * lum, 0.62 + 0.18 * lum
            else:
                top_a, bot_a = 0.56 + 0.26 * dark_bd, 0.44 + 0.28 * dark_bd
        else:
            if glass_dark:
                top_a, bot_a = 0.90, 0.93
            else:
                top_a, bot_a = 0.91, 0.84
        top_a, bot_a = min(1.0, top_a * tk), min(1.0, bot_a * tk)
        sheen_k, glint_k, shade_k = (0.30, 0.40, 2.2) if glass_dark else (1.0, 1.0, 1.0)

        rip = g("ripple", -1.0)
        rip = -1.0 if rip is None else float(rip)
        rr = g("ripple_rgb") or (24, 200, 112)

        ring_phase = float(g("ring", 0.0))
        ring_phase -= math.floor(ring_phase)
        aur = float(g("aur", 0.0))

        floats = [
            cw, ch, resx, resy,
            float(g("x", 0.0)), float(g("y", 0.0)), pw, ph,
            radius, float(g("rh", ph)), aur, ring_phase,
            float(g("fill", 0.0)), float(g("fmul", 1.0)), float(g("amp", 2.0)), float(g("voice", 0.0)),
            float(g("swell", 0.0)), float(g("sw_head", 0.0)), float(g("sw_sig", 60.0)), float(g("breath", 0.5)),
            float(g("ring_a", 0.0)), float(g("flare", 0.0)), float(g("glow", 1.0)), 1.0 if g("bloom", False) else 0.0,
            rip, rr[0] / 255.0, rr[1] / 255.0, rr[2] / 255.0,
            1.0 if glass_dark else 0.0, top_a, bot_a, 1.0 if use_bd else 0.0,
            sheen_k, glint_k, shade_k, float(g("t", 0.0)),
            self._bd_off[0], self._bd_off[1], float(self._bd_size[0]), float(self._bd_size[1]),
        ] + palv

        with self.ctx:
            ctx = self.ctx
            self._ensure_targets(W, H)
            blur_ok = False
            if use_bd:
                blur_ok = self._ensure_blur(res, float(g("blur_radius", 22.0)))
            ctx.disable(moderngl.BLEND)
            self._ubo.write(_pack(floats))
            self._ubo.bind_to_uniform_block(0)

            self._fbo_under.use()
            self._fbo_under.viewport = (0, 0, W, H)
            if blur_ok:
                self._blur_tex.use(0)
            else:
                self._dummy.use(0)
            self._vao["under"].render(moderngl.TRIANGLE_STRIP)

            self._fbo_over.use()
            self._fbo_over.viewport = (0, 0, W, H)
            self._vao["over"].render(moderngl.TRIANGLE_STRIP)

            self._flip ^= 1
            bu, bo = self._bufs[self._flip]
            self._fbo_under.read_into(bu, components=4, alignment=1)
            self._fbo_over.read_into(bo, components=4, alignment=1)

        fmt = QImage.Format_RGBA8888_Premultiplied
        return QImage(bu, W, H, W * 4, fmt), QImage(bo, W, H, W * 4, fmt)

    # -- lifetime ----------------------------------------------------------------------

    def _check_alive(self) -> None:
        if self._released or self.ctx is None:
            raise RuntimeError("GpuFx has been released")

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        ctx = self.ctx
        if ctx is None:
            return
        try:
            with ctx:
                self._free_blur_work()
                objs = [getattr(self, n, None) for n in (
                    "_fbo_under", "_fbo_over", "_rb_under", "_rb_over", "_bd_raw", "_dummy", "_ubo",
                    "_vbo", "_prog_under", "_prog_over", "_prog_down", "_prog_axis")]
                objs += list(getattr(self, "_vao", {}).values())
                for o in objs:
                    if o is not None:
                        try:
                            o.release()
                        except Exception:
                            pass
        except Exception:
            pass
        try:
            ctx.release()
        except Exception:
            pass
        self.ctx = None
        self._bufs = None

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    def __del__(self) -> None:           # a pill destroyed without an explicit release
        try:
            self.release()
        except Exception:
            pass
