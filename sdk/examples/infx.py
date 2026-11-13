#!/usr/bin/env python3
"""INPUT FX - shared definitions for the live input effects inside MASTER.WAV (see master.py): presets, tables, the
bit-exact Python model of the per-frame DSP, and the addresses of the OS input path.

How it works (OS 1.31, firmware disassembly 2026-11-04, HW probe FXPROBE.WAV 2026-11-04):
  - the input interrupt (0x884C0DBC) writes 4 stereo frames per IRQ into the input ring 0x884882A8..0x8848C2A8
    (2048 frames x 8 bytes, s16 in the high half of each long) at [0xA5017020] and advances that pointer.
  - every sampler state reads the input ring only up to the pointer whose ADDRESS is in one of 5 literal pools
    (enter 0x884EC1B4, go-record 0x884EC4DC, idle meter 0x884EC80C, armed 0x884ECA50, waiting/recording 0x884ECCC4).
    INPUT FX points those 5 literals to its own pointer SPTR: the interrupt keeps writing raw frames, MASTER's block
    processor (ISR level, every 32 frames) runs the new frames through the effects IN PLACE and then advances SPTR, so
    the sampler (MPC Record, Audio Track, S500 Input...) only ever sees processed audio. Restored when switched off.
  - the input converter runs only while a recording page has the sampler entered (probe: In ring moving = 0 on other
    pages); the INPUT FX page enters it itself (as Audio Track) so a player can rehearse there.
  - "Record: Main" of the OS copies the OUTPUT ring (pre-MASTER) - untouched. SOURCE = MAIN here instead feeds the dry
    output mix through the effects into the input ring: record with "Rec In".
Memory: everything lives in the loader's 512 KB FX buffer (B + 0x60000, B = MGR[1]): state, queues, delay line.
"""
import math

FS = 44100
M32 = 0xFFFFFFFF
# ---- OS input path ----
IN_WR = 0xA5017020                          # interrupt's write pointer (address of the next frame)
RING, RING_END, RMASK = 0x884882A8, 0x8848C2A8, 0x3FF8
SMP_LITS = [0x884EC1B4, 0x884EC4DC, 0x884EC80C, 0x884ECA50, 0x884ECCC4]
SMP = 0x8849D114
SMP_ENTER, SMP_EXIT = 0x884EC06E, 0x884EC0EE
# Record page Monitor (disassembly 2026-11-05): SMP+0x14 (setter 0x884EC14E, only with source IN), applied by
# 0x884EC586(SMP): Monitor 0 or source MAIN -> the render's 4 input->output gains (0xA500812E/8122/8146/813A) = 0
SMP_MON, SMP_MONAPPLY = 0x14, 0x884EC586
REC_PAGE = 0x88490E60                       # the OS Record page object (S500 menu, HW)

# ---- FX buffer layout (offsets from the FX buffer start) ----
IX_OFF = 0x0                                # state block (r13 = coefficients, r12 = IX + 0x40 = states)
MONQ_OFF, MAINQ_OFF, QN = 0x400, 0x800, 256  # queues: 256 packed frames (L low16 | R high16)
DLY_OFF, DLY_N = 0x40000, 0x10000           # delay line: 65536 packed frames (1.486 s)
# coefficient block (r13), each a 32-bit word; written by the main loop
(C_RUN, C_SPOS, C_SPTR, C_SRC, C_DW, C_DT, C_FB, C_MIX, C_CLP, C_CHP, C_QM, C_PING, C_MODD, C_MODINC, C_DON,
 C_KILL) = [4 * i for i in range(16)]
# states (r12 = IX + 0x40)
(S_FLL, S_FLR, S_FHL, S_FHR, S_PH, S_MONW, S_MONR, S_MQW, S_MQR, S_TFL, S_TFR, S_MONOK, S_MQOK) = [4 * i for i in range(13)]
MON_START, MON_MIN, MON_MAX, MON_TGT = 48, 32, 160, 48

