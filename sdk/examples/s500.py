#!/usr/bin/env python3
"""S500 - sampler conversion of a pad, based on the Akai S-series samplers like the S900 (not a re-creation of them): pitch up, anti-alias filter, sample rate, bits, normalize -> ONE mono
sample that replaces the pad's layers (the originals stay in memory).

    python sdk/examples/s500.py          -> builds S500.WAV and tests it in the emulator

On the MPC500: copy S500.WAV to the CF card, LOAD it (after HUB.WAV), then MODE -> OTHER -> "S500".
  row 0: "12BIT NRM:Y  A05"   bits 16/12/8 (default 12) . normalize (default Y) . the pad (shows "  45%" while working)
  row 1: "26.04k AA:Y P+12"   rate 7.5k..40.0k in 0.5k steps + 26.04k (default) . anti-alias filter (default Y) .
                              pitch up 0..+24 semitones (default +0)
Flow: hit a pad (it plays as usual) -> set the fields -> DO IT. CANCEL while working = stop, the pad is left as it was.
  1. every layer with a sample: its region [start, end] mixed to mono ((L+R)/2), sped up by its own tuning + pitch up
     (drop-sample: samples skipped/repeated like the old varispeed samplers), at its level relative to the loudest layer
     / number of layers (no clipping); all layers summed into a new sample (copy of layer 1's sample, then made mono
     and resized: it is the last block of the sample list).
  2. anti-alias: 4th-order Butterworth low-pass at 0.45 x rate (two biquads, Q13), optional.
  3. sample & hold at the rate (phase accumulator, exact fractional step: 26.04k = 26040 Hz).
  4. normalize (optional): peak of the result of 1-3 -> full scale, applied before 5 so the bits are used fully.
  5. bits: rounding to 12 or 8 bits (16 = untouched).
  6. the pad: layer 1 = the new sample, tuned down by the pitch up, level = the loudest layer, velocity range 0..127;
     layers 2-4 cleared. Originals untouched in memory.

OS addresses (MPC500 OS 1.31):
  Program/pads: SDK README (program 0x8837B5AC + idx*0x1F98, idx u16 0x8847C0E2; pad = +0x18 + pad*0xA4; layer 0x18:
  name16, +0x11 level, +0x12/+0x13 velocity range, +0x14 tune s16 cents). Bank byte 0x8849C98C.
  Samples: MGR 0x8848ED30 (+0 list start, +4 limit, +8 current). Block: +0 size, +4 name16, +0x18 length, +0x1C frames
  per channel (0 = mono), +0x20 start, +0x24 end, +0x28 loop, +0x92 channels (u16), +0x98 bytes/s, +0x9C block align,
  PCM s16 from +0x100, R at +0x100 + 2*[+0x1C] (Sample FX; OS sampler create 0x884EC670 / stop 0x884EC2F0, 2026-11-01).
  Copy 0x884C5FA0 via os_extract (new block = last of the list, current), Delete 0x884C5FFC (current), LOOP_GUARD
  0x884C4F04, find by name 0x884C5090 (Sample FX / Audio Track).
  Heavy loops run through the cached P1 alias of our code after invalidating its cache lines (Sample FX, HW: P2 code
  runs at ~1M instructions/s).
"""
import sys, os, struct, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import page_or_addon, ld, st, sti
from mpcmod import EVQ

PROG_BASE, PROG_IDX, PROG_STRIDE = 0x8837B5AC, 0x8847C0E2, 0x1F98
PAD_OFF, PAD_SIZE, LAYER = 0x18, 0xA4, 0x18
BANKOBJ = 0x8849C98C
MGR, COPY_CMD, DELETE_CMD, LOOP_GUARD, FINDNAME = 0x8848ED30, 0x884C5FA0, 0x884C5FFC, 0x884C4F04, 0x884C5090
CHUNK = 2048
CACHE_SPAN = 0x900

# ---- settings tables (built here, embedded in the code) ----
import s500_rate as SR
RATES, RATE_DEF = SR.RATES, SR.RATE_DEF                   # 7.00 .. 44.10k (s500_rate.py)
#   # x0.01 kHz, 7.50 .. 40.00
BITS_OPTS, BITS = ["16BIT", "12BIT", " 8BIT"], [16, 12, 8]
PITCH_OPTS = ["%+3d" % n if n else " +0" for n in range(25)]
Q = 13
def biquads(rate100):
    fs = 44100.0; fc = min(0.45 * rate100 * 10, 0.45 * fs); w = 2 * math.pi * fc / fs; c, s = math.cos(w), math.sin(w)
    out = []
    for qv in (0.5411961, 1.3065630):
        al = s / (2 * qv); a0 = 1 + al
        out += [round((1 - c) / 2 / a0 * (1 << Q)), round(-2 * c / a0 * (1 << Q)), round((1 - al) / a0 * (1 << Q))]
    return out                                            # b0a a1a a2a b0b a1b a2b (b1 = 2 b0, b2 = b0)
