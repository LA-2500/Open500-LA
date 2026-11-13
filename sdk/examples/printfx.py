#!/usr/bin/env python3
"""PRINT FX -> PRINTFX.WAV: render a pad through an effect, preview it, keep it as ONE new sample (2026-11-06; was REVERB).

    python sdk/examples/printfx.py         -> builds PRINTFX.WAV and tests it in the emulator

On the MPC500: copy PRINTFX.WAV to the CF card (instead of REVERB.WAV: same add-on, the HUB name stays "reverb1"), LOAD
it after HUB.WAV; MODE + pad 9 -> Pad FX -> PRINT FX (or MODE -> OTHER -> "Print FX"). Pages:
  PRINT FX  "REVERB" / "DO IT = SELECT"      the effect (wheel: REVERB, EQ); DO IT opens its page, CANCEL there = back
  REVERB    "VINT PLATE   A05" / "MIX 40%     MONO"   as the old REVERB.WAV: preset . pad / % / PREV; mix . MONO/STEREO
  EQ        "EQ  DEFAULT     " / "PARAMS >     A05"   preset (DEFAULT = flat); DO IT on "PARAMS >" opens EQ PARAMS,
                                                       DO IT elsewhere renders / keeps (as REVERB)
  EQ PARAMS "LOW   100Hz  A05" / "GAIN  +0dB      "   band (wheel on the name: LOW / MID / HIGH), its frequency, gain;
            "MID    1kHz  A05" / "GAIN  +0dB Q 0.7"   MID also has Q. CANCEL = back to EQ. DO IT / pads as on EQ.
Flow (REVERB, EQ, EQ PARAMS): hit a pad (plays as usual) -> DO IT renders a PREVIEW (the pad plays it at its velocity,
  127 with FULL LEVEL on) -> changing any setting re-renders it by itself -> DO IT again keeps it: the result replaces
  the pad's layers (layer 1, level of the loudest layer, range 0..127, tune 0 because the tunings are rendered in; layers
  2-4 cleared); the originals stay in memory. CANCEL: drops the preview. Another pad during the preview: drops it, that
  pad is chosen. Switching between REVERB and EQ drops a preview too.
Sound: every layer of the pad mixed to mono at its tuning and relative level (as S500), then
  REVERB: a Dattorro-style plate/hall tank (class DSP below), normalized; tail = the preset's decay time.
  EQ (mono, same length): low shelf 50/100/200 Hz -> mid bell 400 Hz..5 kHz, Q 0.4..2.0 -> high shelf 8/10/14 kHz, each
    -12..+12 dB. Shelves: first-order (smooth, no bump), x + (G-1)*LP(x) / x + (G-1)*HP(x); mid: x + (G-1)*BP(x) with
    a 0 dB-peak band-pass biquad (Q28, 64-bit sums). 0 dB = that band bypassed exactly (DEFAULT = the dry sound).
    Level: as the original; lowered only if a boost would clip.
Implementation: the reverb is described ONCE (class DSP) and generates both the SH-3 kernel and the bit-exact Python
  model; the EQ kernel is written by hand next to its model (eq_model). Work memory (dry mix + 64K-word ring for the
  reverb lines) lives inside the new sample block (last block of the list) and is cut off when you keep the result.

OS addresses (MPC500 OS 1.31): see s500.py (program/pads/samples/Copy/Delete/LOOP_GUARD/find by name) and
  audition player 0x8848CD78, play 0x884C2636(player, block, 0, velocity), stop 0x884C29D2(player) (Sample FX, HW).
  Page focus = page object +0x24, SET_FOCUS (pages.py), OS_PUSH_PAGE (mpcmod.py), as master.py (HW).
"""
import sys, os, struct, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import PageAddon, ld, st, sti, SET_FOCUS
from mpcmod import EVQ, OS_PUSH_PAGE, LOAD_SCRATCH, SCRATCH as mpcmod_SCRATCH