# ---- delay presets: (name, time index, feedback %, mix %, low-pass Hz, high-pass Hz, 12 bit, ping pong,
#                       wow depth (frames), wow rate Hz) ----
TIMES = ["1/32 ", "1/16T", "1/16 ", "1/8T ", "1/16D", "1/8  ", "1/4T ", "1/8D ", "1/4  ", "1/2  ",
         " 40ms", " 60ms", " 80ms", " 90ms", "100ms", "120ms", "150ms", "200ms", "250ms", "300ms"]
NNOTE = 10
BEATS = [1 / 8, 1 / 6, 1 / 4, 1 / 3, 3 / 8, 1 / 2, 2 / 3, 3 / 4, 1, 2]          # of a quarter note
MSV = [40, 60, 80, 90, 100, 120, 150, 200, 250, 300]
TI = {t.strip(): i for i, t in enumerate(TIMES)}
DPRESETS = [
    ("DP4 DDL", TI["1/4"], 45, 30, 11000, 50, 0, 0, 0, 0),     # Ensoniq DP/4-inspired: clean, bright, long repeats
    ("DUB TAPE", TI["1/4"], 60, 35, 2500, 150, 0, 0, 24, 0.8),
    ("1/8 SYNC", TI["1/8"], 35, 30, 9000, 80, 0, 0, 0, 0),
    ("SLAPBACK", TI["90ms"], 10, 35, 6000, 100, 0, 0, 0, 0),
    ("1/8 DOT", TI["1/8D"], 40, 30, 8000, 100, 0, 0, 0, 0),
    ("1/4 SYNC", TI["1/4"], 35, 30, 8000, 80, 0, 0, 0, 0),
    ("PING PONG", TI["1/8"], 45, 35, 8000, 100, 0, 1, 0, 0),
    ("1/16", TI["1/16"], 30, 30, 9000, 300, 0, 0, 0, 0),
    ("ECHO", TI["1/8"], 55, 35, 10000, 200, 0, 0, 0, 0),
    ("DDL 12BIT", TI["1/8"], 40, 30, 7000, 120, 1, 0, 0, 0),
    ("DDL 16BIT", TI["1/8"], 40, 30, 12000, 60, 0, 0, 0, 0),
]                       # user 2026-11-06: DP4 DDL first, the two DDLs last
DSTRENGTH, FB_MAX = 1.25, 75   # user 2026-11-06: delay strength +25%: preset feedback and mix x1.25 (5% steps, fb <= 75)
def _r5(v): return int(5 * math.floor(v / 5 + 0.5))
DPRESETS = [(p[0], p[1], min(FB_MAX, _r5(p[2] * DSTRENGTH)), _r5(p[3] * DSTRENGTH)) + p[4:] for p in DPRESETS]
DNAMES = [" OFF"] + [" " + p[0] for p in DPRESETS]                      # choice index 0 = OFF
PCT5 = ["%2d" % v for v in range(0, 100, 5)]                             # MIX / FB choices 0..95
def pct_index(p): return min(len(PCT5) - 1, int(round(p / 5)))
def q15(x): return max(0, min(32767, int(round(x * 32768))))
def onepole(f): return q15(1 - math.exp(-2 * math.pi * f / FS))
def dcoef(i):
    """per-preset table row: CLP, CHP, QM, PING, MODD (Q8 frames), MODINC"""
    _, _, _, _, lp, hp, b12, ping, wow, rate = DPRESETS[i]
    return [onepole(lp), onepole(hp), 0xFFFFFFF0 if b12 else M32, ping, int(round(wow * 256)),
            int(round(rate / FS * 2 ** 32)) & M32]
DTAB = [dcoef(i) for i in range(len(DPRESETS))]
TMUL = [int(round(b * 4096)) for b in BEATS]                             # DT = FPB * TMUL >> 12
TMS = [int(round(ms * FS / 1000)) for ms in MSV]
def pct_q12(idx): return int(round(idx * 5 / 100 * 4096))

