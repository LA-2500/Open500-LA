#!/usr/bin/env python3
"""MIXER - volume, low-pass filter cutoff and pan of the current program's pads with the Q-Link slider (catch-up) or the
data wheel, 0..100 like the MPC's own Program screens. One pad at a time, or the whole program together.

    python sdk/examples/mixer.py          -> builds MIXER.WAV and tests it in the emulator

On the MPC500: copy MIXER.WAV to the CF card, LOAD it (after HUB.WAV), then MODE -> OTHER -> "Mixer".
  row 0: "VOL PAD      A05"  parameter VOL / LPF / PAN . scope PAD / PROG . the pad you hit ("HIT PAD" before one)
  row 1: "VALUE 80  MOVE ^"  the pad's value: 0..100 (VOL, LPF), L50..C..R50 (PAN); the wheel changes it.
         "MOVE ^ / v": the slider has not caught the value yet - move it that way; once it passes the value it follows.
Flow: hit a pad (it plays as usual) -> it becomes the pad shown, with its own value. Slider (catch-up) or wheel:
  PAD  -> only that pad changes.
  PROG -> every pad of the program that has a sample (and the pad shown) moves by the same amount, so the balance between
          pads is kept; each pad stops at 0 / 100 and comes back exactly when you move back (the values when you hit the
          pad / changed parameter or scope are the reference). Hitting another pad just changes the pad shown.
  Changing the parameter, the scope or the program takes a new reference. Changes go straight into the CURRENT program
  (save the program as usual to keep them). The filter type is not changed: LPF only acts when the pad's filter is on.

OS addresses (MPC500 OS 1.31):
  Current program 0x8837B5AC + idx*0x1F98, idx = u16 at 0x8847C0E2; pad block = program + 0x18 + pad*0xA4; layer names
  at +0, +0x18, +0x30, +0x48 (empty = 0) (SDK README; Pitch, HW). Current bank: byte 0 of 0x8849C98C. Pad events 0x38.
  Pad +0x8F mixer level 0..100 (SDK README; Pad Mute, HW). +0x90 pan 0..100, 50 = centre (Pad Tools PAD_INIT default).
  +0x72 filter 1 cutoff 0..100: from the OS pad defaults (PAD_INIT 0x884B52D0 run in the emulator 2026-11-02: +0x71 = 1,
  +0x72 = 100, +0x73 = 0, filter 2 the same at +0x79..; MPC1000 PGM layout shifted by one byte from +0x65). NOT yet
  confirmed on hardware: set a pad's cutoff on the OS page, hit it here, the value must match.
  Q-Link slider: A/D channel 0x8848C3E4, 0..1023, noisy (Master FX, HW): filtered (IIR 1/8), range learned outward from
  64..960, 101 steps with hysteresis (Pitch).
"""
import sys, os, struct
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import PageAddon, ld, st, sti
from mpcmod import EVQ

PROG_BASE, PROG_IDX, PROG_STRIDE = 0x8837B5AC, 0x8847C0E2, 0x1F98
PAD_OFF, PAD_SIZE, LAYER = 0x18, 0xA4, 0x18
NPADS = 48
BANKOBJ = 0x8849C98C
SLIDER = 0x8848C3E4
SL_LO, SL_HI, HYST = 64, 960, 3
STEPS = 100
PARAMS = ["VOL", "LPF", "PAN"]
POFF = [0x8F, 0x72, 0x90]                                 # byte in the pad block
PANBASE = 101                                             # PAN entries of the value field start here
def pan_txt(v): return "  C" if v == 50 else ("L%2d" % (50 - v) if v < 50 else "R%2d" % (v - 50))
OPTS = ["%3d" % v for v in range(101)] + [pan_txt(v) for v in range(101)]

pg = PageAddon("mixer1", open_press=None)
pg.other_menu("Mixer")
pg.far_hend = True
PARAM = pg.choice("", 0, 0, PARAMS, default=0)
SCOPE = pg.choice(" ", 3, 0, ["PAD ", "PROG"], default=0)
PADL = pg.dlabel(9, 0, 7)
IDX = pg.choice("VALUE ", 0, 1, OPTS, default=0)       # byte = value (+101 for PAN): the OS edits it with the wheel
MOVEL = pg.dlabel(10, 1, 6)
(PEND, SEL, BASEV, LASTV, CAUGHT, SIDE, SS, SF, SINIT, SLO, SHI, LPARAM, LSCOPE, LPROG) = \
    [pg.alloc_slot() for _ in range(14)]
