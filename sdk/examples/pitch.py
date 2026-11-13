#!/usr/bin/env python3
"""PITCH - tune pads of the current program with the Q-Link slider (catch-up) or the data wheel, -12..+12 semitones.

    python sdk/examples/pitch.py          -> builds PITCH.WAV and tests it in the emulator

On the MPC500: copy PITCH.WAV to the CF card, LOAD it (after HUB.WAV), then MODE -> OTHER -> "Pitch".
  row 0: "PITCH  PAD A05"  the pad being edited ("HIT A PAD" before the first one, "A01  EMPTY" = no samples)
  row 1: "TUNE +3   MOVE ^"  pitch of the pad (layer 1, semitones); the wheel changes it. "MOVE ^ / v": the slider
         has not caught the pad yet - move it that way; once it passes the pad's pitch it follows the slider.
Flow: hit a pad (it plays as usual) -> it becomes the pad being edited, showing its own pitch. Slider (catch-up) or wheel
  -> every layer of the pad that has a sample moves by the same number of semitones (differences between layers and fine
  tuning in cents are kept), clamped to -36..+36 semitones per layer. Hit another pad -> edit that one.
  Changes go straight into the CURRENT program (save the program as usual to keep them).

OS addresses (MPC500 OS 1.31):
  Current program 0x8837B5AC + idx*0x1F98, idx = u16 at 0x8847C0E2 (SDK README). Pad block = program + 0x18 + pad*0xA4,
  4 layers x 0x18: name16 (+0, empty = 0), tune s16 cents at +0x14 (SDK README; Scale Pads / Pad Tools, HW).
  Current bank: byte 0 of 0x8849C98C (SDK README). Pad events 0x38 (code 0..11, data = velocity).
  Q-Link slider: A/D channel 0x8848C3E4, 0..1023, noisy (Master FX, HW 2026-09-27): filtered (IIR 1/8), range learned
  outward from 64..960, 25 steps with hysteresis.
"""
import sys, os, struct
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import PageAddon, ld, st, sti
from mpcmod import EVQ

PROG_BASE, PROG_IDX, PROG_STRIDE = 0x8837B5AC, 0x8847C0E2, 0x1F98
PAD_OFF, PAD_SIZE, LAYER, TUNE_OFF = 0x18, 0xA4, 0x18, 0x14
BANKOBJ = 0x8849C98C
SLIDER = 0x8848C3E4
SL_LO, SL_HI, HYST = 64, 960, 6
STEPS = 24                      # -12..+12
TUNE_MAX = 3600                 # per layer, cents
OPTS = ["%+3d" % (i - 12) if i != 12 else "  0" for i in range(25)]

pg = PageAddon("pitch1", open_press=None)
pg.other_menu("Pitch")
pg.far_hend = True
pg.label("PITCH", 0, 0)
PADL = pg.dlabel(6, 0, 10)
IDX = pg.choice("TUNE ", 0, 1, OPTS, default=12)        # byte = semitones + 12 (the OS edits it with the wheel)
MOVEL = pg.dlabel(10, 1, 6)
(PEND, SEL, EMPTYF, LMASK, BASEIDX, LASTIDX, CAUGHT, SIDE, SS, SF, SINIT, SLO, SHI) = [pg.alloc_slot() for _ in range(13)]
BASE = [pg.alloc_slot() for _ in range(4)]               # cents of each layer when the pad was chosen
pg.defaults[SLO] = SL_LO; pg.defaults[SHI] = SL_HI; pg.defaults[SS] = 0xFF

def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()
def rdb(m, addr, r): m.imm32(addr, r); m.a.movb_ld(r, r); m.a.extu_b(r, r)

def padblk(m, rpad, rd_):
    """rd_ = pad block of pad rpad (0..47) in the current program. Uses r1, r3"""
    a = m.a
    m.imm32(PROG_IDX, 1); a.movw_ld(1, rd_); a.extu_w(rd_, rd_); m.imm32(PROG_STRIDE, 1); a.mul_l(1, rd_); a.sts_macl(rd_)
    m.imm32(PROG_BASE + PAD_OFF, 1); a.add_rr(1, rd_)
    m.imm32(PAD_SIZE, 3); a.mul_l(rpad, 3); a.sts_macl(3); a.add_rr(3, rd_)