# ---- MODULATION (2026-11-05, replaces the live reverb: user "get rid of the reverbs, add chorus and phaser"):
#      one chorus OR phaser, BEFORE the delay (mod -> delay), both may run together (a preset with the "no delay"
#      flag would switch the delay off - none has it since the mono phaser, 2026-11-05). Per frame, ix_mod.
#      y = x + (wet - x) * MIX: MIX 50 = the classic 50/50 blend (phaser notches, Juno chorus), 95 = nearly all wet.
#      v2 (HW 2026-11-05: v1 "metallic lofi", phasers rang when idle = 16-bit truncation limit cycles): 24-bit inside,
#      64-bit products (mul16: (a * b) >> 16), Hermite interpolation on the chorus tap, ONE (mono) chain for the
#      phaser; stereo by INV: R = dry - wet (the Juno-60 trick, also the classic stereo phaser), CPU halved.
#  chorus: (L+R)/2 into a 2048-frame line; one tap at CD + DEP * (lfo - 1/2) (Q16 frames), 4-point Hermite,
#          one-pole low-pass in 24 bit (BBD darkness).
#  phaser: (L+R)/2 << 7 (24 bit) + FB * last output -> N first-order allpasses y = x' + a (x - y'); a (Q16) set once
#          per call (block) from the lfo: sweep position on a log-frequency table (16 steps / octave from 40 Hz),
#          plus a fixed per-stage offset (VIBE: staggered stages as the Uni-Vibe's).
#  lfo: triangle, or SHAPE 1 = smoothstep of the triangle (close to a sine). ----
MLINE_OFF, MLN, MMSK = 0x1000, 2048, 0x7FF      # chorus line (s16) at FX buffer + 0x1000
MB_OFF, MS_OFF = 0x80, 0x100                    # coefficient block (written by the main loop) and states, from IX
(M_ON, M_INC, M_MIX, M_INV, M_SHAPE, M_P1, M_P2, M_P3, M_FB) = [4 * i for i in range(9)]
M_OFFS, M_GEN = 36, 60                          # 6 stage offsets (phaser); generation (+1 at every preset load)
(T_PH, T_W, T_LP) = [4 * i for i in range(3)]
# phaser: Z[0..6] at MS + 16 = the previous value at each point of the chain (input, then each stage's output: one
# multiply per stage, y = Z[k] + a (x - Z[k + 1])), A[0..5] at MS + 44 = coefficients; N stages use 6-N..5
T_Z, T_A = 16, 44
S_FAP, S_SP0, S_N = 0x34, 0x38, 0x3C           # in the INPUT FX state block: frame array pointer, ring start, frames
FA_OFF, NMAX = 0x8000, 64                      # frame array (32 B per frame: y L, y R, wet L, wet R, x L, x R)
# frames per call (2026-11-05, HW "all effects noisy"): a late block left 64 frames -> a double-length call -> the next
# block late too -> every other output block missed (sent without the monitor). <= 40: a backlog is caught up gradually.
NCAP = 40
APT_F0, APT_N = 40.0, 128                      # allpass coefficient table: 40 Hz * 2^(i/16), i = 0..128 (10.24 kHz)
def apt_idx(f): return int(round(16 * math.log2(f / APT_F0) * 256))          # Q8 table index
def apt(i):
    t = math.tan(math.pi * APT_F0 * 2 ** (i / 16) / FS); return max(-65535, min(65535, int(round((t - 1) / (t + 1) * 65536))))
APT = [apt(i) for i in range(APT_N + 1)]       # Q16
IMAX = APT_N * 256 - 1
X24 = 0x7FFFFF
MRATES = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.51, 0.60, 0.70, 0.86, 1.00, 1.20, 1.50, 2.00, 2.50, 3.00, 4.00,
          5.00, 6.00, 8.00, 9.75]