RTAB = [[(r << 16) // 4410] + biquads(r) for r in RATES]
assert all(-32768 <= c < 32768 for row in RTAB for c in row[1:]), "coefficients fit s16"
RTAB16 = [h for row in RTAB for h in ((row[0] & 0xFFFF), (row[0] >> 16)) + tuple(c & 0xFFFF for c in row[1:])]   # 16 B / rate  # per rate: step (Q16 of 44.1k), 6 coefficients
SEMI = [round(65536 * 2 ** (k / 12)) for k in range(-60, 61)]      # 2^(k/12), k = -60..60
CENT = [round(65536 * 2 ** (r / 1200)) for r in range(100)]
CENTI = [round(65536 * 2 ** (-r / 1200)) for r in range(100)]

pg = page_or_addon("s500a", open_press=None)
pg.other_menu("S500")
pg.far_hend = True
pg.msg_fires = 0x600
BITSI = pg.choice("", 0, 0, BITS_OPTS, default=1)
NORM = pg.choice(" NRM:", 5, 0, ["N", "Y"], default=1)
PADL = pg.dlabel(11, 0, 5)
RATE = SR.fields(pg); RATEI = RATE['I']                 # "26.04k": wheel by digit, slider 6 rates (s500_rate.py)
AA = pg.choice(" AA:", 6, 1, ["N", "Y"], default=1)
PUP = pg.choice(" P", 11, 1, PITCH_OPTS, default=0)
pg.message()
(ST, SEL, PEND, PHASE, LI, NL, DST, NOUT, POS, DONEU, TOTU, MAXLV, PREVCUR, LASTPCT) = [pg.alloc_slot() for _ in range(14)]
LSRC, LSTART, LEND, LSTEP, LGAIN, LN = [[pg.alloc_slot() for _ in range(4)] for _ in range(6)]
# kernel parameters (contiguous, read by the kernels)
KP = [pg.alloc_slot() for _ in range(32)]
for _s in KP: del pg.host.defaults[_s]
K_X1, K_X2, K_Y1, K_Y2, K_Z1, K_Z2, K_ACC, K_HELD, K_B0A, K_A1A, K_A2A, K_B0B, K_A1B, K_A2B, K_STEP, K_G = range(0, 64, 4)
K_MASK, K_RND, K_PEAK, K_AA, K_PASS = range(64, 84, 4)                  # (offsets from KP[16] = +64: r12 base)
M_LBASE, M_RBASE, M_DST, M_N, M_I, M_F, M_SI, M_SF, M_END, M_G, M_DONE = range(84, 128, 4)
KB = KP[0]
ST_IDLE, ST_JOB = 0, 1
PH_START, PH_CLEAR, PH_MIX, PH_PEAK, PH_WRITE, PH_FIN = range(6)

def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()
def osc(m, fn): m.imm32(fn, 3); m.a.jsr(3); m.a.nop()
def rdb(m, addr, r): m.imm32(addr, r); m.a.movb_ld(r, r); m.a.extu_b(r, r)
def kst(m, r, off): m.imm32(KB + off, 1); m.a.movl_st(r, 1)              # (uses r1)
def kld(m, off, r): m.imm32(KB + off, r); m.a.movl_ld(r, r)
def cached_call(m, label):
    m.a.movl_pool(label, 2); m.imm32(0xDFFFFFFF, 3); m.a.and_rr(3, 2); m.a.jsr(2); m.a.nop()

def padblk(m, rpad, rd_):
    """rd_ = pad block of pad rpad (0..47) in the current program. Uses r1, r3"""
    a = m.a
    m.imm32(PROG_IDX, 1); a.movw_ld(1, rd_); a.extu_w(rd_, rd_); m.imm32(PROG_STRIDE, 1); a.mul_l(1, rd_); a.sts_macl(rd_)
    m.imm32(PROG_BASE + PAD_OFF, 1); a.add_rr(1, rd_)
    m.imm32(PAD_SIZE, 3); a.mul_l(rpad, 3); a.sts_macl(3); a.add_rr(3, rd_)

def fire(m):
    a = m.a
    ld(m, PEND, 0); a.tst_rr(0, 0); np_ = m.newlabel('np'); m.bt_far(np_)
    st(m, 0, SEL); sti(m, 0, PEND)
    a.deflabel(np_)
    ld(m, ST, 0); a.tst_rr(0, 0); nj = m.newlabel('nj'); m.bt_far(nj); call(m, 's9_job'); a.deflabel(nj)
    call(m, 's9_disp')
pg.on_fire(fire)
SR.attach(pg, RATE)

def inactive(m):
    a = m.a
    ld(m, ST, 0); a.tst_rr(0, 0); nj = m.newlabel('nj'); m.bt_far(nj); call(m, 's9_job'); a.deflabel(nj)
    SR.leave(m, RATE)
pg.on_inactive(inactive)

def subs(m):
    a = m.a
    # ---- s9_disp: pad / percentage at row 0 col 11 ----
    a.deflabel('s9_disp'); a.stsl_pr()
    m.imm32(pg.addr['dlabels'][PADL], 12); a.mov_imm(0, 14)
    def cput(s):
        for ch in s:
            n = m.newlabel('cp'); a.movb_ld(12, 1); a.mov_imm(ord(ch), 0); a.cmpeq(0, 1); a.bt_lbl(n)
            a.movb_st(0, 12); a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
    def cputr(r):
        n = m.newlabel('cr'); a.movb_ld(12, 1); a.cmpeq(r, 1); a.bt_lbl(n); a.mov_rm_rn(r, 0); a.movb_st(0, 12)
        a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
    dn, job, nos = m.newlabel('dn'), m.newlabel('job'), m.newlabel('nos')
    ld(m, ST, 0); a.tst_rr(0, 0); m.bf_far(job)
    ld(m, SEL, 9); a.tst_rr(9, 9); m.bt_far(nos)
    a.add_imm(-1, 9); a.mov_rm_rn(9, 4); a.mov_imm(12, 5); pg._hcall(m, '__udiv')
    a.add_imm(ord('A'), 0); a.mov_rm_rn(0, 10); a.add_imm(1, 1); a.mov_rm_rn(1, 4); a.mov_imm(10, 5)
    pg._hcall(m, '__udiv'); a.add_imm(0x30, 0); a.mov_rm_rn(0, 11); a.add_imm(0x30, 1); a.mov_rm_rn(1, 13)
    cput("  "); cputr(10); cputr(11); cputr(13); a.bra_lbl(dn); a.nop()
    a.deflabel(nos); cput(" PAD?"); a.bra_lbl(dn); a.nop()
    m.pool_jump()
    a.deflabel(job)                                                          # " 45%"
    ld(m, TOTU, 5); a.shlr2(5); a.shlr2(5); a.shlr2(5); a.add_imm(1, 5)        # (avoid overflow: /64 both)
    ld(m, DONEU, 4); a.shlr2(4); a.shlr2(4); a.shlr2(4); a.mov_imm(100, 0); a.mul_l(0, 4); a.sts_macl(4)
    pg._hcall(m, '__udiv'); a.mov_imm(99, 1); c9 = m.newlabel('c9'); a.cmpgt(1, 0); a.bf_lbl(c9); a.mov_rm_rn(1, 0); a.deflabel(c9)
    a.mov_rm_rn(0, 4); a.mov_imm(10, 5); pg._hcall(m, '__udiv')
    a.add_imm(0x30, 0); a.mov_rm_rn(0, 10); a.add_imm(0x30, 1); a.mov_rm_rn(1, 11)
    cput("  "); cputr(10); cputr(11); cput("%")
    a.deflabel(dn)
    a.tst_rr(14, 14); px = m.newlabel('px'); m.bt_far(px); m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(px); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- s9_job: one step of the job per call ----
    a.deflabel('s9_job'); a.stsl_pr()
    jx = m.newlabel('jx')
    labs = {p: m.newlabel('ph%d' % p) for p in range(6)}
    ld(m, PHASE, 0)
    for p in range(6):
        nx = m.newlabel('pn'); a.mov_imm(p, 1); a.cmpeq(1, 0); a.bf_lbl(nx); a.movl_pool(labs[p], 2); a.jmp(2); a.nop(); a.deflabel(nx)
    a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()

    # == START: layers -> sources, steps, lengths; copy of the first source = the new sample; resize, make mono ==
    a.deflabel(labs[PH_START])
    ld(m, SEL, 9); a.add_imm(-1, 9); padblk(m, 9, 11)                       # r11 = pad block
    a.mov_imm(0, 13); a.mov_imm(0, 0); st(m, 0, MAXLV); st(m, 0, NOUT)       # r13 = layers found
    rdb(m, PUP, 0); a.mov_imm(100, 1); a.mul_l(1, 0); a.sts_macl(0); kst(m, 0, M_DONE)   # (M_DONE as temp: pitch cents)
    for l in range(4):
        sk = m.newlabel('ls')
        a.mov_rm_rn(11, 1); a.add_imm(l * LAYER, 1); a.movb_ld(1, 0); a.tst_rr(0, 0); m.bt_far(sk)
        m.imm32(MGR, 4); a.mov_rm_rn(11, 5); a.add_imm(l * LAYER, 5); osc(m, FINDNAME); a.tst_rr(0, 0); m.bt_far(sk)
        # slot index r13: source, region, level, cents
        a.mov_rm_rn(13, 10); a.shll2(10)                                     # r10 = 4*k
        m.imm32(LSRC[0], 1); a.add_rr(10, 1); a.movl_st(0, 1); a.mov_rm_rn(0, 12)   # r12 = source block
        a.movl_ld_disp(12, 0x20, 2); a.movl_ld_disp(12, 0x24, 3); a.movl_ld_disp(12, 0x18, 4)
        a.add_imm(-1, 4); a.cmphi(4, 3); e1 = m.newlabel('e1'); a.bf_lbl(e1); a.mov_rm_rn(4, 3); a.deflabel(e1)   # end <= len-1
        a.cmphi(3, 2); e2 = m.newlabel('e2'); a.bf_lbl(e2); a.mov_rm_rn(3, 2); a.deflabel(e2)                    # start <= end
        m.imm32(LSTART[0], 1); a.add_rr(10, 1); a.movl_st(2, 1); m.imm32(LEND[0], 1); a.add_rr(10, 1); a.movl_st(3, 1)
        a.mov_rm_rn(3, 6); a.sub(2, 6)                                       # r6 = end - start (= length - 1)
        a.mov_rm_rn(11, 1); a.add_imm(l * LAYER + 0x11, 1); a.movb_ld(1, 7); a.extu_b(7, 7)   # r7 = level
        m.imm32(LGAIN[0], 1); a.add_rr(10, 1); a.movl_st(7, 1)               # (level for now; gain later)
        ld(m, MAXLV, 0); mx = m.newlabel('mx'); a.cmphi(0, 7); a.bf_lbl(mx); st(m, 7, MAXLV); a.deflabel(mx)
        a.mov_rm_rn(11, 1); a.add_imm(l * LAYER + 0x14, 1); a.movw_ld(1, 4); kld(m, M_DONE, 0); a.add_rr(0, 4)   # r4 = cents
        a.movl_st_dec(6, 15); a.movl_st_dec(10, 15)
        call(m, 's9_pow')                                                    # r0 = step (Q16), r1 = inverse (Q16)
        a.movl_ld_inc(15, 10); a.movl_ld_inc(15, 6)
        m.imm32(LSTEP[0], 2); a.add_rr(10, 2); a.movl_st(0, 2)
        a.dmulu_l(1, 6); a.sts_mach(0); a.sts_macl(2); a.shll16(0); a.shlr16(2); a.or_rr(2, 0); a.add_imm(1, 0)   # n = (len-1)*inv>>16 + 1
        m.imm32(LN[0], 2); a.add_rr(10, 2); a.movl_st(0, 2)
        ld(m, NOUT, 2); mo = m.newlabel('mo'); a.cmphi(2, 0); a.bf_lbl(mo); st(m, 0, NOUT); a.deflabel(mo)
        a.add_imm(1, 13)
        a.deflabel(sk)
        m.pool_jump()
    st(m, 13, NL)
    a.tst_rr(13, 13); some = m.newlabel('some'); m.bf_far(some)
    sti(m, ST_IDLE, ST); pg.emit_message(m, "NO SAMPLE"); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(some)
    # gains: level * 65536 / (max level * layers); max level 0 -> 100
    ld(m, MAXLV, 0); a.tst_rr(0, 0); ml = m.newlabel('ml'); a.bf_lbl(ml); a.mov_imm(100, 0); st(m, 0, MAXLV)
    a.deflabel(ml)
    a.mov_imm(0, 10)
    gl = m.newlabel('gl'); a.deflabel(gl)
    m.imm32(LGAIN[0], 9); a.add_rr(10, 9); a.movl_ld(9, 4)
    ld(m, MAXLV, 0); a.tst_rr(4, 4); gz = m.newlabel('gz'); a.bf_lbl(gz); a.mov_rm_rn(0, 4); a.deflabel(gz)   # level 0 = all 0: as max
    a.shll16(4); ld(m, NL, 5); a.mul_l(0, 5); a.sts_macl(5); pg._hcall(m, '__udiv'); a.movl_st(0, 9)
    a.add_imm(4, 10); ld(m, NL, 0); a.shll2(0); a.cmpeq(0, 10); a.bf_lbl(gl)
    # the new sample: copy of the first source (current), resized to NOUT mono frames
    ld(m, LSRC[0], 8); m.imm32(MGR, 1); a.movl_ld_disp(1, 8, 0); st(m, 0, PREVCUR)
    m.imm32(MGR, 1); a.movl_st_disp(8, 8, 1)                                 # current = first source
    m.os_extract(8, COPY_CMD)
    a.tst_rr(0, 0); cok = m.newlabel('cok'); m.bt_far(cok)
    ld(m, PREVCUR, 0); m.imm32(MGR, 1); a.movl_st_disp(0, 8, 1)
    sti(m, ST_IDLE, ST); pg.emit_message(m, "MEMORY FULL"); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(cok)
    m.imm32(MGR, 1); a.movl_ld_disp(1, 8, 9); st(m, 9, DST)                  # r9 = new block
    ld(m, NOUT, 4); a.shll(4); m.imm32(0x100 + 3, 0); a.add_rr(0, 4); a.mov_imm(-4, 0); a.and_rr(0, 4)   # r4 = new size
    a.mov_rm_rn(9, 0); a.add_rr(4, 0); a.add_imm(4, 0); m.imm32(MGR, 1); a.movl_ld_disp(1, 4, 1)     # r1 = limit
    a.cmphi(1, 0); fit = m.newlabel('fit'); m.bf_far(fit)
    m.imm32(DELETE_CMD, 2); m.imm32(MGR, 4); a.jsr(2); a.nop()               # does not fit: delete the copy
    ld(m, PREVCUR, 0); m.imm32(MGR, 1); a.movl_st_disp(0, 8, 1)
    sti(m, ST_IDLE, ST); pg.emit_message(m, "MEMORY FULL"); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(fit)
    a.movl_st(4, 9); a.mov_rm_rn(9, 0); a.add_rr(4, 0); a.mov_imm(0, 1); a.movl_st(1, 0)   # size, list end after it
    ld(m, NOUT, 0); a.movl_st_disp(0, 0x18, 9); a.add_imm(-1, 0); a.movl_st_disp(0, 0x24, 9)
    a.mov_imm(0, 0); a.movl_st_disp(0, 0x1C, 9); a.movl_st_disp(0, 0x20, 9); a.movl_st_disp(0, 0x28, 9)
    m.imm32(0x92, 1); a.add_rr(9, 1); a.mov_imm(1, 0); a.movw_st(0, 1)
    m.imm32(0x98, 1); a.add_rr(9, 1); m.imm32(88200, 0); a.movl_st(0, 1)
    m.imm32(0x9C, 1); a.add_rr(9, 1); a.mov_imm(2, 0); a.movw_st(0, 1)
    # units for the percentage: clear + each layer + (peak) + write
    ld(m, NOUT, 0); ld(m, NL, 2); a.add_imm(2, 2); rdb(m, NORM, 3); a.add_rr(3, 2); a.mul_l(2, 0); a.sts_macl(0); st(m, 0, TOTU)
    sti(m, 0, DONEU); sti(m, 0, POS); sti(m, PH_CLEAR, PHASE)
    call(m, 'icinv')
    a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()

    # == CLEAR: the new PCM = 0 ==
    a.deflabel(labs[PH_CLEAR])
    ld(m, NOUT, 2); ld(m, POS, 3); a.sub(3, 2); m.imm32(CHUNK, 0); a.cmphi(0, 2); c1 = m.newlabel('c1'); a.bf_lbl(c1); a.mov_rm_rn(0, 2); a.deflabel(c1)
    kst(m, 2, M_N)
    ld(m, DST, 4); a.mov_rm_rn(3, 0); a.shll(0); a.add_rr(0, 4); m.imm32(0x100, 0); a.add_rr(0, 4); kst(m, 4, M_DST)
    a.add_rr(2, 3); st(m, 3, POS); ld(m, DONEU, 0); a.add_rr(2, 0); st(m, 0, DONEU)
    cached_call(m, 's9_kclr')
    ld(m, NOUT, 0); ld(m, POS, 3); a.cmpeq(0, 3); m.bf_far(jx)
    sti(m, 0, LI); call(m, 's9_layer'); sti(m, PH_MIX, PHASE); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()

    # == MIX: a chunk of layer LI ==
    a.deflabel(labs[PH_MIX])
    ld(m, LI, 10); a.shll2(10); m.imm32(LN[0], 1); a.add_rr(10, 1); a.movl_ld(1, 2)
    ld(m, POS, 3); a.sub(3, 2); m.imm32(CHUNK, 0); a.cmphi(0, 2); c2 = m.newlabel('c2'); a.bf_lbl(c2); a.mov_rm_rn(0, 2); a.deflabel(c2)
    kst(m, 2, M_N)
    ld(m, DST, 4); a.mov_rm_rn(3, 0); a.shll(0); a.add_rr(0, 4); m.imm32(0x100, 0); a.add_rr(0, 4); kst(m, 4, M_DST)
    a.add_rr(2, 3); st(m, 3, POS); ld(m, DONEU, 0); a.add_rr(2, 0); st(m, 0, DONEU)
    cached_call(m, 's9_kmix')
    kld(m, M_DONE, 0); a.tst_rr(0, 0); ld_ = m.newlabel('ldn'); m.bf_far(ld_)       # source finished
    ld(m, LI, 10); a.shll2(10); m.imm32(LN[0], 1); a.add_rr(10, 1); a.movl_ld(1, 2); ld(m, POS, 3); a.cmpeq(2, 3); m.bf_far(jx)
    a.deflabel(ld_)
    ld(m, LI, 0); a.add_imm(1, 0); st(m, 0, LI); ld(m, NL, 1); a.cmpeq(1, 0); nl_ = m.newlabel('nl'); m.bt_far(nl_)
    call(m, 's9_layer'); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    a.deflabel(nl_)
    call(m, 's9_fxinit'); sti(m, 0, POS)
    rdb(m, NORM, 0); a.tst_rr(0, 0); m.imm32(0x10000, 0); kst(m, 0, K_G); a.mov_imm(PH_WRITE, 0)
    nn = m.newlabel('nn'); a.bt_lbl(nn); a.mov_imm(PH_PEAK, 0); a.deflabel(nn)
    st(m, 0, PHASE)
    a.mov_imm(PH_PEAK, 1); a.cmpeq(1, 0); wp = m.newlabel('wp'); a.bf_lbl(wp); a.mov_imm(0, 0); kst(m, 0, K_PASS); a.bra_lbl('s9_jx2'); a.nop()
    a.deflabel(wp); a.mov_imm(1, 0); kst(m, 0, K_PASS)
    a.deflabel('s9_jx2'); a.movl_pool(jx, 2); a.jmp(2); a.nop()
    m.pool_jump()

    # == PEAK / WRITE: a chunk in place ==
    for ph in (PH_PEAK, PH_WRITE):
        a.deflabel(labs[ph])
        ld(m, NOUT, 2); ld(m, POS, 3); a.sub(3, 2); m.imm32(CHUNK, 0); a.cmphi(0, 2); c3 = m.newlabel('c3'); a.bf_lbl(c3); a.mov_rm_rn(0, 2); a.deflabel(c3)
        kst(m, 2, M_N)
        ld(m, DST, 4); a.mov_rm_rn(3, 0); a.shll(0); a.add_rr(0, 4); m.imm32(0x100, 0); a.add_rr(0, 4); kst(m, 4, M_DST)
        a.add_rr(2, 3); st(m, 3, POS); ld(m, DONEU, 0); a.add_rr(2, 0); st(m, 0, DONEU)
        cached_call(m, 's9_kfx')
        ld(m, NOUT, 0); ld(m, POS, 3); a.cmpeq(0, 3); m.bf_far(jx)
        if ph == PH_PEAK:                                                    # gain = 32767 / peak (Q16)
            kld(m, K_PEAK, 5); m.imm32(0x10000, 0); a.mov_imm(16, 1); a.cmphs(1, 5); pk = m.newlabel('pk'); a.bf_lbl(pk)
            m.imm32(0x7FFF0000, 4); pg._hcall(m, '__udiv')
            a.deflabel(pk); a.mov_rm_rn(0, 13)
            call(m, 's9_fxinit'); sti(m, 0, POS); sti(m, PH_WRITE, PHASE)    # (fxinit clears the peak)
            kst(m, 13, K_G); a.mov_imm(1, 0); kst(m, 0, K_PASS)
        else:
            sti(m, PH_FIN, PHASE)
        a.movl_pool(jx, 2); a.jmp(2); a.nop()
        m.pool_jump()

    # == FIN: loop guard, the pad ==
    a.deflabel(labs[PH_FIN])
    ld(m, DST, 4); osc(m, LOOP_GUARD)
    ld(m, SEL, 9); a.add_imm(-1, 9); padblk(m, 9, 11)
    ld(m, DST, 8); a.mov_rm_rn(8, 2); a.add_imm(4, 2); a.mov_rm_rn(11, 3); a.mov_imm(16, 4)
    nc = m.newlabel('nc'); a.deflabel(nc); a.movb_ld(2, 0); a.movb_st(0, 3); a.add_imm(1, 2); a.add_imm(1, 3); a.dt(4); a.bf_lbl(nc)
    ld(m, MAXLV, 0); a.mov_rm_rn(11, 1); a.add_imm(0x11, 1); a.movb_st(0, 1)
    a.mov_imm(0, 0); a.add_imm(1, 1); a.movb_st(0, 1); a.mov_imm(127, 0); a.add_imm(1, 1); a.movb_st(0, 1)
    rdb(m, PUP, 0); a.mov_imm(-100, 1); a.mul_l(1, 0); a.sts_macl(0); a.mov_rm_rn(11, 1); a.add_imm(0x14, 1); a.movw_st(0, 1)
    a.mov_imm(0, 0)
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

    # ---- s9_layer: kernel parameters of layer LI (source pointers, step, end, gain); POS = 0 ----
    a.deflabel('s9_layer')
    ld(m, LI, 10); a.shll2(10)
    m.imm32(LSRC[0], 1); a.add_rr(10, 1); a.movl_ld(1, 8)                   # r8 = source block
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

    # ---- s9_fxinit: filter / hold state, coefficients and step of the rate, bits ----
    a.deflabel('s9_fxinit'); a.stsl_pr()
    a.mov_imm(0, 0)
    for off in (K_X1, K_X2, K_Y1, K_Y2, K_Z1, K_Z2, K_HELD, K_PEAK): kst(m, 0, off)
    m.imm32(0x10000, 0); kst(m, 0, K_ACC)
    rdb(m, RATEI, 4); a.shll2(4); a.shll2(4); a.movl_pool('s9_rtab', 2); a.add_rr(4, 2)    # 16 bytes per rate
    a.movl_ld(2, 0); a.add_imm(4, 2); kst(m, 0, K_STEP)                                 # step u32
    for off in (K_B0A, K_A1A, K_A2A, K_B0B, K_A1B, K_A2B):                              # coefficients s16
        a.movw_ld(2, 0); a.add_imm(2, 2); kst(m, 0, off)
    rdb(m, AA, 0); kst(m, 0, K_AA)
    rdb(m, BITSI, 4)                                                         # 0 16, 1 12, 2 8 bits
    a.mov_imm(-1, 0); a.mov_imm(0, 2); a.tst_rr(4, 4); b16 = m.newlabel('b16'); a.bt_lbl(b16)
    m.imm32(0xFFFFFFF0, 0); a.mov_imm(8, 2); a.mov_imm(1, 3); a.cmpeq(3, 4); a.bt_lbl(b16)
    m.imm32(0xFFFFFF00, 0); m.imm32(0x80, 2)
    a.deflabel(b16); kst(m, 0, K_MASK); kst(m, 2, K_RND)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- s9_pow: r4 = cents (-3600..6000) -> r0 = 2^(c/1200) Q16, r1 = 2^(-c/1200) Q16. r0-r7 ----
    a.deflabel('s9_pow'); a.stsl_pr()
    m.imm32(-3600, 0); lo_ = m.newlabel('lo'); a.cmpge(0, 4); a.bt_lbl(lo_); a.mov_rm_rn(0, 4); a.deflabel(lo_)
    m.imm32(6000, 0); hi_ = m.newlabel('hi'); a.cmpgt(0, 4); a.bf_lbl(hi_); a.mov_rm_rn(0, 4); a.deflabel(hi_)
    m.imm32(6000, 0); a.add_rr(0, 4); a.mov_imm(100, 5); pg._hcall(m, '__udiv')   # r0 = k + 60, r1 = r (c + 6000 = 100(k+60) + r)
    a.mov_rm_rn(0, 6); a.mov_rm_rn(1, 7)
    a.movl_pool('s9_semi', 2); a.mov_rm_rn(6, 0); a.shll2(0); a.add_rr(0, 2); a.movl_ld(2, 4)      # SEMI[k]
    a.movl_pool('s9_cent', 2); a.mov_rm_rn(7, 0); a.shll2(0); a.add_rr(0, 2); a.movl_ld(2, 5)      # CENT[r]
    a.dmulu_l(4, 5); a.sts_mach(0); a.sts_macl(3); a.shll16(0); a.shlr16(3); a.or_rr(3, 0); a.movl_st_dec(0, 15)
    m.imm32(120, 0); a.sub(6, 0); a.mov_rm_rn(0, 6)                          # -k + 60
    a.movl_pool('s9_semi', 2); a.shll2(0); a.add_rr(0, 2); a.movl_ld(2, 4)
    a.movl_pool('s9_centi', 2); a.mov_rm_rn(7, 0); a.shll2(0); a.add_rr(0, 2); a.movl_ld(2, 5)
    a.dmulu_l(4, 5); a.sts_mach(1); a.sts_macl(3); a.shll16(1); a.shlr16(3); a.or_rr(3, 1)
    a.movl_ld_inc(15, 0)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- icinv: invalidate the cache lines of our kernels (Sample FX) ----
    a.deflabel('icinv')
    a.movl_pool('__cstart', 4); m.imm32(0xFFFFFFF0, 1); a.and_rr(1, 4)
    m.imm32(CACHE_SPAN // 16 + 1, 5); m.imm32(0xF0000008, 6); m.imm32(0x1FFFFC00, 7); m.imm32(0xFF0, 3)
    icl = m.newlabel('icl'); a.deflabel(icl)
    a.mov_rm_rn(4, 0); a.and_rr(3, 0); a.or_rr(6, 0); a.mov_rm_rn(4, 1); a.and_rr(7, 1); a.movl_st(1, 0)
    a.add_imm(16, 4); a.dt(5); a.bf_lbl(icl)
    a.rts(); a.nop()
    m.pool_jump()

    # =========================== kernels: run from the cached alias (PC-relative only) ===========================
    a.deflabel('__cstart')
    # ---- s9_kclr: n samples of dst = 0 ----
    a.deflabel('s9_kclr')
    m.imm32(KB + 64, 12); a.movl_ld_disp(12, M_DST - 64, 3); a.movl_ld_disp(12, M_N - 64, 4)
    cz = m.newlabel('cz'); a.tst_rr(4, 4); a.bt_lbl(cz); a.mov_imm(0, 0)
    clp = m.newlabel('clp'); a.deflabel(clp); a.movw_st(0, 3); a.add_imm(2, 3); a.dt(4); a.bf_lbl(clp)
    a.deflabel(cz); a.rts(); a.nop()
    m.pool_jump()
    # ---- s9_kmix: n output samples of a layer added into dst (drop-sample speed, mono mix, gain, clip) ----
    a.deflabel('s9_kmix'); a.stsl_pr()
    m.imm32(KB + 64, 12)                                                     # r12 = KB + 64
    a.movl_ld_disp(12, M_LBASE - 64, 1); a.movl_ld_disp(12, M_RBASE - 64, 2); a.movl_ld_disp(12, M_DST - 64, 3)
    a.movl_ld_disp(12, M_N - 64, 4); a.movl_ld_disp(12, M_I - 64, 5); a.movl_ld_disp(12, M_F - 64, 6)
    a.movl_ld_disp(12, M_SI - 64, 7); a.movl_ld_disp(12, M_SF - 64, 8); a.movl_ld_disp(12, M_END - 64, 9)
    a.movl_ld_disp(12, M_G - 64, 10); a.mov_imm(-16, 14)
    kend = m.newlabel('kend'); a.tst_rr(4, 4); m.bt_far(kend)
    kl = m.newlabel('kl'); a.deflabel(kl)
    a.mov_rm_rn(5, 0); a.shll(0); a.mov_rm_rn(1, 13); a.add_rr(0, 13); a.movw_ld(13, 11)
    km = m.newlabel('km'); a.tst_rr(2, 2); a.bt_lbl(km)
    a.mov_rm_rn(2, 13); a.add_rr(0, 13); a.movw_ld(13, 13); a.add_rr(13, 11); a.shar(11)
    a.deflabel(km)
    a.mul_l(10, 11); a.sts_macl(11); a.shad(14, 11)                          # x * gain >> 16
    a.movw_ld(3, 13); a.add_rr(13, 11)
    m.imm32(32767, 13); c1 = m.newlabel('kc1'); a.cmpgt(13, 11); a.bf_lbl(c1); a.mov_rm_rn(13, 11); a.deflabel(c1)
    m.imm32(-32768, 13); c2 = m.newlabel('kc2'); a.cmpgt(11, 13); a.bf_lbl(c2); a.mov_rm_rn(13, 11); a.deflabel(c2)
    a.movw_st(11, 3); a.add_imm(2, 3)
    a.add_rr(8, 6); m.imm32(0x10000, 13); na = m.newlabel('na'); a.cmphs(13, 6); a.bf_lbl(na); a.sub(13, 6); a.add_imm(1, 5)
    a.deflabel(na); a.add_rr(7, 5)
    a.cmphi(9, 5); kd = m.newlabel('kd'); a.bt_lbl(kd)                       # past the end of the region: layer done
    a.dt(4); kn = m.newlabel('kn'); a.bt_lbl(kn); a.bra_lbl(kl); a.nop()
    a.deflabel(kd); a.mov_imm(1, 0); a.movl_st_disp(0, M_DONE - 64, 12)
    a.deflabel(kn)
    a.deflabel(kend)
    a.movl_st_disp(5, M_I - 64, 12); a.movl_st_disp(6, M_F - 64, 12)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- s9_kfx: n samples in place: filter, sample & hold; pass 0 = peak, pass 1 = gain, bits, write ----
    a.deflabel('s9_kfx'); a.stsl_pr()
    m.imm32(KB, 14); m.imm32(KB + 64, 12)
    a.movl_ld_disp(12, M_DST - 64, 3); a.movl_ld_disp(12, M_N - 64, 4); a.mov_imm(-Q, 10)
    fend = m.newlabel('fend'); a.tst_rr(4, 4); m.bt_far(fend)
    fl = m.newlabel('fl'); a.deflabel(fl)
    a.movw_ld(3, 5)                                                          # r5 = x
    na_ = m.newlabel('naa'); a.movl_ld_disp(12, K_AA - 64, 0); a.tst_rr(0, 0); m.bt_far(na_)
    a.movl_ld_disp(14, K_X1, 6); a.mov_rm_rn(6, 7); a.shll(7); a.add_rr(5, 7); a.movl_ld_disp(14, K_X2, 0); a.add_rr(0, 7)
    a.movl_ld_disp(14, K_B0A, 0); a.mul_l(0, 7); a.sts_macl(7)
    a.movl_ld_disp(14, K_Y1, 8); a.movl_ld_disp(14, K_A1A, 0); a.mul_l(8, 0); a.sts_macl(0); a.sub(0, 7)
    a.movl_ld_disp(14, K_Y2, 9); a.movl_ld_disp(14, K_A2A, 0); a.mul_l(9, 0); a.sts_macl(0); a.sub(0, 7)
    a.shad(10, 7)                                                            # r7 = y (section 1)
    a.movl_st_disp(6, K_X2, 14); a.movl_st_disp(5, K_X1, 14); a.movl_st_disp(8, K_Y2, 14); a.movl_st_disp(7, K_Y1, 14)
    a.mov_rm_rn(8, 0); a.shll(0); a.add_rr(7, 0); a.add_rr(9, 0)            # section 2 input: y, y1, y2
    a.movl_ld_disp(14, K_B0B, 11); a.mul_l(11, 0); a.sts_macl(11)
    a.movl_ld_disp(14, K_Z1, 13); a.movl_ld_disp(14, K_A1B, 0); a.mul_l(13, 0); a.sts_macl(0); a.sub(0, 11)
    a.movl_ld_disp(14, K_Z2, 6); a.movl_ld_disp(14, K_A2B, 0); a.mul_l(6, 0); a.sts_macl(0); a.sub(0, 11)
    a.shad(10, 11)
    a.movl_st_disp(13, K_Z2, 14); a.movl_st_disp(11, K_Z1, 14); a.mov_rm_rn(11, 5)
    a.deflabel(na_)
    a.movl_ld_disp(14, K_ACC, 0); a.movl_ld_disp(14, K_STEP, 6); a.add_rr(6, 0)
    m.imm32(0x10000, 6); nh = m.newlabel('nh'); a.cmphs(6, 0); a.bf_lbl(nh); a.sub(6, 0); a.movl_st_disp(5, K_HELD, 14)
    a.deflabel(nh); a.movl_st_disp(0, K_ACC, 14); a.movl_ld_disp(14, K_HELD, 5)
    wr = m.newlabel('wr'); nx = m.newlabel('fnx')
    a.movl_ld_disp(12, K_PASS - 64, 0); a.tst_rr(0, 0); m.bf_far(wr)
    a.mov_rm_rn(5, 0); pz = m.newlabel('pz'); a.cmppz(0); a.bt_lbl(pz); a.neg(0, 0); a.deflabel(pz)
    a.movl_ld_disp(12, K_PEAK - 64, 6); npk = m.newlabel('npk'); a.cmphi(6, 0); a.bf_lbl(npk); a.movl_st_disp(0, K_PEAK - 64, 12)
    a.deflabel(npk); a.add_imm(2, 3); a.bra_lbl(nx); a.nop()
    a.deflabel(wr)
    a.mov_imm(0, 13); ps = m.newlabel('ps'); a.cmppz(5); a.bt_lbl(ps); a.neg(5, 5); a.mov_imm(1, 13); a.deflabel(ps)
    a.movl_ld_disp(14, K_G, 6); a.dmulu_l(6, 5); a.sts_mach(0); a.sts_macl(5); a.shll16(0); a.shlr16(5); a.or_rr(0, 5)
    a.movl_ld_disp(12, K_RND - 64, 0); a.add_rr(0, 5)
    m.imm32(32767, 6); cl = m.newlabel('fcl'); a.cmphi(6, 5); a.bf_lbl(cl); a.mov_rm_rn(6, 5); a.deflabel(cl)
    a.movl_ld_disp(12, K_MASK - 64, 0); a.and_rr(0, 5)
    sn = m.newlabel('sn'); a.tst_rr(13, 13); a.bt_lbl(sn); a.neg(5, 5); a.deflabel(sn)
    a.movw_st(5, 3); a.add_imm(2, 3)
    a.deflabel(nx)
    a.dt(4); fdn = m.newlabel('fdn'); a.bt_lbl(fdn); a.bra_lbl(fl); a.nop()
    a.deflabel(fdn)
    a.deflabel(fend)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tables ----
    def table(name, words):
        m.pool_jump()
        while (len(a.words) * 2 + a.base) & 3: a.nop()
        a.deflabel(name)
        for w in words: a._emit(w & 0xFFFF); a._emit((w >> 16) & 0xFFFF)
    skip = m.newlabel('tskip'); a.bra_lbl(skip); a.nop()
    m.pool_jump()
    while (len(a.words) * 2 + a.base) & 3: a.nop()
    a.deflabel('s9_rtab')
    for h_ in RTAB16: a._emit(h_)
    table('s9_semi', SEMI); table('s9_cent', CENT); table('s9_centi', CENTI)
    a.deflabel(skip)

def doit(m):
    a = m.a
    dx = m.newlabel('dx')
    ld(m, ST, 0); a.tst_rr(0, 0); m.bf_far(dx)
    ld(m, SEL, 9); a.tst_rr(9, 9); np_ = m.newlabel('np'); m.bf_far(np_)
    pg.emit_message(m, "HIT A PAD FIRST"); a.movl_pool(dx, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(np_)
    sti(m, PH_START, PHASE); sti(m, 0, DONEU); sti(m, 1, TOTU); sti(m, ST_JOB, ST)
    a.deflabel(dx)
    a.movl_pool('__s9_end', 2); a.jmp(2); a.nop(); m.pool_jump()
    subs(m)
    a.deflabel('__s9_end')
pg.on_doit(doit)

def ev_pad(m, hret0, hret1):
    a = m.a
    ld(m, ST, 0); a.tst_rr(0, 0); m.bf_far(hret0)                            # working: pads just play
    a.movl_ld_disp(8, 4, 0); a.tst_rr(0, 0); m.bt_far(hret0)
    a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 9); a.extu_w(9, 9); a.mov_imm(12, 0); a.cmphs(0, 9); m.bt_far(hret0)
    m.imm32(BANKOBJ, 1); a.movb_ld(1, 0); a.and_imm(3); a.mov_imm(12, 2); a.mul_l(2, 0); a.sts_macl(0); a.add_rr(0, 9)
    a.add_imm(1, 9); st(m, 9, PEND)
    a.movl_pool(hret0, 2); a.jmp(2); a.nop()
    m.pool_jump()
pg.on_event(0x38, ev_pad)

def cancel(m, hret1):
    a = m.a
    lv = m.newlabel('lv')
    ld(m, ST, 0); a.tst_rr(0, 0); m.bt_far(lv)
    ld(m, PHASE, 0); a.mov_imm(PH_CLEAR, 1); a.cmphs(1, 0); nc = m.newlabel('nc'); m.bf_far(nc)   # the copy exists
    ld(m, DST, 0); m.imm32(MGR, 1); a.movl_st_disp(0, 8, 1); m.imm32(MGR, 4); m.imm32(DELETE_CMD, 2); a.jsr(2); a.nop()
    ld(m, PREVCUR, 0); m.imm32(MGR, 1); a.movl_st_disp(0, 8, 1)
    a.deflabel(nc)
    sti(m, ST_IDLE, ST); pg.emit_message(m, "CANCELLED")
    a.movl_pool(hret1, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(lv)
pg.cancel_fn = cancel


# =====================================================================================================================
def ref_pow(c):
    c = max(-3600, min(6000, c)); k, r = divmod(c + 6000, 100)
    step = (SEMI[k] * CENT[r]) >> 16 & 0xFFFFFFFF
    inv = (SEMI[120 - k] * CENTI[r]) >> 16 & 0xFFFFFFFF
    return step, inv

def ref_convert(layers, rate_i, aa, bits_i, norm, pup):
    """bit-exact model. layers: dicts L, R (or None), start, end, cents, level"""
    def s32(v): v &= 0xFFFFFFFF; return v - (1 << 32) if v & 0x80000000 else v
    maxlv = max(l['level'] for l in layers) or 100
    nl = len(layers); prm = []
    for l in layers:
        st_, en = l['start'], min(l['end'], len(l['L']) - 1); st_ = min(st_, en)
        step, inv = ref_pow(l['cents'] + pup * 100)
        n = (((en - st_) * inv) >> 16 & 0xFFFFFFFF) + 1
        lv = l['level'] or maxlv
        prm.append((st_, en, step, n, (lv << 16) // (maxlv * nl)))
    nout = max(p[3] for p in prm); buf = [0] * nout
    for l, (st_, en, step, n, g) in zip(layers, prm):
        i, f, si, sf = st_, 0, step >> 16, step & 0xFFFF
        for j in range(n):
            x = l['L'][i]
            if l['R'] is not None: x = (x + l['R'][i]) >> 1
            y = s32(x * g) >> 16
            buf[j] = max(-32768, min(32767, buf[j] + y))
            f += sf
            if f >= 0x10000: f -= 0x10000; i += 1
            i += si
            if i > en: break
    stp, b0a, a1a, a2a, b0b, a1b, a2b = RTAB[rate_i]
    def run(pass_, G):
        x1 = x2 = y1 = y2 = z1 = z2 = held = 0; acc = 0x10000; peak = 0; out = []
        mask, rnd = {16: (0xFFFFFFFF, 0), 12: (0xFFFFFFF0, 8), 8: (0xFFFFFF00, 0x80)}[BITS[bits_i]]
        for x in buf:
            if aa:
                y = s32(b0a * (x + 2 * x1 + x2) - a1a * y1 - a2a * y2) >> Q
                w = s32(b0b * (y + 2 * y1 + y2) - a1b * z1 - a2b * z2) >> Q
                x2, x1, y2, y1, z2, z1 = x1, x, y1, y, z1, w; x = w
            acc += stp
            if acc >= 0x10000: acc -= 0x10000; held = x
            v = held
            if pass_ == 0: peak = max(peak, abs(v)); continue
            neg = v < 0; v = abs(v); v = (v * G) >> 16 & 0xFFFFFFFF; v += rnd
            v = min(v, 32767) & mask
            out.append(-v if neg else v)
        return peak, out
    G = 0x10000
    if norm:
        pk, _ = run(0, 0)
        if pk >= 16: G = 0x7FFF0000 // pk
    return run(1, G)[1]


def selftest(out):
    """emulator test on the WAV 'out' (this add-on alone, or S500.WAV with this page hosted)"""
    from validate import Harness
    from shasm import SHAsm
    from mpcmod import OS_PUSH_PAGE, HOOKS
    from pages import ENABLE_DOIT, ENABLE_CANCEL, SET_FOCUS
    from testkit import PageTest, FIRMWARE
    import random

    class OSTest(PageTest):
        """PageTest with the OS code image in SDRAM, so the REAL Copy / Delete / find-by-name run."""
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

    t = OSTest(pg, out); h, e = t.h, t.e
    R32, W32 = h.ram_r32, h.ram_w32
    def s16(v): return v - 0x10000 if v & 0x8000 else v
    rnd = random.Random(900)
    def mkblock(name, L, R=None, start=0, end=None):
        n = len(L); stereo = R is not None
        pcm = b''.join(struct.pack('<h', v) for v in L) + (b''.join(struct.pack('<h', v) for v in R) if stereo else b'')
        blk = h.add_sample(pcm, name=name)
        W32(blk + 0x18, n); W32(blk + 0x1C, n if stereo else 0); W32(blk + 0x20, start); W32(blk + 0x24, n - 1 if end is None else end)
        W32(blk + 0x28, start); h.ram_w16(blk + 0x92, 2 if stereo else 1); W32(blk + 0x98, 176400 if stereo else 88200)
        h.ram_w16(blk + 0x9C, 4 if stereo else 2)
        return blk
    def noise(n, amp): return [int(amp * math.sin(i * 0.37) * math.sin(i * 0.011)) + rnd.randint(-amp // 8, amp // 8) for i in range(n)]
    KL, KR = noise(3000, 9000), noise(3000, 7000)
    SUB = noise(1200, 4000)
    HAT = noise(2000, 12000)
    B_K = mkblock(b"KICK", KL, KR, start=100, end=2899)
    B_S = mkblock(b"SUB", SUB)
    B_H = mkblock(b"HAT", HAT)
    def list_end():
        p = R32(MGR)
        while R32(p): p += R32(p)
        return p
    h.ram_w16(PROG_IDX, 1); PROG = PROG_BASE + PROG_STRIDE
    def lay(p, l): return PROG + PAD_OFF + p * PAD_SIZE + l * LAYER
    def setpad(p, layers):
        for l in range(4):
            for i in range(0x18): e.wr(lay(p, l) + i, 0, 1)
            if l < len(layers) and layers[l]:
                n, lv, c, lo, hi = layers[l]
                for i, ch in enumerate(n): e.wr(lay(p, l) + i, ch, 1)
                e.wr(lay(p, l) + 0x11, lv, 1); e.wr(lay(p, l) + 0x12, lo, 1); e.wr(lay(p, l) + 0x13, hi, 1); e.wr(lay(p, l) + 0x14, c & 0xFFFF, 2)
    for p in range(48): setpad(p, [])
    setpad(4, [(b"KICK", 100, 0, 64, 127), (b"SUB", 50, -1200, 0, 63)])       # A05: two velocity layers
    setpad(5, [(b"HAT", 80, 0, 0, 127)])
    setpad(6, [(b"GONE", 100, 0, 0, 127)])                                     # sample not in memory
    def jobs(limit=3000):
        for k in range(limit):
            if t.slot(ST) == ST_IDLE: return k
            t.fire(1)
        raise AssertionError("job did not finish")
    def pcm_of(blk): return [s16(e.rd(blk + 0x100 + 2 * i, 2)) for i in range(R32(blk + 0x18))]
    orig = {b: bytes(e.rd(b + i, 1) for i in range(R32(b))) for b in (B_K, B_S, B_H)}
    t.open()
    assert t.label(PADL, 5) == " PAD?", repr(t.label(PADL, 5))
    t.doit(); assert t.msg() == "HIT A PAD FIRST", t.msg()
    assert [e.rd(s, 1) for s in (BITSI, NORM, RATEI, AA, PUP)] == [1, 1, RATE_DEF, 1, 0]
    SR.check(t, pg, RATE)
    print("  ok defaults 12BIT NRM:Y 26.04k AA:Y P+0; DO IT without a pad refused")
    # --- A05, two layers, P+12 ---
    t.pad(0, 4); t.fire(2); assert t.label(PADL, 5) == "  A05"
    e.wr(PUP, 12, 1); end0 = list_end(); cur0 = R32(MGR + 8)
    t.doit(); t.fire(1); pct = t.label(PADL, 5); fires = jobs()
    assert t.msg().startswith("DONE "), t.msg()
    D = R32(MGR + 8); assert D == end0 and list_end() == D + R32(D), "new sample = last block, list end after it"
    pad = lay(4, 0); name = bytes(e.rd(pad + i, 1) for i in range(16)).split(b'\0')[0]
    assert name == bytes(e.rd(D + 4 + i, 1) for i in range(16)).split(b'\0')[0] and name != b"KICK", name
    assert s16(e.rd(pad + 0x14, 2)) == -1200 and e.rd(pad + 0x11, 1) == 100 and (e.rd(pad + 0x12, 1), e.rd(pad + 0x13, 1)) == (0, 127)
    assert all(e.rd(lay(4, l), 1) == 0 for l in (1, 2, 3)), "layers 2-4 cleared"
    assert (e.rd(D + 0x92, 2), R32(D + 0x1C), R32(D + 0x98), e.rd(D + 0x9C, 2)) == (1, 0, 88200, 2), "mono header"
    exp = ref_convert([dict(L=KL, R=KR, start=100, end=2899, cents=0, level=100),
                       dict(L=SUB, R=None, start=0, end=1199, cents=-1200, level=50)], RATE_DEF, 1, 1, 1, 12)
    got = pcm_of(D)
    assert len(got) == len(exp) and got == exp, (len(got), len(exp), [i for i in range(min(len(got), len(exp))) if got[i] != exp[i]][:5])
    assert max(abs(v) for v in got) >= 32000 and all(v % 16 == 0 for v in got), "normalized, 12-bit grid"
    assert (R32(D + 0x20), R32(D + 0x24)) == (0, len(got) - 1)
    assert all(bytes(e.rd(b + i, 1) for i in range(R32(b))) == orig[b] for b in orig), "originals untouched"
    print("  ok A05 (KICK stereo + SUB -1 oct at 50%%, P+12): one mono sample %r, %d frames (= model, bit-exact), "
          "12-bit grid, normalized; layer 1 tune -12, range 0..127, layers 2-4 cleared; %d fires, showed %r" % (name, len(got), fires, pct))
    # --- A06, one layer, 16 bit, no filter, no normalize, 8.0k, P+0 ---
    e.wr(BITSI, 0, 1); e.wr(NORM, 0, 1); e.wr(AA, 0, 1); e.wr(RATEI, 1, 1); e.wr(PUP, 0, 1)
    t.pad(0, 5); t.fire(2); t.doit(); jobs(); D2 = R32(MGR + 8)
    exp2 = ref_convert([dict(L=HAT, R=None, start=0, end=1999, cents=0, level=80)], 1, 0, 0, 0, 0)
    assert pcm_of(D2) == exp2 and len(exp2) == 2000 and e.rd(lay(5, 0) + 0x11, 1) == 80
    held = [exp2[i] for i in range(0, 2000)]; runs = sum(1 for i in range(1, 2000) if held[i] != held[i - 1])
    assert 300 < runs < 400, runs                                              # 8 kHz: ~363 new values in 2000 frames
    print("  ok A06 16BIT NRM:N AA:N 8.0k: pure sample & hold (%d new values in 2000 frames), level kept" % runs)
    # --- 8 bit grid, 40k with filter ---
    e.wr(BITSI, 2, 1); e.wr(RATEI, len(RATES) - 1, 1); e.wr(AA, 1, 1); e.wr(NORM, 1, 1)
    W32(RATE['SIDE'], 0xFF)                                                    # (as a wheel move does: the test pokes the index)
    setpad(5, [(b"HAT", 80, 0, 0, 127)]); t.pad(0, 5); t.fire(2); t.doit(); jobs(); D3 = R32(MGR + 8)
    exp3 = ref_convert([dict(L=HAT, R=None, start=0, end=1999, cents=0, level=80)], len(RATES) - 1, 1, 2, 1, 0)
    assert pcm_of(D3) == exp3 and all(v % 256 == 0 for v in exp3)
    print("  ok 8BIT 40.0k AA:Y = model, 8-bit grid")
    # --- CANCEL while working: copy deleted, pad unchanged ---
    setpad(5, [(b"HAT", 80, 0, 0, 127)]); before = bytes(e.rd(lay(5, 0) + i, 1) for i in range(PAD_SIZE))
    end1 = list_end(); t.pad(0, 5); t.fire(2); t.doit(); t.fire(3); assert t.slot(ST) == ST_JOB
    t.cancel(); assert t.msg() == "CANCELLED" and t.slot(ST) == ST_IDLE, t.msg()
    assert list_end() == end1 and bytes(e.rd(lay(5, 0) + i, 1) for i in range(PAD_SIZE)) == before
    print("  ok CANCEL while working: the new sample is deleted, the pad is as it was")
    # --- empty pad, missing sample, memory full ---
    t.pad(0, 0); t.fire(2); t.doit(); jobs(); assert t.msg() == "NO SAMPLE", t.msg()
    t.pad(0, 6); t.fire(2); t.doit(); jobs(); assert t.msg() == "NO SAMPLE", t.msg()
    fill = list_end(); W32(fill, (R32(MGR + 4) - fill - 0x1000) & ~3); W32(fill + R32(fill), 0)   # memory almost full
    for i, c in enumerate(b"FILLER"): e.wr(fill + 4 + i, c, 1)
    end2 = list_end(); t.pad(0, 4); setpad(4, [(b"KICK", 100, 0, 0, 127)]); t.fire(2); t.doit(); jobs()
    assert t.msg() == "MEMORY FULL" and list_end() == end2 and e.rd(lay(4, 0), 1) == ord('K'), (t.msg(), hex(list_end()), hex(end2))
    W32(fill, 0)
    print("  ok empty pad / sample not in memory -> NO SAMPLE; not enough memory -> MEMORY FULL, nothing changed")
    assert t.pad(0, 7)[0] == 0, "pads still play"
    print("OK: S500 (pad) -> %s built and tested" % os.path.basename(out))


if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', '..', 'S500PAD.WAV'); pg.build_wav(out); selftest(out)