def fire(m):
    a = m.a
    call(m, 'pt_slider')
    ld(m, PEND, 0); a.tst_rr(0, 0); np_ = m.newlabel('np'); m.bt_far(np_); call(m, 'pt_select'); a.deflabel(np_)
    disp = m.newlabel('disp')
    ld(m, SEL, 0); a.tst_rr(0, 0); m.bt_far(disp); ld(m, EMPTYF, 0); a.tst_rr(0, 0); m.bf_far(disp)
    # wheel: the OS changed the field -> apply, the slider has to catch the new pitch
    rdb(m, IDX, 9); ld(m, LASTIDX, 0); a.cmpeq(9, 0); nw = m.newlabel('nw'); m.bt_far(nw)
    st(m, 9, LASTIDX); call(m, 'pt_apply'); sti(m, 0, CAUGHT)
    rdb(m, IDX, 9); ld(m, SS, 10); a.cmphi(9, 10); a.movt(0); st(m, 0, SIDE)
    a.deflabel(nw)
    # slider, catch-up
    fol = m.newlabel('fol'); cat = m.newlabel('cat')
    ld(m, CAUGHT, 0); a.tst_rr(0, 0); m.bf_far(fol)
    rdb(m, IDX, 9); ld(m, SS, 10); a.cmpeq(9, 10); m.bt_far(cat)
    a.cmphi(9, 10); a.movt(2); ld(m, SIDE, 0); a.cmpeq(2, 0); m.bf_far(cat)      # passed the pad's pitch: caught
    a.movl_pool(disp, 1); a.jmp(1); a.nop(); m.pool_jump()
    a.deflabel(cat); sti(m, 1, CAUGHT)
    a.deflabel(fol)
    rdb(m, IDX, 9); ld(m, SS, 10); a.cmpeq(9, 10); m.bt_far(disp)
    m.imm32(IDX, 1); a.mov_rm_rn(10, 0); a.movb_st(0, 1); st(m, 10, LASTIDX); call(m, 'pt_apply')
    m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)                         # the OS redraws the TUNE field
    a.deflabel(disp)
    call(m, 'pt_disp')
    a.movl_pool('__pt_end', 2); a.jmp(2); a.nop(); m.pool_jump()
    subs(m)
    a.deflabel('__pt_end')
pg.on_fire(fire)

