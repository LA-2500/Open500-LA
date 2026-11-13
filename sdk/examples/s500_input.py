#!/usr/bin/env python3
"""S500 INPUT - sample the line input, based on the Akai S-series samplers like the S900 (not a re-creation of them): the take is converted to the S500 sound (anti-alias
filter, sample rate, bits, normalize -> ONE mono sample) as soon as you stop, then you hit an empty pad to keep it.
Same sound chain as S500 (s500.py), with the same pitch up (P+0..+24: the take is sped up, the pad is tuned down by it).

    python sdk/examples/s500_input.py          -> builds S500IN.WAV and tests it in the emulator

On the MPC500: copy S500IN.WAV to the CF card, LOAD it (after HUB.WAV), then MODE -> OTHER -> "S500 Input".
  row 0: "12B N:Y T-30 -18"  bits 16/12/8 (default 12) . normalize (default Y) . threshold TOFF, T-60..T -3 dB in 3 dB
                             steps (default -30) . input level (dB below full scale) when ready, else the status:
                             WAIT (armed, waiting for the threshold) / REC / 45% (converting) / PAD? (hit an empty pad)
  row 1: "26.04k AA:Y P+12"  rate 7.5k..40.0k in 0.5k steps + 26.04k (default) . anti-alias filter (default Y) . pitch up
Flow (works with the sequencer stopped or running: S500 Input never touches the transport):
  DO IT (READY)  -> the OS sampler is armed (100 ms pre-roll); recording starts when the input level passes the
                    threshold (TOFF: at once). The OS's own threshold start only runs on its Record page (emulator
                    trace of the sampler tick, 2026-11-02), so S500 Input compares the sampler meter itself.
  DO IT (WAIT)   -> recording starts now (threshold skipped)
  DO IT (REC)    -> recording stops; the take is converted a piece per fire ("  45%"), then "HIT BLANK PAD"
                    (the OS sampler also stops by itself when the memory is full: "MEMORY FULL", the take is kept)
  hit an EMPTY pad -> the take is kept as S500INnn on that pad (layer 1; full pads are refused: "PAD NOT EMPTY")
  CANCEL         -> WAIT / REC: the take is stopped and deleted. Converting / PAD?: the take is deleted. READY: leaves.
  While the page is open the OS sampler stays entered (input converter on) so the meter works; leaving it gives it back.
  The recording level is the OS's (Record page). Start / end points: as the OS sampler sets them.
Sound (S500 steps 2-5, bit-exact with ref_convert below):
  1. stereo take -> mono, (L + R) / 2, in place (a mono take is used as it is); pitch up: drop-sample speed-up in place
     (2^(P/12), as S500), start / end / loop scaled, layer 1 of the pad tuned -P semitones
  2. anti-alias: 4th-order Butterworth low-pass at 0.45 x rate (two biquads, Q13), optional
  3. sample & hold at the rate (phase accumulator, exact fractional step: 26.04k = 26040 Hz)
  4. normalize (optional): peak of 1-3 -> full scale, applied before 5 so the bits are used fully
  5. bits: rounding to 12 or 8 bits (16 = untouched)
  Before Keep the block header is made mono (+0x1C 0, +0x92 1 channel, +0x98 88200, +0x9C 2) and the block is cut to
  the mono PCM (the R half is given back to free memory). The OS Keep always normalizes START..END to full scale
  (emulator trace 2026-11-02: it would undo NRM:N and push the 12/8-bit grid off by up to 0.05%), so the sample at
  START is set to 32767 during the Keep call (gain exactly 1) and put back right after it. Then LOOP_GUARD.

OS addresses (MPC500 OS 1.31), all from Audio Track (audio_track.py, HW 2026-11-01) and S500 (s500.py):
  SMP 0x8849D114 sampler object: +8 entered, +0x0C mono, +0x24 state (0 idle, 1 pre-roll, 2 waiting for threshold,
      3 recording), +0x28 name16 used by Keep, +0x34/+0x38 meters L/R, +0x88 memory full, +0x90 the take's block.
  enter 0x884EC06E, exit 0x884EC0EE, arm 0x884EC21E (r0 = 0 ok), go-record 0x884EC2AE (skips the threshold),
  stop 0x884EC2F0 (block +0x18 length, +0x1C frames per channel, PCM from +0x100: L then R), keep 0x884EC362
  (r5 = pad + 1), discard 0x884EC3C8, tick 0x884EC3D8 (OS main loop). Find by name 0x884C5090 (MGR 0x8848ED30),
  LOOP_GUARD 0x884C4F04 (Sample FX). Program/pads/bank: SDK README (as S500 / Audio Track).
  Heavy loops run through the cached P1 alias of our code after invalidating its cache lines (Sample FX, S500).
"""
import sys, os, struct, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import page_or_addon, ld, st, sti
from mpcmod import EVQ

SMP = 0x8849D114
SMP_ENTER, SMP_EXIT, SMP_ARM, SMP_GOREC = 0x884EC06E, 0x884EC0EE, 0x884EC21E, 0x884EC2AE
SMP_STOP, SMP_KEEP, SMP_DISCARD, SMP_TICK = 0x884EC2F0, 0x884EC362, 0x884EC3C8, 0x884EC3D8
MGR, FINDNAME, LOOP_GUARD = 0x8848ED30, 0x884C5090, 0x884C4F04
PROG_BASE, PROG_IDX, PROG_STRIDE = 0x8837B5AC, 0x8847C0E2, 0x1F98
PAD_OFF, PAD_SIZE = 0x18, 0xA4
BANKOBJ = 0x8849C98C
DB_STEP = 58409          # 10^(-1/20) in Q16 (Audio Track meter)
CHUNK_M, CHUNK = 4096, 2048
CACHE_SPAN = 0x900
NAME = "S500IN"
CODEC_W, IXLIT, IN_WR = 0x884C6722, 0x884ECCC4, 0xA5017020   # codec write(reg, val); INPUT FX literal (Audio Track)
REG4_OS, REG4_THRU, REFRESH = 0x12, 0x1A, 32

# ---- settings tables (as S500) ----
import s500_rate as SR
RATES, RATE_DEF = SR.RATES, SR.RATE_DEF                   # 7.00 .. 44.10k (s500_rate.py)
#   # x0.01 kHz, 7.50 .. 40.00
BITS_OPTS, BITS = ["16B", "12B", " 8B"], [16, 12, 8]
PITCH_OPTS = ["%+3d" % n if n else " +0" for n in range(25)]               # as S500
PTAB = [round(65536 * 2 ** (n / 12)) for n in range(1, 25)]             # speed (Q16) of P+1 .. P+24
THR_DB = list(range(-60, 0, 3))                                          # threshold -60 .. -3 dB, 3 dB steps
THR_OPTS = ["TOFF"] + ["T%3d" % d for d in THR_DB]
THR_DEF = 1 + THR_DB.index(-30)
THRV = [0] + [round(32767 * 10 ** (d / 20)) for d in THR_DB]            # sampler meter units (peak sample)
Q = 13
def biquads(rate100):
    fs = 44100.0; fc = min(0.45 * rate100 * 10, 0.45 * fs); w = 2 * math.pi * fc / fs; c, s = math.cos(w), math.sin(w)
    out = []
    for qv in (0.5411961, 1.3065630):
        al = s / (2 * qv); a0 = 1 + al
        out += [round((1 - c) / 2 / a0 * (1 << Q)), round(-2 * c / a0 * (1 << Q)), round((1 - al) / a0 * (1 << Q))]
    return out