RATEN = ["%4.2f" % r for r in MRATES]
def minc(r): return int(round(r / FS * 2 ** 32)) & M32
def q16(x): return int(round(x * 65536))
# (name, type 1 chorus / 2 phaser, rate Hz, mix %, shape, R inverted, no delay, voicing)
#   chorus voicing: centre delay ms, depth +- ms, wet low-pass Hz
#   phaser voicing: stages, sweep low Hz, sweep high Hz, feedback, stage offsets (octaves)
MPRESETS = [
    ("JUNO I",    1, 0.51, 50, 0, 1, 0, (3.5, 1.85, 7500)),      # Juno-60 chorus I: 0.5 Hz triangle, 1.66..5.35 ms
    ("JUNO II",   1, 0.86, 50, 0, 1, 0, (3.5, 1.85, 7500)),      # chorus II: faster
    ("JUNO I+II", 1, 9.75, 50, 0, 1, 0, (3.5, 0.20, 7500)),      # both buttons: fast, shallow shimmer
    ("70S CHOR",  1, 0.70, 50, 1, 0, 0, (7.0, 3.00, 5000)),      # CE-style: deeper, darker, sine, mono
    ("EP PHASER", 2, 0.40, 50, 1, 1, 0, (4, 200, 2000, 0.15, None)),   # slow soft 4-stage for Rhodes / Wurli, wide
    ("GTR PHASE", 2, 1.20, 50, 0, 0, 0, (4, 150, 3500, 0.45, None)),   # 4-stage, faster, feedback (script style)
    ("VIBE",      2, 3.00, 50, 1, 0, 0, (4, 300, 900, 0.0, [0, -2.0, 1.3, 2.6])),   # staggered stages, throb
    ("SLOWPHASE", 2, 0.15, 50, 0, 1, 0, (6, 100, 4000, 0.55, None)),   # deep 6-stage sweep, wide
]                       # DP4 PHASE removed (user 2026-11-05: dropouts with it)
MNAMES = [" OFF"] + [" " + p[0] for p in MPRESETS]
ONE_AT_A_TIME = True    # user 2026-11-05 (stability): a MOD preset switches the DELAY off and the other way round
STRENGTH = 1.44         # user 2026-11-05: "increase the strength of the mod fx ~20%, not just the mix": chorus depth,
                        # phaser sweep range (octaves, same centre) and feedback (max 0.7) x 1.2; then +20% more -> 1.44
def mrow(i):
    """preset table row (16 words): type, default mix index, default rate index, then MB words 3..14
    (INV, SHAPE, P1, P2, P3, FB, OFF0..OFF5), no-delay flag"""
    name, typ, rate, mix, shape, inv, nodly, v = MPRESETS[i]
    ri = min(range(len(MRATES)), key=lambda k: abs(MRATES[k] - rate))
    row = [typ, pct_index(mix), ri, inv, shape]
    if typ == 1:
        cd, dep, lp = v
        dep *= STRENGTH
        row += [q16(cd * FS / 1000), q16(dep * FS / 1000), q16(1 - math.exp(-2 * math.pi * lp / FS)), 0] + [0] * 6
    else:
        n, f0, f1, fb, offs = v
        fc, half = math.sqrt(f0 * f1), math.log2(f1 / f0) / 2 * STRENGTH
        f0, f1 = max(APT_F0, fc / 2 ** half), min(APT_F0 * 2 ** (APT_N / 16) * 0.95, fc * 2 ** half)
        fb = min(0.7, fb * STRENGTH)
        offs = (offs or [0] * n) + [0] * (6 - n)
        row += [n, apt_idx(f0), apt_idx(f1) - apt_idx(f0), q16(fb)] + [int(round(16 * o * 256)) & M32 for o in offs]
    return row + [1 if ONE_AT_A_TIME else nodly]
MTAB = [mrow(i) for i in range(len(MPRESETS))]
assert all(len(r) == 16 for r in MTAB)
def lfo(ph, sine):
    """lfo value Q16 (0..65534) from the phase (Q32): triangle, or smoothstep(triangle) (Q15 inside)"""
    u = (ph >> 16) & 0xFFFF
    if u & 0x8000: u = (~u) & 0xFFFF
    t = u << 1
    if sine:
        t15 = t >> 1; t2 = (t15 * t15) >> 15; t = ((t2 * (98304 - 2 * t15)) >> 15) << 1
    return t