def subs(m):
    a = m.a
    # ---- pt_slider: SS = slider step 0..24 (filtered, learned range, hysteresis). r0-r7 ----
    a.deflabel('pt_slider'); a.stsl_pr()
    m.imm32(SLIDER, 1); a.movl_ld(1, 2); m.imm32(0x3FF, 1); a.and_rr(1, 2); a.shll2(2); a.shll2(2)   # r2 = raw*16
    ld(m, SINIT, 0); a.tst_rr(0, 0); go = m.newlabel('go'); a.bf_lbl(go)
    st(m, 2, SF); sti(m, 1, SINIT)
    a.deflabel(go)
    ld(m, SF, 0); a.sub(0, 2); a.shar(2); a.shar(2); a.shar(2); a.add_rr(2, 0); st(m, 0, SF)
    a.mov_rm_rn(0, 6); a.shar(6); a.shar(6); a.shar(6); a.shar(6)                    # r6 = f (0..1023)
    ld(m, SLO, 0); c1 = m.newlabel('c1'); a.cmpgt(6, 0); a.bf_lbl(c1); st(m, 6, SLO); a.deflabel(c1)
    ld(m, SHI, 0); c2 = m.newlabel('c2'); a.cmpgt(0, 6); a.bf_lbl(c2); st(m, 6, SHI); a.deflabel(c2)
    ld(m, SLO, 7); a.sub(7, 6)                                                        # r6 = f - lo (>= 0)
    ld(m, SHI, 5); a.sub(7, 5); a.mov_rm_rn(5, 7)                                     # r5 = r7 = span
    a.mov_rm_rn(6, 4); a.mov_imm(STEPS, 0); a.mul_l(0, 4); a.sts_macl(4)
    a.mov_rm_rn(7, 0); a.shlr(0); a.add_rr(0, 4); pg._hcall(m, '__udiv')             # r0 = round((f-lo)*24/span)
    a.mov_imm(STEPS, 1); c3 = m.newlabel('c3'); a.cmpgt(1, 0); a.bf_lbl(c3); a.mov_rm_rn(1, 0); a.deflabel(c3)
    a.mov_rm_rn(0, 3)                                                                 # r3 = target
    ld(m, SS, 0); a.cmpeq(3, 0); sx = m.newlabel('sx'); m.bt_far(sx)
    a.mov_imm(STEPS, 1); a.cmphi(1, 0); setv = m.newlabel('set'); m.bt_far(setv)      # not set yet (0xFF)
    # hysteresis: leave the current step only when f is more than half a step + HYST from its centre
    a.mov_rm_rn(0, 4); a.mul_l(7, 4); a.sts_macl(4); a.mov_imm(STEPS, 5)
    a.movl_st_dec(3, 15); pg._hcall(m, '__udiv'); a.movl_ld_inc(15, 3)                # r0 = SS*span/24
    a.sub(0, 6); a.cmppz(6); ab = m.newlabel('ab'); a.bt_lbl(ab); a.neg(6, 6); a.deflabel(ab)   # r6 = |f - centre|
    a.mov_rm_rn(7, 4); a.mov_imm(2 * STEPS, 5); a.movl_st_dec(3, 15); a.movl_st_dec(6, 15)
    pg._hcall(m, '__udiv'); a.movl_ld_inc(15, 6); a.movl_ld_inc(15, 3); a.add_imm(HYST, 0)   # r0 = span/48 + HYST
    a.cmphi(0, 6); m.bf_far(sx)
    a.deflabel(setv); st(m, 3, SS)
    a.deflabel(sx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- pt_select: PEND = pad + 1 -> SEL, layer cents, the pad's pitch in the field ----
    a.deflabel('pt_select'); a.stsl_pr()
    ld(m, PEND, 9); st(m, 9, SEL); sti(m, 0, PEND); a.add_imm(-1, 9)
    padblk(m, 9, 11)
    a.mov_imm(0, 12); a.mov_imm(-1, 13)                                               # r12 = mask, r13 = reference layer
    for l in range(4):
        sk = m.newlabel('sk')
        a.mov_rm_rn(11, 1); a.add_imm(l * LAYER + TUNE_OFF, 1); a.movw_ld(1, 0); st(m, 0, BASE[l])
        a.mov_rm_rn(11, 1); a.add_imm(l * LAYER, 1); a.movb_ld(1, 0); a.tst_rr(0, 0); a.bt_lbl(sk)
        a.mov_imm(1 << l, 0); a.or_rr(0, 12)
        a.cmppz(13); a.bt_lbl(sk); a.mov_imm(l, 13)
        a.deflabel(sk)
    st(m, 12, LMASK)
    a.tst_rr(12, 12); a.movt(0); st(m, 0, EMPTYF); se = m.newlabel('se'); m.bt_far(se)
    # pitch of the reference layer, rounded to semitones, -12..+12 -> field (0..24)
    m.imm32(BASE[0], 1); a.mov_rm_rn(13, 0); a.shll2(0); a.add_rr(0, 1); a.movl_ld(1, 4)     # r4 = cents
    a.mov_imm(0, 13); a.cmppz(4); pz = m.newlabel('pz'); a.bt_lbl(pz); a.neg(4, 4); a.mov_imm(1, 13); a.deflabel(pz)
    a.add_imm(50, 4); a.mov_imm(100, 5); pg._hcall(m, '__udiv')
    a.tst_rr(13, 13); ng = m.newlabel('ng'); a.bt_lbl(ng); a.neg(0, 0); a.deflabel(ng)
    a.mov_imm(12, 1); a.cmpgt(1, 0); h1 = m.newlabel('h1'); a.bf_lbl(h1); a.mov_rm_rn(1, 0); a.deflabel(h1)
    a.mov_imm(-12, 1); a.cmpgt(0, 1); h2 = m.newlabel('h2'); a.bf_lbl(h2); a.mov_rm_rn(1, 0); a.deflabel(h2)
    a.add_imm(12, 0); m.imm32(IDX, 1); a.movb_st(0, 1); st(m, 0, BASEIDX); st(m, 0, LASTIDX)
    # caught only if the slider is already there; otherwise remember on which side it is
    a.mov_rm_rn(0, 9); ld(m, SS, 10); a.cmpeq(9, 10); a.movt(0); st(m, 0, CAUGHT)
    a.cmphi(9, 10); a.movt(0); st(m, 0, SIDE)
    a.deflabel(se); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- pt_apply: every layer with a sample = its cents when chosen + (field - pitch when chosen) * 100 ----
    a.deflabel('pt_apply'); a.stsl_pr()
    ld(m, SEL, 9); a.add_imm(-1, 9); padblk(m, 9, 11)
    rdb(m, IDX, 0); ld(m, BASEIDX, 1); a.sub(1, 0); a.mov_imm(100, 1); a.mul_l(1, 0); a.sts_macl(12)   # r12 = delta
    ld(m, LMASK, 13); m.imm32(TUNE_MAX, 6); a.neg(6, 7)
    for l in range(4):
        sk = m.newlabel('ak')
        a.mov_rm_rn(13, 0); a.tst_imm(1 << l); a.bt_lbl(sk)
        ld(m, BASE[l], 0); a.add_rr(12, 0)
        k1 = m.newlabel('k1'); a.cmpgt(6, 0); a.bf_lbl(k1); a.mov_rm_rn(6, 0); a.deflabel(k1)
        k2 = m.newlabel('k2'); a.cmpgt(0, 7); a.bf_lbl(k2); a.mov_rm_rn(7, 0); a.deflabel(k2)
        a.mov_rm_rn(11, 1); a.add_imm(l * LAYER + TUNE_OFF, 1); a.movw_st(0, 1)
        a.deflabel(sk)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- pt_disp: row 0 pad, row 1 catch hint; redraw only when the text changed ----
    a.deflabel('pt_disp'); a.stsl_pr()
    m.imm32(pg.addr['dlabels'][PADL], 12); a.mov_imm(0, 14)                           # r14 = changed
    def cput(s):                                                                      # compare-and-write a char string
        for ch in s:
            n = m.newlabel('cp'); a.movb_ld(12, 1); a.mov_imm(ord(ch), 0); a.cmpeq(0, 1); a.bt_lbl(n)
            a.movb_st(0, 12); a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
    def cputr(r):                                                                     # compare-and-write the char in r
        n = m.newlabel('cr'); a.movb_ld(12, 1); a.cmpeq(r, 1); a.bt_lbl(n); a.mov_rm_rn(r, 0); a.movb_st(0, 12)
        a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
    nos, emp, row1 = m.newlabel('nos'), m.newlabel('emp'), m.newlabel('row1')
    ld(m, SEL, 9); a.tst_rr(9, 9); m.bt_far(nos)
    a.add_imm(-1, 9); a.mov_rm_rn(9, 4); a.mov_imm(12, 5); pg._hcall(m, '__udiv')     # r0 = bank, r1 = pad
    a.add_imm(ord('A'), 0); a.mov_rm_rn(0, 10); a.add_imm(1, 1); a.mov_rm_rn(1, 4); a.mov_imm(10, 5)
    pg._hcall(m, '__udiv'); a.add_imm(0x30, 0); a.mov_rm_rn(0, 11); a.add_imm(0x30, 1); a.mov_rm_rn(1, 13)
    ld(m, EMPTYF, 0); a.tst_rr(0, 0); m.bf_far(emp)
    cput("PAD "); cputr(10); cputr(11); cputr(13); cput("   "); a.bra_lbl(row1); a.nop()
    a.deflabel(emp); cputr(10); cputr(11); cputr(13); cput("  EMPTY"); a.bra_lbl(row1); a.nop()
    m.pool_jump()
    a.deflabel(nos); cput("HIT A PAD ")
    a.deflabel(row1)
    m.imm32(pg.addr['dlabels'][MOVEL], 12)
    blank, up = m.newlabel('blank'), m.newlabel('up'); dn = m.newlabel('dn')
    ld(m, SEL, 0); a.tst_rr(0, 0); m.bt_far(blank); ld(m, EMPTYF, 0); a.tst_rr(0, 0); m.bf_far(blank)
    ld(m, CAUGHT, 0); a.tst_rr(0, 0); m.bf_far(blank)
    rdb(m, IDX, 9); ld(m, SS, 10); a.cmphi(10, 9); m.bt_far(up)                       # slider below the pad: up
    cput("MOVE v"); a.bra_lbl(dn); a.nop()
    a.deflabel(up); cput("MOVE ^"); a.bra_lbl(dn); a.nop()
    a.deflabel(blank); cput("      ")
    a.deflabel(dn)
    a.tst_rr(14, 14); px = m.newlabel('px'); m.bt_far(px); m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(px); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

def ev_pad(m, hret0, hret1):
    """a pad hit chooses the pad to edit (fire does the work); the OS plays it as usual"""
    a = m.a
    a.movl_ld_disp(8, 4, 0); a.tst_rr(0, 0); m.bt_far(hret0)                          # release
    a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 9); a.extu_w(9, 9); a.mov_imm(12, 0); a.cmphs(0, 9); m.bt_far(hret0)
    m.imm32(BANKOBJ, 1); a.movb_ld(1, 0); a.and_imm(3); a.mov_imm(12, 2); a.mul_l(2, 0); a.sts_macl(0); a.add_rr(0, 9)
    a.add_imm(1, 9); st(m, 9, PEND)
    a.movl_pool(hret0, 2); a.jmp(2); a.nop()
    m.pool_jump()