pg.defaults[SLO] = SL_LO; pg.defaults[SHI] = SL_HI; pg.defaults[SS] = 0xFF
REF = pg.alloc_buf(NPADS)                                 # value of each pad when the reference was taken
INCL = pg.alloc_buf(NPADS)                                # 1 = this pad moves

def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()
def rdb(m, addr, r): m.imm32(addr, r); m.a.movb_ld(r, r); m.a.extu_b(r, r)
def prog(m, rd_):
    """rd_ = first pad block of the current program. Uses r1"""
    a = m.a
    m.imm32(PROG_IDX, 1); a.movw_ld(1, rd_); a.extu_w(rd_, rd_); m.imm32(PROG_STRIDE, 1); a.mul_l(1, rd_); a.sts_macl(rd_)
    m.imm32(PROG_BASE + PAD_OFF, 1); a.add_rr(1, rd_)
def poff(m, r):
    """r = offset of the chosen parameter in the pad block. Uses r0, r1"""
    a = m.a
    rdb(m, PARAM, 0); m.imm32(POFF[0], r); a.mov_imm(1, 1)
    n1, n2 = m.newlabel('po1'), m.newlabel('po2')
    a.cmpeq(1, 0); a.bf_lbl(n1); m.imm32(POFF[1], r); a.deflabel(n1)
    a.mov_imm(2, 1); a.cmpeq(1, 0); a.bf_lbl(n2); m.imm32(POFF[2], r); a.deflabel(n2)

def fire(m):
    a = m.a
    call(m, 'mx_slider')
    sel, disp = m.newlabel('sel'), m.newlabel('disp')
    ld(m, PEND, 0); a.tst_rr(0, 0); m.bf_far(sel)
    ld(m, SEL, 0); a.tst_rr(0, 0); m.bt_far(disp)
    # parameter, scope or program changed -> new reference
    nr = m.newlabel('nr')
    rdb(m, PARAM, 0); ld(m, LPARAM, 1); a.cmpeq(1, 0); m.bf_far(sel)
    rdb(m, SCOPE, 0); ld(m, LSCOPE, 1); a.cmpeq(1, 0); m.bf_far(sel)
    m.imm32(PROG_IDX, 1); a.movw_ld(1, 0); a.extu_w(0, 0); ld(m, LPROG, 1); a.cmpeq(1, 0); m.bt_far(nr)
    a.deflabel(sel); call(m, 'mx_select')
    a.deflabel(nr)
    ld(m, SEL, 0); a.tst_rr(0, 0); m.bt_far(disp)
    # wheel: the OS changed the field -> apply, the slider has to catch the new value
    call(m, 'mx_getv')                                                                # r9 = value 0..100
    ld(m, LASTV, 0); a.cmpeq(9, 0); nw = m.newlabel('nw'); m.bt_far(nw)
    st(m, 9, LASTV); call(m, 'mx_apply'); sti(m, 0, CAUGHT)
    ld(m, LASTV, 9); ld(m, SS, 10); a.cmphi(9, 10); a.movt(0); st(m, 0, SIDE)
    a.deflabel(nw)
    # slider, catch-up
    fol = m.newlabel('fol'); cat = m.newlabel('cat')
    ld(m, CAUGHT, 0); a.tst_rr(0, 0); m.bf_far(fol)
    ld(m, LASTV, 9); ld(m, SS, 10); a.cmpeq(9, 10); m.bt_far(cat)
    a.cmphi(9, 10); a.movt(2); ld(m, SIDE, 0); a.cmpeq(2, 0); m.bf_far(cat)          # passed the value: caught
    a.movl_pool(disp, 1); a.jmp(1); a.nop(); m.pool_jump()
    a.deflabel(cat); sti(m, 1, CAUGHT)
    a.deflabel(fol)
    ld(m, LASTV, 9); ld(m, SS, 10); a.cmpeq(9, 10); m.bt_far(disp)
    st(m, 10, LASTV); a.mov_rm_rn(10, 9); call(m, 'mx_setf'); call(m, 'mx_apply')
    m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)                         # the OS redraws the value field
    a.deflabel(disp)
    call(m, 'mx_disp')
    a.movl_pool('__mx_end', 2); a.jmp(2); a.nop(); m.pool_jump()
    subs(m)
    a.deflabel('__mx_end')
