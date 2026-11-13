#!/usr/bin/env python3
"""MASTER -> MASTER.WAV: a Pultec-style EQ into a dbx 160-style compressor, live on the MPC's output (Pad FX list).

    python sdk/examples/master.py          -> builds MASTER.WAV and tests it in the emulator

On the MPC500: LOAD MASTER.WAV (after HUB.WAV); MODE + pad 9 -> Pad FX -> MASTER (or MODE -> OTHER -> "Master").
NOT together with Master FX (both use the same audio hook: whichever hooks first wins, the other finds the OS code
changed and stays off). The cursor on the page name + the wheel chooses the page; every value is a wheel choice:
  MASTER FX "MASTER FX     ON" / "GR -3dB #"      on / off (2.9 ms fade); gain reduction meter. Stays on when you
                                                   leave the page, until you switch it off.
  LOW EQ    "LOW EQ     60Hz" / "BOOST  3 ATTEN 2"  Pultec low: 20/30/60/100 Hz; boost and atten on the SAME frequency
            (both up = the bump with a dip above it). Boost 10 = +13.5 dB shelf, atten 10 = -17.5 dB.
  HIGH EQ   "HIGH EQ   10kHz" / "BOOST  3 WIDTH 5"  bell 3..16 kHz, boost 10 = +16 dB, width 0 sharp .. 10 broad.
  HIGH CUT  "HIGH CUT  10kHz" / "ATTEN  0"          gentle high shelf cut 5/10/20 kHz, 10 = -16 dB.
  DBX 160   "DBX 160 THR -3dB" / "RATIO  3:1 "      threshold -40..+10 dB, ratio 1:1 .. INF.
  DBX OUT   "DBX OUT     +0dB" / "KNEE SOFT"        output gain -10..+20 dB (default 0: v2), knee HARD / SOFT (OverEasy style).
Defaults (user 2026-11-04): low 60 Hz boost 3 atten 2, high 10 kHz boost 3 width 5, cut 0; THR -3 dB, 3:1, SOFT, +2 dB.

Sound (block of 32 frames, Pultec -> dbx):
  Low:  y = X + B*LP2(X) - A*LP1(X): LP2 = two one-poles at f/0.643 (-3 dB at f), LP1 = one pole at f; boost and atten
        curves differ in shape, so both up give the low bump with a dip above it (the "Pultec trick").
  High: zero-delay state-variable band-pass at f, Q from width, added with the boost gain; cut: one-pole shelf.
  dbx:  stereo-linked TRUE RMS detector on (L+R)/2: mean square of the block into a one-pole with tau = 36 ms. This one
        detector gives the dbx 160 program-dependent attack (about 16 ms for a 10 dB jump, 6 ms for 20, 3 ms for 30)
        and its ~120 dB/s release (exponential power decay = linear in dB). Gain computer in log2 (Q8): feed-forward,
        exact ratio, soft knee 10 dB wide (quadratic), output gain; gain ramped across each block.`n  v2 (user: "overdriven in a bad way"; all samples normalized): full-scale input (SH 16, no early clip), EQ peaks kept`n  (no clip inside), soft limit at the end, threshold 0 dB = RMS -9 dBFS, output 0 dB by default.
  "0 dB" of the threshold = RMS 8192 in the hook's 16-bit scale (the MPC mix peaks around 7000 there: HW probe of the
  Master FX); calibrate by ear / with the meter.
INPUT FX (2026-11-04, second page "ix", opened from Pad FX: MODE + pad 9 -> INPUT FX): live modulation + delay on what
the MPC RECORDS (MPC Record, Audio Track, S500 Input), heard by the player; see infx.py. Screens (wheel on the name):
  "INPUT FX      ON" / "SOURCE INPUT 23%"   on/off; INPUT (instrument) or MAIN (dry mix -> FX -> record "Rec In"); CPU
  "DELAY  DDL 12BIT" / "MIX30 FB40 1/8  "   11 delay presets (first choice OFF), mix, feedback, time (tempo or ms)
  "MOD      JUNO I" / "MIX50 RATE 0.51 "   8 chorus / phaser presets (first choice OFF), mix, lfo rate (Hz)
  "REVERB ASR HALL" / "MIX35           "   2 reverb presets (2026-11-06, ASR HALL / VINT PLT; first choice OFF), mix
  One effect at a time: a delay, MOD or reverb preset switches the other two OFF. The reverb runs at half rate
  (infx.py). The "M 12" missed-block counter was removed 2026-11-06 (room for the reverb).
  MASTER FX and INPUT FX never run together (2026-11-06, CPU): opening INPUT FX with MASTER FX on shows
  "DISABLE MSTR FX?" / "DO IT / CANCEL  ": DO IT = MASTER FX off, CANCEL = back to the Pad FX list. INPUT FX off (or the
  page left without switching it on) -> MASTER FX on again, only if it was on before. MASTER FX switched on while INPUT
  FX runs -> INPUT FX off.
Audio hook, interrupt split, latency (3 blocks = 2.2 ms), cache rules and the load guard: copied from the official
Master FX (sdk/addons/wav_masterfx.py, HW-proven), with its buffers inside this add-on's scratch page (no 8-page
reservation): frame struct +0x400, ISR variables +0x600 (P1 only), IN +0x800, OUT +0xC00.
"""
import sys, os, struct, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import PageAddon, SET_FOCUS
import pages as _pages
from mpcmod import EVQ, LOAD_SCRATCH, SCRATCH_MAGIC, FXMARK_OFF, RESERVE, FXBUF_OFF

# ---------------- ISR variables only through P1 (Master FX) ----------------
CSET = set()
def _p1(m, slot):
    from mpcmod import SCRATCH
    v = slot
    if LOAD_SCRATCH <= v < LOAD_SCRATCH + 0x1000 and m.scratch != LOAD_SCRATCH and m.scratch != SCRATCH:
        v = m.scratch + (v - LOAD_SCRATCH)
    return v & 0xDFFFFFFF
def ld(m, slot, r):
    if slot in CSET: m.a.movl_pool(_p1(m, slot), 1); m.a.movl_ld(1, r)
    else: _pages.ld(m, slot, r)
def st(m, r, slot):
    if slot in CSET: m.a.movl_pool(_p1(m, slot), 1); m.a.movl_st(r, 1)
    else: _pages.st(m, r, slot)
def sti(m, val, slot):
    if slot in CSET: m.a.movl_pool(_p1(m, slot), 1); m.imm32(val, 0); m.a.movl_st(0, 1)
    else: _pages.sti(m, val, slot)

RLIT, RENDER, ISR_EXIT = 0x884C10C8, 0xA50089B0, 0x884C0D50
OUT_RPTR, OUT_BASE, OUT_SIZE = 0x881C05F4, 0x884842A8, 0x4000
LAT, SH = 3, 16                              # v2: the FULL s32 scale (Master FX's 14 clipped hot mixes 12 dB early: user's samples are normalized)
BLK, BSH = 32, 5
GSTEP = 1024
EXITLIT, FRAMES = 0x884C10B4, 0x881C05F0
TXPATCH = 0x884C0E4A
TXORIG = [0x6026, 0xC218] * 8
TCOR1, TCNT1, TICKS = 0xFFFFFEA0, 0xFFFFFEA4, 0x881C05C8
NWIN, LOADMAX = 32, 60
FS = 44100
M32 = 0xFFFFFFFF
ARR_OFF, CP_OFF, IN_OFF, OUT_OFF = 0x400, 0x600, 0x800, 0xC00      # inside the scratch page
FSTR = 16                                                          # frame struct: W L, W R, dry L, dry R
FW, FWR, FDL, FDR = 0, 4, 8, 12
CACHE_SPAN = 0x4000                          # ISR code incl. INPUT FX (2026-11-04)

# ---------------- the sound: parameters -> coefficient tables ----------------
LFREQS = [20, 30, 60, 100]; HFREQS = [3000, 4000, 5000, 8000, 10000, 12000, 16000]; CFREQS = [5000, 10000, 20000]
THRS = list(range(-40, 11)); OUTS = list(range(-10, 21))
RATIOS = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0, 8.0, 10.0, 20.0, None]
RATN = [" 1:1 ", "1.5:1", " 2:1 ", "2.5:1", " 3:1 ", " 4:1 ", " 6:1 ", " 8:1 ", "10:1 ", "20:1 ", " INF "]
def q(x, b): return int(round(x * (1 << b)))
def opc(f): return 1 - math.exp(-2 * math.pi * f / FS)
T_LP2 = [q(opc(f / 0.643), 24) for f in LFREQS]
T_LP1 = [q(opc(f), 24) for f in LFREQS]
T_B = [q(10 ** (13.5 * k / 10 / 20) - 1, 16) for k in range(11)]
T_A = [q(1 - 10 ** (-17.5 * k / 10 / 20), 16) for k in range(11)]
T_BH = [10 ** (16 * k / 10 / 20) - 1 for k in range(11)]
def svf(f, w):
    Qv = 2.5 * (0.5 / 2.5) ** (w / 10)                       # width 0: Q 2.5 (sharp) .. 10: Q 0.5 (broad)
    g = math.tan(math.pi * f / FS); k = 1 / Qv; a1 = 1 / (1 + g * (g + k)); a2 = g * a1; a3 = g * a2
    return [q(a1, 16), q(a2, 16), q(a3, 16)], k
T_SVF = []
for f in HFREQS:
    for w in range(11):
        c, k = svf(f, w); T_SVF += c + [k]                      # k (float) -> KB below, per boost
T_CH = [q(opc(f), 24) for f in CFREQS]
T_AH = [q(1 - 10 ** (-16 * k / 10 / 20), 16) for k in range(11)]
DBU = 256 / (10 * math.log10(2))                               # log2-power Q8 units per dB (85.04)
RMS0 = 32768 * 10 ** (-9 / 20)                                 # v2: "0 dB" = RMS -9 dBFS (a loud, normalized mix)
REF = math.log2(RMS0 ** 2 / 64) * 256                           # the detector sees ((L+R)/16)^2
T_THR = [int(round(REF + t * DBU)) for t in THRS]
T_SLOPE = [0 if r is None else 0 for r in RATIOS]
T_SLOPE = [4096 if r is None else q(1 - 1 / r, 12) for r in RATIOS]
T_OUT = [int(round(o * DBU / 2)) for o in OUTS]                # log2-AMPLITUDE Q8 units
T_EXP2 = [int(round(32768 * 2 ** (i / 64))) for i in range(64)]     # 0.094 dB steps (index = frac >> 2)
T_LOG2 = [int(round(256 * math.log2(1 + i / 32))) for i in range(32)]
TAU = 0.036
CB = q(1 - math.exp(-BLK / (TAU * FS)), 16)
KNEE_W = int(round(10 * DBU)); KNEE_H = KNEE_W // 2; R2W = int(round(65536 / (2 * KNEE_W)))

# ---------------- page ----------------
(P_MAIN, P_LOW, P_HIGH, P_CUT, P_DBX, P_OUT) = range(6)
NAMES = ["MASTER FX", "LOW EQ   ", "HIGH EQ  ", "HIGH CUT ", "DBX 160  ", "DBX OUT  "]   # user: "MASTER FX"
pg = PageAddon("master1", open_press=None)
pg.other_menu("Master")
pg.far_hend = True
pg.far_fire = True
pg.lean_pools = True
PUB = pg.alloc_slot()                    # = scratch + 0x44: address of the INPUT FX page, for Pad FX (2026-11-04)
assert PUB == LOAD_SCRATCH + 0x44
PAGEF = pg.choice("", 0, 0, NAMES, default=P_MAIN)
R0 = pg.dlabel(8, 0, 3)                                         # "THR" on DBX 160 (hidden elsewhere: "MASTER FX" uses x8)
R1 = pg.dlabel(0, 1, 16)                                        # row 1 text (fields over it)
ONF = pg.choice("", 13, 0, ["OFF", " ON"], default=0)          # user 2026-11-04: OFF by default
LFREQF = pg.choice("", 11, 0, [" 20Hz", " 30Hz", " 60Hz", "100Hz"], default=2)
HFREQF = pg.choice("", 10, 0, ["  3kHz", "  4kHz", "  5kHz", "  8kHz", " 10kHz", " 12kHz", " 16kHz"], default=4)
CFREQF = pg.choice("", 11, 0, [" 5kHz", "10kHz", "20kHz"], default=1)
THRF = pg.choice("", 11, 0, ["%3ddB" % t for t in THRS], default=THRS.index(-3))
OUTF = pg.choice("", 11, 0, ["%+3ddB" % o for o in OUTS], default=OUTS.index(0))   # v2: 0 dB (normalized samples: no added level)
NUM = ["%2d" % k for k in range(11)]
LBF = pg.choice("", 6, 1, NUM, default=3)
LAF = pg.choice("", 14, 1, NUM, default=2)
HBF = pg.choice("", 6, 1, NUM, default=3)
HWF = pg.choice("", 14, 1, NUM, default=5)
HAF = pg.choice("", 6, 1, NUM, default=0)
RATF = pg.choice("", 6, 1, RATN, default=4)
KNEEF = pg.choice("", 5, 1, ["HARD", "SOFT"], default=1)
CURSOR = [PAGEF, ONF, LFREQF, HFREQF, CFREQF, THRF, OUTF, LBF, LAF, HBF, HWF, HAF, RATF, KNEEF]
VIS = {ONF: [P_MAIN], LFREQF: [P_LOW], HFREQF: [P_HIGH], CFREQF: [P_CUT], THRF: [P_DBX], OUTF: [P_OUT],
       LBF: [P_LOW], LAF: [P_LOW], HBF: [P_HIGH], HWF: [P_HIGH], HAF: [P_CUT], RATF: [P_DBX], KNEEF: [P_OUT]}
def fid(slot): return [it[1] for it in pg.items if it[0] in ('choice', 'value') and it[-1] == slot][0]
def W(slot): return pg.addr['values'][fid(slot)]

# ---------------- INPUT FX page (2026-11-04): live effects on what the MPC records, see infx.py ----------------
import infx as IX
(X_IN, X_DLY, X_MOD, X_RVB) = range(4)
pg2 = pg.add_page("ix")
XR0 = pg2.dlabel(6, 0, 2)                                       # "FX" after "INPUT" (INPUT screen)
XR1 = pg2.dlabel(0, 1, 16)                                      # row 1 text (fields over it)
XSEL = pg2.choice("", 0, 0, ["INPUT ", "DELAY ", "MOD   ", "REVERB"], default=X_IN)
INON = pg2.choice("", 13, 0, ["OFF", " ON"], default=0)
SRCF = pg2.choice("", 7, 1, ["INPUT", "MAIN "], default=0)
DPF = pg2.choice("", 6, 0, IX.DNAMES, default=0)                # 0 = OFF (user 2026-11-05: both OFF by default)
DMIX = pg2.choice("", 3, 1, IX.PCT5, default=IX.pct_index(IX.DPRESETS[0][3]), share=True)
DFB = pg2.choice("", 8, 1, IX.PCT5, default=IX.pct_index(IX.DPRESETS[0][2]), share=True)
DTM = pg2.choice("", 11, 1, IX.TIMES, default=IX.DPRESETS[0][1])
MPF = pg2.choice("", 6, 0, IX.MNAMES, default=0)                # 0 = OFF, 1.. = chorus / phaser presets
MMIX = pg2.choice("", 3, 1, IX.PCT5, default=IX.pct_index(IX.MPRESETS[0][3]), share=True)
MRATE = pg2.choice("", 11, 1, IX.RATEN, default=IX.MTAB[0][2])
RPF = pg2.choice("", 6, 0, IX.RNAMES, default=0)                # 0 = OFF, 1.. = reverb presets (2026-11-06)
RMIX = pg2.choice("", 3, 1, IX.PCT5, default=IX.RTAB[0][0], share=True)
XP0 = pg2.dlabel(0, 0, 16)                                      # prompt row 0 (last object: drawn over row 0), prompt only
XCURSOR = [XSEL, INON, SRCF, DPF, DMIX, DFB, DTM, MPF, MMIX, MRATE, RPF, RMIX]
XVIS = {INON: [X_IN], SRCF: [X_IN], DPF: [X_DLY], DMIX: [X_DLY], DFB: [X_DLY], DTM: [X_DLY],
        MPF: [X_MOD], MMIX: [X_MOD], MRATE: [X_MOD], RPF: [X_RVB], RMIX: [X_RVB]}
def fid2(slot): return [it[1] for it in pg2.items if it[0] in ('choice', 'value') and it[-1] == slot][0]
def W2(slot): return pg2.addr['values'][fid2(slot)]
XLASTF, XLASTP, IXENT, FPB, FCNT, IXINIT, DCLR, DONL, XLASTM, MLIVE = [pg.alloc_slot() for _ in range(10)]
pg.defaults[XLASTP] = 0; pg.defaults[XLASTM] = 0; pg.defaults[FPB] = 22050   # 120 BPM until the tempo is read
XLASTR, RLIVE = pg.alloc_slot(), pg.alloc_slot()                # reverb: last preset seen on the page / loaded
KCNT = pg.alloc_slot(); pg.defaults[KCNT] = 0                    # fires since an INPUT FX overload (auto retry)
# MASTER FX / INPUT FX never together (user 2026-11-06, instead of a CPU rework): INPUT FX on -> MASTER FX off; MASTER FX
# comes back when INPUT FX goes off only if it was on before (SAVEDM). Entering the INPUT FX page with MASTER FX on ->
# prompt "DISABLE MSTR FX?" (PROMPT): DO IT = MASTER FX off (remembered), CANCEL = back to the Pad FX list.
SAVEDM, LIN, LMON, PROMPT, XWAS, XSCR = [pg.alloc_slot() for _ in range(6)]
X_PROMPT = 7                                                    # screen number of the prompt (bit 0x80: unused by both pages)
KRETRY = 500                                                    # fires (~3 ms each): ~1.5 s
MGR_, SIG2_ = 0x8848ED30, 0x8B2A3D01
SYS_PARAM, ID_TEMPO = 0x884E5CD0, 0x2000C
XMSG = [pg.alloc_slot() for _ in range(4)]                       # message to the system parameter manager

CPOOL = LOAD_SCRATCH + CP_OFF
_cp = [CPOOL]
def cs(n=None):
    if n is None: s_ = _cp[0]; _cp[0] += 4; CSET.add(s_); return s_
    return [cs() for _ in range(n)]
HOOKED, LASTF_, LASTON, OLDGR = [pg.alloc_slot() for _ in range(4)]
ACT, G, ARRP, INP, OUTP, LASTJ, BUSY2, CFFX, CFOK, MISS, GRM = cs(11)
OUTID = cs(4)
TENT, DSUM, NCALL, TWT, TWC, LOADP, KILL, TTK, TXP = cs(9)
OVC = cs()                                    # consecutive overloaded windows (KILL after OVN)
OVN = 3
NCF = 15
CF = cs(NCF); CFR = cs(NCF)
(C_L2, C_L1, C_B, C_A, C_A1, C_A2, C_A3, C_KB, C_CH, C_AH, C_T, C_SL, C_KN, C_OUT, C_ON) = range(NCF)
ST = cs(16)                                   # states: L chain 0..5, R chain 6..11, P 12, Gp 13
IXBP, IXACT = cs(2)                           # INPUT FX: P1 address of its state block (0 = no FX buffer); running
CPOOL_END = (_cp[0] + 15) & ~15
assert pg.next_slot <= LOAD_SCRATCH + ARR_OFF and CPOOL_END <= LOAD_SCRATCH + IN_OFF, (hex(pg.next_slot), hex(CPOOL_END))
pg.defaults[OLDGR] = 0xFFFF

STRS = {}                                     # text -> label (msput strings, emitted once in ix_main's tables)
def sput(m, s):
    """display text: dst r12 (advances), r14 = 1 if a character changed (as the old per-character cput). r0 r1 r2 r4 r5"""
    lbl = STRS.setdefault(s, '__s%d' % len(STRS))
    m.a.movl_pool(lbl, 4); m.a.mov_imm(len(s), 5); m.a.movl_pool('msput', 2); m.a.jsr(2); m.a.nop()
def tword(m, v):
    """a table word that is an address of our scratch follows the build base like m.imm32 (relocation)"""
    if isinstance(v, int) and LOAD_SCRATCH <= v < LOAD_SCRATCH + 0x1000 and m.scratch != LOAD_SCRATCH and m.scratch != mpcmod_SCRATCH:
        v = m.scratch + (v - LOAD_SCRATCH)
    m.a._emit(v & 0xFFFF); m.a._emit((v >> 16) & 0xFFFF)
from mpcmod import SCRATCH as mpcmod_SCRATCH
def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()            # clobbers r2
def jmpl(m, lbl): m.a.movl_pool(lbl, 2); m.a.jmp(2); m.a.nop()
def rdb(m, addr, r): m.imm32(addr, r); m.a.movb_ld(r, r); m.a.extu_b(r, r)
def redraw(m): m.imm32(EVQ + 0x70, 1); m.a.mov_imm(1, 0); m.a.movl_st(0, 1)
def sar(m, r, n, tmp): m.a.mov_imm(-n, tmp); m.a.shad(tmp, r)
def clip(m, r, kmax=14, tmp=1):
    a = m.a
    if kmax == 14:      # r14 = 32767 (s16): 3 instructions when nothing clips (2026-11-06, same size and result)
        ok = m.newlabel('k0')
        a.exts_w(r, tmp); a.cmpeq(r, tmp); a.bt_lbl(ok); a.cmppz(r); a.mov_rm_rn(14, r); a.bt_lbl(ok); a.not_rr(14, r)
        a.deflabel(ok); return
    l1 = m.newlabel('k1'); l2 = m.newlabel('k2')
    a.cmpgt(kmax, r); a.bf_lbl(l1); a.mov_rm_rn(kmax, r); a.deflabel(l1)
    a.not_rr(kmax, tmp); a.cmpgt(r, tmp); a.bf_lbl(l2); a.mov_rm_rn(tmp, r); a.deflabel(l2)
def dmuls(m, rm, rn): m.a._emit(0x300D | (rn << 8) | (rm << 4))
def xtrct(m, rm, rn): m.a._emit(0x200D | (rn << 8) | (rm << 4))
def mul16(m, rc, rs):
    """r2 = (rs * rc) >> 16 (64-bit product). r1"""
    a = m.a; dmuls(m, rc, rs); a.sts_mach(1); a.sts_macl(2); xtrct(m, 1, 2)
def mul24(m, rc, rs):
    """r2 = (rs * rc) >> 24 (64-bit product). r1"""
    a = m.a; dmuls(m, rc, rs); a.sts_mach(1); a.sts_macl(2); a.shll8(1); a.shlr16(2); a.shlr8(2); a.or_rr(1, 2)
LIMK = 20000                                                     # v2 soft limit: above -4.3 dBFS slope 1/4, full scale at +6.7 dB over
def softlim(m, r, tmp):
    a = m.a; l1 = m.newlabel('s1'); l2 = m.newlabel('s2')
    m.imm32(LIMK, tmp); a.cmpgt(tmp, r); a.bf_lbl(l1); a.sub(tmp, r); a.shar(r); a.shar(r); a.add_rr(tmp, r); a.deflabel(l1)
    m.imm32(-LIMK, tmp); a.cmpgt(r, tmp); a.bf_lbl(l2); a.sub(tmp, r); a.shar(r); a.shar(r); a.add_rr(tmp, r); a.deflabel(l2)
    clip(m, r, 14, tmp)
def jt_abs(m, lbl):
    a = m.a; sk = m.newlabel('ja'); a.bf_lbl(sk); a.movl_pool('P1:' + lbl, 2); a.jmp(2); a.nop(); a.deflabel(sk)
def jf_abs(m, lbl):
    a = m.a; sk = m.newlabel('jb'); a.bt_lbl(sk); a.movl_pool('P1:' + lbl, 2); a.jmp(2); a.nop(); a.deflabel(sk)

