#!/usr/bin/env python3
"""REVERB - render a pad through a warm 90s-style digital reverb (10 presets), preview it, keep it as ONE new sample.

    python sdk/examples/reverb.py          -> builds REVERB.WAV and tests it in the emulator

On the MPC500: copy REVERB.WAV to the CF card, LOAD it (after HUB.WAV), then MODE -> OTHER -> "Reverb".
  row 0: "VINT PLATE   A05"   preset . the pad (shows "  45%" while rendering, " PREV" when the preview is ready)
  row 1: "MIX 40%     MONO"   amount of reverb (0..100 %, each preset sets its own default) . MONO / STEREO result
Flow: hit a pad (plays as usual) -> DO IT renders a PREVIEW (dry + reverb, normalized; the pad plays it at its velocity, 127 with FULL LEVEL on)
  -> hitting that pad plays the preview; changing preset / mix / mono-stereo re-renders it by itself
  -> DO IT again keeps it: the result replaces the pad's layers (layer 1, level of the loudest layer, range 0..127,
  tune 0 because the tunings are rendered in; layers 2-4 cleared); the originals stay in memory.
  CANCEL: drops the preview, the pad is left as it was. Another pad during the preview: drops it, that pad is chosen.
Sound: every layer of the pad mixed to mono at its tuning and relative level (as S500), then a Dattorro-style
  plate/hall tank (pre-delay, input band-limit, 4 input diffusers, two modulated cross-coupled branches with damping),
  14 output taps (stereo), wet low cut + high cut, optional "grain" (sample & hold + 12 bit on the wet signal only).
  The result is normalized (peak at full scale). Tail = the preset's decay time.
Implementation: the reverb is described ONCE (class DSP below) and generates both the SH-3 kernel and the bit-exact
  Python model used by the test. Work memory (dry mix + 64K-word ring for the delay lines) lives inside the new
  sample block (last block of the list) and is cut off when you keep the result.

OS addresses (MPC500 OS 1.31): see s500.py (program/pads/samples/Copy/Delete/LOOP_GUARD/find by name) and
  audition player 0x8848CD78, play 0x884C2636(player, block, 0, velocity), stop 0x884C29D2(player) (Sample FX, HW).
"""
import sys, os, struct, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import PageAddon, ld, st, sti
from mpcmod import EVQ

PROG_BASE, PROG_IDX, PROG_STRIDE = 0x8837B5AC, 0x8847C0E2, 0x1F98
PAD_OFF, PAD_SIZE, LAYER = 0x18, 0xA4, 0x18
BANKOBJ = 0x8849C98C
MGR, COPY_CMD, DELETE_CMD, LOOP_GUARD, FINDNAME = 0x8848ED30, 0x884C5FA0, 0x884C5FFC, 0x884C4F04, 0x884C5090
PLAYER, AUD_PLAY, AUD_STOP = 0x8848CD78, 0x884C2636, 0x884C29D2
FS = 44100
RING = 0x10000                       # words
CHUNK_MIX, CHUNK_CLR, CHUNK_REV = 2048, 4096, 256
CACHE_SPAN = 0x2000
GUARD = 256                          # the OS keeps 256 spare frames per channel after the audio (LOOP_GUARD writes there, see trim.py)
DEBOUNCE = 48
SEMI = [round(65536 * 2 ** (k / 12)) for k in range(-60, 61)]
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
    """bit-exact model of the kernel: returns (peak, L, R)"""
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
        def sc(v_):
            neg = v_ < 0; v_ = (abs(v_) * G) >> 16 & 0xFFFFFFFF; v_ = min(v_, 32767); return -v_ if neg else v_
        L.append(sc(oL))
        if stereo: R.append(sc(oR))
    return pk, L, R

# ================================================== the page ==================================================
pg = PageAddon("reverb1", open_press=None)
pg.other_menu("Reverb")
pg.far_hend = True
pg.msg_fires = 0x600
PRESET = pg.choice("", 0, 0, NAMES, default=DEFP)
PADL = pg.dlabel(11, 0, 5)
MIX = pg.value("MIX", 0, 1, fmt="  0", lo=0, hi=100, default=MIXDEF[DEFP])
pg.label("%", 6, 1)
STER = pg.choice("", 10, 1, ["  MONO", "STEREO"], default=0)
pg.message()
(ST, SEL, PEND, PHASE, LI, NL, DST, NIN, NOUT, NMAX, POS, DONEU, TOTU, MAXLV, PREVCUR, LASTP, LASTSET, DEB, RSET,
 SAVEPK) = [pg.alloc_slot() for _ in range(20)]
pg.defaults[LASTP] = 3
LSRC, LSTART, LEND, LSTEP, LGAIN, LN = [[pg.alloc_slot() for _ in range(4)] for _ in range(6)]
KP = [pg.alloc_slot() for _ in range(11)]                 # mix kernel parameters (as S500)
for _s in KP: del pg.defaults[_s]
M_LBASE, M_RBASE, M_DST, M_N, M_I, M_F, M_SI, M_SF, M_END, M_G, M_DONE = range(0, 44, 4)
KB = KP[0]
KV = [pg.alloc_slot() for _ in range(NV)]                 # reverb kernel variables
for _s in KV: del pg.defaults[_s]
VB = KV[0]
def VA(n): return VB + 4 * IDX[n]
ST_IDLE, ST_JOB, ST_PREV = 0, 1, 2
PH_START, PH_CLRD, PH_MIX, PH_RINIT, PH_CLRR, PH_REND, PH_PREV, PH_FIN = range(8)

