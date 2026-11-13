#!/usr/bin/env python3
"""FX PROBE - read-only hardware probe for the coming live INPUT FX (delay + reverb on what the MPC records).

    python sdk/examples/fx_probe.py          -> builds FXPROBE.WAV and tests it in the emulator

It changes NOTHING in the MPC: it only reads OS memory and shows the values on its own page.
On the MPC500: LOAD FXPROBE.WAV (after HUB.WAV), MODE -> OTHER -> "FX Probe".
  row 0: item name (turn the wheel to choose)      row 1: its value (decimal)
Live items show the value now; "@" items are captured automatically on any page while the sampler RECORDS, so you can
leave the page, record, come back and read them.

Questions (2026-11-04) and the addresses involved (MPC500 OS 1.31, firmware disassembly, NOT confirmed on hardware):
  - does the input ring run (and carry audio) when no record page is open?
      RX interrupt 0x884C0DBC: 8 longs from @(0x64,GBR) -> ring 0x884882A8..0x8848C2A8, write pointer [0xA5017020].
  - which ring does the sampler read with "Record: Rec In" / "Main"?
      sampler tick, state 3 (0x884ECC5C): SMP+0x10 == 0 -> reads the INPUT ring from SMP+0x44 up to [0xA5017020];
      SMP+0x10 != 0 -> reads the OUTPUT ring 0x884842A8..0x884882A8 from SMP+0x50 up to [0x881C05F4] (TX pointer).
  SMP 0x8849D114 (+0x24 state, 3 = recording), REC_FLAG 0x881C297C (ADC on for the OS), FRAMES 0x881C05F0.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import PageAddon, ld, st, sti
from mpcmod import EVQ

SMP = 0x8849D114
SMPST, SMPSRC, SMP44, SMP50 = SMP + 0x24, SMP + 0x10, SMP + 0x44, SMP + 0x50
REC_FLAG, FRAMES = 0x881C297C, 0x881C05F0
IN_WR, IN_BASE = 0xA5017020, 0x884882A8
OUT_RPTR, OUT_BASE = 0x881C05F4, 0x884842A8
RMASK = 0x3FF8                       # both rings: 16 KB = 2048 frames x 8 bytes
NPK = 32                             # frames looked at per fire for the peaks
WIN = 64                             # fires per "moving" / peak window

ITEMS = ["Sampler state", "Smp source +10", "Rec flag", "In ring moving", "Out ring moving", "In peak", "Out peak",
         "@Source at rec", "@In rd moves", "@Out rd moves", "@Rec frames"]

pg = PageAddon("fxprob1", open_press=None)
pg.other_menu("FX Probe")
ITEM = pg.choice("", 0, 0, ITEMS, default=0)
VAL = pg.dlabel(0, 1, 16)
(PIN, POUT, INMOV, OUTMOV, INSH, OUTSH, INPK, OUTPK, INPKS, OUTPKS, NF, PSMP, SRC, R44, R50, P44, P50, RECF0, RECLEN,
 CNT) = [pg.alloc_slot() for _ in range(20)]
TXT = [pg.alloc_slot() for _ in range(4)][0]           # 16-char text being formatted
for _s in range(TXT, TXT + 16, 4): del pg.defaults[_s]

def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()
def rd(m, addr, r): m.imm32(addr, 1); m.a.movl_ld(1, r)

def moved(m, addr, prev, cnt):
    """cnt += 1 if [addr] != prev; prev = [addr]. r0, r1, r4, r5"""
    a = m.a; same = m.newlabel('same')
    rd(m, addr, 4); ld(m, prev, 5); a.cmpeq(4, 5); a.bt_lbl(same)
    st(m, 4, prev); ld(m, cnt, 0); a.add_imm(1, 0); st(m, 0, cnt)
    a.deflabel(same)

def peak(m, ptr_addr, base, pk):
    """pk = max(pk, |L|) over the NPK frames before [ptr_addr] in the ring at base. r0-r7"""
    a = m.a
    rd(m, ptr_addr, 6); m.imm32(base, 7); a.sub(7, 6); m.imm32(RMASK, 3); a.mov_imm(NPK, 2); ld(m, pk, 5); a.mov_imm(-16, 4)
    lp, pos, sk = m.newlabel('pl'), m.newlabel('pp'), m.newlabel('ps')
    a.deflabel(lp)
    a.add_imm(-8, 6); a.and_rr(3, 6); a.mov_rm_rn(6, 1); a.add_rr(7, 1); a.movl_ld(1, 0); a.shad(4, 0)
    a.cmppz(0); a.bt_lbl(pos); a.neg(0, 0); a.deflabel(pos)
    a.cmpgt(5, 0); a.bf_lbl(sk); a.mov_rm_rn(0, 5); a.deflabel(sk)
    a.dt(2); a.bf_lbl(lp)
    st(m, 5, pk)

def subs(m):
    a = m.a
    # ---- fp_cap: every fire, any page. Reads only (OS memory); writes only the probe's own slots. r0-r7 ----
    a.deflabel('fp_cap'); a.stsl_pr()
    moved(m, IN_WR, PIN, INMOV); moved(m, OUT_RPTR, POUT, OUTMOV)
    peak(m, IN_WR, IN_BASE, INPK); m.pool_jump(); peak(m, OUT_RPTR, OUT_BASE, OUTPK)
    nw = m.newlabel('nw')
    ld(m, NF, 0); a.add_imm(1, 0); st(m, 0, NF); a.tst_imm(WIN - 1); m.bf_far(nw)          # every WIN fires: latch
    for src, dst in ((INMOV, INSH), (OUTMOV, OUTSH), (INPK, INPKS), (OUTPK, OUTPKS)): ld(m, src, 0); st(m, 0, dst); sti(m, 0, src)
    a.deflabel(nw); m.pool_jump()
    # sampler: recording starts -> source + read pointers; while recording count which read pointer moves
    nst, nrec = m.newlabel('nst'), m.newlabel('nrec')
    rd(m, SMPST, 4); ld(m, PSMP, 5); a.cmpeq(4, 5); m.bt_far(nst)
    st(m, 4, PSMP); a.mov_imm(3, 0); a.cmpeq(0, 4); m.bf_far(nst)
    rd(m, SMPSRC, 0); st(m, 0, SRC); sti(m, 0, R44); sti(m, 0, R50)
    rd(m, SMP44, 0); st(m, 0, P44); rd(m, SMP50, 0); st(m, 0, P50); rd(m, FRAMES, 0); st(m, 0, RECF0)
    a.deflabel(nst); m.pool_jump()
    rd(m, SMPST, 4); a.mov_imm(3, 0); a.cmpeq(0, 4); m.bf_far(nrec)
    moved(m, SMP44, P44, R44); moved(m, SMP50, P50, R50)
    rd(m, FRAMES, 0); ld(m, RECF0, 1); a.sub(1, 0); st(m, 0, RECLEN)
    a.deflabel(nrec)
    a.ldsl_pr(); a.rts(); a.nop(); m.pool_jump()
    # ---- fp_fmt: r4 (signed) -> TXT, 16 chars, right aligned. r0-r7, r12 (as Rec Probe) ----
    a.deflabel('fp_fmt')
    m.imm32(TXT, 12); m.imm32(0x20202020, 0)
    for d in (0, 4, 8, 12): a.movl_st_disp(0, d, 12)
    a.add_imm(16, 12); a.mov_imm(0, 7); pos = m.newlabel('pos'); a.cmppz(4); a.bt_lbl(pos); a.neg(4, 4); a.mov_imm(1, 7)
    a.deflabel(pos); m.imm32(0xCCCCCCCD, 3)
    lp = m.newlabel('lp'); a.deflabel(lp)
    a.dmulu_l(3, 4); a.sts_mach(0); a.shlr2(0); a.shlr(0)
    a.mov_rm_rn(0, 1); a.shll2(1); a.add_rr(0, 1); a.shll(1)
    a.mov_rm_rn(4, 2); a.sub(1, 2); a.add_imm(0x30, 2); a.add_imm(-1, 12); a.movb_st(2, 12)
    a.mov_rm_rn(0, 4); a.tst_rr(4, 4); a.bf_lbl(lp)
    dn = m.newlabel('dn'); a.tst_rr(7, 7); a.bt_lbl(dn); a.mov_imm(0x2D, 0); a.add_imm(-1, 12); a.movb_st(0, 12)
    a.deflabel(dn); a.rts(); a.nop(); m.pool_jump()

VALS = [lambda m: rd(m, SMPST, 4), lambda m: rd(m, SMPSRC, 4), lambda m: rd(m, REC_FLAG, 4),
        lambda m: ld(m, INSH, 4), lambda m: ld(m, OUTSH, 4), lambda m: ld(m, INPKS, 4), lambda m: ld(m, OUTPKS, 4),
        lambda m: ld(m, SRC, 4), lambda m: ld(m, R44, 4), lambda m: ld(m, R50, 4), lambda m: ld(m, RECLEN, 4)]
assert len(VALS) == len(ITEMS)

def fire(m):
    a = m.a
    call(m, 'fp_cap')
    skip = m.newlabel('skip')
    ld(m, CNT, 0); a.add_imm(1, 0); st(m, 0, CNT); a.tst_imm(7); m.bf_far(skip)    # refresh every 8 fires
    got = m.newlabel('got')
    m.imm32(ITEM, 1); a.movb_ld(1, 3); a.extu_b(3, 3)
    for i, fn in enumerate(VALS):
        nx = m.newlabel('it'); a.mov_imm(i, 0); a.cmpeq(0, 3); m.bf_far(nx)
        fn(m); m.a.movl_pool(got, 2); a.jmp(2); a.nop(); m.pool_jump(); a.deflabel(nx)
    a.mov_imm(0, 4)
    a.deflabel(got)
    call(m, 'fp_fmt')
    m.imm32(TXT, 1); m.imm32(pg.addr['dlabels'][VAL], 2); a.mov_imm(16, 3); a.mov_imm(0, 6)
    cl, cs = m.newlabel('cl'), m.newlabel('cs')
    a.deflabel(cl); a.movb_ldinc(1, 0); a.movb_ld(2, 5); a.cmpeq(0, 5); a.bt_lbl(cs); a.movb_st(0, 2); a.mov_imm(1, 6)
    a.deflabel(cs); a.add_imm(1, 2); a.dt(3); a.bf_lbl(cl)
    a.tst_rr(6, 6); m.bt_far(skip); m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(skip)
    m.a.movl_pool('__fp_end', 2); a.jmp(2); a.nop(); m.pool_jump()
    subs(m)
    a.deflabel('__fp_end')
pg.on_fire(fire)
pg.on_inactive(lambda m: call(m, 'fp_cap'))           # keep watching on every other page

if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', '..', 'FXPROBE.WAV'); pg.build_wav(out)
    from testkit import PageTest
    t = PageTest(pg, out); h, e = t.h, t.e
    W32, R32 = h.ram_w32, h.ram_r32
    WATCH = [(SMP, 0xA0), (REC_FLAG, 4), (FRAMES, 0x10), (IN_BASE, 0x4000), (OUT_BASE, 0x4000)]
    def snap(): return [bytes(e.rd(ad + i, 1) for i in range(n)) for ad, n in WATCH]
    def show(i):
        e.wr(ITEM, i, 1); t.fire(8); return t.label(VAL, 16)
    def num(i): return int(show(i).strip())
    def put(base, k, l): W32(base + 8 * k, (l & 0xFFFF) << 16); W32(base + 8 * k + 4, 0)
    # rings: input carries a signal with peak 12000 (negative), output peak 3000
    for k in range(2048): put(IN_BASE, k, 0); put(OUT_BASE, k, 0)
    put(IN_BASE, 99, -12000); put(IN_BASE, 90, 5000); put(OUT_BASE, 500, 3000)
    W32(IN_WR, IN_BASE + 8 * 100); W32(OUT_RPTR, OUT_BASE + 8 * 510); W32(SMPST, 0); W32(SMPSRC, 0)
    t.open()
    def run(fires, step_in=4, step_out=4):           # the audio interrupts move the pointers between fires
        for _ in range(fires):
            if step_in: W32(IN_WR, IN_BASE + ((R32(IN_WR) - IN_BASE + 8 * step_in) & RMASK))
            if step_out: W32(OUT_RPTR, OUT_BASE + ((R32(OUT_RPTR) - OUT_BASE + 8 * step_out) & RMASK))
            t.fire(1)
    # the pointer at frame 100: peak of frames 68..99 = 12000; then keep it still for the peak window
    t.fire(WIN * 2); assert num(5) == 12000, show(5)
    W32(OUT_RPTR, OUT_BASE + 8 * 510); t.fire(WIN * 2); assert num(6) == 3000, show(6)
    assert num(3) == 0 and num(4) == 0, (show(3), show(4))
    print("  ok peaks of the last %d frames before each pointer (in 12000, out 3000); pointers still -> not moving" % NPK)
    run(WIN * 3); assert num(3) >= WIN - 8 and num(4) >= WIN - 8, (show(3), show(4))     # show() fires: pointers still
    run(WIN * 3, step_in=0); assert num(3) == 0 and num(4) >= WIN - 8, (show(3), show(4))
    print("  ok 'moving' = fires out of %d in which the ring pointer moved (in stopped -> 0)" % WIN)
    # ring wrap: pointer at the start, the frames before it are at the END of the ring
    W32(IN_WR, IN_BASE); put(IN_BASE, 2047, 7777); t.fire(WIN * 2); assert num(5) == 7777, show(5)
    print("  ok peak across the ring wrap")
    # recording from MAIN with the page CLOSED: source 1, the output read pointer moves, 3000 frames
    t.close()
    W32(SMPSRC, 1); W32(FRAMES, 1000); W32(SMPST, 3); t.fire(1)
    for k in range(20): W32(SMP50, 0x100 + k); W32(FRAMES, 1000 + 150 * (k + 1)); t.fire(1)
    W32(SMPST, 0); t.fire(2); t.open()
    assert (num(7), num(8), num(9), num(10)) == (1, 0, 20, 3000), [show(i) for i in (7, 8, 9, 10)]
    W32(SMPSRC, 0); W32(SMPST, 3); t.fire(1)
    for k in range(5): W32(SMP44, 0x200 + k); t.fire(1)
    W32(SMPST, 0); t.fire(1)
    assert (num(7), num(8), num(9)) == (0, 5, 0), [show(i) for i in (7, 8, 9)]
    print("  ok captured while recording on another page: source, which read pointer moved, length in frames")
    W32(REC_FLAG, 1); assert num(2) == 1 and num(0) == 0 and num(1) == 0
    s0 = snap(); t.fire(64); t.close(); t.fire(64); assert snap() == s0, "the probe wrote OS memory"
    print("  ok nothing written to the sampler, flags, counters or audio rings")
    print("OK: FXPROBE.WAV built and tested (%d items)" % len(ITEMS))
