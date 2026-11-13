#!/usr/bin/env python3
"""AUDIO TRACK - record audio over the sequence; the new sample starts exactly on the sequence's first downbeat.

    python sdk/examples/audio_track.py          -> builds AUDTRACK.WAV and tests it in the emulator

On the MPC500: copy AUDTRACK.WAV to the CF card, LOAD it (after HUB.WAV), then MODE -> OTHER -> "Audio Track".
  row 0: "1BAR NRM:Y T+9.0"  count-in 1/2 bars, normalize Y/N (default Y), input thru + level: T OFF, T-34.5..T+12.0
                             (1.5 dB steps, default +9.0: the MPC500 line input is low). The level is also the
                             recording level (one codec input stage).
  row 1: meter "IN ##------  -31" (ready), "COUNT-IN########", "REC #######-  -1" (bar = now, number = take peak);
         "NORMALIZING...", "HIT BLANK PAD". While the page is open the OS sampler stays entered (input converter on)
         so the meter works; leaving the page gives it back.
Flow:
  DO IT          -> the OS sampler is armed (threshold bypassed), then the sequence starts from the top with a count-in
                    (1 or 2 bars) whatever the Count-in setting is. Status: GET READY -> COUNT-IN -> REC  STOP=FINISH
  STOP (transport) -> recording ends; with NRM:Y the take is normalized (peak from the downbeat on = full scale),
                    a piece per fire ("NORMALIZING..."). Status: HIT BLANK PAD
  hit an EMPTY pad -> the take is kept as AUDTRKnn, assigned to that pad (layer 1, Note On, pad Mono) and that pad's
                    note is added at tick 0 with velocity 127 and the sequence's length, on the track you are on
                    (event track byte = track number; outside 1..48: track 1). "ASSIGNED TO A05"
  CANCEL         -> after STOP: the take is deleted. While recording: the take is stopped and deleted (the sequence
                    keeps playing). On READY: leaves the page.
Input thru (T not OFF): analog bypass of the codec (as Input Thru: zero latency, zero CPU), on every page, at the T level
  (refreshed every 32 fires while we have the sampler entered). Sampling on the OS Record page pauses it (codec to the OS).
Meter: max of the OS sampler meters SMP+0x34 / +0x38 (peak with decay, updated by the sampler tick in every state),
  shown in dB below full scale (1 dB steps, 0..-48), louder at once, quieter 1 dB per 4 fires.
Alignment: the sampler records from before the count-in; when the sequence starts (transport 2 -> 1) Audio Track reads
  how many frames the sampler has stored plus the frames still waiting in the input ring, adds RT_FRAMES (round trip
  output->ear->input, estimated) and puts the sample START point there. The pre-roll stays in memory (move START back in
  TRIM to recover an early note).

OS addresses (MPC500 OS 1.31; SDRAM = MPC500.bin file offset + 0x8849495C; firmware is little-endian). Found by
disassembly 2026-11-01; the sampler functions run in this file's emulator test on the real OS code. HW: Rec Track
(this tool's first version) worked on the MPC (2026-11-01).
  SMP 0x8849D114 sampler object: +8 entered, +0x0C mono, +0x10 source (0 = analog ring), +0x24 state (0 idle, 1 pre-roll,
      2 waiting for threshold, 3 recording), +0x28 name16 used by Keep, +0x44 analog ring read ptr, +0x50 other ring
      read ptr, +0x88 memory full, +0x90 new sample block, +0x98 frames stored (starts at 4410 = 100 ms pre-roll).
  enter 0x884EC06E, exit 0x884EC0EE, arm 0x884EC21E (r0 = 0 ok), go-record 0x884EC2AE (skips the threshold),
  stop 0x884EC2F0 (block +0x18 length, +0x20 start, +0x28; PCM from +0x100: L then R, length frames each),
  keep 0x884EC362 (r5 = pad + 1: names the block from SMP+0x28, adds it to the sample list, layer 1 of the pad, clears
  layers 2-4), discard 0x884EC3C8, tick 0x884EC3D8 (OS main loop). Find sample by name 0x884C5090(MGR 0x8848ED30, name).
  Input ring 0x884882A8 (2048 frames x 8 B), its write pointer 0xA5017020 (audio IRQ, 4 frames per IRQ).
  Other ring 0x884842A8, write pointer 0x881C05F4.
  Transport SEQ 0x8847C10C: +0xA84 0 stop / 2 count-in / 1 play (probe HW 2026-11-01), +0xB14 cleared by PLAY START,
  +0xB48 count-in length in ticks (count-in ends when +0xB44 reaches it: 0x884B56D2). PLAY START = locate 0x884B7C0A
  (seq, 0), 0x884B8436(seq, 0xFA), +0xB14 = 0, start 0x884B8D52(seq) (the OS key handler at 0x884B73B8 does this).
  Count-in setting: byte 0x88483FA1 bits 7..6 (0 OFF, 1 REC, 2 REC+PLAY; HW 2026-11-01): set to 2 only during the start call.
  Codec (Input Thru, HW): 0x884C6722(reg, val): reg 0/1 line volume (23 = 0 dB), reg 4 = 0x1A bypass, 0x12 OS normal;
  0x884C672E(shadow 0x881C2980) = OS line volume; 0x881C297C != 0 = the OS is sampling (ADC on).
  Pad layer +0x16 play mode (1 = Note On), pad +0x62 voice (1 = Mono) (MPC1000 PGM layout, Pad Tools PAD_INIT).
  Sequence insert: same code as Grid Seq (HW tested).

RESAMPLE (second page of this add-on, opened from the S500 Record menu; its address is published at scratch + 0x44):
  row 0: "STEREO NRM:N -18"  STEREO / MONO (L + R mixed) . normalize (default N) . level of the MPC's output
  row 1: "T-30    RESAMPLE"  threshold TOFF, T-60..T-3 (default T-30) . status: RESAMPLE / READY / REC / 45% / HIT PAD
  Shares this add-on's take engine (RS = 1 while the take is a Resample one). Opening the page saves the sampler's Record
  source and mono flag, sets Record = Main and stereo; they are put back when the page is left and no take is running.
  DO IT arms; DO IT while waiting starts at once; DO IT or the STOP key ends the take (the sequencer is never touched).
"""
import sys, os, struct
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import PageAddon, ld, st, sti
from mpcmod import EVQ, LOAD_SCRATCH

SMP = 0x8849D114
SMP_ENTER, SMP_EXIT, SMP_ARM, SMP_GOREC = 0x884EC06E, 0x884EC0EE, 0x884EC21E, 0x884EC2AE
SMP_STOP, SMP_KEEP, SMP_DISCARD, SMP_TICK = 0x884EC2F0, 0x884EC362, 0x884EC3C8, 0x884EC3D8
MGR, FINDNAME = 0x8848ED30, 0x884C5090
IN_WR, OUT_WR, RING_MASK = 0xA5017020, 0x881C05F4, 0x3FFF
SEQ = 0x8847C10C
A84, B14, B48 = SEQ + 0xA84, SEQ + 0xB14, SEQ + 0xB48
LOCATE, TRMSG, TSTART = 0x884B7C0A, 0x884B8436, 0x884B8D52
SETB = 0x88483F88 + 0x19
CODEC_W, CODEC_GAIN, GAIN_SHADOW, REC_FLAG = 0x884C6722, 0x884C672E, 0x881C2980, 0x881C297C
REG4_OS, REG4_THRU, LVL_0DB, REFRESH, REFRESH_REC = 0x12, 0x1A, 23, 256, 32
LVL_DEF = LVL_0DB + 6                                    # +9.0 dB (the MPC500 line input is low)
LEVELS = ["%+5.1f" % ((v - LVL_0DB) * 1.5) for v in range(32)]   # LINVOL 0..31 = -34.5 .. +12.0 dB, 1.5 dB steps
DB_STEP = 58409          # 10^(-1/20) in Q16: meter thresholds 1 dB apart
BAR_DB = (42, 36, 30, 24, 18, 12, 6, 2)                  # meter: block i lit when the level is above -BAR_DB[i] dB
CURSEQ, PLAYPTR = 0x8847CBC8, 0x8847CBD0
EV_OFF, BARS_OFF, BART_OFF, END = 0x1C10, 0x1C, 0x30, 0x7FFFFFFF
TRKNUM, MAXTRK = 0x881D3218 + 0x970, 48      # the track you are on, u16 1..48 (as TRACK.WAV / the OS track setter)
ARENA_LO, ARENA_HI = 0x881DB59C, 0x881DB59C + 0x1A0000
PROG_BASE, PROG_IDX, PROG_STRIDE, NOTE_A = 0x8837B5AC, 0x8847C0E2, 0x1F98, 0x1ED8
PAD_OFF, PAD_SIZE = 0x18, 0xA4
BANKOBJ = 0x8849C98C
RT_FRAMES = 80          # estimated round trip (mixer writes 32 frames ahead + converters); change here if parts sound off
CHUNK = 16384           # samples normalized per fire (~2 ms of CPU)
NAME, RNAME = "AUDTRK", "RESAMP"
assert len(NAME) == len(RNAME)
CHUNK_M, K_STOP = 4096, 0x1C                                             # mono mix per fire; STOP key event code (mpcmod.EVCODE)
LOOP_GUARD = 0x884C4F04
THR_DB = list(range(-60, 0, 3))                                          # Resample threshold -60 .. -3 dB, 3 dB steps
THR_OPTS = ["TOFF"] + ["T%3d" % d for d in THR_DB]
THR_DEF = 1 + THR_DB.index(-30)
THRV = [0] + [round(32767 * 10 ** (d / 20)) for d in THR_DB]             # sampler meter units (peak sample)
RS_ROWS = ["RESAMPLE", "   READY", "        ", "     REC", " HIT PAD"]   # status by ST (ST_IDLE .. ST_PAD)
ST_IDLE, ST_PRE, ST_CI, ST_REC, ST_PAD, ST_NORM, ST_MIX = range(7)
STATUS = {ST_IDLE: "DO IT = RECORD", ST_PRE: "GET READY...", ST_CI: "COUNT-IN",
          ST_REC: "REC  STOP=FINISH", ST_PAD: "HIT BLANK PAD", ST_NORM: "NORMALIZING..."}

pg = PageAddon("audtrk1", open_press=None)
pg.other_menu("Audio Track")
pg.far_hend = True
pg.lean_pools = True
PUB = pg.alloc_slot()                                   # = scratch + 0x44: address of the Resample page, for the S500 Record menu
assert PUB == LOAD_SCRATCH + 0x44
CIBARS = pg.choice("", 0, 0, ["1BAR", "2BAR"], default=0)
NORM = pg.choice(" NRM:", 4, 0, ["N", "Y"], default=1)
THRU = pg.choice(" T", 10, 0, ["  OFF"] + LEVELS, default=1 + LVL_DEF)      # 0 = off, 1 + LINVOL
STAT = pg.dlabel(0, 1, 16)
pg.message()
(ST, ENTERED, SAVESET, SPOS, LASTST, NAMEK, NOTE, TRACK, DUR, PSEQ, FULLF, PADN,
 BLK, LEN, NCH, SEG, CUR, PEAK, GAIN, PHASE, APPLIED, TCNT, STRIDE, ALV, MD, TPK, MCNT) = [pg.alloc_slot() for _ in range(27)]
DES4, A4M = [pg.alloc_slot() for _ in range(2)]          # reg 4 wanted / applied (INPUT FX: no bypass on our page)
IXLIT = 0x884ECCC4                                       # sampler literal: INPUT FX points it to its own pointer
IXSRC_D = 4                                              # infx.C_SRC - infx.C_SPTR: INPUT FX's SOURCE (0 INPUT, 1 MAIN), from that pointer
pg.defaults[LASTST] = 0xFF; pg.defaults[MD] = 48; pg.defaults[TPK] = 48
TXT = [pg.alloc_slot() for _ in range(4)][0]           # meter line being built (16 chars)
for _s in range(TXT, TXT + 16, 4): del pg.defaults[_s]

pgR = pg.add_page("resamp")
pgR.far_hend = True; pgR.msg_fires = 0x600
RCH = pgR.choice("", 0, 0, ["STEREO", "MONO  "], default=0)
RNRM = pgR.choice(" NRM:", 6, 0, ["N", "Y"], default=0)
RMETER = pgR.dlabel(12, 0, 4)
RTHR = pgR.choice("", 0, 1, THR_OPTS, default=THR_DEF)
RSTAT = pgR.dlabel(8, 1, 8)
pgR.message()
RS, RSON, SVSRC, SVMONO, FORCE, RPREV = [pg.alloc_slot() for _ in range(6)]    # take is Resample's; page holds the sampler; saved source / mono; start now; last state shown

def gcall(m, lbl):                                                      # a subroutine of the add-on, from any page's code
    ns = m.a.ns; m.a.ns = ''; call(m, lbl); m.a.ns = ns
def emsg(m, text):                                                      # message on the page that owns the take
    pg.emit_message(m, text)
    if m.a.ns == '': call(m, 'at_rsm')