def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()     # clobbers r2! (rv_dry is called via r1: CLRD/MIX keep r2)
def osc(m, fn): m.imm32(fn, 3); m.a.jsr(3); m.a.nop()
def rdb(m, addr, r): m.imm32(addr, r); m.a.movb_ld(r, r); m.a.extu_b(r, r)
def kst(m, r, off): m.imm32(KB + off, 1); m.a.movl_st(r, 1)
def kld(m, off, r): m.imm32(KB + off, r); m.a.movl_ld(r, r)
def vst(m, r, n): m.imm32(VA(n), 1); m.a.movl_st(r, 1)
def vld(m, n, r): m.imm32(VA(n), r); m.a.movl_ld(r, r)
def cached_call(m, label):
    m.a.movl_pool(label, 2); m.imm32(0xDFFFFFFF, 3); m.a.and_rr(3, 2); m.a.jsr(2); m.a.nop()
def padblk(m, rpad, rd_):
    a = m.a
    m.imm32(PROG_IDX, 1); a.movw_ld(1, rd_); a.extu_w(rd_, rd_); m.imm32(PROG_STRIDE, 1); a.mul_l(1, rd_); a.sts_macl(rd_)
    m.imm32(PROG_BASE + PAD_OFF, 1); a.add_rr(1, rd_)
    m.imm32(PAD_SIZE, 3); a.mul_l(rpad, 3); a.sts_macl(3); a.add_rr(3, rd_)
def settings(m, r):
    """r = preset | mix << 8 | stereo << 16 (uses r0, r1)"""
    a = m.a
    rdb(m, PRESET, r); ld(m, MIX, 0); a.shll8(0); a.or_rr(0, r); rdb(m, STER, 0); a.shll16(0); a.or_rr(0, r)

def fire(m):
    a = m.a
    # preset changed -> its default mix
    rdb(m, PRESET, 9); ld(m, LASTP, 0); a.cmpeq(9, 0); sp = m.newlabel('sp'); m.bt_far(sp)
    st(m, 9, LASTP); a.mov_rm_rn(9, 0); a.shll2(0); a.movl_pool('rv_mixdef', 1); a.add_rr(0, 1); a.movl_ld(1, 0)
    st(m, 0, MIX); m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(sp)
    ld(m, PEND, 0); a.tst_rr(0, 0); np_ = m.newlabel('np'); m.bt_far(np_); call(m, 'rv_pick'); a.deflabel(np_)
    call(m, 'rv_tick')
    call(m, 'rv_disp')
pg.on_fire(fire)

def inactive(m):
    call(m, 'rv_tick')
pg.on_inactive(inactive)

