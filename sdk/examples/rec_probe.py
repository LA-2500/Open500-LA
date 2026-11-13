#!/usr/bin/env python3
"""REC PROBE - read-only hardware probe for the coming "Rec Track" add-on (record a track over the sequence).

    python sdk/examples/rec_probe.py          -> builds RECPROBE.WAV and tests it in the emulator

It changes NOTHING in the MPC: it only reads OS memory and shows the values on its own page, so the Rec Track design
can be checked against the real machine before anything writes to the sequencer or the sampler.

On the MPC500: copy RECPROBE.WAV to the CF card, LOAD it (after HUB.WAV), then MODE -> OTHER -> "Rec Probe".
  row 0: item name (turn the wheel to choose)      row 1: its value (decimal)
Live items show the value now; "@" items are captured automatically on any page (count-in start, play start,
sampler start), so you can leave the page, do the test, come back and read them.

Addresses (MPC500 OS 1.31; SDRAM address = file offset of MPC500.bin + 0x8849495C), all found by disassembly on
2026-11-01 and NOT yet confirmed on hardware - that is what this probe is for:
  SET   0x88483F88  global settings (getter 0x884BF7B0). +0x19 bits 7..6 = Count in (0 OFF, 1 REC ONLY, 2 REC+PLAY)?
                    read by the transport at 0x884B7060 / 0x884B70FE / 0x884B8D8E.
  SEQ   0x8847C10C  sequencer/transport object (83 references). Known: +0xABC = CURSEQ 0x8847CBC8, +0xAC4 = PLAYPTR
                    0x8847CBD0 (Grid Seq, HW). New: +0xA84 transport state (0 stop, 2 count-in = 0x884B8BD4,
                    1 running = 0x884B8C08)?  +0xAAC record mode (0 play, 1 rec, 2 overdub: 0x884B8F48/7E/0x884B8FF0)?
                    +0xB14 count-in length in ticks (written by 0x884B84EE when the count-in starts)?
                    +0xA98 / +0xAA4 copied from the audio object 0x88483F58 when play starts (0x884B8ED4).
  SMP   0x8849D114  sampler object (getter 0x884EBEDC, Record page handler 0x884CC45E). +0x24 state (0 idle, 1 armed,
                    2 waiting for input, 3 recording; tick 0x884EC3D8 runs from the OS main loop on every page),
                    +0x0C mode (0 stereo), +0x10 monitor, +0x64 input-ring read index, +0x74 PCM write pointer (L),
                    +0x90 new sample block (made by 0x884EC670 at the end of the sample list).
  FRAMES 0x881C05F0 audio frame counter (Master FX / Grid Seq, HW).   INIDX 0x881C05FC input ring write index
                    (sampler enter 0x884EC06E: input ring 0x884882A8).
  SYS_PARAM 0x884E5CD0 + ID 0x2000C = tempo x10 (Grid Seq, HW).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import PageAddon, ld, st, sti
from mpcmod import EVQ

SET_CI = 0x88483F88 + 0x19
SEQ = 0x8847C10C
A84, AAC, B14, A98, AA4 = SEQ + 0xA84, SEQ + 0xAAC, SEQ + 0xB14, SEQ + 0xA98, SEQ + 0xAA4
CURSEQ = 0x8847CBC8
SMP = 0x8849D114
SMPST, SMP64, SMP74, SMP90 = SMP + 0x24, SMP + 0x64, SMP + 0x74, SMP + 0x90
FRAMES, INIDX = 0x881C05F0, 0x881C05FC
SYS_PARAM, ID_TEMPO = 0x884E5CD0, 0x2000C

ITEMS = ["Count-in set", "Transport A84", "Rec mode AAC", "Count B14", "Sampler state", "Frames",
         "@CountIn frames", "@B14 at start", "@B14 at end", "Tempo x10", "Bar 1 ticks", "Seq bars",
         "@Rec->Play fr", "@WrPtr at play", "@RingLag play", "@Smp st at play", "@Seq A98 play", "@Seq AA4 play",
         "@Frames at play", "Input index"]

pg = PageAddon("recprob1", open_press=None)
pg.other_menu("Rec Probe")
ITEM = pg.choice("", 0, 0, ITEMS, default=0)
VAL = pg.dlabel(0, 1, 16)
(PA84, PSMP, CIFR0, CILEN, B14S, B14E, RECFR0, REC2P, WRP, RLAG, RECS, SA98, SAA4, PLAYFR,
 CNT, TEMPO) = [pg.alloc_slot() for _ in range(16)]
MSGB = [pg.alloc_slot() for _ in range(4)]              # message to the system parameter manager (16 contiguous bytes)
TXT = [pg.alloc_slot() for _ in range(4)][0]           # 16-char text being formatted
for _s in range(TXT, TXT + 16, 4): del pg.defaults[_s]

def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()
def rd(m, addr, r): m.imm32(addr, 1); m.a.movl_ld(1, r)

def subs(m):
    a = m.a
    # ---- rp_cap: watch transport and sampler, latch the "@" values. Reads only. r0-r7 ----
    a.deflabel('rp_cap'); a.stsl_pr()
    same, n1, n2, n3, n4, nci, nci2 = [m.newlabel(x) for x in ('same', 'n1', 'n2', 'n3', 'n4', 'nci', 'nci2')]
    rd(m, A84, 4); ld(m, PA84, 5); a.cmpeq(4, 5); m.bt_far(same)
    st(m, 4, PA84)
    a.mov_imm(2, 0); a.cmpeq(0, 4); m.bf_far(n1)                          # -> 2: count-in starts
    rd(m, FRAMES, 0); st(m, 0, CIFR0); rd(m, B14, 0); st(m, 0, B14S); st(m, 0, B14E)
    a.deflabel(n1)
    a.mov_imm(1, 0); a.cmpeq(0, 4); m.bf_far(n2)                          # -> 1: the sequence starts
    rd(m, FRAMES, 6); st(m, 6, PLAYFR)
    a.mov_imm(2, 0); a.cmpeq(0, 5); m.bf_far(nci)
    ld(m, CIFR0, 0); a.mov_rm_rn(6, 3); a.sub(0, 3); st(m, 3, CILEN); a.bra_lbl(nci2); a.nop()
    a.deflabel(nci); sti(m, 0, CILEN); a.deflabel(nci2)
    rd(m, SMPST, 0); st(m, 0, RECS)
    ld(m, RECFR0, 0); a.mov_rm_rn(6, 3); a.sub(0, 3); st(m, 3, REC2P)
    rd(m, SMP74, 3); rd(m, SMP90, 2); a.sub(2, 3); st(m, 3, WRP)
    rd(m, INIDX, 3); rd(m, SMP64, 2); a.sub(2, 3); st(m, 3, RLAG)
    rd(m, A98, 0); st(m, 0, SA98); rd(m, AA4, 0); st(m, 0, SAA4)
    a.deflabel(n2); m.pool_jump()
    a.deflabel(same)
    a.mov_imm(2, 0); a.cmpeq(0, 4); m.bf_far(n3); rd(m, B14, 0); st(m, 0, B14E); a.deflabel(n3)   # count-in running
    rd(m, SMPST, 4); ld(m, PSMP, 5); a.cmpeq(4, 5); m.bt_far(n4)
    st(m, 4, PSMP); a.mov_imm(3, 0); a.cmpeq(0, 4); m.bf_far(n4); rd(m, FRAMES, 0); st(m, 0, RECFR0)  # recording starts
    a.deflabel(n4)
    a.ldsl_pr(); a.rts(); a.nop(); m.pool_jump()
    # ---- rp_fmt: r4 (signed) -> TXT, 16 chars, right aligned. r0-r7, r12 ----
    a.deflabel('rp_fmt')
    m.imm32(TXT, 12); m.imm32(0x20202020, 0)
    for d in (0, 4, 8, 12): a.movl_st_disp(0, d, 12)
    a.add_imm(16, 12); a.mov_imm(0, 7); pos = m.newlabel('pos'); a.cmppz(4); a.bt_lbl(pos); a.neg(4, 4); a.mov_imm(1, 7)
    a.deflabel(pos); m.imm32(0xCCCCCCCD, 3)
    lp = m.newlabel('lp'); a.deflabel(lp)
    a.dmulu_l(3, 4); a.sts_mach(0); a.shlr2(0); a.shlr(0)                 # r0 = r4 / 10
    a.mov_rm_rn(0, 1); a.shll2(1); a.add_rr(0, 1); a.shll(1)               # r1 = r0 * 10
    a.mov_rm_rn(4, 2); a.sub(1, 2); a.add_imm(0x30, 2); a.add_imm(-1, 12); a.movb_st(2, 12)
    a.mov_rm_rn(0, 4); a.tst_rr(4, 4); a.bf_lbl(lp)
    dn = m.newlabel('dn'); a.tst_rr(7, 7); a.bt_lbl(dn); a.mov_imm(0x2D, 0); a.add_imm(-1, 12); a.movb_st(0, 12)
    a.deflabel(dn); a.rts(); a.nop(); m.pool_jump()

def item_value(m, i):
    """r4 = value of item i (may use r0-r7)"""
    a = m.a
    if i == 0: m.imm32(SET_CI, 1); a.movb_ld(1, 4); a.extu_b(4, 4); a.mov_imm(-6, 0); a.shad(0, 4)
    elif i == 1: rd(m, A84, 4)
    elif i == 2: rd(m, AAC, 4)
    elif i == 3: rd(m, B14, 4)
    elif i == 4: rd(m, SMPST, 4)
    elif i == 5: rd(m, FRAMES, 4)
    elif i == 6: ld(m, CILEN, 4)
    elif i == 7: ld(m, B14S, 4)
    elif i == 8: ld(m, B14E, 4)
    elif i == 9:
        sti(m, 0, MSGB[1]); sti(m, 0x10, MSGB[0]); sti(m, ID_TEMPO, MSGB[3]); sti(m, 0, MSGB[2])
        m.imm32(MSGB[0], 4); m.imm32(SYS_PARAM, 2); a.jsr(2); a.nop()     # tempo (BPM x10), as Grid Seq
        ld(m, MSGB[1], 4)
    elif i == 10:
        rd(m, CURSEQ, 1); a.mov_rm_rn(1, 2); a.add_imm(0x30, 2); a.movl_ld(2, 5); a.add_imm(4, 2); a.movl_ld(2, 4)
        m.imm32(0xFFFFFF, 0); a.and_rr(0, 4); a.and_rr(0, 5); a.sub(5, 4)  # bar table [1] - [0]
    elif i == 11: rd(m, CURSEQ, 1); a.add_imm(0x1C, 1); a.movw_ld(1, 4); a.extu_w(4, 4)
    elif i == 12: ld(m, REC2P, 4)
    elif i == 13: ld(m, WRP, 4)
    elif i == 14: ld(m, RLAG, 4)
    elif i == 15: ld(m, RECS, 4)
    elif i == 16: ld(m, SA98, 4)
    elif i == 17: ld(m, SAA4, 4)
    elif i == 18: ld(m, PLAYFR, 4)
    elif i == 19: rd(m, INIDX, 4)

def fire(m):
    a = m.a
    call(m, 'rp_cap')
    skip = m.newlabel('skip')
    ld(m, CNT, 0); a.add_imm(1, 0); st(m, 0, CNT); a.tst_imm(7); m.bf_far(skip)    # refresh every 8 fires
    got = m.newlabel('got')
    m.imm32(ITEM, 1); a.movb_ld(1, 3); a.extu_b(3, 3)
    for i in range(len(ITEMS)):
        nx = m.newlabel('it'); a.mov_imm(i, 0); a.cmpeq(0, 3); m.bf_far(nx)
        item_value(m, i); m.a.movl_pool(got, 2); a.jmp(2); a.nop(); m.pool_jump(); a.deflabel(nx)
    a.mov_imm(0, 4)
    a.deflabel(got)
    call(m, 'rp_fmt')
    # copy into the label only what changed; redraw only then
    m.imm32(TXT, 1); m.imm32(pg.addr['dlabels'][VAL], 2); a.mov_imm(16, 3); a.mov_imm(0, 6)
    cl, cs = m.newlabel('cl'), m.newlabel('cs')
    a.deflabel(cl); a.movb_ldinc(1, 0); a.movb_ld(2, 5); a.cmpeq(0, 5); a.bt_lbl(cs); a.movb_st(0, 2); a.mov_imm(1, 6)
    a.deflabel(cs); a.add_imm(1, 2); a.dt(3); a.bf_lbl(cl)
    a.tst_rr(6, 6); m.bt_far(skip); m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(skip)
    m.a.movl_pool('__rp_end', 2); a.jmp(2); a.nop(); m.pool_jump()
    subs(m)
    a.deflabel('__rp_end')
pg.on_fire(fire)

def inactive(m):
    call(m, 'rp_cap')                                  # keep watching on every other page (a few dozen instructions)
pg.on_inactive(inactive)

if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', '..', 'RECPROBE.WAV'); pg.build_wav(out)
    from testkit import PageTest
    t = PageTest(pg, out); h, e = t.h, t.e
    t.stub(SYS_PARAM, lambda s_: (s_.movl_pool(1200, 2), s_.movl_st_disp(2, 4, 4), s_.mov_imm(1, 0), s_.rts(), s_.nop()))
    WATCH = [(SET_CI, 1), (SEQ, 0xC60), (SMP, 0xA0), (FRAMES, 0x10)]
    def snap(): return [bytes(e.rd(ad + i, 1) for i in range(n)) for ad, n in WATCH]
    def show(i):
        e.wr(ITEM, i, 1); t.fire(8); return t.label(VAL, 16)
    def num(i): return int(show(i).strip())
    t.open()
    assert t.slot(ITEM) & 0xFF == 0
    # count-in setting decoding (bits 7..6)
    for byte, want in ((0x00, 0), (0x40, 1), (0x80, 2), (0xBF, 2)):
        e.wr(SET_CI, byte, 1); assert num(0) == want, (hex(byte), show(0))
    print("  ok count-in setting: byte +0x19 >> 6 shown (0/1/2)")
    # sequence: bars and first bar length
    P = 0x88300000; h.ram_w32(CURSEQ, P); e.wr(P + 0x1C, 4, 2); h.ram_w32(P + 0x30, 0x04000000); h.ram_w32(P + 0x34, 0x04000180)
    assert num(11) == 4 and num(10) == 384, (show(11), show(10))
    assert num(9) == 1200, show(9)
    print("  ok sequence bars, bar 1 ticks, tempo")
    # a run: sampler starts recording, count-in, then play - with the page CLOSED (captured on any page)
    t.close()
    h.ram_w32(SMP90, 0x88500000); h.ram_w32(SMP74, 0x88500100); h.ram_w32(SMP64, 100); h.ram_w32(INIDX, 164)
    h.ram_w32(FRAMES, 500); h.ram_w32(SMPST, 1); t.fire(2); h.ram_w32(SMPST, 3); t.fire(2)
    h.ram_w32(FRAMES, 1000); h.ram_w32(B14, 384); h.ram_w32(A84, 2); t.fire(2)
    h.ram_w32(FRAMES, 50000); h.ram_w32(B14, 100); t.fire(2)
    h.ram_w32(SMP74, 0x88500100 + 2 * 88700); h.ram_w32(FRAMES, 89200); h.ram_w32(A84, 1); t.fire(2)
    before = snap(); t.open()
    vals = {i: num(i) for i in (1, 6, 7, 8, 12, 13, 14, 15, 18)}
    assert vals == {1: 1, 6: 88200, 7: 384, 8: 100, 12: 88700, 13: 0x100 + 2 * 88700, 14: 64, 15: 3, 18: 89200}, vals
    print("  ok captured on another page: count-in 88200 frames, B14 384 -> 100, rec->play 88700, pointers")
    # play without count-in: count-in length 0
    h.ram_w32(A84, 0); t.fire(2); h.ram_w32(FRAMES, 200000); h.ram_w32(A84, 1); t.fire(2)
    assert num(6) == 0 and num(18) == 200000
    # negative numbers and big numbers
    h.ram_w32(RLAG, (-1234) & 0xFFFFFFFF); assert show(14).strip() == "-1234", show(14)
    h.ram_w32(FRAMES, 0xFFFFFFFF); assert show(5).strip() == "-1"
    h.ram_w32(FRAMES, 0x7FFFFFFF); assert show(5).strip() == "2147483647"
    print("  ok play without count-in, negative and 10-digit values")
    # read-only: the probe itself never writes OS memory (only the test did)
    s0 = snap(); t.fire(64); t.close(); t.fire(64); assert snap() == s0, "the probe wrote OS memory"
    print("  ok nothing written to the sequencer, sampler, settings or audio counters")
    print("OK: RECPROBE.WAV built and tested (%d items)" % len(ITEMS))