def cp_init(m):
    """once, scratch just cleared: a hook left by a previous install is removed BEFORE its variables are cleared; the
    P1 lines of the ISR variables are invalidated and cleared via P1 (Master FX)"""
    a = m.a
    # INPUT FX of a previous install: the sampler's 5 input-pointer literals back to the OS pointer (only we change them)
    a.movl_pool('ix_lits', 3); a.mov_imm(len(IX.SMP_LITS), 4)
    ll, nl = m.newlabel('ll'), m.newlabel('nl'); a.deflabel(ll)
    a.movl_ld_inc(3, 1); a.movl_ld(1, 0); m.imm32(0x88800000, 2); a.cmphs(2, 0); a.bf_lbl(nl)        # ours: in high RAM
    m.imm32(0x98000000, 2); a.cmphs(2, 0); a.bt_lbl(nl); m.imm32(IX.IN_WR, 0); a.movl_st(0, 1)
    a.deflabel(nl); a.dt(4); a.bf_lbl(ll)
    m.pool_jump()
    m.imm32(TXPATCH, 1); a.movw_ld(1, 0); a.extu_w(0, 0); m.imm32(0xD002, 2); a.cmpeq(2, 0); nsh = m.newlabel('nsh'); a.bf_lbl(nsh)
    a.add_imm(2, 1); a.movw_ld(1, 0); a.extu_w(0, 0); m.imm32(0x400B, 2); a.cmpeq(2, 0); a.bf_lbl(nsh)
    a.movl_pool('ms_txoff', 2); a.jsr(2); a.nop()
    a.deflabel(nsh)
    m.imm32(_p1(m, CPOOL), 4); m.imm32((CPOOL_END - CPOOL) // 16, 5); m.imm32(0xF0000008, 6); m.imm32(0x1FFFFC00, 7); m.imm32(0xFF0, 3)
    l = m.newlabel('cpi'); a.deflabel(l)
    a.mov_rm_rn(4, 0); a.and_rr(3, 0); a.or_rr(6, 0); a.mov_rm_rn(4, 1); a.and_rr(7, 1); a.movl_st(1, 0)
    a.add_imm(16, 4); a.dt(5); a.bf_lbl(l)
    m.imm32(_p1(m, CPOOL), 1); m.imm32((CPOOL_END - CPOOL) // 4, 2); a.mov_imm(0, 0)
    l = m.newlabel('cpz'); a.deflabel(l); a.movl_st(0, 1); a.add_imm(4, 1); a.dt(2); a.bf_lbl(l)
pg.init_fn = cp_init

# ================= main loop =================
def fire(m):
    a = m.a
    call(m, 'ms_maint')
    call(m, 'ms_vis')
    call(m, 'ms_disp')
pg.on_fire(fire)
def inactive(m):
    call(m, 'ms_maint')
pg.on_inactive(inactive)

def subs(m):
    a = m.a
    # ---- ms_maint (every fire, any page): pointers, coefficients, ON, hook / unhook ----
    a.deflabel('ms_maint'); a.stsl_pr()
    call(m, 'ix_arb')                                                  # MASTER FX / INPUT FX: one at a time
    for slot, off in ((ARRP, ARR_OFF), (INP, IN_OFF), (OUTP, OUT_OFF)):
        m.imm32(LOAD_SCRATCH + off, 0); m.imm32(0xDFFFFFFF, 1); a.and_rr(1, 0); st(m, 0, slot)
    sti(m, -1, CFFX)                                                   # the ISR does not copy half-written coefficients
    a.movl_pool('ms_tabs', 13)                                         # r13 = tables
    def tab(field, tname, k, mult=1, plus=None):
        """CF[k] = table[tname][field (* mult + plus field)]"""
        rdb(m, field, 4)
        if mult != 1: m.imm32(mult, 0); a.mul_l(0, 4); a.sts_macl(4)
        if plus is not None: rdb(m, plus[0], 0)
        if plus is not None:
            if plus[1] != 1: m.imm32(plus[1], 3); a.mul_l(3, 0); a.sts_macl(0)
            a.add_rr(0, 4)
        a.shll2(4); m.imm32(TOFF[tname], 0); a.add_rr(0, 4); a.add_rr(13, 4); a.movl_ld(4, 0); st(m, 0, CF[k])
    tab(LFREQF, 'LP2', C_L2); tab(LFREQF, 'LP1', C_L1); tab(LBF, 'B', C_B); tab(LAF, 'A', C_A)
    for j, k in enumerate((C_A1, C_A2, C_A3)):
        rdb(m, HFREQF, 4); a.mov_imm(11, 0); a.mul_l(0, 4); a.sts_macl(4); rdb(m, HWF, 0); a.add_rr(0, 4)   # (f*11 + w)
        a.mov_rm_rn(4, 0); a.shll(4); a.add_rr(0, 4); a.shll2(4)                                  # * 12
        m.imm32(TOFF['SVF'] + 4 * j, 0); a.add_rr(0, 4); a.add_rr(13, 4); a.movl_ld(4, 0); st(m, 0, CF[k])
    m.pool_jump()                                                      # KB[(f*11 + w)*11 + boost]:
    rdb(m, HFREQF, 4); a.mov_imm(11, 0); a.mul_l(0, 4); a.sts_macl(4); rdb(m, HWF, 0); a.add_rr(0, 4); a.shll2(4)
    m.imm32(TOFF['KQ'], 0); a.add_rr(0, 4); a.add_rr(13, 4); a.movl_ld(4, 5)                          # r5 = 1/Q
    rdb(m, HBF, 4); a.shll2(4); m.imm32(TOFF['BH'], 0); a.add_rr(0, 4); a.add_rr(13, 4); a.movl_ld(4, 3)   # r3 = boost
    mul16(m, 3, 5); st(m, 2, CF[C_KB])
    tab(CFREQF, 'CH', C_CH); tab(HAF, 'AH', C_AH); tab(THRF, 'THR', C_T); tab(RATF, 'SLOPE', C_SL); tab(OUTF, 'OUT', C_OUT)
    rdb(m, KNEEF, 0); st(m, 0, CF[C_KN])
    rdb(m, ONF, 0); st(m, 0, CF[C_ON])
    sti(m, 0, CFFX)
    m.pool_jump()
    # ON: an overload (KILL) holds until it is switched off and on again
    rdb(m, ONF, 9); ld(m, LASTON, 0); a.cmpeq(9, 0); so = m.newlabel('so'); m.bt_far(so)
    st(m, 9, LASTON); a.tst_rr(9, 9); m.bt_far(so); sti(m, 0, KILL); sti(m, 0, OVC); sti(m, 0, NCALL); sti(m, -1, LOADP)
    a.deflabel(so)
    ld(m, KILL, 0); a.tst_rr(0, 0); a.mov_rm_rn(9, 0); kz = m.newlabel('kz'); a.bt_lbl(kz); a.mov_imm(0, 0); a.deflabel(kz)
    st(m, 0, ACT)
    rdb(m, INON, 0); a.or_rr(0, 9)                                     # the hook is also wanted for INPUT FX
    # hook when on (only on the expected OS); unhook when off and faded
    hk, hx = m.newlabel('hk'), m.newlabel('hx')
    ld(m, HOOKED, 0); a.tst_rr(0, 0); m.bf_far(hk)
    a.tst_rr(9, 9); m.bt_far(hx)
    call(m, 'ms_txchk'); a.tst_rr(0, 0); m.bt_far(hx)
    call(m, 'ms_icinv')
    sti(m, 0, G)
    for k in range(4): sti(m, -1, OUTID[k])
    ld(m, INP, 1); m.imm32(0x800 // 4, 2); a.mov_imm(0, 3)
    zi = m.newlabel('zi'); a.deflabel(zi); a.movl_st(3, 1); a.add_imm(4, 1); a.dt(2); a.bf_lbl(zi)
    m.a.movl_pool(_p1(m, ST[0]), 1); a.mov_imm(16, 2); a.mov_imm(0, 3)
    zs = m.newlabel('zs'); a.deflabel(zs); a.movl_st(3, 1); a.add_imm(4, 1); a.dt(2); a.bf_lbl(zs)
    sti(m, -1, LASTJ); sti(m, 0, BUSY2); sti(m, 0, NCALL); sti(m, 0, CFOK)
    call(m, 'ms_txon')
    sti(m, 1, HOOKED); jmpl(m, hx)
    m.pool_jump()
    a.deflabel(hk)
    a.tst_rr(9, 9); m.bf_far(hx)
    ld(m, G, 0); a.tst_rr(0, 0); m.bf_far(hx)
    ld(m, BUSY2, 0); a.tst_rr(0, 0); m.bf_far(hx)
    ld(m, IXACT, 0); a.tst_rr(0, 0); m.bf_far(hx)                     # INPUT FX still running (stops at the next fire)
    call(m, 'ms_txoff'); sti(m, 0, HOOKED)
    a.deflabel(hx)
    call(m, 'ix_maint')
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- ms_vis: the fields of the page; the cursor skips hidden fields (table vt_master, routine vis_tab) ----
    a.deflabel('ms_vis'); a.movl_pool('vt_master', 4); jmpl(m, 'vis_tab')
    m.pool_jump()

    # ---- ms_disp: row texts; the meter "GR -3dB ###" (OVERLOAD after the load guard) ----
    a.deflabel('ms_disp'); a.stsl_pr()
    a.mov_imm(0, 14)
    def cput(s): sput(m, s)
    def cputr(r):
        n = m.newlabel('cr'); a.movb_ld(12, 1); a.cmpeq(r, 1); a.bt_lbl(n); a.mov_rm_rn(r, 0); a.movb_st(0, 12)
        a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
    rdb(m, PAGEF, 6)
    m.imm32(pg.addr['dlabels'][R0], 12)
    d0 = m.newlabel('d0'); a.mov_imm(P_DBX, 0); a.cmpeq(0, 6); r0t = m.newlabel('r0t'); m.bt_far(r0t)
    cput("   "); jmpl(m, d0)
    a.deflabel(r0t); cput("THR")
    a.deflabel(d0)
    m.imm32(pg.addr['dlabels'][R1], 12)
    lbls = {P_LOW: "BOOST    ATTEN  ", P_HIGH: "BOOST    WIDTH  ", P_CUT: "ATTEN           ", P_DBX: "RATIO           ",
            P_OUT: "KNEE            "}
    dd = m.newlabel('dd'); mt = m.newlabel('mt')
    for p, s in lbls.items():
        n = m.newlabel('pl'); a.mov_imm(p, 0); a.cmpeq(0, 6); m.bf_far(n); cput(s); jmpl(m, dd); a.deflabel(n); m.pool_jump()
    # MASTER: meter
    rdb(m, ONF, 0); a.tst_rr(0, 0); onl = m.newlabel('onl'); m.bf_far(onl); cput("OFF             "); jmpl(m, dd)
    a.deflabel(onl)
    ld(m, KILL, 0); a.tst_rr(0, 0); nkl = m.newlabel('nkl'); m.bt_far(nkl); cput("OVERLOAD - OFF? "); jmpl(m, dd)
    a.deflabel(nkl)
    m.pool_jump()
    ld(m, GRM, 4); m.imm32(int(round(65536 / DBU)), 5); a.mul_l(5, 4); a.sts_macl(4); a.shlr16(4)   # dB (rounded down)
    a.mov_imm(99, 0); g9 = m.newlabel('g9'); a.cmpgt(0, 4); a.bf_lbl(g9); a.mov_rm_rn(0, 4); a.deflabel(g9)
    a.mov_rm_rn(4, 9)
    cput("GR"); a.mov_rm_rn(9, 4); a.mov_imm(10, 5); pg._hcall(m, '__udiv'); a.mov_rm_rn(1, 11); a.mov_rm_rn(0, 10)
    a.mov_imm(ord('-'), 3); a.tst_rr(9, 9); z0 = m.newlabel('z0'); a.bf_lbl(z0); a.mov_imm(ord(' '), 3); a.deflabel(z0)
    tz = m.newlabel('tz'); a.tst_rr(10, 10); a.bf_lbl(tz)
    a.mov_imm(ord(' '), 0); a.mov_rm_rn(0, 10); cputr(10); cputr(3); a.bra_lbl('__msu'); a.nop()
    a.deflabel(tz); cputr(3); a.add_imm(0x30, 10); cputr(10)
    a.deflabel('__msu'); a.add_imm(0x30, 11); cputr(11); cput("dB ")
    # bars: one per 2 dB, 7 at most
    a.mov_rm_rn(9, 10); a.shlr(10); a.mov_imm(7, 0); b7 = m.newlabel('b7'); a.cmpgt(0, 10); a.bf_lbl(b7); a.mov_rm_rn(0, 10); a.deflabel(b7)
    for i in range(7):
        n = m.newlabel('bb'); bx_ = m.newlabel('bx'); a.mov_imm(i, 0); a.cmpgt(0, 10); m.imm32(0xFF, 3); a.bt_lbl(n); a.mov_imm(ord(' '), 3)
        a.deflabel(n); cputr(3)
    cput(" ")
    a.deflabel(dd)
    a.tst_rr(14, 14); px = m.newlabel('px'); m.bt_far(px); redraw(m)
    a.deflabel(px); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    ix_main(m)
    isr_code(m)

def ix_main(m):
    a = m.a
    C = IX
    # ---- ix_arb: every fire, any page (start of ms_maint): MASTER FX and INPUT FX never on together. r0 r1 r4 r5 ----
    #   MASTER FX switched on while INPUT FX runs -> INPUT FX off (the player chose MASTER FX; nothing to restore)
    #   INPUT FX on while MASTER FX on -> MASTER FX off, remembered (SAVEDM)
    #   INPUT FX off (or never switched on and the page left) with SAVEDM -> MASTER FX on again
    a.deflabel('ix_arb'); a.stsl_pr()
    rdb(m, ONF, 4); rdb(m, INON, 5)
    r2_, r3_, rx_ = m.newlabel('ar2'), m.newlabel('ar3'), m.newlabel('arx')
    ld(m, LMON, 0); a.tst_rr(0, 0); m.bf_far(r2_)
    a.tst_rr(4, 4); m.bt_far(r2_)
    a.tst_rr(5, 5); m.bt_far(r2_)
    a.mov_imm(0, 5); m.imm32(INON, 1); a.movb_st(5, 1); sti(m, 0, SAVEDM); jmpl(m, rx_)
    a.deflabel(r2_)
    a.tst_rr(5, 5); m.bt_far(r3_)
    a.tst_rr(4, 4); m.bt_far(rx_)
    a.mov_imm(0, 4); m.imm32(ONF, 1); a.movb_st(4, 1); sti(m, 1, SAVEDM); jmpl(m, rx_)
    a.deflabel(r3_)                                                    # INPUT FX off
    ld(m, SAVEDM, 0); a.tst_rr(0, 0); m.bt_far(rx_)
    rs_ = m.newlabel('ars')
    ld(m, LIN, 0); a.tst_rr(0, 0); m.bf_far(rs_)                       # it just went off
    m.imm32(EVQ + 0x68, 1); a.movl_ld(1, 0); m.imm32(pg2.addr['page'], 1); a.cmpeq(1, 0); m.bt_far(rx_)   # still on the page
    a.deflabel(rs_)
    a.mov_imm(1, 4); m.imm32(ONF, 1); a.movb_st(4, 1); sti(m, 0, SAVEDM)
    a.deflabel(rx_)
    st(m, 4, LMON); st(m, 5, LIN)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- ix_pfx: INPUT FX page fire, first: entering with MASTER FX on -> the prompt; XSCR = the screen to show ----
    a.deflabel('ix_pfx'); a.stsl_pr()
    hv = m.newlabel('hv'); np_ = m.newlabel('np'); sx = m.newlabel('sx')
    ld(m, XWAS, 0); a.tst_rr(0, 0); m.bf_far(hv)
    sti(m, 1, XWAS)
    rdb(m, ONF, 0); a.tst_rr(0, 0); m.bt_far(hv)
    sti(m, 1, PROMPT)
    a.deflabel(hv)
    ld(m, PROMPT, 0); a.tst_rr(0, 0); m.bt_far(np_)
    a.mov_imm(0, 0); m.imm32(INON, 1); a.movb_st(0, 1)                 # (the wheel on a hidden field: INPUT FX stays off)
    a.mov_imm(X_PROMPT, 5); a.bra_lbl(sx); a.nop()
    a.deflabel(np_); rdb(m, XSEL, 5)
    a.deflabel(sx); st(m, 5, XSCR)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- ix_maint: every fire, any page (from ms_maint, after the hook logic). r0-r14 ----
    a.deflabel('ix_maint'); a.stsl_pr()
    m.imm32(pg2.addr['page'], 0); st(m, 0, PUB)                        # for Pad FX
    # Record page "Monitor" always OFF while MASTER is loaded (user 2026-11-05: with it on, the OS mixes the DRY input
    # into the output in software, on top of INPUT FX's effected monitor -> doubled, metallic). SMP+0x14 = Monitor
    # (setter 0x884EC14E); 0x884EC586(SMP) applies it: Monitor 0 -> the 4 input->output gains of the render = 0.
    mo = m.newlabel('mo')
    m.imm32(IX.SMP + IX.SMP_MON, 1); a.movl_ld(1, 0); a.tst_rr(0, 0); m.bt_far(mo)
    a.mov_imm(0, 0); a.movl_st(0, 1); m.imm32(IX.SMP, 4); m.imm32(IX.SMP_MONAPPLY, 2); a.jsr(2); a.nop()
    a.deflabel(mo)
    # the FX buffer of loader v10 (B = MGR[1]: signatures + marker), as Master FX
    a.mov_imm(0, 13)
    nb = m.newlabel('nb')
    m.imm32(MGR_, 1); a.movl_ld_disp(1, 4, 8)
    a.mov_rm_rn(8, 1); a.add_imm(0x40, 1); a.movl_ld(1, 0); m.imm32(SCRATCH_MAGIC, 2); a.cmpeq(2, 0); m.bf_far(nb)
    a.mov_rm_rn(8, 1); a.add_imm(0x44, 1); a.movl_ld(1, 0); m.imm32(SIG2_, 2); a.cmpeq(2, 0); m.bf_far(nb)
    a.mov_rm_rn(8, 1); a.add_imm(FXMARK_OFF, 1); a.movl_ld(1, 0); m.imm32(RESERVE, 2); a.cmpeq(2, 0); m.bf_far(nb)
    m.imm32(FXBUF_OFF + C.IX_OFF, 13); a.add_rr(8, 13); m.imm32(0xDFFFFFFF, 1); a.and_rr(1, 13)
    a.deflabel(nb)
    st(m, 13, IXBP)
    m.pool_jump()
    # first fire after install: what a previous install left in the FX buffer is not running (cp_init unhooked it)
    di = m.newlabel('di'); ld(m, IXINIT, 0); a.tst_rr(0, 0); m.bf_far(di)
    a.tst_rr(13, 13); dz = m.newlabel('dz'); a.bt_lbl(dz); a.mov_imm(0, 0); a.movl_st_disp(0, C.C_RUN, 13); a.movl_st_disp(0, C.C_DON, 13)
    a.mov_rm_rn(13, 1); a.add_imm(0x40, 1); a.add_imm(0x40, 1); a.movl_st_disp(0, IX.M_ON, 1)
    m.imm32(IX.RB_OFF - 0x80, 2); a.add_rr(2, 1); a.movl_st_disp(0, IX.R_ON, 1)
    a.deflabel(dz); sti(m, 1, IXINIT); sti(m, 0, IXACT); sti(m, 0, DONL); sti(m, 0, MLIVE); sti(m, 0, RLIVE)
    a.deflabel(di)
    # tempo every 32 fires -> FPB = frames per beat = 26460000 / (BPM x 10)
    nt = m.newlabel('nt')
    ld(m, FCNT, 0); a.add_imm(1, 0); st(m, 0, FCNT); a.and_imm(31); a.tst_rr(0, 0); m.bf_far(nt)
    sti(m, 0, XMSG[1]); sti(m, 0x10, XMSG[0]); sti(m, ID_TEMPO, XMSG[3]); sti(m, 0, XMSG[2])
    m.imm32(XMSG[0], 4); m.imm32(SYS_PARAM, 2); a.jsr(2); a.nop()
    ld(m, XMSG[1], 5); m.imm32(300, 3); a.cmpge(3, 5); m.bf_far(nt)
    m.imm32(3000, 3); a.cmpgt(3, 5); m.bt_far(nt)
    m.imm32(26460000, 4); pg._hcall(m, '__udiv'); st(m, 0, FPB)
    a.deflabel(nt)
    m.pool_jump()
    # a new delay preset: its TIME, FB and MIX (table ix_ptab: byte 0 time, 1 fb, 2 mix)
    sp = m.newlabel('sp')
    rdb(m, DPF, 4); ld(m, XLASTP, 0); a.cmpeq(4, 0); m.bt_far(sp)
    st(m, 4, XLASTP); a.tst_rr(4, 4); m.bt_far(sp)
    # a modulation with the "no delay" flag (none at the moment) is switched OFF by a delay preset
    nx = m.newlabel('nx')
    rdb(m, MPF, 0); a.tst_rr(0, 0); m.bt_far(nx)
    a.add_imm(-1, 0); a.shll2(0); a.shll2(0); a.shll2(0); a.movl_pool('ix_mtab', 1); a.add_rr(0, 1)
    a.movl_ld_disp(1, 60, 0); a.tst_rr(0, 0); m.bt_far(nx)
    a.mov_imm(0, 0); m.imm32(MPF, 2); a.movb_st(0, 2); st(m, 0, XLASTM)
    a.deflabel(nx)
    a.mov_imm(0, 0); m.imm32(RPF, 2); a.movb_st(0, 2); st(m, 0, XLASTR)                      # and the reverb OFF
    a.mov_rm_rn(4, 0); a.add_imm(-1, 0); a.shll2(0); a.movl_pool('ix_ptab', 1); a.add_rr(0, 1); a.movl_ld(1, 0)
    m.imm32(DTM, 1); a.movb_st(0, 1); a.shlr8(0); m.imm32(DFB, 1); a.movb_st(0, 1); a.shlr8(0); m.imm32(DMIX, 1); a.movb_st(0, 1)
    redraw(m)
    a.deflabel(sp)
    m.pool_jump()
    # coefficients (r13 = state block)
    ixx = m.newlabel('ixx')
    a.tst_rr(13, 13); m.bt_far(ixx)
    rdb(m, SRCF, 0); a.movl_st_disp(0, C.C_SRC, 13)
    # overload with INPUT FX alone (MASTER FX off): retry by itself after KRETRY fires - delay and modulation restart
    # clean (DON / MLIVE cleared: the sections below clear the line and reload the preset). MASTER FX keeps its latch.
    kr, kz_ = m.newlabel('kr'), m.newlabel('kz')
    ld(m, KILL, 0); a.tst_rr(0, 0); m.bt_far(kz_)
    ld(m, ACT, 0); a.tst_rr(0, 0); m.bf_far(kz_)
    ld(m, KCNT, 0); a.add_imm(1, 0); st(m, 0, KCNT); m.imm32(KRETRY, 1); a.cmpge(1, 0); m.bf_far(kr)
    a.mov_imm(0, 0); a.movl_st_disp(0, C.C_DON, 13); a.mov_rm_rn(13, 1); a.add_imm(0x40, 1); a.add_imm(0x40, 1)
    a.movl_st_disp(0, IX.M_ON, 1); sti(m, 0, DONL); sti(m, 0, MLIVE); sti(m, 0, RLIVE); sti(m, 0, OVC); sti(m, 0, KILL)
    a.deflabel(kz_); sti(m, 0, KCNT)
    a.deflabel(kr)
    ld(m, KILL, 0); a.movl_st_disp(0, C.C_KILL, 13)
    rdb(m, DMIX, 0); a.shll2(0); a.movl_pool('ix_pq', 1); a.add_rr(0, 1); a.movl_ld(1, 0); a.movl_st_disp(0, C.C_MIX, 13)
    rdb(m, DFB, 0); a.shll2(0); a.movl_pool('ix_pq', 1); a.add_rr(0, 1); a.movl_ld(1, 0); a.movl_st_disp(0, C.C_FB, 13)
    # delay time: note values from the tempo, else milliseconds; 1 .. DLY_N - 2 frames
    rdb(m, DTM, 4); tms, tok = m.newlabel('tms'), m.newlabel('tok')
    a.mov_imm(C.NNOTE, 0); a.cmpge(0, 4); m.bt_far(tms)
    a.mov_rm_rn(4, 0); a.shll2(0); a.movl_pool('ix_tmul', 1); a.add_rr(0, 1); a.movl_ld(1, 0)
    ld(m, FPB, 1); a.mul_l(1, 0); a.sts_macl(0); a.shlr8(0); a.shlr2(0); a.shlr2(0); a.bra_lbl(tok); a.nop()
    a.deflabel(tms)
    a.mov_rm_rn(4, 0); a.add_imm(-C.NNOTE, 0); a.shll2(0); a.movl_pool('ix_tms', 1); a.add_rr(0, 1); a.movl_ld(1, 0)
    a.deflabel(tok)
    t1, t2 = m.newlabel('t1'), m.newlabel('t2')
    a.mov_imm(IX.NMAX, 1); a.cmpge(1, 0); a.bt_lbl(t1); a.mov_rm_rn(1, 0); a.deflabel(t1)
    m.imm32(C.DLY_N - 2, 1); a.cmpgt(1, 0); a.bf_lbl(t2); a.mov_rm_rn(1, 0); a.deflabel(t2)
    a.movl_st_disp(0, C.C_DT, 13)
    m.pool_jump()
    # preset row: CLP, CHP, QM, PING, MODD, MODINC (table ix_dtab, 6 words per preset)
    rdb(m, DPF, 4); nop_ = m.newlabel('nop'); a.tst_rr(4, 4); m.bt_far(nop_)
    a.mov_rm_rn(4, 0); a.add_imm(-1, 0); a.mov_imm(24, 1); a.mul_l(1, 0); a.sts_macl(0); a.movl_pool('ix_dtab', 1); a.add_rr(0, 1)
    for k, off in enumerate((C.C_CLP, C.C_CHP, C.C_QM, C.C_PING, C.C_MODD, C.C_MODINC)):
        a.movl_ld_disp(1, 4 * k, 0); a.movl_st_disp(0, off, 13)
    a.deflabel(nop_)
    m.pool_jump()
    # delay on: the line and its filters cleared first (the ISR does not touch them while C_DON = 0)
    rdb(m, DPF, 4); a.tst_rr(4, 4); a.movt(4); a.mov_imm(1, 0); a.xor_rr(0, 4)               # r4 = delay wanted
    dn1, dn2 = m.newlabel('dn1'), m.newlabel('dn2')
    a.tst_rr(4, 4); m.bf_far(dn1)
    a.movl_st_disp(4, C.C_DON, 13); st(m, 4, DONL); jmpl(m, dn2)
    a.deflabel(dn1)
    ld(m, DONL, 0); a.tst_rr(0, 0); m.bf_far(dn2)
    m.imm32(C.DLY_OFF, 1); a.add_rr(13, 1); m.imm32(C.DLY_N, 2); a.mov_imm(0, 0)
    cl = m.newlabel('cl'); a.deflabel(cl); a.movl_st(0, 1); a.add_imm(4, 1); a.dt(2); a.bf_lbl(cl)
    a.mov_rm_rn(13, 1); a.add_imm(0x40 + C.S_FLL, 1)
    for k in range(5): a.movl_st_disp(0, 4 * k, 1)                                            # FLL FLR FHL FHR PH
    a.mov_imm(1, 0); a.movl_st_disp(0, C.C_DON, 13); st(m, 0, DONL)
    a.deflabel(dn2)
    m.pool_jump()
    # modulation: a new preset -> its default MIX and RATE on the page (once, then the player's)
    sm = m.newlabel('sm')
    rdb(m, MPF, 4); ld(m, XLASTM, 0); a.cmpeq(4, 0); m.bt_far(sm)
    st(m, 4, XLASTM); a.tst_rr(4, 4); m.bt_far(sm)
    a.mov_rm_rn(4, 0); a.add_imm(-1, 0); a.shll2(0); a.shll2(0); a.shll2(0); a.movl_pool('ix_mtab', 1); a.add_rr(0, 1)
    a.movl_ld_disp(1, 4, 0); m.imm32(MMIX, 2); a.movb_st(0, 2)
    a.movl_ld_disp(1, 8, 0); m.imm32(MRATE, 2); a.movb_st(0, 2)
    nd_ = m.newlabel('nd')                                                                    # no delay with it
    a.movl_ld_disp(1, 60, 0); a.tst_rr(0, 0); m.bt_far(nd_)
    a.mov_imm(0, 0); m.imm32(DPF, 2); a.movb_st(0, 2); st(m, 0, XLASTP)
    a.deflabel(nd_)
    a.mov_imm(0, 0); m.imm32(RPF, 2); a.movb_st(0, 2); st(m, 0, XLASTR)                      # and the reverb OFF
    redraw(m)
    a.deflabel(sm)
    m.pool_jump()
    # the preset into the ISR's block (r12 = IX + 0x80) when it differs from the loaded one: M_ON = 0 first (the ISR
    # leaves line and states alone), line + states cleared, voicing words, GEN + 1, then M_ON = its type
    a.mov_rm_rn(13, 12); a.add_imm(0x40, 12); a.add_imm(0x40, 12)
    mlv = m.newlabel('mlv')
    rdb(m, MPF, 4); ld(m, MLIVE, 0); a.cmpeq(4, 0); m.bt_far(mlv)
    a.mov_imm(0, 0); a.movl_st_disp(0, IX.M_ON, 12)
    m.imm32(IX.MLINE_OFF, 1); a.add_rr(13, 1); m.imm32(IX.MLN // 2, 2)
    mc1 = m.newlabel('mc1'); a.deflabel(mc1); a.movl_st(0, 1); a.add_imm(4, 1); a.dt(2); a.bf_lbl(mc1)
    m.imm32(IX.MS_OFF, 1); a.add_rr(13, 1); a.mov_imm(32, 2)
    mc2 = m.newlabel('mc2'); a.deflabel(mc2); a.movl_st(0, 1); a.add_imm(4, 1); a.dt(2); a.bf_lbl(mc2)
    st(m, 4, MLIVE); a.tst_rr(4, 4); m.bt_far(mlv)
    a.mov_rm_rn(4, 0); a.add_imm(-1, 0); a.shll2(0); a.shll2(0); a.shll2(0); a.movl_pool('ix_mtab', 1); a.add_rr(0, 1)
    for k in range(3, 15): a.movl_ld_disp(1, 4 * k, 0); a.movl_st_disp(0, 4 * k, 12)
    a.movl_ld_disp(12, IX.M_GEN, 0); a.add_imm(1, 0); a.movl_st_disp(0, IX.M_GEN, 12)
    a.movl_ld(1, 0); a.movl_st_disp(0, IX.M_ON, 12)
    a.deflabel(mlv)
    m.pool_jump()
    # every fire: lfo increment (RATE) and MIX
    rdb(m, MRATE, 0); a.shll2(0); a.movl_pool('ix_mrate', 1); a.add_rr(0, 1); a.movl_ld(1, 0); a.movl_st_disp(0, IX.M_INC, 12)
    rdb(m, MMIX, 0); a.shll2(0); a.movl_pool('ix_pq', 1); a.add_rr(0, 1); a.movl_ld(1, 0); a.movl_st_disp(0, IX.M_MIX, 12)
    m.pool_jump()
    # reverb (2026-11-06, one effect at a time): a new preset on the page -> its MIX, the delay and the modulation OFF
    def rrow_r1():                                                                            # r1 = row of preset r4
        a.mov_rm_rn(4, 0); a.add_imm(-1, 0); a.mov_imm(56, 1); a.mul_l(1, 0); a.sts_macl(0)
        a.movl_pool('ix_rtab', 1); a.add_rr(0, 1)
    sr = m.newlabel('sr')
    rdb(m, RPF, 4); ld(m, XLASTR, 0); a.cmpeq(4, 0); m.bt_far(sr)
    st(m, 4, XLASTR); a.tst_rr(4, 4); m.bt_far(sr)
    a.mov_imm(0, 0); m.imm32(DPF, 1); a.movb_st(0, 1); st(m, 0, XLASTP)
    m.imm32(MPF, 1); a.movb_st(0, 1); st(m, 0, XLASTM)
    rrow_r1(); a.movl_ld(1, 0); m.imm32(RMIX, 1); a.movb_st(0, 1); redraw(m)
    a.deflabel(sr)
    m.pool_jump()
    # the preset into RB (r12) when it differs from the loaded one: R_ON 0, lines + states cleared, words, GEN + 1
    m.imm32(IX.RB_OFF, 12); a.add_rr(13, 12)
    rlv = m.newlabel('rlv')
    rdb(m, RPF, 4); ld(m, RLIVE, 0); a.cmpeq(4, 0); m.bt_far(rlv)
    a.mov_imm(0, 0); a.movl_st_disp(0, IX.R_ON, 12)
    m.imm32(IX.REV_OFF, 1); a.add_rr(13, 1); m.imm32(IX.NRL * IX.RLN // 2, 2)
    rc1 = m.newlabel('rc1'); a.deflabel(rc1); a.movl_st(0, 1); a.add_imm(4, 1); a.dt(2); a.bf_lbl(rc1)
    a.mov_rm_rn(12, 1); a.add_imm(0x40, 1); a.mov_imm(16, 2)
    rc2 = m.newlabel('rc2'); a.deflabel(rc2); a.movl_st(0, 1); a.add_imm(4, 1); a.dt(2); a.bf_lbl(rc2)
    st(m, 4, RLIVE); a.tst_rr(4, 4); m.bt_far(rlv)
    rrow_r1(); a.add_imm(4, 1); a.mov_rm_rn(12, 2); a.add_imm(4, 2); a.mov_imm(12, 3)
    rc3 = m.newlabel('rc3'); a.deflabel(rc3); a.movl_ld_inc(1, 0); a.movl_st(0, 2); a.add_imm(4, 2); a.dt(3); a.bf_lbl(rc3)
    a.movl_ld(1, 0); a.movl_st_disp(0, IX.R_OGB, 12)
    a.movl_ld_disp(12, IX.R_GEN, 0); a.add_imm(1, 0); a.movl_st_disp(0, IX.R_GEN, 12)
    a.deflabel(rlv)
    m.pool_jump()
    # every fire: output gain x MIX; ON while a preset is loaded
    rdb(m, RMIX, 0); a.shll2(0); a.movl_pool('ix_pq', 1); a.add_rr(0, 1); a.movl_ld(1, 0)
    a.movl_ld_disp(12, IX.R_OGB, 1); a.mul_l(1, 0); a.sts_macl(0); a.shlr8(0); a.shlr2(0); a.shlr2(0)
    a.movl_st_disp(0, IX.R_OGM, 12)
    ld(m, RLIVE, 0); a.tst_rr(0, 0); a.movt(0); a.mov_imm(1, 1); a.xor_rr(1, 0); a.movl_st_disp(0, IX.R_ON, 12)
    m.pool_jump()
    # run / stop: INON, FX buffer and MASTER's audio hook
    want, run_, stop_, done_ = m.newlabel('wt'), m.newlabel('ru'), m.newlabel('stp'), m.newlabel('dn')
    rdb(m, INON, 4); ld(m, HOOKED, 0); a.and_rr(0, 4)                                          # r4 = wanted
    a.movl_ld_disp(13, C.C_RUN, 5)
    a.tst_rr(4, 4); m.bt_far(stop_)
    a.tst_rr(5, 5); m.bf_far(done_)
    ld(m, G, 0); a.tst_rr(0, 0); m.bf_far(done_)                       # MASTER FX still fading out: start after it
    # start: only if the 5 sampler literals are the OS's (OS 1.31)
    a.movl_pool('ix_lits', 3); a.mov_imm(len(C.SMP_LITS), 4); m.imm32(C.IN_WR, 2)
    lc = m.newlabel('lc'); a.deflabel(lc); a.movl_ld_inc(3, 1); a.movl_ld(1, 0); a.cmpeq(2, 0); m.bf_far(done_)
    a.dt(4); a.bf_lbl(lc)
    m.pool_jump()
    a.mov_rm_rn(13, 1); a.add_imm(0x40, 1); a.mov_imm(0, 0)
    for k in range(13): a.movl_st_disp(0, 4 * k, 1)                                            # states + queues
    m.imm32(C.IN_WR, 1); a.movl_ld(1, 0); a.movl_st_disp(0, C.C_SPOS, 13); a.movl_st_disp(0, C.C_SPTR, 13)
    sti(m, 0, KILL); sti(m, 0, OVC); sti(m, 0, MISS); a.mov_imm(1, 0); a.movl_st_disp(0, C.C_RUN, 13); sti(m, 1, IXACT)
    a.mov_rm_rn(13, 0); a.add_imm(C.C_SPTR, 0)
    a.movl_pool('ix_lits', 3); a.mov_imm(len(C.SMP_LITS), 4)
    lp_ = m.newlabel('lp'); a.deflabel(lp_); a.movl_ld_inc(3, 1); a.movl_st(0, 1); a.dt(4); a.bf_lbl(lp_)   # -> SPTR
    jmpl(m, done_)
    m.pool_jump()
    a.deflabel(stop_)
    a.tst_rr(5, 5); m.bt_far(done_)
    a.mov_rm_rn(13, 3); a.add_imm(C.C_SPTR, 3); m.imm32(C.IN_WR, 2)
    a.movl_pool('ix_lits', 5); a.mov_imm(len(C.SMP_LITS), 4)
    lr_, nr_ = m.newlabel('lr'), m.newlabel('nr'); a.deflabel(lr_)
    a.movl_ld_inc(5, 1); a.movl_ld(1, 0); a.cmpeq(3, 0); a.bf_lbl(nr_); a.movl_st(2, 1); a.deflabel(nr_); a.dt(4); a.bf_lbl(lr_)
    a.mov_imm(0, 0); a.movl_st_disp(0, C.C_RUN, 13); sti(m, 0, IXACT)
    a.deflabel(done_)
    a.deflabel(ixx)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- ix_disp: row 1 text of the INPUT FX page; " OVL" (overload) / "NOFX" (no FX buffer) on the INPUT screen ----
    a.deflabel('ix_disp'); a.stsl_pr()
    a.mov_imm(0, 14)
    def cput(s): sput(m, s)
    m.imm32(pg2.addr['dlabels'][XR0], 12); cput("FX")
    # (the "M 12" missed-block counter of 2026-11-05 was removed 2026-11-06 to make room for the reverb; MISS still counts)
    dd, din = m.newlabel('dd'), m.newlabel('din')
    pq = m.newlabel('pq')                                              # the prompt (MASTER FX on when the page opened)
    ld(m, PROMPT, 0); a.tst_rr(0, 0); m.bt_far(pq)
    m.imm32(pg2.addr['dlabels'][XP0], 12); cput("DISABLE MSTR FX?")
    m.imm32(pg2.addr['dlabels'][XR1], 12); cput("DO IT / CANCEL  "); jmpl(m, dd)
    a.deflabel(pq)
    m.pool_jump()
    m.imm32(pg2.addr['dlabels'][XR1], 12)
    rdb(m, XSEL, 6)
    a.mov_imm(X_DLY, 0); a.cmpeq(0, 6); m.bf_far(din); cput("MIX   FB        "); jmpl(m, dd)
    a.deflabel(din)
    drv = m.newlabel('drv'); a.mov_imm(X_MOD, 0); a.cmpeq(0, 6); m.bf_far(drv); cput("MIX   RATE      "); jmpl(m, dd)
    a.deflabel(drv)
    drr = m.newlabel('drr'); a.mov_imm(X_RVB, 0); a.cmpeq(0, 6); m.bf_far(drr); cput("MIX             "); jmpl(m, dd)
    a.deflabel(drr)
    m.pool_jump()
    cput("SOURCE:     ")
    s1, s2 = m.newlabel('s1'), m.newlabel('s2')
    ld(m, IXBP, 0); a.tst_rr(0, 0); m.bf_far(s1); cput("NOFX"); jmpl(m, dd)
    a.deflabel(s1); ld(m, KILL, 0); a.tst_rr(0, 0); m.bt_far(s2); cput(" OVL"); jmpl(m, dd)
    a.deflabel(s2)
    # CPU load of MASTER's block processor (the overload guard's measure), e.g. " 23%", while INPUT FX runs
    s3 = m.newlabel('s3')
    ld(m, IXACT, 0); a.tst_rr(0, 0); m.bt_far(s3)
    ld(m, LOADP, 4); a.cmppz(4); m.bf_far(s3)
    a.mov_imm(99, 0); c9 = m.newlabel('c9'); a.cmpgt(0, 4); a.bf_lbl(c9); a.mov_rm_rn(0, 4); a.deflabel(c9)
    a.mov_imm(10, 5); pg._hcall(m, '__udiv'); a.mov_rm_rn(0, 10); a.mov_rm_rn(1, 11)          # tens, ones
    def cputr(r):
        n = m.newlabel('cr'); a.movb_ld(12, 1); a.cmpeq(r, 1); a.bt_lbl(n); a.mov_rm_rn(r, 0); a.movb_st(0, 12)
        a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
    a.mov_imm(ord(' '), 3); cputr(3)
    zt = m.newlabel('zt'); a.tst_rr(10, 10); a.add_imm(0x30, 10); a.bf_lbl(zt); a.mov_imm(ord(' '), 10); a.deflabel(zt)
    cputr(10); a.add_imm(0x30, 11); cputr(11); a.mov_imm(ord('%'), 3); cputr(3); jmpl(m, dd)
    a.deflabel(s3); cput("    ")
    a.deflabel(dd)
    a.tst_rr(14, 14); px = m.newlabel('px'); m.bt_far(px); redraw(m)
    a.deflabel(px); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    a.deflabel('ix_vis'); a.movl_pool('vt_ix', 4); jmpl(m, 'vis_tab')
    m.pool_jump()
    # ---- msput: r4 = text, r5 = length, r12 = display text (advances); r14 = 1 if anything changed. r0 r1 r4 r5 ----
    a.deflabel('msput')
    l = m.newlabel('mp'); s_ = m.newlabel('ms'); a.deflabel(l)
    a.movb_ldinc(4, 0); a.movb_ld(12, 1); a.cmpeq(0, 1); a.bt_lbl(s_); a.movb_st(0, 12); a.mov_imm(1, 14)
    a.deflabel(s_); a.add_imm(1, 12); a.dt(5); a.bf_lbl(l)
    a.rts(); a.nop()
    # ---- vis_tab (r4 = table): fields shown only on their screens, the cursor skips hidden fields, LASTF, redraw.
    #      table: page, selector slot, LASTF slot, n, n x (widget, screen mask | -1 = always), m, m x (object, mask) ----
    a.deflabel('vis_tab'); a.stsl_pr()
    a.mov_rm_rn(4, 8); a.mov_imm(0, 14)
    a.movl_ld_disp(8, 4, 0); a.movb_ld(0, 0); a.extu_b(0, 0); a.mov_imm(1, 9); a.shad(0, 9)     # r9 = screen bit
    a.movl_ld_disp(8, 12, 10); a.mov_rm_rn(8, 11); a.add_imm(16, 11)
    a.bsr_lbl('vt_flags'); a.nop()
    a.movl_ld_inc(11, 10); a.bsr_lbl('vt_flags'); a.nop()
    vdone, vpick, vgo = m.newlabel('vd'), m.newlabel('vp'), m.newlabel('vg')
    m.imm32(1 << X_PROMPT, 0); a.cmpeq(9, 0); m.bt_far(vdone)                                  # prompt: every field hidden
    a.movl_ld(8, 0); a.movl_ld_disp(0, 0x24, 12)                                              # r12 = focused
    a.mov_rm_rn(12, 4); a.bsr_lbl('vt_index'); a.nop(); a.mov_rm_rn(0, 13)                   # r13 = its index
    a.cmppz(13); m.bf_far(vdone)
    a.mov_rm_rn(12, 1); a.add_imm(0x17, 1); a.movb_ld(1, 0); a.tst_imm(0x80); m.bf_far(vdone)  # visible: nothing to do
    a.movl_ld_disp(8, 8, 0); a.movl_ld(0, 10)                                                 # r10 = last focused
    a.mov_rm_rn(10, 4); a.bsr_lbl('vt_index'); a.nop(); a.mov_rm_rn(0, 7)                    # r7 = its index
    a.mov_imm(0, 6)
    a.cmppz(7); m.bf_far(vpick)
    a.movl_ld_disp(8, 12, 5)                                                                  # r5 = n
    fwd, bwd, fl, bl = m.newlabel('fw'), m.newlabel('bw'), m.newlabel('fl'), m.newlabel('bl')
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
    # ---- ix_page: the INPUT FX page is open: keep the OS sampler entered (input converter on), as Audio Track ----
    a.deflabel('ix_page'); a.stsl_pr()
    pe = m.newlabel('pe')
    m.imm32(IX.SMP + 8, 1); a.movl_ld(1, 0); a.tst_rr(0, 0); m.bf_far(pe)
    m.imm32(IX.SMP, 4); m.imm32(IX.SMP_ENTER, 2); a.jsr(2); a.nop(); sti(m, 1, IXENT)
    a.deflabel(pe)
    a.ldsl_pr(); a.rts(); a.nop()
    # ---- ix_leave: another page: give the sampler back if we entered it (not to the OS Record page, nor mid-take) ----
    a.deflabel('ix_leave'); a.stsl_pr()
    sti(m, 0, XWAS); sti(m, 0, PROMPT)                                 # next entry: the prompt again if MASTER FX is on
    le = m.newlabel('le')
    ld(m, IXENT, 0); a.tst_rr(0, 0); m.bt_far(le)
    sti(m, 0, IXENT)
    m.imm32(EVQ + 0x68, 1); a.movl_ld(1, 0); m.imm32(IX.REC_PAGE, 1); a.cmpeq(1, 0); m.bt_far(le)
    m.imm32(IX.SMP + 0x24, 1); a.movl_ld(1, 0); a.tst_rr(0, 0); m.bf_far(le)
    m.imm32(IX.SMP + 8, 1); a.movl_ld(1, 0); a.tst_rr(0, 0); m.bt_far(le)
    m.imm32(IX.SMP, 4); m.imm32(IX.SMP_EXIT, 2); a.jsr(2); a.nop()
    a.deflabel(le)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- tables ----
    while (len(a.words) * 2 + a.base) & 3: a.nop()
    a.deflabel('ix_ptab')
    for p in IX.DPRESETS:
        w = p[1] | (IX.pct_index(p[2]) << 8) | (IX.pct_index(p[3]) << 16); a._emit(w & 0xFFFF); a._emit(w >> 16)
    for nm, vals in (('ix_dtab', [v for row in IX.DTAB for v in row]), ('ix_tmul', IX.TMUL), ('ix_tms', IX.TMS),
                     ('ix_pq', [IX.pct_q12(i) for i in range(len(IX.PCT5))]),
                     ('ix_mtab', [v for row in IX.MTAB for v in row]),
                     ('ix_rtab', [v for row in IX.RTAB for v in row]),
                     ('ix_mrate', [IX.minc(r) for r in IX.MRATES]),
                     ('ix_apt', [v & M32 for v in IX.APT])):
        a.deflabel(nm)
        for v in vals: a._emit(v & 0xFFFF); a._emit((v >> 16) & 0xFFFF)
    a.deflabel('ix_lits')
    for v in IX.SMP_LITS: a._emit(v & 0xFFFF); a._emit(v >> 16)
    # screens of both pages (vis_tab)
    def vt(lbl, page, sel, lastf, cursor, vis, W_, extras):
        a.deflabel(lbl)
        for v in (page, sel, lastf, len(cursor)): tword(m, v)
        for s in cursor: tword(m, W_(s)); tword(m, sum(1 << p for p in vis[s]) if s in vis else M32)
        tword(m, len(extras))
        for o, mk in extras: tword(m, o); tword(m, mk)
    vt('vt_master', pg.addr['page'], PAGEF, LASTF_, CURSOR, VIS, W, [(pg.addr['dlabels'][R0] - 0x28, 1 << P_DBX)])
    lbo = lambda k: pg2.addr['dlabels'][k] - 0x28                     # the label object of a dlabel
    vt('vt_ix', pg2.addr['page'], XSCR, XLASTF, XCURSOR, {**XVIS, XSEL: [X_IN, X_DLY, X_MOD, X_RVB]}, W2,
       [(lbo(XR0), 15), (lbo(XP0), 1 << X_PROMPT)])
    # texts (msput)
    for s, lbl in STRS.items():
        a.deflabel(lbl); b_ = s.encode('latin-1') + (b'\0' if len(s) & 1 else b'')
        for i in range(0, len(b_), 2): a._emit(b_[i] | (b_[i + 1] << 8))

def gcall(m, lbl):
    """call a routine of the first page's code from the INPUT FX page's code (its labels have no 'ix:' prefix)"""
    ns = m.a.ns; m.a.ns = ''; call(m, lbl); m.a.ns = ns
def ix_fire(m):
    gcall(m, 'ix_pfx'); gcall(m, 'ix_page'); gcall(m, 'ix_vis'); gcall(m, 'ix_disp')
pg2.on_fire(ix_fire)
def ix_doit(m):
    """DO IT on the prompt: MASTER FX off (remembered: back on when INPUT FX goes off), then the INPUT FX screens"""
    a = m.a; nd = m.newlabel('nd')
    ld(m, PROMPT, 0); a.tst_rr(0, 0); m.bt_far(nd)
    sti(m, 0, PROMPT); a.mov_imm(0, 0); m.imm32(ONF, 1); a.movb_st(0, 1); sti(m, 1, SAVEDM); redraw(m)
    a.deflabel(nd)
pg2.on_doit(ix_doit)
def sel_cancel(p, wsel, prompt=None):
    """CANCEL one step back (user 2026-11-06): cursor on a setting -> back on the screen selector; on the selector (or the
    prompt) -> the default: back to the Pad FX list"""
    def fn(m, hret1):
        a = m.a; lv = m.newlabel('lv')
        if prompt is not None: ld(m, prompt, 0); a.tst_rr(0, 0); m.bf_far(lv)
        # (selector address - 4 in the pool: the real address = the page's "first" field, which the build's dummy
        # pass keeps distinct -> a shared pool word would shrink the second pass)
        m.imm32(p.addr['page'], 4); a.movl_ld_disp(4, 0x24, 0)
        a.movl_pool(wsel() - 4, 5); a.add_imm(4, 5); a.cmpeq(5, 0); m.bt_far(lv)
        m.imm32(SET_FOCUS, 2); a.jsr(2); a.nop(); redraw(m); jmpl(m, hret1)
        m.pool_jump()
        a.deflabel(lv)
    return fn
pg.cancel_fn = sel_cancel(pg, lambda: W(PAGEF))
pg2.cancel_fn = sel_cancel(pg2, lambda: W2(XSEL), PROMPT)
pg2.on_inactive(lambda m: gcall(m, 'ix_leave'))

def bulk(m):
    a = m.a
    a.movl_pool('ms_dx', 2); a.jmp(2); a.nop()
    m.pool_jump()
    subs(m)
    a.deflabel('ms_dx')
pg.on_doit(bulk)                                                    # (DO IT: nothing; the code lives here)

# ================= ISR (alias P1; Master FX architecture) =================
def frame_loop(m, body, n=BLK):
    a = m.a
    ld(m, ARRP, 12); a.mov_imm(n, 4)
    m.pool_jump(); l = m.newlabel('fl'); a.deflabel(l)
    body(m)
    a.add_imm(FSTR, 12); a.dt(4); m.bf_far(l)

def isr_code(m):
    a = m.a
    a.deflabel('ms_icinv')
    a.movl_pool('__cstart', 4); m.imm32(0xFFFFFFF0, 1); a.and_rr(1, 4)
    m.imm32(CACHE_SPAN // 16 + 1, 5); m.imm32(0xF0000008, 6); m.imm32(0x1FFFFC00, 7); m.imm32(0xFF0, 3)
    icl = m.newlabel('icl'); a.deflabel(icl)
    a.mov_rm_rn(4, 0); a.and_rr(3, 0); a.or_rr(6, 0); a.mov_rm_rn(4, 1); a.and_rr(7, 1); a.movl_st(1, 0)
    a.add_imm(16, 4); a.dt(5); a.bf_lbl(icl)
    a.rts(); a.nop()
    m.pool_jump()
    a.deflabel('ms_txchk')
    m.imm32(EXITLIT, 1); a.movl_ld(1, 3); m.imm32(ISR_EXIT, 4); a.cmpeq(4, 3); bad = m.newlabel('bad'); a.bf_lbl(bad)
    m.imm32(TXPATCH, 1); a.mov_imm(8, 2); a.mov_imm(1, 0)
    ck = m.newlabel('ck'); a.deflabel(ck)
    a.movw_ld(1, 3); a.extu_w(3, 3); m.imm32(0x6026, 4); a.cmpeq(4, 3); a.bf_lbl(bad); a.add_imm(2, 1)
    a.movw_ld(1, 3); a.extu_w(3, 3); m.imm32(0xC218, 4); a.cmpeq(4, 3); a.bf_lbl(bad); a.add_imm(2, 1)
    a.dt(2); a.bf_lbl(ck); a.rts(); a.nop()
    a.deflabel(bad); a.rts(); a.mov_imm(0, 0)
    m.pool_jump()
    def words(ws, lit, exitval):
        a.stc_sr(7); a.mov_rm_rn(7, 0); a.or_imm(0xF0); a.ldc_sr(0)
        m.imm32(TXPATCH, 1)
        for w in ws:
            if w == 'LO': a.mov_rm_rn(lit, 0)
            elif w == 'HI': a.mov_rm_rn(lit, 0); a.shlr16(0)
            else: m.imm32(w, 0)
            a.movw_st(0, 1); a.add_imm(2, 1)
        exitval(); m.imm32(EXITLIT, 1); a.movl_st(0, 1)
        a.ldc_sr(7); a.rts(); a.nop()
        m.pool_jump()
    a.deflabel('ms_txon')
    a.movl_pool('ms_tx', 6); m.imm32(0xDFFFFFFF, 1); a.and_rr(1, 6)
    words([0xD002, 0x400B, 0x0009, 0xA00B, 0x0009, 'LO', 'HI'] + [0x0009] * 9, 6,
          lambda: (a.movl_pool('ms_trig', 0), m.imm32(0xDFFFFFFF, 1), a.and_rr(1, 0)))
    a.deflabel('ms_txoff')
    words(TXORIG, 6, lambda: m.imm32(ISR_EXIT, 0))
    a.deflabel('__cstart')
    # ---- ms_tx: TX interrupt (instead of sending 8 longs) ----
    a.deflabel('ms_tx')
    for r in (3, 4, 5, 6, 7): a.movl_st_dec(r, 15)
    a.movl_ld(1, 3)
    a.mov_rm_rn(3, 0); a.shlr2(0); a.and_imm(7); a.shll2(0); a.shll2(0); a.shll(0); a.mov_rm_rn(0, 5)
    a.shlr2(3); a.shlr2(3); a.shlr(3)
    a.mov_rm_rn(3, 0); a.and_imm(3); a.shll8(0); m.a.movl_pool(_p1(m, INP), 4); a.movl_ld(4, 4); a.add_rr(0, 4); a.add_rr(5, 4)
    for _ in range(8): a.movl_ld(2, 0); a.add_imm(4, 2); a.movl_st(0, 4); a.add_imm(4, 4)
    a.add_imm(-LAT, 3); a.mov_rm_rn(3, 0); a.and_imm(3); a.mov_rm_rn(0, 6)
    a.shll2(0); m.a.movl_pool(_p1(m, OUTID[0]), 7); a.add_rr(0, 7); a.movl_ld(7, 7)
    a.shll8(6); a.add_rr(5, 6)
    m.a.movl_pool(_p1(m, INP), 4); a.cmpeq(3, 7); dry = m.newlabel('dry'); a.bf_lbl(dry); m.a.movl_pool(_p1(m, OUTP), 4); a.deflabel(dry)
    a.movl_ld(4, 4)
    a.add_rr(6, 4)
    for _ in range(8): a.movl_ld(4, 0); a.add_imm(4, 4); a.movl_st_gbr(0x60)
    mx = m.newlabel('mx'); a.tst_rr(5, 5); a.bf_lbl(mx)
    m.a.movl_pool(_p1(m, OUTID[0]), 7)
    a.mov_rm_rn(3, 0); a.and_imm(3); a.shll2(0); a.add_rr(7, 0); a.movl_ld(0, 6); a.cmpeq(3, 6); a.bt_lbl(mx)
    a.mov_rm_rn(3, 4); a.add_imm(-1, 4); a.mov_rm_rn(4, 0); a.and_imm(3); a.shll2(0); a.add_rr(7, 0); a.movl_ld(0, 6)
    a.cmpeq(4, 6); a.bf_lbl(mx)
    m.a.movl_pool(_p1(m, MISS), 6); a.movl_ld(6, 0); a.add_imm(1, 0); a.movl_st(0, 6)
    a.deflabel(mx)
    for r in (7, 6, 5, 4, 3): a.movl_ld_inc(15, r)
    a.rts(); a.nop()
    m.pool_jump()
    # ---- ms_trig: exit of every ISR / return of the render ----
    a.deflabel('ms_trig')
    ld(m, BUSY2, 0); a.tst_rr(0, 0); tx = m.newlabel('tx'); a.bf_lbl(tx)
    ld(m, ACT, 0); ld(m, G, 1); a.or_rr(1, 0); ld(m, IXACT, 1); a.or_rr(1, 0); a.tst_rr(0, 0); a.bt_lbl(tx)
    m.imm32(FRAMES, 1); a.movl_ld(1, 3); a.shlr2(3); a.shlr2(3); a.shlr(3); a.add_imm(-1, 3)
    ld(m, LASTJ, 0); a.cmpeq(3, 0); a.bt_lbl(tx)
    # blocks behind but still in time (2..LAT-1, 2026-11-05: HW dropouts "M 50"): start with the oldest; ms_pdone
    # then catches up block by block instead of skipping
    co = m.newlabel('co'); a.mov_rm_rn(3, 2); a.sub(0, 2); a.mov_imm(2, 1); a.cmpge(1, 2); a.bf_lbl(co)
    a.mov_imm(LAT - 1, 1); a.cmpgt(1, 2); a.bt_lbl(co); a.mov_rm_rn(0, 3); a.add_imm(1, 3)
    a.deflabel(co)
    st(m, 3, LASTJ); sti(m, 1, BUSY2)
    m.imm32(ISR_EXIT, 1); a.lds_pr(1)
    a.movl_pool('ms_proc', 3); m.imm32(0xDFFFFFFF, 1); a.and_rr(1, 3)
    a.stc_sr(0); m.imm32(0x20000000, 1); a.tst_rr(1, 0); rb0 = m.newlabel('rb0'); a.bt_lbl(rb0)
    a.ldc_spc(3); a.rte(); a.nop()
    a.deflabel(rb0); a.jmp(3); a.nop()
    a.deflabel(tx); m.imm32(ISR_EXIT, 1); a.jmp(1); a.nop()
    m.pool_jump()
    # ---- ms_proc ----
    a.deflabel('ms_proc')
    a.stsl_pr()
    for r in range(8, 15): a.movl_st_dec(r, 15)
    a.deflabel('ms_body')
    m.imm32(TCNT1, 1); a.movl_ld(1, 3); st(m, 3, TENT); m.imm32(TICKS, 1); a.movl_ld(1, 3); st(m, 3, TTK)
    a.movl_pool('P1:ix_proc', 2); a.jsr(2); a.nop()                                   # INPUT FX: the new input frames
    ld(m, CFFX, 0); a.tst_rr(0, 0); nc = m.newlabel('nc'); m.bf_far(nc)               # coefficients complete: lock them
    for k in range(NCF): ld(m, CF[k], 2); st(m, 2, CFR[k])
    sti(m, 1, CFOK)
    a.deflabel(nc)
    # overload (KILL): no effect, G = 0, the block goes out as it came in + INPUT FX's monitor (2026-11-05: before, the
    # TX sent the plain mix and the player's input went silent)
    ld(m, KILL, 0); a.tst_rr(0, 0); nkl = m.newlabel('nkl'); a.bt_lbl(nkl); sti(m, 0, G)
    a.deflabel(nkl)
    ld(m, LASTJ, 0); a.and_imm(3); a.shll8(0); ld(m, INP, 3); a.add_rr(0, 3); st(m, 3, TXP)
    # MASTER FX off and faded (INPUT FX alone), or overload: the block goes out as it came in (= the mix with G = 0)
    cpy, kc = m.newlabel('cpy'), m.newlabel('kc')
    ld(m, KILL, 0); a.tst_rr(0, 0); m.bf_far(kc)
    ld(m, ACT, 0); ld(m, G, 1); a.or_rr(1, 0); a.tst_rr(0, 0); m.bf_far(cpy)
    a.deflabel(kc)
    ld(m, LASTJ, 0); a.and_imm(3); a.shll8(0); ld(m, OUTP, 11); a.add_rr(0, 11); a.mov_imm(2 * BLK, 4)
    cl_ = m.newlabel('cl'); a.deflabel(cl_); a.movl_ld_inc(3, 0); a.movl_st(0, 11); a.add_imm(4, 11); a.dt(4); a.bf_lbl(cl_)
    a.movl_pool('P1:ms_mixd', 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(cpy)
    a.mov_imm(-SH, 5); m.imm32(32767, 14)
    def unpack(m):
        a.movl_ld(3, 0); a.shad(5, 0); clip(m, 0, 14, 2); a.movl_ld_disp(3, 4, 1); a.shad(5, 1); clip(m, 1, 14, 2)
        a.movl_st_disp(0, FW, 12); a.movl_st_disp(0, FDL, 12); a.movl_st_disp(1, FWR, 12); a.movl_st_disp(1, FDR, 12)
        a.add_imm(8, 3)
    frame_loop(m, unpack)
    ld(m, CFOK, 0); a.tst_rr(0, 0); jt_abs(m, 'ms_mix')
    fx_master(m)
    a.movl_pool('P1:ms_mix', 2); a.jmp(2); a.nop()
    m.pool_jump()
    # ---- mix with ramp (G -> 4096 / 0 by GSTEP per block), original low bits -> OUT[j%4] ----
    a.deflabel('ms_mix')
    ld(m, G, 8); a.mov_imm(0, 9); ld(m, ACT, 0); a.tst_rr(0, 0); ga = m.newlabel('ga'); a.bt_lbl(ga); m.imm32(4096, 9); a.deflabel(ga)
    a.sub(8, 9); m.imm32(GSTEP, 0); c1 = m.newlabel('c1'); a.cmpgt(0, 9); a.bf_lbl(c1); a.mov_rm_rn(0, 9); a.deflabel(c1)
    a.neg(0, 0); c2 = m.newlabel('c2'); a.cmpgt(9, 0); a.bf_lbl(c2); a.mov_rm_rn(0, 9); a.deflabel(c2)
    a.mov_rm_rn(8, 0); a.add_rr(9, 0); st(m, 0, G)
    ld(m, TXP, 3); ld(m, LASTJ, 0); a.and_imm(3); a.shll8(0); ld(m, OUTP, 11); a.add_rr(0, 11)
    m.imm32(32767, 14); a.mov_imm(0, 10)
    a.mov_imm(SH, 5); m.imm32((1 << SH) - 1, 13)
    def mix(m):
        a.mov_rm_rn(9, 2); a.mul_l(10, 2); a.sts_macl(2); sar(m, 2, BSH, 0); a.add_rr(8, 2)
        for fw, fd, lo in ((FW, FDL, 0), (FWR, FDR, 4)):
            a.movl_ld_disp(12, fw, 0); a.movl_ld_disp(12, fd, 7); a.sub(7, 0); a.mul_l(2, 0); a.sts_macl(0)
            sar(m, 0, 12, 1); a.add_rr(7, 0); clip(m, 0)
            a.movl_ld_disp(3, lo, 1); a.and_rr(13, 1); a.shad(5, 0); a.or_rr(1, 0); a.movl_st_disp(0, lo, 11)
        a.add_imm(8, 3); a.add_imm(8, 11); a.add_imm(1, 10)
    m.imm32(4096, 0); a.cmpeq(0, 8); slow = m.newlabel('slow'); a.bf_lbl(slow); a.tst_rr(9, 9); a.bf_lbl(slow)
    def fastmix(m):
        for fw, lo in ((FW, 0), (FWR, 4)):
            a.movl_ld_disp(12, fw, 0); clip(m, 0)
            a.movl_ld_disp(3, lo, 1); a.and_rr(13, 1); a.shad(5, 0); a.or_rr(1, 0); a.movl_st_disp(0, lo, 11)
        a.add_imm(8, 3); a.add_imm(8, 11)
    frame_loop(m, fastmix)
    a.movl_pool('P1:ms_mixd', 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(slow)
    frame_loop(m, mix)
    a.deflabel('ms_mixd')
    a.movl_pool('P1:ix_mon', 2); a.jsr(2); a.nop()                                    # INPUT FX: monitor into OUT[j%4]
    ld(m, LASTJ, 0); a.mov_rm_rn(0, 3); a.and_imm(3); a.shll2(0); m.a.movl_pool(_p1(m, OUTID[0]), 1); a.add_rr(0, 1); a.movl_st(3, 1)
    # load guard (Master FX): sum of durations / elapsed over NWIN blocks; > LOADMAX % -> KILL
    m.imm32(TCOR1, 1); a.movl_ld(1, 8); a.add_imm(1, 8)
    m.imm32(TCNT1, 1); a.movl_ld(1, 9); m.imm32(TICKS, 1); a.movl_ld(1, 10)
    ld(m, TTK, 0); a.mov_rm_rn(10, 2); a.sub(0, 2); a.mul_l(8, 2); a.sts_macl(2)
    ld(m, TENT, 0); a.sub(9, 0); a.add_rr(2, 0)
    ld(m, DSUM, 2); a.add_rr(0, 2); st(m, 2, DSUM)
    ld(m, NCALL, 0); a.tst_rr(0, 0); w0 = m.newlabel('w0'); a.bf_lbl(w0)
    st(m, 10, TWT); ld(m, TENT, 2); st(m, 2, TWC)
    sti(m, 0, DSUM); a.deflabel(w0)
    a.add_imm(1, 0); st(m, 0, NCALL); m.imm32(NWIN, 1); a.cmpge(1, 0); dn_ = m.newlabel('dn'); m.bf_far(dn_)
    ld(m, TWT, 0); a.sub(0, 10); a.mul_l(8, 10); a.sts_macl(5)
    ld(m, TWC, 0); a.sub(9, 0); a.add_rr(0, 5)
    ld(m, DSUM, 4); a.mov_imm(100, 0); a.mul_l(0, 4); a.sts_macl(4)
    a.bsr_lbl('ms_udiv'); a.nop(); st(m, 0, LOADP)
    # KILL only after OVN windows in a row over LOADMAX (2026-11-05, HW: one spike while idle latched it)
    okw = m.newlabel('okw')
    a.mov_rm_rn(0, 3); sti(m, 0, NCALL); a.mov_imm(LOADMAX, 1); a.cmpgt(1, 3); m.bf_far(okw)
    ld(m, OVC, 0); a.add_imm(1, 0); st(m, 0, OVC); a.mov_imm(OVN, 1); a.cmpge(1, 0); m.bf_far(dn_); sti(m, 1, KILL)
    a.bra_lbl(dn_); a.nop()
    a.deflabel(okw); sti(m, 0, OVC)
    a.deflabel(dn_)
    a.deflabel('ms_pdone')
    # a block still due (1..LAT-1 behind the last complete one): process it now, it is not sent yet
    cn2 = m.newlabel('cn2')
    m.imm32(FRAMES, 1); a.movl_ld(1, 3); a.shlr2(3); a.shlr2(3); a.shlr(3); a.add_imm(-1, 3)
    ld(m, LASTJ, 0); a.sub(0, 3); a.cmppl(3); a.bf_lbl(cn2)
    a.mov_imm(LAT - 1, 1); a.cmpgt(1, 3); a.bt_lbl(cn2)
    a.add_imm(1, 0); st(m, 0, LASTJ)
    a.movl_pool('P1:ms_body', 2); a.jmp(2); a.nop()
    a.deflabel(cn2)
    sti(m, 0, BUSY2)
    for r in range(14, 7, -1): a.movl_ld_inc(15, r)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    a.deflabel('ms_udiv'); a.mov_imm(0, 0); a.mov_imm(0, 1); a.tst_rr(5, 5); dz = m.newlabel('dz'); a.bt_lbl(dz); a.mov_imm(32, 2)
    dl = m.newlabel('dl'); a.deflabel(dl); a.shll(4); a.rotcl(1); a.cmphs(5, 1); ds = m.newlabel('ds'); a.bf_lbl(ds); a.sub(5, 1); a.sett(); a.deflabel(ds); a.rotcl(0); a.dt(2); a.bf_lbl(dl)
    a.deflabel(dz); a.rts(); a.nop()
    m.pool_jump()
    ix_isr(m)
    eq_channel(m)
    # ---- tables ----
    while (len(a.words) * 2 + a.base) & 3: a.nop()
    a.deflabel('ms_tabs')
    for w in TABWORDS: a._emit(w & 0xFFFF); a._emit((w >> 16) & 0xFFFF)
    CEND[0] = len(a.words) * 2 + a.base - a.labels.get(a._n('__cstart'), a.base) if hasattr(a, 'labels') else None
CEND = [None]

# ---- table layout ----
T_KQ = [q(T_SVF[i * 4 + 3], 16) for i in range(len(T_SVF) // 4)]   # 1/Q per (freq, width); KB = KQ * BH >> 16 (main loop)
T_BHQ = [q(b, 16) for b in T_BH]
T_SVF3 = []
for i in range(len(T_SVF) // 4): T_SVF3 += T_SVF[4 * i:4 * i + 3]
TABS = [('LP2', T_LP2), ('LP1', T_LP1), ('B', T_B), ('A', T_A), ('SVF', T_SVF3), ('CH', T_CH), ('AH', T_AH),
        ('KQ', T_KQ), ('BH', T_BHQ), ('THR', T_THR), ('SLOPE', T_SLOPE), ('OUT', T_OUT), ('EXP2', T_EXP2), ('LOG2', T_LOG2)]
TOFF = {}; TABWORDS = []
for nm, vals in TABS: TOFF[nm] = 4 * len(TABWORDS); TABWORDS += [v & M32 for v in vals]

# ================= INPUT FX (ISR, P1): see infx.py; r13 = state block (coefficients), r12 = r13 + 0x40 (states) ==========
def ix_isr(m):
    a = m.a
    C, S = IX, IX
    def sh(r, n, tmp): a.mov_imm(n, tmp); a.shad(tmp, r)
    def q16(rq, rbase_off_reg):
        """rq = address of entry (index & 255) of the queue at r13 + offset in rbase_off_reg (destroys r0)"""
    # ---- ix_proc: from ms_proc (interrupts on, r0-r14 free, returns with rts) ----
    a.deflabel('ix_proc'); a.stsl_pr()
    ixr = m.newlabel('ixr')
    ld(m, IXBP, 13); a.tst_rr(13, 13); m.bt_far(ixr)
    a.movl_ld_disp(13, C.C_RUN, 0); a.tst_rr(0, 0); m.bt_far(ixr)
    a.mov_rm_rn(13, 12); a.add_imm(0x40, 12)
    # 1) SOURCE MAIN: the dry block just captured (IN[LASTJ % 4], pre-MASTER) into the MAIN queue
    nomq = m.newlabel('nomq')
    a.movl_ld_disp(13, C.C_SRC, 0); a.tst_rr(0, 0); m.bt_far(nomq)
    ld(m, LASTJ, 0); a.and_imm(3); a.shll8(0); ld(m, INP, 1); a.add_rr(0, 1)
    a.mov_imm(C.MAINQ_OFF >> 8, 2); a.shll8(2); a.add_rr(13, 2)
    a.movl_ld_disp(12, S.S_MQW, 3); a.mov_imm(32, 4); m.imm32(0xFFFF0000, 7)
    l = m.newlabel('mq'); a.deflabel(l)
    a.movl_ld_inc(1, 5); a.movl_ld_inc(1, 6); a.shlr16(5); a.and_rr(7, 6); a.or_rr(6, 5)
    a.mov_rm_rn(3, 0); a.and_imm(255); a.shll2(0); a.add_rr(2, 0); a.movl_st(5, 0)
    a.add_imm(1, 3); a.dt(4); a.bf_lbl(l)
    a.movl_st_disp(3, S.S_MQW, 12)
    # queue level: start once 48 frames are there, drop the excess above 160
    a.movl_ld_disp(12, S.S_MQR, 1); a.mov_rm_rn(3, 0); a.sub(1, 0)
    ok1 = m.newlabel('mk'); a.mov_imm(C.MON_START, 2); a.cmpge(2, 0); a.bf_lbl(ok1); a.mov_imm(1, 2); a.movl_st_disp(2, S.S_MQOK, 12)
    a.deflabel(ok1)
    ok2 = m.newlabel('mx'); m.imm32(C.MON_MAX, 2); a.cmpgt(2, 0); a.bf_lbl(ok2)
    a.mov_rm_rn(3, 1); a.add_imm(-C.MON_TGT, 1); a.movl_st_disp(1, S.S_MQR, 12)
    a.deflabel(ok2)
    a.deflabel(nomq)
    m.pool_jump()
    # 2) the new input frames: SPOS .. the interrupt's write pointer (first: the phaser's coefficients for this block,
    #    from the lfo phase - the same with no new frames, the phase only moves with frames)
    a.movl_pool('P1:ix_modblk', 2); a.jsr(2); a.nop()
    ixd = m.newlabel('ixd')
    m.imm32(C.IN_WR, 1); a.movl_ld(1, 10); a.movl_ld_disp(13, C.C_SPOS, 9)
    a.mov_rm_rn(10, 0); a.sub(9, 0); m.imm32(C.RMASK, 1); a.and_rr(1, 0)
    a.tst_rr(0, 0); m.bt_far(ixd)
    rs = m.newlabel('rs'); m.imm32(512 * 8, 1); a.cmphs(1, 0); a.bf_lbl(rs)                # > 512 frames: resync
    m.imm32(32 * 8, 0); a.mov_rm_rn(10, 9); a.sub(0, 9); m.imm32(C.RING, 1); a.sub(1, 9); m.imm32(C.RMASK, 2)
    a.and_rr(2, 9); a.add_rr(1, 9)
    a.deflabel(rs)
    a.shlr2(0); a.shlr(0); a.mov_rm_rn(0, 8)                                                 # r8 = frames
    a.mov_imm(C.NCAP, 0); cn = m.newlabel('cn'); a.cmpgt(0, 8); a.bf_lbl(cn); a.mov_rm_rn(0, 8); a.deflabel(cn)   # <= NCAP
    a.movl_st_disp(9, C.S_SP0, 12); a.movl_st_disp(8, C.S_N, 12)
    m.imm32(C.FA_OFF, 0); a.add_rr(13, 0); a.movl_st_disp(0, C.S_FAP, 12)
    m.imm32(C.DLY_OFF, 10); a.add_rr(13, 10)                                                 # r10 = delay line
    a.movl_ld_disp(13, C.C_DW, 11); m.imm32(32767, 14)
    m.pool_jump()
    lp = m.newlabel('fl'); a.deflabel(lp)
    # x: the input frame, or (SOURCE MAIN) the next dry mix frame
    hx, mz, mu = m.newlabel('hx'), m.newlabel('mz'), m.newlabel('mu')
    a.movl_ld_disp(13, C.C_SRC, 0); a.tst_rr(0, 0); mx_ = m.newlabel('mxx'); a.bf_lbl(mx_)
    a.movl_ld(9, 6); a.shlr16(6); a.exts_w(6, 6); a.movl_ld_disp(9, 4, 7); a.shlr16(7); a.exts_w(7, 7)
    a.bra_lbl(hx); a.nop()
    a.deflabel(mx_)
    a.movl_ld_disp(12, S.S_MQOK, 0); a.tst_rr(0, 0); a.bt_lbl(mz)
    a.movl_ld_disp(12, S.S_MQR, 1); a.movl_ld_disp(12, S.S_MQW, 2); a.cmpeq(1, 2); a.bt_lbl(mu)
    a.mov_rm_rn(1, 0); a.and_imm(255); a.shll2(0); a.mov_imm(C.MAINQ_OFF >> 8, 3); a.shll8(3); a.add_rr(13, 3); a.add_rr(3, 0)
    a.movl_ld(0, 2); a.add_imm(1, 1); a.movl_st_disp(1, S.S_MQR, 12)
    a.exts_w(2, 6); a.mov_rm_rn(2, 7); sh(7, -16, 3)
    a.bra_lbl(hx); a.nop()
    a.deflabel(mu); a.mov_imm(0, 0); a.movl_st_disp(0, S.S_MQOK, 12)
    a.deflabel(mz); a.mov_imm(0, 6); a.mov_imm(0, 7)
    a.deflabel(hx)
    # x into the frame array (+16, +20: wet = y - x below), then the modulation (chorus / phaser) on r6 r7
    a.movl_ld_disp(12, C.S_FAP, 0); a.movl_st_disp(6, 16, 0); a.movl_st_disp(7, 20, 0)
    nmo = m.newlabel('nmo')
    a.mov_rm_rn(13, 0); a.add_imm(0x40, 0); a.add_imm(0x40, 0); a.movl_ld(0, 0); a.tst_rr(0, 0); a.bt_lbl(nmo)   # M_ON
    a.movl_pool('P1:ix_mod', 2); a.jsr(2); a.nop()
    a.deflabel(nmo)
    m.pool_jump()
    # delay (off, or overload: y = x, wet 0)
    nod, dd = m.newlabel('nod'), m.newlabel('dd')
    a.movl_ld_disp(13, C.C_DON, 0); a.tst_rr(0, 0); m.bt_far(nod)
    a.movl_ld_disp(13, C.C_KILL, 0); a.tst_rr(0, 0); m.bf_far(nod)
    # wow LFO -> read offset (Q8 frames)
    a.movl_ld_disp(12, S.S_PH, 0); a.movl_ld_disp(13, C.C_MODINC, 1); a.add_rr(1, 0); a.movl_st_disp(0, S.S_PH, 12)
    a.shlr16(0); a.mov_rm_rn(0, 1); a.shll16(1); tp = m.newlabel('tp'); a.cmppz(1); a.bt_lbl(tp); a.not_rr(0, 0); a.extu_w(0, 0)
    a.deflabel(tp)
    a.movl_ld_disp(13, C.C_MODD, 1); a.mul_l(1, 0); a.sts_macl(0); sh(0, -15, 1)
    a.mov_rm_rn(0, 3); a.and_imm(255); a.mov_rm_rn(0, 5); a.mov_rm_rn(3, 0); a.shlr8(0)   # r5 = frac, r0 = whole
    a.mov_rm_rn(11, 1); a.movl_ld_disp(13, C.C_DT, 2); a.sub(2, 1); a.sub(0, 1); a.extu_w(1, 1)
    a.mov_rm_rn(1, 2); a.shll2(2); a.add_rr(10, 2); a.movl_ld(2, 3)                           # wa
    a.add_imm(-1, 1); a.extu_w(1, 1); a.shll2(1); a.add_rr(10, 1); a.movl_ld(1, 4)            # wb
    a.exts_w(3, 0); a.exts_w(4, 1); a.sub(0, 1); a.mul_l(5, 1); a.sts_macl(1); sh(1, -8, 2); a.add_rr(0, 1)   # dl
    a.mov_imm(-16, 2); a.shad(2, 3); a.shad(2, 4); a.sub(3, 4); a.mul_l(5, 4); a.sts_macl(4); sh(4, -8, 2); a.add_rr(3, 4)  # dr
    m.pool_jump()
    # tone of the repeats: low-pass, then high-pass, then the 12-bit mask
    for d, LP, HP, T in ((1, S.S_FLL, S.S_FHL, S.S_TFL), (4, S.S_FLR, S.S_FHR, S.S_TFR)):
        a.movl_ld_disp(12, LP, 0); a.sub(0, d); a.movl_ld_disp(13, C.C_CLP, 2); a.mul_l(2, d); a.sts_macl(d); sh(d, -15, 2)
        a.add_rr(d, 0); a.movl_st_disp(0, LP, 12)
        a.movl_ld_disp(12, HP, 1); a.mov_rm_rn(0, 3); a.sub(1, 3); a.movl_ld_disp(13, C.C_CHP, 2); a.mul_l(2, 3); a.sts_macl(3)
        sh(3, -15, 2); a.add_rr(3, 1); a.movl_st_disp(1, HP, 12)
        a.sub(1, 0); a.movl_ld_disp(13, C.C_QM, 2); a.and_rr(2, 0); a.movl_st_disp(0, T, 12)
    m.pool_jump()
    # feedback into the line (ping pong: crossed, input only on the left)
    a.movl_ld_disp(13, C.C_FB, 2)
    a.movl_ld_disp(12, S.S_TFL, 1); a.mul_l(2, 1); a.sts_macl(1); sh(1, -12, 3)                # tl
    a.movl_ld_disp(12, S.S_TFR, 4); a.mul_l(2, 4); a.sts_macl(4); sh(4, -12, 3)                # tr
    np_, wc = m.newlabel('np'), m.newlabel('wc')
    a.movl_ld_disp(13, C.C_PING, 0); a.tst_rr(0, 0); a.bt_lbl(np_)
    a.mov_rm_rn(6, 0); a.add_rr(7, 0); a.shar(0); a.add_rr(4, 0); a.mov_rm_rn(1, 2); a.bra_lbl(wc); a.nop()
    a.deflabel(np_); a.mov_rm_rn(6, 0); a.add_rr(1, 0); a.mov_rm_rn(7, 2); a.add_rr(4, 2)
    a.deflabel(wc)
    clip(m, 0, 14, 3); clip(m, 2, 14, 3)
    a.extu_w(0, 0); a.shll16(2); a.or_rr(2, 0); a.mov_rm_rn(11, 1); a.shll2(1); a.add_rr(10, 1); a.movl_st(0, 1)
    a.add_imm(1, 11); a.extu_w(11, 11)
    # wet = filtered repeat * MIX; y = x + wet
    a.movl_ld_disp(13, C.C_MIX, 2)
    a.movl_ld_disp(12, S.S_TFL, 1); a.mul_l(2, 1); a.sts_macl(1); sh(1, -12, 3); a.movl_st_disp(1, S.S_TFL, 12)
    a.movl_ld_disp(12, S.S_TFR, 4); a.mul_l(2, 4); a.sts_macl(4); sh(4, -12, 3); a.movl_st_disp(4, S.S_TFR, 12)
    a.add_rr(1, 6); clip(m, 6, 14, 3); a.add_rr(4, 7); clip(m, 7, 14, 3)
    a.bra_lbl(dd); a.nop()
    a.deflabel(nod); a.mov_imm(0, 0); a.movl_st_disp(0, S.S_TFL, 12); a.movl_st_disp(0, S.S_TFR, 12)
    a.deflabel(dd)
    m.pool_jump()
    # into the frame array: y and the wet = y - x (write-back and monitor run on the whole block after this loop)
    a.movl_ld_disp(12, C.S_FAP, 0); a.movl_st(6, 0); a.movl_st_disp(7, 4, 0)
    a.movl_ld_disp(0, 16, 1); a.mov_rm_rn(6, 2); a.sub(1, 2); a.movl_st_disp(2, 8, 0)
    a.movl_ld_disp(0, 20, 1); a.mov_rm_rn(7, 2); a.sub(1, 2); a.movl_st_disp(2, 12, 0)
    a.add_imm(32, 0); a.movl_st_disp(0, C.S_FAP, 12)
    # next frame (ring wrap)
    a.add_imm(8, 9); m.imm32(C.RING_END, 0); nw = m.newlabel('nw'); a.cmpeq(0, 9); a.bf_lbl(nw); m.imm32(C.RING, 9)
    a.deflabel(nw)
    a.dt(8); m.bf_far(lp)
    a.movl_st_disp(11, C.C_DW, 13)
    a.movl_pool('P1:ix_rvb', 2); a.jsr(2); a.nop()                                         # reverb on the block
    m.pool_jump()
    # write-back: the processed frames into the ring (what the sampler records); monitor: INPUT -> y, MAIN -> wet
    ld(m, IXBP, 13); a.mov_rm_rn(13, 12); a.add_imm(0x40, 12)
    a.movl_ld_disp(12, C.S_SP0, 9); a.movl_ld_disp(12, C.S_N, 8); m.imm32(C.FA_OFF, 1); a.add_rr(13, 1)
    a.mov_imm(C.MONQ_OFF >> 8, 3); a.shll8(3); a.add_rr(13, 3); a.movl_ld_disp(12, S.S_MONW, 2); a.movl_ld_disp(13, C.C_SRC, 4)
    m.imm32(C.RING_END, 10); m.imm32(C.RING, 11)
    m.pool_jump()
    lb = m.newlabel('lb'); a.deflabel(lb)
    a.movl_ld(1, 0); a.shll16(0); a.movl_st(0, 9); a.movl_ld_disp(1, 4, 0); a.shll16(0); a.movl_st_disp(0, 4, 9)
    mi, mp = m.newlabel('mi'), m.newlabel('mp')
    a.tst_rr(4, 4); a.bt_lbl(mi); a.movl_ld_disp(1, 8, 5); a.movl_ld_disp(1, 12, 6); a.bra_lbl(mp); a.nop()
    a.deflabel(mi); a.movl_ld(1, 5); a.movl_ld_disp(1, 4, 6)
    a.deflabel(mp)
    a.extu_w(5, 5); a.shll16(6); a.or_rr(6, 5)
    a.mov_rm_rn(2, 0); a.and_imm(255); a.shll2(0); a.add_rr(3, 0); a.movl_st(5, 0); a.add_imm(1, 2)
    a.add_imm(32, 1); a.add_imm(8, 9); nw2 = m.newlabel('nw2'); a.cmpeq(10, 9); a.bf_lbl(nw2); a.mov_rm_rn(11, 9); a.deflabel(nw2)
    a.dt(8); m.bf_far(lb)
    a.movl_st_disp(2, S.S_MONW, 12)
    a.movl_st_disp(9, C.C_SPOS, 13); a.movl_st_disp(9, C.C_SPTR, 13)                       # the sampler may read them now
    a.deflabel(ixd)
    a.deflabel(ixr)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- ix_mon: add the monitor queue to OUT[LASTJ % 4] (32 frames), from ms_proc after the mix ----
    a.deflabel('ix_mon'); a.stsl_pr()
    mr = m.newlabel('mr')
    ld(m, IXBP, 13); a.tst_rr(13, 13); m.bt_far(mr)
    a.movl_ld_disp(13, C.C_RUN, 0); a.tst_rr(0, 0); m.bt_far(mr)
    a.mov_rm_rn(13, 12); a.add_imm(0x40, 12)
    a.movl_ld_disp(12, S.S_MONW, 3); a.movl_ld_disp(12, S.S_MONR, 1); a.mov_rm_rn(3, 0); a.sub(1, 0)   # r0 = level
    st1 = m.newlabel('st1'); a.mov_imm(C.MON_START, 2); a.cmpge(2, 0); a.bf_lbl(st1); a.mov_imm(1, 2); a.movl_st_disp(2, S.S_MONOK, 12)
    a.deflabel(st1)
    nx1 = m.newlabel('nx1'); m.imm32(C.MON_MAX, 2); a.cmpgt(2, 0); a.bf_lbl(nx1)
    a.mov_rm_rn(3, 1); a.add_imm(-C.MON_TGT, 1); a.movl_st_disp(1, S.S_MONR, 12)
    a.deflabel(nx1)
    a.movl_ld_disp(12, S.S_MONOK, 0); a.tst_rr(0, 0); m.bt_far(mr)
    ld(m, LASTJ, 0); a.and_imm(3); a.shll8(0); ld(m, OUTP, 11); a.add_rr(0, 11)             # r11 = OUT block
    a.mov_imm(C.MONQ_OFF >> 8, 10); a.shll8(10); a.add_rr(13, 10)                           # r10 = queue
    a.mov_imm(32, 8); m.imm32(32767, 14); m.imm32(0xFFFF, 9)
    ml = m.newlabel('ml'); mend = m.newlabel('me'); a.deflabel(ml)
    a.movl_ld_disp(12, S.S_MONR, 1); a.movl_ld_disp(12, S.S_MONW, 2); a.cmpeq(1, 2); un = m.newlabel('un'); a.bt_lbl(un)
    a.mov_rm_rn(1, 0); a.and_imm(255); a.shll2(0); a.add_rr(10, 0); a.movl_ld(0, 4); a.add_imm(1, 1); a.movl_st_disp(1, S.S_MONR, 12)
    a.exts_w(4, 5); a.mov_imm(-16, 2); a.shad(2, 4)                                         # r5 = L, r4 = R
    for v, off in ((5, 0), (4, 4)):
        a.movl_ld_disp(11, off, 0); a.mov_rm_rn(0, 6); a.and_rr(9, 6); a.shad(2, 0); a.add_rr(v, 0); clip(m, 0, 14, 3)
        a.shll16(0); a.or_rr(6, 0); a.movl_st_disp(0, off, 11)
    a.add_imm(8, 11); a.dt(8); a.bf_lbl(ml); a.bra_lbl(mend); a.nop()
    a.deflabel(un); a.mov_imm(0, 0); a.movl_st_disp(0, S.S_MONOK, 12)
    a.deflabel(mend)
    a.deflabel(mr)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    ix_mod_code(m)
    ix_rvb_code(m)

def ix_rvb_code(m):
    """ix_rvb (2026-11-06): the live reverb (infx.Model.reverb_block) on the frame array after the per-frame loop, at
    HALF RATE: one step per frame pair, one stage at a time over the call's pairs (state in registers). Frame +0 / +4 =
    y, +8 / +12 = wet; scratch in the pair's first frame: +16 x (mono) then wet L, +20 even combs then wet R, +24 odd
    combs then o R, +28 o L. Equal to the model: every line is longer than a call. r0-r14."""
    a = m.a; C = IX
    def sh(r, n, tmp): a.mov_imm(n, tmp); a.shad(tmp, r)
    def bases(): a.bsr_lbl('ixr_b'); a.nop()        # r13 = IX, r10 = RB, r11 = RS, r8 = pairs, r12 = frames, r1 = RW
    def line(r, k):                                   # r = base of line k (0 PD, 1..4 combs, 5 APL, 6 APR, 7 D0..D3)
        m.imm32(C.REV_OFF + k * C.RLN * 2, r); a.add_rr(13, r)
    def loop(body):
        l = m.newlabel('rl'); a.deflabel(l); body(); a.add_imm(64, 12); a.dt(8); a.bf_lbl(l)
    def adv(): a.add_imm(1, 1); a.and_rr(7, 1)
    def rd_line(off_reg, dst, base=2):                # dst = line base [(r1 - off_reg) & mask]
        a.mov_rm_rn(1, 0); a.sub(off_reg, 0); a.and_rr(7, 0); a.shll(0); a.add_rr(base, 0); a.movw_ld(0, dst)
    def wr_line(src, tmp, base=2):                    # line base [r1] = src
        a.mov_rm_rn(1, tmp); a.shll(tmp); a.add_rr(base, tmp); a.movw_st(src, tmp)
    a.deflabel('ix_rvb'); a.stsl_pr()
    rx = m.newlabel('rx')
    bases()
    a.movl_ld_disp(10, C.R_ON, 0); a.tst_rr(0, 0); m.bt_far(rx)
    a.movl_ld_disp(13, C.C_KILL, 0); a.tst_rr(0, 0); m.bf_far(rx)
    a.tst_rr(8, 8); m.bt_far(rx)
    m.imm32(32767, 14); m.imm32(C.RMSK, 7); a.mov_imm(-15, 9)
    # 1) (L0 + R0 + L1 + R1) / 16 -> pre-delay + input band-limit -> +16; comb sums +20, +24 = 0
    line(2, 0); a.movl_ld_disp(10, C.R_PDN, 3); a.movl_ld_disp(10, C.R_CBW, 4); a.movl_ld_disp(11, C.Q_BW, 5)
    def s1():
        a.movl_ld(12, 0)
        for o in (4, 32, 36): a.movl_ld_disp(12, o, 6); a.add_rr(6, 0)
        sh(0, -4, 6); wr_line(0, 6)
        rd_line(3, 6)
        a.mov_rm_rn(6, 0); a.sub(5, 0); a.mul_l(4, 0); a.sts_macl(0); a.shad(9, 0); a.add_rr(0, 5)
        a.movl_st_disp(5, 16, 12); a.mov_imm(0, 0)
        for o in (20, 24): a.movl_st_disp(0, o, 12)
        adv()
    loop(s1)
    a.movl_st_disp(5, C.Q_BW, 11)
    m.pool_jump()
    # 2) the 4 input diffusers (allpass g 1/2) in one pass on frame +16: v = x + bo / 2 -> line, x = bo - v / 2.
    #    Their lines are interleaved (8 B per index: D0..D3 s16) at line 7: one write pointer serves all four.
    #    D0 needs no clip: its input is the band-limited (L0+R0+L1+R1)/16 (|x| <= 8192).
    bases(); line(2, 7)
    LR_ = (9, 10, 11, 13)
    for Ld, r in zip(C.DIFF, LR_): m.imm32(Ld, r)
    def s2():
        a.mov_rm_rn(1, 3); a.shll2(3); a.shll(3); a.add_rr(2, 3)                              # r3 = write address
        a.movl_ld_disp(12, 16, 5)                                                            # r5 = x
        for j, r in enumerate(LR_):
            a.mov_rm_rn(1, 6); a.sub(r, 6); a.and_rr(7, 6); a.shll2(6); a.shll(6); a.add_rr(2, 6)
            a._emit(0x8500 | (6 << 4) | j)                                                   # mov.w @(2j,r6),r0 = bo
            a.mov_rm_rn(0, 4); a.shar(0); a.add_rr(5, 0)                                     # v
            if j: clip(m, 0, 14, 6)
            a._emit(0x8100 | (3 << 4) | j)                                                   # mov.w r0,@(2j,r3)
            a.shar(0); a.mov_rm_rn(4, 5); a.sub(0, 5)                                        # x = bo - v / 2
        a.movl_st_disp(5, 16, 12)
        adv()
    loop(s2)
    m.pool_jump()
    # 3) the 4 damped combs, k on the stack; yk summed into +20 (even k) or +24 (odd k): offset in r13
    a.mov_imm(0, 0); a.movl_st_dec(0, 15)
    ck = m.newlabel('ck'); a.deflabel(ck)
    bases(); a.mov_imm(-15, 9)
    a.movl_ld(15, 0); a.shll2(0); a.mov_rm_rn(0, 3); a.add_rr(10, 3); a.movl_ld_disp(3, C.R_L0, 3)   # r3 = Lk
    a.mov_rm_rn(0, 6); a.add_rr(11, 6); a.movl_ld_disp(6, C.Q_F0, 6)                         # r6 = F
    a.shll8(0); a.shll2(0); m.imm32(C.REV_OFF + C.RLN * 2, 2); a.add_rr(0, 2); a.add_rr(13, 2)   # r2 = line 1 + k
    a.movl_ld_disp(10, C.R_DAMP, 4); a.movl_ld_disp(10, C.R_FB, 5)
    a.movl_ld(15, 0); a.and_imm(1); a.shll2(0); a.add_imm(20, 0); a.mov_rm_rn(0, 13)         # r13 = 20 / 24
    def s3():
        rd_line(3, 10)                                                                        # r10 = yk
        a.mov_rm_rn(10, 0); a.sub(6, 0); a.mul_l(4, 0); a.sts_macl(0); a.shad(9, 0); a.add_rr(0, 6)    # filter
        a.mov_rm_rn(6, 0); a.mul_l(5, 0); a.sts_macl(0); a.shad(9, 0); a.movl_ld_disp(12, 16, 11); a.add_rr(11, 0)
        clip(m, 0, 14, 11); wr_line(0, 11)
        a.mov_rm_rn(13, 0); a._emit(0x000E | (11 << 8) | (12 << 4))                          # mov.l @(r0,r12),r11
        a.add_rr(10, 11); a._emit(0x0006 | (12 << 8) | (11 << 4))                            # mov.l r11,@(r0,r12)
        adv()
    loop(s3)
    bases(); a.movl_ld(15, 0); a.shll2(0); a.add_rr(11, 0); a.movl_st_disp(6, C.Q_F0, 0)
    a.movl_ld(15, 0); a.add_imm(1, 0); a.movl_st(0, 15); a.mov_imm(4, 1); a.cmpeq(1, 0); m.bf_far(ck)
    a.add_imm(4, 15)
    m.pool_jump()
    # 4) the stereo allpasses (g 1/2), one pass: u = even + odd (L) / even - odd (R); o = bo - u -> +28 / +24,
    #    line = u + bo / 2 (APL line in r2, length r3; APR line in r9, length r13)
    bases(); m.imm32(C.REV_OFF + 6 * C.RLN * 2, 9); a.add_rr(13, 9); line(2, 5)
    a.movl_ld_disp(10, C.R_APL, 3); a.movl_ld_disp(10, C.R_APR, 13)
    def s4():
        for base, ln_, off in ((2, 3, 28), (9, 13, 24)):
            rd_line(ln_, 4, base)                                                             # r4 = bo
            a.movl_ld_disp(12, 20, 5); a.movl_ld_disp(12, 24, 0)
            if off == 24: a.sub(0, 5)
            else: a.add_rr(0, 5)
            a.mov_rm_rn(4, 6); a.sub(5, 6)
            a.mov_rm_rn(4, 0); a.shar(0); a.add_rr(5, 0); clip(m, 0, 14, 10); wr_line(0, 10, base)
            a.movl_st_disp(6, off, 12)
        adv()
    loop(s4)
    m.pool_jump()
    bases(); a.add_rr(8, 1); a.and_rr(7, 1); a.movl_st_disp(1, C.Q_RW, 11)
    # 5) output gain (x MIX), low cut, high cut -> wet L +16, wet R +20; both channels in one pass
    #    (states: HP L r6, LP L r1, HP R r7, LP R r2; temp r13)
    a.movl_ld_disp(10, C.R_OGM, 3); a.movl_ld_disp(10, C.R_CHP, 4); a.movl_ld_disp(10, C.R_CLP, 5)
    a.movl_ld_disp(11, C.Q_HPL, 6); a.movl_ld_disp(11, C.Q_LPL, 1); a.movl_ld_disp(11, C.Q_HPR, 7)
    a.movl_ld_disp(11, C.Q_LPR, 2); a.mov_imm(-12, 10); a.mov_imm(-15, 9)
    def s5():
        for off, HP, LP, wo in ((28, 6, 1, 16), (24, 7, 2, 20)):
            a.movl_ld_disp(12, off, 0); a.shar(0); clip(m, 0, 14, 13); a.mul_l(3, 0); a.sts_macl(0); a.shad(10, 0)
            clip(m, 0, 14, 13)
            a.mov_rm_rn(0, 13); a.sub(HP, 13); a.mul_l(4, 13); a.sts_macl(13); a.shad(9, 13); a.add_rr(13, HP); a.sub(HP, 0)
            a.mov_rm_rn(0, 13); a.sub(LP, 13); a.mul_l(5, 13); a.sts_macl(13); a.shad(9, 13); a.add_rr(13, LP)
            a.movl_st_disp(LP, wo, 12)
    loop(s5)
    a.movl_st_disp(6, C.Q_HPL, 11); a.movl_st_disp(1, C.Q_LPL, 11); a.movl_st_disp(7, C.Q_HPR, 11)
    a.movl_st_disp(2, C.Q_LPR, 11)
    m.pool_jump()
    # 6) back to 44.1 kHz: frame 0 += (previous + wet) / 2, frame 1 += wet (y clipped; the wet totals too)
    bases(); a.movl_ld_disp(11, C.Q_PVL, 3); a.movl_ld_disp(11, C.Q_PVR, 4)
    def s6():
        for wo, PV, yo, eo in ((16, 3, 0, 8), (20, 4, 4, 12)):
            a.movl_ld_disp(12, wo, 5); a.mov_rm_rn(5, 0); a.add_rr(PV, 0); a.shar(0); a.mov_rm_rn(5, PV)
            for fo, v in ((0, 0), (32, 5)):
                a.movl_ld_disp(12, fo + yo, 6); a.add_rr(v, 6); clip(m, 6, 14, 13); a.movl_st_disp(6, fo + yo, 12)
                a.movl_ld_disp(12, fo + eo, 6); a.add_rr(v, 6); a.movl_st_disp(6, fo + eo, 12)
    loop(s6)
    a.movl_st_disp(3, C.Q_PVL, 11); a.movl_st_disp(4, C.Q_PVR, 11)
    m.pool_jump()
    a.deflabel(rx)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    a.deflabel('ixr_b')
    ld(m, IXBP, 13); a.mov_rm_rn(13, 10); a.add_imm(0x40, 10); a.movl_ld_disp(10, C.S_N, 8); a.shlr(8)
    m.imm32(C.RB_OFF, 10); a.add_rr(13, 10); a.mov_rm_rn(10, 11); a.add_imm(0x40, 11)
    m.imm32(C.FA_OFF, 12); a.add_rr(13, 12); a.rts(); a.movl_ld_disp(11, C.Q_RW, 1)
    m.pool_jump()

def ix_mod_code(m):
    """INPUT FX modulation v2 (2026-11-05), bit-exact with infx.Model.mod / mod_block:
      ix_mod     per frame from ix_proc's loop: x in r6 r7 -> y (chorus / phaser, one mono chain); keeps r8-r14
      ix_modblk  once per ix_proc call: the phaser's allpass coefficients from the lfo; keeps r12 r13
      ixm_shape  r0 = lfo phase (Q32) -> r0 = lfo Q16 (triangle / smoothstep); r10 = coefficient block; r1-r3"""
    a = m.a; C = IX
    # ---- ixm_shape ----
    a.deflabel('ixm_shape')
    tp, sd = m.newlabel('tp'), m.newlabel('sd')
    a.shlr16(0); a.mov_rm_rn(0, 1); a.shll16(1); a.cmppz(1); a.bt_lbl(tp); a.not_rr(0, 0); a.extu_w(0, 0)
    a.deflabel(tp); a.shll(0)
    a.movl_ld_disp(10, C.M_SHAPE, 1); a.tst_rr(1, 1); a.bt_lbl(sd)
    a.shlr(0); a.mov_rm_rn(0, 1); a.mul_l(0, 1); a.sts_macl(1); a.mov_imm(-15, 2); a.shad(2, 1)            # t2 (Q15)
    m.imm32(98304, 3); a.sub(0, 3); a.sub(0, 3); a.mul_l(3, 1); a.sts_macl(1); a.shad(2, 1); a.shll(1); a.mov_rm_rn(1, 0)
    a.deflabel(sd); a.rts(); a.nop()
    m.pool_jump()

    def push():
        a.stsl_pr()
        for r in (8, 9, 10, 11): a.movl_st_dec(r, 15)
    def pop():
        for r in (11, 10, 9, 8): a.movl_ld_inc(15, r)
        a.ldsl_pr()
    def mixout(rx):
        """rx = c16(rx + ((r0 - rx) * MIX >> 12)); r0 r1"""
        a.sub(rx, 0); a.movl_ld_disp(10, C.M_MIX, 1); a.mul_l(1, 0); a.sts_macl(0); a.mov_imm(-12, 1); a.shad(1, 0)
        a.add_rr(0, rx); clip(m, rx, 14, 1)
    def round8(r):
        """r = (r + 128) >> 8 (24 -> 16 bit, rounded). r1"""
        a.add_imm(127, r); a.add_imm(1, r); a.mov_imm(-8, 1); a.shad(1, r)
    def lline(idx, k, dst):
        """dst = line[(idx + k) & mask] (r8 = line, r4 = mask). r1"""
        a.mov_rm_rn(idx, 1)
        if k: a.add_imm(k, 1); a.and_rr(4, 1)
        a.shll(1); a.add_rr(8, 1); a.movw_ld(1, dst)

    # ---- ix_mod ----
    a.deflabel('ix_mod')
    ret, out, ph = m.newlabel('mret'), m.newlabel('mout'), m.newlabel('mph')
    a.mov_rm_rn(13, 0); a.add_imm(0x40, 0); a.add_imm(0x40, 0); a.movl_ld(0, 1); a.tst_rr(1, 1); m.bt_far(ret)
    a.movl_ld_disp(13, C.C_KILL, 2); a.tst_rr(2, 2); m.bf_far(ret)
    push()
    a.mov_rm_rn(0, 10); a.mov_rm_rn(10, 11); a.add_imm(0x40, 11); a.add_imm(0x40, 11)        # r10 = MB, r11 = MS
    a.movl_ld_disp(11, C.T_PH, 0); a.movl_ld_disp(10, C.M_INC, 2); a.add_rr(2, 0); a.movl_st_disp(0, C.T_PH, 11)
    a.mov_imm(2, 2); a.cmpeq(2, 1); m.bt_far(ph)
    # chorus: line[W] = (L + R) / 2; one tap at d = CD + DEP (lfo - 1/2) (Q16 frames), 4-point Hermite, 24-bit low-pass
    m.imm32(C.MLINE_OFF, 8); a.add_rr(13, 8); m.imm32(C.MMSK, 4)                              # r8 = line, r4 = mask
    a.movl_ld_disp(11, C.T_W, 2); a.mov_rm_rn(6, 3); a.add_rr(7, 3); a.shar(3)
    a.mov_rm_rn(2, 5); a.shll(5); a.add_rr(8, 5); a.movw_st(3, 5)
    a.bsr_lbl('ixm_shape'); a.nop()                                                          # r0 = lfo (phase in r0)
    m.imm32(-32768, 1); a.add_rr(1, 0); a.shll(0)
    a.movl_ld_disp(10, C.M_P2, 5); mul16(m, 5, 0); a.movl_ld_disp(10, C.M_P1, 1); a.add_rr(1, 2)   # r2 = d
    a.mov_rm_rn(2, 9); a.extu_w(9, 9); a.shlr16(2)                                           # r9 = u, r2 = whole
    a.movl_ld_disp(11, C.T_W, 0); a.sub(2, 0); a.and_rr(4, 0)                                # r0 = i0
    lline(0, 0, 3); lline(0, -1, 5); lline(0, -2, 6); lline(0, 1, 2)                        # p0 r3, p1 r5, p2 r6, pm1 r2
    a.mov_rm_rn(5, 7); a.sub(2, 7); a.shar(7)                                                # r7 = c1
    a.mov_rm_rn(3, 1); a.sub(5, 1); a.mov_rm_rn(1, 4); a.shll(1); a.add_rr(4, 1); a.shar(1)
    a.mov_rm_rn(6, 4); a.sub(2, 4); a.shar(4); a.add_rr(4, 1)                                # r1 = c3
    a.mov_rm_rn(3, 4); a.shll2(4); a.add_rr(3, 4); a.shar(4); a.mov_rm_rn(2, 0); a.sub(4, 0)
    a.mov_rm_rn(5, 4); a.shll(4); a.add_rr(4, 0); a.shar(6); a.sub(6, 0)                     # r0 = c2
    mul16(m, 9, 1); a.add_rr(0, 2); mul16(m, 9, 2); a.add_rr(7, 2); mul16(m, 9, 2); a.add_rr(3, 2)   # r2 = v
    a.shll8(2); a.movl_ld_disp(11, C.T_LP, 0); a.sub(0, 2); a.movl_ld_disp(10, C.M_P3, 5); mul16(m, 5, 2)
    a.add_rr(2, 0); a.movl_st_disp(0, C.T_LP, 11)
    round8(0); a.mov_rm_rn(0, 5)                                                             # r5 = wet
    m.imm32(C.MMSK, 4); a.movl_ld_disp(11, C.T_W, 0); a.add_imm(1, 0); a.and_rr(4, 0); a.movl_st_disp(0, C.T_W, 11)
    a.movl_pool('P1:' + out, 2); a.jmp(2); a.nop()                                           # ISR: P1 address
    m.pool_jump()
    # phaser: (L+R) << 7 + FB * last output -> the allpass chain (24 bit), unrolled; 4-stage presets enter at stage 2
    #   stage k: y = Z[k] + a_k (x - Z[k+1]), Z[k] = x  (r8 = Z[k] old, r4 = Z[k+1] old, r0 = x, r3 = &Z)
    a.deflabel(ph)
    a.mov_rm_rn(11, 3); a.add_imm(C.T_Z, 3)
    a.mov_rm_rn(6, 0); a.add_rr(7, 0); a.shll8(0); a.shar(0)
    a.movl_ld_disp(3, 24, 5); a.movl_ld_disp(10, C.M_FB, 4); mul16(m, 4, 5); a.add_rr(2, 0)
    m.imm32(C.X24, 4); clip(m, 0, 4, 1)                                                      # r0 = x
    e4, s2 = m.newlabel('e4'), m.newlabel('s2')
    a.movl_ld_disp(10, C.M_P1, 1); a.mov_imm(4, 5); a.cmpeq(5, 1); a.bt_lbl(e4)
    a.movl_ld_disp(3, 0, 8)
    for k in range(6):
        if k == 2:
            a.bra_lbl(s2); a.nop()
            a.deflabel(e4); a.movl_ld_disp(3, 8, 8)
            a.deflabel(s2)
        a.movl_ld_disp(3, 4 * (k + 1), 4); a.movl_ld_disp(3, C.T_A - C.T_Z + 4 * k, 1)
        a.mov_rm_rn(0, 5); a.sub(4, 5); mul16(m, 1, 5); a.add_rr(8, 2)
        a.movl_st_disp(0, 4 * k, 3); a.mov_rm_rn(4, 8); a.mov_rm_rn(2, 0)
    a.movl_st_disp(0, 24, 3)
    round8(0); a.mov_rm_rn(0, 5)                                                             # r5 = wet
    # both: L = x + (wet - x) MIX, R the same with the wet inverted when INV
    a.deflabel(out)
    a.movl_ld_disp(12, C.S_FAP, 1); a.movl_ld_disp(1, 16, 6); a.movl_ld_disp(1, 20, 7)        # x back (frame array)
    a.mov_rm_rn(5, 0); mixout(6)
    nn = m.newlabel('nn')
    a.mov_rm_rn(5, 0); a.movl_ld_disp(10, C.M_INV, 1); a.tst_rr(1, 1); a.bt_lbl(nn); a.neg(0, 0); a.deflabel(nn)
    mixout(7)
    pop()
    a.deflabel(ret); a.rts(); a.nop()
    m.pool_jump()

    # ---- ix_modblk (mono: one coefficient set) ----
    a.deflabel('ix_modblk')
    bret = m.newlabel('bret')
    a.mov_rm_rn(13, 0); a.add_imm(0x40, 0); a.add_imm(0x40, 0); a.movl_ld(0, 1); a.mov_imm(2, 2); a.cmpeq(2, 1)
    m.bf_far(bret)
    a.movl_ld_disp(13, C.C_KILL, 1); a.tst_rr(1, 1); m.bf_far(bret)
    push()
    a.mov_rm_rn(0, 10); a.mov_rm_rn(10, 11); a.add_imm(0x40, 11); a.add_imm(0x40, 11)
    a.movl_ld_disp(11, C.T_PH, 0)
    a.bsr_lbl('ixm_shape'); a.nop()
    a.movl_ld_disp(10, C.M_P3, 1); a.mul_l(1, 0); a.sts_macl(0); a.shlr16(0); a.movl_ld_disp(10, C.M_P2, 1)
    a.add_rr(1, 0); a.mov_rm_rn(0, 6)                                                           # r6 = sweep position
    a.movl_ld_disp(10, C.M_P1, 5)
    a.mov_imm(6, 7); a.sub(5, 7); a.shll2(7); a.add_rr(11, 7); a.add_imm(C.T_A, 7)             # r7 = &A[6 - N]
    a.mov_rm_rn(10, 8); a.add_imm(C.M_OFFS, 8)
    a.movl_pool('P1:ix_apt', 9); m.imm32(C.IMAX, 4)
    bl_ = m.newlabel('bl'); a.deflabel(bl_)
    c1, c2 = m.newlabel('c1'), m.newlabel('c2')
    a.movl_ld_inc(8, 1); a.mov_rm_rn(6, 0); a.add_rr(1, 0)
    a.cmppz(0); a.bt_lbl(c1); a.mov_imm(0, 0); a.deflabel(c1)
    a.cmpgt(4, 0); a.bf_lbl(c2); a.mov_rm_rn(4, 0); a.deflabel(c2)
    a.mov_rm_rn(0, 2); a.shlr8(2); a.shll2(2); a.add_rr(9, 2); a.movl_ld_inc(2, 3); a.movl_ld(2, 1); a.sub(3, 1)
    a.and_imm(255); a.mul_l(0, 1); a.sts_macl(1); a.mov_imm(-8, 2); a.shad(2, 1); a.add_rr(3, 1)
    a.movl_st(1, 7); a.add_imm(4, 7)
    a.dt(5); a.bf_lbl(bl_)
    pop()
    a.deflabel(bret); a.rts(); a.nop()
    m.pool_jump()

# ================= the effect (ISR, P1, coefficients in CFR via r13) =================
def fx_master(m):
    a = m.a
    m.a.movl_pool(_p1(m, CFR[0]), 13)
    def c(k, r): a.movl_ld_disp(13, 4 * k, r)
    # ---- EQ, channel by channel (one routine: r12 = &frame[0].field, r14 = states) ----
    for fo, s0 in ((FW, 0), (FWR, 6)):
        ld(m, ARRP, 12); a.add_imm(fo, 12); m.a.movl_pool(_p1(m, ST[s0]), 14)
        a.movl_pool('P1:ms_eqch', 2); a.jsr(2); a.nop()                                     # far (ISR: P1 address)
    m.pool_jump()
    # ---- dbx: mean square of (L+R)/2 over the block ----
    ld(m, ARRP, 12); a.mov_imm(BLK, 4); a.mov_imm(0, 7)
    m.pool_jump(); l = m.newlabel('ms'); a.deflabel(l)
    a.movl_ld_disp(12, FW, 0); a.movl_ld_disp(12, FWR, 1); a.add_rr(1, 0); a.shar(0); a.shar(0); a.shar(0); a.shar(0)   # (L+R)/16
    a.mul_l(0, 0); a.sts_macl(0); sar(m, 0, 5, 1); a.add_rr(0, 7)
    a.add_imm(FSTR, 12); a.dt(4); m.bf_far(l)
    # P += (MS - P) * CB
    ld(m, ST[12], 5); a.mov_rm_rn(7, 0); a.sub(5, 0); m.imm32(CB, 3); mul16(m, 3, 0); a.add_rr(2, 5); st(m, 5, ST[12])
    # L = log2(P) Q8 (P <= 0: 0)
    a.mov_imm(0, 6); a.cmppl(5); lz = m.newlabel('lz'); m.bf_far(lz)
    a.mov_imm(31, 6); a.mov_rm_rn(5, 0)
    nl2 = m.newlabel('n2'); nd2 = m.newlabel('d2'); a.deflabel(nl2)
    a.cmppz(0); a.bf_lbl(nd2); a.shll(0); a.bra_lbl(nl2); a.add_imm(-1, 6)
    a.deflabel(nd2)
    a.shll(0); a.shlr16(0); a.shlr8(0); a.shlr2(0); a.shlr(0); a.and_imm(31)            # 5 bits after the top bit
    a.shll2(0); m.a.movl_pool('P1:ms_tabs', 1); m.imm32(TOFF['LOG2'], 2); a.add_rr(2, 1); a.add_rr(0, 1); a.movl_ld(1, 0)
    a.shll8(6); a.add_rr(0, 6)
    a.deflabel(lz)
    # over = L - T; GR (log2 power Q8)
    c(C_T, 0); a.sub(0, 6); a.mov_imm(0, 7)                                             # r6 = over, r7 = GR
    c(C_SL, 3)
    soft, gd = m.newlabel('soft'), m.newlabel('gd')
    c(C_KN, 0); a.tst_rr(0, 0); m.bf_far(soft)
    a.cmppl(6); m.bf_far(gd); a.mov_rm_rn(6, 7); a.mul_l(3, 7); a.sts_macl(7); sar(m, 7, 12, 1); a.bra_lbl(gd); a.nop()
    a.deflabel(soft)
    m.imm32(-KNEE_H, 0); a.cmpgt(0, 6); m.bf_far(gd)                                     # over <= -W/2: 0
    m.imm32(KNEE_H, 0); a.cmpge(0, 6); hk_ = m.newlabel('hk'); m.bf_far(hk_)
    a.mov_rm_rn(6, 7); a.mul_l(3, 7); a.sts_macl(7); sar(m, 7, 12, 1); a.bra_lbl(gd); a.nop()       # over >= W/2: slope
    a.deflabel(hk_)
    m.imm32(KNEE_H, 0); a.add_rr(6, 0); a.mul_l(0, 0); a.sts_macl(0)                      # (over + W/2)^2
    m.imm32(R2W, 1); a.mul_l(1, 0); a.sts_macl(0); a.shlr16(0)                          # / 2W
    a.mul_l(3, 0); a.sts_macl(7); sar(m, 7, 12, 1)
    a.deflabel(gd)
    st(m, 7, GRM)
    # gain (log2 amplitude Q8) = OUT - GR/2 -> linear Q12 = EXP2[frac] * 2^(int - 3)
    c(C_OUT, 0); a.mov_rm_rn(7, 1); a.shar(1); a.sub(1, 0)
    a.mov_rm_rn(0, 1); a.and_imm(252); m.a.movl_pool('P1:ms_tabs', 2); m.imm32(TOFF['EXP2'], 3); a.add_rr(3, 2); a.add_rr(0, 2)
    a.movl_ld(2, 0)
    a.mov_imm(-8, 2); a.shad(2, 1); a.add_imm(-3, 1); a.shad(1, 0)                      # r0 = g (Q12)
    # ramp from Gp to g across the block
    ld(m, ST[13], 8); a.tst_rr(8, 8); g0 = m.newlabel('g0'); a.bf_lbl(g0); a.mov_rm_rn(0, 8); a.deflabel(g0)
    st(m, 0, ST[13]); a.mov_rm_rn(0, 9); a.sub(8, 9)                                    # r8 = Gp, r9 = g - Gp
    ld(m, ARRP, 12); a.mov_imm(BLK, 4); a.mov_imm(0, 10); m.imm32(32767, 14)
    m.pool_jump(); l = m.newlabel('ga'); a.deflabel(l)
    a.mov_rm_rn(9, 2); a.mul_l(10, 2); a.sts_macl(2); sar(m, 2, BSH, 0); a.add_rr(8, 2)
    for fo in (FW, FWR):
        a.movl_ld_disp(12, fo, 0); dmuls(m, 2, 0); a.sts_mach(1); a.sts_macl(0)                 # (x * g) >> 12, 64-bit
        a.shll16(1); a.shll2(1); a.shll2(1); a.shlr8(0); a.shlr2(0); a.shlr2(0); a.or_rr(1, 0)
        softlim(m, 0, 1); a.movl_st_disp(0, fo, 12)
    a.add_imm(1, 10); a.add_imm(FSTR, 12); a.dt(4); m.bf_far(l)


def eq_channel(m):
    """ms_eqch: the EQ of one channel over the block. r12 = &frame[0].field, r13 = coefficients, r14 = 6 states"""
    a = m.a
    fo = 0
    def c(k, r): a.movl_ld_disp(13, 4 * k, r)
    a.deflabel('ms_eqch')
    a.movl_st_dec(14, 15)
    for i, r in enumerate((5, 6, 7, 8, 9, 10)): a.movl_ld_disp(14, 4 * i, r)
    a.mov_imm(BLK, 4)
    m.pool_jump(); l = m.newlabel('eq'); a.deflabel(l)
    a.movl_ld_disp(12, fo, 0); a.mov_rm_rn(0, 11); a.shll8(11)                     # r11 = X (Q8)
    # low: y = X + B*LP2 - A*LP1
    nl = m.newlabel('nl'); c(C_B, 0); c(C_A, 1); a.or_rr(1, 0); a.tst_rr(0, 0); m.bt_far(nl)
    c(C_L2, 3)
    a.mov_rm_rn(11, 14); a.sub(5, 14); mul24(m, 3, 14); a.add_rr(2, 5)
    a.mov_rm_rn(5, 14); a.sub(6, 14); mul24(m, 3, 14); a.add_rr(2, 6)
    c(C_L1, 3); a.mov_rm_rn(11, 14); a.sub(7, 14); mul24(m, 3, 14); a.add_rr(2, 7)
    c(C_B, 3); mul16(m, 3, 6); a.add_rr(2, 11)
    c(C_A, 3); mul16(m, 3, 7); a.sub(2, 11)
    a.deflabel(nl)
    # high bell (ZDF SVF band-pass)
    nh = m.newlabel('nh'); c(C_KB, 0); a.tst_rr(0, 0); m.bt_far(nh)
    a.mov_rm_rn(11, 0); a.sub(9, 0)                                                 # r0 = v3
    c(C_A1, 3); mul16(m, 3, 8); a.mov_rm_rn(2, 14)
    c(C_A2, 3); mul16(m, 3, 0); a.add_rr(2, 14)                                     # r14 = v1
    c(C_A3, 3); mul16(m, 3, 0); a.mov_rm_rn(2, 0)
    c(C_A2, 3); mul16(m, 3, 8); a.add_rr(2, 0); a.add_rr(9, 0)                      # r0 = v2
    a.mov_rm_rn(14, 2); a.shll(2); a.sub(8, 2); a.mov_rm_rn(2, 8)                   # ic1 = 2 v1 - ic1
    a.mov_rm_rn(0, 2); a.shll(2); a.sub(9, 2); a.mov_rm_rn(2, 9)                    # ic2 = 2 v2 - ic2
    c(C_KB, 3); mul16(m, 3, 14); a.add_rr(2, 11)
    a.deflabel(nh)
    # high cut: y -= Ah * (y - LP1)
    nu = m.newlabel('nu'); c(C_AH, 0); a.tst_rr(0, 0); m.bt_far(nu)
    c(C_CH, 3); a.mov_rm_rn(11, 14); a.sub(10, 14); mul24(m, 3, 14); a.add_rr(2, 10)
    a.mov_rm_rn(11, 14); a.sub(10, 14); c(C_AH, 3); mul16(m, 3, 14); a.sub(2, 11)
    a.deflabel(nu)
    a.mov_rm_rn(11, 0); sar(m, 0, 8, 1); a.movl_st_disp(0, fo, 12)                      # (no clip: boosts keep their peaks)
    a.add_imm(FSTR, 12); a.dt(4); m.bf_far(l)
    a.movl_ld_inc(15, 0)
    for i, r in enumerate((5, 6, 7, 8, 9, 10)): a.movl_st_disp(r, 4 * i, 0)
    a.rts(); a.nop()
    m.pool_jump()

# ================= Python model (bit-exact with the ISR) =================
def s32(v): v &= M32; return v - (1 << 32) if v & 0x80000000 else v
def c16(v): return max(-32768, min(32767, v))
def soft(v):
    if v > LIMK: v = LIMK + ((v - LIMK) >> 2)
    elif v < -LIMK: v = -LIMK + ((v + LIMK) >> 2)
    return c16(v)
def m16(cf, x): return s32((x * cf) >> 16)
def m24(cf, x): return s32((x * cf) >> 24)
def coefs(v):
    """v: dict of the page choices -> the 15 coefficients (as the main loop writes them)"""
    tb = dict(TABS)
    f, w = v['hf'], v['hw']
    cf = [0] * NCF
    cf[C_L2], cf[C_L1], cf[C_B], cf[C_A] = T_LP2[v['lf']], T_LP1[v['lf']], T_B[v['lb']], T_A[v['la']]
    cf[C_A1], cf[C_A2], cf[C_A3] = T_SVF3[(f * 11 + w) * 3:(f * 11 + w) * 3 + 3]
    cf[C_KB] = s32((T_KQ[f * 11 + w] * T_BHQ[v['hb']]) >> 16)
    cf[C_CH], cf[C_AH] = T_CH[v['cf']], T_AH[v['ha']]
    cf[C_T], cf[C_SL], cf[C_KN], cf[C_OUT], cf[C_ON] = T_THR[v['thr']], T_SLOPE[v['rat']], v['knee'], T_OUT[v['out']], v['on']
    return [s32(x) for x in cf]

class Model:
    def __init__(self): self.S = [0] * 16; self.G = 0; self.cfr = None; self.grm = 0
    def fx(self, WL, WR, cf):
        S = self.S
        for W_, s0 in ((WL, 0), (WR, 6)):
            sa, sb, s1, ic1, ic2, h1 = S[s0:s0 + 6]
            for i in range(BLK):
                X = W_[i] << 8; y = X
                if cf[C_B] | cf[C_A]:
                    sa = s32(sa + m24(cf[C_L2], s32(y - sa))); sb = s32(sb + m24(cf[C_L2], s32(sa - sb)))
                    s1 = s32(s1 + m24(cf[C_L1], s32(X - s1)))
                    y = s32(y + m16(cf[C_B], sb)); y = s32(y - m16(cf[C_A], s1))
                if cf[C_KB]:
                    v3 = s32(y - ic2); v1 = s32(m16(cf[C_A1], ic1) + m16(cf[C_A2], v3))
                    v2 = s32(m16(cf[C_A3], v3) + m16(cf[C_A2], ic1) + ic2)
                    ic1 = s32(2 * v1 - ic1); ic2 = s32(2 * v2 - ic2)
                    y = s32(y + m16(cf[C_KB], v1))
                if cf[C_AH]:
                    h1 = s32(h1 + m24(cf[C_CH], s32(y - h1)))
                    y = s32(y - m16(cf[C_AH], s32(y - h1)))
                W_[i] = y >> 8
            S[s0:s0 + 6] = [sa, sb, s1, ic1, ic2, h1]
        ms = 0
        for i in range(BLK):
            s = (WL[i] + WR[i]) >> 4; ms = s32(ms + (s32(s * s) >> 5))
        P = s32(S[12] + m16(CB, s32(ms - S[12]))); S[12] = P
        if P > 0:
            n = P.bit_length() - 1; mant = ((P << (31 - n)) << 1 & M32) >> 27 & 31
            L = n * 256 + T_LOG2[mant]
        else: L = 0
        over = L - cf[C_T]; sl = cf[C_SL]
        if not cf[C_KN]: GR = (over * sl) >> 12 if over > 0 else 0
        elif over <= -KNEE_H: GR = 0
        elif over >= KNEE_H: GR = (over * sl) >> 12
        else:
            t = (s32((s32((over + KNEE_H) ** 2) * R2W)) & M32) >> 16
            GR = s32(t * sl) >> 12
        self.grm = GR
        u = cf[C_OUT] - (GR >> 1)
        e = T_EXP2[(u & 255) >> 2]; sh = (u >> 8) - 3
        g = s32(e << sh) if sh >= 0 else e >> -sh
        Gp = S[13] or g; S[13] = g
        for i in range(BLK):
            gi = Gp + (((g - Gp) * i) >> BSH)
            WL[i] = soft((WL[i] * gi) >> 12); WR[i] = soft((WR[i] * gi) >> 12)
    def block(self, L, R, act, cf):
        if cf is not None: self.cfr = list(cf)
        WL, WR = list(L), list(R)
        if self.cfr is not None: self.fx(WL, WR, self.cfr)
        gt = 4096 if act else 0; g0 = self.G; step = max(-GSTEP, min(GSTEP, gt - g0)); self.G = g0 + step
        oL, oR = [], []
        for i in range(BLK):
            g = g0 + ((step * i) >> BSH)
            oL.append(c16(L[i] + (((WL[i] - L[i]) * g) >> 12))); oR.append(c16(R[i] + (((WR[i] - R[i]) * g) >> 12)))
        return oL, oR


def selftest(out):
    import random
    from validate import Harness
    from shasm import SHAsm
    from pages import ENABLE_DOIT
    from mpcmod import OS_PUSH_PAGE
    # ---- the dbx 160 behaviour, measured on the model (the ISR is checked bit-exact against it below) ----
    flat = dict(lf=2, lb=0, la=0, hf=4, hw=5, hb=0, cf=1, ha=0, thr=THRS.index(-20), rat=RATIOS.index(4.0), knee=0, out=OUTS.index(0), on=1)
    def run_level(md, cfv, amp, nblk, f=1000):
        out_ = []
        for b in range(nblk):
            L = [int(amp * math.sin(2 * math.pi * f * (b * BLK + i) / FS)) for i in range(BLK)]
            WL, WR = list(L), list(L); md.fx(WL, WR, cfv); out_.append((md.grm / DBU, max(abs(x) for x in WL)))
        return out_
    def rms_db(a_): return 20 * math.log10(a_ / math.sqrt(2) / 8192)
    cfv = coefs(flat)
    for amp_db in (-10, 0):                                  # 4:1 above -20: output level rises 1 dB per 4 dB in
        amp = RMS0 * math.sqrt(2) * 10 ** (amp_db / 20); md = Model(); r = run_level(md, cfv, amp, 600)
        gr = r[-1][0]; want = (amp_db - (-20)) * (1 - 1 / 4)
        assert abs(gr - want) < 0.6, (amp_db, gr, want)
    md = Model(); r = run_level(md, coefs(dict(flat, rat=RATIOS.index(None))), RMS0 * math.sqrt(2) * 10 ** (10 / 20), 600)
    assert abs(r[-1][0] - 30) < 0.6, r[-1]
    print("  ok dbx static curve: 4:1 above -20 dB (GR %.1f dB at 0 dB in), INF:1 holds the level (GR %.1f dB for +30)" % (
        run_level(Model(), cfv, RMS0 * math.sqrt(2), 600)[-1][0], r[-1][0]))
    def attack_ms(jump):
        md = Model(); cf_ = coefs(dict(flat, thr=THRS.index(-40), rat=RATIOS.index(None)))
        lo = RMS0 * math.sqrt(2) * 10 ** (-40 / 20); run_level(md, cf_, lo, 400)
        r_ = run_level(md, cf_, lo * 10 ** (jump / 20), 200)
        tgt = jump * 0.63
        for b, (gr, _) in enumerate(r_):
            if gr >= tgt: return (b + 1) * BLK / FS * 1000
    at = [attack_ms(j) for j in (10, 20, 30)]
    assert 12 < at[0] < 21 and 3.5 < at[1] < 8.5 and 1.5 < at[2] < 5, at
    md = Model(); cf_ = coefs(dict(flat, thr=THRS.index(-40), rat=RATIOS.index(None)))
    run_level(md, cf_, RMS0 * math.sqrt(2) * 10 ** (-10 / 20), 600); r_ = run_level(md, cf_, 1, 300)
    t1 = next(b for b, (gr, _) in enumerate(r_) if gr < 25); t2 = next(b for b, (gr, _) in enumerate(r_) if gr < 5)
    rate = 20 / ((t2 - t1) * BLK / FS)
    assert 105 < rate < 145, rate
    print("  ok dbx dynamics (RMS detector, tau %.0f ms): attack to 63%% of a 10/20/30 dB jump in %.0f/%.0f/%.0f ms "
          "(dbx 160: ~15/5/3), release %.0f dB/s (dbx: 120)" % (TAU * 1000, at[0], at[1], at[2], rate))
    # knee: soft is smooth around the threshold, hard has none below it
    hard = run_level(Model(), coefs(dict(flat, knee=0)), RMS0 * math.sqrt(2) * 10 ** (-21 / 20), 600)[-1][0]
    softv = run_level(Model(), coefs(dict(flat, knee=1)), RMS0 * math.sqrt(2) * 10 ** (-21 / 20), 600)[-1][0]
    assert hard < 0.05 and 0.2 < softv < 1.5, (hard, softv)
    print("  ok knee 1 dB under the threshold: HARD %.2f dB, SOFT %.2f dB of reduction" % (hard, softv))
    # Pultec low: boost + atten on 60 Hz = bump below, dip above
    def resp(cfv_, f):
        md = Model(); amp = 2000
        for b in range(400):
            L = [int(amp * math.sin(2 * math.pi * f * (b * BLK + i) / FS)) for i in range(BLK)]
            WL, WR = list(L), list(L)
            md.S[12] = 0; md.S[13] = 0; cfv_ = list(cfv_); cfv_[C_T] = 1 << 30                # (compressor out of the way)
            md.fx(WL, WR, cfv_)
        return 20 * math.log10(max(abs(x) for x in WL) / amp)
    eqv = dict(flat, out=OUTS.index(0), thr=THRS.index(10))
    both = [resp(coefs(dict(eqv, lb=10, la=10)), f) for f in (30, 60, 200, 1000)]
    assert both[0] > 3 and both[2] < -1 and abs(both[3]) < 1, both
    print("  ok Pultec low 60 Hz boost 10 + atten 10: %+.1f dB at 30 Hz, %+.1f at 60, %+.1f at 200 (the dip), %+.1f at 1k" % tuple(both))
    hi = [resp(coefs(dict(eqv, hb=10, hw=5)), f) for f in (1000, 10000)]
    assert abs(hi[0]) < 1.5 and hi[1] > 12, hi
    print("  ok Pultec high 10 kHz boost 10: %+.1f dB at 1k, %+.1f dB at 10k" % tuple(hi))
    # v2: normalized, full-scale material with the defaults: no hard clipping (soft limit), and the compressor works
    dflt = coefs(dict(lf=2, lb=3, la=2, hf=4, hw=5, hb=3, cf=1, ha=0, thr=THRS.index(-3), rat=RATIOS.index(3.0), knee=1, out=OUTS.index(0), on=1))
    md = Model(); peak = 0; hard_n = 0; grs = []
    for b in range(400):
        L = [int(32767 * (0.7 * math.sin(2 * math.pi * 55 * (b * BLK + i) / FS) + 0.3 * math.sin(2 * math.pi * 3000 * (b * BLK + i) / FS))) for i in range(BLK)]
        WL, WR = list(L), list(L); md.fx(WL, WR, dflt)
        if b > 100: peak = max(peak, max(abs(x) for x in WL)); hard_n += sum(1 for x in WL if abs(x) >= 32767); grs.append(md.grm / DBU)
    assert hard_n == 0 and peak < 32767, (hard_n, peak)
    print("  ok full-scale (normalized) input, defaults: never hits the ceiling (peak %d), compressor GR %.1f dB" % (peak, sum(grs) / len(grs)))

    # ---- the add-on in the emulator (hook, interrupts, bit-exact with the model) ----
    h = Harness(os.path.join(os.path.dirname(__file__), '..', 'mpc500_loader.bin')); h.fill_scratch(); e = h.emu
    def stub(addr, fn):
        s_ = SHAsm(addr); fn(s_); blob = s_.assemble()
        for i in range(0, len(blob), 2): e.wr(addr + i, struct.unpack('<H', blob[i:i+2])[0], 2)
    for ad in (OS_PUSH_PAGE, ENABLE_DOIT): stub(ad, lambda s_: (s_.rts(), s_.nop()))
    stub(SET_FOCUS, lambda s_: (s_.movl_st_disp(5, 0x24, 4), s_.rts(), s_.nop()))
    TEMPO = {'bpm10': 1200}
    def tempo_stub(s_):                                               # SYS_PARAM: msg+4 = tempo x 10 (from TEMPO_VAR)
        s_.movl_pool(0x88E30000, 2); s_.movl_ld(2, 2); s_.movl_st_disp(2, 4, 4); s_.mov_imm(1, 0); s_.rts(); s_.nop()
    stub(SYS_PARAM, tempo_stub); h.ram_w32(0x88E30000, TEMPO['bpm10'])
    for k, w in enumerate(TXORIG): h.ram_w16(TXPATCH + 2 * k, w)
    TC = {'v': 0x7FFFFF, 'n': 0, 'slow': False}
    def tcnt(a_):
        TC['n'] += 1; TC['v'] -= (1000 if TC['slow'] and TC['n'] % 2 == 0 else 10); return TC['v'] & 0xFFFFFF
    e.mmio_r[TCNT1] = tcnt; e.mmio_r[TCOR1] = lambda a_: 0xFFFFFF
    h.ram_w32(EXITLIT, ISR_EXIT)
    h.load_hub(); h.load_wav(out); h.fire(10)
    R32, W32 = h.ram_r32, h.ram_w32
    WT = lambda F: W(F) - (pg.addr['page'] - pg.page)
    patched = lambda: [e.rd(TXPATCH + 2 * k, 2) for k in range(16)]
    OTHER = 0x88123456
    W32(EVQ + 0x68, OTHER); h.fire(3)
    assert patched() == TXORIG and R32(HOOKED) == 0 and e.rd(ONF, 1) == 0, "OFF by default: nothing hooked"
    e.wr(ONF, 1, 1); h.fire(3)
    pw = patched(); txent = pw[5] | (pw[6] << 16); trig = R32(EXITLIT)
    assert pw[:5] == [0xD002, 0x400B, 0x0009, 0xA00B, 0x0009] and R32(HOOKED) == 1 and trig != ISR_EXIT, [hex(x) for x in pw]
    print("  ok OFF by default (nothing hooked); switched ON: hooked, also with the page closed (TX %08x -> ms_tx %08x, ISR exit -> ms_trig %08x)" % (TXPATCH, txent, trig))
    W32(EVQ + 0x68, pg.page); h.fire(3)
    txt = lambda k, w: bytes(e.rd(pg.dlabels[k] + i, 1) for i in range(w)).decode('latin-1')
    rnd = random.Random(5); md = Model(); FR = [0x7C0 * 32]; hist = {}; inputs = {}; mlast = [None]; stepsmax = [0]
    e.logrange = (0xA40000E0, 0xA40000E4)
    def tick(longs):
        F = FR[0]; p = OUT_BASE + (F * 8) % OUT_SIZE
        for k in range(8): W32(p + 4 * k, longs[k])
        W32(FRAMES, F)
        regs = [0x33330000 + i for i in range(16)]; regs[15] = 0x88400000 - 256; regs[1] = FRAMES; regs[2] = p
        e.r = regs[:]; e.wlog = []; e.gbr = 0xA4000080; e.maxsteps = 200_000; e.pc = TXPATCH; e.pr = 0x12345678
        e.forbid = (0xA8F18000, 0xA8F58000)                           # the ISR never runs from the uncached alias
        assert e.run(stop_pc=TXPATCH + 0x20), "TX does not return"
        for i in range(3, 16): assert e.r[i] == regs[i], "TX: r%d" % i
        fifo = [v for pc_, a_, v, sz in e.wlog if a_ == 0xA40000E0 and sz == 4]; assert len(fifo) == 8
        FR[0] = F + 4; W32(FRAMES, FR[0])
        regs = [0x44440000 + i for i in range(16)]; regs[15] = 0x88400000 - 256
        e.srv = 0x20000000 if (F // 32) % 2 else 0
        e.r = regs[:]; e.maxsteps = 3_000_000; e.pc = trig; e.pr = 0x12345678
        assert e.run(stop_pc=ISR_EXIT), "trig does not reach the exit"
        e.forbid = None
        for i in range(8, 16): assert e.r[i] == regs[i], "proc: r%d" % i
        return fifo, e.steps
    def pack(el, er, lows): return [((el[jj] << SH) & M32) | lows[2 * jj] if kk == 0 else ((er[jj] << SH) & M32) | lows[2 * jj + 1] for jj in range(BLK) for kk in (0, 1)]
    ctx = {'act': 1, 'cf': None}
    def block(L, R, check=True):
        b = FR[0] // 32; lows = [rnd.randrange(1 << SH) for _ in range(2 * BLK)]
        longs = []
        for jj in range(BLK): longs += [((L[jj] << SH) & M32) | lows[2 * jj], ((R[jj] << SH) & M32) | lows[2 * jj + 1]]
        inputs[b] = (L, R, lows); hist[b] = longs
        outl = []
        for k in range(8):
            f_, stp = tick(longs[8 * k:8 * k + 8]); outl += f_; stepsmax[0] = max(stepsmax[0], stp)
            jb = FR[0] // 32 - 1
            if (ctx['act'] or md.G) and jb != mlast[0]:                    # (a block before the test's first: silence)
                mlast[0] = jb; Lj, Rj, lj = inputs.get(jb, ([0] * BLK, [0] * BLK, [0] * (2 * BLK)))
                el, er = md.block(Lj, Rj, ctx['act'], [s32(R32(CF[i])) for i in range(NCF)])
                hist[jb] = pack(el, er, lj)
        if check and b - LAT in hist:
            if outl != hist[b - LAT]:
                bad = [q_ for q_ in range(2 * BLK) if outl[q_] != hist[b - LAT][q_]][:4]
                raise AssertionError("block %d: long %s emu=%s want=%s" % (b, bad, [hex(outl[q_]) for q_ in bad], [hex(hist[b - LAT][q_]) for q_ in bad]))
    def music(n, loud):
        for _ in range(n):
            t0 = FR[0]
            L = [int(loud * (0.6 * math.sin(2 * math.pi * 55 * (t0 + i) / FS) + 0.4 * rnd.uniform(-1, 1))) for i in range(BLK)]
            R = [int(loud * (0.6 * math.sin(2 * math.pi * 55 * (t0 + i) / FS + 1) + 0.4 * rnd.uniform(-1, 1))) for i in range(BLK)]
            block(L, R)
    music(LAT + 2, 3000)
    assert [s32(R32(CF[i])) for i in range(NCF)] == coefs(dict(lf=2, lb=3, la=2, hf=4, hw=5, hb=3, cf=1, ha=0, thr=THRS.index(-3),
                                                               rat=RATIOS.index(3.0), knee=1, out=OUTS.index(0), on=1)), ("defaults (after ON)", [(i, s32(R32(CF[i])), x) for i, x in enumerate(coefs(dict(lf=2, lb=3, la=2, hf=4, hw=5, hb=3, cf=1, ha=0, thr=THRS.index(-3), rat=RATIOS.index(3.0), knee=1, out=OUTS.index(0), on=1))) if s32(R32(CF[i])) != x])
    music(40, 9000); music(40, 32000)                                  # (normalized-level material)
    gr = R32(GRM); assert 0 < s32(gr), gr
    h.fire(2); row = txt(R1, 16); assert row.startswith("GR -") and "dB" in row, repr(row)
    print("  ok defaults (60 Hz 3/2, 10 kHz 3 width 5, THR -3, 3:1, SOFT, 0 dB): 80 blocks bit-exact with the model; meter %r" % row)
    # settings changed while playing: every page's values
    for fld, val in ((LFREQF, 0), (LBF, 10), (LAF, 10), (HFREQF, 6), (HBF, 7), (HWF, 0), (CFREQF, 0), (HAF, 6),
                     (THRF, THRS.index(-20)), (RATF, RATIOS.index(None)), (KNEEF, 0), (OUTF, OUTS.index(-6))):
        e.wr(fld, val, 1); h.fire(1); music(6, 12000)
    print("  ok every setting changed while playing: still bit-exact (cut 5 kHz, INF:1, HARD, -6 dB ...); max %d steps per interrupt" % stepsmax[0])
    # every page: the name, the THR label only on DBX 160, the fields of that page only
    vis = lambda F: e.rd(WT(F) + 0x17, 1) & 0x80 != 0
    for p in range(6):
        e.wr(PAGEF, p, 1); h.fire(2)
        shown = [F for F in VIS if vis(F)]
        assert shown == [F for F in VIS if p in VIS[F]], (p, shown)
        assert (e.rd(pg.dlabels[R0] - 0x28 + 0x17, 1) & 0x80 != 0) == (p == P_DBX), p
    e.wr(PAGEF, P_MAIN, 1); h.fire(2)
    print("  ok pages: each shows only its own settings; 'THR' only on DBX 160 ('MASTER FX' keeps its X)")
    # off: fades out, then unhooks; on again: hooks
    e.wr(ONF, 0, 1); h.fire(1); ctx['act'] = 0
    music(6, 8000); h.fire(2)
    assert patched() == TXORIG and R32(HOOKED) == 0 and R32(EXITLIT) == ISR_EXIT, "unhooked after the fade"
    assert txt(R1, 16) == "OFF             ", repr(txt(R1, 16))
    print("  ok OFF: faded out (bit-exact), then the OS code restored")
    # load guard
    e.wr(ONF, 1, 1); h.fire(2); ctx['act'] = 1; md.G = 0; md.S = [0] * 16; mlast[0] = None
    trig = R32(EXITLIT)
    for _ in range(LAT): block([0] * BLK, [0] * BLK, check=False)
    TC['slow'] = True; TC['n'] = 0
    for k in range(NWIN * (OVN - 1) + 3):
        block([rnd.randint(-9000, 9000) for _ in range(BLK)], [rnd.randint(-9000, 9000) for _ in range(BLK)], check=False)
    assert R32(KILL) == 0 and R32(OVC) == OVN - 1, ("one window too few: no overload yet", R32(OVC))
    for k in range(NWIN): block([rnd.randint(-9000, 9000) for _ in range(BLK)], [rnd.randint(-9000, 9000) for _ in range(BLK)], check=False)
    assert R32(KILL) == 1 and R32(G) == 0, "overload not caught"
    TC['slow'] = False; h.fire(1); assert txt(R1, 16) == "OVERLOAD - OFF? ", repr(txt(R1, 16))
    e.wr(ONF, 0, 1); h.fire(1); e.wr(ONF, 1, 1); h.fire(1); assert R32(KILL) == 0
    print("  ok load guard: > %d%% of the time for %d windows in a row -> passthrough + 'OVERLOAD - OFF?'; off/on clears it"
          % (LOADMAX, OVN))
    # Master FX (or anything) already patched the OS: no hook
    e.wr(ONF, 0, 1); h.fire(1); md.G = 0; ctx['act'] = 0
    for _ in range(8): block([0] * BLK, [0] * BLK, check=False)
    h.fire(2); assert R32(HOOKED) == 0
    h.ram_w16(TXPATCH + 6, 0x1234); e.wr(ONF, 1, 1); h.fire(3); assert R32(HOOKED) == 0 and patched()[3] == 0x1234
    for k, w in enumerate(TXORIG): h.ram_w16(TXPATCH + 2 * k, w)
    print("  ok OS code already changed (e.g. Master FX hooked): no hook")
    selftest_infx(h, e, tick, FR, TEMPO)
    print("OK: MASTER -> %s" % os.path.basename(out))


def selftest_infx(h, e, tick, FR, TEMPO):
    """INPUT FX in the emulator: sampler literals, in-place processing bit-exact with infx.Model (delay OR reverb),
    monitor queue, SOURCE MAIN, start / stop, the page, the cost"""
    import random
    R32, W32 = h.ram_r32, h.ram_w32
    B = 0x88F00000                                                    # loader v10: MGR[1] = B, signatures, FX buffer
    W32(0x8848ED30 + 4, B); W32(B + 0x40, SCRATCH_MAGIC); W32(B + 0x44, SIG2_); W32(B + FXMARK_OFF, RESERVE)
    IXB = B + FXBUF_OFF + IX.IX_OFF
    for lit in IX.SMP_LITS: W32(lit, IX.IN_WR)
    W32(IX.IN_WR, IX.RING + 8 * 100)
    from shasm import SHAsm
    def stub(addr, fn):
        s_ = SHAsm(addr); fn(s_); blob = s_.assemble()
        for i in range(0, len(blob), 2): e.wr(addr + i, struct.unpack('<H', blob[i:i+2])[0], 2)
    stub(IX.SMP_ENTER, lambda s_: (s_.mov_imm(1, 0), s_.movl_st_disp(0, 8, 4), s_.rts(), s_.nop()))   # entered = 1
    stub(IX.SMP_EXIT, lambda s_: (s_.mov_imm(0, 0), s_.movl_st_disp(0, 8, 4), s_.rts(), s_.nop()))
    W32(IX.SMP + 8, 0); W32(IX.SMP + 0x24, 0)
    md = IX.Model(); md.rv_init(); md.rvb_init()
    st_ = {'don': 0, 'gen': 0, 'kill': 0, 'rgen': 0}
    MB = IXB + IX.MB_OFF
    RB = IXB + IX.RB_OFF
    def fire(n=1):
        """fire, and clear the model's lines exactly when the add-on clears its own (DON on / a modulation or reverb
        preset load / the overload retry)"""
        h.fire(n)
        don, gen, kill, rgen = R32(IXB + IX.C_DON), R32(MB + IX.M_GEN), R32(KILL), R32(RB + IX.R_GEN)
        if (don and not st_['don']) or (st_['kill'] and not kill):
            md.dly = [0] * IX.DLY_N; md.s = dict(FLL=0, FLR=0, FHL=0, FHR=0, PH=0); md.dw = R32(IXB + IX.C_DW)
        if gen != st_['gen']: md.rv_init()
        if rgen != st_['rgen']: md.rvb_init()
        st_['don'], st_['gen'], st_['kill'], st_['rgen'] = don, gen, kill, rgen
    W32(EVQ + 0x68, 0x88123456); e.wr(ONF, 0, 1); fire(3)
    lits = lambda: [R32(l) for l in IX.SMP_LITS]
    assert R32(IXBP) == IXB & 0xDFFFFFFF and lits() == [IX.IN_WR] * 5 and R32(HOOKED) == 0, (hex(R32(IXBP)), [hex(x) for x in lits()])
    assert e.rd(INON, 1) == 0 and e.rd(DPF, 1) == 0 and e.rd(MPF, 1) == 0 and R32(MB + IX.M_ON) == 0
    print("  INPUT FX: off by default (sampler untouched); FX buffer at %08x; defaults: delay OFF, modulation OFF" % IXB)
    # ---- on: hooked, the sampler follows SPTR ----
    e.wr(INON, 1, 1); fire(3)
    assert R32(HOOKED) == 1 and R32(IXB + IX.C_RUN) == 1 and lits() == [IXB + IX.C_SPTR] * 5, [hex(x) for x in lits()]
    print("  ok ON: MASTER hooked for INPUT FX alone; the 5 sampler literals -> SPTR")
    rnd = random.Random(9); ys = []; outs = []; pend = []; STEPS = [0]; NSEEN = [0]
    def coefs():
        c = {k: R32(IXB + getattr(IX, 'C_' + k)) for k in ('DON', 'DT', 'FB', 'MIX', 'CLP', 'CHP', 'QM', 'PING', 'MODD', 'MODINC')}
        return {k: (v if k in ('QM', 'MODINC') else s32(v)) for k, v in c.items()}
    def rcoefs(): return [R32(IXB + 0x80 + 4 * k) for k in range(16)]
    def rbw(): return [R32(RB + 4 * k) for k in range(16)]
    def rx(frames):
        p = R32(IX.IN_WR)
        for l, r in frames:
            W32(p, (l & 0xFFFF) << 16 | rnd.randrange(1 << 16)); W32(p + 4, (r & 0xFFFF) << 16 | rnd.randrange(1 << 16)); p += 8
            if p == IX.RING_END: p = IX.RING
        W32(IX.IN_WR, p)
    def ringat(ad):
        if ad >= IX.RING_END: ad -= IX.RING_END - IX.RING
        return s32(R32(ad)) >> 16, s32(R32(ad + 4)) >> 16, R32(ad) & 0xFFFF, R32(ad + 4) & 0xFFFF
    def run(nblocks, gen, model=True):
        for b in range(nblocks):
            for k in range(8):
                fr = [gen() for _ in range(4)]; rx(fr); pend.extend(fr)
                sp0 = R32(IXB + IX.C_SPOS)
                fifo, stp = tick([0] * 8); STEPS[0] = max(STEPS[0], stp)
                outs.extend((s32(fifo[2 * i]) >> 16, s32(fifo[2 * i + 1]) >> 16) for i in range(4))
                n = ((R32(IXB + IX.C_SPOS) - sp0) & IX.RMASK) // 8
                NSEEN[0] = max(NSEEN[0], n)
                if n:
                    c = coefs(); rc = rcoefs(); rb = rbw(); md.mod_block(rc)
                    yb = md.block(c, rc, rb, [pend.pop(0) for _ in range(n)])
                    for j in range(n):
                        y = yb[j]; ys.append(y)
                        gl, gr, lo1, lo2 = ringat(sp0 + 8 * j)
                        assert (gl, gr) == y[:2], ("frame %d: ring %s model %s" % (len(ys), (gl, gr), y[:2]))
                        assert lo1 == 0 and lo2 == 0
                assert R32(IXB + IX.C_SPTR) == R32(IXB + IX.C_SPOS)
    tone = [0]
    def gen():
        tone[0] += 1; t = tone[0]
        return (int(12000 * math.sin(t * 0.05)) if t % 4000 < 600 else 0, rnd.randint(-3000, 3000))
    run(20, gen)
    assert all(y[2] == 0 and y[3] == 0 for y in ys), "both OFF: dry"
    e.wr(MPF, 1, 1); fire(2)
    assert R32(MB + IX.M_ON) == 1 and e.rd(MMIX, 1) == IX.pct_index(50) and e.rd(MRATE, 1) == IX.MTAB[0][2]
    assert R32(MB + IX.M_INC) == IX.minc(IX.MRATES[IX.MTAB[0][2]])
    run(200, gen)
    assert any(abs(y[2]) > 300 for y in ys[1000:]), "chorus wet"
    print("  ok JUNO I (MIX 50, RATE %s Hz): %d input frames processed in place, bit-exact with the per-frame model"
          % (IX.RATEN[IX.MTAB[0][2]], len(ys)))
    # the monitor: the output carries the processed input after a fixed latency
    yl = [y[0] for y in ys]; ol = [o[0] for o in outs]
    lat = None
    for L in range(0, 400):
        if all(ol[i + L] == yl[i] for i in range(2000, 5000)): lat = L; break
    assert lat is not None, "monitor not found in the output"
    print("  ok monitor: the player hears the processed input %d frames (%.1f ms) later" % (lat, lat / 44.1))
    # ---- one effect at a time (user 2026-11-05, stability): a delay preset switches the modulation OFF and back ----
    d16 = [p[0] for p in IX.DPRESETS].index("DDL 16BIT"); P1 = IX.DPRESETS[d16]           # (a 1/8 delay: echoes in the run)
    e.wr(DPF, d16 + 1, 1); fire(2)
    assert R32(IXB + IX.C_DON) == 1 and R32(MB + IX.M_ON) == 0 and e.rd(MPF, 1) == 0, "delay: MOD off"
    assert e.rd(DTM, 1) == P1[1] and e.rd(DFB, 1) == IX.pct_index(P1[2]) and e.rd(DMIX, 1) == IX.pct_index(P1[3])
    assert R32(IXB + IX.C_DT) == IX.delay_time(P1[1], 22050), R32(IXB + IX.C_DT)
    run(400, gen)
    assert any(abs(y[2]) > 1000 for y in ys[-1500:]), "echoes"
    e.wr(MPF, 5, 1); fire(2)
    assert R32(MB + IX.M_ON) == 2 and R32(IXB + IX.C_DON) == 0 and e.rd(DPF, 1) == 0 and e.rd(MMIX, 1) == IX.pct_index(50)
    run(60, gen)
    e.wr(MRATE, 15, 1); e.wr(MMIX, 18, 1); fire(2)
    assert R32(MB + IX.M_INC) == IX.minc(IX.MRATES[15]) and R32(MB + IX.M_MIX) == IX.pct_q12(18)
    run(40, gen)
    print("  ok one effect at a time: DDL 16BIT switched the MOD off (1/8 at 120 BPM = 11025 frames, echoes), "
          "EP PHASER switched the delay off; RATE / MIX changed while running: bit-exact")
    # ---- every preset, bit-exact ----
    e.wr(MPF, 0, 1); fire(2); assert R32(MB + IX.M_ON) == 0
    for pi in range(1, len(IX.DNAMES)):
        e.wr(DPF, pi, 1); fire(2); run(25, gen)
    e.wr(DPF, 0, 1)
    for pi in range(1, len(IX.MNAMES)):
        e.wr(MPF, pi, 1); fire(2)
        r_ = IX.MTAB[pi - 1]
        assert R32(MB + IX.M_ON) == r_[0] and e.rd(MMIX, 1) == r_[1] and e.rd(MRATE, 1) == r_[2]
        assert [R32(MB + 4 * k) for k in range(3, 15)] == r_[3:15]
        run(40, gen)
    e.wr(MPF, 0, 1); fire(2); run(8, gen)
    assert all(y[2] == 0 and y[3] == 0 for y in ys[-200:]), "both OFF: no wet"
    print("  ok all %d delays and %d modulations bit-exact (12 bit, ping pong, dub wow, Juno inverted side, 6-stage "
          "phaser, vibe ...); both OFF = dry" % (len(IX.DNAMES) - 1, len(IX.MNAMES) - 1))
    # ---- reverb (2026-11-06): one effect at a time, both presets bit-exact, MIX while running ----
    d16 = [p[0] for p in IX.DPRESETS].index("DDL 16BIT")
    e.wr(DPF, d16 + 1, 1); fire(2); assert R32(IXB + IX.C_DON) == 1
    e.wr(RPF, 1, 1); fire(2)
    assert e.rd(DPF, 1) == 0 and e.rd(MPF, 1) == 0 and R32(IXB + IX.C_DON) == 0, "reverb: delay and MOD off"
    assert e.rd(RMIX, 1) == IX.RTAB[0][0] and rbw() == IX.rblock(0, IX.RTAB[0][0], R32(RB + IX.R_GEN)), \
        (rbw(), IX.rblock(0, IX.RTAB[0][0], R32(RB + IX.R_GEN)))
    n0 = len(ys); run(400, gen)
    tail = [y for y in ys[n0:]]
    assert any(abs(y[2]) > 200 for y in tail) and any(abs(y[2] - y[3]) > 50 for y in tail), "reverb wet, stereo"
    print("  ok ASR HALL (MIX %s): DDL 16BIT switched off; %d frames bit-exact with the per-frame model (pre-delay, "
          "diffusers, combs, stereo allpasses, filters)" % (IX.PCT5[IX.RTAB[0][0]].strip(), len(tail)))
    e.wr(RMIX, 18, 1); fire(2); assert R32(RB + IX.R_OGM) == (IX.RTAB[0][13] * IX.pct_q12(18)) >> 12
    run(60, gen)
    e.wr(MPF, 1, 1); fire(2)
    assert e.rd(RPF, 1) == 0 and R32(RB + IX.R_ON) == 0 and R32(MB + IX.M_ON) == 1, "MOD switched the reverb off"
    run(20, gen)
    e.wr(RPF, 2, 1); fire(2)
    assert e.rd(MPF, 1) == 0 and R32(MB + IX.M_ON) == 0 and R32(RB + IX.R_ON) == 1 and e.rd(RMIX, 1) == IX.RTAB[1][0]
    run(300, gen)
    e.wr(DPF, 3, 1); fire(2); assert e.rd(RPF, 1) == 0 and R32(RB + IX.R_ON) == 0, "delay switched the reverb off"
    run(20, gen)
    e.wr(DPF, 0, 1); e.wr(RPF, 1, 1); fire(2); run(40, gen)
    e.wr(RPF, 0, 1); fire(2); run(8, gen)
    print("  ok VINT PLT bit-exact; MIX changed while running; a MOD or a delay preset switches the reverb off and back")
    # ---- the cost (one effect at a time), without and with MASTER FX ----
    MI = {p[0]: i + 1 for i, p in enumerate(IX.MPRESETS)}
    DI = {p[0]: i + 1 for i, p in enumerate(IX.DPRESETS)}
    CASES = (("no effect", 0, 0, 0), ("DDL 16BIT", DI["DDL 16BIT"], 0, 0), ("DUB TAPE", DI["DUB TAPE"], 0, 0),
             ("PING PONG", DI["PING PONG"], 0, 0), ("JUNO I", 0, MI["JUNO I"], 0), ("70S CHOR", 0, MI["70S CHOR"], 0),
             ("GTR PHASE", 0, MI["GTR PHASE"], 0), ("SLOWPHASE", 0, MI["SLOWPHASE"], 0), ("ASR HALL", 0, 0, 1),
             ("VINT PLT", 0, 0, 2))
    costs = {}                                                        # (MASTER FX is never on with INPUT FX: 2026-11-06)
    for nm, dp, mp, rp in CASES:
        e.wr(DPF, dp, 1); e.wr(MPF, mp, 1); e.wr(RPF, rp, 1); fire(2); STEPS[0] = 0; run(10, gen); costs[nm] = STEPS[0]
        assert e.rd(DPF, 1) == dp and e.rd(MPF, 1) == mp and e.rd(RPF, 1) == rp, nm
    e.wr(RPF, 0, 1)
    print("  cost per 32-frame block (MASTER FX off): " + ", ".join("%s %d" % kv for kv in costs.items()))
    e.wr(DPF, 0, 1); e.wr(MPF, MI["SLOWPHASE"], 1); fire(2); run(10, gen)
    # ---- a block the trigger could not start (MASTER still busy): processed late, not skipped -> no miss ----
    m0 = R32(MISS); W32(BUSY2, 1)
    for k in range(8):
        fr = [gen() for _ in range(4)]; rx(fr); pend.extend(fr); tick([0] * 8)
    W32(BUSY2, 0); NSEEN[0] = 0
    run(20, gen)
    assert R32(MISS) == m0, ("a late block was skipped", R32(MISS) - m0)
    print("  ok a block started late (busy for a whole block): caught up, no output block missed, bit-exact")
    # ---- a late block (64 frames waiting): caught up NCAP frames at a time, never a double-length call ----
    extra = [gen() for _ in range(64)]; rx(extra); pend.extend(extra); NSEEN[0] = 0; STEPS[0] = 0
    run(30, gen)
    lag = ((R32(IX.IN_WR) - R32(IXB + IX.C_SPOS)) & IX.RMASK) // 8
    assert NSEEN[0] <= IX.NCAP and lag <= 8, (NSEEN[0], lag)
    print("  ok backlog of 64 frames: at most %d frames per call (max %d steps), caught up (lag %d), bit-exact"
          % (NSEEN[0], STEPS[0], lag))
    # ---- overload: the input still reaches the output (dry, no effect), then INPUT FX retries by itself ----
    W32(KILL, 1); fire(1); assert R32(IXB + IX.C_KILL) == 1
    heard = 0
    for b in range(40):
        for k in range(8):
            fr = [gen() for _ in range(4)]; rx(fr); pend.extend(fr)
            sp0 = R32(IXB + IX.C_SPOS); fifo, _ = tick([0] * 8)
            heard = max([heard] + [abs(s32(v) >> 16) for v in fifo])
            n = ((R32(IXB + IX.C_SPOS) - sp0) & IX.RMASK) // 8
            for j in range(n):
                x = pend.pop(0); assert ringat(sp0 + 8 * j)[:2] == x, "overload: the ring = the dry input"
    assert heard > 1000, "overload: the player's input is silent"
    fire(KRETRY - 10); assert R32(KILL) == 1, "retry too early"
    fire(20)
    assert R32(KILL) == 0 and R32(IXB + IX.C_DON) == 0 and R32(MB + IX.M_ON) == 2, \
        ("no retry", R32(KILL), R32(IXB + IX.C_DON), R32(MB + IX.M_ON))
    pend.clear(); run(30, gen)
    print("  ok overload: dry input still monitored (peak %d), recording dry; INPUT FX retried by itself after %d fires,"
          " then bit-exact again" % (heard, KRETRY))
    # ---- the Record page Monitor is switched OFF (SMP+0x14 -> 0, then the OS apply routine) ----
    stub(IX.SMP_MONAPPLY, lambda s_: (s_.mov_imm(0x55, 0), s_.movl_st_disp(0, 0x30, 4), s_.rts(), s_.nop()))
    W32(IX.SMP + 0x30, 0); W32(IX.SMP + IX.SMP_MON, 1); fire(2)
    assert R32(IX.SMP + IX.SMP_MON) == 0 and R32(IX.SMP + 0x30) == 0x55, "Monitor forced OFF and applied"
    W32(IX.SMP + 0x30, 0); fire(2); assert R32(IX.SMP + 0x30) == 0, "applied only when it was on"
    print("  ok Record page Monitor ON -> switched OFF at once (OS apply 0x%08x called once)" % IX.SMP_MONAPPLY)
    # ---- SOURCE MAIN: the dry output mix goes through the effects into the input ring ----
    e.wr(DPF, 0, 1); e.wr(MPF, MI["JUNO II"], 1); e.wr(SRCF, 1, 1); fire(2)
    mains = []
    def olongs(k):
        v = []
        for i in range(4):
            t = len(mains); l = int(9000 * math.sin(t * 0.03)); r = -l // 2; mains.append((l, r))
            v += [(l & 0xFFFF) << 16, (r & 0xFFFF) << 16]
        return v
    import copy
    saved = (list(md.dly), dict(md.s), md.dw, list(md.ml), copy.deepcopy(md.t))
    got = []
    for b in range(200):
        for k in range(8):
            rx([(rnd.randint(-20000, 20000), 0) for _ in range(4)])              # the instrument is ignored
            sp0 = R32(IXB + IX.C_SPOS); tick(olongs(k))
            n = ((R32(IXB + IX.C_SPOS) - sp0) & IX.RMASK) // 8
            got += [ringat(sp0 + 8 * j)[:2] for j in range(n)]
    c = coefs(); rc = rcoefs(); ok_shift = None
    for sh_ in range(0, 200):
        m2 = IX.Model(); m2.dly, m2.s, m2.dw = list(saved[0]), dict(saved[1]), saved[2]
        m2.ml, m2.t = list(saved[3]), copy.deepcopy(saved[4])
        stream = [(0, 0)] * sh_ + mains
        want = [m2.full(c, rc, *stream[i])[:2] for i in range(len(got))]
        if want[600:] == got[600:]: ok_shift = sh_; break
    assert ok_shift is not None, "MAIN source: ring is not the processed dry mix"
    print("  ok SOURCE MAIN: the input ring = the dry output mix through the chorus (queue %d frames), instrument ignored" % ok_shift)
    # ---- off: literals back, then unhooked ----
    e.wr(SRCF, 0, 1); e.wr(INON, 0, 1); fire(1)
    assert lits() == [IX.IN_WR] * 5 and R32(IXB + IX.C_RUN) == 0, [hex(x) for x in lits()]
    for _ in range(4): tick([0] * 8)
    fire(2); assert R32(HOOKED) == 0, "unhooked once INPUT FX stopped"
    print("  ok OFF: the sampler literals back to the OS pointer first, then MASTER unhooked")
    # ---- the page: screens, row 2 texts, sampler entered while open, given back on leaving ----
    W32(EVQ + 0x68, pg2.page); fire(3)
    assert R32(IX.SMP + 8) == 1 and e.rd(IXENT, 1) == 1, "page open: sampler entered"
    vis2 = lambda F: e.rd(pg2.values[fid2(F)] + 0x17, 1) & 0x80 != 0
    row = lambda: bytes(e.rd(pg2.dlabels[XR1] + i, 1) for i in range(16)).decode()
    assert [F for F in XVIS if vis2(F)] == [INON, SRCF] and row() == "SOURCE:         ", (row(),)
    e.wr(XSEL, X_DLY, 1); fire(2)
    assert [F for F in XVIS if vis2(F)] == [DPF, DMIX, DFB, DTM] and row() == "MIX   FB        ", repr(row())
    e.wr(XSEL, X_MOD, 1); fire(2)
    assert [F for F in XVIS if vis2(F)] == [MPF, MMIX, MRATE] and row() == "MIX   RATE      ", repr(row())
    e.wr(XSEL, X_RVB, 1); fire(2)
    assert [F for F in XVIS if vis2(F)] == [RPF, RMIX] and row() == "MIX             ", repr(row())
    W32(EVQ + 0x68, 0x88123456); fire(2); assert R32(IX.SMP + 8) == 0 and e.rd(IXENT, 1) == 0, "left: sampler given back"
    W32(EVQ + 0x68, pg2.page); fire(2); W32(EVQ + 0x68, IX.REC_PAGE); fire(2)
    assert R32(IX.SMP + 8) == 1, "to the OS Record page: left entered for it"
    W32(IX.SMP + 8, 0)
    assert R32(PUB) == pg2.page, (hex(R32(PUB)), hex(pg2.page))
    print("  ok page: INPUT ('SOURCE:') / DELAY / MOD screens show their own fields; sampler entered while open, "
          "given back on leaving (kept for the OS Record page); page address published for Pad FX")
    selftest_excl(h, e, tick, IXB)
    print("  ok INPUT FX done")


def selftest_excl(h, e, tick, IXB):
    """MASTER FX and INPUT FX never together (2026-11-06): the prompt, DO IT / CANCEL, MASTER FX restored or not"""
    from shasm import SHAsm
    from mpcmod import OS_PUSH_PAGE
    from pages import EV_DOIT, EV_CANCEL
    R32, W32 = h.ram_r32, h.ram_w32
    LOGP, MSGB, OTHER = 0x88E40000, 0x88E50000, 0x88123456
    s_ = SHAsm(OS_PUSH_PAGE)
    s_.movl_pool(LOGP, 1); s_.movl_ld(1, 0); s_.add_imm(1, 0); s_.movl_st(0, 1); s_.shll2(0); s_.shll2(0)
    s_.add_rr(0, 1); s_.movl_st(5, 1); s_.movl_st_disp(6, 4, 1); s_.rts(); s_.nop()
    blob = s_.assemble()
    for i in range(0, len(blob), 2): e.wr(OS_PUSH_PAGE + i, struct.unpack('<H', blob[i:i+2])[0], 2)
    def pushes(): return [(R32(LOGP + 16 * (k + 1)), R32(LOGP + 16 * (k + 1) + 4)) for k in range(R32(LOGP))]
    def key(code, p=pg2):
        W32(LOGP, 0); W32(MSGB, 2 | (code << 16)); W32(MSGB + 4, 0); W32(MSGB + 8, 0); W32(MSGB + 12, 0)
        regs = [0x22220000 + i for i in range(16)]; regs[15] = 0x88400000 - 256; regs[4] = p.page; regs[5] = MSGB
        e.r = regs[:]; e.pr = 0xDEADBEEE; e.pc = p.handler; e.maxsteps = 2_000_000; e.forbid = None
        assert e.run(stop_pc=0xDEADBEEE), "handler did not return"
        for i in range(8, 16): assert e.r[i] == regs[i], "r%d corrupted" % i
    def ticks(n=80):                                                   # 8 ticks = one 32-frame block
        for _ in range(n): tick([0] * 8)
    vis = lambda F: e.rd(pg2.values[fid2(F)] + 0x17, 1) & 0x80 != 0
    lvis = lambda k: e.rd(pg2.dlabels[k] - 0x28 + 0x17, 1) & 0x80 != 0
    txt = lambda k: bytes(e.rd(pg2.dlabels[k] + i, 1) for i in range(16)).decode()
    on = lambda: e.rd(ONF, 1); inon = lambda: e.rd(INON, 1); run_ = lambda: R32(IXB + IX.C_RUN)
    page = lambda p: W32(EVQ + 0x68, p)
    assert on() == 0 and inon() == 0
    # MASTER FX on, then the INPUT FX page: the prompt, every field hidden, INPUT FX can't be switched on
    page(OTHER); e.wr(ONF, 1, 1); h.fire(3); ticks(); assert R32(HOOKED) == 1
    e.wr(XSEL, X_IN, 1); page(pg2.page); h.fire(2)
    assert R32(PROMPT) == 1 and lvis(XP0) and txt(XP0) == "DISABLE MSTR FX?" and txt(XR1) == "DO IT / CANCEL  ", (txt(XP0), txt(XR1))
    assert not any(vis(F) for F in XCURSOR) and not lvis(XR0), [F for F in XCURSOR if vis(F)]
    e.wr(INON, 1, 1); h.fire(2); assert inon() == 0 and on() == 1 and run_() == 0, "wheel on the prompt: nothing on"
    print("  ok MASTER FX on + INPUT FX page: prompt %r / %r, the fields hidden, INPUT FX can't come on" % (txt(XP0), txt(XR1)))
    # CANCEL: back (to Pad FX), MASTER FX still on; next entry prompts again
    key(EV_CANCEL); assert pushes() == [(0, 2)], pushes()
    page(OTHER); h.fire(2); assert on() == 1 and R32(PROMPT) == 0
    page(pg2.page); h.fire(2); assert R32(PROMPT) == 1
    print("  ok CANCEL: back to the Pad FX list, MASTER FX untouched; next time the prompt again")
    # DO IT: MASTER FX off, the INPUT screen; INPUT FX on (after MASTER's fade) / off -> MASTER FX on again
    key(EV_DOIT); assert pushes() == [] and on() == 0 and R32(PROMPT) == 0 and R32(SAVEDM) == 1
    h.fire(2); assert vis(XSEL) and vis(INON) and vis(SRCF) and not lvis(XP0) and lvis(XR0) and txt(XR1).startswith("SOURCE:")
    e.wr(INON, 1, 1); h.fire(2); assert run_() == 0, "starts only once MASTER FX has faded"
    ticks(); h.fire(2); assert run_() == 1 and on() == 0
    ticks(4); e.wr(INON, 0, 1); h.fire(2)
    assert run_() == 0 and on() == 1 and R32(SAVEDM) == 0 and R32(ACT) == 1, "MASTER FX back on"
    print("  ok DO IT: MASTER FX off -> INPUT FX on (after the 2.9 ms fade); INPUT FX off -> MASTER FX on again")
    # still on the page: INPUT FX on again (no prompt: MASTER FX goes off, remembered) and off
    e.wr(INON, 1, 1); h.fire(1); assert on() == 0 and R32(SAVEDM) == 1
    ticks(); h.fire(2); assert run_() == 1
    e.wr(INON, 0, 1); h.fire(2); assert on() == 1 and run_() == 0
    # DO IT, then the page left without INPUT FX: MASTER FX on again
    page(OTHER); h.fire(2); ticks(); page(pg2.page); h.fire(2); assert R32(PROMPT) == 1
    key(EV_DOIT); h.fire(2); assert on() == 0
    page(OTHER); h.fire(2); assert on() == 1 and R32(SAVEDM) == 0
    print("  ok DO IT but INPUT FX never switched on: MASTER FX back on when the page is left")
    # MASTER FX off before: no prompt; INPUT FX on / off -> MASTER FX stays off
    e.wr(ONF, 0, 1); h.fire(2); ticks(); h.fire(2)
    page(pg2.page); h.fire(2); assert R32(PROMPT) == 0 and vis(XSEL) and not lvis(XP0)
    e.wr(INON, 1, 1); h.fire(2); assert run_() == 1
    e.wr(INON, 0, 1); h.fire(2); assert run_() == 0 and on() == 0
    print("  ok MASTER FX off: no prompt; INPUT FX on / off leaves MASTER FX off")
    # CANCEL one step back: on a DELAY setting -> cursor on the screen selector (stays); again -> back to Pad FX
    e.wr(XSEL, X_DLY, 1); h.fire(2); W32(pg2.page + 0x24, pg2.values[fid2(DMIX)]); h.fire(1)
    key(EV_CANCEL); assert pushes() == [] and R32(pg2.page + 0x24) == pg2.values[fid2(XSEL)], (pushes(), hex(R32(pg2.page + 0x24)))
    h.fire(1); assert e.rd(XSEL, 1) == X_DLY and vis(DPF)
    key(EV_CANCEL); assert pushes() == [(0, 2)], pushes()
    W32(pg.page + 0x24, pg.values[fid(LBF)]); key(EV_CANCEL, pg)
    assert pushes() == [] and R32(pg.page + 0x24) == pg.values[fid(PAGEF)], "MASTER FX page: CANCEL -> page selector"
    key(EV_CANCEL, pg); assert pushes() == [(0, 2)], pushes()
    e.wr(XSEL, X_IN, 1); h.fire(2)
    print("  ok CANCEL on a setting -> cursor on the screen selector; CANCEL there -> back to Pad FX (INPUT FX and MASTER FX pages)")
    # MASTER FX switched on (MASTER page) while INPUT FX runs: INPUT FX off, nothing to restore
    e.wr(INON, 1, 1); h.fire(2); assert run_() == 1
    page(pg.page); h.fire(1); e.wr(ONF, 1, 1); h.fire(2)
    assert inon() == 0 and run_() == 0 and on() == 1 and R32(SAVEDM) == 0
    print("  ok MASTER FX switched on while INPUT FX runs: INPUT FX off (MASTER FX stays on)")
    e.wr(ONF, 0, 1); h.fire(2); ticks(); page(OTHER); h.fire(3); assert R32(HOOKED) == 0



if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', '..', 'MASTER.WAV'); pg.build_wav(out); selftest(out)