pg.on_event(0x38, ev_pad)


# =====================================================================================================================
if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', '..', 'PITCH.WAV'); pg.build_wav(out)
    from testkit import PageTest
    t = PageTest(pg, out); h, e = t.h, t.e
    R32, W32 = h.ram_r32, h.ram_w32
    h.ram_w16(PROG_IDX, 1); PROG = PROG_BASE + PROG_STRIDE
    def lay(p, l): return PROG + PAD_OFF + p * PAD_SIZE + l * LAYER
    def tune(p, l): v = e.rd(lay(p, l) + TUNE_OFF, 2); return v - 0x10000 if v & 0x8000 else v
    def setpad(p, layers):               # layers: list of (name, cents) for layers 1..4 (None = empty)
        for l in range(4):
            for i in range(16): e.wr(lay(p, l) + i, 0, 1)
            e.wr(lay(p, l) + TUNE_OFF, 0, 2)
            if l < len(layers) and layers[l]:
                n, c = layers[l]
                for i, ch in enumerate(n.encode()): e.wr(lay(p, l) + i, ch, 1)
                e.wr(lay(p, l) + TUNE_OFF, c & 0xFFFF, 2)
    for p in range(48): setpad(p, [])
    setpad(4, [("SNARE", 350), ("SNARE2", -200)])
    setpad(5, [("HAT", 0)])
    setpad(14, [None, ("PERC", 3500)])                                   # B03: only layer 2, near the top
    e.wr(lay(4, 2) + TUNE_OFF, 777, 2)                                   # empty layer 3 of A05: never touched
    snap = lambda: bytes(e.rd(PROG + i, 1) for i in range(PROG_STRIDE))
    def slider(k, fires=60):             # put the slider on step k (0..24 = -12..+12)
        W32(SLIDER, SL_LO + round(k * (SL_HI - SL_LO) / STEPS)); t.fire(fires)
    lab = lambda: (t.label(PADL, 10).rstrip(), t.label(MOVEL, 6).rstrip())
    idx = lambda: e.rd(IDX, 1) - 12
    W32(SLIDER, (SL_LO + SL_HI) // 2); t.open(); slider(12)
    assert (R32(SLO), R32(SHI)) == (SL_LO, SL_HI), "range not widened by a reading inside it"
    assert lab() == ("HIT A PAD", ""), lab()
    e.wr(IDX, 20, 1); t.fire(3); before = snap(); slider(20); assert snap() == before, "no pad: nothing changes"
    print("  ok no pad chosen: slider and wheel change nothing")
    # A05: layer 1 +3.50, layer 2 -2.00 -> shows +4 (rounded), slider at 0 is below
    slider(12); t.pad(0, 4); t.fire(2)
    assert idx() == 4 and lab() == ("PAD A05", "MOVE ^"), (idx(), lab())
    before = snap()
    for k in (8, 14, 15): slider(k); assert snap() == before and idx() == 4, k          # below: nothing
    print("  ok A05 chosen: shows its own pitch +4, slider below -> MOVE ^, moving it below changes nothing")
    slider(16); assert snap() == before and lab()[1] == "", "caught at the pad's pitch, no change"
    W32(EVQ + 0x70, 0); slider(17, fires=40); assert idx() == 5 and R32(EVQ + 0x70) == 1, "slider change redraws the value"
    W32(EVQ + 0x70, 0); t.fire(20); assert R32(EVQ + 0x70) == 0, "no redraw while nothing changes (no flicker)"
    slider(19); assert idx() == 7 and (tune(4, 0), tune(4, 1), tune(4, 2), tune(4, 3)) == (650, 100, 777, 0), \
        (idx(), tune(4, 0), tune(4, 1))
    print("  ok caught at +4, slider +7 -> layer 1 +6.50, layer 2 +1.00 (same shift), empty layers untouched")
    # noise around a step does not move it
    import random; rnd = random.Random(5); v0 = SL_LO + round(19 * (SL_HI - SL_LO) / STEPS)
    for _ in range(200): W32(SLIDER, v0 + rnd.randint(-10, 10)); t.fire(1)
    assert idx() == 7 and tune(4, 0) == 650, "slider noise"
    # wheel: +3 -> layers follow, the slider has to catch again (it is above now)
    e.wr(IDX, 15, 1); t.fire(2)
    assert (tune(4, 0), tune(4, 1)) == (250, -300) and lab()[1] == "MOVE v", (tune(4, 0), tune(4, 1), lab())
    slider(18); assert tune(4, 0) == 250, "not caught: above"
    slider(15); slider(13); assert idx() == 1 and tune(4, 0) == 50 and tune(4, 1) == -500, (idx(), tune(4, 0))
    print("  ok wheel +3 -> layers +2.50/-3.00, slider must catch again (MOVE v), then follows down to +1")
    # A06: its own pitch 0; slider at +1 is above; going down past 0 catches
    t.pad(0, 5); t.fire(2); assert idx() == 0 and lab() == ("PAD A06", "MOVE v"), (idx(), lab())
    slider(10); assert idx() == -2 and tune(5, 0) == -200 and tune(4, 0) == 50, (idx(), tune(5, 0))
    print("  ok A06: shows 0, slider crossing 0 downwards catches -> -2 (A05 untouched)")
    # empty pad
    before = snap(); t.pad(0, 0); t.fire(2); assert lab() == ("A01  EMPTY", ""), lab()
    slider(24); e.wr(IDX, 3, 1); t.fire(3); assert snap() == before
    print("  ok empty pad A01: nothing to tune")
    # bank B, only layer 2, clamped at +36 semitones
    h.ram_w8(BANKOBJ, 1); t.pad(1, 2); t.fire(2); assert lab()[0] == "PAD B03" and idx() == 12, (lab(), idx())
    h.ram_w8(BANKOBJ, 1); slider(12); slider(24); assert idx() == 12 and tune(14, 1) == 3500
    e.wr(IDX, 0, 1); t.fire(2); assert tune(14, 1) == 3500 - 2400, tune(14, 1)
    e.wr(IDX, 24, 1); t.fire(2); assert tune(14, 1) == 3500
    h.ram_w8(BANKOBJ, 0)
    print("  ok bank B pad B03 (sample only on layer 2: shown at +12 = clamped display, +35 cents kept, max +36)")
    # nothing but tunes ever written
    mask = {lay(p, l) + TUNE_OFF + i for p in range(48) for l in range(4) for i in (0, 1)}
    s0 = snap(); slider(3); slider(20)
    assert all(a == b for i, (a, b) in enumerate(zip(s0, snap())) if PROG + i not in mask)
    assert t.pad(0, 7)[0] == 0, "pads still play (event left to the OS)"
    print("OK: PITCH.WAV built and tested")