RTAB = [[(r << 16) // 4410] + biquads(r) for r in RATES]
assert all(-32768 <= c < 32768 for row in RTAB for c in row[1:]), "coefficients fit s16"
RTAB16 = [h for row in RTAB for h in ((row[0] & 0xFFFF), (row[0] >> 16)) + tuple(c & 0xFFFF for c in row[1:])]   # 16 B / rate

pg = page_or_addon("s500in1", open_press=None)
pg.other_menu("S500 Input")
pg.far_hend = True
pg.msg_fires = 0x600
BITSI = pg.choice("", 0, 0, BITS_OPTS, default=1)
NORM = pg.choice(" N:", 3, 0, ["N", "Y"], default=1)
THR = pg.choice(" ", 7, 0, THR_OPTS, default=THR_DEF)
METER = pg.dlabel(12, 0, 4)
RATE = SR.fields(pg); RATEI = RATE['I']                 # "26.04k": wheel by digit, slider 6 rates (s500_rate.py)
AA = pg.choice(" AA:", 6, 1, ["N", "Y"], default=1)
PUP = pg.choice(" P", 11, 1, PITCH_OPTS, default=0)
pg.message()
(ST, ENTERED, FORCE, FULLF, NCH, BLK, LEN, STRIDE, POS, PHASE, DONEU, TOTU, NAMEK, PADN, MD, MCNT, GADDR, GVAL) = \
    [pg.alloc_slot() for _ in range(18)]
pg.host.defaults[MD] = 48
A4, TCNT = [pg.alloc_slot() for _ in range(2)]          # codec reg 4 we applied (0 = the OS's), refresh counter
TXT =[pg.alloc_slot() for _ in range(2)][0]            # text being built (8 bytes)
for _s in (TXT, TXT + 4): del pg.host.defaults[_s]
KP = [pg.alloc_slot() for _ in range(32)]               # kernel parameters (S500 layout)
for _s in KP: del pg.host.defaults[_s]
K_X1, K_X2, K_Y1, K_Y2, K_Z1, K_Z2, K_ACC, K_HELD, K_B0A, K_A1A, K_A2A, K_B0B, K_A1B, K_A2B, K_STEP, K_G = range(0, 64, 4)
K_MASK, K_RND, K_PEAK, K_AA, K_PASS = range(64, 84, 4)
M_LBASE, M_RBASE, M_DST, M_N = range(84, 100, 4)
M_IDX, M_FRAC, M_STEP, M_SLEN = range(100, 116, 4)
KB = KP[0]
ST_IDLE, ST_ARM, ST_REC, ST_CONV, ST_PAD = range(5)
PH_MONO, PH_PEAK, PH_WRITE, PH_PITCH = range(4)
STATUS = {ST_ARM: "WAIT", ST_REC: " REC", ST_PAD: "PAD?"}                  # in the place of the level

def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()
def osc(m, fn): m.imm32(fn, 3); m.a.jsr(3); m.a.nop()
def smp4(m): m.imm32(SMP, 4)
def rd(m, addr, r): m.imm32(addr, r); m.a.movl_ld(r, r)
def rdb(m, addr, r): m.imm32(addr, r); m.a.movb_ld(r, r); m.a.extu_b(r, r)
def kst(m, r, off): m.imm32(KB + off, 1); m.a.movl_st(r, 1)              # (uses r1)
def kld(m, off, r): m.imm32(KB + off, r); m.a.movl_ld(r, r)
def cached_call(m, label):
    m.a.movl_pool(label, 2); m.imm32(0xDFFFFFFF, 3); m.a.and_rr(3, 2); m.a.jsr(2); m.a.nop()
def jmpl(m, lbl): m.a.movl_pool(lbl, 2); m.a.jmp(2); m.a.nop()

def fire(m):
    a = m.a
    # page open, no take: keep the OS sampler entered (input converter on) for the meter
    ld(m, ST, 0); a.tst_rr(0, 0); ent = m.newlabel('ent'); m.bf_far(ent)
    rd(m, SMP + 8, 0); a.tst_rr(0, 0); m.bf_far(ent)
    smp4(m); osc(m, SMP_ENTER); sti(m, 1, ENTERED)
    a.deflabel(ent)
    call(m, 'si_mon')
    call(m, 'si_meter')
    call(m, 'si_disp')
    call(m, 'si_thru')
pg.on_fire(fire)
SR.attach(pg, RATE)

def inactive(m):
    a = m.a
    call(m, 'si_mon')                       # a take that is recording / converting goes on if you leave the page
    # page left and nothing recording: give the sampler back if we entered it
    ix = m.newlabel('ix')
    ld(m, ENTERED, 0); a.tst_rr(0, 0); m.bt_far(ix)
    ld(m, ST, 0); a.mov_imm(ST_ARM, 1); a.cmpeq(1, 0); m.bt_far(ix); a.mov_imm(ST_REC, 1); a.cmpeq(1, 0); m.bt_far(ix)
    smp4(m); osc(m, SMP_EXIT); sti(m, 0, ENTERED)
    a.deflabel(ix)
    call(m, 'si_thru')
    SR.leave(m, RATE)
pg.on_inactive(inactive)

def subs(m):
    a = m.a
    def text(s):
        for ch in s: a.mov_imm(ord(ch), 0); a.movb_st(0, 12); a.add_imm(1, 12)

    # ---- si_mon: follow the take (fire and inactive) ----
    a.deflabel('si_mon'); a.stsl_pr()
    ret, s1, s2, s3 = m.newlabel('ret'), m.newlabel('s1'), m.newlabel('s2'), m.newlabel('s3')
    ld(m, ST, 0); a.mov_imm(ST_ARM, 1); a.cmpeq(1, 0); m.bt_far(s1)
    a.mov_imm(ST_REC, 1); a.cmpeq(1, 0); m.bt_far(s2)
    a.mov_imm(ST_CONV, 1); a.cmpeq(1, 0); m.bt_far(s3)
    a.bra_lbl(ret); a.nop()
    m.pool_jump()
    # armed, pre-roll filled (state 2): DO IT pressed, threshold OFF or the input meter above the threshold -> record.
    # (The OS threshold start runs only on its Record page: on any other page the sampler waits forever.)
    a.deflabel(s1)
    torec, gone, go = m.newlabel('torec'), m.newlabel('gone'), m.newlabel('go')
    rd(m, SMP + 0x24, 1); a.mov_imm(3, 0); a.cmpeq(0, 1); m.bt_far(torec)
    a.tst_rr(1, 1); m.bt_far(gone)
    a.mov_imm(2, 0); a.cmphs(0, 1); m.bf_far(ret)
    ld(m, FORCE, 0); a.tst_rr(0, 0); m.bf_far(go)
    rdb(m, THR, 5); a.tst_rr(5, 5); m.bt_far(go)
    a.shll2(5); a.movl_pool('si_thr', 2); a.add_rr(2, 5); a.movl_ld(5, 5)   # r5 = threshold (meter units)
    rd(m, SMP + 0x34, 4); rd(m, SMP + 0x38, 6); a.cmphi(4, 6); mx_ = m.newlabel('mx'); a.bf_lbl(mx_); a.mov_rm_rn(6, 4)
    a.deflabel(mx_); a.cmphi(5, 4); m.bf_far(ret)                            # level > threshold?
    a.deflabel(go); smp4(m); osc(m, SMP_GOREC)
    a.deflabel(torec); sti(m, ST_REC, ST); a.bra_lbl(ret); a.nop()
    a.deflabel(gone); call(m, 'si_abort'); pg.emit_message(m, "TAKE CANCELLED"); a.bra_lbl(ret); a.nop()
    m.pool_jump()
    # recording: the sampler stopped (memory full / someone else) -> finish
    a.deflabel(s2)
    fin = m.newlabel('fin')
    rd(m, SMP + 0x24, 0); a.mov_imm(3, 1); a.cmpeq(1, 0); m.bf_far(fin)
    rd(m, SMP + 0x88, 0); a.tst_rr(0, 0); m.bt_far(ret)
    a.deflabel(fin); call(m, 'si_finish'); a.bra_lbl(ret); a.nop()
    a.deflabel(s3); call(m, 'si_job')
    a.deflabel(ret); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- si_thru: input thru while WE have the sampler entered (HW 2026-11-07: "can't hear the inputs" on S500 Input).
    # Entering the sampler turns the codec's analog bypass off (reg 4 0x12) and the OS monitors in software only per
    # the Record page Monitor, which MASTER forces OFF -> silence. As Audio Track: reg 4 = 0x1A (analog bypass, zero
    # latency) while entered, except when MASTER's INPUT FX processes the input (it monitors the effected input itself;
    # INPUT FX on = sampler literal 0x884ECCC4 is not 0xA5017020). Refreshed every 32 fires; given back (0x12) on exit.
    a.deflabel('si_thru'); a.stsl_pr()
    tx, tap, toff = m.newlabel('tx'), m.newlabel('tap'), m.newlabel('toff')
    ld(m, ENTERED, 0); a.tst_rr(0, 0); m.bt_far(toff)
    a.mov_imm(REG4_THRU, 9)
    rd(m, IXLIT, 0); m.imm32(IN_WR, 1); a.cmpeq(1, 0); nf = m.newlabel('nf'); a.bt_lbl(nf); a.mov_imm(REG4_OS, 9)
    a.deflabel(nf)
    ld(m, A4, 0); a.cmpeq(9, 0); m.bf_far(tap)
    ld(m, TCNT, 0); a.add_imm(1, 0); st(m, 0, TCNT); a.mov_imm(REFRESH, 1); a.cmphs(1, 0); m.bf_far(tx)
    a.deflabel(tap)
    sti(m, 0, TCNT); st(m, 9, A4); a.mov_imm(4, 4); a.mov_rm_rn(9, 5); osc(m, CODEC_W); a.bra_lbl(tx); a.nop()
    a.deflabel(toff)
    ld(m, A4, 0); a.tst_rr(0, 0); m.bt_far(tx)
    sti(m, 0, A4); a.mov_imm(4, 4); a.mov_imm(REG4_OS, 5); osc(m, CODEC_W)
    a.deflabel(tx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- si_finish: stop the sampler, start the conversion ----
    a.deflabel('si_finish'); a.stsl_pr()
    fx, emp = m.newlabel('fx'), m.newlabel('emp')
    rd(m, SMP + 0x88, 0); st(m, 0, FULLF)
    rd(m, SMP + 0x0C, 0); a.tst_rr(0, 0); a.mov_imm(1, 0); mo = m.newlabel('mo'); a.bf_lbl(mo); a.mov_imm(2, 0)
    a.deflabel(mo); st(m, 0, NCH)                                            # channels (+0x0C = 0: stereo)
    smp4(m); osc(m, SMP_STOP)
    rd(m, SMP + 0x90, 13); st(m, 13, BLK); a.movl_ld_disp(13, 0x1C, 0); st(m, 0, STRIDE)
    a.movl_ld_disp(13, 0x18, 0); st(m, 0, LEN)
    a.tst_rr(0, 0); m.bt_far(emp)
    call(m, 'si_exit')
    # units for the percentage: (mono) + (peak) + write
    ld(m, NCH, 2); a.add_imm(-1, 2); rdb(m, NORM, 3); a.add_rr(3, 2); a.add_imm(1, 2)
    rdb(m, PUP, 3); a.tst_rr(3, 3); np_ = m.newlabel('np'); a.bt_lbl(np_); a.add_imm(1, 2); a.deflabel(np_)
    ld(m, LEN, 0); a.mul_l(2, 0); a.sts_macl(0); st(m, 0, TOTU); sti(m, 0, DONEU); sti(m, 0, POS)
    call(m, 'icinv')
    sti(m, ST_CONV, ST)
    ld(m, NCH, 0); a.mov_imm(2, 1); a.cmpeq(1, 0); st_ = m.newlabel('st'); m.bt_far(st_)
    call(m, 'si_afterm'); a.bra_lbl(fx); a.nop()
    a.deflabel(st_); sti(m, PH_MONO, PHASE)
    a.deflabel(fx)
    ld(m, FULLF, 0); a.tst_rr(0, 0); fz = m.newlabel('fz'); m.bt_far(fz); pg.emit_message(m, "MEMORY FULL")
    a.bra_lbl(fz); a.nop()
    m.pool_jump()
    a.deflabel(emp); smp4(m); osc(m, SMP_DISCARD); call(m, 'si_exit'); sti(m, ST_IDLE, ST)
    pg.emit_message(m, "NOTHING RECORDED")
    a.deflabel(fz); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- si_abort: stop + delete the take; si_exit: leave the sampler as we found it ----
    a.deflabel('si_abort'); a.stsl_pr()
    smp4(m); osc(m, SMP_STOP); smp4(m); osc(m, SMP_DISCARD)
    call(m, 'si_exit'); sti(m, ST_IDLE, ST)
    a.ldsl_pr(); a.rts(); a.nop()
    a.deflabel('si_exit'); a.stsl_pr()                                     # page open: stay entered for the meter
    ne = m.newlabel('ne')
    ld(m, ENTERED, 0); a.tst_rr(0, 0); m.bt_far(ne)
    m.imm32(EVQ + 0x68, 1); a.movl_ld(1, 0); m.imm32(pg.addr['page'], 1); a.cmpeq(1, 0); m.bt_far(ne)
    smp4(m); osc(m, SMP_EXIT); sti(m, 0, ENTERED)
    a.deflabel(ne); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- si_job: one step of the conversion per call ----
    a.deflabel('si_job'); a.stsl_pr()
    jx = m.newlabel('jx')
    labs = {p: m.newlabel('ph%d' % p) for p in range(4)}
    ld(m, PHASE, 0)
    for p in range(4):
        nx = m.newlabel('pn'); a.mov_imm(p, 1); a.cmpeq(1, 0); a.bf_lbl(nx); jmpl(m, labs[p]); a.deflabel(nx)
    jmpl(m, jx)
    m.pool_jump()
    # == MONO: a chunk of L = (L + R) / 2 ==
    a.deflabel(labs[PH_MONO])
    ld(m, LEN, 2); ld(m, POS, 3); a.sub(3, 2); m.imm32(CHUNK_M, 0); a.cmphi(0, 2); c1 = m.newlabel('c1'); a.bf_lbl(c1); a.mov_rm_rn(0, 2); a.deflabel(c1)
    kst(m, 2, M_N)
    ld(m, BLK, 4); a.mov_rm_rn(3, 0); a.shll(0); a.add_rr(0, 4); m.imm32(0x100, 0); a.add_rr(0, 4); kst(m, 4, M_LBASE)
    ld(m, STRIDE, 0); a.shll(0); a.add_rr(4, 0); kst(m, 0, M_RBASE)
    a.add_rr(2, 3); st(m, 3, POS); ld(m, DONEU, 0); a.add_rr(2, 0); st(m, 0, DONEU)
    cached_call(m, 'si_kmono')
    ld(m, LEN, 0); ld(m, POS, 3); a.cmpeq(0, 3); m.bf_far(jx)
    call(m, 'si_afterm'); jmpl(m, jx)
    m.pool_jump()
    # == PITCH: a chunk of the speed-up, in place ==
    a.deflabel(labs[PH_PITCH])
    m.imm32(CHUNK, 0); kst(m, 0, M_N); ld(m, DONEU, 2); a.add_rr(0, 2); st(m, 2, DONEU)
    cached_call(m, 'si_kpitch')
    kld(m, M_IDX, 0); ld(m, LEN, 2); a.cmphs(2, 0); m.bf_far(jx)
    call(m, 'si_pend'); call(m, 'si_fxstart'); jmpl(m, jx)
    m.pool_jump()
    # == PEAK / WRITE: a chunk in place (S500) ==
    for ph in (PH_PEAK, PH_WRITE):
        a.deflabel(labs[ph])
        ld(m, LEN, 2); ld(m, POS, 3); a.sub(3, 2); m.imm32(CHUNK, 0); a.cmphi(0, 2); c3 = m.newlabel('c3'); a.bf_lbl(c3); a.mov_rm_rn(0, 2); a.deflabel(c3)
        kst(m, 2, M_N)
        ld(m, BLK, 4); a.mov_rm_rn(3, 0); a.shll(0); a.add_rr(0, 4); m.imm32(0x100, 0); a.add_rr(0, 4); kst(m, 4, M_DST)
        a.add_rr(2, 3); st(m, 3, POS); ld(m, DONEU, 0); a.add_rr(2, 0); st(m, 0, DONEU)
        cached_call(m, 'si_kfx')
        ld(m, LEN, 0); ld(m, POS, 3); a.cmpeq(0, 3); m.bf_far(jx)
        if ph == PH_PEAK:                                                    # gain = 32767 / peak (Q16)
            kld(m, K_PEAK, 5); m.imm32(0x10000, 0); a.mov_imm(16, 1); a.cmphs(1, 5); pk = m.newlabel('pk'); a.bf_lbl(pk)
            m.imm32(0x7FFF0000, 4); pg._hcall(m, '__udiv')
            a.deflabel(pk); a.mov_rm_rn(0, 13)
            call(m, 'si_fxinit'); sti(m, 0, POS); sti(m, PH_WRITE, PHASE)
            kst(m, 13, K_G); a.mov_imm(1, 0); kst(m, 0, K_PASS)
        else:
            sti(m, ST_PAD, ST); pg.emit_message(m, "HIT BLANK PAD")
        jmpl(m, jx)
        m.pool_jump()
    a.deflabel(jx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- si_afterm: the take is mono: pitch up if asked, else the sound chain ----
    a.deflabel('si_afterm'); a.stsl_pr()
    pu, am = m.newlabel('pu'), m.newlabel('am')
    rdb(m, PUP, 6); a.tst_rr(6, 6); m.bf_far(pu)
    call(m, 'si_fxstart'); a.bra_lbl(am); a.nop()
    m.pool_jump()
    a.deflabel(pu)
    ld(m, BLK, 4); m.imm32(0x100, 0); a.add_rr(0, 4); kst(m, 4, M_DST); kst(m, 4, M_LBASE)
    a.mov_imm(0, 0); kst(m, 0, M_IDX); kst(m, 0, M_FRAC); ld(m, LEN, 0); kst(m, 0, M_SLEN)
    a.mov_rm_rn(6, 4); a.add_imm(-1, 4); a.shll2(4); a.movl_pool('si_ptab', 2); a.add_rr(2, 4); a.movl_ld(4, 0); kst(m, 0, M_STEP)
    sti(m, PH_PITCH, PHASE)
    a.deflabel(am); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- si_pend: speed-up done: new length, START / END / LOOP scaled (x 1 / speed), percentage for what is left ----
    a.deflabel('si_pend'); a.stsl_pr()
    kld(m, M_STEP, 5); m.imm32(0xFFFFFFFF, 4); pg._hcall(m, '__udiv'); a.mov_rm_rn(0, 12)     # r12 = 1 / speed (Q16)
    kld(m, M_DST, 8); ld(m, BLK, 13); a.sub(13, 8); m.imm32(0x100, 0); a.sub(0, 8); a.shlr(8)  # r8 = new length
    st(m, 8, LEN); a.movl_st_disp(8, 0x18, 13); a.mov_rm_rn(8, 11); a.add_imm(-1, 11)
    for off in (0x20, 0x24, 0x28):
        a.movl_ld_disp(13, off, 0); a.dmulu_l(12, 0); a.sts_mach(0); a.sts_macl(2); a.shll16(0); a.shlr16(2); a.or_rr(2, 0)
        kc = m.newlabel('kc'); a.cmphi(11, 0); a.bf_lbl(kc); a.mov_rm_rn(11, 0); a.deflabel(kc); a.movl_st_disp(0, off, 13)
    rdb(m, NORM, 2); a.add_imm(1, 2); a.mul_l(2, 8); a.sts_macl(0); ld(m, DONEU, 2); a.add_rr(2, 0); st(m, 0, TOTU)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- si_fxstart: filter state, POS = 0, first pass (peak with NRM:Y, else write at gain 1) ----
    a.deflabel('si_fxstart'); a.stsl_pr()
    call(m, 'si_fxinit'); sti(m, 0, POS)
    m.imm32(0x10000, 0); kst(m, 0, K_G)
    rdb(m, NORM, 0); a.tst_rr(0, 0); wp = m.newlabel('wp'); m.bt_far(wp)
    sti(m, PH_PEAK, PHASE); a.mov_imm(0, 0); kst(m, 0, K_PASS); fe = m.newlabel('fe'); a.bra_lbl(fe); a.nop()
    a.deflabel(wp); sti(m, PH_WRITE, PHASE); a.mov_imm(1, 0); kst(m, 0, K_PASS)
    a.deflabel(fe); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- si_fxinit: filter / hold state, coefficients and step of the rate, bits (S500) ----
    a.deflabel('si_fxinit'); a.stsl_pr()
    a.mov_imm(0, 0)
    for off in (K_X1, K_X2, K_Y1, K_Y2, K_Z1, K_Z2, K_HELD, K_PEAK): kst(m, 0, off)
    m.imm32(0x10000, 0); kst(m, 0, K_ACC)
    rdb(m, RATEI, 4); a.shll2(4); a.shll2(4); a.movl_pool('si_rtab', 2); a.add_rr(4, 2)    # 16 bytes per rate
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

    # ---- si_assign: r9 = pad 0..47. Keep, then mono header + cut the block ----
    a.deflabel('si_assign'); a.stsl_pr()
    st(m, 9, PADN); sti(m, 1, NAMEK)
    nl = m.newlabel('nl'); uq = m.newlabel('uq'); a.deflabel(nl)
    m.imm32(SMP + 0x28, 12)
    text(NAME)
    ld(m, NAMEK, 4); a.mov_imm(10, 5); pg._hcall(m, '__udiv')
    a.add_imm(0x30, 0); a.movb_st(0, 12); a.add_imm(1, 12); a.mov_rm_rn(1, 0); a.add_imm(0x30, 0); a.movb_st(0, 12)
    a.add_imm(1, 12); a.mov_imm(0, 0)
    for _ in range(16 - len(NAME) - 2): a.movb_st(0, 12); a.add_imm(1, 12)
    m.imm32(MGR, 4); m.imm32(SMP + 0x28, 5); osc(m, FINDNAME); a.tst_rr(0, 0); m.bt_far(uq)
    ld(m, NAMEK, 0); a.add_imm(1, 0); st(m, 0, NAMEK); a.mov_imm(100, 1); a.cmpeq(1, 0); m.bf_far(nl)
    sti(m, 99, NAMEK)
    m.pool_jump()
    a.deflabel(uq)
    # mono header + cut first (Keep then sees only the mono PCM), then Keep with a full-scale guard sample at START:
    # the OS Keep normalizes START..END to 32767 (emulator trace, 2026-11-02); with the guard its gain is exactly 1.
    hx = m.newlabel('hx')
    dcut = m.newlabel('dcut'); ld(m, NCH, 0); a.mov_imm(2, 1); a.cmpeq(1, 0); m.bt_far(dcut)
    rdb(m, PUP, 0); a.tst_rr(0, 0); m.bt_far(hx)                             # (a sped-up mono take is shorter: cut it too)
    a.deflabel(dcut); ld(m, BLK, 13)
    a.mov_imm(0, 0); a.movl_st_disp(0, 0x1C, 13)
    m.imm32(0x92, 1); a.add_rr(13, 1); a.mov_imm(1, 0); a.movw_st(0, 1)
    m.imm32(0x98, 1); a.add_rr(13, 1); m.imm32(88200, 0); a.movl_st(0, 1)
    m.imm32(0x9C, 1); a.add_rr(13, 1); a.mov_imm(2, 0); a.movw_st(0, 1)
    ld(m, LEN, 4); a.shll(4); m.imm32(0x100 + 3, 0); a.add_rr(0, 4); a.mov_imm(-4, 0); a.and_rr(0, 4)
    a.movl_st(4, 13); a.mov_rm_rn(13, 0); a.add_rr(4, 0); a.mov_imm(0, 1); a.movl_st(1, 0)   # size, list end after it
    a.deflabel(hx)
    ld(m, BLK, 13); a.movl_ld_disp(13, 0x20, 0); a.shll(0); a.add_rr(13, 0); m.imm32(0x100, 1); a.add_rr(1, 0)
    st(m, 0, GADDR); a.movw_ld(0, 2); st(m, 2, GVAL)                           # save the sample at START
    ld(m, GADDR, 0); m.imm32(32767, 2); a.movw_st(2, 0)
    smp4(m); ld(m, PADN, 5); a.add_imm(1, 5); osc(m, SMP_KEEP)
    ld(m, GADDR, 0); ld(m, GVAL, 2); a.movw_st(2, 0)                           # and put it back
    ld(m, BLK, 4); osc(m, LOOP_GUARD)
    ld(m, PADN, 4); m.imm32(PAD_SIZE, 0); a.mul_l(0, 4); a.sts_macl(4)                   # layer 1 of the pad: tuned down by the pitch up
    m.imm32(PROG_IDX, 1); a.movw_ld(1, 0); a.extu_w(0, 0); m.imm32(PROG_STRIDE, 1); a.mul_l(1, 0); a.sts_macl(0); a.add_rr(0, 4)
    m.imm32(PROG_BASE + PAD_OFF + 0x14, 0); a.add_rr(0, 4)
    rdb(m, PUP, 0); a.mov_imm(-100, 1); a.mul_l(1, 0); a.sts_macl(0); a.movw_st(0, 4)
    sti(m, ST_IDLE, ST)
    m.imm32(pg.addr['msgt'], 12)
    text("ASSIGNED TO ")
    ld(m, PADN, 4); a.mov_imm(12, 5); pg._hcall(m, '__udiv')
    a.add_imm(ord('A'), 0); a.movb_st(0, 12); a.add_imm(1, 12); a.add_imm(1, 1); a.mov_rm_rn(1, 4); a.mov_imm(10, 5)
    pg._hcall(m, '__udiv')
    a.add_imm(0x30, 0); a.movb_st(0, 12); a.add_imm(1, 12); a.add_imm(0x30, 1); a.mov_rm_rn(1, 0); a.movb_st(0, 12)
    a.add_imm(1, 12); a.mov_imm(ord(' '), 0); a.movb_st(0, 12); a.add_imm(1, 12); a.mov_imm(0, 0); a.movb_st(0, 12)
    pg._hcall(m, '__showmsg')
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- si_put: copy r3 chars r4 -> r5 where they differ; redraw only if something changed. r0, r3-r7 ----
    a.deflabel('si_put')
    a.mov_imm(0, 6)
    cl, cs = m.newlabel('cl'), m.newlabel('cs')
    a.deflabel(cl); a.movb_ldinc(4, 0); a.movb_ld(5, 7); a.cmpeq(0, 7); a.bt_lbl(cs); a.movb_st(0, 5); a.mov_imm(1, 6)
    a.deflabel(cs); a.add_imm(1, 5); a.dt(3); a.bf_lbl(cl)
    px = m.newlabel('px'); a.tst_rr(6, 6); a.bt_lbl(px); m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(px); a.rts(); a.nop()
    m.pool_jump()

    # ---- si_disp: status (row 0, col 12, where the level is when ready) ----
    a.deflabel('si_disp'); a.stsl_pr()
    dput = m.newlabel('dput'); dout = m.newlabel('dout')
    ld(m, ST, 9); a.tst_rr(9, 9); m.bt_far(dout)
    m.imm32(TXT, 12)
    for s, t_ in STATUS.items():
        nx = m.newlabel('sx'); a.mov_imm(s, 0); a.cmpeq(0, 9); m.bf_far(nx)
        text(t_); jmpl(m, dput); a.deflabel(nx); m.pool_jump()
    # converting: "  45%"
    ld(m, TOTU, 5); a.shlr2(5); a.shlr2(5); a.shlr2(5); a.add_imm(1, 5)
    ld(m, DONEU, 4); a.shlr2(4); a.shlr2(4); a.shlr2(4); a.mov_imm(100, 0); a.mul_l(0, 4); a.sts_macl(4)
    pg._hcall(m, '__udiv'); a.mov_imm(99, 1); c9 = m.newlabel('c9'); a.cmpgt(1, 0); a.bf_lbl(c9); a.mov_rm_rn(1, 0); a.deflabel(c9)
    a.mov_rm_rn(0, 4); a.mov_imm(10, 5); pg._hcall(m, '__udiv')
    a.add_imm(0x30, 0); a.mov_rm_rn(0, 10); a.add_imm(0x30, 1); a.mov_rm_rn(1, 11)
    m.imm32(TXT, 12); text(" ")
    a.mov_rm_rn(10, 0); a.movb_st(0, 12); a.add_imm(1, 12); a.mov_rm_rn(11, 0); a.movb_st(0, 12); a.add_imm(1, 12)
    text("%")
    a.deflabel(dput)
    m.imm32(TXT, 4); m.imm32(pg.addr['dlabels'][METER], 5); a.mov_imm(4, 3); call(m, 'si_put')
    a.deflabel(dout); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- si_meter: input level in dB below full scale (row 0, col 11), Audio Track meter ----
    a.deflabel('si_meter'); a.stsl_pr()
    mx = m.newlabel('mx')
    rd(m, SMP + 8, 0); a.tst_rr(0, 0); m.bt_far(mx)
    rd(m, SMP + 0x34, 4); rd(m, SMP + 0x38, 5); a.cmphi(4, 5); mm = m.newlabel('mm'); a.bf_lbl(mm); a.mov_rm_rn(5, 4)
    a.deflabel(mm)
    a.mov_imm(0, 6); m.imm32(32767, 7); m.imm32(DB_STEP, 2)
    dl, dd = m.newlabel('dl'), m.newlabel('dd'); a.deflabel(dl)
    a.cmphs(7, 4); a.bt_lbl(dd); a.mov_imm(48, 0); a.cmpeq(0, 6); a.bt_lbl(dd)
    a.mul_l(2, 7); a.sts_macl(7); a.shlr16(7); a.add_imm(1, 6); a.bra_lbl(dl); a.nop()
    a.deflabel(dd)
    md = m.newlabel('md'); slow = m.newlabel('slow')
    ld(m, MD, 0); a.cmphs(0, 6); a.bt_lbl(slow); st(m, 6, MD); a.bra_lbl(md); a.nop()
    a.deflabel(slow)
    ld(m, MCNT, 0); a.tst_imm(3); a.bf_lbl(md)
    ld(m, MD, 0); a.cmpeq(6, 0); a.bt_lbl(md); a.add_imm(1, 0); st(m, 0, MD)
    a.deflabel(md)
    ld(m, MCNT, 0); a.add_imm(1, 0); st(m, 0, MCNT); a.tst_imm(7); m.bf_far(mx)   # text every 8 fires
    ld(m, ST, 0); a.tst_rr(0, 0); m.bf_far(mx)                               # a take: the status is there
    m.pool_jump()
    m.imm32(TXT, 12); ld(m, MD, 4); call(m, 'si_num')
    m.imm32(TXT, 4); m.imm32(pg.addr['dlabels'][METER], 5); a.mov_imm(4, 3); call(m, 'si_put')
    a.deflabel(mx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # si_num: r4 = dB below full scale -> 4 chars at r12 ("   0", "  -6", " -31")
    a.deflabel('si_num'); a.stsl_pr()
    z, two, nd = m.newlabel('z'), m.newlabel('two'), m.newlabel('nd')
    a.tst_rr(4, 4); a.bt_lbl(z); a.mov_imm(10, 5); a.cmphs(5, 4); a.bt_lbl(two)
    text("  -"); a.mov_rm_rn(4, 0); a.add_imm(0x30, 0); a.movb_st(0, 12); a.add_imm(1, 12); a.bra_lbl(nd); a.nop()
    a.deflabel(two); pg._hcall(m, '__udiv'); a.mov_rm_rn(0, 2); text(" -"); a.mov_rm_rn(2, 0); a.add_imm(0x30, 0)
    a.movb_st(0, 12); a.add_imm(1, 12); a.mov_rm_rn(1, 0); a.add_imm(0x30, 0); a.movb_st(0, 12); a.add_imm(1, 12)
    a.bra_lbl(nd); a.nop()
    a.deflabel(z); text("   0")
    a.deflabel(nd); a.ldsl_pr(); a.rts(); a.nop()
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
    # ---- si_kmono: n frames of L = (L + R) >> 1, in place ----
    a.deflabel('si_kmono')
    m.imm32(KB + 64, 12)
    a.movl_ld_disp(12, M_LBASE - 64, 1); a.movl_ld_disp(12, M_RBASE - 64, 2); a.movl_ld_disp(12, M_N - 64, 4)
    mz = m.newlabel('mz'); a.tst_rr(4, 4); a.bt_lbl(mz)
    ml = m.newlabel('ml'); a.deflabel(ml)
    a.movw_ld(1, 0); a.movw_ld(2, 3); a.add_rr(3, 0); a.shar(0); a.movw_st(0, 1)
    a.add_imm(2, 1); a.add_imm(2, 2); a.dt(4); a.bf_lbl(ml)
    a.deflabel(mz); a.rts(); a.nop()
    m.pool_jump()

    # ---- si_kpitch: up to n output samples of the speed-up, in place (step >= 1: the source is never overwritten first) ----
    a.deflabel('si_kpitch')
    m.imm32(KB + 64, 12)
    a.movl_ld_disp(12, M_DST - 64, 1); a.movl_ld_disp(12, M_LBASE - 64, 2); a.movl_ld_disp(12, M_IDX - 64, 3); a.movl_ld_disp(12, M_N - 64, 4)
    a.movl_ld_disp(12, M_FRAC - 64, 5); a.movl_ld_disp(12, M_STEP - 64, 6); a.movl_ld_disp(12, M_SLEN - 64, 7)
    kl_, kd_ = m.newlabel('kl'), m.newlabel('kd'); a.deflabel(kl_)
    a.cmphs(7, 3); a.bt_lbl(kd_)
    a.mov_rm_rn(3, 0); a.shll(0); a.add_rr(2, 0); a.movw_ld(0, 0); a.movw_st(0, 1); a.add_imm(2, 1)
    a.add_rr(6, 5); a.mov_rm_rn(5, 0); a.shlr16(0); a.add_rr(0, 3); a.extu_w(5, 5)
    a.dt(4); a.bf_lbl(kl_)
    a.deflabel(kd_)
    a.movl_st_disp(1, M_DST - 64, 12); a.movl_st_disp(3, M_IDX - 64, 12); a.movl_st_disp(5, M_FRAC - 64, 12)
    a.rts(); a.nop()
    m.pool_jump()

    # ---- si_kfx: n samples in place: filter, sample & hold; pass 0 = peak, pass 1 = gain, bits, write (S500) ----
    a.deflabel('si_kfx'); a.stsl_pr()
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
    a.mov_rm_rn(8, 0); a.shll(0); a.add_rr(7, 0); a.add_rr(9, 0)
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
    m.imm32(32767, 6); cl_ = m.newlabel('fcl'); a.cmphi(6, 5); a.bf_lbl(cl_); a.mov_rm_rn(6, 5); a.deflabel(cl_)
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
    m.pool_jump()
    skip = m.newlabel('tskip'); a.bra_lbl(skip); a.nop()
    while (len(a.words) * 2 + a.base) & 3: a.nop()
    a.deflabel('si_rtab')
    for h_ in RTAB16: a._emit(h_)
    a.deflabel('si_ptab')
    for w in PTAB: a._emit(w & 0xFFFF); a._emit((w >> 16) & 0xFFFF)
    a.deflabel('si_thr')
    for w in THRV: a._emit(w & 0xFFFF); a._emit((w >> 16) & 0xFFFF)
    a.deflabel(skip)

def doit(m):
    a = m.a
    dx = m.newlabel('dx'); n1, n2 = m.newlabel('n1'), m.newlabel('n2')
    ld(m, ST, 0); a.tst_rr(0, 0); m.bf_far(n1)
    # READY: enter (if needed) + arm
    rd(m, SMP + 8, 0); a.tst_rr(0, 0); en = m.newlabel('en'); m.bf_far(en)
    smp4(m); osc(m, SMP_ENTER); sti(m, 1, ENTERED)
    a.deflabel(en)
    smp4(m); osc(m, SMP_ARM); a.tst_rr(0, 0); ok = m.newlabel('ok'); m.bt_far(ok)
    call(m, 'si_exit'); pg.emit_message(m, "NO MEMORY"); jmpl(m, dx)
    m.pool_jump()
    a.deflabel(ok); sti(m, ST_ARM, ST); sti(m, 0, FORCE); jmpl(m, dx)
    m.pool_jump()
    # WAIT: record now (si_mon does it as soon as the pre-roll is filled)
    a.deflabel(n1); a.mov_imm(ST_ARM, 1); a.cmpeq(1, 0); m.bf_far(n2)
    sti(m, 1, FORCE); call(m, 'si_mon'); jmpl(m, dx)
    # REC: stop, convert
    a.deflabel(n2); a.mov_imm(ST_REC, 1); a.cmpeq(1, 0); m.bf_far(dx)
    call(m, 'si_finish')
    a.deflabel(dx)
    jmpl(m, '__si_end'); m.pool_jump()
    subs(m)
    a.deflabel('__si_end')
pg.on_doit(doit)

def ev_pad(m, hret0, hret1):
    """waiting for a pad: an EMPTY pad gets the take; other states: the OS plays the pad as usual (Audio Track)"""
    a = m.a
    ld(m, ST, 0); a.mov_imm(ST_PAD, 1); a.cmpeq(1, 0); m.bf_far(hret0)
    a.movl_ld_disp(8, 4, 0); a.tst_rr(0, 0); m.bt_far(hret1)                 # release
    a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 9); a.extu_w(9, 9); a.mov_imm(12, 0); a.cmphs(0, 9); m.bt_far(hret1)
    m.imm32(BANKOBJ, 1); a.movb_ld(1, 0); a.and_imm(3); a.mov_imm(12, 2); a.mul_l(2, 0); a.sts_macl(0); a.add_rr(0, 9)
    m.imm32(PROG_IDX, 1); a.movw_ld(1, 10); a.extu_w(10, 10); m.imm32(PROG_STRIDE, 1); a.mul_l(1, 10); a.sts_macl(10)
    m.imm32(PROG_BASE, 1); a.add_rr(1, 10)
    m.imm32(PAD_SIZE, 11); a.mul_l(9, 11); a.sts_macl(11); a.add_rr(10, 11); a.add_imm(PAD_OFF, 11)   # r11 = pad block
    a.mov_imm(0, 2)
    for off in (0, 0x18, 0x30, 0x48):
        a.mov_rm_rn(11, 1); a.add_imm(off, 1); a.movb_ld(1, 0); a.or_rr(0, 2)
    a.extu_b(2, 2); a.tst_rr(2, 2); emp = m.newlabel('emp'); m.bt_far(emp)
    pg.emit_message(m, "PAD NOT EMPTY"); jmpl(m, hret1)
    m.pool_jump()
    a.deflabel(emp); call(m, 'si_assign')
    jmpl(m, hret1)
    m.pool_jump()
pg.on_event(0x38, ev_pad)

K_STOP = 0x1C                                                                # STOP key event code (mpcmod.EVCODE)
_rate_keys = pg.event_fns[2]                                                 # the rate field's LEFT / RIGHT (s500_rate.attach)
def ev_stop(m, hret0, hret1):
    """the STOP key ends the take like DO IT; the OS still gets the key (a playing sequence stops as usual)"""
    a = m.a; kx = m.newlabel('kx')
    a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 10); a.extu_w(10, 10)
    m.imm32(K_STOP, 0); a.cmpeq(0, 10); m.bf_far(kx)
    ld(m, ST, 0); a.mov_imm(ST_REC, 1); a.cmpeq(1, 0); m.bf_far(kx)
    call(m, 'si_finish'); jmpl(m, hret0)
    m.pool_jump()
    a.deflabel(kx)
    _rate_keys(m, hret0, hret1)
pg.on_event(2, ev_stop)

def cancel(m, hret1):
    a = m.a
    lv, dsc = m.newlabel('lv'), m.newlabel('dsc')
    ld(m, ST, 0); a.tst_rr(0, 0); m.bt_far(lv)                               # READY: CANCEL leaves the page
    a.mov_imm(ST_CONV, 1); a.cmphs(1, 0); m.bt_far(dsc)
    call(m, 'si_abort'); pg.emit_message(m, "TAKE CANCELLED"); jmpl(m, hret1)
    m.pool_jump()
    a.deflabel(dsc)
    smp4(m); osc(m, SMP_DISCARD); sti(m, ST_IDLE, ST); pg.emit_message(m, "TAKE DISCARDED")
    jmpl(m, hret1)
    m.pool_jump()
    a.deflabel(lv)
pg.cancel_fn = cancel


# =====================================================================================================================
def ref_convert(L, R, rate_i, aa, bits_i, norm, pup=0):
    """bit-exact model: the take (L, R or None) -> mono, filter, sample & hold, normalize, bits (S500 run())"""
    def s32(v): v &= 0xFFFFFFFF; return v - (1 << 32) if v & 0x80000000 else v
    buf = list(L) if R is None else [(l + r) >> 1 for l, r in zip(L, R)]
    if pup:
        src, buf, idx, frac = buf, [], 0, 0
        while idx < len(src):
            buf.append(src[idx]); frac += PTAB[pup - 1]; idx += frac >> 16; frac &= 0xFFFF
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

    class OSTest(PageTest):
        """PageTest with the OS code image in SDRAM, so the REAL sampler / Keep / find-by-name code runs (Audio Track)"""
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

    CODEC_W, CODEC_GAIN, REC_FLAG = 0x884C6722, 0x884C672E, 0x881C297C
    LOG = 0x88E20000
    def stubs(t):
        def logw(s):
            s.movl_pool(LOG, 1); s.movl_ld(1, 0); s.add_imm(1, 0); s.movl_st(0, 1)
            for r in (0, 2, 3, 4, 5, 6, 7): s.mov_imm(0x55, r)
            s.rts(); s.nop()
        def logcw(s):                   # codec write: (reg, val) pairs at LOG + 8n
            s.movl_pool(LOG, 1); s.movl_ld(1, 0); s.add_imm(1, 0); s.movl_st(0, 1); s.shll2(0); s.shll(0)
            s.add_rr(0, 1); s.movl_st(4, 1); s.movl_st_disp(5, 4, 1)
            for r in (0, 2, 3, 4, 5, 6, 7): s.mov_imm(0x55, r)
            s.rts(); s.nop()
        t.stub(CODEC_GAIN, logw); t.stub(CODEC_W, logcw)   # (logcw runs over CODEC_GAIN: S500 Input never calls it)
        t.stub(0x884C67BA, lambda s: (s.movl_pool(REC_FLAG, 1), s.movl_st(4, 1), s.rts(), s.nop()))   # ADC on/off flag
        t.h.ram_w32(LOG, 0); t.h.ram_w32(REC_FLAG, 0)
    t = OSTest(pg, out, before_hub=stubs); h, e = t.h, t.e
    R32, W32 = h.ram_r32, h.ram_w32
    def oscall(fn, *args):
        regs = [0x22220000 + i for i in range(16)]; regs[15] = 0x88400000 - 256
        for i, v in enumerate(args): regs[4 + i] = v
        e.r = regs[:]; e.pr = 0xDEADBEEE; e.pc = fn; e.maxsteps = 20_000_000
        assert e.run(stop_pc=0xDEADBEEE), "OS fn %08X" % fn
    def s16(v): return v - 0x10000 if v & 0x8000 else v
    # --- machine state: sampler (stereo, analog), input ring, program ---
    IN_WR = 0xA5017020
    W32(SMP + 8, 0); W32(SMP + 0x0C, 0); W32(SMP + 0x10, 0); W32(0x881C05FC, 4096)
    RING, RING_END = 0x884882A8, 0x8848C2A8
    W32(IN_WR, RING)
    h.ram_w16(PROG_IDX, 1); PROG = PROG_BASE + PROG_STRIDE
    for p in range(48):
        for l in range(4): e.wr(PROG + PAD_OFF + p * PAD_SIZE + l * 0x18, 0, 1)
    for p in range(4):
        for i, c in enumerate(b"KICK%d" % p): e.wr(PROG + PAD_OFF + p * PAD_SIZE + i, c, 1)
    h.ram_w8(BANKOBJ, 0)
    ctr = [0]; pos = [RING]; amp = [0]
    def sig(k):                         # L, R of input frame k: a two-tone "riff" at amplitude amp
        a_ = amp[0]
        l = int(a_ * (0.7 * math.sin(k * 0.031) + 0.3 * math.sin(k * 0.47)))
        r = int(a_ * (0.6 * math.sin(k * 0.029 + 1) + 0.4 * math.sin(k * 0.33)))
        return l, r
    def feed(n):
        p = pos[0]
        for _ in range(n):
            l, r = sig(ctr[0]); ctr[0] += 1
            W32(p, (l & 0xFFFF) << 16); W32(p + 4, (r & 0xFFFF) << 16); p += 8
            if p == RING_END: p = RING
        pos[0] = p; W32(IN_WR, p)
    def loop(frames, step=128):         # OS main loop: audio arrives, sampler tick, add-on fire
        for _ in range(frames // step): feed(step); oscall(SMP_TICK, SMP); t.fire(1)
    def list_end():
        p = R32(MGR)
        while R32(p): p += R32(p)
        return p
    def jobs(limit=400):
        for k in range(limit):
            if t.slot(ST) != ST_CONV: return k
            t.fire(1)
        raise AssertionError("conversion did not finish")
    status = lambda: t.label(METER, 4)
    def raw_take():
        B = R32(SMP + 0x90); n = R32(B + 0x18); sd = R32(B + 0x1C)
        L = [s16(e.rd(B + 0x100 + 2 * i, 2)) for i in range(n)]
        R = [s16(e.rd(B + 0x100 + 2 * sd + 2 * i, 2)) for i in range(n)] if R32(SMP + 0x0C) == 0 else None
        return B, L, R
    def pcm(B, n): return [s16(e.rd(B + 0x100 + 2 * i, 2)) for i in range(n)]
    from testkit import EV_KEY, K_DOIT
    def stop():                         # DO IT while recording, raw take read BEFORE any fire converts a piece of it
        t.event(EV_KEY, K_DOIT); return raw_take()
    def pad_name(p): return bytes(e.rd(PROG + PAD_OFF + p * PAD_SIZE + i, 1) for i in range(16)).split(b'\0')[0]

    # --- page, defaults, meter ---
    t.open(); t.fire(8)
    assert [e.rd(s, 1) for s in (BITSI, NORM, THR, RATEI, AA, PUP)] == [1, 1, THR_DEF, RATE_DEF, 1, 0], "defaults"
    SR.check(t, pg, RATE)
    assert THR_OPTS[THR_DEF] == "T-30" and THR_OPTS[-1] == "T -3" and THRV[THR_DEF] == 1036
    assert R32(SMP + 8) == 1 and t.slot(ENTERED) == 1, R32(SMP + 8)
    amp[0] = 1000; loop(128 * 40); mt = t.label(METER, 4)
    assert mt in (" -30", " -31", " -32", " -33"), repr(mt)
    t.close(); assert R32(SMP + 8) == 0 and t.slot(ENTERED) == 0, "page left: sampler given back"
    t.open(); t.fire(2); assert R32(SMP + 8) == 1
    print("  ok defaults 12B N:Y T-30 26.04k AA:Y, status blank; sampler entered while open, meter %r at peak ~1000" % mt)

    # --- input thru: analog bypass while we have the sampler, none under INPUT FX, given back on exit ---
    def clog():
        n_ = R32(LOG); out_ = [(R32(LOG + 8 * k), R32(LOG + 8 * k + 4)) for k in range(1, n_ + 1)]; W32(LOG, 0); return out_
    t.close(); clog(); t.open(); t.fire(1)
    assert clog() == [(4, 0x1A)], "page open: analog bypass on (input audible)"
    t.fire(REFRESH + 1); assert clog() == [(4, 0x1A)], "refreshed every 32 fires"
    ixlit0 = R32(IXLIT); W32(IXLIT, 0x88F60008); t.fire(1)
    assert clog() == [(4, 0x12)], "INPUT FX on: no analog bypass (it monitors the effected input)"
    W32(IXLIT, ixlit0); t.fire(1); assert clog() == [(4, 0x1A)], "INPUT FX off: bypass back"
    t.close(); assert R32(SMP + 8) == 0 and clog() == [(4, 0x12)], "page left: codec given back"
    t.fire(REFRESH + 2); assert clog() == [], "not ours: no codec writes"
    # recording, page left: thru stays until the take ends
    t.open(); t.fire(1); clog(); e.wr(THR, 0, 1); t.doit(); amp[0] = 3000; loop(4480 + 256)
    assert t.slot(ST) == ST_REC; t.close(); t.fire(1)
    c_ = clog(); assert R32(SMP + 8) == 1 and set(c_) <= {(4, 0x1A)}, ("recording off the page: still entered, bypass kept", c_)
    t.open(); t.fire(1); t.cancel(); e.wr(THR, THR_DEF, 1); amp[0] = 0; t.fire(2); clog()
    print("  ok input thru: analog bypass (reg 4 0x1A) while S500 Input has the sampler, 0x12 under INPUT FX / on exit")

    # --- take 1: threshold start, stereo, defaults ---
    amp[0] = 0; loop(1024)
    end0 = list_end()
    t.doit(); assert t.slot(ST) == ST_ARM and R32(SMP + 0x24) == 1, (t.slot(ST), R32(SMP + 0x24))
    t.fire(1); assert status() == "WAIT", repr(status())
    loop(4480 + 1024)
    st_wait = R32(SMP + 0x24)
    print("  .. silence after the pre-roll: sampler state %d, add-on state %d" % (st_wait, t.slot(ST)))
    assert st_wait == 2 and t.slot(ST) == ST_ARM, "waits for the threshold"
    amp[0] = 600; loop(2048)                                                 # ~-35 dB: below T-30
    assert R32(SMP + 0x24) == 2 and t.slot(ST) == ST_ARM and status() == "WAIT", "quiet input: still waiting"
    amp[0] = 20000; loop(256)
    assert R32(SMP + 0x24) == 3 and t.slot(ST) == ST_REC and status() == " REC", (R32(SMP + 0x24), t.slot(ST), status())
    print("  ok DO IT arms; silence and -35 dB: WAIT; loud input passes T-30 -> REC")
    loop(128 * 60)
    B, L, R = stop(); n = len(L)
    assert t.slot(ST) == ST_CONV and R32(SMP + 0x24) == 0, (t.slot(ST), R32(SMP + 0x24))
    assert B == end0 and R is not None and n > 7000, (hex(B), hex(end0), n)
    t.fire(1); pct = status()
    fires = jobs(); assert t.slot(ST) == ST_PAD and status() == "PAD?" and t.msg() == "HIT BLANK PAD", (status(), t.msg())
    exp = ref_convert(L, R, RATE_DEF, 1, 1, 1); got = pcm(B, n)
    bad = [i for i in range(n) if got[i] != exp[i]]
    assert not bad, (len(bad), bad[:5], [(got[i], exp[i]) for i in bad[:3]])
    assert max(abs(v) for v in got) >= 32000 and all(v % 16 == 0 for v in got), "normalized, 12-bit grid"
    print("  ok DO IT stops: %d frames converted in %d fires (showed %r), = model bit-exact, normalized, 12-bit" % (n, fires, pct))
    # --- pads ---
    t.pad(0, 2); assert t.msg() == "PAD NOT EMPTY" and t.slot(ST) == ST_PAD, t.msg()
    t.pad(0, 4); assert t.msg() == "ASSIGNED TO A05", t.msg()
    assert pad_name(4) == b"S500IN01" and bytes(e.rd(B + 4 + i, 1) for i in range(8)) == b"S500IN01", pad_name(4)
    hdr = (R32(B + 0x18), R32(B + 0x1C), e.rd(B + 0x92, 2), R32(B + 0x98), e.rd(B + 0x9C, 2))
    assert hdr == (n, 0, 1, 88200, 2), hdr
    assert R32(B) == (0x100 + 2 * n + 3) & ~3 and list_end() == B + R32(B), "block cut to the mono PCM, list end after it"
    assert pcm(B, n) == exp, "PCM unchanged by Keep"
    assert R32(B + 0x20) <= R32(B + 0x24) < n, (R32(B + 0x20), R32(B + 0x24), n)
    assert all(e.rd(PROG + PAD_OFF + 4 * PAD_SIZE + l * 0x18, 1) == 0 for l in (1, 2, 3))
    t.fire(9); assert t.slot(ST) == ST_IDLE and status() not in ("WAIT", " REC", "PAD?"), repr(status())
    print("  ok full pad refused; empty pad A05 -> S500IN01, mono header, block %d bytes, start %d end %d"
          % (R32(B), R32(B + 0x20), R32(B + 0x24)))

    # --- take 2: DO IT during the pre-roll forces the start; 16BIT NRM:N AA:N 8.0k; while the 'sequence plays' ---
    e.wr(BITSI, 0, 1); e.wr(NORM, 0, 1); e.wr(AA, 0, 1); e.wr(RATEI, 1, 1)
    amp[0] = 0; t.fire(2)
    t.doit(); t.fire(1); t.doit(); assert t.slot(FORCE) == 1 and t.slot(ST) == ST_ARM
    loop(4480 + 256)
    assert R32(SMP + 0x24) == 3 and t.slot(ST) == ST_REC, (R32(SMP + 0x24), t.slot(ST))
    amp[0] = 9000; loop(128 * 30)
    B2, L2, R2 = stop(); n2 = len(L2); jobs()
    exp2 = ref_convert(L2, R2, 1, 0, 0, 0); assert pcm(B2, n2) == exp2
    runs = sum(1 for i in range(1, n2) if exp2[i] != exp2[i - 1] and i > n2 // 2)
    print("  ok DO IT while waiting: recording starts at once (no threshold); 16BIT NRM:N AA:N 8.0k = model (%d frames)" % n2)
    t.pad(0, 5); assert t.msg() == "ASSIGNED TO A06" and pad_name(5) == b"S500IN02", (t.msg(), pad_name(5))
    assert pcm(B2, n2) == exp2 and max(abs(v) for v in exp2) < 16000, "NRM:N: Keep did not normalize the quiet take"
    print("  ok next take S500IN02 on A06, NRM:N level kept through Keep (peak %d)" % max(abs(v) for v in exp2))

    # --- take with pitch up: P+12, defaults otherwise ---
    e.wr(BITSI, 1, 1); e.wr(NORM, 1, 1); e.wr(AA, 1, 1); e.wr(RATEI, RATE_DEF, 1); e.wr(PUP, 12, 1); e.wr(THR, 0, 1)
    amp[0] = 0; t.fire(2); t.doit(); loop(4480 + 256); assert t.slot(ST) == ST_REC
    amp[0] = 9000; loop(128 * 40)
    B4, L4, R4 = stop(); s4, e4 = R32(B4 + 0x20), R32(B4 + 0x24); jobs(); assert t.slot(ST) == ST_PAD, t.slot(ST)
    exp4 = ref_convert(L4, R4, RATE_DEF, 1, 1, 1, pup=12); n4 = len(exp4)
    assert abs(n4 - len(L4) / 2) <= 1 and R32(B4 + 0x18) == n4 and pcm(B4, n4) == exp4, (n4, len(L4), R32(B4 + 0x18))
    assert abs(R32(B4 + 0x20) - s4 / 2) <= 1 and R32(B4 + 0x24) < n4 and abs(R32(B4 + 0x24) - e4 / 2) <= 1, (s4, e4, R32(B4 + 0x20), R32(B4 + 0x24))
    t.pad(0, 8); assert t.msg() == "ASSIGNED TO A09", t.msg()
    assert s16(e.rd(PROG + PAD_OFF + 8 * PAD_SIZE + 0x14, 2)) == -1200 and R32(B4 + 0x18) == n4 and pcm(B4, n4) == exp4
    assert R32(B4) == (0x100 + 2 * n4 + 3) & ~3 and list_end() == B4 + R32(B4), "block cut"
    e.wr(PUP, 0, 1); e.wr(THR, THR_DEF, 1)
    print("  ok P+12: %d -> %d frames = model bit-exact, start/end scaled, pad A09 tuned -12, block cut" % (len(L4), n4))

    # --- TOFF: recording starts as soon as the pre-roll is filled, even in silence ---
    e.wr(THR, 0, 1); amp[0] = 0; t.fire(2); t.doit(); loop(4480 + 256)
    assert R32(SMP + 0x24) == 3 and t.slot(ST) == ST_REC, (R32(SMP + 0x24), t.slot(ST))
    t.cancel(); assert t.msg() == "TAKE CANCELLED"; e.wr(THR, THR_DEF, 1)
    print("  ok TOFF: records right after the pre-roll")

    # --- the STOP key ends the take like DO IT, and the OS still gets the key ---
    e.wr(THR, 0, 1); amp[0] = 0; t.fire(2); t.doit(); loop(4480 + 256); assert t.slot(ST) == ST_REC
    amp[0] = 8000; loop(128 * 20); r_ = t.event(EV_KEY, K_STOP); assert r_ == 0 and t.slot(ST) == ST_CONV, (r_, t.slot(ST))
    jobs(); assert t.slot(ST) == ST_PAD and t.msg() == "HIT BLANK PAD", t.msg()
    t.cancel(); assert t.slot(ST) == ST_IDLE; e.wr(THR, THR_DEF, 1)
    r_ = t.event(EV_KEY, K_STOP); assert r_ == 0 and t.slot(ST) == ST_IDLE, "STOP with no take: only the OS"
    print("  ok STOP key: ends the take, converts, HIT BLANK PAD (the OS still gets the key)")

    # --- CANCEL: waiting, recording, converting, at PAD? ---
    def take_to(state):
        t.doit(); loop(4480 + 256)
        if state == ST_ARM: return
        t.doit(); t.fire(1); amp[0] = 8000; loop(128 * 20)
        if state == ST_REC: return
        t.doit()
        if state == ST_PAD: jobs()
    for stt, msg in ((ST_ARM, "TAKE CANCELLED"), (ST_REC, "TAKE CANCELLED"), (ST_CONV, "TAKE DISCARDED"), (ST_PAD, "TAKE DISCARDED")):
        amp[0] = 0; t.fire(2); e0 = list_end(); take_to(stt); assert t.slot(ST) == stt, (stt, t.slot(ST))
        t.cancel(); assert t.msg() == msg and t.slot(ST) == ST_IDLE and list_end() == e0, (stt, t.msg(), hex(list_end()), hex(e0))
    print("  ok CANCEL while waiting / recording (TAKE CANCELLED), converting / at PAD? (TAKE DISCARDED): nothing left")
    t.cancel(); assert t.slot(ST) == ST_IDLE

    # --- mono sampler: the take is used as it is (8BIT 40k AA:Y NRM:Y) ---
    t.close(); W32(SMP + 0x0C, 1); t.fire(1); e.wr(BITSI, 2, 1); e.wr(NORM, 1, 1); e.wr(AA, 1, 1); e.wr(RATEI, len(RATES) - 1, 1)
    t.open(); t.fire(2)
    amp[0] = 0; t.doit(); loop(4480 + 256); t.doit(); amp[0] = 5000; loop(128 * 20)
    B3, L3, R3 = stop(); n3 = len(L3); assert R3 is None and R32(B3 + 0x1C) == 0, R32(B3 + 0x1C)
    jobs(); exp3 = ref_convert(L3, None, len(RATES) - 1, 1, 2, 1); assert pcm(B3, n3) == exp3 and all(v % 256 == 0 for v in exp3)
    size3 = R32(B3); t.pad(0, 6); assert t.msg() == "ASSIGNED TO A07" and R32(B3) == size3
    W32(SMP + 0x0C, 0)
    print("  ok mono take: no mono step, 8BIT 40.0k AA:Y = model, block left as the OS made it")
    assert t.pad(0, 7)[0] == 0, "pads go to the OS when not waiting for one"
    print("OK: S500 Input -> %s built and tested" % os.path.basename(out))


if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', '..', 'S500IN.WAV'); pg.build_wav(out); selftest(out)