pg.on_fire(fire)

def subs(m):
    a = m.a
    # ---- mx_slider: SS = slider step 0..100 (filtered, learned range, hysteresis), as Pitch. r0-r7 ----
    a.deflabel('mx_slider'); a.stsl_pr()
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
    a.mov_rm_rn(7, 0); a.shlr(0); a.add_rr(0, 4); pg._hcall(m, '__udiv')             # r0 = round((f-lo)*100/span)
    a.mov_imm(STEPS, 1); c3 = m.newlabel('c3'); a.cmpgt(1, 0); a.bf_lbl(c3); a.mov_rm_rn(1, 0); a.deflabel(c3)
    a.mov_rm_rn(0, 3)                                                                 # r3 = target
    ld(m, SS, 0); a.cmpeq(3, 0); sx = m.newlabel('sx'); m.bt_far(sx)
    a.mov_imm(STEPS, 1); a.cmphi(1, 0); setv = m.newlabel('set'); m.bt_far(setv)      # not set yet (0xFF)
    # hysteresis: leave the current step only when f is more than half a step + HYST from its centre
    a.mov_rm_rn(0, 4); a.mul_l(7, 4); a.sts_macl(4); a.mov_imm(STEPS, 5)
    a.movl_st_dec(3, 15); pg._hcall(m, '__udiv'); a.movl_ld_inc(15, 3)                # r0 = SS*span/100
    a.sub(0, 6); a.cmppz(6); ab = m.newlabel('ab'); a.bt_lbl(ab); a.neg(6, 6); a.deflabel(ab)   # r6 = |f - centre|
    a.mov_rm_rn(7, 4); m.imm32(2 * STEPS, 5); a.movl_st_dec(3, 15); a.movl_st_dec(6, 15)
    pg._hcall(m, '__udiv'); a.movl_ld_inc(15, 6); a.movl_ld_inc(15, 3); a.add_imm(HYST, 0)   # r0 = span/200 + HYST
    a.cmphi(0, 6); m.bf_far(sx)
    a.deflabel(setv); st(m, 3, SS)
    a.deflabel(sx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- mx_select: (PEND = pad + 1 -> SEL) reference values of all pads, which pads move, the value in the field ----
    a.deflabel('mx_select'); a.stsl_pr()
    ld(m, PEND, 0); a.tst_rr(0, 0); kp = m.newlabel('kp'); a.bt_lbl(kp); st(m, 0, SEL); sti(m, 0, PEND)
    a.deflabel(kp)
    rdb(m, PARAM, 0); st(m, 0, LPARAM); rdb(m, SCOPE, 0); st(m, 0, LSCOPE)
    m.imm32(PROG_IDX, 1); a.movw_ld(1, 0); a.extu_w(0, 0); st(m, 0, LPROG)
    poff(m, 13)                                                                       # r13 = parameter offset
    prog(m, 11)                                                                       # r11 = pad block 0
    rdb(m, SCOPE, 14)                                                                 # r14 = 1: whole program
    ld(m, SEL, 12); a.add_imm(-1, 12)                                                 # r12 = pad shown
    m.imm32(REF, 7); m.imm32(INCL, 6); a.mov_imm(0, 9)                                # r9 = pad
    lp, nx = m.newlabel('lp'), m.newlabel('snx'); a.deflabel(lp)
    a.mov_rm_rn(11, 1); a.add_rr(13, 1); a.movb_ld(1, 0); a.movb_st(0, 7)             # REF[p] = value
    a.mov_imm(0, 2)
    for l in range(4): a.mov_rm_rn(11, 1); a.add_imm(l * LAYER, 1); a.movb_ld(1, 0); a.or_rr(0, 2)
    a.extu_b(2, 2); a.tst_rr(2, 2); a.movt(2); a.mov_imm(1, 0); a.xor_rr(0, 2)        # r2 = has a sample
    a.and_rr(14, 2)                                                                   # ... and whole program
    a.cmpeq(12, 9); sp = m.newlabel('sp'); a.bf_lbl(sp); a.mov_imm(1, 2); a.deflabel(sp)   # the pad shown always moves
    a.movb_st(2, 6)
    m.imm32(PAD_SIZE, 0); a.add_rr(0, 11); a.add_imm(1, 7); a.add_imm(1, 6); a.add_imm(1, 9)
    a.mov_imm(NPADS, 0); a.cmpeq(0, 9); a.bf_lbl(lp)
    # value of the pad shown -> field; caught only if the slider is already there
    m.imm32(REF, 1); a.add_rr(12, 1); a.movb_ld(1, 9); a.extu_b(9, 9)
    m.imm32(100, 0); a.cmphi(0, 9); ok = m.newlabel('ok'); a.bf_lbl(ok); a.mov_rm_rn(0, 9); a.deflabel(ok)   # (0..100)
    st(m, 9, BASEV); st(m, 9, LASTV); call(m, 'mx_setf')
    ld(m, LASTV, 9); ld(m, SS, 10); a.cmpeq(9, 10); a.movt(0); st(m, 0, CAUGHT)
    a.cmphi(9, 10); a.movt(0); st(m, 0, SIDE)
    m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- mx_setf: field = r9 (+101 for PAN). r0, r1 ----
    a.deflabel('mx_setf')
    rdb(m, PARAM, 0); a.mov_imm(2, 1); a.cmpeq(1, 0); a.mov_rm_rn(9, 0); sf = m.newlabel('sf'); a.bf_lbl(sf)
    a.add_imm(PANBASE, 0)
    a.deflabel(sf); m.imm32(IDX, 1); a.movb_st(0, 1)
    a.rts(); a.nop()
    m.pool_jump()

    # ---- mx_getv: r9 = value 0..100 from the field; a wheel turn into the other parameter's entries is put back ----
    a.deflabel('mx_getv'); a.stsl_pr()
    rdb(m, IDX, 9); rdb(m, PARAM, 0); a.mov_imm(2, 1); a.cmpeq(1, 0); pn = m.newlabel('pn'); m.bf_far(pn)
    m.imm32(PANBASE, 0); a.sub(0, 9)                                                  # PAN: entries 101..201
    a.cmppz(9); fx = m.newlabel('fx'); gx = m.newlabel('gx'); a.bt_lbl(gx); a.mov_imm(0, 9); a.bra_lbl(fx); a.nop()
    a.deflabel(pn)
    m.imm32(100, 0); a.cmphi(0, 9); a.bf_lbl(gx); a.mov_rm_rn(0, 9)                   # VOL / LPF: entries 0..100
    a.deflabel(fx); call(m, 'mx_setf'); m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(gx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- mx_apply: every pad that moves = its reference value + (value - value of the pad shown), 0..100 ----
    a.deflabel('mx_apply'); a.stsl_pr()
    ld(m, LASTV, 12); ld(m, BASEV, 0); a.sub(0, 12)                                   # r12 = delta
    poff(m, 13); prog(m, 11)
    m.imm32(REF, 7); m.imm32(INCL, 6); a.mov_imm(NPADS, 9); m.imm32(100, 5)
    al, ak = m.newlabel('al'), m.newlabel('ak'); a.deflabel(al)
    a.movb_ld(6, 0); a.tst_rr(0, 0); a.bt_lbl(ak)
    a.movb_ld(7, 0); a.extu_b(0, 0); a.add_rr(12, 0)
    k1 = m.newlabel('k1'); a.cmppz(0); a.bt_lbl(k1); a.mov_imm(0, 0); a.deflabel(k1)
    k2 = m.newlabel('k2'); a.cmpgt(5, 0); a.bf_lbl(k2); a.mov_rm_rn(5, 0); a.deflabel(k2)
    a.mov_rm_rn(11, 1); a.add_rr(13, 1); a.movb_st(0, 1)
    a.deflabel(ak)
    m.imm32(PAD_SIZE, 0); a.add_rr(0, 11); a.add_imm(1, 7); a.add_imm(1, 6); a.dt(9); a.bf_lbl(al)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- mx_disp: row 0 the pad, row 1 catch hint; redraw only when the text changed ----
    a.deflabel('mx_disp'); a.stsl_pr()
    m.imm32(pg.addr['dlabels'][PADL], 12); a.mov_imm(0, 14)                           # r14 = changed
    def cput(s):
        for ch in s:
            n = m.newlabel('cp'); a.movb_ld(12, 1); a.mov_imm(ord(ch), 0); a.cmpeq(0, 1); a.bt_lbl(n)
            a.movb_st(0, 12); a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
    def cputr(r):
        n = m.newlabel('cr'); a.movb_ld(12, 1); a.cmpeq(r, 1); a.bt_lbl(n); a.mov_rm_rn(r, 0); a.movb_st(0, 12)
        a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
    nos, row1 = m.newlabel('nos'), m.newlabel('row1')
    ld(m, SEL, 9); a.tst_rr(9, 9); m.bt_far(nos)
    a.add_imm(-1, 9); a.mov_rm_rn(9, 4); a.mov_imm(12, 5); pg._hcall(m, '__udiv')     # r0 = bank, r1 = pad
    a.add_imm(ord('A'), 0); a.mov_rm_rn(0, 10); a.add_imm(1, 1); a.mov_rm_rn(1, 4); a.mov_imm(10, 5)
    pg._hcall(m, '__udiv'); a.add_imm(0x30, 0); a.mov_rm_rn(0, 11); a.add_imm(0x30, 1); a.mov_rm_rn(1, 13)
    cput("    "); cputr(10); cputr(11); cputr(13); a.bra_lbl(row1); a.nop()
    m.pool_jump()
    a.deflabel(nos); cput("HIT PAD")
    a.deflabel(row1)
    m.imm32(pg.addr['dlabels'][MOVEL], 12)
    blank, up = m.newlabel('blank'), m.newlabel('up'); dn = m.newlabel('dn')
    ld(m, SEL, 0); a.tst_rr(0, 0); m.bt_far(blank)
    ld(m, CAUGHT, 0); a.tst_rr(0, 0); m.bf_far(blank)
    ld(m, LASTV, 9); ld(m, SS, 10); a.cmphi(10, 9); m.bt_far(up)                      # slider below the value: up
    cput("MOVE v"); a.bra_lbl(dn); a.nop()
    a.deflabel(up); cput("MOVE ^"); a.bra_lbl(dn); a.nop()
    a.deflabel(blank); cput("      ")
    a.deflabel(dn)
    a.tst_rr(14, 14); px = m.newlabel('px'); m.bt_far(px); m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(px); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

def ev_pad(m, hret0, hret1):
    """a pad hit chooses the pad shown (fire does the work); the OS plays it as usual"""
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
    out = os.path.join(os.path.dirname(__file__), '..', '..', 'MIXER.WAV'); pg.build_wav(out)
    from testkit import PageTest
    t = PageTest(pg, out); h, e = t.h, t.e
    R32, W32 = h.ram_r32, h.ram_w32
    h.ram_w16(PROG_IDX, 1); PROG = PROG_BASE + PROG_STRIDE
    def pad(p): return PROG + PAD_OFF + p * PAD_SIZE
    def setpad(p, name, vol, lpf, pan, prog_base=None):
        b = (prog_base or PROG) + PAD_OFF + p * PAD_SIZE
        for i in range(PAD_SIZE): e.wr(b + i, 0, 1)
        for i, ch in enumerate(name.encode()): e.wr(b + LAYER + i, ch, 1)   # (sample in layer 2: any layer counts)
        e.wr(b + 0x8F, vol, 1); e.wr(b + 0x72, lpf, 1); e.wr(b + 0x90, pan, 1); e.wr(b + 0x71, 1, 1)
    for p in range(NPADS): setpad(p, "", 100, 100, 50)
    setpad(4, "SNARE", 80, 60, 30)
    setpad(5, "HAT", 100, 100, 70)
    setpad(6, "KICK", 50, 40, 50)
    setpad(20, "PERC", 20, 10, 95)
    e.wr(pad(9) + 0x8F, 77, 1)                                         # A10: no sample, level 77
    vals = lambda off, ps=(4, 5, 6, 20, 9): tuple(e.rd(pad(p) + off, 1) for p in ps)
    snap = lambda base=PROG: bytes(e.rd(base + i, 1) for i in range(PROG_STRIDE))
    def slider(k, fires=60):             # put the slider on step k (0..100)
        W32(SLIDER, SL_LO + round(k * (SL_HI - SL_LO) / STEPS)); t.fire(fires)
    lab = lambda: (t.label(PADL, 7), t.label(MOVEL, 6).rstrip())
    field = lambda: OPTS[e.rd(IDX, 1)].strip()
    W32(SLIDER, (SL_LO + SL_HI) // 2); t.open(); slider(50)
    assert (R32(SLO), R32(SHI)) == (SL_LO, SL_HI)
    assert lab() == ("HIT PAD", "") and e.rd(PARAM, 1) == 0 and e.rd(SCOPE, 1) == 0, lab()
    before = snap(); slider(90); e.wr(IDX, 33, 1); t.fire(3); assert snap() == before, "no pad: nothing changes"
    print("  ok defaults VOL PAD, HIT PAD: slider and wheel change nothing")

    # --- PAD, VOL: A05 level 80 ---
    slider(50); t.pad(0, 4); t.fire(2)
    assert field() == "80" and lab() == ("    A05", "MOVE ^"), (field(), lab())
    before = snap()
    for k in (60, 75): slider(k); assert snap() == before and field() == "80", k
    slider(80); assert snap() == before and lab()[1] == "", "caught at the pad's value"
    W32(EVQ + 0x70, 0); slider(85, fires=40); assert field() == "85" and R32(EVQ + 0x70) == 1
    W32(EVQ + 0x70, 0); t.fire(20); assert R32(EVQ + 0x70) == 0, "no redraw while nothing changes"
    slider(95); assert vals(0x8F) == (95, 100, 50, 20, 77), vals(0x8F)
    slider(100); assert vals(0x8F)[0] == 100
    print("  ok PAD VOL: A05 shows 80, MOVE ^, catches at 80, follows to 95 / 100; other pads untouched")
    import random; rnd = random.Random(7); v0 = SL_LO + round(100 * (SL_HI - SL_LO) / STEPS)
    for k in (40, 41):
        slider(k); vk = SL_LO + round(k * (SL_HI - SL_LO) / STEPS)
        for _ in range(200): W32(SLIDER, vk + rnd.randint(-4, 4)); t.fire(1)
        assert field() == str(k), (k, field())
    print("  ok slider noise +-4 counts does not move a step (101 steps)")
    e.wr(IDX, 30, 1); t.fire(2); assert vals(0x8F)[0] == 30 and lab()[1] == "MOVE v", (vals(0x8F), lab())   # slider at 41
    slider(35); assert vals(0x8F)[0] == 30, "wheel: must catch again"
    e.wr(IDX, 150, 1); t.fire(2); assert field() == "100" and vals(0x8F)[0] == 100, "wheel can't go into the PAN entries"
    print("  ok wheel sets 30 (slider must catch again); wheel past 100 stays at 100")

    # --- PAD, LPF and PAN ---
    e.wr(PARAM, 1, 1); t.fire(2); assert field() == "60", field()               # LPF of A05: new reference
    e.wr(IDX, 25, 1); t.fire(2); assert vals(0x72) == (25, 100, 40, 10, 100), vals(0x72)
    e.wr(PARAM, 2, 1); t.fire(2); assert field() == "L20", field()               # PAN 30 = L20
    e.wr(IDX, PANBASE + 50, 1); t.fire(2); assert field() == "C" and vals(0x90)[0] == 50
    e.wr(IDX, PANBASE + 100, 1); t.fire(2); assert field() == "R50" and vals(0x90)[0] == 100
    e.wr(IDX, PANBASE - 1, 1); t.fire(2); assert field() == "L50" and vals(0x90)[0] == 0, "wheel can't leave the PAN entries"
    print("  ok LPF (byte +0x72) 60 -> 25; PAN shows L20, C at 50, R50 at 100, wheel clamped to L50..R50")

    # --- PROG, VOL: every pad with a sample moves together, balance kept, clamps undo exactly ---
    for p, v in ((4, 80), (5, 100), (6, 50), (20, 20)): e.wr(pad(p) + 0x8F, v, 1)
    e.wr(PARAM, 0, 1); e.wr(SCOPE, 1, 1); t.fire(2)
    assert field() == "80" and lab()[0] == "    A05", (field(), lab())
    e.wr(IDX, 70, 1); t.fire(2); assert vals(0x8F) == (70, 90, 40, 10, 77), vals(0x8F)
    e.wr(IDX, 95, 1); t.fire(2); assert vals(0x8F) == (95, 100, 65, 35, 77), vals(0x8F)
    e.wr(IDX, 0, 1); t.fire(2); assert vals(0x8F) == (0, 20, 0, 0, 77), vals(0x8F)
    e.wr(IDX, 80, 1); t.fire(2); assert vals(0x8F) == (80, 100, 50, 20, 77), "back to 80: every pad exactly as before"
    print("  ok PROG VOL: -10 -> 70/90/40/10, up to 95 (A06 stops at 100), down to 0, back: all exact; empty pad untouched")
    # hitting another pad in PROG: just the pad shown (new reference = current values)
    t.pad(0, 6); t.fire(2); assert field() == "50" and lab()[0] == "    A07", (field(), lab())
    e.wr(IDX, 60, 1); t.fire(2); assert vals(0x8F) == (90, 100, 60, 30, 77), vals(0x8F)
    print("  ok PROG: hitting A07 shows its 50, +10 moves the whole program again")
    # PROG, PAN on bank B pad
    e.wr(PARAM, 2, 1); t.fire(2); h.ram_w8(BANKOBJ, 1); t.pad(1, 8); t.fire(2)
    assert lab()[0] == "    B09" and field() == "R45", (lab(), field())
    e.wr(IDX, PANBASE + 85, 1); t.fire(2); assert vals(0x90) == (0, 60, 40, 85, 50), vals(0x90)    # A05 was L50
    h.ram_w8(BANKOBJ, 0)
    print("  ok PROG PAN from bank B pad B09 (R45 -> R35): all pans -10")

    # --- program change: new reference in the other program ---
    PROG2 = PROG_BASE + 2 * PROG_STRIDE
    for p in range(NPADS): setpad(p, "", 100, 100, 50, prog_base=PROG2)
    setpad(20, "BASS", 100, 100, 70, prog_base=PROG2)
    s1 = snap(); h.ram_w16(PROG_IDX, 2); t.fire(2); assert field() == "R20", field()   # B09 of program 2
    e.wr(IDX, PANBASE + 60, 1); t.fire(2)
    assert e.rd(PROG2 + PAD_OFF + 20 * PAD_SIZE + 0x90, 1) == 60 and snap() == s1, "only program 2 changed"
    h.ram_w16(PROG_IDX, 1); t.fire(2)
    print("  ok program changed: new reference, only the current program is edited")

    # --- only levels / cutoffs / pans are ever written ---
    mask = {pad(p) + o for p in range(NPADS) for o in POFF}
    s0 = snap(); e.wr(SCOPE, 1, 1)
    for prm in (0, 1, 2):
        e.wr(PARAM, prm, 1); t.fire(2); slider(10); slider(90); e.wr(IDX, (PANBASE if prm == 2 else 0) + 33, 1); t.fire(2)
    assert all(a == b for i, (a, b) in enumerate(zip(s0, snap())) if PROG + i not in mask), "other bytes changed"
    assert all(e.rd(pad(p) + o, 1) <= 100 for p in range(NPADS) for o in POFF)
    assert t.pad(0, 7)[0] == 0, "pads still play (event left to the OS)"
    print("  ok nothing but level / cutoff / pan bytes written, all within 0..100; pads still play")
    print("OK: MIXER.WAV built and tested")