# ---- REVERB (2026-11-06, back by user request: "build a reverb input effect without going over the limit"; 2 presets,
#      one effect at a time with DELAY / MOD). The 2026-11-05 live reverb with its HW fixes: grain OFF (hiss) and 4 input
#      diffusers (metallic halls). Pre-delay -> input band-limit -> 4 allpass diffusers (g 1/2) -> 4 damped combs (one
#      write counter for every line, lines of 2048 s16) -> L = sum, R = alternating signs (computed as 2 x even - sum),
#      each through one allpass (g 1/2) -> output gain x MIX, low cut, high cut -> added to y. On the block (ix_rvb),
#      equal to the per-frame model because every line is longer than a call (NMAX). ----
#      CPU (emulator 2026-11-06): at 44.1 kHz the reverb block cost 17.8k instructions (HW overloaded at ~15-17.6k,
#      stable at ~11k) -> the reverb runs at HALF RATE: frame pairs (calls are always a multiple of 4 frames) -> one
#      22.05 kHz input (L0+R0+L1+R1)/16; the wet goes back linearly interpolated (frame 0: (previous + new) / 2,
#      frame 1: new). Tails are high-cut at 5.5-6.5 kHz anyway (Nyquist now 11 kHz). Lengths / coefficients for 22.05k.
REV_OFF, RLN, RMSK, NRL = 0x10000, 2048, 0x7FF, 11   # PD, C0..C3, APL, APR, D0..D3 (interleaved): 11 x 4 KB
FSR = FS // 2
COMBS0 = [558, 594, 639, 678]; APL0, APR0 = 278, 290
DIFF = [107, 79, 281, 199]
RB_OFF, RS_OFF = 0x180, 0x1C0                  # coefficient block (main loop) and states, from IX
(R_ON, R_L0, R_L1, R_L2, R_L3, R_APL, R_APR, R_PDN, R_CBW, R_DAMP, R_FB, R_CHP, R_CLP, R_OGM, R_GEN, R_OGB) = \
    [4 * i for i in range(16)]
(Q_RW, Q_BW, Q_F0, Q_F1, Q_F2, Q_F3, Q_HPL, Q_HPR, Q_LPL, Q_LPR, Q_PVL, Q_PVR) = [4 * i for i in range(12)]
# (name, size, RT60 s, damping, input bandwidth, pre-delay ms, low cut Hz, high cut Hz, wet, mix %)
RPRESETS = [
    ("ASR HALL", 1.15, 2.00, 0.65, 0.55, 18, 160, 5500, 0.70, 35),   # in the style of the Ensoniq ASR-10 hall
    ("VINT PLT", 0.85, 1.40, 0.55, 0.65, 10, 200, 6500, 0.75, 40),   # vintage plate
]
RNAMES = [" OFF"] + [" " + p[0] for p in RPRESETS]
WET_BOOST = 2.0
def rrow(i, og):
    """table row (14 words): default MIX index, L0..L3, APL, APR, PDN, CBW, DAMP, FB, CHP, CLP, output gain (Q12)"""
    name, size, rt, damp, bw, pd, lc, hc, wet, mix = RPRESETS[i]
    L = [min(RLN - 1, int(round(c * size))) for c in COMBS0]
    g = 10 ** (-3 * (sum(L) / 4) / (rt * FSR))
    def op(f): return q15(1 - math.exp(-2 * math.pi * f / FSR))
    # one-pole coefficients of the 44.1 kHz voicing (bandwidth, comb damping) moved to 22.05 kHz: same cutoff
    return [pct_index(mix)] + L + [APL0, APR0, max(NMAX, int(round(pd * FSR / 1000))), q15(1 - (1 - bw) ** 2),
                                   q15(1 - (damp * 0.8) ** 2), q15(min(0.97, g)), op(lc), op(hc), og]
ROGS = [20902, 20373]   # output gains: wet RMS at MIX 100 % = input RMS x wet x WET_BOOST (noise -12 dBFS, 1.5 s;
                        # half-rate model 2026-11-06; the 44.1k B_fix versions were 20873, 19456)