def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()
def osc(m, fn): m.imm32(fn, 3); m.a.jsr(3); m.a.nop()
def smp4(m): m.imm32(SMP, 4)
def codec(m, reg, val): m.a.mov_imm(reg, 4); m.imm32(val, 5); osc(m, CODEC_W)
def rd(m, addr, r): m.imm32(addr, r); m.a.movl_ld(r, r)       # (st()/sti() use r1/r0 as temporaries)
def rdb(m, addr, r): m.imm32(addr, r); m.a.movb_ld(r, r); m.a.extu_b(r, r)

def fire(m):
    a = m.a
    # page open, no take: keep the OS sampler entered (input converter on) for the meter
    ld(m, ST, 0); a.tst_rr(0, 0); ent = m.newlabel('ent'); m.bf_far(ent)
    rd(m, SMP + 8, 0); a.tst_rr(0, 0); m.bf_far(ent)
    smp4(m); osc(m, SMP_ENTER); sti(m, 1, ENTERED); sti(m, 0, APPLIED)
    a.deflabel(ent)
    call(m, 'at_mon')
    # status line: meter while ready / count-in / recording, fixed text while normalizing / waiting for a pad
    ld(m, ST, 0); a.mov_imm(ST_PAD, 1); a.cmphs(1, 0); fixed = m.newlabel('fixed'); m.bt_far(fixed)
    sti(m, 0xFF, LASTST); call(m, 'at_meter'); same = m.newlabel('same')
    a.movl_pool(same, 2); a.jmp(2); a.nop(); m.pool_jump()
    a.deflabel(fixed)
    ld(m, ST, 9); ld(m, LASTST, 0); a.cmpeq(9, 0); m.bt_far(same)
    st(m, 9, LASTST)
    for s, text in STATUS.items():
        nx = m.newlabel('sx'); a.mov_imm(s, 0); a.cmpeq(0, 9); m.bf_far(nx)
        m.imm32(pg.addr['dlabels'][STAT], 12)
        for ch in text.ljust(16): a.mov_imm(ord(ch), 0); a.movb_st(0, 12); a.add_imm(1, 12)
        a.deflabel(nx); m.pool_jump()
    m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(same)
pg.on_fire(fire)                            # (the subroutines live after DO IT, outside the fire flow: fire < 4 KB)

def inactive(m):
    a = m.a
    call(m, 'at_mon')                       # thru, a running take and normalizing go on if you leave the page
    # page left and no take recording: give the sampler back (input converter off) if we entered it
    ix = m.newlabel('ix')
    ld(m, ENTERED, 0); a.tst_rr(0, 0); m.bt_far(ix)
    m.imm32(EVQ + 0x68, 1); a.movl_ld(1, 0); m.imm32(pgR.addr['page'], 1); a.cmpeq(1, 0); m.bt_far(ix)   # Resample has it
    ld(m, ST, 0); a.mov_imm(ST_PRE, 1); a.cmphs(1, 0); dx = m.newlabel('dx'); a.bf_lbl(dx)
    a.mov_imm(ST_REC, 1); a.cmphi(1, 0); m.bf_far(ix)
    a.deflabel(dx); smp4(m); osc(m, SMP_EXIT); sti(m, 0, ENTERED); sti(m, 0, APPLIED)
    a.deflabel(ix)
pg.on_inactive(inactive)