PROG_BASE, PROG_IDX, PROG_STRIDE = 0x8837B5AC, 0x8847C0E2, 0x1F98
PAD_OFF, PAD_SIZE, LAYER = 0x18, 0xA4, 0x18
BANKOBJ = 0x8849C98C
MGR, COPY_CMD, DELETE_CMD, LOOP_GUARD, FINDNAME = 0x8848ED30, 0x884C5FA0, 0x884C5FFC, 0x884C4F04, 0x884C5090
PLAYER, AUD_PLAY, AUD_STOP = 0x8848CD78, 0x884C2636, 0x884C29D2
FS = 44100
RING = 0x10000                       # words
CHUNK_MIX, CHUNK_CLR, CHUNK_REV, CHUNK_EQ = 2048, 4096, 256, 2048
CACHE_SPAN = 0x3000                  # kernels (checked at build: __cend - __cstart)
GUARD = 256                          # the OS keeps 256 spare frames per channel after the audio (LOOP_GUARD writes there, see trim.py)
DEBOUNCE = 48
SEMI12 = [round(2 ** 24 * 2 ** (n / 12)) for n in range(12)]    # 2026-11-06 (size): one octave, Q24
def semi(x):
    """2^((x - 60) / 12) in Q16 for x = 0..120 (as the old 121-entry table, within 1 LSB): SEMI12[x % 12] >> (13 - x // 12)"""
    return SEMI12[x % 12] >> (13 - x // 12)
CENT = [round(65536 * 2 ** (r / 1200)) for r in range(100)]
CENTI = [round(65536 * 2 ** (-r / 1200)) for r in range(100)]
def s32(v): v &= 0xFFFFFFFF; return v - (1 << 32) if v & 0x80000000 else v
def clamp16(v): return -32768 if v < -32768 else 32767 if v > 32767 else v

# ============================================ the reverb, described once ============================================
class DSP:
    """ops on 32-bit variables + one ring memory addressed relative to a pointer P that moves back one word per sample"""
    def __init__(self): self.ops = []; self.state = []; self.params = []
    def v(self, n):
        if n not in self.state and n not in self.params: self.state.append(n)
        return n
    def p(self, n):
        if n not in self.params: self.params.append(n)
        return n
    def op(self, *o): self.ops.append(o)
    # primitives
    def rd(self, d, off): self.op('rd', self.v(d), self.p(off))
    def wr(self, s, off): self.op('wr', s, self.p(off))
    def rdi(self, d, off, mod): self.op('rdi', self.v(d), self.p(off), mod)
    def mul(self, d, s, c, sh=15): self.op('mul', self.v(d), s, self.p(c), sh)
    def add(self, d, a, b): self.op('add', self.v(d), a, b)
    def sub(self, d, a, b): self.op('sub', self.v(d), a, b)
    def lfo(self, d, ph, inc, depth): self.op('lfo', self.v(d), self.v(ph), self.p(inc), self.p(depth))
    def sh2(self, d1, d2, s1, s2, acc, step): self.op('sh2', self.v(d1), self.v(d2), s1, s2, self.v(acc), self.p(step))
    def quant(self, d, s, mask, rnd): self.op('quant', self.v(d), s, self.p(mask), self.p(rnd))
    # building blocks
    def onepole(self, z, x, c):          # z += (x - z) * c
        self.sub('t', x, self.v(z)); self.mul('t', 't', c); self.add(z, z, 't')
    def ap(self, x, line, g):            # Schroeder allpass on 'x' with the delay line 'line'
        self.rd('dl', line + '_r'); self.mul('t', 'dl', g); self.add('w', x, 't'); self.wr('w', line + '_w')
        self.mul('t', 'w', g); self.sub(x, 'dl', 't')
    def ap_mod(self, x, line, g, mod):
        self.rdi('dl', line + '_r', mod); self.mul('t', 'dl', g); self.add('w', x, 't'); self.wr('w', line + '_w')
        self.mul('t', 'w', g); self.sub(x, 'dl', 't')

D = DSP()
D.v('in')
D.mul('x', 'in', 'ig')
D.wr('x', 'pre_w'); D.rd('x', 'pre_r')
D.onepole('bwz', 'x', 'cbw'); D.add('x', 'bwz', 'zero')
for k, g in ((1, 'gdi1'), (2, 'gdi1'), (3, 'gdi2'), (4, 'gdi2')): D.ap('x', 'd%d' % k, g)
for me, other, ph, mod, damp in (('a', 'b', 'phA', 'modA', 'dA'), ('b', 'a', 'phB', 'modB', 'dB')):
    D.rd('t', other + '3_r'); D.mul('t', 't', 'dec'); D.add(me, 'x', 't')
    D.lfo(mod, ph, 'lfo_inc', 'lfo_depth')
    D.ap_mod(me, me + 'm', 'gdec1', mod)
    D.wr(me, me + '1_w'); D.rd(me, me + '1_r')
    D.onepole(damp, me, 'cdamp'); D.mul(me, damp, 'dec')
    D.ap(me, me + '2', 'gdec2')
    D.wr(me, me + '3_w')
TAPS = {'L': [('a1', 266, 1), ('a1', 2974, 1), ('a2', 1913, -1), ('a3', 1996, 1), ('b1', 1990, -1), ('b2', 187, -1), ('b3', 1066, -1)],
        'R': [('b1', 353, 1), ('b1', 3627, 1), ('b2', 1228, -1), ('b3', 2673, 1), ('a1', 2111, -1), ('a2', 335, -1), ('a3', 121, -1)]}
for ch in 'LR':
    acc = 'w' + ch
    for k, (line, _, sign) in enumerate(TAPS[ch]):
        D.rd('t', 'tap%s%d' % (ch, k))
        if k == 0: D.add(acc, 't', 'zero') if sign > 0 else D.sub(acc, 'zero', 't')
        else: D.add(acc, acc, 't') if sign > 0 else D.sub(acc, acc, 't')
    D.mul(acc, acc, 'wg')
    D.onepole('h' + ch, acc, 'chp'); D.sub(acc, acc, 'h' + ch)                # low cut
    D.onepole('l' + ch, acc, 'clp'); D.add(acc, 'l' + ch, 'zero')              # high cut
D.sh2('wL', 'wR', 'wL', 'wR', 'gacc', 'gstep')                                 # grain: sample & hold
D.quant('wL', 'wL', 'gmask', 'grnd'); D.quant('wR', 'wR', 'gmask', 'grnd')
D.v('zero')
# loop / output variables (not touched by the ops)
LOOPV = ['P', 'ring', 'dptr', 'dcnt', 'outL', 'outR', 'n', 'pass', 'pk', 'G', 'mixq', 'stereo']
STATE = D.state + [x for x in LOOPV if x not in D.state]
VARS = STATE + D.params
IDX = {n: i for i, n in enumerate(VARS)}
NV = len(VARS)
assert NV <= 112, NV
PBASE = len(STATE)

# ---- presets: (name, size, RT60 s, damping, input bandwidth, pre-delay ms, diffusion, mod depth (samples) & rate,
#               low cut Hz, high cut Hz, grain rate (0 = off), wet gain, default mix %) ----
PRESETS = [
    # user 2026-11-04: ASR HALL first (and the default) = "in the style of" the Ensoniq ASR-10 halls: dense, dark,
    # modulated tail, sample & hold + 12-bit grain on the wet only. SOFT CLOUD removed.
    ("ASR HALL",   1.15, 2.00, 0.65, 0.55, 18, 1.00, 14, 0.5, 160, 5500, 31250, 0.70, 35),
    ("WARM ROOM", 0.45, 0.60, 0.55, 0.60,  8, 1.00,  6, 0.7, 180, 7000,     0, 0.85, 40),
    ("AMBIENCE",   0.30, 0.35, 0.40, 0.80,  2, 1.00,  3, 0.9, 200, 9000,     0, 0.85, 45),
    ("SNARE PLT",  0.80, 1.20, 0.35, 0.85, 12, 1.00,  8, 0.9, 220, 9500,     0, 0.75, 40),
    ("VINT PLATE", 0.85, 1.40, 0.55, 0.65, 10, 1.00, 10, 0.6, 200, 6500, 31250, 0.75, 40),
    ("DUSTY ROOM", 0.50, 0.80, 0.60, 0.55,  6, 0.95,  6, 0.7, 250, 5500, 26040, 0.85, 40),
    ("CHAMBER",    0.70, 1.50, 0.50, 0.70, 15, 0.92,  8, 0.5, 180, 7500,     0, 0.75, 35),
    ("90S HALL",   1.10, 1.80, 0.50, 0.70, 20, 1.00, 12, 0.5, 160, 7000, 39062, 0.70, 35),
    ("DARK HALL",  1.35, 2.50, 0.70, 0.50, 25, 1.00, 14, 0.4, 150, 4500,     0, 0.70, 30),
    ("STUDIO RM",  0.55, 0.80, 0.30, 0.90,  5, 1.00,  4, 0.8, 200, 10000,    0, 0.85, 40),
]
DROP = ["DARK HALL", "CHAMBER", "DUSTY ROOM"]   # user 2026-11-06: "if needed delete dark hall, chamber, dusty room" (size: needed)
PRESETS = [p for p in PRESETS if p[0] not in DROP]
DEFP = 0                             # default preset: ASR HALL
NAMES = [p[0] for p in PRESETS]
LINES = [('d1', 142), ('d2', 107), ('d3', 379), ('d4', 277), ('am', 672), ('a1', 4453), ('a2', 1800), ('a3', 3720),
         ('bm', 908), ('b1', 4217), ('b2', 2656), ('b3', 3163)]
WET_BOOST = 2.0                      # user 2026-11-03: 1x not pronounced enough, 3x "a little much" -> 2x (+6 dB)
MIXK = round(8192 * WET_BOOST)       # mixq = MIX * MIXK / 100, applied as (wet * mixq) >> 13 after the wet path (no clipping inside)
def q15(x): return max(-32768, min(32767, round(x * 32768)))
def onepole_c(f): return q15(1 - math.exp(-2 * math.pi * f / FS))
def preset_params(p):
    name, size, rt, damp, bw, pd_ms, diff, depth, rate, hp, lp, grain, wg, _ = p
    f = FS / 29761.0 * size; fi = FS / 29761.0
    vals = {'zero': 0}; cur = 0; lens = {}
    pd = max(1, round(pd_ms * FS / 1000))
    vals['pre_w'], vals['pre_r'] = cur, cur + pd; cur += pd + 2
    dep = max(1, round(depth * size))
    for nm, ln in LINES:
        L = round(ln * (fi if nm[0] == 'd' else f)); lens[nm] = L
        vals[nm + '_w'], vals[nm + '_r'] = cur, cur + L
        cur += L + (dep + 3 if nm in ('am', 'bm') else 2)
    assert cur < RING - 8, (name, cur)
    for ch in 'LR':
        for k, (line, tap, _) in enumerate(TAPS[ch]):
            vals['tap%s%d' % (ch, k)] = vals[line + '_w'] + min(lens[line] - 1, round(tap * f))
    loop = sum(lens[n] for n in ('am', 'a1', 'a2', 'a3', 'bm', 'b1', 'b2', 'b3')) / FS
    vals['dec'] = q15(min(0.92, 10 ** (-1.5 * loop / rt)))
    vals['ig'] = q15(0.5); vals['cbw'] = q15(bw); vals['cdamp'] = q15(1 - damp)
    vals['gdi1'] = q15(0.75 * diff); vals['gdi2'] = q15(0.625 * diff); vals['gdec1'] = q15(0.70); vals['gdec2'] = q15(0.50)
    vals['lfo_inc'] = round(rate / FS * 2 ** 32); vals['lfo_depth'] = dep
    vals['wg'] = q15(wg); vals['chp'] = onepole_c(hp); vals['clp'] = onepole_c(lp)
    if grain: vals['gstep'], vals['gmask'], vals['grnd'] = round(grain / FS * 65536), 0xFFFFFFF0, 8
    else: vals['gstep'], vals['gmask'], vals['grnd'] = 0x10000, 0xFFFFFFFF, 0
    return [vals[n] & 0xFFFFFFFF for n in D.params], pd
PTAB, PDS = [], []
for p in PRESETS: row, pd = preset_params(p); PTAB.append(row); PDS.append(pd)
TAILS = [round(p[2] * FS) + PDS[i] for i, p in enumerate(PRESETS)]
MIXDEF = [p[13] for p in PRESETS]
TMAX = max(TAILS)

def model_render(dry, preset, mix, stereo, pass_, G=0x10000, limit=None):
    """bit-exact model of the reverb kernel: returns (peak, L, R)"""
    var = {n: 0 for n in VARS}
    for n, v in zip(D.params, PTAB[preset]): var[n] = s32(v)
    var['phB'] = s32(0x80000000); var['mixq'] = mix * MIXK // 100
    ring = [0] * RING; P = 0; nout = len(dry) + TAILS[preset]
    if limit: nout = min(nout, limit)
    L, R, pk = [], [], 0
    for i in range(nout):
        var['in'] = dry[i] if i < len(dry) else 0
        for o in D.ops:
            k = o[0]
            if k == 'rd': var[o[1]] = ring[(P + var[o[2]]) & 0xFFFF]
            elif k == 'wr': ring[(P + var[o[2]]) & 0xFFFF] = clamp16(var[o[1]])
            elif k == 'rdi':
                m_ = var[o[3]] & 0xFFFFFFFF; ip, fr = m_ >> 16, (m_ & 0xFFFF) >> 1; base = var[o[2]] + ip
                a_ = ring[(P + base) & 0xFFFF]; b_ = ring[(P + base + 1) & 0xFFFF]
                var[o[1]] = s32(a_ + (s32((b_ - a_) * fr) >> 15))
            elif k == 'mul': var[o[1]] = s32(var[o[2]] * var[o[3]]) >> o[4]
            elif k == 'add': var[o[1]] = s32(var[o[2]] + var[o[3]])
            elif k == 'sub': var[o[1]] = s32(var[o[2]] - var[o[3]])
            elif k == 'lfo':
                ph = (var[o[2]] + var[o[3]]) & 0xFFFFFFFF; var[o[2]] = s32(ph)
                t = abs((ph >> 16) - 32768) * 2; var[o[1]] = s32(t * var[o[4]])
            elif k == 'sh2':
                acc = s32(var[o[5]] + var[o[6]])
                if (acc & 0xFFFFFFFF) >= 0x10000: acc -= 0x10000; var[o[1]] = var[o[3]]; var[o[2]] = var[o[4]]
                var[o[5]] = acc
            elif k == 'quant':
                v_ = clamp16(s32(var[o[2]] + var[o[4]])); var[o[1]] = s32(v_ & var[o[3]])
        P = (P - 1) & 0xFFFF
        oL = s32(s32(var['wL'] * var['mixq']) >> 13) + var['in']; oR = s32(s32(var['wR'] * var['mixq']) >> 13) + var['in']
        if not stereo: oL = oR = s32(oL + oR) >> 1
        if pass_ == 0: pk = max(pk, abs(oL), abs(oR)); continue
        L.append(scale16(oL, G))
        if stereo: R.append(scale16(oR, G))
    return pk, L, R
def scale16(v_, G):
    neg = v_ < 0; v_ = (abs(v_) * G) >> 16 & 0xFFFFFFFF; v_ = min(v_, 32767); return -v_ if neg else v_

# ================================================ the EQ (2026-11-06) ================================================
LOWF_HZ = [50, 100, 200]
MIDF_HZ = [400, 800, 1000, 1500, 3000, 5000]
HIGHF_HZ = [8000, 10000, 14000]
QS = [0.4, 0.5, 0.7, 1.0, 1.4, 2.0]
GAINS = list(range(-12, 13)); G0 = GAINS.index(0)
LOWN = ["  50Hz", " 100Hz", " 200Hz"]
MIDN = [" 400Hz", " 800Hz", "  1kHz", "1.5kHz", "  3kHz", "  5kHz"]
HIGHN = ["  8kHz", " 10kHz", " 14kHz"]
QN = ["%.1f" % q for q in QS]
GAINN = ["%+3ddB" % g for g in GAINS]
EQ_G1 = [round((10 ** (g / 20) - 1) * 65536) for g in GAINS]             # G - 1 (Q16)
def opc16(f): return round((1 - math.exp(-2 * math.pi * f / FS)) * 65536)
EQ_LC = [opc16(f) for f in LOWF_HZ]; EQ_HC = [opc16(f) for f in HIGHF_HZ]  # one-pole coefficients (Q16)
EQ_BP = []                                                                  # per (mid f, Q): b0, -a1 (Q28); -a2 = 2 b0 - 1
for f_ in MIDF_HZ:
    for q_ in QS:
        w_ = 2 * math.pi * f_ / FS; al = math.sin(w_) / (2 * q_)
        EQ_BP += [round(al / (1 + al) * 2 ** 28), round(2 * math.cos(w_) / (1 + al) * 2 ** 28)]
# presets for single sounds (beatmaking): (name, low Hz, dB, mid Hz, dB, Q, high Hz, dB)
EQ_PRESETS = [
    ("DEFAULT",    100,   0, 1000,  0, 0.7, 10000,   0),
    ("BASS BOOST", 100,  +6, 1000,  0, 0.7, 10000,   0),
    ("SUB BOOM",    50,  +9,  400, -3, 1.0, 10000,   0),
    ("LOW CUT",    200, -12, 1000,  0, 0.7, 10000,   0),
    ("MID SCOOP",  100,   0,  800, -6, 0.7, 10000,   0),
    ("MID PUSH",   100,   0, 1000, +5, 0.7, 10000,   0),
    ("BOX CUT",    100,   0,  400, -6, 1.4, 10000,   0),
    ("BRIGHT",     100,   0, 1000,  0, 0.7, 10000,  +6),
    ("DARK",       100,   0, 1000,  0, 0.7,  8000,  -9),
    ("WARM",       200,  +3, 3000, -2, 0.7, 14000,  -4),
    ("KICK PUNCH",  50,  +4,  400, -6, 1.0,  8000,  +2),
    ("SNARE SNAP", 100,  -3, 3000, +5, 1.0, 10000,  +2),
    ("HAT SHINE",  200, -12, 1000, -3, 0.7, 10000,  +4),
    ("VOCAL",      100,  -6, 3000, +3, 0.7, 10000,  +3),
    ("LO-FI",      200, -12, 1500, +4, 0.7,  8000, -12),
]
def eq_idx(p):
    """preset -> field indices (LOWF, LOWG, MIDF, MIDG, QF, HIGHF, HIGHG)"""
    _, lf, lg, mf, mg, q_, hf, hg = p
    return (LOWF_HZ.index(lf), GAINS.index(lg), MIDF_HZ.index(mf), GAINS.index(mg), QS.index(q_), HIGHF_HZ.index(hf), GAINS.index(hg))
EQ_PIDX = [eq_idx(p) for p in EQ_PRESETS]
EQ_PTAB = []
for ix_ in EQ_PIDX: EQ_PTAB += [ix_[0] | ix_[1] << 8 | ix_[2] << 16 | ix_[3] << 24, ix_[4] | ix_[5] << 8 | ix_[6] << 16]
EQ_NAMES = [p[0] for p in EQ_PRESETS]
assert all(len(n) <= 10 for n in EQ_NAMES)
def m16(a_, c): return s32((s32(a_) * c) >> 16)
def eq_model(dry, ix):
    """bit-exact model of eq_kern: the mono output before the final level (list of s32)"""
    lf, lg, mf, mg, q_, hf, hg = ix
    CL, GL, GM, CH, GH = EQ_LC[lf], EQ_G1[lg], EQ_G1[mg], EQ_HC[hf], EQ_G1[hg]
    B0, NA1 = EQ_BP[2 * (mf * len(QS) + q_)], EQ_BP[2 * (mf * len(QS) + q_) + 1]; NA2 = 2 * B0 - (1 << 28)
    lz = hz = x1 = x2 = y1 = y2 = 0; out = []
    for d in dry:
        x = s32(d << 8)
        lz = s32(lz + m16(x - lz, CL)); x = s32(x + m16(lz, GL))
        bp = s32((B0 * s32(x - x2) + NA1 * y1 + NA2 * y2) >> 28)
        x2, x1, y2, y1 = x1, x, y1, bp
        x = s32(x + m16(bp, GM))
        hz = s32(hz + m16(x - hz, CH)); x = s32(x + m16(x - hz, GH))
        out.append(x >> 8)
    return out
def eq_gain(pk): return 0x10000 if pk <= 32767 else 0x7FFF0000 // pk

# ---- DELAY (2026-11-07, user: "basic, with presets: dub delay first, 1/8, 1/16, 1/4") ----
# The Input FX delay of MASTER.WAV (infx.Model.frame, left channel, no ping pong / 12 bit), rendered offline in mono:
# wow (triangle lfo, linear interpolation) -> one-pole low-pass -> one-pole high-pass on the repeats, feedback, mix.
# Presets = the Input FX ones of the same sound (same numbers, strength included). Time: the Input FX list (note values
# from the sequence tempo, or ms). Tail: until the repeats are below ~-54 dB (feedback only), at most DL_TAILMAX, the
# last DL_FADE frames of the tail fade out (a capped dub tail never ends with a cut). Level: as the EQ (kept, lowered
# only if it would clip). Delay line = the reverb's ring (RING frames).
import infx as IX
DL_SRC = [("DUB DELAY", "DUB TAPE"), ("1/8", "1/8 SYNC"), ("1/16", "1/16"), ("1/4", "1/4 SYNC")]
DLP = [[p for p in IX.DPRESETS if p[0] == src][0] for _, src in DL_SRC]
DL_NAMES = [n for n, _ in DL_SRC]
DL_PDEF = [p[1] | (IX.pct_index(p[2]) << 8) | (IX.pct_index(p[3]) << 16) for p in DLP]    # time, fb, mix (indices)
DL_CO = [[IX.onepole(p[4]), IX.onepole(p[5]), int(round(p[8] * 256)), int(round(p[9] / FS * 2 ** 32)) & 0xFFFFFFFF] for p in DLP]
assert all(not p[6] and not p[7] for p in DLP), "12 bit / ping pong not done here"
DL_TAILMAX, DL_FADE = 6 * FS, 32768
DL_PQ = [IX.pct_q12(i) for i in range(len(IX.PCT5))]
DL_NREP = [1 if i == 0 else min(40, math.ceil(math.log(1 / 500) / math.log(i * 5 / 100))) for i in range(len(IX.PCT5))]
ID_TEMPO, SYS_PARAM = 0x2000C, 0x884E5CD0                   # tempo x 10 (master.py, HW)
def dl_dt(time_idx, fpb):
    dt = (fpb * IX.TMUL[time_idx]) >> 12 if time_idx < IX.NNOTE else IX.TMS[time_idx - IX.NNOTE]
    return max(1, min(RING - 2, dt))
def dl_tail(time_idx, fb_idx, fpb): return min(dl_dt(time_idx, fpb) * (DL_NREP[fb_idx] + 1), DL_TAILMAX)
def dl_model(dry, preset, time_idx, fb_idx, mix_idx, fpb):
    """bit-exact model of dl_kern: the mono output before the final level (list of s32)"""
    DT = dl_dt(time_idx, fpb); FB, MIX = DL_PQ[fb_idx], DL_PQ[mix_idx]; CLP, CHP, MODD, MODINC = DL_CO[preset]
    nout = len(dry) + dl_tail(time_idx, fb_idx, fpb)
    dly = [0] * RING; dw = ph = lp = hp = 0; out = []
    for i in range(nout):
        x = dry[i] if i < len(dry) else 0
        ph = (ph + MODINC) & 0xFFFFFFFF; t = ph >> 16
        if t & 0x8000: t = (~t) & 0xFFFF
        off = (t * MODD) >> 15; f = off & 255
        i0 = (dw - DT - (off >> 8)) & 0xFFFF; av = dly[i0]; bv = dly[(i0 - 1) & 0xFFFF]
        d = av + (s32((bv - av) * f) >> 8)
        lp = s32(lp + (s32((d - lp) * CLP) >> 15)); hp = s32(hp + (s32((lp - hp) * CHP) >> 15)); fl = s32(lp - hp)
        w = s32(x + (s32(fl * FB) >> 12)); dly[dw] = -32768 if w < -32768 else 32767 if w > 32767 else w; dw = (dw + 1) & 0xFFFF
        e = s32(fl * MIX) >> 12; left = nout - i
        if left < DL_FADE: e = s32(e * left) >> 15
        out.append(x + e)
    return out

# ================================================== the pages ==================================================
# PRINT FX (first page: the HUB, MODE -> OTHER and Pad FX open it): the effect list
pg = PageAddon("reverb1", open_press=None)            # HUB name unchanged: PRINTFX.WAV replaces REVERB.WAV
pg.other_menu("Print FX")
pg.far_fire = True
pg.far_hend = True
pg.lean_pools = True
(X_REV, X_EQ, X_DLY) = range(3)
TYPE = pg.choice("", 0, 0, ["REVERB", "EQ    ", "DELAY "], default=X_REV)
pg.label("DO IT = SELECT", 0, 1)
# REVERB
pgR = pg.add_page("rv")
pgR.msg_fires = 0x600
PRESET = pgR.choice("", 0, 0, NAMES, default=DEFP)
PADL = pgR.dlabel(11, 0, 5)
MIX = pgR.value("MIX", 0, 1, fmt="  0", lo=0, hi=100, default=MIXDEF[DEFP])
pgR.label("%", 6, 1)
STER = pgR.choice("", 10, 1, ["  MONO", "STEREO"], default=0)
pgR.message()
# EQ
pgE = pg.add_page("eq")
pgE.msg_fires = 0x600
pgE.label("EQ", 0, 0)
EPRE = pgE.choice("", 4, 0, EQ_NAMES, default=0)
PARF = pgE.choice("", 0, 1, ["PARAMS >"], default=0)
EPADL = pgE.dlabel(11, 1, 5)
pgE.message()
# EQ PARAMS
pgP = pg.add_page("ep")
pgP.msg_fires = 0x600
(B_LOW, B_MID, B_HIGH) = range(3)
BAND = pgP.choice("", 0, 0, ["LOW ", "MID ", "HIGH"], default=B_LOW)
D0 = EQ_PIDX[0]
LOWF = pgP.choice("", 4, 0, LOWN, default=D0[0])
MIDF = pgP.choice("", 4, 0, MIDN, default=D0[2])
HIGHF = pgP.choice("", 4, 0, HIGHN, default=D0[5])
PPADL = pgP.dlabel(11, 0, 5)
pgP.label("GAIN", 0, 1)
LOWG = pgP.choice("", 5, 1, GAINN, default=D0[1], share=True)
MIDG = pgP.choice("", 5, 1, GAINN, default=D0[3], share=True)
HIGHG = pgP.choice("", 5, 1, GAINN, default=D0[6], share=True)
QLBL = pgP.dlabel(11, 1, 1)
QF = pgP.choice("", 13, 1, QN, default=D0[4])
pgP.message()
# DELAY: "DUB DELAY   A05" / "MIX45 FB75 1/4  " (as the Input FX delay page)
pgD = pg.add_page("dl")
pgD.msg_fires = 0x600
DPRE = pgD.choice("", 0, 0, DL_NAMES, default=0)
DPADL = pgD.dlabel(11, 0, 5)
pgD.label("MIX", 0, 1)
DMIX = pgD.choice("", 3, 1, IX.PCT5, default=(DL_PDEF[0] >> 16) & 0xFF, share=True)
pgD.label("FB", 6, 1)
DFB = pgD.choice("", 8, 1, IX.PCT5, default=(DL_PDEF[0] >> 8) & 0xFF, share=True)
DTIME = pgD.choice("", 11, 1, IX.TIMES, default=DL_PDEF[0] & 0xFF)
pgD.message()
PCURSOR = [BAND, LOWF, MIDF, HIGHF, LOWG, MIDG, HIGHG, QF]                 # widget order
PVIS = {LOWF: [B_LOW], MIDF: [B_MID], HIGHF: [B_HIGH], LOWG: [B_LOW], MIDG: [B_MID], HIGHG: [B_HIGH], QF: [B_MID]}
EQFIELDS = (LOWF, LOWG, MIDF, MIDG, QF, HIGHF, HIGHG)                     # eq_idx order (preset table)
def fidp(p, slot): return [it[1] for it in p.items if it[0] in ('choice', 'value') and it[-1] == slot][0]

(ST, SEL, PEND, PHASE, LI, NL, DST, NIN, NOUT, NMAX, POS, DONEU, TOTU, MAXLV, PREVCUR, LASTP, LASTSET, DEB, RSET,
 SAVEPK) = [pg.alloc_slot() for _ in range(20)]
pg.defaults[LASTP] = 3
EFX, OST, RSZ, DLBL, ELASTP, PLASTF = [pg.alloc_slot() for _ in range(6)]  # effect of the job, its stereo, ring bytes
LSRC, LSTART, LEND, LSTEP, LGAIN, LN = [[pg.alloc_slot() for _ in range(4)] for _ in range(6)]
KP = [pg.alloc_slot() for _ in range(11)]                 # mix kernel parameters (as S500)
for _s in KP: del pg.defaults[_s]
M_LBASE, M_RBASE, M_DST, M_N, M_I, M_F, M_SI, M_SF, M_END, M_G, M_DONE = range(0, 44, 4)
KB = KP[0]
KV = [pg.alloc_slot() for _ in range(NV)]                 # reverb kernel variables (+ shared loop variables of the EQ)
for _s in KV: del pg.defaults[_s]
VB = KV[0]
def VA(n): return VB + 4 * IDX[n]
EQB = pg.alloc_buf(56)                                    # EQ kernel: states + coefficients
(E_LZ, E_HZ, E_X1, E_X2, E_Y1, E_Y2, E_CL, E_GL, E_B0, E_NA1, E_NA2, E_GM, E_CH, E_GH) = range(0, 56, 4)
MSGB = pg.alloc_buf(20)                                   # message text (px_msg shows it on the open page)
DLB = pg.alloc_buf(64)                                    # delay kernel: states + coefficients, tempo message
(D_PH, D_DW, D_LP, D_HP, D_DT, D_FB, D_MIX, D_CLP, D_CHP, D_MODD, D_MODINC, D_LEFT, D_MSG) = range(0, 52, 4)
DLASTP = pg.alloc_slot()
MSTR = {s: '__m%d' % i for i, s in enumerate(["NO SAMPLE", "MEMORY FULL", "HIT A PAD FIRST"])}
ST_IDLE, ST_JOB, ST_PREV = 0, 1, 2
PH_START, PH_CLRD, PH_MIX, PH_RINIT, PH_CLRR, PH_REND, PH_PREV, PH_FIN = range(8)

def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()     # clobbers r2! (rv_dry is called via r1: CLRD/MIX keep r2)
def gcall(m, lbl):
    """call a routine of the shared code (namespace '') from a page's code"""
    ns = m.a.ns; m.a.ns = ''; call(m, lbl); m.a.ns = ns
def emsg(m, text):
    """show a fixed message on the open page (r0-r7, r12, r13)"""
    ns = m.a.ns; m.a.ns = ''; m.a.movl_pool(MSTR[text], 4); call(m, 'px_msgs'); m.a.ns = ns
def osc(m, fn): m.imm32(fn, 3); m.a.jsr(3); m.a.nop()
def rdb(m, addr, r): m.imm32(addr, r); m.a.movb_ld(r, r); m.a.extu_b(r, r)
def kst(m, r, off): m.imm32(KB + off, 1); m.a.movl_st(r, 1)
def kld(m, off, r): m.imm32(KB + off, r); m.a.movl_ld(r, r)
def vst(m, r, n): m.imm32(VA(n), 1); m.a.movl_st(r, 1)
def vld(m, n, r): m.imm32(VA(n), r); m.a.movl_ld(r, r)
def redraw(m): m.imm32(EVQ + 0x70, 1); m.a.mov_imm(1, 0); m.a.movl_st(0, 1)
def push(m, page_addr): m.imm32(EVQ, 4); m.imm32(page_addr, 5); m.a.mov_imm(0, 6); m.imm32(OS_PUSH_PAGE, 2); m.a.jsr(2); m.a.nop()
def dmuls(m, rm, rn): m.a._emit(0x300D | (rn << 8) | (rm << 4))
def xtrct(m, rm, rn): m.a._emit(0x200D | (rn << 8) | (rm << 4))
def addc(m, rm, rn): m.a._emit(0x300E | (rn << 8) | (rm << 4))
def jmpl(m, lbl): m.a.movl_pool(lbl, 2); m.a.jmp(2); m.a.nop()
def tword(m, v):
    """a table word that is an address of our scratch follows the build base like m.imm32 (relocation)"""
    if isinstance(v, int) and LOAD_SCRATCH <= v < LOAD_SCRATCH + 0x1000 and m.scratch != LOAD_SCRATCH and m.scratch != mpcmod_SCRATCH:
        v = m.scratch + (v - LOAD_SCRATCH)
    m.a._emit(v & 0xFFFF); m.a._emit((v >> 16) & 0xFFFF)
def cached_call(m, label):
    m.a.movl_pool(label, 2); m.imm32(0xDFFFFFFF, 3); m.a.and_rr(3, 2); m.a.jsr(2); m.a.nop()
def padblk(m, rpad, rd_):
    a = m.a
    m.imm32(PROG_IDX, 1); a.movw_ld(1, rd_); a.extu_w(rd_, rd_); m.imm32(PROG_STRIDE, 1); a.mul_l(1, rd_); a.sts_macl(rd_)
    m.imm32(PROG_BASE + PAD_OFF, 1); a.add_rr(1, rd_)
    m.imm32(PAD_SIZE, 3); a.mul_l(rpad, 3); a.sts_macl(3); a.add_rr(3, rd_)

# ---- PRINT FX list: the job runs here every fire (whatever page is open), the shared code sits in this page ----
def fire(m):
    a = m.a
    call(m, 'rv_tick')
    jmpl(m, '__px_end'); m.pool_jump()
    subs(m)
    a.deflabel('__px_end')
pg.on_fire(fire)
pg.on_inactive(lambda m: call(m, 'rv_tick'))
def list_doit(m):
    a = m.a; rv = m.newlabel('rv'); dn = m.newlabel('dn'); eq_ = m.newlabel('eq')
    rdb(m, TYPE, 0); a.tst_rr(0, 0); m.bt_far(rv)
    a.mov_imm(X_DLY, 1); a.cmpeq(1, 0); m.bf_far(eq_); go(m, pgD.addr['page']); jmpl(m, dn)
    a.deflabel(eq_); go(m, pgE.addr['page']); jmpl(m, dn)
    a.deflabel(rv); go(m, pgR.addr['page'])
    a.deflabel(dn)
pg.on_doit(list_doit)

# ---- the effect pages ----
def page_fire(efx, lbl, extra):
    def f(m):
        m.a.mov_imm(efx, 4); m.imm32(lbl, 5); gcall(m, 'px_pg'); gcall(m, extra)
    return f
pgR.on_fire(lambda m: page_fire(0, pgR.addr['dlabels'][PADL], 'rv_pdef')(m))
pgE.on_fire(lambda m: page_fire(1, pgE.addr['dlabels'][EPADL], 'eq_pdef')(m))
pgP.on_fire(lambda m: page_fire(1, pgP.addr['dlabels'][PPADL], 'eqp_vis')(m))
pgD.on_fire(lambda m: page_fire(X_DLY, pgD.addr['dlabels'][DPADL], 'dl_pdef')(m))
def ev_pad(m, hret0, hret1):
    a = m.a; nz = m.newlabel('nz')
    gcall(m, 'px_pad'); a.tst_rr(0, 0); a.bf_lbl(nz); jmpl(m, hret0)
    a.deflabel(nz); jmpl(m, hret1); m.pool_jump()
def go(m, page_addr):
    """open a page of ours IN PLACE of the open one (OS back, then push): the OS page stack stays one deep inside Print
    FX (HW 2026-11-06: four pages pushed deep, CANCEL got stuck in EQ PARAMS)"""
    m.imm32(page_addr, 5); gcall(m, 'px_go')
def cancel_to(parent, drop):
    """CANCEL (user 2026-11-06): REVERB / EQ -> the PRINT FX list (a preview or a render dropped); EQ PARAMS -> EQ (kept)"""
    def fn(m, hret1):
        if drop: gcall(m, 'rv_drop')                                    # (nothing when idle)
        go(m, parent.addr['page']); jmpl(m, hret1)
        m.pool_jump()
    return fn
for p_ in (pgR, pgE, pgP, pgD): p_.on_event(0x38, ev_pad)
pgR.cancel_fn = cancel_to(pg, True); pgE.cancel_fn = cancel_to(pg, True); pgP.cancel_fn = cancel_to(pgE, False)
pgD.cancel_fn = cancel_to(pg, True)
pgR.on_doit(lambda m: gcall(m, 'px_doit'))
pgD.on_doit(lambda m: gcall(m, 'px_doit'))
pgP.on_doit(lambda m: gcall(m, 'px_doit'))
def eq_doit(m):
    """DO IT on "PARAMS >" opens EQ PARAMS; elsewhere render / keep"""
    a = m.a; dn = m.newlabel('dn'); rn = m.newlabel('rn')
    m.imm32(pgE.addr['page'], 1); a.movl_ld_disp(1, 0x24, 0); m.imm32(pgE.addr['values'][fidp(pgE, PARF)], 1); a.cmpeq(1, 0)
    m.bf_far(rn); go(m, pgP.addr['page']); jmpl(m, dn)
    a.deflabel(rn); gcall(m, 'px_doit')
    a.deflabel(dn)
pgE.on_doit(eq_doit)
K_PARAMS = 0x15                                                        # PARAMS key code (track.py, HW)
def eq_params_key(m, hret0, hret1):
    """the MPC's PARAMS key on the EQ page opens EQ PARAMS; other keys fall through to DO IT / CANCEL (r9 kept)"""
    a = m.a; kx = m.newlabel('kx')
    a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 10); a.extu_w(10, 10)
    a.mov_imm(K_PARAMS, 0); a.cmpeq(0, 10); m.bf_far(kx)
    go(m, pgP.addr['page']); jmpl(m, hret1)
    a.deflabel(kx)