RTAB = [rrow(i, ROGS[i]) for i in range(len(RPRESETS))]
def rblock(i, mix_idx, gen=1):
    """the 16 words of the reverb block (RB) as the main loop writes them for preset i (0-based)"""
    r = RTAB[i]
    return [1] + r[1:13] + [(r[13] * pct_q12(mix_idx)) >> 12, gen, r[13]]

def s32(v): v &= M32; return v - (1 << 32) if v & 0x80000000 else v
def mul16(a, b): return s32((a * b) >> 16)
def c16(v): return -32768 if v < -32768 else 32767 if v > 32767 else v
def x16(v): v &= 0xFFFF; return v - 0x10000 if v & 0x8000 else v

def delay_time(time_idx, fpb):
    if time_idx < NNOTE: dt = (fpb * TMUL[time_idx]) >> 12
    else: dt = TMS[time_idx - NNOTE]
    return max(NMAX, min(DLY_N - 2, dt))

class Model:
    """bit-exact model of the per-frame input processing (ix_proc)"""
    def __init__(self):
        self.dly = [0] * DLY_N; self.s = dict(FLL=0, FLR=0, FHL=0, FHR=0, PH=0); self.dw = 0
    def frame(self, c, xl, xr):
        """c: dict DON, DT, FB, MIX, CLP, CHP, QM, PING, MODD, MODINC. Returns (yl, yr, el, er)"""
        if not c['DON']: return xl, xr, 0, 0
        s = self.s
        s['PH'] = (s['PH'] + c['MODINC']) & M32
        t = s['PH'] >> 16
        if t & 0x8000: t = (~t) & 0xFFFF
        off = (t * c['MODD']) >> 15
        f = off & 255
        i0 = (self.dw - c['DT'] - (off >> 8)) & 0xFFFF
        wa = self.dly[i0]; wb = self.dly[(i0 - 1) & 0xFFFF]
        aL, bL = x16(wa), x16(wb); dl = aL + (s32((bL - aL) * f) >> 8)
        aR, bR = s32(wa) >> 16, s32(wb) >> 16; dr = aR + (s32((bR - aR) * f) >> 8)
        fl_ = []
        for d, LP, HP in ((dl, 'FLL', 'FHL'), (dr, 'FLR', 'FHR')):
            s[LP] = s[LP] + (s32((d - s[LP]) * c['CLP']) >> 15)
            s[HP] = s[HP] + (s32((s[LP] - s[HP]) * c['CHP']) >> 15)
            fl_.append(s32((s[LP] - s[HP]) & c['QM']))
        fl, fr = fl_
        tl = s32(fl * c['FB']) >> 12; tr = s32(fr * c['FB']) >> 12
        if c['PING']: wl = c16(((xl + xr) >> 1) + tr); wr = c16(tl)
        else: wl = c16(xl + tl); wr = c16(xr + tr)
        self.dly[self.dw] = (wl & 0xFFFF) | ((wr & 0xFFFF) << 16); self.dw = (self.dw + 1) & 0xFFFF
        el = s32(fl * c['MIX']) >> 12; er = s32(fr * c['MIX']) >> 12
        return c16(xl + el), c16(xr + er), el, er
    def rv_init(self):
        """modulation: line and states cleared (as the main loop does at every preset load)"""
        self.ml = [0] * MLN
        self.t = dict(PH=0, W=0, LP=0, Z=[0] * 7, A=[0] * 6)
    @staticmethod
    def _mb(mb):
        return dict(ON=mb[0], INC=mb[1], MIX=s32(mb[2]), INV=mb[3], SHAPE=mb[4], P1=s32(mb[5]), P2=s32(mb[6]),
                    P3=s32(mb[7]), FB=s32(mb[8]), OFF=[s32(mb[9 + k]) for k in range(6)])
    def mod_block(self, mb, kill=0):
        """at the start of every call (block): the phaser's allpass coefficients from the lfo (ix_modblk)"""
        if not hasattr(self, 't'): self.rv_init()
        b = self._mb(mb)
        if b['ON'] != 2 or kill: return
        T = self.t
        t = lfo(T['PH'], b['SHAPE'])
        pos = s32(b['P2'] + (((t * b['P3']) & M32) >> 16))
        s0 = 6 - b['P1']
        for k in range(b['P1']):
            idx = s32(pos + b['OFF'][k]); idx = 0 if idx < 0 else IMAX if idx > IMAX else idx
            i, f = idx >> 8, idx & 255
            T['A'][s0 + k] = s32(APT[i] + (s32((APT[i + 1] - APT[i]) * f) >> 8))
    def mod(self, mb, xl, xr):
        """one frame of chorus / phaser (ix_mod). Returns (yl, yr)"""
        if not hasattr(self, 't'): self.rv_init()
        b = self._mb(mb); T = self.t
        if not b['ON']: return xl, xr
        T['PH'] = (T['PH'] + b['INC']) & M32
        def mix(x0, w): return c16(s32(x0 + (s32((w - x0) * b['MIX']) >> 12)))
        if b['ON'] == 1:
            L = self.ml; W = T['W']
            L[W] = x16((xl + xr) >> 1)
            t = lfo(T['PH'], b['SHAPE'])
            d = s32(b['P1'] + mul16((t - 32768) << 1, b['P2']))
            whole, u = (d & M32) >> 16, d & 0xFFFF
            i0 = (W - whole) & MMSK
            p0, p1, p2, pm1 = L[i0], L[(i0 - 1) & MMSK], L[(i0 - 2) & MMSK], L[(i0 + 1) & MMSK]
            c1 = (p1 - pm1) >> 1
            c2 = pm1 - ((5 * p0) >> 1) + 2 * p1 - (p2 >> 1)
            c3 = ((p2 - pm1) >> 1) + ((3 * (p0 - p1)) >> 1)
            v = s32(p0 + mul16(u, s32(c1 + mul16(u, s32(c2 + mul16(u, c3))))))
            T['LP'] = s32(T['LP'] + mul16(b['P3'], s32((v << 8) - T['LP'])))
            wet = s32(T['LP'] + 128) >> 8
            T['W'] = (W + 1) & MMSK
        else:
            Z, A = T['Z'], T['A']; s0 = 6 - b['P1']
            x = s32(((xl + xr) << 7) + mul16(Z[6], b['FB']))
            x = -X24 - 1 if x < -X24 - 1 else X24 if x > X24 else x
            prev = Z[s0]
            for k in range(s0, 6):
                old = Z[k + 1]
                y = s32(prev + mul16(A[k], s32(x - old)))
                Z[k] = x; prev = old; x = y
            Z[6] = x
            wet = s32(x + 128) >> 8
        return mix(xl, wet), mix(xr, -wet if b['INV'] else wet)

    def rvb_init(self):
        """reverb: lines and states cleared (as the main loop does at every reverb preset load)"""
        self.rl = [[0] * RLN for _ in range(NRL)]
        self.q = {k: 0 for k in ('RW', 'BW', 'F0', 'F1', 'F2', 'F3', 'HPL', 'HPR', 'LPL', 'LPR', 'PVL', 'PVR')}
    def reverb_block(self, r, ys):
        """the reverb on one call's frames (ix_rvb): ys = [[yl, yr], ...] (even count), changed in place, by pairs"""
        if not hasattr(self, 'q'): self.rvb_init()
        if not r[0]: return
        q = self.q
        for p in range(len(ys) // 2):
            f0, f1 = ys[2 * p], ys[2 * p + 1]
            wl, wr = self.rvb_pair(r, (f0[0] + f0[1] + f1[0] + f1[1]) >> 4)
            f0[0] = c16(f0[0] + ((q['PVL'] + wl) >> 1)); f0[1] = c16(f0[1] + ((q['PVR'] + wr) >> 1))
            f1[0] = c16(f1[0] + wl); f1[1] = c16(f1[1] + wr)
            q['PVL'], q['PVR'] = wl, wr
    def rvb_pair(self, r, u):
        """one 22.05 kHz step of the reverb core: u = mono input. Returns the wet (L, R)"""
        L0, L1, L2, L3, APL, APR, PDN, CBW, DAMP, FB, CHP, CLP, OGM = [s32(v) for v in r[1:14]]
        q, B = self.q, self.rl; w = q['RW']
        B[0][w] = x16(u)
        x = B[0][(w - PDN) & RMSK]
        q['BW'] += s32((x - q['BW']) * CBW) >> 15; x = q['BW']
        for j, Ld in enumerate(DIFF):                                # input diffusers, allpass g 1/2
            bo = B[7 + j][(w - Ld) & RMSK]; v = x + (bo >> 1)
            if j: v = c16(v)
            B[7 + j][w] = x16(v); x = bo - (v >> 1)
        aca = ace = 0
        for k, Lk in enumerate((L0, L1, L2, L3)):
            yk = B[1 + k][(w - Lk) & RMSK]
            F = 'F%d' % k; q[F] += s32((yk - q[F]) * DAMP) >> 15
            B[1 + k][w] = c16(x + (s32(q[F] * FB) >> 15))
            aca += yk
            if k % 2 == 0: ace += yk
        o = []
        for ln, u, Ln in ((5, aca, APL), (6, 2 * ace - aca, APR)):
            bo = B[ln][(w - Ln) & RMSK]; o.append(bo - u); B[ln][w] = c16(u + (bo >> 1))
        q['RW'] = (w + 1) & RMSK
        out = []
        for v, HP, LP in ((o[0], 'HPL', 'LPL'), (o[1], 'HPR', 'LPR')):
            v = c16(s32(c16(v >> 1) * OGM) >> 12)
            q[HP] += s32((v - q[HP]) * CHP) >> 15; v = v - q[HP]
            q[LP] += s32((v - q[LP]) * CLP) >> 15; out.append(q[LP])
        return out

    def full(self, c, mb, xl, xr):
        """modulation then delay (ix_proc, per frame): (yl, yr, wet L = yl - xl, wet R = yr - xr)"""
        ml, mr = (xl, xr) if c.get('KILL') else self.mod(mb, xl, xr)
        yl, yr, _, _ = self.frame(c, ml, mr)
        return yl, yr, yl - xl, yr - xr
    def block(self, c, mb, rb, xs):
        """one ix_proc call: modulation + delay per frame, then the reverb on the block. -> [(yl, yr, wet L, wet R)]"""
        ys = [list(self.full(c, mb, xl, xr)[:2]) + [xl, xr] for xl, xr in xs]
        if not c.get('KILL'): self.reverb_block(rb, ys)
        return [(y[0], y[1], y[0] - y[2], y[1] - y[3]) for y in ys]

if __name__ == '__main__':
    m = Model(); c = dict(DON=1, DT=100, FB=2048, MIX=4096, CLP=32767, CHP=0, QM=M32, PING=0, MODD=0, MODINC=0)
    out = [m.frame(c, 10000 if i == 0 else 0, 0)[0] for i in range(400)]
    assert out[0] == 10000 and out[100] > 9000 and out[200] > 4000 and all(v == 0 for v in out[1:100]), out[:3]
    print("infx model ok: echo at 100 frames %d, 200 frames %d" % (out[100], out[200]))
    # modulation: every preset runs, stays bounded, changes the signal
    for i, p in enumerate(MPRESETS):
        m = Model(); m.rv_init(); r = MTAB[i]
        mb = [r[0], minc(p[2]), pct_q12(r[1])] + r[3:15] + [0]
        mx = dif = 0
        for n in range(44100):
            if n % 32 == 0: m.mod_block(mb)
            x = int(12000 * math.sin(n * 2 * math.pi * 440 / FS)); yl, yr = m.mod(mb, x, x)
            mx = max(mx, abs(yl), abs(yr)); dif += abs(yl - x) + abs(yr - x)
        print("  %-10s peak %5d  mean |y - x| %5d" % (p[0], mx, dif // 88200))