def subs(m):
    a = m.a
    # ---- at_mon: thru + follow the take (fire and inactive). r0-r7, r9-r13 ----
    a.deflabel('at_mon'); a.stsl_pr()
    sti(m, pgR.addr['page'], PUB)
    call(m, 'at_thru')
    ret = m.newlabel('ret')
    s1, s2, s3, s5, s6 = m.newlabel('s1'), m.newlabel('s2'), m.newlabel('s3'), m.newlabel('s5'), m.newlabel('s6')
    ld(m, ST, 0); a.mov_imm(ST_PRE, 1); a.cmpeq(1, 0); m.bt_far(s1)
    a.mov_imm(ST_CI, 1); a.cmpeq(1, 0); m.bt_far(s2)
    a.mov_imm(ST_REC, 1); a.cmpeq(1, 0); m.bt_far(s3)
    a.mov_imm(ST_NORM, 1); a.cmpeq(1, 0); m.bt_far(s5)
    a.mov_imm(ST_MIX, 1); a.cmpeq(1, 0); m.bt_far(s6)
    a.bra_lbl(ret); a.nop()
    m.pool_jump()
    # pre-roll filled (state >= 2) -> record now, start the sequence; Resample: DO IT or the level passes the threshold
    a.deflabel(s1)
    rd(m, SMP + 0x24, 1); a.mov_imm(2, 0); a.cmphs(0, 1); m.bf_far(ret)
    rsw = m.newlabel('rsw'); ld(m, RS, 0); a.tst_rr(0, 0); m.bf_far(rsw)
    smp4(m); osc(m, SMP_GOREC); sti(m, 48, TPK); sti(m, 0, APPLIED)
    call(m, 'at_play'); a.bra_lbl(ret); a.nop()
    m.pool_jump()
    a.deflabel(rsw)
    go_, mx_ = m.newlabel('go'), m.newlabel('mx')
    ld(m, FORCE, 0); a.tst_rr(0, 0); m.bf_far(go_)
    rdb(m, RTHR, 5); a.tst_rr(5, 5); m.bt_far(go_)
    a.shll2(5); a.movl_pool('at_thr', 2); a.add_rr(2, 5); a.movl_ld(5, 5)   # r5 = threshold (meter units)
    rd(m, SMP + 0x34, 4); rd(m, SMP + 0x38, 6); a.cmphi(4, 6); a.bf_lbl(mx_); a.mov_rm_rn(6, 4)
    a.deflabel(mx_); a.cmphi(5, 4); m.bf_far(ret)
    a.deflabel(go_); smp4(m); osc(m, SMP_GOREC); sti(m, 48, TPK); sti(m, 0, APPLIED); sti(m, ST_REC, ST)
    a.bra_lbl(ret); a.nop()
    m.pool_jump()
    # count-in
    a.deflabel(s2)
    rd(m, A84, 0); a.mov_imm(2, 1); a.cmpeq(1, 0); m.bt_far(ret)
    a.mov_imm(1, 1); a.cmpeq(1, 0); c0 = m.newlabel('c0'); m.bf_far(c0)
    call(m, 'at_mark'); sti(m, ST_REC, ST); a.bra_lbl(ret); a.nop()
    a.deflabel(c0)
    call(m, 'at_abort'); emsg(m, "TAKE CANCELLED"); a.bra_lbl(ret); a.nop()
    m.pool_jump()
    # recording: STOP pressed, sampler stopped or memory full -> finish (Resample ignores the sequencer)
    a.deflabel(s3)
    fin, rn_ = m.newlabel('fin'), m.newlabel('rn')
    ld(m, RS, 0); a.tst_rr(0, 0); m.bf_far(rn_)
    rd(m, A84, 0); a.tst_rr(0, 0); m.bt_far(fin)
    a.deflabel(rn_)
    rd(m, SMP + 0x24, 0); a.mov_imm(3, 1); a.cmpeq(1, 0); m.bf_far(fin)
    rd(m, SMP + 0x88, 0); a.tst_rr(0, 0); m.bt_far(ret)
    a.deflabel(fin); call(m, 'at_finish'); a.bra_lbl(ret); a.nop()
    a.deflabel(s5); call(m, 'at_norm'); a.bra_lbl(ret); a.nop()
    a.deflabel(s6); call(m, 'at_mix')
    a.deflabel(ret); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- at_thru: analog bypass + line level wanted = T not OFF and (the OS is not sampling, or we entered it) ----
    # The line level (codec reg 0/1) is also the recording level: refreshed often while we have the sampler entered.
    a.deflabel('at_thru'); a.stsl_pr()
    tx, toff, tap = m.newlabel('tx'), m.newlabel('toff'), m.newlabel('tap')
    rdb(m, THRU, 9); a.tst_rr(9, 9); m.bt_far(toff)
    a.add_imm(-1, 9)                                                         # r9 = LINVOL
    rd(m, REC_FLAG, 0); a.tst_rr(0, 0); tw = m.newlabel('tw'); m.bt_far(tw)
    ld(m, ENTERED, 0); a.tst_rr(0, 0); m.bt_far(toff)                        # OS sampling, not us: pause
    a.deflabel(tw)
    # reg 4: bypass, except on our page / take while MASTER's INPUT FX processes the input (2026-11-04): it monitors the
    # effected input digitally, the analog dry would double it. INPUT FX is on = the sampler's literal 0x884ECCC4 is
    # not the OS input pointer 0xA5017020 any more (see infx.py).
    sti(m, REG4_THRU, DES4); nx4 = m.newlabel('nx4')
    ld(m, ENTERED, 0); a.tst_rr(0, 0); m.bt_far(nx4)
    rd(m, IXLIT, 0); m.imm32(IN_WR, 1); a.cmpeq(1, 0); m.bt_far(nx4)
    sti(m, REG4_OS, DES4)
    a.deflabel(nx4)
    ld(m, DES4, 0); ld(m, A4M, 1); a.cmpeq(0, 1); m.bf_far(tap)
    ld(m, APPLIED, 0); a.tst_rr(0, 0); m.bt_far(tap)
    ld(m, ALV, 0); a.cmpeq(9, 0); m.bf_far(tap)                              # level changed
    ld(m, ENTERED, 2); m.imm32(REFRESH, 3); a.tst_rr(2, 2); rp = m.newlabel('rp'); a.bt_lbl(rp); a.mov_imm(REFRESH_REC, 3)
    a.deflabel(rp)
    ld(m, TCNT, 0); a.add_imm(1, 0); st(m, 0, TCNT); a.cmphs(3, 0); m.bt_far(tap)
    a.bra_lbl(tx); a.nop()
    a.deflabel(tap)
    sti(m, 0, TCNT); st(m, 9, ALV); sti(m, 1, APPLIED)
    a.mov_imm(0, 4); a.mov_rm_rn(9, 5); osc(m, CODEC_W)
    a.mov_imm(1, 4); a.mov_rm_rn(9, 5); osc(m, CODEC_W)
    a.mov_imm(4, 4); ld(m, DES4, 5); osc(m, CODEC_W); ld(m, DES4, 0); st(m, 0, A4M)
    a.bra_lbl(tx); a.nop()
    m.pool_jump()
    a.deflabel(toff)                                                         # not wanted: give the codec back once
    sti(m, 0, A4M)
    ld(m, APPLIED, 0); a.tst_rr(0, 0); m.bt_far(tx)
    sti(m, 0, APPLIED); codec(m, 4, REG4_OS)
    rd(m, REC_FLAG, 0); a.tst_rr(0, 0); m.bf_far(tx)                         # sampling: the volume is the OS's
    m.imm32(GAIN_SHADOW, 1); a.movw_ld(1, 4); osc(m, CODEC_GAIN)
    a.deflabel(tx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- at_play: PLAY START with a forced count-in (1 or 2 bars) ----
    a.deflabel('at_play'); a.stsl_pr()
    m.imm32(SETB, 13); a.movb_ld(13, 0); a.extu_b(0, 0); st(m, 0, SAVESET)
    a.and_imm(0x3F); a.or_imm(0x80); a.movb_st(0, 13)                        # Count-in = REC+PLAY, only for this call
    m.imm32(SEQ, 4); a.mov_imm(0, 5); osc(m, LOCATE)
    m.imm32(SEQ, 4); a.mov_imm(-6, 5); a.extu_b(5, 5); osc(m, TRMSG)
    m.imm32(B14, 1); a.mov_imm(0, 0); a.movl_st(0, 1)
    m.imm32(SEQ, 4); osc(m, TSTART)
    ld(m, SAVESET, 0); m.imm32(SETB, 13); a.movb_st(0, 13)                  # your setting back
    pret, pci, pgo = m.newlabel('pret'), m.newlabel('pci'), m.newlabel('pgo')
    rd(m, A84, 0); a.mov_imm(2, 1); a.cmpeq(1, 0); m.bt_far(pci)
    a.mov_imm(1, 1); a.cmpeq(1, 0); m.bt_far(pgo)
    call(m, 'at_abort'); emsg(m, "CAN'T START"); a.bra_lbl(pret); a.nop()
    m.pool_jump()
    a.deflabel(pci)
    rdb(m, CIBARS, 0); a.tst_rr(0, 0); one = m.newlabel('one'); a.bt_lbl(one)
    m.imm32(B48, 1); a.movl_ld(1, 0); a.shll(0); a.movl_st(0, 1)            # 2 bars: double the count-in length
    a.deflabel(one); sti(m, ST_CI, ST); a.bra_lbl(pret); a.nop()
    a.deflabel(pgo); call(m, 'at_mark'); sti(m, ST_REC, ST)
    a.deflabel(pret); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- at_mark: SPOS = frames stored + frames waiting in the input ring + round trip. r0-r3 ----
    a.deflabel('at_mark')
    rd(m, SMP + 0x10, 0); a.tst_rr(0, 0); oth = m.newlabel('oth'); mk = m.newlabel('mk'); a.bf_lbl(oth)
    rd(m, IN_WR, 1); rd(m, SMP + 0x44, 2); a.bra_lbl(mk); a.nop()
    a.deflabel(oth); rd(m, OUT_WR, 1); rd(m, SMP + 0x50, 2)
    a.deflabel(mk)
    a.sub(2, 1); m.imm32(RING_MASK, 0); a.and_rr(0, 1); a.shlr2(1); a.shlr(1)  # frames not yet read by the sampler
    rd(m, SMP + 0x98, 2); a.add_rr(2, 1); m.imm32(RT_FRAMES, 0); a.add_rr(0, 1)
    a.mov_rm_rn(1, 3); st(m, 3, SPOS); a.rts(); a.nop()
    m.pool_jump()

    # ---- at_finish: stop the sampler, START = SPOS, normalize or wait for a pad ----
    a.deflabel('at_finish'); a.stsl_pr()
    rd(m, SMP + 0x88, 0); st(m, 0, FULLF)
    rd(m, SMP + 0x0C, 0); a.tst_rr(0, 0); a.mov_imm(1, 0); mo = m.newlabel('mo'); a.bf_lbl(mo); a.mov_imm(2, 0)
    a.deflabel(mo); st(m, 0, NCH)                                            # channels (+0x0C = 0: stereo)
    smp4(m); osc(m, SMP_STOP)
    rd(m, SMP + 0x90, 13); st(m, 13, BLK); a.movl_ld_disp(13, 0x1C, 0); st(m, 0, STRIDE)  # frames per channel in the block
    a.movl_ld_disp(13, 0x18, 0); st(m, 0, LEN)                               # r0 = length
    emp = m.newlabel('emp'); fx = m.newlabel('fx'); a.tst_rr(0, 0); m.bt_far(emp)
    nsp = m.newlabel('nsp'); ld(m, RS, 2); a.tst_rr(2, 2); a.bt_lbl(nsp)     # Resample: START stays where the OS put it
    a.movl_ld_disp(13, 0x20, 2); st(m, 2, SPOS)
    a.deflabel(nsp)
    ld(m, SPOS, 2); a.cmphs(0, 2); ok = m.newlabel('ok'); a.bf_lbl(ok); a.mov_rm_rn(0, 2); a.add_imm(-1, 2)
    a.deflabel(ok); st(m, 2, SPOS); a.movl_st_disp(2, 0x20, 13); a.movl_st_disp(2, 0x28, 13)
    call(m, 'at_exit'); sti(m, ST_PAD, ST)
    nm_, ndn = m.newlabel('nmx'), m.newlabel('ndn')
    ld(m, RS, 0); a.tst_rr(0, 0); m.bt_far(nm_)                              # Resample MONO: mix first
    rdb(m, RCH, 0); a.tst_rr(0, 0); m.bt_far(nm_)
    ld(m, NCH, 0); a.mov_imm(2, 1); a.cmpeq(1, 0); m.bf_far(nm_)
    sti(m, 0, CUR); sti(m, ST_MIX, ST); a.bra_lbl(ndn); a.nop()
    a.deflabel(nm_); call(m, 'at_nst')
    a.deflabel(ndn)
    ld(m, FULLF, 0); a.tst_rr(0, 0); m.bt_far(fx); emsg(m, "MEMORY FULL")
    a.bra_lbl(fx); a.nop()
    a.deflabel(emp); smp4(m); osc(m, SMP_DISCARD); call(m, 'at_exit'); sti(m, ST_IDLE, ST)
    emsg(m, "NOTHING RECORDED")
    a.deflabel(fx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- at_nst: start normalizing if the take's page asks for it (else the state stays ST_PAD) ----
    a.deflabel('at_nst'); a.stsl_pr()
    nn, wf, wg = m.newlabel('nn'), m.newlabel('wf'), m.newlabel('wg')
    ld(m, RS, 0); a.tst_rr(0, 0); a.bf_lbl(wf); rdb(m, NORM, 0); a.bra_lbl(wg); a.nop()
    a.deflabel(wf); rdb(m, RNRM, 0)
    a.deflabel(wg); a.tst_rr(0, 0); m.bt_far(nn)
    sti(m, 0, PHASE); sti(m, 0, SEG); sti(m, 0, PEAK); ld(m, SPOS, 0); st(m, 0, CUR); sti(m, ST_NORM, ST)
    a.deflabel(nn); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- at_mix: Resample MONO, a chunk of L = (L + R) / 2 per call; then one channel, normalize / pad ----
    a.deflabel('at_mix'); a.stsl_pr()
    mxx, mc = m.newlabel('mxx'), m.newlabel('mc')
    ld(m, LEN, 2); ld(m, CUR, 3); a.sub(3, 2); m.imm32(CHUNK_M, 0); a.cmphi(0, 2); a.bf_lbl(mc); a.mov_rm_rn(0, 2); a.deflabel(mc)
    ld(m, BLK, 4); a.mov_rm_rn(3, 0); a.shll(0); a.add_rr(0, 4); m.imm32(0x100, 0); a.add_rr(0, 4)   # r4 = &L
    ld(m, STRIDE, 5); a.shll(5); a.add_rr(4, 5)                              # r5 = &R
    a.add_rr(2, 3); st(m, 3, CUR)
    a.tst_rr(2, 2); mz = m.newlabel('mz'); m.bt_far(mz)
    ml = m.newlabel('ml'); a.deflabel(ml)
    a.movw_ld(4, 0); a.movw_ld(5, 3); a.add_rr(3, 0); a.shar(0); a.movw_st(0, 4); a.add_imm(2, 4); a.add_imm(2, 5); a.dt(2); a.bf_lbl(ml)
    a.deflabel(mz)
    ld(m, LEN, 0); ld(m, CUR, 3); a.cmpeq(0, 3); m.bf_far(mxx)
    sti(m, 1, NCH); sti(m, ST_PAD, ST); call(m, 'at_nst')
    a.deflabel(mxx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- at_rsm: a message of the take engine also shows on the Resample page when the take is Resample's ----
    a.deflabel('at_rsm'); a.stsl_pr()
    rmx = m.newlabel('rmx'); ld(m, RS, 0); a.tst_rr(0, 0); m.bt_far(rmx)
    m.imm32(pgR.addr['msgw'] + 0x17, 1); a.movb_ld(1, 0); a.or_imm(0x80); a.movb_st(0, 1)
    sti(m, pgR.msg_fires, pgR.SHOWMSG)
    m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(rmx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- rs_on: Resample page open: take over the sampler's Record source (Main) and mono flag (stereo) ----
    a.deflabel('rs_on'); a.stsl_pr()
    rox = m.newlabel('rox'); ld(m, RSON, 0); a.tst_rr(0, 0); m.bf_far(rox)
    ld(m, ST, 0); a.tst_rr(0, 0); m.bf_far(rox)                              # an Audio Track take is still running
    rd(m, SMP + 8, 0); a.tst_rr(0, 0); rne = m.newlabel('rne'); m.bt_far(rne)
    smp4(m); osc(m, SMP_EXIT); sti(m, 0, ENTERED); sti(m, 0, APPLIED)
    a.deflabel(rne)
    rd(m, SMP + 0x10, 0); st(m, 0, SVSRC); rd(m, SMP + 0x0C, 0); st(m, 0, SVMONO)
    # source: Main, or Rec In when INPUT FX is on with SOURCE MAIN (its effected mix is in the input ring; the OS Main is dry).
    # INPUT FX on = literal IXLIT points to its pointer word C_SPTR (state block + 8); C_SRC is the word before it (+0x0C = +4)
    a.mov_imm(1, 3); rd(m, IXLIT, 2); m.imm32(IN_WR, 1); a.cmpeq(1, 2); rsm_ = m.newlabel('rsm_'); a.bt_lbl(rsm_)
    a.movl_ld_disp(2, IXSRC_D, 0); a.tst_rr(0, 0); a.bt_lbl(rsm_); a.mov_imm(0, 3)
    a.deflabel(rsm_); m.imm32(SMP + 0x10, 1); a.movl_st(3, 1)
    sti(m, 0, SMP + 0x0C); sti(m, 1, RSON)                                   # (not entered: the next enter applies them)
    a.deflabel(rox); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- rs_disp: Resample page, level (every 8 fires) and status ----
    a.deflabel('rs_disp'); a.stsl_pr()
    rs1, rpc, rdo = m.newlabel('rs1'), m.newlabel('rpc'), m.newlabel('rdo')
    call(m, 'at_md'); a.tst_rr(0, 0); m.bt_far(rs1)
    ld(m, MCNT, 0); a.add_imm(1, 0); st(m, 0, MCNT); a.tst_imm(7); m.bf_far(rs1)
    m.imm32(TXT, 12); ld(m, MD, 4); call(m, 'at_num')
    m.imm32(TXT, 4); m.imm32(pgR.addr['dlabels'][RMETER], 5); a.mov_imm(4, 3); call(m, 'at_put')
    a.deflabel(rs1)
    rsm = m.newlabel('rsm'); ld(m, ST, 9); ld(m, RPREV, 0); a.cmpeq(9, 0); m.bt_far(rsm)
    st(m, 9, RPREV); a.mov_imm(ST_PAD, 0); a.cmpeq(0, 9); m.bf_far(rsm)
    emsg(m, "HIT BLANK PAD")
    a.deflabel(rsm)
    ld(m, ST, 9); a.mov_imm(ST_NORM, 0); a.cmphs(0, 9); m.bt_far(rpc)
    a.mov_rm_rn(9, 4); a.shll2(4); a.shll(4); a.movl_pool('rs_rows', 2); a.add_rr(2, 4)
    a.movl_pool(rdo, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(rpc)                                                          # working: percent of the whole job
    ld(m, LEN, 6); a.shlr2(6); a.shlr2(6); a.shlr2(6); a.add_imm(1, 6)
    ld(m, CUR, 7); a.shlr2(7); a.shlr2(7); a.shlr2(7); a.mov_rm_rn(6, 5)
    a.mov_imm(ST_MIX, 0); a.cmpeq(0, 9); rpd = m.newlabel('rpd'); m.bt_far(rpd)
    ld(m, NCH, 2); a.mul_l(2, 6); a.sts_macl(5); a.shll(5)                   # peak + scale, each channel
    ld(m, PHASE, 0); a.mul_l(2, 0); a.sts_macl(0); ld(m, SEG, 1); a.add_rr(1, 0)
    a.mul_l(6, 0); a.sts_macl(0); a.add_rr(0, 7)
    a.deflabel(rpd)
    a.mov_imm(100, 0); a.mul_l(0, 7); a.sts_macl(4); pg._hcall(m, '__udiv')
    c9 = m.newlabel('c9'); a.mov_imm(99, 1); a.cmpgt(1, 0); a.bf_lbl(c9); a.mov_rm_rn(1, 0); a.deflabel(c9)
    a.mov_rm_rn(0, 4); a.mov_imm(10, 5); pg._hcall(m, '__udiv')
    a.add_imm(0x30, 0); a.mov_rm_rn(0, 10); a.add_imm(0x30, 1); a.mov_rm_rn(1, 11)
    m.imm32(TXT, 12)
    for ch in "     ": a.mov_imm(ord(ch), 0); a.movb_st(0, 12); a.add_imm(1, 12)
    a.mov_rm_rn(10, 0); a.movb_st(0, 12); a.add_imm(1, 12); a.mov_rm_rn(11, 0); a.movb_st(0, 12); a.add_imm(1, 12)
    a.mov_imm(ord('%'), 0); a.movb_st(0, 12)
    m.imm32(TXT, 4)
    a.deflabel(rdo)
    m.imm32(pgR.addr['dlabels'][RSTAT], 5); a.mov_imm(8, 3); call(m, 'at_put')
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- rs_keep: r9 = pad. Mono header + cut, Keep with full-scale guards (the OS Keep must not change the level) ----
    a.deflabel('rs_keep'); a.stsl_pr()
    hx, nr2, ng, ng2 = m.newlabel('hx'), m.newlabel('nr2'), m.newlabel('ng'), m.newlabel('ng2')
    ld(m, BLK, 13); ld(m, NCH, 0); a.mov_imm(2, 1); a.cmpeq(1, 0); m.bt_far(hx)
    a.mov_imm(0, 0); a.movl_st_disp(0, 0x1C, 13)
    m.imm32(0x92, 1); a.add_rr(13, 1); a.mov_imm(1, 0); a.movw_st(0, 1)
    m.imm32(0x98, 1); a.add_rr(13, 1); m.imm32(88200, 0); a.movl_st(0, 1)
    m.imm32(0x9C, 1); a.add_rr(13, 1); a.mov_imm(2, 0); a.movw_st(0, 1)
    ld(m, LEN, 4); a.shll(4); m.imm32(0x100 + 3, 0); a.add_rr(0, 4); a.mov_imm(-4, 0); a.and_rr(0, 4)
    a.movl_st(4, 13); a.mov_rm_rn(13, 0); a.add_rr(4, 0); a.mov_imm(0, 1); a.movl_st(1, 0)   # size, list end after it
    a.deflabel(hx)
    ld(m, BLK, 8); a.movl_ld_disp(8, 0x20, 0); a.shll(0); a.add_rr(8, 0); m.imm32(0x100, 1); a.add_rr(1, 0); a.mov_rm_rn(0, 8)   # r8 = &L[START]
    a.mov_imm(0, 10); ld(m, NCH, 0); a.mov_imm(2, 1); a.cmpeq(1, 0); a.bf_lbl(nr2)
    ld(m, STRIDE, 10); a.shll(10); a.add_rr(8, 10)                           # r10 = &R[START] (0 = mono)
    a.deflabel(nr2)
    a.movw_ld(8, 12); m.imm32(32767, 0); a.movw_st(0, 8)
    a.tst_rr(10, 10); a.bt_lbl(ng); a.movw_ld(10, 13); a.movw_st(0, 10); a.deflabel(ng)
    smp4(m); a.mov_rm_rn(9, 5); a.add_imm(1, 5); osc(m, SMP_KEEP)
    a.movw_st(12, 8); a.tst_rr(10, 10); a.bt_lbl(ng2); a.movw_st(13, 10); a.deflabel(ng2)
    ld(m, BLK, 4); osc(m, LOOP_GUARD)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- at_norm: one run of up to CHUNK samples per call. PHASE 0 = peak from START (each channel), 1 = scale all ----
    a.deflabel('at_norm'); a.stsl_pr()
    nx_, seg_end = m.newlabel('nx'), m.newlabel('segend')
    ld(m, LEN, 6); ld(m, CUR, 7)                                             # r6 = length, r7 = cursor
    a.mov_rm_rn(6, 2); a.sub(7, 2)                                           # r2 = samples left in this channel
    m.imm32(CHUNK, 0); a.cmphi(0, 2); cl = m.newlabel('cl'); a.bf_lbl(cl); a.mov_rm_rn(0, 2); a.deflabel(cl)
    a.mov_rm_rn(2, 13)                                                       # r13 = n
    ld(m, SEG, 0); ld(m, STRIDE, 3); a.mul_l(3, 0); a.sts_macl(4); a.add_rr(7, 4); a.shll(4)   # r4 = (SEG*stride + cur)*2
    ld(m, BLK, 0); a.add_rr(0, 4); m.imm32(0x100, 0); a.add_rr(0, 4)         # r4 = &PCM (ld/st use r1)
    a.add_rr(13, 7); st(m, 7, CUR)
    a.tst_rr(2, 2); m.bt_far(seg_end)
    ld(m, PHASE, 0); a.tst_rr(0, 0); sc = m.newlabel('sc'); m.bf_far(sc)
    # peak: r3 = max |s|
    ld(m, PEAK, 3)
    pk, pp_, pn = m.newlabel('pk'), m.newlabel('pp'), m.newlabel('pn'); a.deflabel(pk)
    a.movw_ld(4, 0); a.add_imm(2, 4); a.cmppz(0); a.bt_lbl(pp_); a.neg(0, 0); a.deflabel(pp_)
    a.cmphi(3, 0); a.bf_lbl(pn); a.mov_rm_rn(0, 3); a.deflabel(pn); a.dt(2); a.bf_lbl(pk)
    st(m, 3, PEAK); a.bra_lbl(seg_end); a.nop()
    m.pool_jump()
    # scale: s = clamp(|s| * GAIN >> 16) with the sign back
    a.deflabel(sc)
    ld(m, GAIN, 5); m.imm32(32767, 7)
    sl, sp, sk, sn = m.newlabel('sl'), m.newlabel('sp'), m.newlabel('sk'), m.newlabel('sn'); a.deflabel(sl)
    a.movw_ld(4, 0); a.mov_imm(0, 6); a.cmppz(0); a.bt_lbl(sp); a.neg(0, 0); a.mov_imm(1, 6); a.deflabel(sp)
    a.dmulu_l(5, 0); a.sts_mach(3); a.sts_macl(12); a.shll16(3); a.shlr16(12); a.or_rr(12, 3); a.mov_rm_rn(3, 0)
    a.cmphi(7, 0); a.bf_lbl(sk); a.mov_rm_rn(7, 0); a.deflabel(sk)
    a.tst_rr(6, 6); a.bt_lbl(sn); a.neg(0, 0); a.deflabel(sn)
    a.movw_st(0, 4); a.add_imm(2, 4); a.dt(2); a.bf_lbl(sl)
    a.deflabel(seg_end)
    # end of this channel?
    ld(m, CUR, 7); ld(m, LEN, 6); a.cmpeq(6, 7); m.bf_far(nx_)
    ld(m, SEG, 0); a.add_imm(1, 0); st(m, 0, SEG); ld(m, NCH, 1); a.cmpeq(1, 0); nseg = m.newlabel('nseg'); m.bt_far(nseg)
    ld(m, PHASE, 0); a.tst_rr(0, 0); p1 = m.newlabel('p1'); cs_ = m.newlabel('cs'); a.bf_lbl(p1)
    ld(m, SPOS, 2); a.bra_lbl(cs_); a.nop()                                  # peak: next channel from START
    a.deflabel(p1); a.mov_imm(0, 2)                                          # scale: whole channel
    a.deflabel(cs_); st(m, 2, CUR); a.bra_lbl(nx_); a.nop()
    m.pool_jump()
    a.deflabel(nseg)                                                         # all channels done
    ld(m, PHASE, 0); a.tst_rr(0, 0); dn = m.newlabel('dn'); m.bf_far(dn)
    ld(m, PEAK, 5); a.mov_imm(16, 0); a.cmphs(0, 5); m.bf_far(dn)            # silence: leave it
    m.imm32(0x7FFF0000, 4); pg._hcall(m, '__udiv'); st(m, 0, GAIN)           # GAIN = 32767 / peak (Q16)
    m.imm32(0x10000, 1); a.cmphi(1, 0); m.bf_far(dn)                         # already at full scale
    sti(m, 1, PHASE); sti(m, 0, SEG); sti(m, 0, CUR); a.bra_lbl(nx_); a.nop()
    a.deflabel(dn); sti(m, ST_PAD, ST)
    a.deflabel(nx_); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- at_abort: stop + delete the take; at_exit: leave the sampler as we found it ----
    a.deflabel('at_abort'); a.stsl_pr()
    smp4(m); osc(m, SMP_STOP); smp4(m); osc(m, SMP_DISCARD)
    call(m, 'at_exit'); sti(m, ST_IDLE, ST)
    a.ldsl_pr(); a.rts(); a.nop()
    a.deflabel('at_exit'); a.stsl_pr()                                     # page open: stay entered for the meter
    ne = m.newlabel('ne')
    ld(m, ENTERED, 0); a.tst_rr(0, 0); m.bt_far(ne)
    m.imm32(EVQ + 0x68, 1); a.movl_ld(1, 0); m.imm32(pg.addr['page'], 1); a.cmpeq(1, 0); m.bt_far(ne)
    m.imm32(pgR.addr['page'], 1); a.cmpeq(1, 0); m.bt_far(ne)
    smp4(m); osc(m, SMP_EXIT); sti(m, 0, ENTERED)
    a.deflabel(ne)
    sti(m, 0, APPLIED)                                                       # thru: re-apply at the next fire
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- at_meter: bottom row = level meter (fire only). MD = shown level (dB below full scale, 0..48) ----
    a.deflabel('at_meter'); a.stsl_pr()
    mx = m.newlabel('mx')
    call(m, 'at_md'); a.tst_rr(0, 0); m.bt_far(mx)
    ld(m, MCNT, 0); a.add_imm(1, 0); st(m, 0, MCNT); a.tst_imm(7); m.bf_far(mx)   # text every 8 fires
    m.pool_jump()
    m.imm32(TXT, 12)
    def text(s):
        for ch in s: a.mov_imm(ord(ch), 0); a.movb_st(0, 12); a.add_imm(1, 12)
    tci, trec, tb = m.newlabel('tci'), m.newlabel('trec'), m.newlabel('tb')
    ld(m, ST, 0); a.mov_imm(ST_REC, 1); a.cmpeq(1, 0); m.bt_far(trec)
    a.tst_rr(0, 0); m.bf_far(tci)
    text("IN "); call(m, 'at_bar'); text(" "); ld(m, MD, 4); call(m, 'at_num'); a.bra_lbl(tb); a.nop()
    a.deflabel(tci); text("COUNT-IN"); call(m, 'at_bar'); a.bra_lbl(tb); a.nop()
    a.deflabel(trec); text("REC "); call(m, 'at_bar'); ld(m, TPK, 4); call(m, 'at_num')
    a.deflabel(tb)
    m.imm32(TXT, 4); m.imm32(pg.addr['dlabels'][STAT], 5); a.mov_imm(16, 3); call(m, 'at_put')
    a.deflabel(mx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # at_put: copy r3 chars r4 -> r5 where they differ; redraw only if something changed
    a.deflabel('at_put')
    a.mov_imm(0, 6)
    cl, cs, px = m.newlabel('cl'), m.newlabel('cs'), m.newlabel('px')
    a.deflabel(cl); a.movb_ldinc(4, 0); a.movb_ld(5, 7); a.cmpeq(0, 7); a.bt_lbl(cs); a.movb_st(0, 5); a.mov_imm(1, 6)
    a.deflabel(cs); a.add_imm(1, 5); a.dt(3); a.bf_lbl(cl)
    a.tst_rr(6, 6); a.bt_lbl(px); m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(px); a.rts(); a.nop()
    m.pool_jump()
    # at_md: MD = shown level (dB below full scale) from the OS sampler meters. r0 = 0 when the sampler is not entered
    a.deflabel('at_md'); a.stsl_pr()
    mdx = m.newlabel('mdx')
    rd(m, SMP + 8, 0); a.tst_rr(0, 0); m.bt_far(mdx)
    rd(m, SMP + 0x34, 4); rd(m, SMP + 0x38, 5); a.cmphi(4, 5); mm = m.newlabel('mm'); a.bf_lbl(mm); a.mov_rm_rn(5, 4)
    a.deflabel(mm)                                                           # r4 = max(L, R) meter of the OS
    a.mov_imm(0, 6); m.imm32(32767, 7); m.imm32(DB_STEP, 2)
    dl, dd = m.newlabel('dl'), m.newlabel('dd'); a.deflabel(dl)
    a.cmphs(7, 4); a.bt_lbl(dd); a.mov_imm(48, 0); a.cmpeq(0, 6); a.bt_lbl(dd)
    a.mul_l(2, 7); a.sts_macl(7); a.shlr16(7); a.add_imm(1, 6); a.bra_lbl(dl); a.nop()
    a.deflabel(dd)                                                           # r6 = dB below full scale
    md = m.newlabel('md'); slow = m.newlabel('slow')
    ld(m, MD, 0); a.cmphs(0, 6); a.bt_lbl(slow); st(m, 6, MD); a.bra_lbl(md); a.nop()   # louder: at once
    a.deflabel(slow)                                                         # quieter: 1 dB every 4 fires
    ld(m, MCNT, 0); a.tst_imm(3); a.bf_lbl(md)
    ld(m, MD, 0); a.cmpeq(6, 0); a.bt_lbl(md); a.add_imm(1, 0); st(m, 0, MD)
    a.deflabel(md)
    ld(m, ST, 0); a.mov_imm(ST_REC, 1); a.cmpeq(1, 0); np = m.newlabel('np'); a.bf_lbl(np)
    ld(m, TPK, 0); a.cmphi(6, 0); a.bf_lbl(np); st(m, 6, TPK)                 # loudest of the take
    a.deflabel(np)
    a.mov_imm(1, 0)
    a.deflabel(mdx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # at_bar: 8 blocks at r12 for level MD
    a.deflabel('at_bar')
    ld(m, MD, 5)
    for thr in BAR_DB:
        sk = m.newlabel('bk'); a.mov_imm(thr, 0); a.cmphs(5, 0); a.mov_imm(ord('-'), 1); a.bf_lbl(sk); a.mov_imm(-1, 1)
        a.deflabel(sk); a.movb_st(1, 12); a.add_imm(1, 12)
    a.rts(); a.nop()
    # at_num: r4 = dB below full scale -> 4 chars at r12 ("   0", "  -6", " -31")
    a.deflabel('at_num'); a.stsl_pr()
    z, two, nd = m.newlabel('z'), m.newlabel('two'), m.newlabel('nd')
    a.tst_rr(4, 4); a.bt_lbl(z); a.mov_imm(10, 5); a.cmphs(5, 4); a.bt_lbl(two)
    text("  -"); a.mov_rm_rn(4, 0); a.add_imm(0x30, 0); a.movb_st(0, 12); a.add_imm(1, 12); a.bra_lbl(nd); a.nop()
    a.deflabel(two); pg._hcall(m, '__udiv'); a.mov_rm_rn(0, 2); text(" -"); a.mov_rm_rn(2, 0); a.add_imm(0x30, 0)
    a.movb_st(0, 12); a.add_imm(1, 12); a.mov_rm_rn(1, 0); a.add_imm(0x30, 0); a.movb_st(0, 12); a.add_imm(1, 12)
    a.bra_lbl(nd); a.nop()
    a.deflabel(z); text("   0")
    a.deflabel(nd); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- at_assign: r9 = pad 0..47, r10 = program, r11 = pad block. Keep, Note On + Mono, note at tick 0 ----
    a.deflabel('at_assign'); a.stsl_pr()
    sti(m, 1, NAMEK)
    nl = m.newlabel('nl'); uq = m.newlabel('uq'); a.deflabel(nl)
    m.imm32(SMP + 0x28, 12)
    nrn, nrd = m.newlabel('nrn'), m.newlabel('nrd')
    ld(m, RS, 0); a.tst_rr(0, 0); m.bf_far(nrn)
    for ch in NAME: a.mov_imm(ord(ch), 0); a.movb_st(0, 12); a.add_imm(1, 12)
    a.bra_lbl(nrd); a.nop()
    a.deflabel(nrn)
    for ch in RNAME: a.mov_imm(ord(ch), 0); a.movb_st(0, 12); a.add_imm(1, 12)
    a.deflabel(nrd)
    ld(m, NAMEK, 4); a.mov_imm(10, 5); pg._hcall(m, '__udiv')
    a.add_imm(0x30, 0); a.movb_st(0, 12); a.add_imm(1, 12); a.mov_rm_rn(1, 0); a.add_imm(0x30, 0); a.movb_st(0, 12)
    a.add_imm(1, 12); a.mov_imm(0, 0)
    for _ in range(16 - len(NAME) - 2): a.movb_st(0, 12); a.add_imm(1, 12)
    m.imm32(MGR, 4); m.imm32(SMP + 0x28, 5); osc(m, FINDNAME); a.tst_rr(0, 0); m.bt_far(uq)
    ld(m, NAMEK, 0); a.add_imm(1, 0); st(m, 0, NAMEK); a.mov_imm(100, 1); a.cmpeq(1, 0); m.bf_far(nl)
    sti(m, 99, NAMEK)                                                        # 99 takes with these names: reuse 99
    m.pool_jump()
    a.deflabel(uq)
    ka, aend = m.newlabel('ka'), m.newlabel('aend')
    ld(m, RS, 0); a.tst_rr(0, 0); m.bt_far(ka)
    call(m, 'rs_keep'); a.mov_imm(1, 13); m.a.movl_pool(aend, 2); a.jmp(2); a.nop()   # Resample: no sequence note
    m.pool_jump()
    a.deflabel(ka)
    smp4(m); a.mov_rm_rn(9, 5); a.add_imm(1, 5); osc(m, SMP_KEEP)
    a.mov_imm(1, 0); a.mov_rm_rn(11, 1); a.add_imm(0x16, 1); a.movb_st(0, 1)   # layer 1: Note On
    a.mov_rm_rn(11, 1); a.add_imm(0x62, 1); a.movb_st(0, 1)                    # pad: Mono
    # note of the pad, the track you are on (1..48, else 1), length of the sequence
    m.imm32(NOTE_A, 0); a.add_rr(10, 0); a.add_rr(9, 0); a.movb_ld(0, 0); a.extu_b(0, 0); st(m, 0, NOTE)
    rd(m, CURSEQ, 8); st(m, 8, PSEQ)
    m.imm32(TRKNUM, 1); a.movw_ld(1, 0); a.extu_w(0, 0); a.add_imm(-1, 0)
    a.mov_imm(MAXTRK - 1, 1); a.cmphi(1, 0); t1 = m.newlabel('t1'); a.bf_lbl(t1); a.mov_imm(0, 0); a.deflabel(t1)
    a.add_imm(1, 0); st(m, 0, TRACK)
    a.mov_rm_rn(8, 1); a.add_imm(BARS_OFF, 1); a.movw_ld(1, 0); a.extu_w(0, 0); a.shll2(0); a.add_rr(8, 0)
    m.imm32(BART_OFF, 1); a.add_rr(1, 0); a.movl_ld(0, 0); m.imm32(0xFFFFFF, 1); a.and_rr(1, 0)
    m.imm32(0xFFFF, 1); a.cmphi(1, 0); dl = m.newlabel('dl'); a.bf_lbl(dl); a.mov_rm_rn(1, 0); a.deflabel(dl)
    st(m, 0, DUR)
    call(m, 'at_insert'); a.mov_rm_rn(0, 13)
    a.deflabel(aend)
    st(m, 9, PADN); sti(m, ST_IDLE, ST)
    a.tst_rr(13, 13); ins = m.newlabel('ins'); m.bf_far(ins)
    emsg(m, "SEQ MEMORY FULL"); ax = m.newlabel('ax'); a.bra_lbl(ax); a.nop()
    a.deflabel(ins)
    # "ASSIGNED TO A05"
    m.imm32(pg.addr['msgt'], 12)
    for ch in "ASSIGNED TO ": a.mov_imm(ord(ch), 0); a.movb_st(0, 12); a.add_imm(1, 12)
    ld(m, PADN, 4); a.mov_imm(12, 5); pg._hcall(m, '__udiv')
    a.add_imm(ord('A'), 0); a.movb_st(0, 12); a.add_imm(1, 12); a.add_imm(1, 1); a.mov_rm_rn(1, 4); a.mov_imm(10, 5)
    pg._hcall(m, '__udiv')
    a.add_imm(0x30, 0); a.movb_st(0, 12); a.add_imm(1, 12); a.add_imm(0x30, 1); a.mov_rm_rn(1, 0); a.movb_st(0, 12)
    a.add_imm(1, 12); a.mov_imm(ord(' '), 0); a.movb_st(0, 12); a.add_imm(1, 12); a.mov_imm(0, 0); a.movb_st(0, 12)
    pg._hcall(m, '__showmsg'); call(m, 'at_rsm')
    a.deflabel(ax); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- at_insert: note NOTE, track TRACK, vel 127, dur DUR at tick 0 (Grid Seq insert). r0 = 1 ok, 0 full ----
    a.deflabel('at_end')
    m.imm32(ARENA_LO, 10); m.imm32(ARENA_HI, 3)
    el, ee = m.newlabel('el'), m.newlabel('ee'); a.deflabel(el)
    a.movl_ld(10, 0); a.tst_rr(0, 0); a.bt_lbl(ee); a.add_rr(0, 10); a.cmphs(3, 10); a.bf_lbl(el)
    a.rts(); a.mov_imm(0, 0)
    a.deflabel(ee); a.rts(); a.mov_imm(1, 0)
    m.pool_jump()
    a.deflabel('at_insert'); a.stsl_pr()
    for r in (8, 9, 10, 11, 12, 13, 14): a.movl_st_dec(r, 15)
    a.mov_imm(0, 11)                                                         # r11 = tick 0
    a.bsr_lbl('at_end'); a.nop(); a.tst_rr(0, 0); ifu = m.newlabel('ifu'); m.bt_far(ifu)
    a.mov_rm_rn(10, 0); a.add_imm(4 + 16, 0); m.imm32(ARENA_HI, 1); a.cmphs(1, 0); m.bt_far(ifu)
    ld(m, PSEQ, 8); m.imm32(EV_OFF, 12); a.add_rr(8, 12)
    m.pool_jump()
    pl, px = m.newlabel('pl'), m.newlabel('px'); a.deflabel(pl)
    a.movl_ld(12, 0); m.imm32(END, 1); a.cmpeq(1, 0); a.bt_lbl(px); a.cmpgt(11, 0); a.bt_lbl(px); a.add_imm(16, 12); a.bra_lbl(pl); a.nop()
    a.deflabel(px)
    ld(m, TRACK, 13); ld(m, NOTE, 14); a.mov_imm(127, 9); ld(m, DUR, 6)
    a.stc_sr(7); a.mov_rm_rn(7, 0); a.or_imm(0xF0); a.ldc_sr(0)
    a.mov_rm_rn(10, 1); a.add_imm(4, 1); a.mov_rm_rn(1, 2); a.add_imm(16, 2)
    ml2, mx2 = m.newlabel('ml2'), m.newlabel('mx2'); a.deflabel(ml2)
    a.cmphs(1, 12); a.bt_lbl(mx2); a.add_imm(-4, 1); a.add_imm(-4, 2); a.movl_ld(1, 0); a.movl_st(0, 2); a.bra_lbl(ml2); a.nop()
    a.deflabel(mx2)
    a.movl_st(11, 12); a.mov_rm_rn(12, 1); a.add_imm(4, 1)
    a.mov_rm_rn(13, 0); a.movb_st(0, 1); a.add_imm(1, 1); m.imm32(0x90, 0); a.movb_st(0, 1); a.add_imm(1, 1)
    a.mov_rm_rn(14, 0); a.movb_st(0, 1); a.add_imm(1, 1); a.mov_rm_rn(9, 0); a.movb_st(0, 1); a.add_imm(1, 1)
    a.mov_rm_rn(6, 0); a.movw_st(0, 1); a.add_imm(2, 1); a.mov_imm(0, 0); a.movw_st(0, 1); a.add_imm(2, 1); a.movl_st(0, 1)
    a.movl_ld(8, 0); a.add_imm(16, 0); a.movl_st(0, 8)
    m.imm32(PLAYPTR, 1); a.movl_ld(1, 0); a.cmphs(12, 0); pp = m.newlabel('pp'); a.bf_lbl(pp); a.add_imm(16, 0); a.movl_st(0, 1); a.deflabel(pp)
    a.ldc_sr(7)
    a.mov_imm(1, 0); ix = m.newlabel('ix'); a.bra_lbl(ix); a.nop()
    a.deflabel(ifu); a.mov_imm(0, 0)
    a.deflabel(ix)
    for r in (14, 13, 12, 11, 10, 9, 8): a.movl_ld_inc(15, r)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tables ----
    skip = m.newlabel('tskip'); a.bra_lbl(skip); a.nop()
    while (len(a.words) * 2 + a.base) & 3: a.nop()
    a.deflabel('at_thr')
    for w in THRV: a._emit(w & 0xFFFF); a._emit((w >> 16) & 0xFFFF)
    a.deflabel('rs_rows')
    t_ = ''.join(RS_ROWS); assert len(t_) == 40
    for i in range(0, 40, 2): a._emit(ord(t_[i]) | (ord(t_[i + 1]) << 8))
    a.deflabel(skip)

def doit(m):
    a = m.a
    dx = m.newlabel('dx')
    idl = m.newlabel('idl'); ld(m, ST, 0); a.tst_rr(0, 0); m.bt_far(idl)
    a.mov_imm(ST_REC, 1); a.cmpeq(1, 0); m.bf_far(dx)                        # (count-in, normalizing, pad: ignored)
    m.post_event(2, K_STOP)                                                  # recording: DO IT = the STOP key (ends sequence + take)
    a.movl_pool(dx, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(idl)
    sti(m, 0, RS)
    rd(m, CURSEQ, 0); m.imm32(ARENA_LO, 1); ns = m.newlabel('ns'); a.cmphs(1, 0); m.bf_far(ns)
    m.imm32(ARENA_HI, 1); a.cmphs(1, 0); m.bt_far(ns)
    rd(m, A84, 0); a.tst_rr(0, 0); pl = m.newlabel('pl'); m.bf_far(pl)
    rd(m, SMP + 8, 0); a.tst_rr(0, 0); en = m.newlabel('en'); m.bf_far(en)
    smp4(m); osc(m, SMP_ENTER); sti(m, 1, ENTERED)
    a.deflabel(en)
    smp4(m); osc(m, SMP_ARM); a.tst_rr(0, 0); ok = m.newlabel('ok'); m.bt_far(ok)
    call(m, 'at_exit'); pg.emit_message(m, "NO MEMORY"); a.movl_pool(dx, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(ok)
    sti(m, ST_PRE, ST); sti(m, 0, APPLIED); sti(m, 48, TPK)
    a.movl_pool(dx, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(ns); pg.emit_message(m, "NO SEQUENCE"); a.movl_pool(dx, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(pl); pg.emit_message(m, "STOP SEQ FIRST")
    a.deflabel(dx)
    a.movl_pool('__at_end', 2); a.jmp(2); a.nop(); m.pool_jump()
    subs(m)
    a.deflabel('__at_end')
pg.on_doit(doit)

def ev_pad(m, hret0, hret1):
    """waiting for a pad: an EMPTY pad gets the take; other states: the OS plays the pad as usual"""
    a = m.a
    ld(m, ST, 0); a.mov_imm(ST_PAD, 1); a.cmpeq(1, 0); m.bf_far(hret0)
    a.movl_ld_disp(8, 4, 0); a.tst_rr(0, 0); m.bt_far(hret1)                 # release
    a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 9); a.extu_w(9, 9); a.mov_imm(12, 0); a.cmphs(0, 9); m.bt_far(hret1)
    m.imm32(BANKOBJ, 1); a.movb_ld(1, 0); a.and_imm(3); a.mov_imm(12, 2); a.mul_l(2, 0); a.sts_macl(0); a.add_rr(0, 9)
    m.imm32(PROG_IDX, 1); a.movw_ld(1, 10); a.extu_w(10, 10); m.imm32(PROG_STRIDE, 1); a.mul_l(1, 10); a.sts_macl(10)
    m.imm32(PROG_BASE, 1); a.add_rr(1, 10)                                   # r10 = program
    m.imm32(PAD_SIZE, 11); a.mul_l(9, 11); a.sts_macl(11); a.add_rr(10, 11); a.add_imm(PAD_OFF, 11)   # r11 = pad block
    a.mov_imm(0, 2)
    for off in (0, 0x18, 0x30, 0x48):
        a.mov_rm_rn(11, 1); a.add_imm(off, 1); a.movb_ld(1, 0); a.or_rr(0, 2)
    a.extu_b(2, 2); a.tst_rr(2, 2); emp = m.newlabel('emp'); m.bt_far(emp)
    pg.emit_message(m, "PAD NOT EMPTY"); a.movl_pool(hret1, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(emp); gcall(m, 'at_assign')
    a.movl_pool(hret1, 2); a.jmp(2); a.nop()
    m.pool_jump()
pg.on_event(0x38, ev_pad)

def cancel(m, hret1):
    a = m.a
    lv = m.newlabel('lv')
    ld(m, ST, 0); a.tst_rr(0, 0); m.bt_far(lv)                               # READY: CANCEL leaves the page
    a.mov_imm(ST_PAD, 1); a.cmpeq(1, 0); dsc = m.newlabel('dsc'); m.bt_far(dsc)
    a.mov_imm(ST_MIX, 1); a.cmpeq(1, 0); m.bt_far(dsc)
    a.mov_imm(ST_NORM, 1); a.cmpeq(1, 0); rn = m.newlabel('rn'); m.bf_far(rn)
    a.deflabel(dsc)
    smp4(m); osc(m, SMP_DISCARD); sti(m, ST_IDLE, ST); pg.emit_message(m, "TAKE DISCARDED")
    a.movl_pool(hret1, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(rn); gcall(m, 'at_abort'); pg.emit_message(m, "TAKE CANCELLED")
    a.movl_pool(hret1, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(lv)
pg.cancel_fn = cancel

# ===================================== RESAMPLE page (shares the take engine above) ==================================
def rfire(m):
    a = m.a
    m.imm32(pgR.addr['msgw'] + 0x24, 1); m.imm32(pg.addr['msgt'], 0); a.movl_st(0, 1)   # one message buffer for the engine
    gcall(m, 'rs_on')
    ld(m, ST, 0); a.tst_rr(0, 0); ent = m.newlabel('ent'); m.bf_far(ent)
    rd(m, SMP + 8, 0); a.tst_rr(0, 0); m.bf_far(ent)
    smp4(m); osc(m, SMP_ENTER); sti(m, 1, ENTERED); sti(m, 0, APPLIED)
    a.deflabel(ent)
    gcall(m, 'rs_disp')
pgR.on_fire(rfire)

def rinactive(m):
    """page left and no take recording: the sampler's own settings back (Audio Track's inactive gave the sampler back)"""
    a = m.a; rx = m.newlabel('rx'); do_ = m.newlabel('do')
    ld(m, RSON, 0); a.tst_rr(0, 0); m.bt_far(rx)
    ld(m, ST, 0); a.mov_imm(ST_PRE, 1); a.cmphs(1, 0); a.bf_lbl(do_)
    a.mov_imm(ST_REC, 1); a.cmphi(1, 0); m.bf_far(rx)
    a.deflabel(do_)
    ld(m, SVSRC, 0); m.imm32(SMP + 0x10, 1); a.movl_st(0, 1)
    ld(m, SVMONO, 0); m.imm32(SMP + 0x0C, 1); a.movl_st(0, 1)
    sti(m, 0, RSON)
    a.deflabel(rx)
pgR.on_inactive(rinactive)

def rdoit(m):
    a = m.a
    dx, n1, n2 = m.newlabel('dx'), m.newlabel('n1'), m.newlabel('n2')
    ld(m, ST, 0); a.tst_rr(0, 0); m.bf_far(n1)
    ld(m, RSON, 0); a.tst_rr(0, 0); m.bt_far(dx)
    sti(m, 1, RS); sti(m, 0, FORCE)
    rd(m, SMP + 8, 0); a.tst_rr(0, 0); en = m.newlabel('en'); m.bf_far(en)
    smp4(m); osc(m, SMP_ENTER); sti(m, 1, ENTERED)
    a.deflabel(en)
    smp4(m); osc(m, SMP_ARM); a.tst_rr(0, 0); ok = m.newlabel('ok'); m.bt_far(ok)
    gcall(m, 'at_exit'); pg.emit_message(m, "NO MEMORY"); a.movl_pool(dx, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(ok); sti(m, ST_PRE, ST); sti(m, 0, APPLIED); sti(m, 48, TPK); a.movl_pool(dx, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(n1); ld(m, RS, 2); a.tst_rr(2, 2); m.bt_far(dx)                # an Audio Track take: not ours
    a.mov_imm(ST_PRE, 1); a.cmpeq(1, 0); m.bf_far(n2)
    sti(m, 1, FORCE); a.movl_pool(dx, 2); a.jmp(2); a.nop()
    m.pool_jump()
    a.deflabel(n2); a.mov_imm(ST_REC, 1); a.cmpeq(1, 0); m.bf_far(dx)
    gcall(m, 'at_finish')
    a.deflabel(dx)
pgR.on_doit(rdoit)

def rkey(m, hret0, hret1):
    """the STOP key ends a Resample take; the OS still gets the key (a playing sequence stops as usual)"""
    a = m.a; kx = m.newlabel('kx')
    a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 10); a.extu_w(10, 10)
    m.imm32(K_STOP, 0); a.cmpeq(0, 10); m.bf_far(kx)
    ld(m, RS, 0); a.tst_rr(0, 0); m.bt_far(kx)
    ld(m, ST, 0); a.mov_imm(ST_REC, 1); a.cmpeq(1, 0); m.bf_far(kx)
    gcall(m, 'at_finish')
    a.movl_pool(hret0, 2); a.jmp(2); a.nop(); m.pool_jump()
    a.deflabel(kx)
pgR.on_event(2, rkey)
pgR.on_event(0x38, ev_pad)
pgR.cancel_fn = cancel


# =====================================================================================================================
if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', '..', 'AUDTRACK.WAV'); pg.build_wav(out)
    from validate import Harness
    from shasm import SHAsm
    from mpcmod import OS_PUSH_PAGE, HOOKS
    from pages import ENABLE_DOIT, ENABLE_CANCEL, SET_FOCUS
    from testkit import PageTest, FIRMWARE
    from testseq import make_seq

    class OSTest(PageTest):
        """PageTest with the OS code image in SDRAM (as the boot copy does), so the REAL sampler / Keep code runs.
        Hardware-only routines are stubbed; the harness stubs are kept."""
        def __init__(self, pg, wav, before_hub=None):
            self.pg = pg
            self.h = h = Harness(FIRMWARE); h.fill_scratch(); self.e = e = h.emu
            fw = open(FIRMWARE, 'rb').read(); OSB = 0x8849495C
            keep = [(HOOKS[h.hook][1], 4), (ENABLE_DOIT, 10), (ENABLE_CANCEL, 10)]
            saved = [(ad, [e.rd(ad + i, 2) for i in range(0, n, 2)]) for ad, n in keep]
            buf, off = e._region(OSB); buf[off:off + len(fw)] = fw
            for ad, ws in saved:
                for i, w in enumerate(ws): e.wr(ad + 2 * i, w, 2)
            # sample-list end (harness logic) in the 26 bytes of the OS function: the next one (free memory) is real
            s = SHAsm(0x884C4FE0)
            s.movl_ld(4, 1); s.movl_ld_disp(4, 4, 3); s.deflabel('l'); s.cmphs(3, 1); s.bt_lbl('z')
            s.movl_ld(1, 0); s.tst_rr(0, 0); s.bt_lbl('f'); s.add_rr(0, 1); s.bra_lbl('l'); s.nop()
            s.deflabel('z'); s.mov_imm(0, 1); s.deflabel('f'); s.rts(); s.mov_rm_rn(1, 0)
            blob = s.assemble()[:26]
            assert struct.unpack('<H', blob[22:24])[0] == 0x000B
            for i in range(0, 26, 2): e.wr(0x884C4FE0 + i, struct.unpack('<H', blob[i:i + 2])[0], 2)
            for ad in (OS_PUSH_PAGE, SET_FOCUS, 0x884C0076, 0x884C67BA): self.stub(ad)
            if before_hub: before_hub(self)
            h.load_hub(); h.load_wav(wav); h.fire(10)

    LOG = 0x88E20000                  # codec log: +0 count, entries (reg, val) from +8; +4 count-in byte at PLAY START
    def stubs(t):
        def logw(s, fixed_reg=None):
            s.movl_pool(LOG, 1); s.movl_ld(1, 0); s.add_imm(1, 0); s.movl_st(0, 1); s.shll2(0); s.shll(0); s.add_rr(0, 1)
            if fixed_reg is not None: s.mov_imm(fixed_reg - 256, 4); s.extu_b(4, 4)
            s.movl_st(4, 1); s.movl_st_disp(5, 4, 1)
            for r in (0, 2, 3, 4, 5, 6, 7): s.mov_imm(0x55, r)                 # dirty r0-r7 like a C function
            s.rts(); s.nop()
        t.stub(0x88E21000, logw); t.stub(0x88E21100, lambda s: (s.mov_rm_rn(4, 5), logw(s, 0xFF)))
        t.stub(CODEC_W, lambda s: (s.movl_pool(0x88E21000, 0), s.jmp(0), s.nop()))
        t.stub(CODEC_GAIN, lambda s: (s.movl_pool(0x88E21100, 0), s.jmp(0), s.nop()))
        t.stub(LOCATE); t.stub(TRMSG)
        def tstart(s):     # PLAY START: log the count-in setting, then behave like the OS with a 1-bar count-in
            s.movl_pool(SETB, 1); s.movb_ld(1, 0); s.movl_pool(LOG, 1); s.movl_st_disp(0, 4, 1)
            s.movl_pool(A84, 1); s.mov_imm(2, 0); s.movl_st(0, 1); s.movl_pool(B48, 1); s.movl_pool(384, 2); s.movl_st(2, 1)
            s.rts(); s.nop()
        t.stub(TSTART, tstart)
        t.stub(0x884C67BA, lambda s: (s.movl_pool(REC_FLAG, 1), s.movl_st(4, 1), s.rts(), s.nop()))   # ADC on/off flag
        t.h.ram_w32(LOG, 0); t.h.ram_w32(REC_FLAG, 0); t.h.ram_w16(GAIN_SHADOW, 0x1234)
        from mpcmod import OS_POST_EVENT
        t.stub(OS_POST_EVENT, lambda s: (s.movl_pool(POSTLOG, 1), s.movl_ld(1, 0), s.add_imm(1, 0), s.movl_st(0, 1), s.movl_ld(5, 0), s.movl_st_disp(0, 4, 1), s.rts(), s.nop()))
        t.h.ram_w32(POSTLOG, 0)
    POSTLOG = 0x88E22000              # posted events: +0 count, +4 last {type | code << 16}
    t = OSTest(pg, out, before_hub=stubs); h, e = t.h, t.e
    R32, W32 = h.ram_r32, h.ram_w32
    def clog():
        n = R32(LOG); r = [(R32(LOG + 8 * i + 8) & 0xFF, R32(LOG + 8 * i + 12)) for i in range(n)]
        W32(LOG, 0); return r
    THRU_IDLE = [(0, LVL_DEF), (1, LVL_DEF), (4, REG4_THRU)]
    def oscall(fn, *args):
        regs = [0x22220000 + i for i in range(16)]; regs[15] = 0x88400000 - 256
        for i, v in enumerate(args): regs[4 + i] = v
        e.r = regs[:]; e.pr = 0xDEADBEEE; e.pc = fn; e.maxsteps = 20_000_000
        assert e.run(stop_pc=0xDEADBEEE), "OS fn %08X" % fn
    # --- machine state: sampler (stereo, analog), input ring, program, sequence ---
    W32(SMP + 8, 0); W32(SMP + 0x0C, 0); W32(SMP + 0x10, 0); W32(0x881C05FC, 4096)   # ring size in words
    RING, RING_END = 0x884882A8, 0x8848C2A8
    W32(IN_WR, RING)
    h.ram_w16(PROG_IDX, 1)
    PROG = PROG_BASE + PROG_STRIDE
    for p in range(48):
        for l in range(4): e.wr(PROG + PAD_OFF + p * PAD_SIZE + l * 0x18, 0, 1)
        h.ram_w8(PROG + NOTE_A + p, 36 + p)
    for p in range(4):
        for i, c in enumerate(b"KICK%d" % p): e.wr(PROG + PAD_OFF + p * PAD_SIZE + i, c, 1)
    seq = make_seq(bars=2); P = ARENA_LO; arena = seq.ljust(struct.unpack('<I', seq[:4])[0], b'\0') + bytes(8)
    for i in range(0, len(arena), 4): W32(P + i, struct.unpack('<I', arena[i:i + 4])[0])
    W32(CURSEQ, P); W32(PLAYPTR, P + EV_OFF); W32(A84, 0); e.wr(SETB, 0x40 | 0x05, 1)   # your setting: REC ONLY
    h.ram_w8(BANKOBJ, 0)
    ctr = [0]; pos = [RING]; amp = [None]
    def sig(k):                         # L, R of input frame k
        if amp[0] is None: return k % 30000, (30000 - k % 30000)
        v = (k * 7) % (2 * amp[0] + 1) - amp[0]; return v, -(v // 2)
    def feed(n):                        # n input frames from the "audio IRQ"
        p = pos[0]
        for _ in range(n):
            l, r = sig(ctr[0]); ctr[0] += 1
            W32(p, (l & 0xFFFF) << 16); W32(p + 4, (r & 0xFFFF) << 16); p += 8
            if p == RING_END: p = RING
        pos[0] = p; W32(IN_WR, p)
    def loop(frames, step=128):         # OS main loop: audio arrives, sampler tick, add-on fire
        for _ in range(frames // step): feed(step); oscall(SMP_TICK, SMP); t.fire(1)
    def events():
        n = []; o = P + EV_OFF
        while True:
            b = bytes(e.rd(o + i, 1) for i in range(16)); tk = struct.unpack('<I', b[:4])[0]
            if tk == END: return n
            n.append((tk, b[4], b[5], b[6], b[7], struct.unpack('<H', b[8:10])[0])); o += 16
    def s16(v): return v - 0x10000 if v & 0x8000 else v
    status = lambda: t.label(STAT, 16).rstrip()
    def take(start_after=1024, length=2048):
        t.doit(); loop(4480); loop(start_after); feed(37); W32(A84, 1); k = ctr[0] - 1; t.fire(1); loop(length)
        W32(A84, 0); t.fire(1); return k
    def norm_done(limit=400):
        for _ in range(limit):
            if t.slot(ST) != ST_NORM: return
            t.fire(1)
        raise AssertionError("normalize did not finish")

    # --- page, settings, thru + level ---
    assert e.rd(CIBARS, 1) == 0 and e.rd(NORM, 1) == 1 and e.rd(THRU, 1) == 1 + LVL_DEF, "defaults 1BAR NRM:Y T+9.0"
    assert LEVELS[LVL_DEF] == " +9.0" and LEVELS[31] == "+12.0" and LEVELS[0] == "-34.5"
    t.close(); t.fire(2); assert clog() == THRU_IDLE, "thru on as soon as it is loaded, on any page"
    t.fire(REFRESH + 2); assert clog() == THRU_IDLE, "refresh"
    W32(REC_FLAG, 1); t.fire(2); assert clog() == [(4, REG4_OS)], "OS sampling (Record page): paused"
    W32(REC_FLAG, 0); t.fire(2); assert clog() == THRU_IDLE
    t.open(); assert clog() == THRU_IDLE and R32(SMP + 8) == 1 and t.slot(ENTERED) == 1 and R32(REC_FLAG) == 1, \
        "page open: sampler entered for the meter, thru re-applied at our level"
    e.wr(THRU, 1 + 31, 1); t.fire(1); assert clog() == [(0, 31), (1, 31), (4, REG4_THRU)], "T+12.0"
    e.wr(THRU, 0, 1); t.fire(1); assert clog() == [(4, REG4_OS)], "T OFF while sampling: reg 4 back, level is the OS's"
    e.wr(THRU, 1 + LVL_DEF, 1); t.fire(1); assert clog() == THRU_IDLE
    t.fire(REFRESH_REC); assert clog() == THRU_IDLE, "fast refresh while the sampler is ours (it is the record level)"
    W32(IXLIT, 0x88F60008); t.fire(1)                                    # MASTER's INPUT FX switched on
    assert clog() == [(0, LVL_DEF), (1, LVL_DEF), (4, REG4_OS)], "INPUT FX on: our level, no analog bypass on our page"
    t.fire(REFRESH_REC); assert clog() == [(0, LVL_DEF), (1, LVL_DEF), (4, REG4_OS)], "refreshed the same way"
    W32(IXLIT, IN_WR); t.fire(1); assert clog() == THRU_IDLE, "INPUT FX off: bypass back"
    print("  ok INPUT FX on (sampler literal redirected): on our page the level stays ours, the analog bypass goes off")
    t.close(); assert R32(SMP + 8) == 0 and t.slot(ENTERED) == 0 and R32(REC_FLAG) == 0 and clog() == THRU_IDLE, \
        "page left: sampler given back, thru re-applied"
    e.wr(THRU, 0, 1); t.fire(1); assert clog() == [(4, REG4_OS), (0xFF, 0x1234)], "T OFF: codec and line level to the OS"
    e.wr(THRU, 1 + LVL_DEF, 1); t.fire(1); assert clog() == THRU_IDLE
    print("  ok thru: on everywhere at +9.0 dB, level T-34.5..T+12.0, pauses for OS sampling, T OFF gives the codec back")
    # --- meter ---
    t.open(); clog(); amp[0] = 1000; loop(128 * 40)
    lab = t.label(STAT, 16); bar = lab[3:11]
    assert lab[:3] == "IN " and bar == "\xff\xff------" and lab[11:] in ("  -31", "  -32", "  -33"), repr(lab)
    amp[0] = None
    print("  ok meter: input peak 1000 -> %r" % lab.replace("\xff", "#"))
    # --- DO IT while playing / no sequence ---
    W32(A84, 1); t.doit(); assert t.msg() == "STOP SEQ FIRST" and t.slot(ST) == 0, t.msg(); W32(A84, 0)
    W32(CURSEQ, 0x88001000); t.doit(); assert t.msg() == "NO SEQUENCE", t.msg(); W32(CURSEQ, P)
    print("  ok DO IT refused while playing / without a sequence")
    # --- take 1: 2 bars, NRM:N -> exact alignment and stereo layout ---
    e.wr(CIBARS, 1, 1); e.wr(NORM, 0, 1); N0 = len(events())
    t.fire(REFRESH_REC); clog(); t.doit(); assert t.slot(ST) == ST_PRE and R32(SMP + 0x24) == 1 and t.slot(ENTERED) == 1
    t.fire(8); assert clog() == THRU_IDLE, "take armed: our level re-applied (it is the record level)"
    assert status().startswith("COUNT-IN"), status()
    loop(4480)
    assert R32(SMP + 0x24) == 3 and t.slot(ST) == ST_CI and status().startswith("COUNT-IN"), (R32(SMP + 0x24), t.slot(ST))
    assert (R32(LOG + 4) >> 6) & 3 == 2, "count-in forced to REC+PLAY during the start call"
    assert e.rd(SETB, 1) == 0x45, "your Count-in setting restored"
    assert R32(B48) == 768, "2 bars = 768 ticks"
    print("  ok DO IT: sampler entered + armed, pre-roll, record, PLAY START with count-in 2 bars, setting restored")
    loop(110000 // 128 * 128)
    feed(37); W32(A84, 1); K = ctr[0] - 1
    t.fire(1); assert t.slot(ST) == ST_REC, t.slot(ST)
    loop(44100 // 128 * 128)
    rl = t.label(STAT, 16); assert rl.startswith("REC ") and rl[4:10] == "\xff" * 6 and rl[12:] in ("   0", "  -1"), repr(rl)
    print("  ok meter while recording: %r (take peak held)" % rl.replace("\xff", "#"))
    W32(A84, 0); t.fire(1)
    assert t.slot(ST) == ST_PAD and status() == "HIT BLANK PAD"
    assert R32(SMP + 0x24) == 0 and t.slot(ENTERED) == 1 and R32(SMP + 8) == 1, "sampler stopped, kept for the meter"
    t.fire(2); assert THRU_IDLE[-1] in clog(), "thru back to the idle level after the take"
    B = R32(SMP + 0x90); S = R32(B + 0x20); ln = R32(B + 0x18)
    assert S == t.slot(SPOS) and R32(B + 0x28) == S and S < ln, (S, t.slot(SPOS), R32(B + 0x28), ln)
    sd = R32(B + 0x1C); assert sd >= ln and R32(B) >= 0x100 + 4 * sd, (sd, ln, R32(B))
    L = lambda i: s16(e.rd(B + 0x100 + 2 * i, 2)); Rr = lambda i: s16(e.rd(B + 0x100 + 2 * sd + 2 * i, 2))
    assert L(S - RT_FRAMES - 1) == sig(K)[0], (L(S - RT_FRAMES - 1), K)
    assert all(L(i + 1) == (L(i) + 1) % 30000 for i in range(S - 200, S + 200)), "continuous around START"
    assert all(L(i) + Rr(i) == 30000 for i in range(S - 50, S + 50)), "R after L, block +0x1C frames per channel"
    print("  ok STOP: START = %d (length %d) = frame at sequence start + %d frames round trip; R at +0x1C stride"
          % (S, ln, RT_FRAMES))
    # --- pads ---
    t.pad(0, 2); assert t.msg() == "PAD NOT EMPTY" and t.slot(ST) == ST_PAD, t.msg()
    h.ram_w16(TRKNUM, 4)                                                   # you are on track 4 (the drums are on 1)
    t.pad(0, 4); assert t.msg() == "ASSIGNED TO A05", t.msg()
    pad = PROG + PAD_OFF + 4 * PAD_SIZE
    assert bytes(e.rd(pad + i, 1) for i in range(9)) == b"AUDTRK01\0" and bytes(e.rd(B + 4 + i, 1) for i in range(8)) == b"AUDTRK01"
    assert e.rd(pad + 0x16, 1) == 1 and e.rd(pad + 0x62, 1) == 1, "Note On + Mono"
    ev = events(); assert len(ev) == N0 + 1 and (0, 4, 0x90, 40, 127, 768) in ev[:8], ev[:4]
    assert [x[0] for x in ev] == sorted(x[0] for x in ev)
    assert t.slot(ST) == ST_IDLE and R32(B) != 0
    print("  ok empty pad A05: AUDTRK01 in layer 1, Note On, Mono, note 40 at tick 0 on track 4 (the one you are on; "
          "drums on 1), vel 127, 768 ticks; full pad refused")
    # --- take 2: NRM:Y, quiet input (peak 1000) -> normalized to full scale, in pieces ---
    e.wr(CIBARS, 0, 1); e.wr(NORM, 1, 1); amp[0] = 1000
    take(start_after=1024, length=44100 // 128 * 128); assert R32(B48) == 384, "1 bar"
    B2 = R32(SMP + 0x90); S2 = R32(B2 + 0x20); ln2 = R32(B2 + 0x18); sd2 = R32(B2 + 0x1C)
    raw = {i: s16(e.rd(B2 + 0x100 + 2 * i, 2)) for i in (0, S2, S2 + 1, S2 + 3, ln2 - 1)}
    rawR = {i: s16(e.rd(B2 + 0x100 + 2 * sd2 + 2 * i, 2)) for i in (0, S2, S2 + 2, ln2 - 1)}
    after = s16(e.rd(B2 + 0x100 + 2 * ln2, 2))                     # padding between L and R: untouched
    assert t.slot(ST) == ST_NORM and status() == "NORMALIZING...", (t.slot(ST), status())
    fires = 0
    while t.slot(ST) == ST_NORM: t.fire(1); fires += 1; assert fires < 400
    assert t.slot(PEAK) == 1000, t.slot(PEAK)
    G = 0x7FFF0000 // 1000
    def scl(v): r = min((abs(v) * G) >> 16, 32767); return -r if v < 0 else r
    for i, v in raw.items(): assert s16(e.rd(B2 + 0x100 + 2 * i, 2)) == scl(v), (i, v, s16(e.rd(B2 + 0x100 + 2 * i, 2)))
    for i, v in rawR.items(): assert s16(e.rd(B2 + 0x100 + 2 * sd2 + 2 * i, 2)) == scl(v), (i, v)
    assert s16(e.rd(B2 + 0x100 + 2 * ln2, 2)) == after
    mx = max(abs(s16(e.rd(B2 + 0x100 + 2 * i, 2))) for i in range(S2, S2 + 3000))
    assert mx == scl(1000) == 32766 and status() == "HIT BLANK PAD", (mx, status())
    print("  ok NRM:Y: peak 1000 -> x%.2f, L and R scaled (%d samples in %d fires), then HIT BLANK PAD"
          % (G / 65536, 2 * ln2, fires))
    t.cancel(); assert t.msg() == "TAKE DISCARDED" and t.slot(ST) == 0 and R32(B2) == 0, t.msg()
    assert len(events()) == N0 + 1 and R32(B) != 0
    print("  ok CANCEL at HIT BLANK PAD deletes only that take")
    # --- take 3: loud input beyond the START point only clamps; CANCEL while normalizing deletes it ---
    amp[0] = None; take(); assert t.slot(ST) == ST_NORM
    t.fire(1); t.cancel(); assert t.msg() == "TAKE DISCARDED" and t.slot(ST) == 0, t.msg()
    print("  ok CANCEL while normalizing deletes the take")
    # --- CANCEL while recording; sampler already entered by the OS (Record page): left entered ---
    t.close(); assert R32(SMP + 8) == 0
    oscall(SMP_ENTER, SMP); t.open(); assert t.slot(ENTERED) == 0
    t.doit(); assert t.slot(ENTERED) == 0; loop(4480); loop(1024); W32(A84, 1); t.fire(1); loop(1024)
    B3 = R32(SMP + 0x90); t.cancel(); assert t.msg() == "TAKE CANCELLED" and t.slot(ST) == 0 and R32(B3) == 0
    t.close(); assert R32(SMP + 0x24) == 0 and R32(SMP + 8) == 1, "OS sampler state kept"
    W32(A84, 0); oscall(SMP_EXIT, SMP); t.open()
    print("  ok CANCEL while recording: take deleted, an OS-entered sampler stays entered")
    # --- next take keeps the next free name, on pad A06 ---
    e.wr(NORM, 0, 1); take(512, 512)
    t.pad(0, 5); assert t.msg() == "ASSIGNED TO A06", t.msg()
    assert bytes(e.rd(PROG + PAD_OFF + 5 * PAD_SIZE + i, 1) for i in range(8)) == b"AUDTRK02"
    print("  ok next take named AUDTRK02 (AUDTRK01 exists)")
    assert t.pad(0, 7)[0] == 0, "pads go to the OS when not waiting"
    # --- DO IT while recording = the STOP key (the OS stops the sequence, the take ends as usual) ---
    e.wr(NORM, 0, 1); amp[0] = None
    t.doit(); loop(4480); loop(512); W32(A84, 1); t.fire(1); assert t.slot(ST) == ST_REC, t.slot(ST)
    W32(POSTLOG, 0); t.doit()
    assert R32(POSTLOG) == 1 and R32(POSTLOG + 4) == (2 | (K_STOP << 16)) and t.slot(ST) == ST_REC, (R32(POSTLOG), hex(R32(POSTLOG + 4)))
    W32(A84, 0); t.fire(1); assert t.slot(ST) == ST_PAD and status() == "HIT BLANK PAD"
    t.cancel(); assert t.slot(ST) == 0
    print("  ok Audio Track: DO IT while recording posts the STOP key, the take ends, HIT BLANK PAD")

    # ===== RESAMPLE: same OS sampler, source Main (the output ring), its own page =====
    from testkit import EV_KEY, K_DOIT
    W32(0x881C05F8, 4096)
    OUT_RING, OUT_END = 0x884842A8, 0x884882A8
    W32(OUT_WR, OUT_RING); opos = [OUT_RING]
    def ofeed(n):
        p = opos[0]
        for _ in range(n):
            l, r = sig(ctr[0]); ctr[0] += 1
            W32(p, (l & 0xFFFF) << 16); W32(p + 4, (r & 0xFFFF) << 16); p += 8
            if p == OUT_END: p = OUT_RING
        opos[0] = p; W32(OUT_WR, p)
    def oloop(frames, step=128):
        for _ in range(frames // step): ofeed(step); oscall(SMP_TICK, SMP); t.fire(1)
    t.close(); t.pg = pgR
    rmsg = lambda: bytes(e.rd(pg.msgt + i, 1) for i in range(16)).split(b'\0')[0].decode('latin-1').rstrip()
    rstat = lambda: t.label(RSTAT, 8)
    def channels(B):
        n = R32(B + 0x18); sd = R32(B + 0x1C)
        return [s16(e.rd(B + 0x100 + 2 * i, 2)) for i in range(n)], [s16(e.rd(B + 0x100 + 2 * sd + 2 * i, 2)) for i in range(n)]
    def key_stop(): return t.event(2, K_STOP)
    def rtake(frames=128 * 60, level=20000, finish=None):
        """armed from silence, the threshold passes, 'frames' recorded; the raw take is read BEFORE any fire can process it"""
        amp[0] = 0; t.fire(2); t.doit(); oloop(4480 + 512); amp[0] = level; oloop(256); oloop(frames)
        assert t.slot(ST) == ST_REC, t.slot(ST)
        (finish or key_stop)(); B_ = R32(SMP + 0x90); return B_, channels(B_)
    def work(limit=800):
        for _ in range(limit):
            if t.slot(ST) == ST_PAD: return
            t.fire(1)
        raise AssertionError("did not finish")
    # page open: Main + stereo; left: the saved settings back
    W32(SMP + 0x10, 0); W32(SMP + 0x0C, 1); t.open()
    assert (R32(SMP + 0x10), R32(SMP + 0x0C), t.slot(RSON), R32(SMP + 8)) == (1, 0, 1, 1), "open: Main, stereo, sampler entered"
    assert [e.rd(s, 1) for s in (RCH, RNRM, RTHR)] == [0, 0, THR_DEF] and THR_OPTS[THR_DEF] == "T-30" and rstat() == "RESAMPLE", rstat()
    assert R32(PUB) == pgR.page, (hex(R32(PUB)), hex(pgR.page))
    t.close(); assert (R32(SMP + 0x10), R32(SMP + 0x0C), t.slot(RSON), R32(SMP + 8)) == (0, 1, 0, 0), "left: Rec In + mono back"
    W32(SMP + 0x0C, 0); W32(SMP + 0x10, 1); t.open(); t.close(); assert R32(SMP + 0x10) == 1, "a Main setting stays Main"
    W32(SMP + 0x10, 0)
    import infx as _IX; assert _IX.C_SRC - _IX.C_SPTR == IXSRC_D
    IXB_ = 0x88F60000; W32(IXLIT, IXB_ + _IX.C_SPTR)                     # MASTER's INPUT FX on
    W32(IXB_ + _IX.C_SRC, 1); t.open(); assert R32(SMP + 0x10) == 0 and R32(SMP + 8) == 1, "INPUT FX + SOURCE MAIN: Rec In (effected ring)"
    t.close(); assert R32(SMP + 0x10) == 0
    W32(IXB_ + _IX.C_SRC, 0); t.open(); assert R32(SMP + 0x10) == 1, "INPUT FX + SOURCE INPUT: plain Main"
    t.close(); assert R32(SMP + 0x10) == 0
    W32(SMP + 0x10, 1); W32(IXB_ + _IX.C_SRC, 1); t.open(); assert R32(SMP + 0x10) == 0; t.close(); assert R32(SMP + 0x10) == 1, "a Main setting comes back"
    W32(SMP + 0x10, 0); W32(IXLIT, IN_WR)
    print("  ok Resample with INPUT FX: SOURCE MAIN records the effected input ring, SOURCE INPUT plain Main, setting restored")
    print("  ok Resample opens on Record = Main + stereo, leaving restores what was set; page published for the S500 menu")
    # meter, threshold, STOP key, stereo, NRM:N: exact audio through the OS Keep
    t.open(); amp[0] = 1000; oloop(128 * 200); mt = t.label(RMETER, 4); assert mt in (" -31", " -32", " -33"), repr(mt)
    amp[0] = 0; t.fire(2); t.doit(); assert t.slot(ST) == ST_PRE; t.fire(1); assert rstat() == "   READY", repr(rstat())
    oloop(4480 + 1024); assert R32(SMP + 0x24) == 2 and t.slot(ST) == ST_PRE, "waits for the threshold"
    amp[0] = 600; oloop(2048); assert t.slot(ST) == ST_PRE, "quiet: still waiting"
    amp[0] = 20000; oloop(256); assert t.slot(ST) == ST_REC, t.slot(ST)
    t.fire(1); assert rstat() == "     REC", repr(rstat())
    oloop(128 * 60); W32(A84, 0)
    r = key_stop(); assert r == 0 and t.slot(ST) == ST_PAD and R32(A84) == 0, (r, t.slot(ST))   # the OS gets the key too
    B5 = R32(SMP + 0x90); L5, R5 = channels(B5); n5 = len(L5); assert n5 > 7000 and L5 != R5
    t.fire(1); assert rstat() == " HIT PAD" and rmsg() == "HIT BLANK PAD", (rstat(), rmsg())
    t.pad(0, 2); assert rmsg() == "PAD NOT EMPTY" and t.slot(ST) == ST_PAD, rmsg()
    t.pad(0, 8); assert rmsg() == "ASSIGNED TO A09", rmsg()
    assert bytes(e.rd(B5 + 4 + i, 1) for i in range(8)) == b"RESAMP01" and t.slot(ST) == ST_IDLE
    assert channels(B5) == (L5, R5), "stereo audio untouched by Keep (NRM:N)"
    assert R32(B5 + 0x1C) >= n5 and e.rd(B5 + 0x92, 2) != 1, "still stereo"
    print("  ok Resample: threshold, READY / REC, STOP key ends it (sequencer untouched, OS still gets STOP), HIT BLANK PAD; "
          "RESAMP01 on A09, %d stereo frames exact through Keep" % n5)
    # DO IT while waiting starts at once; DO IT ends it
    amp[0] = 0; t.fire(2); t.doit(); t.doit(); assert t.slot(FORCE) == 1; oloop(4480 + 256); assert t.slot(ST) == ST_REC, t.slot(ST)
    amp[0] = 9000; oloop(128 * 20); t.event(EV_KEY, K_DOIT); assert t.slot(ST) == ST_PAD and R32(A84) == 0
    t.pad(0, 9); assert rmsg() == "ASSIGNED TO A10", rmsg()
    assert bytes(e.rd(R32(SMP + 0x90) + 4 + i, 1) for i in range(8)) == b"RESAMP02"
    print("  ok DO IT while waiting records at once, DO IT stops; next name RESAMP02")
    # stereo NRM:Y: one gain for both channels
    e.wr(RNRM, 1, 1); B6, (L6, R6) = rtake(level=5000); assert t.slot(ST) == ST_NORM, t.slot(ST)
    t.fire(1); pc = rstat(); assert pc.endswith("%"), repr(pc); work()
    S6 = R32(B6 + 0x20); pk = max(abs(v) for v in L6[S6:] + R6[S6:]); G = 0x7FFF0000 // pk
    scale = lambda v: (lambda a_: -a_ if v < 0 else a_)(min((abs(v) * G) >> 16, 32767))
    assert channels(B6) == ([scale(v) for v in L6], [scale(v) for v in R6]), "same gain on L and R"
    t.pad(0, 10); assert rmsg() == "ASSIGNED TO A11", rmsg()
    assert channels(B6) == ([scale(v) for v in L6], [scale(v) for v in R6]), "kept"
    print("  ok stereo NRM:Y: one gain (x%.2f) on both channels (showed %s)" % (G / 65536, pc.strip()))
    # MONO NRM:N: (L + R) / 2, mono header, block cut
    e.wr(RNRM, 0, 1); e.wr(RCH, 1, 1); B7, (L7, R7) = rtake(level=12000); assert t.slot(ST) == ST_MIX, t.slot(ST); work()
    mix = [(l + r) >> 1 for l, r in zip(L7, R7)]; n7 = len(mix)
    assert [s16(e.rd(B7 + 0x100 + 2 * i, 2)) for i in range(n7)] == mix, "mixed"
    t.fire(1); t.pad(1, 0); assert rmsg() == "ASSIGNED TO B01", rmsg()
    hdr = (R32(B7 + 0x18), R32(B7 + 0x1C), e.rd(B7 + 0x92, 2), R32(B7 + 0x98), e.rd(B7 + 0x9C, 2)); assert hdr == (n7, 0, 1, 88200, 2), hdr
    assert R32(B7) == (0x100 + 2 * n7 + 3) & ~3, "cut to the mono PCM"
    assert [s16(e.rd(B7 + 0x100 + 2 * i, 2)) for i in range(n7)] == mix, "kept exactly"
    h.ram_w8(BANKOBJ, 0)
    print("  ok MONO: L + R mixed, mono header, block cut, %d frames exact" % n7)
    # MONO NRM:Y
    e.wr(RNRM, 1, 1); B8, (L8, R8) = rtake(level=3000); work()
    S8 = R32(B8 + 0x20); m8 = [(l + r) >> 1 for l, r in zip(L8, R8)]; pk8 = max(abs(v) for v in m8[S8:]); G = 0x7FFF0000 // pk8
    assert [s16(e.rd(B8 + 0x100 + 2 * i, 2)) for i in range(len(m8))] == [scale(v) for v in m8], "mixed, then normalized"
    t.cancel(); assert rmsg() == "TAKE DISCARDED" and t.slot(ST) == 0, rmsg()
    # CANCEL: waiting, recording, mixing
    e.wr(RNRM, 0, 1); amp[0] = 0; t.fire(2); t.doit(); oloop(4480 + 256); t.cancel(); assert rmsg() == "TAKE CANCELLED" and t.slot(ST) == 0
    amp[0] = 9000; t.fire(2); t.doit(); oloop(4480 + 256 + 256); assert t.slot(ST) == ST_REC; t.cancel(); assert rmsg() == "TAKE CANCELLED" and t.slot(ST) == 0
    rtake(); assert t.slot(ST) == ST_MIX; t.cancel(); assert rmsg() == "TAKE DISCARDED" and t.slot(ST) == 0
    e.wr(RCH, 0, 1)
    print("  ok MONO NRM:Y mixes then normalizes; CANCEL waiting / recording / mixing deletes the take")
    # leaving while recording: the settings wait for the end of the take
    W32(SMP + 0x10, 1); t.fire(1); W32(SMP + 0x10, 1)
    amp[0] = 9000; t.fire(2); t.doit(); oloop(4480 + 512); assert t.slot(ST) == ST_REC, t.slot(ST)
    t.close(); ofeed(128); oscall(SMP_TICK, SMP); t.fire(2)
    assert R32(SMP + 0x10) == 1 and t.slot(RSON) == 1 and R32(SMP + 8) == 1 and t.slot(ST) == ST_REC, "still Main while the take runs"
    t.event(EV_KEY, K_DOIT); t.fire(2); assert t.slot(ST) == ST_PAD and t.slot(RSON) == 0, (t.slot(ST), t.slot(RSON))
    assert R32(SMP + 0x10) == 0 and R32(SMP + 8) == 0, "take over, page left: settings and sampler back"
    t.open(); t.cancel(); assert t.slot(ST) == 0
    t.close(); t.pg = pg
    print("  ok page left while recording: Main until the take ends, then Rec In + the sampler given back")
    print("OK: AUDTRACK.WAV built and tested")