pgE.on_event(2, eq_params_key)

def subs(m):
    a = m.a
    # ---- px_go (r5 = page): OS back, then push that page ----
    a.deflabel('px_go'); a.stsl_pr(); a.movl_st_dec(5, 15)
    m.imm32(EVQ, 4); a.mov_imm(0, 5); a.mov_imm(2, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()
    a.movl_ld_inc(15, 5); m.imm32(EVQ, 4); a.mov_imm(0, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- px_pg (r4 = effect 0 REVERB / 1 EQ, r5 = status label): an effect page's fire ----
    a.deflabel('px_pg'); a.stsl_pr()
    st(m, 5, DLBL)
    ld(m, EFX, 0); a.cmpeq(4, 0); same = m.newlabel('same'); m.bt_far(same)
    st(m, 4, EFX); call(m, 'rv_drop')                                       # the other effect: its preview dropped
    a.deflabel(same)
    ld(m, PEND, 0); a.tst_rr(0, 0); np_ = m.newlabel('np'); m.bt_far(np_); call(m, 'rv_pick'); a.deflabel(np_)
    ld(m, DLBL, 4); call(m, 'rv_disp')
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- rv_pdef: reverb preset changed -> its default mix ----
    a.deflabel('rv_pdef')
    rdb(m, PRESET, 9); ld(m, LASTP, 0); a.cmpeq(9, 0); sp = m.newlabel('sp'); m.bt_far(sp)
    st(m, 9, LASTP); a.mov_rm_rn(9, 0); a.shll2(0); a.movl_pool('rv_mixdef', 1); a.add_rr(0, 1); a.movl_ld(1, 0)
    st(m, 0, MIX); redraw(m)
    a.deflabel(sp); a.rts(); a.nop()
    m.pool_jump()
    # ---- eq_pdef: EQ preset changed -> its 7 band settings (then the player's) ----
    a.deflabel('eq_pdef')
    rdb(m, EPRE, 9); ld(m, ELASTP, 0); a.cmpeq(9, 0); ep = m.newlabel('ep'); m.bt_far(ep)
    st(m, 9, ELASTP); a.mov_rm_rn(9, 0); a.shll2(0); a.shll(0); a.movl_pool('eq_ptab', 3); a.add_rr(0, 3)
    a.movl_ld(3, 0)
    for k, f_ in enumerate(EQFIELDS):
        if k == 4: a.movl_ld_disp(3, 4, 0)
        m.imm32(f_, 1); a.movb_st(0, 1)
        if k not in (3, 6): a.shlr8(0)
    redraw(m)
    a.deflabel(ep); a.rts(); a.nop()
    m.pool_jump()
    # ---- dl_pdef: delay preset changed -> its TIME, FB, MIX ----
    a.deflabel('dl_pdef')
    rdb(m, DPRE, 9); ld(m, DLASTP, 0); a.cmpeq(9, 0); dpx = m.newlabel('dpx'); m.bt_far(dpx)
    st(m, 9, DLASTP); a.mov_rm_rn(9, 0); a.shll2(0); a.movl_pool('dl_pdef_t', 3); a.add_rr(0, 3); a.movl_ld(3, 0)
    for f_ in (DTIME, DFB, DMIX):
        m.imm32(f_, 1); a.movb_st(0, 1); a.shlr8(0)
    redraw(m)
    a.deflabel(dpx); a.rts(); a.nop()
    m.pool_jump()
    # ---- eqp_vis: EQ PARAMS: "Q" label text; the fields of the band shown, the cursor skips the others ----
    a.deflabel('eqp_vis')
    m.imm32(pgP.addr['dlabels'][QLBL], 1); a.mov_imm(ord('Q'), 0); a.movb_st(0, 1)
    a.movl_pool('vt_eqp', 4); jmpl(m, 'vis_tab')
    m.pool_jump()
    vis_code(m)
    # ---- px_msgs (r4 = 17-byte text) / px_msg (text in MSGB): the message on the open effect page ----
    a.deflabel('px_msgs')
    m.imm32(MSGB, 5); a.mov_imm(17, 6)
    cl = m.newlabel('cl'); a.deflabel(cl); a.movb_ldinc(4, 0); a.movb_st(0, 5); a.add_imm(1, 5); a.dt(6); a.bf_lbl(cl)
    a.deflabel('px_msg')
    m.imm32(EVQ + 0x68, 1); a.movl_ld(1, 7)
    go = m.newlabel('go')
    for p_ in (pgR, pgE, pgP, pgD):
        nx = m.newlabel('mn'); m.imm32(p_.addr['page'], 1); a.cmpeq(1, 7); m.bf_far(nx)
        m.imm32(p_.addr['msgt'], 12); m.imm32(p_.addr['msgw'], 13); m.imm32(p_.SHOWMSG, 6); m.imm32(p_.msg_fires, 3)
        jmpl(m, go)
        a.deflabel(nx)
    a.rts(); a.nop()
    m.pool_jump()
    a.deflabel(go)
    m.imm32(MSGB, 5); a.mov_imm(17, 2)
    cm = m.newlabel('cm'); a.deflabel(cm); a.movb_ldinc(5, 0); a.movb_st(0, 12); a.add_imm(1, 12); a.dt(2); a.bf_lbl(cm)
    a.mov_rm_rn(13, 1); a.add_imm(0x17, 1); a.movb_ld(1, 0); a.or_imm(0x80); a.movb_st(0, 1)
    a.movl_st(3, 6); redraw(m)
    a.rts(); a.nop()
    m.pool_jump()
    # ---- px_pad (r8 = pad event): r0 = 1 consumed (the preview played) / 0 (the pad plays; chosen) ----
    a.deflabel('px_pad'); a.stsl_pr()
    r0_, r1_, px_ = m.newlabel('r0'), m.newlabel('r1'), m.newlabel('px')
    a.movl_ld_disp(8, 4, 0); a.tst_rr(0, 0); m.bt_far(r0_)
    a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 9); a.extu_w(9, 9); a.mov_imm(12, 0); a.cmphs(0, 9); m.bt_far(r0_)
    m.imm32(BANKOBJ, 1); a.movb_ld(1, 0); a.and_imm(3); a.mov_imm(12, 2); a.mul_l(2, 0); a.sts_macl(0); a.add_rr(0, 9)
    a.add_imm(1, 9)
    ld(m, ST, 0); a.mov_imm(ST_JOB, 1); a.cmpeq(1, 0); m.bt_far(r0_)        # rendering: pads just play
    a.mov_imm(ST_PREV, 1); a.cmpeq(1, 0); np2 = m.newlabel('np'); m.bf_far(np2)
    ld(m, SEL, 0); a.cmpeq(9, 0); m.bf_far(np2)
    m.imm32(PLAYER, 4); osc(m, AUD_STOP)                                     # the preview, at the pad's velocity
    m.imm32(PLAYER, 4); ld(m, DST, 5); a.mov_imm(0, 6); a.movl_ld_disp(8, 4, 7)
    m.imm32(BANKOBJ + 4, 1); a.movl_ld(1, 0); a.tst_rr(0, 0); fv = m.newlabel('fv'); a.bt_lbl(fv); a.mov_imm(127, 7); a.deflabel(fv)   # FULL LEVEL on: 127
    osc(m, AUD_PLAY)
    a.bra_lbl(r1_); a.nop()
    a.deflabel(np2); st(m, 9, PEND)
    a.deflabel(r0_); a.mov_imm(0, 0); a.bra_lbl(px_); a.nop()
    a.deflabel(r1_); a.mov_imm(1, 0)
    a.deflabel(px_); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- px_doit: DO IT on an effect page: render the preview / keep it ----
    a.deflabel('px_doit'); a.stsl_pr()
    dx = m.newlabel('dx'); keep = m.newlabel('keep')
    ld(m, ST, 0); a.mov_imm(ST_PREV, 1); a.cmpeq(1, 0); m.bt_far(keep)
    a.tst_rr(0, 0); m.bf_far(dx)                                             # rendering: ignore
    ld(m, SEL, 9); a.tst_rr(9, 9); np3 = m.newlabel('np'); m.bf_far(np3)
    emsg(m, "HIT A PAD FIRST"); jmpl(m, dx)
    a.deflabel(np3)
    sti(m, PH_START, PHASE); sti(m, 0, DONEU); sti(m, 1, TOTU); sti(m, ST_JOB, ST); jmpl(m, dx)
    a.deflabel(keep); sti(m, ST_JOB, ST); sti(m, PH_FIN, PHASE)
    a.deflabel(dx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- px_set: r9 = the settings of the current effect (a change re-renders the preview). r0 r1 r2 ----
    a.deflabel('px_set')
    ld(m, EFX, 0); a.tst_rr(0, 0); eqs = m.newlabel('eqs'); m.bf_far(eqs)
    rdb(m, PRESET, 9); ld(m, MIX, 0); a.shll8(0); a.or_rr(0, 9); rdb(m, STER, 0); a.shll16(0); a.or_rr(0, 9)
    a.rts(); a.nop()
    a.deflabel(eqs); eqs2 = m.newlabel('eqs2')
    a.mov_imm(X_DLY, 1); a.cmpeq(1, 0); m.bf_far(eqs2)                          # DELAY: preset, time, fb, mix
    m.imm32(0x40000000, 9)
    for f_, sh in ((DTIME, 0), (DFB, 8), (DMIX, 16), (DPRE, 24)):
        rdb(m, f_, 0); a.mov_imm(sh, 2); a.shad(2, 0); a.or_rr(0, 9)
    a.rts(); a.nop()
    a.deflabel(eqs2); m.imm32(0x80000000, 9)
    for f_, sh in zip(EQFIELDS, (0, 2, 7, 10, 15, 18, 20)):
        rdb(m, f_, 0); a.mov_imm(sh, 2); a.shad(2, 0); a.or_rr(0, 9)
    a.rts(); a.nop()
    m.pool_jump()
    # ---- rv_pick: a pad was hit (PEND = pad + 1): during a preview of another pad drop it; choose the pad ----
    a.deflabel('rv_pick'); a.stsl_pr()
    ld(m, ST, 0); a.tst_rr(0, 0); pk0 = m.newlabel('pk0'); m.bt_far(pk0)
    call(m, 'rv_drop')
    a.deflabel(pk0); ld(m, PEND, 0); st(m, 0, SEL); sti(m, 0, PEND)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- rv_drop: delete the work / preview sample, current sample back, idle ----
    a.deflabel('rv_drop'); a.stsl_pr()
    ld(m, ST, 0); a.tst_rr(0, 0); dx = m.newlabel('dx'); m.bt_far(dx)
    m.imm32(PLAYER, 4); osc(m, AUD_STOP)
    ld(m, PHASE, 0); a.mov_imm(PH_CLRD, 1); a.cmphs(1, 0); nd = m.newlabel('nd'); m.bf_far(nd)
    ld(m, DST, 0); m.imm32(MGR, 1); a.movl_st_disp(0, 8, 1); m.imm32(MGR, 4); osc(m, DELETE_CMD)
    ld(m, PREVCUR, 0); m.imm32(MGR, 1); a.movl_st_disp(0, 8, 1)
    a.deflabel(nd); sti(m, ST_IDLE, ST)
    a.deflabel(dx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- rv_disp (r4 = status label): pad / % / PREV ----
    a.deflabel('rv_disp'); a.stsl_pr()
    a.mov_rm_rn(4, 12); a.mov_imm(0, 14)
    def cput(s):
        for ch in s:
            n = m.newlabel('cp'); a.movb_ld(12, 1); a.mov_imm(ord(ch), 0); a.cmpeq(0, 1); a.bt_lbl(n)
            a.movb_st(0, 12); a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
    def cputr(r):
        n = m.newlabel('cr'); a.movb_ld(12, 1); a.cmpeq(r, 1); a.bt_lbl(n); a.mov_rm_rn(r, 0); a.movb_st(0, 12)
        a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
    dn, job, nos, prv = m.newlabel('dn'), m.newlabel('job'), m.newlabel('nos'), m.newlabel('prv')
    ld(m, ST, 0); a.mov_imm(ST_PREV, 1); a.cmpeq(1, 0); m.bt_far(prv); a.tst_rr(0, 0); m.bf_far(job)
    ld(m, SEL, 9); a.tst_rr(9, 9); m.bt_far(nos)
    a.add_imm(-1, 9); a.mov_rm_rn(9, 4); a.mov_imm(12, 5); pg._hcall(m, '__udiv')
    a.add_imm(ord('A'), 0); a.mov_rm_rn(0, 10); a.add_imm(1, 1); a.mov_rm_rn(1, 4); a.mov_imm(10, 5)
    pg._hcall(m, '__udiv'); a.add_imm(0x30, 0); a.mov_rm_rn(0, 11); a.add_imm(0x30, 1); a.mov_rm_rn(1, 13)
    cput("  "); cputr(10); cputr(11); cputr(13); a.bra_lbl(dn); a.nop()
    a.deflabel(nos); cput(" PAD?"); a.bra_lbl(dn); a.nop()
    a.deflabel(prv); cput(" PREV"); a.bra_lbl(dn); a.nop()
    m.pool_jump()
    a.deflabel(job)
    ld(m, TOTU, 5); a.shlr2(5); a.shlr2(5); a.shlr2(5); a.add_imm(1, 5)
    ld(m, DONEU, 4); a.shlr2(4); a.shlr2(4); a.shlr2(4); a.mov_imm(100, 0); a.mul_l(0, 4); a.sts_macl(4)
    pg._hcall(m, '__udiv'); a.mov_imm(99, 1); c9 = m.newlabel('c9'); a.cmpgt(1, 0); a.bf_lbl(c9); a.mov_rm_rn(1, 0); a.deflabel(c9)
    a.mov_rm_rn(0, 4); a.mov_imm(10, 5); pg._hcall(m, '__udiv')
    a.add_imm(0x30, 0); a.mov_rm_rn(0, 10); a.add_imm(0x30, 1); a.mov_rm_rn(1, 11)
    cput("  "); cputr(10); cputr(11); cput("%")
    a.deflabel(dn)
    a.tst_rr(14, 14); px = m.newlabel('px'); m.bt_far(px); redraw(m)
    a.deflabel(px); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- rv_tick (every fire): settings changed while rendering / previewing -> re-render after DEBOUNCE; job step ----
    a.deflabel('rv_tick'); a.stsl_pr()
    tx = m.newlabel('tx'); tj = m.newlabel('tj')
    rdb(m, STER, 0); ld(m, EFX, 2); a.tst_rr(2, 2); os_ = m.newlabel('os'); a.bt_lbl(os_); a.mov_imm(0, 0); a.deflabel(os_)
    st(m, 0, OST)                                                            # the EQ result is mono
    ld(m, ST, 0); a.tst_rr(0, 0); m.bt_far(tx)
    ld(m, PHASE, 0); a.mov_imm(PH_RINIT, 1); a.cmphs(1, 0); m.bf_far(tj)       # dry mix not ready yet
    call(m, 'px_set'); ld(m, RSET, 0); a.cmpeq(9, 0); same = m.newlabel('same'); m.bt_far(same)
    ld(m, LASTSET, 0); a.cmpeq(9, 0); chg = m.newlabel('chg'); a.bt_lbl(chg); st(m, 9, LASTSET); sti(m, DEBOUNCE, DEB); a.deflabel(chg)
    ld(m, DEB, 0); a.add_imm(-1, 0); st(m, 0, DEB); a.cmppl(0); m.bt_far(tx)
    m.imm32(PLAYER, 4); osc(m, AUD_STOP)
    sti(m, ST_JOB, ST); sti(m, PH_RINIT, PHASE); a.bra_lbl(tj); a.nop()
    a.deflabel(same); st(m, 9, LASTSET)
    a.deflabel(tj)
    ld(m, ST, 0); a.mov_imm(ST_JOB, 1); a.cmpeq(1, 0); m.bf_far(tx)
    call(m, 'rv_job')
    a.deflabel(tx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- rv_job: one step ----
    a.deflabel('rv_job'); a.stsl_pr()
    jx = m.newlabel('jx')
    labs = {p: m.newlabel('ph%d' % p) for p in range(8)}
    ld(m, PHASE, 0)
    for p in labs:
        nx = m.newlabel('pn'); a.mov_imm(p, 1); a.cmpeq(1, 0); a.bf_lbl(nx); a.movl_pool(labs[p], 2); a.jmp(2); a.nop(); a.deflabel(nx)
    a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()

    # == START: layers (as S500, no pitch up), the work sample: copy of the first source, resized ==
    a.deflabel(labs[PH_START])
    ld(m, SEL, 9); a.add_imm(-1, 9); padblk(m, 9, 11)
    a.mov_imm(0, 13); a.mov_imm(0, 0); st(m, 0, MAXLV); st(m, 0, NIN)
    for l in range(4):
        sk = m.newlabel('ls')
        a.mov_rm_rn(11, 1); a.add_imm(l * LAYER, 1); a.movb_ld(1, 0); a.tst_rr(0, 0); m.bt_far(sk)
        m.imm32(MGR, 4); a.mov_rm_rn(11, 5); a.add_imm(l * LAYER, 5); osc(m, FINDNAME); a.tst_rr(0, 0); m.bt_far(sk)
        a.mov_rm_rn(13, 10); a.shll2(10)
        m.imm32(LSRC[0], 1); a.add_rr(10, 1); a.movl_st(0, 1); a.mov_rm_rn(0, 12)
        a.movl_ld_disp(12, 0x20, 2); a.movl_ld_disp(12, 0x24, 3); a.movl_ld_disp(12, 0x18, 4)
        a.add_imm(-1, 4); a.cmphi(4, 3); e1 = m.newlabel('e1'); a.bf_lbl(e1); a.mov_rm_rn(4, 3); a.deflabel(e1)
        a.cmphi(3, 2); e2 = m.newlabel('e2'); a.bf_lbl(e2); a.mov_rm_rn(3, 2); a.deflabel(e2)
        m.imm32(LSTART[0], 1); a.add_rr(10, 1); a.movl_st(2, 1); m.imm32(LEND[0], 1); a.add_rr(10, 1); a.movl_st(3, 1)
        a.mov_rm_rn(3, 6); a.sub(2, 6)
        a.mov_rm_rn(11, 1); a.add_imm(l * LAYER + 0x11, 1); a.movb_ld(1, 7); a.extu_b(7, 7)
        m.imm32(LGAIN[0], 1); a.add_rr(10, 1); a.movl_st(7, 1)
        ld(m, MAXLV, 0); mx = m.newlabel('mx'); a.cmphi(0, 7); a.bf_lbl(mx); st(m, 7, MAXLV); a.deflabel(mx)
        a.mov_rm_rn(11, 1); a.add_imm(l * LAYER + 0x14, 1); a.movw_ld(1, 4)
        a.movl_st_dec(6, 15); a.movl_st_dec(10, 15); call(m, 'rv_pow'); a.movl_ld_inc(15, 10); a.movl_ld_inc(15, 6)
        m.imm32(LSTEP[0], 2); a.add_rr(10, 2); a.movl_st(0, 2)
        a.dmulu_l(1, 6); a.sts_mach(0); a.sts_macl(2); a.shll16(0); a.shlr16(2); a.or_rr(2, 0); a.add_imm(1, 0)
        m.imm32(LN[0], 2); a.add_rr(10, 2); a.movl_st(0, 2)
        ld(m, NIN, 2); mo = m.newlabel('mo'); a.cmphi(2, 0); a.bf_lbl(mo); st(m, 0, NIN); a.deflabel(mo)
        a.add_imm(1, 13)
        a.deflabel(sk)
        m.pool_jump()
    st(m, 13, NL)
    a.tst_rr(13, 13); some = m.newlabel('some'); m.bf_far(some)
    sti(m, ST_IDLE, ST); emsg(m, "NO SAMPLE"); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(some)
    ld(m, MAXLV, 0); a.tst_rr(0, 0); ml = m.newlabel('ml'); a.bf_lbl(ml); a.mov_imm(100, 0); st(m, 0, MAXLV); a.deflabel(ml)
    a.mov_imm(0, 10)
    gl = m.newlabel('gl'); a.deflabel(gl)
    m.imm32(LGAIN[0], 9); a.add_rr(10, 9); a.movl_ld(9, 4)
    ld(m, MAXLV, 0); a.tst_rr(4, 4); gz = m.newlabel('gz'); a.bf_lbl(gz); a.mov_rm_rn(0, 4); a.deflabel(gz)
    a.shll16(4); ld(m, NL, 5); a.mul_l(0, 5); a.sts_macl(5); pg._hcall(m, '__udiv'); a.movl_st(0, 9)
    a.add_imm(4, 10); ld(m, NL, 0); a.shll2(0); a.cmpeq(0, 10); a.bf_lbl(gl)
    # NMAX = NIN + longest tail (EQ: none); block = header + PCM L,R (NMAX each) + dry (NIN) + ring (EQ: none)
    ld(m, EFX, 2); m.imm32(TMAX + GUARD, 3); m.imm32(2 * RING, 5); a.tst_rr(2, 2); rk = m.newlabel('rk'); a.bt_lbl(rk)
    m.imm32(DL_TAILMAX + GUARD, 3); a.mov_imm(X_DLY, 0); a.cmpeq(0, 2); a.bt_lbl(rk)      # DELAY: ring + its tail
    m.imm32(GUARD, 3); a.mov_imm(0, 5); a.deflabel(rk)
    st(m, 5, RSZ); ld(m, NIN, 0); a.add_rr(3, 0); st(m, 0, NMAX)
    ld(m, LSRC[0], 8); m.imm32(MGR, 1); a.movl_ld_disp(1, 8, 0); st(m, 0, PREVCUR)
    m.imm32(MGR, 1); a.movl_st_disp(8, 8, 1)
    m.os_extract(8, COPY_CMD)
    a.tst_rr(0, 0); cok = m.newlabel('cok'); m.bt_far(cok)
    ld(m, PREVCUR, 0); m.imm32(MGR, 1); a.movl_st_disp(0, 8, 1)
    sti(m, ST_IDLE, ST); emsg(m, "MEMORY FULL"); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(cok)
    m.imm32(MGR, 1); a.movl_ld_disp(1, 8, 9); st(m, 9, DST)
    call(m, 'rv_size')                                                       # r4 = block size
    a.mov_rm_rn(9, 0); a.add_rr(4, 0); a.add_imm(4, 0); m.imm32(MGR, 1); a.movl_ld_disp(1, 4, 1)
    a.cmphi(1, 0); fit = m.newlabel('fit'); m.bf_far(fit)
    m.imm32(MGR, 4); osc(m, DELETE_CMD)
    ld(m, PREVCUR, 0); m.imm32(MGR, 1); a.movl_st_disp(0, 8, 1)
    sti(m, ST_IDLE, ST); emsg(m, "MEMORY FULL"); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(fit)
    a.movl_st(4, 9); a.mov_rm_rn(9, 0); a.add_rr(4, 0); a.mov_imm(0, 1); a.movl_st(1, 0)
    m.imm32(0x3E, 1); a.add_rr(9, 1); a.mov_imm(0, 0); a.movw_st(0, 1)       # the player honours start/end
    sti(m, 0, POS); sti(m, PH_CLRD, PHASE)
    ld(m, NIN, 0); ld(m, NL, 2); a.add_imm(1, 2); a.mul_l(2, 0); a.sts_macl(0); ld(m, RSZ, 1); a.add_rr(1, 0)
    st(m, 0, TOTU); sti(m, 0, DONEU)
    call(m, 'icinv')
    a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()

    # == CLRD: dry buffer = 0 ==
    a.deflabel(labs[PH_CLRD])
    ld(m, NIN, 2); ld(m, POS, 3); a.sub(3, 2); m.imm32(CHUNK_CLR, 0); a.cmphi(0, 2); c1 = m.newlabel('c1'); a.bf_lbl(c1); a.mov_rm_rn(0, 2); a.deflabel(c1)
    kst(m, 2, M_N); a.movl_pool('rv_dry', 1); a.jsr(1); a.nop(); a.mov_rm_rn(3, 0); a.shll(0); a.add_rr(0, 4); kst(m, 4, M_DST)
    a.add_rr(2, 3); st(m, 3, POS); ld(m, DONEU, 0); a.add_rr(2, 0); st(m, 0, DONEU)
    cached_call(m, 'rv_kclr')
    ld(m, NIN, 0); ld(m, POS, 3); a.cmpeq(0, 3); m.bf_far(jx)
    sti(m, 0, LI); call(m, 'rv_layer'); sti(m, PH_MIX, PHASE); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()

    # == MIX: a chunk of layer LI into the dry buffer ==
    a.deflabel(labs[PH_MIX])
    ld(m, LI, 10); a.shll2(10); m.imm32(LN[0], 1); a.add_rr(10, 1); a.movl_ld(1, 2)
    ld(m, POS, 3); a.sub(3, 2); m.imm32(CHUNK_MIX, 0); a.cmphi(0, 2); c2 = m.newlabel('c2'); a.bf_lbl(c2); a.mov_rm_rn(0, 2); a.deflabel(c2)
    kst(m, 2, M_N)
    a.movl_pool('rv_dry', 1); a.jsr(1); a.nop(); a.mov_rm_rn(3, 0); a.shll(0); a.add_rr(0, 4); kst(m, 4, M_DST)
    a.add_rr(2, 3); st(m, 3, POS); ld(m, DONEU, 0); a.add_rr(2, 0); st(m, 0, DONEU)
    cached_call(m, 'rv_kmix')
    kld(m, M_DONE, 0); a.tst_rr(0, 0); ldn = m.newlabel('ldn'); m.bf_far(ldn)
    ld(m, LI, 10); a.shll2(10); m.imm32(LN[0], 1); a.add_rr(10, 1); a.movl_ld(1, 2); ld(m, POS, 3); a.cmpeq(2, 3); m.bf_far(jx)
    a.deflabel(ldn)
    ld(m, LI, 0); a.add_imm(1, 0); st(m, 0, LI); ld(m, NL, 1); a.cmpeq(1, 0); nl_ = m.newlabel('nl'); m.bt_far(nl_)
    call(m, 'rv_layer'); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    a.deflabel(nl_); sti(m, PH_RINIT, PHASE); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()

    # == RINIT: settings of this render; header of the result; pass 0 (peak) ==
    a.deflabel(labs[PH_RINIT])
    call(m, 'px_set'); st(m, 9, RSET); st(m, 9, LASTSET)
    ld(m, EFX, 2); a.mov_imm(0, 0); a.tst_rr(2, 2); nt_ = m.newlabel('nt'); m.bf_far(nt_)
    rdb(m, PRESET, 4); a.shll2(4); a.movl_pool('rv_tails', 1); a.add_rr(4, 1); a.movl_ld(1, 0)
    a.deflabel(nt_)
    ndt = m.newlabel('ndt'); ld(m, EFX, 2); a.mov_imm(X_DLY, 1); a.cmpeq(1, 2); m.bf_far(ndt)
    call(m, 'dl_time')                                                       # DELAY: r0 = its tail
    a.deflabel(ndt); ld(m, NIN, 1); a.add_rr(1, 0)
    st(m, 0, NOUT)
    ld(m, DST, 9); a.movl_st_disp(0, 0x18, 9); a.add_imm(-1, 0); a.movl_st_disp(0, 0x24, 9)
    a.mov_imm(0, 0); a.movl_st_disp(0, 0x20, 9); a.movl_st_disp(0, 0x28, 9)
    ld(m, OST, 2); a.tst_rr(2, 2); mo2 = m.newlabel('mo2'); hd = m.newlabel('hd')
    a.bt_lbl(mo2)
    ld(m, NOUT, 0); m.imm32(GUARD, 1); a.add_rr(1, 0); a.movl_st_disp(0, 0x1C, 9); a.mov_imm(2, 3); m.imm32(176400, 5); a.mov_imm(4, 6); a.bra_lbl(hd); a.nop()
    a.deflabel(mo2); a.mov_imm(0, 0); a.movl_st_disp(0, 0x1C, 9); a.mov_imm(1, 3); m.imm32(88200, 5); a.mov_imm(2, 6)
    a.deflabel(hd)
    m.imm32(0x92, 1); a.add_rr(9, 1); a.movw_st(3, 1); m.imm32(0x98, 1); a.add_rr(9, 1); a.movl_st(5, 1)
    m.imm32(0x9C, 1); a.add_rr(9, 1); a.movw_st(6, 1)
    a.mov_imm(0, 0); call(m, 'rv_vinit')
    ld(m, NOUT, 0); a.shll(0); ld(m, RSZ, 1); a.add_rr(1, 0); st(m, 0, TOTU); sti(m, 0, DONEU)
    sti(m, 0, POS); call(m, 'px_next'); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()

    # == CLRR: ring = 0 ==
    a.deflabel(labs[PH_CLRR])
    m.imm32(RING, 2); ld(m, POS, 3); a.sub(3, 2); m.imm32(CHUNK_CLR, 0); a.cmphi(0, 2); c3 = m.newlabel('c3'); a.bf_lbl(c3); a.mov_rm_rn(0, 2); a.deflabel(c3)
    kst(m, 2, M_N); vld(m, 'ring', 4); a.mov_rm_rn(3, 0); a.shll(0); a.add_rr(0, 4); kst(m, 4, M_DST)
    a.add_rr(2, 3); st(m, 3, POS); ld(m, DONEU, 0); a.add_rr(2, 0); st(m, 0, DONEU)
    cached_call(m, 'rv_kclr')
    m.imm32(RING, 0); ld(m, POS, 3); a.cmpeq(0, 3); m.bf_far(jx)
    sti(m, 0, POS); sti(m, PH_REND, PHASE); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()

    # == REND: a chunk of the effect (pass 0 peak / pass 1 write) ==
    a.deflabel(labs[PH_REND])
    ld(m, EFX, 1); m.imm32(CHUNK_REV, 0); a.tst_rr(1, 1); ck = m.newlabel('ck'); a.bt_lbl(ck); m.imm32(CHUNK_EQ, 0); a.deflabel(ck)
    ld(m, NOUT, 2); ld(m, POS, 3); a.sub(3, 2); a.cmphi(0, 2); c4 = m.newlabel('c4'); a.bf_lbl(c4); a.mov_rm_rn(0, 2); a.deflabel(c4)
    vst(m, 2, 'n'); a.add_rr(2, 3); st(m, 3, POS); ld(m, DONEU, 0); a.add_rr(2, 0); st(m, 0, DONEU)
    ld(m, EFX, 0); a.tst_rr(0, 0); kr, ke = m.newlabel('kr'), m.newlabel('ke'); m.bt_far(kr)
    kq = m.newlabel('kq'); a.mov_imm(X_DLY, 1); a.cmpeq(1, 0); m.bf_far(kq)
    cached_call(m, 'dl_kern'); jmpl(m, ke)
    a.deflabel(kq); cached_call(m, 'eq_kern'); jmpl(m, ke)
    a.deflabel(kr); cached_call(m, 'rv_kverb')
    a.deflabel(ke)
    ld(m, NOUT, 0); ld(m, POS, 3); a.cmpeq(0, 3); m.bf_far(jx)
    vld(m, 'pass', 0); a.tst_rr(0, 0); w1 = m.newlabel('w1'); m.bf_far(w1)
    # gain: REVERB normalizes (peak -> 32767); EQ keeps the level unless it would clip
    vld(m, 'pk', 5); m.imm32(0x10000, 0); a.tst_rr(5, 5); g1 = m.newlabel('g1'); m.bt_far(g1)
    ld(m, EFX, 2); a.tst_rr(2, 2); rg = m.newlabel('rg'); a.bt_lbl(rg)
    m.imm32(32767, 1); a.cmphi(1, 5); m.bf_far(g1)
    a.deflabel(rg); m.imm32(0x7FFF0000, 4); pg._hcall(m, '__udiv')
    a.deflabel(g1); st(m, 0, SAVEPK)
    a.mov_imm(1, 0); call(m, 'rv_vinit'); ld(m, SAVEPK, 0); vst(m, 0, 'G')
    sti(m, 0, POS); call(m, 'px_next'); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    a.deflabel(w1)
    sti(m, ST_PREV, ST); sti(m, PH_PREV, PHASE)
    ld(m, DST, 4); osc(m, LOOP_GUARD)
    a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()

    # == PREV: nothing to do (waiting); FIN: keep ==
    a.deflabel(labs[PH_PREV]); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    a.deflabel(labs[PH_FIN])
    m.imm32(PLAYER, 4); osc(m, AUD_STOP)
    ld(m, DST, 9); ld(m, NOUT, 4); m.imm32(GUARD, 0); a.add_rr(0, 4); a.shll(4); ld(m, OST, 0); a.tst_rr(0, 0); fm = m.newlabel('fm'); a.bt_lbl(fm); a.shll(4)
    a.deflabel(fm); m.imm32(0x100 + 3, 0); a.add_rr(0, 4); a.mov_imm(-4, 0); a.and_rr(0, 4)
    a.movl_st(4, 9); a.mov_rm_rn(9, 0); a.add_rr(4, 0); a.mov_imm(0, 1); a.movl_st(1, 0)   # cut the work memory off
    ld(m, DST, 4); osc(m, LOOP_GUARD)
    ld(m, SEL, 9); a.add_imm(-1, 9); padblk(m, 9, 11)
    ld(m, DST, 8); a.mov_rm_rn(8, 2); a.add_imm(4, 2); a.mov_rm_rn(11, 3); a.mov_imm(16, 4)
    nc = m.newlabel('nc'); a.deflabel(nc); a.movb_ld(2, 0); a.movb_st(0, 3); a.add_imm(1, 2); a.add_imm(1, 3); a.dt(4); a.bf_lbl(nc)
    ld(m, MAXLV, 0); a.mov_rm_rn(11, 1); a.add_imm(0x11, 1); a.movb_st(0, 1)
    a.mov_imm(0, 0); a.add_imm(1, 1); a.movb_st(0, 1); a.mov_imm(127, 0); a.add_imm(1, 1); a.movb_st(0, 1)
    a.mov_imm(0, 0); a.mov_rm_rn(11, 1); a.add_imm(0x14, 1); a.movw_st(0, 1)
    for l in (1, 2, 3): a.mov_rm_rn(11, 1); a.add_imm(l * LAYER, 1); a.movb_st(0, 1)
    sti(m, ST_IDLE, ST)
    m.imm32(MSGB, 12)
    for ch in "DONE ": a.mov_imm(ord(ch), 0); a.movb_st(0, 12); a.add_imm(1, 12)
    a.mov_rm_rn(8, 2); a.add_imm(4, 2); a.mov_imm(11, 4)
    dc = m.newlabel('dc'); dz = m.newlabel('dz'); a.deflabel(dc); a.movb_ld(2, 0); a.tst_rr(0, 0); a.bf_lbl(dz); a.mov_imm(ord(' '), 0)
    a.deflabel(dz); a.movb_st(0, 12); a.add_imm(1, 12); a.add_imm(1, 2); a.dt(4); a.bf_lbl(dc)
    a.mov_imm(0, 0); a.movb_st(0, 12); call(m, 'px_msg')
    a.deflabel(jx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- px_next: after RINIT / pass 0: REVERB clears its ring first, the EQ renders at once. r0 r1 r2 ----
    a.deflabel('px_next')                                                    # (DELAY: its line is the ring, cleared)
    ld(m, EFX, 2); a.mov_imm(X_EQ, 1); a.cmpeq(1, 2); nr = m.newlabel('nr'); a.bf_lbl(nr)
    sti(m, PH_REND, PHASE); a.rts(); a.nop()
    a.deflabel(nr); sti(m, PH_CLRR, PHASE); a.rts(); a.nop()
    m.pool_jump()

    # ---- rv_size: r4 = block size = 0x100 + 4*NMAX + 2*NIN (+align) + ring (RSZ bytes). r0-r1 ----
    a.deflabel('rv_size')
    ld(m, NMAX, 4); a.shll2(4); ld(m, NIN, 0); a.shll(0); a.add_rr(0, 4); m.imm32(0x100 + 3, 0); a.add_rr(0, 4)
    ld(m, RSZ, 0); a.add_rr(0, 4)
    a.mov_imm(-4, 0); a.and_rr(0, 4); a.rts(); a.nop()
    # ---- rv_dry: r4 = dry buffer address = DST + 0x100 + 4*NMAX. r0-r1 ----
    a.deflabel('rv_dry')
    ld(m, DST, 4); m.imm32(0x100, 0); a.add_rr(0, 4); ld(m, NMAX, 0); a.shll2(0); a.add_rr(0, 4); a.rts(); a.nop()
    m.pool_jump()

    # ---- rv_vinit: r0 = pass. Reverb variables: state 0, preset params, ring/dry/out pointers; EQ: eq_init ----
    a.deflabel('rv_vinit'); a.stsl_pr()
    a.mov_rm_rn(0, 8)
    m.imm32(VB, 1); m.imm32(PBASE, 2); a.mov_imm(0, 0)
    zl = m.newlabel('zl'); a.deflabel(zl); a.movl_st(0, 1); a.add_imm(4, 1); a.dt(2); a.bf_lbl(zl)
    rdb(m, PRESET, 4); m.imm32(len(D.params) * 4, 0); a.mul_l(0, 4); a.sts_macl(4); a.movl_pool('rv_ptab', 2); a.add_rr(4, 2)
    m.imm32(VA(D.params[0]), 1); m.imm32(len(D.params), 3)
    pl = m.newlabel('pl'); a.deflabel(pl); a.movl_ld(2, 0); a.movl_st(0, 1); a.add_imm(4, 1); a.add_imm(4, 2); a.dt(3); a.bf_lbl(pl)
    m.imm32(0x80000000, 0); vst(m, 0, 'phB')
    ld(m, MIX, 0); m.imm32(MIXK, 1); a.mul_l(1, 0); a.sts_macl(4); a.mov_imm(100, 5); pg._hcall(m, '__udiv'); vst(m, 0, 'mixq')
    ld(m, OST, 0); vst(m, 0, 'stereo')
    vst(m, 8, 'pass'); m.imm32(0x10000, 0); vst(m, 0, 'G')
    call(m, 'rv_dry'); vst(m, 4, 'dptr'); ld(m, NIN, 0); vst(m, 0, 'dcnt')
    ld(m, NIN, 0); a.shll(0); a.add_rr(0, 4); m.imm32(3, 0); a.add_rr(0, 4); a.mov_imm(-4, 0); a.and_rr(0, 4); vst(m, 4, 'ring')
    ld(m, DST, 4); m.imm32(0x100, 0); a.add_rr(0, 4); vst(m, 4, 'outL')
    ld(m, OST, 0); a.tst_rr(0, 0); a.mov_imm(0, 0); om = m.newlabel('om'); a.bt_lbl(om)
    ld(m, NOUT, 0); m.imm32(GUARD, 1); a.add_rr(1, 0); a.shll(0); a.add_rr(4, 0)
    a.deflabel(om); vst(m, 0, 'outR')
    ld(m, EFX, 0); a.tst_rr(0, 0); ne = m.newlabel('ne'); m.bt_far(ne)
    nd_ = m.newlabel('nd'); a.mov_imm(X_DLY, 1); a.cmpeq(1, 0); m.bf_far(nd_); call(m, 'dl_init'); jmpl(m, ne)
    a.deflabel(nd_); call(m, 'eq_init'); a.deflabel(ne)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- dl_time: the delay time from TIME (+ the sequence tempo) -> DLB DT; r0 = the tail (frames) ----
    a.deflabel('dl_time'); a.stsl_pr()
    m.imm32(DLB + D_MSG, 4); a.mov_imm(0x10, 0); a.movl_st(0, 4); a.mov_imm(0, 0); a.movl_st_disp(0, 4, 4)
    a.movl_st_disp(0, 8, 4); m.imm32(ID_TEMPO, 0); a.movl_st_disp(0, 12, 4)
    m.imm32(SYS_PARAM, 2); a.jsr(2); a.nop()
    m.imm32(DLB + D_MSG, 1); a.movl_ld_disp(1, 4, 5)                                  # tempo x 10
    m.imm32(1200, 0); m.imm32(300, 3); a.cmpge(3, 5); tb = m.newlabel('tb'); a.bf_lbl(tb)
    m.imm32(3000, 3); a.cmpgt(3, 5); a.bt_lbl(tb); a.mov_rm_rn(5, 0)
    a.deflabel(tb); a.mov_rm_rn(0, 5); m.imm32(26460000, 4); pg._hcall(m, '__udiv')     # r0 = frames per beat
    rdb(m, DTIME, 4); tms = m.newlabel('tms'); tok = m.newlabel('tok')
    a.mov_imm(IX.NNOTE, 1); a.cmpge(1, 4); m.bt_far(tms)
    a.shll2(4); a.movl_pool('dl_tmul', 1); a.add_rr(4, 1); a.movl_ld(1, 1); a.mul_l(1, 0); a.sts_macl(0)
    a.shlr8(0); a.shlr2(0); a.shlr2(0); jmpl(m, tok)
    a.deflabel(tms); a.add_imm(-IX.NNOTE, 4); a.shll2(4); a.movl_pool('dl_tms', 1); a.add_rr(4, 1); a.movl_ld(1, 0)
    a.deflabel(tok)
    t1, t2 = m.newlabel('t1'), m.newlabel('t2')
    a.mov_imm(1, 1); a.cmpge(1, 0); a.bt_lbl(t1); a.mov_rm_rn(1, 0); a.deflabel(t1)
    m.imm32(RING - 2, 1); a.cmpgt(1, 0); a.bf_lbl(t2); a.mov_rm_rn(1, 0); a.deflabel(t2)
    m.imm32(DLB + D_DT, 1); a.movl_st(0, 1)
    rdb(m, DFB, 1); a.shll2(1); a.movl_pool('dl_nrep', 2); a.add_rr(1, 2); a.movl_ld(2, 1)
    a.mul_l(1, 0); a.sts_macl(0)                                                      # DT * (repeats + 1)
    m.imm32(DL_TAILMAX, 1); t3 = m.newlabel('t3'); a.cmphi(1, 0); a.bf_lbl(t3); a.mov_rm_rn(1, 0); a.deflabel(t3)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- dl_init: delay states 0, coefficients of the preset and the fields; LEFT = NOUT (fade). r0 r1 r4 r13 ----
    a.deflabel('dl_init')
    m.imm32(DLB, 13); a.mov_imm(0, 0)
    for o in (D_PH, D_DW, D_LP, D_HP): a.movl_st_disp(0, o, 13)
    for f_, o in ((DFB, D_FB), (DMIX, D_MIX)):
        rdb(m, f_, 4); a.shll2(4); a.movl_pool('dl_pq', 1); a.add_rr(4, 1); a.movl_ld(1, 0); a.movl_st_disp(0, o, 13)
    rdb(m, DPRE, 4); a.shll2(4); a.shll2(4); a.movl_pool('dl_co', 1); a.add_rr(4, 1)
    for k, o in enumerate((D_CLP, D_CHP, D_MODD, D_MODINC)): a.movl_ld_disp(1, 4 * k, 0); a.movl_st_disp(0, o, 13)
    ld(m, NOUT, 0); m.imm32(DLB + D_LEFT, 1); a.movl_st(0, 1)
    a.rts(); a.nop()
    m.pool_jump()
    # ---- eq_init: EQ states 0, coefficients from the band settings (tables). r0 r1 r4 r13 ----
    a.deflabel('eq_init')
    m.imm32(EQB, 13); a.mov_imm(0, 0)
    for k in range(6): a.movl_st_disp(0, 4 * k, 13)
    for f_, tb, off in ((LOWF, 'eq_lc', E_CL), (LOWG, 'eq_g1', E_GL), (MIDG, 'eq_g1', E_GM), (HIGHF, 'eq_hc', E_CH), (HIGHG, 'eq_g1', E_GH)):
        rdb(m, f_, 4); a.shll2(4); a.movl_pool(tb, 1); a.add_rr(4, 1); a.movl_ld(1, 0); a.movl_st_disp(0, off, 13)
    rdb(m, MIDF, 4); a.mov_imm(len(QS), 0); a.mul_l(0, 4); a.sts_macl(4); rdb(m, QF, 0); a.add_rr(0, 4); a.shll2(4); a.shll(4)
    a.movl_pool('eq_bp', 1); a.add_rr(4, 1); a.movl_ld(1, 0); a.movl_st_disp(0, E_B0, 13)
    a.movl_ld_disp(1, 4, 0); a.movl_st_disp(0, E_NA1, 13)
    a.movl_ld_disp(13, E_B0, 0); a.shll(0); m.imm32(1 << 28, 1); a.sub(1, 0); a.movl_st_disp(0, E_NA2, 13)
    a.rts(); a.nop()
    m.pool_jump()

    # ---- rv_layer: kernel parameters of layer LI into the mix kernel (as S500) ----
    a.deflabel('rv_layer')
    ld(m, LI, 10); a.shll2(10)
    m.imm32(LSRC[0], 1); a.add_rr(10, 1); a.movl_ld(1, 8)
    m.imm32(0x100, 2); a.add_rr(8, 2); kst(m, 2, M_LBASE)
    m.imm32(0x92, 3); a.add_rr(8, 3); a.movw_ld(3, 0); a.extu_w(0, 0); a.mov_imm(2, 3); a.cmpeq(3, 0)
    a.mov_imm(0, 0); mono = m.newlabel('mono'); a.bf_lbl(mono)
    a.movl_ld_disp(8, 0x1C, 0); a.shll(0); a.add_rr(2, 0)
    a.deflabel(mono); kst(m, 0, M_RBASE)
    m.imm32(LSTART[0], 1); a.add_rr(10, 1); a.movl_ld(1, 0); kst(m, 0, M_I)
    m.imm32(LEND[0], 1); a.add_rr(10, 1); a.movl_ld(1, 0); kst(m, 0, M_END)
    m.imm32(LSTEP[0], 1); a.add_rr(10, 1); a.movl_ld(1, 0); a.mov_rm_rn(0, 2); a.shlr16(2); kst(m, 2, M_SI)
    m.imm32(0xFFFF, 2); a.and_rr(2, 0); kst(m, 0, M_SF)
    m.imm32(LGAIN[0], 1); a.add_rr(10, 1); a.movl_ld(1, 0); kst(m, 0, M_G)
    a.mov_imm(0, 0); kst(m, 0, M_F); kst(m, 0, M_DONE); sti(m, 0, POS)
    a.rts(); a.nop()
    m.pool_jump()

    # ---- rv_pow: r4 = cents -> r0 = 2^(c/1200), r1 = 2^(-c/1200) (Q16) (as S500) ----
    a.deflabel('rv_pow'); a.stsl_pr()
    m.imm32(-3600, 0); lo_ = m.newlabel('lo'); a.cmpge(0, 4); a.bt_lbl(lo_); a.mov_rm_rn(0, 4); a.deflabel(lo_)
    m.imm32(6000, 0); hi_ = m.newlabel('hi'); a.cmpgt(0, 4); a.bf_lbl(hi_); a.mov_rm_rn(0, 4); a.deflabel(hi_)
    m.imm32(6000, 0); a.add_rr(0, 4); a.mov_imm(100, 5); pg._hcall(m, '__udiv')
    a.mov_rm_rn(0, 6); a.mov_rm_rn(1, 7)
    a.mov_rm_rn(6, 4); a.bsr_lbl('rv_semi'); a.nop()
    a.movl_pool('rv_cent', 2); a.mov_rm_rn(7, 0); a.shll2(0); a.add_rr(0, 2); a.movl_ld(2, 5)
    a.dmulu_l(4, 5); a.sts_mach(0); a.sts_macl(3); a.shll16(0); a.shlr16(3); a.or_rr(3, 0); a.movl_st_dec(0, 15)
    m.imm32(120, 4); a.sub(6, 4); a.bsr_lbl('rv_semi'); a.nop()
    a.movl_pool('rv_centi', 2); a.mov_rm_rn(7, 0); a.shll2(0); a.add_rr(0, 2); a.movl_ld(2, 5)
    a.dmulu_l(4, 5); a.sts_mach(1); a.sts_macl(3); a.shll16(1); a.shlr16(3); a.or_rr(3, 1)
    a.movl_ld_inc(15, 0)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- rv_semi: r4 = x (0..120) -> r4 = 2^((x - 60) / 12) Q16 = SEMI12[x % 12] >> (13 - x // 12). r0 r1 r2 r5 ----
    a.deflabel('rv_semi'); a.stsl_pr()
    a.mov_imm(12, 5); pg._hcall(m, '__udiv')
    a.shll2(1); a.movl_pool('rv_semi12', 2); a.add_rr(1, 2); a.movl_ld(2, 4)
    a.add_imm(-13, 0); a.shad(0, 4)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- icinv ----
    a.deflabel('icinv')
    a.movl_pool('__cstart', 4); m.imm32(0xFFFFFFF0, 1); a.and_rr(1, 4)
    m.imm32(CACHE_SPAN // 16 + 1, 5); m.imm32(0xF0000008, 6); m.imm32(0x1FFFFC00, 7); m.imm32(0xFF0, 3)
    icl = m.newlabel('icl'); a.deflabel(icl)
    a.mov_rm_rn(4, 0); a.and_rr(3, 0); a.or_rr(6, 0); a.mov_rm_rn(4, 1); a.and_rr(7, 1); a.movl_st(1, 0)
    a.add_imm(16, 4); a.dt(5); a.bf_lbl(icl)
    a.rts(); a.nop()
    m.pool_jump()

    # ======================== kernels (cached alias, PC-relative only) ========================
    a.deflabel('__cstart')
    a.deflabel('rv_kclr')
    m.imm32(KB, 12); a.movl_ld_disp(12, M_DST, 3); a.movl_ld_disp(12, M_N, 4)
    cz = m.newlabel('cz'); a.tst_rr(4, 4); a.bt_lbl(cz); a.mov_imm(0, 0)
    clp = m.newlabel('clp'); a.deflabel(clp); a.movw_st(0, 3); a.add_imm(2, 3); a.dt(4); a.bf_lbl(clp)
    a.deflabel(cz); a.rts(); a.nop()
    m.pool_jump()
    # mix kernel (S500 s9_kmix)
    a.deflabel('rv_kmix'); a.stsl_pr()
    m.imm32(KB, 12)
    a.movl_ld_disp(12, M_LBASE, 1); a.movl_ld_disp(12, M_RBASE, 2); a.movl_ld_disp(12, M_DST, 3)
    a.movl_ld_disp(12, M_N, 4); a.movl_ld_disp(12, M_I, 5); a.movl_ld_disp(12, M_F, 6)
    a.movl_ld_disp(12, M_SI, 7); a.movl_ld_disp(12, M_SF, 8); a.movl_ld_disp(12, M_END, 9)
    a.movl_ld_disp(12, M_G, 10); a.mov_imm(-16, 14)
    kend = m.newlabel('kend'); a.tst_rr(4, 4); m.bt_far(kend)
    kl = m.newlabel('kl'); a.deflabel(kl)
    a.mov_rm_rn(5, 0); a.shll(0); a.mov_rm_rn(1, 13); a.add_rr(0, 13); a.movw_ld(13, 11)
    km = m.newlabel('km'); a.tst_rr(2, 2); a.bt_lbl(km)
    a.mov_rm_rn(2, 13); a.add_rr(0, 13); a.movw_ld(13, 13); a.add_rr(13, 11); a.shar(11)
    a.deflabel(km)
    a.mul_l(10, 11); a.sts_macl(11); a.shad(14, 11)
    a.movw_ld(3, 13); a.add_rr(13, 11)
    m.imm32(32767, 13); k1 = m.newlabel('k1'); a.cmpgt(13, 11); a.bf_lbl(k1); a.mov_rm_rn(13, 11); a.deflabel(k1)
    m.imm32(-32768, 13); k2 = m.newlabel('k2'); a.cmpgt(11, 13); a.bf_lbl(k2); a.mov_rm_rn(13, 11); a.deflabel(k2)
    a.movw_st(11, 3); a.add_imm(2, 3)
    a.add_rr(8, 6); m.imm32(0x10000, 13); na = m.newlabel('na'); a.cmphs(13, 6); a.bf_lbl(na); a.sub(13, 6); a.add_imm(1, 5)
    a.deflabel(na); a.add_rr(7, 5)
    a.cmphi(9, 5); kd = m.newlabel('kd'); a.bt_lbl(kd)
    a.dt(4); kn = m.newlabel('kn'); a.bt_lbl(kn); a.bra_lbl(kl); a.nop()
    a.deflabel(kd); a.mov_imm(1, 0); a.movl_st_disp(0, M_DONE, 12)
    a.deflabel(kn); a.deflabel(kend)
    a.movl_st_disp(5, M_I, 12); a.movl_st_disp(6, M_F, 12)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    emit_keq(m)
    emit_kverb(m)
    emit_kdl(m)
    a.deflabel('__cend')

    # ---- tables ----
    def table(name, words):
        m.pool_jump()
        while (len(a.words) * 2 + a.base) & 3: a.nop()
        a.deflabel(name)
        for w in words: a._emit(w & 0xFFFF); a._emit((w >> 16) & 0xFFFF)
    skip = m.newlabel('tskip'); jmpl(m, skip)
    table('rv_ptab', [v for row in PTAB for v in row]); table('rv_tails', TAILS); table('rv_mixdef', MIXDEF)
    table('rv_semi12', SEMI12); table('rv_cent', CENT); table('rv_centi', CENTI)
    table('eq_g1', EQ_G1); table('eq_lc', EQ_LC); table('eq_hc', EQ_HC); table('eq_bp', EQ_BP); table('eq_ptab', EQ_PTAB)
    table('dl_pdef_t', DL_PDEF); table('dl_co', [v for row in DL_CO for v in row]); table('dl_pq', DL_PQ)
    table('dl_nrep', [n + 1 for n in DL_NREP]); table('dl_tmul', IX.TMUL); table('dl_tms', IX.TMS)
    lbo = lambda k: pgP.addr['dlabels'][k] - 0x28                       # the label object of a dlabel
    a.deflabel('vt_eqp')
    for v in (pgP.addr['page'], BAND, PLASTF, len(PCURSOR)): tword(m, v)
    for s_ in PCURSOR:
        tword(m, pgP.addr['values'][fidp(pgP, s_)]); tword(m, sum(1 << b for b in PVIS[s_]) if s_ in PVIS else 0xFFFFFFFF)
    tword(m, 1); tword(m, lbo(QLBL)); tword(m, 1 << B_MID)
    for s_, lbl in MSTR.items():
        a.deflabel(lbl); b_ = s_.ljust(16).encode() + b'\0\0'
        for i in range(0, 18, 2): a._emit(b_[i] | (b_[i + 1] << 8))
    a.deflabel(skip)

def vis_code(m):
    """vis_tab (as master.py, HW): r4 = table: page, selector slot, LASTF slot, n, n x (widget, screen mask | -1 =
    always), m, m x (object, mask). Fields shown only on their screens, the cursor skips hidden fields, LASTF, redraw."""
    a = m.a
    a.deflabel('vis_tab'); a.stsl_pr()
    a.mov_rm_rn(4, 8); a.mov_imm(0, 14)
    a.movl_ld_disp(8, 4, 0); a.movb_ld(0, 0); a.extu_b(0, 0); a.mov_imm(1, 9); a.shad(0, 9)     # r9 = screen bit
    a.movl_ld_disp(8, 12, 10); a.mov_rm_rn(8, 11); a.add_imm(16, 11)
    a.bsr_lbl('vt_flags'); a.nop()
    a.movl_ld_inc(11, 10); a.bsr_lbl('vt_flags'); a.nop()
    vdone, vpick, vgo = m.newlabel('vd'), m.newlabel('vp'), m.newlabel('vg')
    a.movl_ld(8, 0); a.movl_ld_disp(0, 0x24, 12)                                              # r12 = focused
    a.mov_rm_rn(12, 4); a.bsr_lbl('vt_index'); a.nop(); a.mov_rm_rn(0, 13)                   # r13 = its index
    a.cmppz(13); m.bf_far(vdone)
    a.mov_rm_rn(12, 1); a.add_imm(0x17, 1); a.movb_ld(1, 0); a.tst_imm(0x80); m.bf_far(vdone)  # visible: nothing to do
    a.movl_ld_disp(8, 8, 0); a.movl_ld(0, 10)                                                 # r10 = last focused
    a.mov_rm_rn(10, 4); a.bsr_lbl('vt_index'); a.nop(); a.mov_rm_rn(0, 7)                    # r7 = its index
    a.mov_imm(0, 6)
    a.cmppz(7); m.bf_far(vpick)
    a.movl_ld_disp(8, 12, 5)                                                                  # r5 = n
    bwd, fl, bl = m.newlabel('bw'), m.newlabel('fl'), m.newlabel('bl')
    a.cmpgt(7, 13); m.bf_far(bwd)
    a.mov_rm_rn(7, 3)                                                                         # forward: first visible after last
    a.deflabel(fl); a.add_imm(1, 3); a.cmpge(5, 3); m.bt_far(vpick)
    a.mov_rm_rn(3, 0); a.shll2(0); a.shll(0); a.add_rr(8, 0); a.movl_ld_disp(0, 16, 1)
    a.mov_rm_rn(1, 2); a.add_imm(0x17, 2); a.movb_ld(2, 0); a.tst_imm(0x80); a.bt_lbl(fl)
    a.mov_rm_rn(1, 6); a.bra_lbl(vpick); a.nop()
    a.deflabel(bwd); a.mov_rm_rn(7, 3)                                                        # backward: last visible before
    a.deflabel(bl); a.add_imm(-1, 3); a.cmppz(3); m.bf_far(vpick)
    a.mov_rm_rn(3, 0); a.shll2(0); a.shll(0); a.add_rr(8, 0); a.movl_ld_disp(0, 16, 1)
    a.mov_rm_rn(1, 2); a.add_imm(0x17, 2); a.movb_ld(2, 0); a.tst_imm(0x80); a.bt_lbl(bl)
    a.mov_rm_rn(1, 6)
    a.deflabel(vpick)
    a.tst_rr(6, 6); m.bf_far(vgo)
    a.mov_rm_rn(10, 6); a.cmppz(7); m.bt_far(vgo); a.movl_ld_disp(8, 16, 6)
    a.deflabel(vgo)
    a.movl_ld(8, 4); a.mov_rm_rn(6, 5); m.imm32(SET_FOCUS, 2); a.jsr(2); a.nop(); a.mov_imm(1, 14)
    a.deflabel(vdone)
    a.movl_ld(8, 0); a.movl_ld_disp(0, 0x24, 0); a.movl_ld_disp(8, 8, 1); a.movl_st(0, 1)     # LASTF
    a.tst_rr(14, 14); vr = m.newlabel('vr'); a.bt_lbl(vr); redraw(m)
    a.deflabel(vr); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # vt_flags: r10 entries at r11 (advances); r9 = screen bit; r14 = 1 if a flag changed. r0 r1 r2 r3
    a.deflabel('vt_flags')
    fe, fn, fs = m.newlabel('fe'), m.newlabel('fn'), m.newlabel('fs')
    a.tst_rr(10, 10); a.bt_lbl(fe)
    a.deflabel(fn)
    a.movl_ld_inc(11, 1); a.movl_ld_inc(11, 2); a.mov_imm(-1, 0); a.cmpeq(0, 2); a.bt_lbl(fs)
    a.add_imm(0x17, 1); a.movb_ld(1, 0); a.extu_b(0, 0); a.mov_rm_rn(0, 3); a.and_imm(0x7F)
    a.tst_rr(9, 2); ns_ = m.newlabel('nv'); a.bt_lbl(ns_); a.or_imm(0x80); a.deflabel(ns_)
    a.cmpeq(0, 3); a.bt_lbl(fs); a.movb_st(0, 1); a.mov_imm(1, 14)
    a.deflabel(fs); a.dt(10); a.bf_lbl(fn)
    a.deflabel(fe); a.rts(); a.nop()
    # vt_index: r0 = index of widget r4 in the table r8's cursor list, -1 if none. r1 r2 r3
    a.deflabel('vt_index')
    a.mov_imm(-1, 0); a.movl_ld_disp(8, 12, 2); a.mov_rm_rn(8, 1); a.add_imm(16, 1); a.mov_imm(0, 3)
    il, ie = m.newlabel('il'), m.newlabel('ie')
    a.tst_rr(2, 2); a.bt_lbl(ie)
    a.deflabel(il); a.movl_ld(1, 0); a.cmpeq(4, 0); fi = m.newlabel('fi'); a.bt_lbl(fi)
    a.add_imm(8, 1); a.add_imm(1, 3); a.dt(2); a.bf_lbl(il)
    a.deflabel(ie); a.rts(); a.mov_imm(-1, 0)
    a.deflabel(fi); a.rts(); a.mov_rm_rn(3, 0)
    m.pool_jump()

# ---------------------------------------- the EQ kernel (model: eq_model) ----------------------------------------
def emit_keq(m):
    """n samples: dry (dptr/dcnt) -> low shelf -> mid bell -> high shelf -> pass 0: peak / pass 1: level G, outL.
    r14 = EQB, r13 dptr, r12 dcnt, r11 outL, r10 n, r9 pk, r8 G, r7 pass; r0-r6 temps"""
    a = m.a
    def m16(rc_off, rs, rd):            # rd = (rs * EQB[rc_off]) >> 16 (64-bit product). r0 r2
        a.movl_ld_disp(14, rc_off, 2); dmuls(m, 2, rs); a.sts_mach(0); a.sts_macl(rd); xtrct(m, 0, rd)
    a.deflabel('eq_kern'); a.stsl_pr()
    m.imm32(EQB, 14)
    for n, r in (('dptr', 13), ('dcnt', 12), ('outL', 11), ('n', 10), ('pk', 9), ('G', 8), ('pass', 7)):
        m.imm32(VA(n), 1); a.movl_ld(1, r)
    qe = m.newlabel('qe'); a.tst_rr(10, 10); m.bt_far(qe)
    m.pool_jump()
    top = m.newlabel('qt'); a.deflabel(top)
    a.mov_imm(0, 5); dz = m.newlabel('qz'); a.tst_rr(12, 12); a.bt_lbl(dz)
    a.movw_ld(13, 5); a.add_imm(2, 13); a.add_imm(-1, 12)
    a.deflabel(dz); a.shll8(5)                                                # x (24 bit)
    # low shelf: lz += (x - lz) * CL; x += lz * (G - 1)
    a.movl_ld_disp(14, E_LZ, 6); a.mov_rm_rn(5, 1); a.sub(6, 1); m16(E_CL, 1, 3); a.add_rr(3, 6); a.movl_st_disp(6, E_LZ, 14)
    m16(E_GL, 6, 3); a.add_rr(3, 5)
    # mid: bp = (B0 (x - x2) + NA1 y1 + NA2 y2) >> 28 (64-bit sum); x += bp * (G - 1)
    a.movl_ld_disp(14, E_X2, 1); a.mov_rm_rn(5, 6); a.sub(1, 6)
    a.movl_ld_disp(14, E_B0, 2); dmuls(m, 2, 6); a.sts_mach(3); a.sts_macl(4)
    a.movl_ld_disp(14, E_NA1, 2); a.movl_ld_disp(14, E_Y1, 1); dmuls(m, 2, 1); a.sts_mach(0); a.sts_macl(6)
    a.clrt(); addc(m, 6, 4); addc(m, 0, 3)
    a.movl_ld_disp(14, E_NA2, 2); a.movl_ld_disp(14, E_Y2, 6); dmuls(m, 2, 6); a.sts_mach(0); a.sts_macl(6)
    a.clrt(); addc(m, 6, 4); addc(m, 0, 3)
    a.shll2(3); a.shll2(3); a.shlr16(4); a.shlr8(4); a.shlr2(4); a.shlr2(4); a.or_rr(4, 3)       # r3 = bp
    a.movl_ld_disp(14, E_X1, 0); a.movl_st_disp(0, E_X2, 14); a.movl_st_disp(5, E_X1, 14)
    a.movl_st_disp(1, E_Y2, 14); a.movl_st_disp(3, E_Y1, 14)
    m16(E_GM, 3, 6); a.add_rr(6, 5)
    # high shelf: hz += (x - hz) * CH; x += (x - hz) * (G - 1)
    a.movl_ld_disp(14, E_HZ, 6); a.mov_rm_rn(5, 1); a.sub(6, 1); m16(E_CH, 1, 3); a.add_rr(3, 6); a.movl_st_disp(6, E_HZ, 14)
    a.mov_rm_rn(5, 1); a.sub(6, 1); m16(E_GH, 1, 3); a.add_rr(3, 5)
    a.mov_imm(-8, 0); a.shad(0, 5)                                            # o = x >> 8
    wr, nx = m.newlabel('qw'), m.newlabel('qn')
    a.tst_rr(7, 7); a.bf_lbl(wr)
    a.mov_rm_rn(5, 0); p_ = m.newlabel('qp'); a.cmppz(0); a.bt_lbl(p_); a.neg(0, 0); a.deflabel(p_)
    n_ = m.newlabel('qk'); a.cmphi(9, 0); a.bf_lbl(n_); a.mov_rm_rn(0, 9); a.deflabel(n_)
    a.bra_lbl(nx); a.nop()
    a.deflabel(wr)
    a.mov_imm(0, 6); s_ = m.newlabel('qs'); a.cmppz(5); a.bt_lbl(s_); a.neg(5, 5); a.mov_imm(1, 6); a.deflabel(s_)
    a.dmulu_l(8, 5); a.sts_mach(0); a.sts_macl(5); a.shll16(0); a.shlr16(5); a.or_rr(0, 5)
    m.imm32(32767, 1); c_ = m.newlabel('qc'); a.cmphi(1, 5); a.bf_lbl(c_); a.mov_rm_rn(1, 5); a.deflabel(c_)
    g_ = m.newlabel('qg'); a.tst_rr(6, 6); a.bt_lbl(g_); a.neg(5, 5); a.deflabel(g_)
    a.movw_st(5, 11); a.add_imm(2, 11)
    a.deflabel(nx)
    a.dt(10); m.bf_far(top)
    a.deflabel(qe)
    for n, r in (('dptr', 13), ('dcnt', 12), ('outL', 11), ('n', 10), ('pk', 9)):
        m.imm32(VA(n), 1); a.movl_st(r, 1)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

# ---------------------------------------- the delay kernel (model: dl_model) ----------------------------------------
def emit_kdl(m):
    """n samples: dry (dptr/dcnt, then 0) -> wow read of the line (ring) -> LP -> HP -> feedback into the line, wet *
    MIX (faded at the end) + dry -> pass 0: peak / pass 1: level G, outL.
    r14 = DLB, r13 dptr, r12 dcnt, r11 outL, r10 n, r9 pk, r8 G, r7 pass, r6 ring; r0-r5 temps"""
    a = m.a
    a.deflabel('dl_kern'); a.stsl_pr()
    m.imm32(DLB, 14)
    for n, r in (('dptr', 13), ('dcnt', 12), ('outL', 11), ('n', 10), ('pk', 9), ('G', 8), ('pass', 7), ('ring', 6)):
        m.imm32(VA(n), 1); a.movl_ld(1, r)
    de = m.newlabel('de'); a.tst_rr(10, 10); m.bt_far(de)
    m.pool_jump()
    top = m.newlabel('dt'); a.deflabel(top)
    a.mov_imm(0, 5); dz = m.newlabel('dz'); a.tst_rr(12, 12); a.bt_lbl(dz)
    a.movw_ld(13, 5); a.add_imm(2, 13); a.add_imm(-1, 12)
    a.deflabel(dz)                                                            # r5 = x
    a.movl_ld_disp(14, D_PH, 0); a.movl_ld_disp(14, D_MODINC, 1); a.add_rr(1, 0); a.movl_st_disp(0, D_PH, 14)
    a.shlr16(0); m.imm32(0x8000, 1); nf = m.newlabel('nf'); a.cmphs(1, 0); a.bf_lbl(nf); a.not_rr(0, 0); a.extu_w(0, 0)
    a.deflabel(nf)                                                            # r0 = triangle 0..0x7FFF
    a.movl_ld_disp(14, D_MODD, 1); a.mul_l(1, 0); a.sts_macl(0); a.mov_imm(-15, 1); a.shad(1, 0)   # off (Q8 frames)
    a.mov_rm_rn(0, 3); a.and_imm(255); a.mov_rm_rn(0, 2); a.shlr8(3)          # r2 = f, r3 = whole frames
    a.movl_ld_disp(14, D_DW, 4); a.movl_ld_disp(14, D_DT, 1); a.sub(1, 4); a.sub(3, 4); a.extu_w(4, 4)   # i0
    a.mov_rm_rn(4, 1); a.shll(1); a.add_rr(6, 1); a.movw_ld(1, 0)             # a
    a.add_imm(-1, 4); a.extu_w(4, 4); a.shll(4); a.add_rr(6, 4); a.movw_ld(4, 1)   # b
    a.sub(0, 1); a.mul_l(2, 1); a.sts_macl(1); a.mov_imm(-8, 3); a.shad(3, 1); a.add_rr(0, 1)   # r1 = d
    a.movl_ld_disp(14, D_LP, 0); a.mov_rm_rn(1, 3); a.sub(0, 3); a.movl_ld_disp(14, D_CLP, 4); a.mul_l(4, 3); a.sts_macl(3)
    a.mov_imm(-15, 4); a.shad(4, 3); a.add_rr(3, 0); a.movl_st_disp(0, D_LP, 14)             # r0 = LP
    a.movl_ld_disp(14, D_HP, 1); a.mov_rm_rn(0, 3); a.sub(1, 3); a.movl_ld_disp(14, D_CHP, 4); a.mul_l(4, 3); a.sts_macl(3)
    a.mov_imm(-15, 4); a.shad(4, 3); a.add_rr(3, 1); a.movl_st_disp(1, D_HP, 14)             # r1 = HP
    a.sub(1, 0)                                                               # r0 = fl
    m.pool_jump()
    a.movl_ld_disp(14, D_FB, 1); a.mov_rm_rn(0, 3); a.mul_l(1, 3); a.sts_macl(3); a.mov_imm(-12, 4); a.shad(4, 3); a.add_rr(5, 3)
    m.imm32(32767, 1); c1 = m.newlabel('c1'); a.cmpgt(1, 3); a.bf_lbl(c1); a.mov_rm_rn(1, 3); a.deflabel(c1)
    m.imm32(-32768, 1); c2 = m.newlabel('c2'); a.cmpgt(3, 1); a.bf_lbl(c2); a.mov_rm_rn(1, 3); a.deflabel(c2)
    a.movl_ld_disp(14, D_DW, 4); a.mov_rm_rn(4, 1); a.shll(1); a.add_rr(6, 1); a.movw_st(3, 1)      # line[DW] = w
    a.add_imm(1, 4); a.extu_w(4, 4); a.movl_st_disp(4, D_DW, 14)
    a.movl_ld_disp(14, D_MIX, 1); a.mul_l(1, 0); a.sts_macl(0); a.mov_imm(-12, 4); a.shad(4, 0)    # e
    a.movl_ld_disp(14, D_LEFT, 1); m.imm32(DL_FADE, 3); nfd = m.newlabel('nfd'); a.cmphs(3, 1); a.bt_lbl(nfd)
    a.mul_l(1, 0); a.sts_macl(0); a.mov_imm(-15, 4); a.shad(4, 0)
    a.deflabel(nfd); a.add_imm(-1, 1); a.movl_st_disp(1, D_LEFT, 14)
    a.add_rr(0, 5)                                                            # y = x + e
    wr, nx = m.newlabel('dw'), m.newlabel('dn')
    a.tst_rr(7, 7); a.bf_lbl(wr)
    a.mov_rm_rn(5, 0); p_ = m.newlabel('dp'); a.cmppz(0); a.bt_lbl(p_); a.neg(0, 0); a.deflabel(p_)
    n_ = m.newlabel('dk'); a.cmphi(9, 0); a.bf_lbl(n_); a.mov_rm_rn(0, 9); a.deflabel(n_)
    a.bra_lbl(nx); a.nop()
    a.deflabel(wr)
    a.mov_imm(0, 3); s_ = m.newlabel('ds'); a.cmppz(5); a.bt_lbl(s_); a.neg(5, 5); a.mov_imm(1, 3); a.deflabel(s_)
    a.dmulu_l(8, 5); a.sts_mach(0); a.sts_macl(5); a.shll16(0); a.shlr16(5); a.or_rr(0, 5)
    m.imm32(32767, 1); c_ = m.newlabel('dc'); a.cmphi(1, 5); a.bf_lbl(c_); a.mov_rm_rn(1, 5); a.deflabel(c_)
    g_ = m.newlabel('dg'); a.tst_rr(3, 3); a.bt_lbl(g_); a.neg(5, 5); a.deflabel(g_)
    a.movw_st(5, 11); a.add_imm(2, 11)
    a.deflabel(nx)
    a.dt(10); m.bf_far(top)
    a.deflabel(de)
    for n, r in (('dptr', 13), ('dcnt', 12), ('outL', 11), ('n', 10), ('pk', 9)):
        m.imm32(VA(n), 1); a.movl_st(r, 1)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

# ---------------------------------------- the reverb kernel, generated from D ----------------------------------------
BASES = [14, 13, 9, 8, 4, 3, 2]
def emit_kverb(m):
    a = m.a
    def B(n): k = IDX[n]; return BASES[k // 16], (k % 16) * 4
    def ldv(n, r): b, d = B(n); a.movl_ld_disp(b, d, r)
    def stv(r, n): b, d = B(n); a.movl_st_disp(r, d, b)
    def addr(offreg):                       # r0 = &ring[(P + offreg) & M]
        a.add_rr(11, offreg); a.and_rr(10, offreg); a.shll(offreg); a.add_rr(12, offreg)
    def clamp(r):
        m.imm32(32767, 1); c1 = m.newlabel('vc'); a.cmpgt(1, r); a.bf_lbl(c1); a.mov_rm_rn(1, r); a.deflabel(c1)
        m.imm32(-32768, 1); c2 = m.newlabel('vc'); a.cmpgt(r, 1); a.bf_lbl(c2); a.mov_rm_rn(1, r); a.deflabel(c2)
    a.deflabel('rv_kverb'); a.stsl_pr()
    FR = 4 * 16 * len(BASES)
    m.imm32(FR, 0); a.sub(0, 15); a.mov_rm_rn(15, 14)
    for i, r in enumerate(BASES[1:], 1): a.mov_rm_rn(14, r); m.imm32(64 * i, 0); a.add_rr(0, r)
    m.imm32(VB, 1); a.mov_rm_rn(14, 5); m.imm32(NV, 6)                       # copy in
    ci = m.newlabel('ci'); a.deflabel(ci); a.movl_ld(1, 0); a.movl_st(0, 5); a.add_imm(4, 1); a.add_imm(4, 5); a.dt(6); a.bf_lbl(ci)
    ldv('ring', 12); ldv('P', 11); m.imm32(RING - 1, 10)
    ldv('n', 0); a.tst_rr(0, 0); vend = m.newlabel('vend'); m.bt_far(vend)
    m.pool_jump()
    top = m.newlabel('vtop'); a.deflabel(top)
    # dry input
    ldv('dcnt', 5); a.tst_rr(5, 5); dz = m.newlabel('dz'); ds = m.newlabel('ds'); a.bt_lbl(dz)
    ldv('dptr', 0); a.movw_ld(0, 1); a.add_imm(2, 0); stv(0, 'dptr'); a.add_imm(-1, 5); stv(5, 'dcnt'); a.bra_lbl(ds); a.nop()
    a.deflabel(dz); a.mov_imm(0, 1)
    a.deflabel(ds); stv(1, 'in')
    n_ops = 0
    for o in D.ops:
        k = o[0]
        if k == 'rd': ldv(o[2], 0); addr(0); a.movw_ld(0, 5); stv(5, o[1])
        elif k == 'wr': ldv(o[1], 5); clamp(5); ldv(o[2], 0); addr(0); a.movw_st(5, 0)
        elif k == 'rdi':
            ldv(o[3], 7); a.mov_rm_rn(7, 5); a.shlr16(5); m.imm32(0xFFFF, 1); a.and_rr(1, 7); a.shlr(7)
            ldv(o[2], 6); a.add_rr(5, 6); a.mov_rm_rn(6, 0); addr(0); a.movw_ld(0, 1)
            a.mov_rm_rn(6, 0); a.add_imm(1, 0); addr(0); a.movw_ld(0, 5)
            a.sub(1, 5); a.mul_l(7, 5); a.sts_macl(5); a.mov_imm(-15, 0); a.shad(0, 5); a.add_rr(1, 5); stv(5, o[1])
        elif k == 'mul': ldv(o[2], 5); ldv(o[3], 1); a.mul_l(1, 5); a.sts_macl(5); a.mov_imm(-o[4], 0); a.shad(0, 5); stv(5, o[1])
        elif k == 'add': ldv(o[2], 5); ldv(o[3], 1); a.add_rr(1, 5); stv(5, o[1])
        elif k == 'sub': ldv(o[2], 5); ldv(o[3], 1); a.sub(1, 5); stv(5, o[1])
        elif k == 'lfo':
            ldv(o[2], 5); ldv(o[3], 1); a.add_rr(1, 5); stv(5, o[2]); a.shlr16(5); m.imm32(32768, 1); a.sub(1, 5)
            p_ = m.newlabel('lp'); a.cmppz(5); a.bt_lbl(p_); a.neg(5, 5); a.deflabel(p_); a.shll(5)
            ldv(o[4], 1); a.mul_l(1, 5); a.sts_macl(5); stv(5, o[1])
        elif k == 'sh2':
            ldv(o[5], 5); ldv(o[6], 1); a.add_rr(1, 5); m.imm32(0x10000, 1); nh = m.newlabel('nh'); a.cmphs(1, 5); a.bf_lbl(nh)
            a.sub(1, 5); ldv(o[3], 6); ldv(o[4], 7); stv(6, o[1]); stv(7, o[2])
            a.deflabel(nh); stv(5, o[5])
        elif k == 'quant': ldv(o[2], 5); ldv(o[4], 1); a.add_rr(1, 5); clamp(5); ldv(o[3], 1); a.and_rr(1, 5); stv(5, o[1])
        n_ops += 1
        if n_ops % 12 == 0: m.pool_jump()
    m.pool_jump()
    a.add_imm(-1, 11); a.and_rr(10, 11)                                      # P moves back
    # output: dry + wet * mix
    ldv('wL', 5); ldv('mixq', 1); a.mul_l(1, 5); a.sts_macl(5); a.mov_imm(-13, 0); a.shad(0, 5); ldv('in', 1); a.add_rr(1, 5)
    ldv('wR', 6); ldv('mixq', 1); a.mul_l(1, 6); a.sts_macl(6); a.shad(0, 6); ldv('in', 1); a.add_rr(1, 6)
    ldv('stereo', 0); a.tst_rr(0, 0); stl = m.newlabel('stl'); a.bf_lbl(stl); a.add_rr(6, 5); a.shar(5); a.mov_rm_rn(5, 6)
    a.deflabel(stl)
    ldv('pass', 0); a.tst_rr(0, 0); wrl = m.newlabel('wrl'); nxt = m.newlabel('nxt'); m.bf_far(wrl)
    for r in (5, 6):
        a.mov_rm_rn(r, 0); p_ = m.newlabel('pa'); a.cmppz(0); a.bt_lbl(p_); a.neg(0, 0); a.deflabel(p_)
        ldv('pk', 1); n_ = m.newlabel('pn'); a.cmphi(1, 0); a.bf_lbl(n_); stv(0, 'pk'); a.deflabel(n_)
    a.bra_lbl(nxt); a.nop()
    m.pool_jump()
    a.deflabel(wrl)
    def scale(r):
        a.mov_imm(0, 7); s_ = m.newlabel('ss'); a.cmppz(r); a.bt_lbl(s_); a.neg(r, r); a.mov_imm(1, 7); a.deflabel(s_)
        ldv('G', 1); a.dmulu_l(1, r); a.sts_mach(0); a.sts_macl(r); a.shll16(0); a.shlr16(r); a.or_rr(0, r)
        m.imm32(32767, 1); c_ = m.newlabel('sc'); a.cmphi(1, r); a.bf_lbl(c_); a.mov_rm_rn(1, r); a.deflabel(c_)
        n_ = m.newlabel('sn'); a.tst_rr(7, 7); a.bt_lbl(n_); a.neg(r, r); a.deflabel(n_)
    scale(5); ldv('outL', 0); a.movw_st(5, 0); a.add_imm(2, 0); stv(0, 'outL')
    ldv('stereo', 0); a.tst_rr(0, 0); a.bt_lbl(nxt)
    scale(6); ldv('outR', 0); a.movw_st(6, 0); a.add_imm(2, 0); stv(0, 'outR')
    a.deflabel(nxt)
    ldv('n', 0); a.add_imm(-1, 0); stv(0, 'n'); a.tst_rr(0, 0); ve = m.newlabel('ve'); a.bt_lbl(ve)
    a.bra_lbl(top); a.nop()
    a.deflabel(ve)
    a.deflabel(vend)
    stv(11, 'P')
    m.imm32(VB, 1); a.mov_rm_rn(14, 5); m.imm32(NV, 6)                       # copy out
    co = m.newlabel('co'); a.deflabel(co); a.movl_ld(5, 0); a.movl_st(0, 1); a.add_imm(4, 1); a.add_imm(4, 5); a.dt(6); a.bf_lbl(co)
    m.imm32(FR, 0); a.add_rr(0, 15)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()


# =====================================================================================================================
if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', '..', 'PRINTFX.WAV'); pg.build_wav(out)
    span = pg.a.labels['__cend'] - pg.a.labels['__cstart']
    assert span <= CACHE_SPAN, "kernels %d B > CACHE_SPAN" % span
    print("  kernels %d B (cache span %d)" % (span, CACHE_SPAN))
    exec(open(os.path.join(os.path.dirname(__file__), 'printfx_test.py')).read())