def subs(m):
    a = m.a
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

    # ---- rv_disp: row 0 right = pad / % / PREV ----
    a.deflabel('rv_disp'); a.stsl_pr()
    m.imm32(pg.addr['dlabels'][PADL], 12); a.mov_imm(0, 14)
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
    a.tst_rr(14, 14); px = m.newlabel('px'); m.bt_far(px); m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(px); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- rv_tick: settings changed while rendering / previewing -> re-render after DEBOUNCE fires; job step ----
    a.deflabel('rv_tick'); a.stsl_pr()
    tx = m.newlabel('tx'); tj = m.newlabel('tj')
    ld(m, ST, 0); a.tst_rr(0, 0); m.bt_far(tx)
    ld(m, PHASE, 0); a.mov_imm(PH_RINIT, 1); a.cmphs(1, 0); m.bf_far(tj)       # dry mix not ready yet
    settings(m, 9); ld(m, RSET, 0); a.cmpeq(9, 0); same = m.newlabel('same'); m.bt_far(same)
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
    sti(m, ST_IDLE, ST); pg.emit_message(m, "NO SAMPLE"); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(some)
    ld(m, MAXLV, 0); a.tst_rr(0, 0); ml = m.newlabel('ml'); a.bf_lbl(ml); a.mov_imm(100, 0); st(m, 0, MAXLV); a.deflabel(ml)
    a.mov_imm(0, 10)
    gl = m.newlabel('gl'); a.deflabel(gl)
    m.imm32(LGAIN[0], 9); a.add_rr(10, 9); a.movl_ld(9, 4)
    ld(m, MAXLV, 0); a.tst_rr(4, 4); gz = m.newlabel('gz'); a.bf_lbl(gz); a.mov_rm_rn(0, 4); a.deflabel(gz)
    a.shll16(4); ld(m, NL, 5); a.mul_l(0, 5); a.sts_macl(5); pg._hcall(m, '__udiv'); a.movl_st(0, 9)
    a.add_imm(4, 10); ld(m, NL, 0); a.shll2(0); a.cmpeq(0, 10); a.bf_lbl(gl)
    # NMAX = NIN + longest tail; block = header + PCM L,R (NMAX each) + dry (NIN) + ring
    ld(m, NIN, 0); m.imm32(TMAX + GUARD, 1); a.add_rr(1, 0); st(m, 0, NMAX)
    ld(m, LSRC[0], 8); m.imm32(MGR, 1); a.movl_ld_disp(1, 8, 0); st(m, 0, PREVCUR)
    m.imm32(MGR, 1); a.movl_st_disp(8, 8, 1)
    m.os_extract(8, COPY_CMD)
    a.tst_rr(0, 0); cok = m.newlabel('cok'); m.bt_far(cok)
    ld(m, PREVCUR, 0); m.imm32(MGR, 1); a.movl_st_disp(0, 8, 1)
    sti(m, ST_IDLE, ST); pg.emit_message(m, "MEMORY FULL"); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(cok)
    m.imm32(MGR, 1); a.movl_ld_disp(1, 8, 9); st(m, 9, DST)
    call(m, 'rv_size')                                                       # r4 = block size
    a.mov_rm_rn(9, 0); a.add_rr(4, 0); a.add_imm(4, 0); m.imm32(MGR, 1); a.movl_ld_disp(1, 4, 1)
    a.cmphi(1, 0); fit = m.newlabel('fit'); m.bf_far(fit)
    m.imm32(MGR, 4); osc(m, DELETE_CMD)
    ld(m, PREVCUR, 0); m.imm32(MGR, 1); a.movl_st_disp(0, 8, 1)
    sti(m, ST_IDLE, ST); pg.emit_message(m, "MEMORY FULL"); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(fit)
    a.movl_st(4, 9); a.mov_rm_rn(9, 0); a.add_rr(4, 0); a.mov_imm(0, 1); a.movl_st(1, 0)
    m.imm32(0x3E, 1); a.add_rr(9, 1); a.mov_imm(0, 0); a.movw_st(0, 1)       # the player honours start/end
    sti(m, 0, POS); sti(m, PH_CLRD, PHASE)
    ld(m, NIN, 0); ld(m, NL, 2); a.add_imm(1, 2); a.mul_l(2, 0); a.sts_macl(0); m.imm32(RING * 2, 1); a.add_rr(1, 0)
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
    settings(m, 9); st(m, 9, RSET); st(m, 9, LASTSET)
    rdb(m, PRESET, 4); a.shll2(4); a.movl_pool('rv_tails', 1); a.add_rr(4, 1); a.movl_ld(1, 0); ld(m, NIN, 1); a.add_rr(1, 0)
    st(m, 0, NOUT)
    ld(m, DST, 9); a.movl_st_disp(0, 0x18, 9); a.add_imm(-1, 0); a.movl_st_disp(0, 0x24, 9)
    a.mov_imm(0, 0); a.movl_st_disp(0, 0x20, 9); a.movl_st_disp(0, 0x28, 9)
    rdb(m, STER, 2); a.tst_rr(2, 2); mo2 = m.newlabel('mo2'); hd = m.newlabel('hd')
    a.bt_lbl(mo2)
    ld(m, NOUT, 0); m.imm32(GUARD, 1); a.add_rr(1, 0); a.movl_st_disp(0, 0x1C, 9); a.mov_imm(2, 3); m.imm32(176400, 5); a.mov_imm(4, 6); a.bra_lbl(hd); a.nop()
    a.deflabel(mo2); a.mov_imm(0, 0); a.movl_st_disp(0, 0x1C, 9); a.mov_imm(1, 3); m.imm32(88200, 5); a.mov_imm(2, 6)
    a.deflabel(hd)
    m.imm32(0x92, 1); a.add_rr(9, 1); a.movw_st(3, 1); m.imm32(0x98, 1); a.add_rr(9, 1); a.movl_st(5, 1)
    m.imm32(0x9C, 1); a.add_rr(9, 1); a.movw_st(6, 1)
    a.mov_imm(0, 0); call(m, 'rv_vinit')
    ld(m, NOUT, 0); a.shll(0); m.imm32(RING * 2, 1); a.add_rr(1, 0); st(m, 0, TOTU); sti(m, 0, DONEU)
    sti(m, 0, POS); sti(m, PH_CLRR, PHASE); a.movl_pool(jx, 2); a.jmp(2); a.nop()
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

    # == REND: a chunk of the reverb (pass 0 peak / pass 1 write) ==
    a.deflabel(labs[PH_REND])
    ld(m, NOUT, 2); ld(m, POS, 3); a.sub(3, 2); m.imm32(CHUNK_REV, 0); a.cmphi(0, 2); c4 = m.newlabel('c4'); a.bf_lbl(c4); a.mov_rm_rn(0, 2); a.deflabel(c4)
    vst(m, 2, 'n'); a.add_rr(2, 3); st(m, 3, POS); ld(m, DONEU, 0); a.add_rr(2, 0); st(m, 0, DONEU)
    cached_call(m, 'rv_kverb')
    ld(m, NOUT, 0); ld(m, POS, 3); a.cmpeq(0, 3); m.bf_far(jx)
    vld(m, 'pass', 0); a.tst_rr(0, 0); w1 = m.newlabel('w1'); m.bf_far(w1)
    # gain: normalize (peak -> 32767)
    vld(m, 'pk', 5); m.imm32(0x10000, 0); a.tst_rr(5, 5); g1 = m.newlabel('g1'); a.bt_lbl(g1)
    m.imm32(0x7FFF0000, 4); pg._hcall(m, '__udiv')
    a.deflabel(g1); st(m, 0, SAVEPK)
    a.mov_imm(1, 0); call(m, 'rv_vinit'); ld(m, SAVEPK, 0); vst(m, 0, 'G')
    sti(m, 0, POS); sti(m, PH_CLRR, PHASE); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    a.deflabel(w1)
    sti(m, ST_PREV, ST); sti(m, PH_PREV, PHASE)
    ld(m, DST, 4); osc(m, LOOP_GUARD)
    a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()

    # == PREV: nothing to do (waiting); FIN: keep ==
    a.deflabel(labs[PH_PREV]); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    a.deflabel(labs[PH_FIN])
    m.imm32(PLAYER, 4); osc(m, AUD_STOP)
    ld(m, DST, 9); ld(m, NOUT, 4); m.imm32(GUARD, 0); a.add_rr(0, 4); a.shll(4); rdb(m, STER, 0); a.tst_rr(0, 0); fm = m.newlabel('fm'); a.bt_lbl(fm); a.shll(4)
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
    m.imm32(pg.addr['msgt'], 12)
    for ch in "DONE ": a.mov_imm(ord(ch), 0); a.movb_st(0, 12); a.add_imm(1, 12)
    a.mov_rm_rn(8, 2); a.add_imm(4, 2); a.mov_imm(11, 4)
    dc = m.newlabel('dc'); dz = m.newlabel('dz'); a.deflabel(dc); a.movb_ld(2, 0); a.tst_rr(0, 0); a.bf_lbl(dz); a.mov_imm(ord(' '), 0)
    a.deflabel(dz); a.movb_st(0, 12); a.add_imm(1, 12); a.add_imm(1, 2); a.dt(4); a.bf_lbl(dc)
    a.mov_imm(0, 0); a.movb_st(0, 12); pg._hcall(m, '__showmsg')
    a.deflabel(jx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- rv_size: r4 = block size = 0x100 + 4*NMAX + 2*NIN (+align) + 2*RING. r0-r1 ----
    a.deflabel('rv_size')
    ld(m, NMAX, 4); a.shll2(4); ld(m, NIN, 0); a.shll(0); a.add_rr(0, 4); m.imm32(0x100 + 3 + 2 * RING, 0); a.add_rr(0, 4)
    a.mov_imm(-4, 0); a.and_rr(0, 4); a.rts(); a.nop()
    # ---- rv_dry: r4 = dry buffer address = DST + 0x100 + 4*NMAX. r0-r1 ----
    a.deflabel('rv_dry')
    ld(m, DST, 4); m.imm32(0x100, 0); a.add_rr(0, 4); ld(m, NMAX, 0); a.shll2(0); a.add_rr(0, 4); a.rts(); a.nop()
    m.pool_jump()

    # ---- rv_vinit: r0 = pass. Reverb variables: state 0, preset params, ring/dry/out pointers ----
    a.deflabel('rv_vinit'); a.stsl_pr()
    a.mov_rm_rn(0, 8)
    m.imm32(VB, 1); m.imm32(PBASE, 2); a.mov_imm(0, 0)
    zl = m.newlabel('zl'); a.deflabel(zl); a.movl_st(0, 1); a.add_imm(4, 1); a.dt(2); a.bf_lbl(zl)
    rdb(m, PRESET, 4); m.imm32(len(D.params) * 4, 0); a.mul_l(0, 4); a.sts_macl(4); a.movl_pool('rv_ptab', 2); a.add_rr(4, 2)
    m.imm32(VA(D.params[0]), 1); m.imm32(len(D.params), 3)
    pl = m.newlabel('pl'); a.deflabel(pl); a.movl_ld(2, 0); a.movl_st(0, 1); a.add_imm(4, 1); a.add_imm(4, 2); a.dt(3); a.bf_lbl(pl)
    m.imm32(0x80000000, 0); vst(m, 0, 'phB')
    ld(m, MIX, 0); m.imm32(MIXK, 1); a.mul_l(1, 0); a.sts_macl(4); a.mov_imm(100, 5); pg._hcall(m, '__udiv'); vst(m, 0, 'mixq')
    rdb(m, STER, 0); vst(m, 0, 'stereo')
    vst(m, 8, 'pass'); m.imm32(0x10000, 0); vst(m, 0, 'G')
    call(m, 'rv_dry'); vst(m, 4, 'dptr'); ld(m, NIN, 0); vst(m, 0, 'dcnt')
    ld(m, NIN, 0); a.shll(0); a.add_rr(0, 4); m.imm32(3, 0); a.add_rr(0, 4); a.mov_imm(-4, 0); a.and_rr(0, 4); vst(m, 4, 'ring')
    ld(m, DST, 4); m.imm32(0x100, 0); a.add_rr(0, 4); vst(m, 4, 'outL')
    rdb(m, STER, 0); a.tst_rr(0, 0); a.mov_imm(0, 0); om = m.newlabel('om'); a.bt_lbl(om)
    ld(m, NOUT, 0); m.imm32(GUARD, 1); a.add_rr(1, 0); a.shll(0); a.add_rr(4, 0)
    a.deflabel(om); vst(m, 0, 'outR')
    a.ldsl_pr(); a.rts(); a.nop()
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
    a.movl_pool('rv_semi', 2); a.mov_rm_rn(6, 0); a.shll2(0); a.add_rr(0, 2); a.movl_ld(2, 4)
    a.movl_pool('rv_cent', 2); a.mov_rm_rn(7, 0); a.shll2(0); a.add_rr(0, 2); a.movl_ld(2, 5)
    a.dmulu_l(4, 5); a.sts_mach(0); a.sts_macl(3); a.shll16(0); a.shlr16(3); a.or_rr(3, 0); a.movl_st_dec(0, 15)
    m.imm32(120, 0); a.sub(6, 0)
    a.movl_pool('rv_semi', 2); a.shll2(0); a.add_rr(0, 2); a.movl_ld(2, 4)
    a.movl_pool('rv_centi', 2); a.mov_rm_rn(7, 0); a.shll2(0); a.add_rr(0, 2); a.movl_ld(2, 5)
    a.dmulu_l(4, 5); a.sts_mach(1); a.sts_macl(3); a.shll16(1); a.shlr16(3); a.or_rr(3, 1)
    a.movl_ld_inc(15, 0)
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
    emit_kverb(m)

    # ---- tables ----
    def table(name, words):
        m.pool_jump()
        while (len(a.words) * 2 + a.base) & 3: a.nop()
        a.deflabel(name)
        for w in words: a._emit(w & 0xFFFF); a._emit((w >> 16) & 0xFFFF)
    skip = m.newlabel('tskip'); a.bra_lbl(skip); a.nop()
    table('rv_ptab', [v for row in PTAB for v in row]); table('rv_tails', TAILS); table('rv_mixdef', MIXDEF)
    table('rv_semi', SEMI); table('rv_cent', CENT); table('rv_centi', CENTI)
    a.deflabel(skip)

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

def doit(m):
    a = m.a
    dx = m.newlabel('dx'); keep = m.newlabel('keep')
    ld(m, ST, 0); a.mov_imm(ST_PREV, 1); a.cmpeq(1, 0); m.bt_far(keep)
    a.tst_rr(0, 0); m.bf_far(dx)                                             # rendering: ignore
    ld(m, SEL, 9); a.tst_rr(9, 9); np_ = m.newlabel('np'); m.bf_far(np_)
    pg.emit_message(m, "HIT A PAD FIRST"); a.movl_pool(dx, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(np_)
    sti(m, PH_START, PHASE); sti(m, 0, DONEU); sti(m, 1, TOTU); sti(m, ST_JOB, ST); a.movl_pool(dx, 2); a.jmp(2); a.nop()
    a.deflabel(keep); sti(m, ST_JOB, ST); sti(m, PH_FIN, PHASE)
    a.deflabel(dx)
    a.movl_pool('__rv_end', 2); a.jmp(2); a.nop(); m.pool_jump()
    subs(m)
    a.deflabel('__rv_end')
pg.on_doit(doit)

def ev_pad(m, hret0, hret1):
    """idle: a pad is chosen. Preview: the chosen pad plays the preview; another pad drops it and is chosen."""
    a = m.a
    a.movl_ld_disp(8, 4, 0); a.tst_rr(0, 0); m.bt_far(hret0)
    a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 9); a.extu_w(9, 9); a.mov_imm(12, 0); a.cmphs(0, 9); m.bt_far(hret0)
    m.imm32(BANKOBJ, 1); a.movb_ld(1, 0); a.and_imm(3); a.mov_imm(12, 2); a.mul_l(2, 0); a.sts_macl(0); a.add_rr(0, 9)
    a.add_imm(1, 9)
    ld(m, ST, 0); a.mov_imm(ST_JOB, 1); a.cmpeq(1, 0); m.bt_far(hret0)       # rendering: pads just play
    a.mov_imm(ST_PREV, 1); a.cmpeq(1, 0); np_ = m.newlabel('np'); m.bf_far(np_)
    ld(m, SEL, 0); a.cmpeq(9, 0); m.bf_far(np_)
    m.imm32(PLAYER, 4); osc(m, AUD_STOP)                                     # the preview, at the pad's velocity
    m.imm32(PLAYER, 4); ld(m, DST, 5); a.mov_imm(0, 6); a.movl_ld_disp(8, 4, 7)
    m.imm32(BANKOBJ + 4, 1); a.movl_ld(1, 0); a.tst_rr(0, 0); fv = m.newlabel('fv'); a.bt_lbl(fv); a.mov_imm(127, 7); a.deflabel(fv)   # FULL LEVEL on: 127
    osc(m, AUD_PLAY)
    a.movl_pool(hret1, 2); a.jmp(2); a.nop()
    a.deflabel(np_); st(m, 9, PEND)
    a.movl_pool(hret0, 2); a.jmp(2); a.nop()
    m.pool_jump()
pg.on_event(0x38, ev_pad)

def cancel(m, hret1):
    a = m.a
    lv = m.newlabel('lv')
    ld(m, ST, 0); a.tst_rr(0, 0); m.bt_far(lv)
    call(m, 'rv_drop'); pg.emit_message(m, "CANCELLED")
    a.movl_pool(hret1, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(lv)
pg.cancel_fn = cancel


# =====================================================================================================================
if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', '..', 'REVERB.WAV'); pg.build_wav(out)
    from validate import Harness
    from shasm import SHAsm
    from mpcmod import OS_PUSH_PAGE, HOOKS
    from pages import ENABLE_DOIT, ENABLE_CANCEL, SET_FOCUS
    from testkit import PageTest, FIRMWARE
    import random, time

    class OSTest(PageTest):
        def __init__(self, pg, wav, before_hub=None):
            self.pg = pg
            self.h = h = Harness(FIRMWARE); h.fill_scratch(); self.e = e = h.emu
            fw = open(FIRMWARE, 'rb').read(); OSB = 0x8849495C
            keep = [(HOOKS[h.hook][1], 4), (ENABLE_DOIT, 10), (ENABLE_CANCEL, 10)]
            saved = [(ad, [e.rd(ad + i, 2) for i in range(0, n, 2)]) for ad, n in keep]
            buf, off = e._region(OSB); buf[off:off + len(fw)] = fw
            for ad, ws in saved:
                for i, w in enumerate(ws): e.wr(ad + 2 * i, w, 2)
            s = SHAsm(0x884C4FE0)
            s.movl_ld(4, 1); s.movl_ld_disp(4, 4, 3); s.deflabel('l'); s.cmphs(3, 1); s.bt_lbl('z')
            s.movl_ld(1, 0); s.tst_rr(0, 0); s.bt_lbl('f'); s.add_rr(0, 1); s.bra_lbl('l'); s.nop()
            s.deflabel('z'); s.mov_imm(0, 1); s.deflabel('f'); s.rts(); s.mov_rm_rn(1, 0)
            blob = s.assemble()[:26]
            for i in range(0, 26, 2): e.wr(0x884C4FE0 + i, struct.unpack('<H', blob[i:i + 2])[0], 2)
            for ad in (OS_PUSH_PAGE, SET_FOCUS, 0x884C0076, 0x884C67BA): self.stub(ad)
            if before_hub: before_hub(self)
            h.load_hub(); h.load_wav(wav); h.fire(10)

    LOG = 0x88E20000
    def stubs(t):
        def logp(s, tag):
            s.movl_pool(LOG, 1); s.movl_ld(1, 0); s.add_imm(1, 0); s.movl_st(0, 1); s.shll2(0); s.shll2(0); s.add_rr(0, 1)
            s.mov_imm(tag, 0); s.movl_st(0, 1); s.movl_st_disp(5, 4, 1); s.movl_st_disp(6, 8, 1); s.movl_st_disp(7, 12, 1)
            s.rts(); s.nop()
        t.stub(AUD_PLAY, lambda s: logp(s, 1)); t.stub(AUD_STOP, lambda s: logp(s, 2))
        if 'noguard' in sys.argv[1:]: t.stub(LOOP_GUARD)
    t = OSTest(pg, out, before_hub=stubs); h, e = t.h, t.e
    R32, W32 = h.ram_r32, h.ram_w32
    def plog():
        n = R32(LOG); r = [(R32(LOG + 16 * i + 16), R32(LOG + 16 * i + 20), R32(LOG + 16 * i + 24)) for i in range(n)]
        W32(LOG, 0); return r
    def s16(v): return v - 0x10000 if v & 0x8000 else v
    rnd = random.Random(10)
    def mkblock(name, L, R=None):
        n = len(L); stereo = R is not None
        pcm = b''.join(struct.pack('<h', v) for v in L) + (b''.join(struct.pack('<h', v) for v in R) if stereo else b'')
        blk = h.add_sample(pcm, name=name)
        W32(blk + 0x18, n); W32(blk + 0x1C, n if stereo else 0); W32(blk + 0x20, 0); W32(blk + 0x24, n - 1); W32(blk + 0x28, 0)
        h.ram_w16(blk + 0x92, 2 if stereo else 1); return blk
    SN = [int(14000 * math.exp(-i / 90.0) * (rnd.random() * 2 - 1)) for i in range(300)]   # a short noisy "snare"
    B_S = mkblock(b"SNARE", SN)
    def list_end():
        p = R32(MGR)
        while R32(p): p += R32(p)
        return p
    h.ram_w16(PROG_IDX, 1); PROG = PROG_BASE + PROG_STRIDE
    def lay(p, l): return PROG + PAD_OFF + p * PAD_SIZE + l * LAYER
    for p in range(48):
        for l in range(4):
            for i in range(0x18): e.wr(lay(p, l) + i, 0, 1)
    for i, c in enumerate(b"SNARE"): e.wr(lay(4, 0) + i, c, 1)
    e.wr(lay(4, 0) + 0x11, 90, 1); e.wr(lay(4, 0) + 0x13, 127, 1)
    # fast test (default): the tails table in the loaded add-on is shortened to SHORT samples, the model follows.
    # "python reverb.py full" renders the real tails (slow: the emulator runs ~900 instructions per sample).
    FULL = 'full' in sys.argv[1:]
    DBG = 'dbg' in sys.argv[1:]
    if not FULL:
        SHORT = 1500
        pat = b''.join(struct.pack('<I', v) for v in TAILS)
        lo_ = ST - 0x8000                                          # the code lies below its scratch slots
        buf_, off_ = e._region(lo_); k = bytes(buf_[off_:off_ + 0x8000]).find(pat)
        assert k >= 0, "tails table not found in memory"
        hit = lo_ + k
        delta = (SHORT * len(TAILS) - sum(TAILS)) & 0xFFFFFFFF
        TAILS[:] = [SHORT] * len(TAILS)
        for i in range(len(TAILS)): W32(hit + 4 * i, SHORT)
        L_ = h.hub_lay(); k_ = [k for k in range(16) if R32(L_.INST(k)) == 1]; assert len(k_) == 1, k_
        W32(L_.SUMC(k_[0]), (R32(L_.SUMC(k_[0])) + delta) & 0xFFFFFFFF)      # the HUB watchdog checksums the code
        print("  (fast test: tails shortened to %d samples; 'full' for real tails)" % SHORT)
    def jobs(limit=None):
        limit = limit or (200000 if FULL else 4000)
        t0_ = time.time()
        for k in range(limit):
            if t.slot(ST) != ST_JOB: return k
            t.fire(1)
            if DBG and k < 4:
                print("    fire %d: PHASE %d POS %08x NIN %d NL %d DST %08x NMAX %d  kclr dst %08x n %08x  slots@%08x" % (
                    k, t.slot(PHASE), t.slot(POS), t.slot(NIN), t.slot(NL), t.slot(DST), t.slot(NMAX),
                    t.slot(KB + M_DST), t.slot(KB + M_N), POS), flush=True)
            if DBG and k % 500 == 0:
                print("    fire %5d  %5.1fs  PHASE %d POS %d NOUT %d pass %d n %d" % (k, time.time() - t0_, t.slot(PHASE), t.slot(POS),
                      t.slot(NOUT), R32(VA('pass')), R32(VA('n'))), flush=True)
        raise AssertionError("job did not finish: PHASE %d POS %d NOUT %d TOTU %d DONEU %d" % (
            t.slot(PHASE), t.slot(POS), t.slot(NOUT), t.slot(TOTU), t.slot(DONEU)))
    print("  reverb: %d ops per sample, %d variables, %d presets, longest tail %.1f s" % (len(D.ops), NV, len(PRESETS), TMAX / FS))
    t.open()
    AMB = NAMES.index("AMBIENCE")
    assert e.rd(PRESET, 1) == DEFP and t.slot(MIX) == MIXDEF[DEFP] and e.rd(STER, 1) == 0
    e.wr(PRESET, AMB, 1); t.fire(2); assert t.slot(MIX) == MIXDEF[AMB], "preset sets its default mix"
    t.doit(); assert t.msg() == "HIT A PAD FIRST"
    print("  ok defaults %s / mix %d / MONO; preset -> its default mix (AMBIENCE %d)" % (NAMES[DEFP], MIXDEF[DEFP], MIXDEF[AMB]))
    # --- render AMBIENCE stereo, compare with the model ---
    e.wr(STER, 1, 1); t.pad(0, 4); t.fire(2); assert t.label(PADL, 5) == "  A05"
    end0 = list_end(); t0 = time.time(); t.doit(); fires = jobs()
    assert t.slot(ST) == ST_PREV and t.label(PADL, 5) == " PREV", (t.slot(ST), t.label(PADL, 5))
    D1 = R32(MGR + 8); assert D1 == end0
    nout = R32(D1 + 0x18); assert nout == len(SN) + TAILS[AMB], (nout, len(SN) + TAILS[AMB])
    dry = [(v * ((90 << 16) // 90)) >> 16 for v in SN]
    pk, _, _ = model_render(dry, AMB, MIXDEF[AMB], 1, 0)
    G = 0x7FFF0000 // pk if pk else 0x10000
    _, ML, MR = model_render(dry, AMB, MIXDEF[AMB], 1, 1, G)
    sd = R32(D1 + 0x1C); GL = [s16(e.rd(D1 + 0x100 + 2 * i, 2)) for i in range(nout)]; GR = [s16(e.rd(D1 + 0x100 + 2 * sd + 2 * i, 2)) for i in range(nout)]
    bad = [i for i in range(nout) if GL[i] != ML[i] or GR[i] != MR[i]]
    assert not bad, (len(bad), bad[:5], [(GL[i], ML[i], GR[i], MR[i]) for i in bad[:3]])
    assert (e.rd(D1 + 0x92, 2), sd) == (2, nout + GUARD) and GL != GR, "stereo, L and R differ"
    tail_e = sum(v * v for v in GL[len(SN) + 200:nout]); assert tail_e > 0, "there is a reverb tail"
    print("  ok AMBIENCE stereo: %d frames (= dry %d + tail), bit-exact with the model, %d fires (%.0f s in the emulator)"
          % (nout, len(SN), fires, time.time() - t0))
    # --- preview: the pad plays the preview; another pad drops it ---
    def plog4():
        n = R32(LOG); r = [tuple(R32(LOG + 16 * i + 16 + 4 * j) for j in range(4)) for i in range(n)]; W32(LOG, 0); return r
    plog4(); W32(BANKOBJ + 4, 0); r0, r1 = t.pad(0, 4, vel=70); assert r0 == 1 and (1, D1, 0, 70) in plog4(), "preview played at the pad velocity"
    W32(BANKOBJ + 4, 1); r0, r1 = t.pad(0, 4, vel=70); assert r0 == 1 and (1, D1, 0, 127) in plog4(), "FULL LEVEL on: 127"
    W32(BANKOBJ + 4, 0)
    r0, _ = t.pad(0, 6); assert r0 == 0; t.fire(2); assert t.slot(ST) == ST_IDLE and list_end() == end0 and t.slot(SEL) == 7
    print("  ok preview: the chosen pad plays the rendered sample; another pad drops the preview")
    # --- mono, mix 0 -> dry only; then keep ---
    t.pad(0, 4); t.fire(2); e.wr(STER, 0, 1); t.set_slot(MIX, 0); t.fire(2)
    t.doit(); jobs(); D2 = R32(MGR + 8); nout2 = R32(D2 + 0x18)
    GM = [s16(e.rd(D2 + 0x100 + 2 * i, 2)) for i in range(nout2)]
    pk, _, _ = model_render(dry, AMB, 0, 0, 0); _, MM, _ = model_render(dry, AMB, 0, 0, 1, 0x7FFF0000 // pk)
    assert GM == MM and max(abs(v) for v in GM) in (32766, 32767) and (e.rd(D2 + 0x92, 2), R32(D2 + 0x1C)) == (1, 0)
    print("  ok MONO, mix 0%%: = the dry sound normalized + silence (%d frames)" % nout2)
    # --- change the mix during the preview -> re-render ---
    assert t.slot(ST) == ST_PREV, t.slot(ST)
    t.set_slot(MIX, 60); t.fire(DEBOUNCE + 3)
    assert t.slot(ST) == ST_JOB, (t.slot(ST), t.slot(PHASE), t.slot(DEB), hex(t.slot(RSET)), hex(t.slot(LASTSET)), t.slot(MIX)); jobs()
    GM2 = [s16(e.rd(D2 + 0x100 + 2 * i, 2)) for i in range(nout2)]
    pk, _, _ = model_render(dry, AMB, 60, 0, 0); G = 0x7FFF0000 // pk if pk else 0x10000
    assert GM2 == model_render(dry, AMB, 60, 0, 1, G)[1]
    print("  ok mix changed during the preview -> re-rendered (mix 60%, = model)")
    # --- keep ---
    t.doit(); jobs(); assert t.slot(ST) == ST_IDLE and t.msg().startswith("DONE "), t.msg()
    assert R32(D2) == ((0x100 + 2 * (nout2 + GUARD) + 3) & ~3) and list_end() == D2 + R32(D2), "work memory cut off"
    name = bytes(e.rd(lay(4, 0) + i, 1) for i in range(16)).split(b'\0')[0]
    assert name == bytes(e.rd(D2 + 4 + i, 1) for i in range(16)).split(b'\0')[0] and name != b"SNARE"
    assert e.rd(lay(4, 0) + 0x11, 1) == 90 and e.rd(lay(4, 0) + 0x14, 2) == 0 and (e.rd(lay(4, 0) + 0x12, 1), e.rd(lay(4, 0) + 0x13, 1)) == (0, 127)
    print("  ok DO IT keeps it: pad A05 -> %r, block cut to the PCM, level 90 kept" % name)
    # --- CANCEL while rendering ---
    end1 = list_end(); t.pad(0, 4); t.fire(2); t.doit(); t.fire(5); assert t.slot(ST) == ST_JOB
    t.cancel(); assert t.msg() == "CANCELLED" and list_end() == end1 and t.slot(ST) == ST_IDLE
    print("  ok CANCEL while rendering: work sample deleted")
    # --- all presets: parameters sane, model runs (short), no clipping in the model ---
    for i, p in enumerate(PRESETS):
        pk, _, _ = model_render(dry[:200], i, MIXDEF[i], 1, 0, limit=3000)
        assert 0 < pk < 200000, (p[0], pk)
    print("  ok all %d presets render in the model (%s)" % (len(PRESETS), ", ".join(NAMES)))
    assert t.pad(0, 9)[0] == 0
    print("OK: REVERB.WAV built and tested")
