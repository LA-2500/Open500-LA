#!/usr/bin/env python3
"""SONG - replaces the MPC's own Song page (MODE + the Song pad): build songs by hitting pads.

    python sdk/examples/song.py          -> builds SONG.WAV and tests it in the emulator

On the MPC500: copy SONG.WAV to the CF card, LOAD it (after HUB.WAV). MODE + the Song pad opens it (MODE -> OTHER ->
"Song" too). The cursor on the page name + the wheel chooses the page:
  Song select     "Sg:01-MYSONG": the song (1..20).
  Assign seq      "001:PRESS PAD" -> A1 -> "001:01-VERSE" -> A2 -> "002:02-CHORUS" ...: PADS A1 = sequence 1 .. A12 = 12,
                  B1 = 13 .. D12 = 48 go into the next step and that step is shown; the END follows (a new step plays
                  once). The wheel on the step number chooses where the next pad goes (it replaces that step); the
                  wheel on the sequence: 1..99, below 1 = the empty step ("PRESS PAD" = the song's END). Pads are
                  silent on this page. CANCEL -> "CLEAR ALL?" -> DO IT empties the song (start over), CANCEL keeps it.  Repeats         "003:Times:   2": how many times the step plays (HOLD, 0..99).
  Insert before   "003:05-VERSE": DO IT inserts a step before this one (the MPC's Insert: the current sequence, once).
  Delete step     "003:05-VERSE": DO IT deletes this step.
  Convert to seq  "To sq:10-(unused)": DO IT converts the song into that sequence (the MPC's Convert song2sq).
  ENTER DEFAULT   "MPC SONG MODE": DO IT opens the MPC's own Song page (Tempo, Song name, Copy / Delete song, Fix
                  tempo stay there).
Every page: the slider moves the page's value (catch-up: only after passing the value shown; no hint); the wheel the
field under the cursor; on numbers LEFT / RIGHT pick the digit (only the digits the number has).

OS addresses (MPC500 OS 1.31; "fw" = read in the firmware 2026-11-03, not yet confirmed on hardware):
  fw: Song page parameters = group 6: getter 0x884F2158, SETTER 0x884F1F88(r4 = id, r5 = &{?, value}) as the MPC's own
  fields use it: 1 song (1..20, -> 0x884BEA40), 3 step (1..256, -> 0x884B7EE2 + 0x884B84EE), 4 sequence of the step
  (1..99; 0x884BEA8A first, -> song + 0x14 + step, 0x884BE724 recalculation, 0x884B7E5C), 7 repeats (-1 HOLD .. 99,
  -> song + 0x114 + step). Sequencer object 0x8847C10C: +0xAB8 current song, +0xAB2 current step (byte). Song: +0x11
  in use, +0x14 sequence per step (0 = END: the song ends at the first 0), +0x114 repeats per step. Current song
  number u16 0x881D3218 + 0x974. Sequence object 0x884B05CC(0x881D3218, n) (0 = empty), name at +4. Song name
  0x884BE9EC(song). END written directly (the setter keeps 1..99) + 0x884BE724, as the setter does.
  fw: Song page object 0x88495E68 (descriptor 0x881C45C4: handler 0x884D6B40): DO IT on its menu: Insert before =
  0x884D6972(), Delete step = 0x884D6A2A(song, step - 1), Convert song2sq = 0x884BEBC6(0x88482E5C, song, sequence).
  Checked at run time (+0x1C = VT_PAGE, +0x30 = 0x884D6B40) before it is redirected or opened. MODE page 0x8849C9C4.
  Pad events 0x38 (bank byte 0 of 0x8849C98C); Q-Link slider 0x8848C3E4; key codes LEFT 2 / RIGHT 3 (Grid Seq, HW).
"""
import sys, os, struct
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import PageAddon, ld, st, sti, SET_FOCUS
from mpcmod import EVQ, OS_PUSH_PAGE

SEQR = 0x8847C10C                       # sequencer object
CURSONG, CURSTEP = SEQR + 0xAB8, SEQR + 0xAB2
SONGNUM = 0x881D3218 + 0x974
SEQTAB = 0x881D3218
SETTER, RECALC, SEQOBJ, SONGNAME = 0x884F1F88, 0x884BE724, 0x884B05CC, 0x884BE9EC
OS_INSERT, OS_DELSTEP, OS_CONVERT, SONGREG = 0x884D6972, 0x884D6A2A, 0x884BEBC6, 0x88482E5C
STEPS_OFF, REP_OFF, NSTEPS = 0x14, 0x114, 256
BANKOBJ = 0x8849C98C
SLIDER = 0x8848C3E4
SL_LO, SL_HI, HYST = 64, 960, 3
MODEPG, OSSONG, OSSONG_H, VT_PAGE = 0x8849C9C4, 0x88495E68, 0x884D6B40, 0x88507BA8
K_LEFT, K_RIGHT = 0x02, 0x03
ID_SONG, ID_STEP, ID_SEQ, ID_REP = 1, 3, 4, 7
NSONGS, NSEQ = 20, 99

(P_SONG, P_ASSIGN, P_REP, P_INS, P_DEL, P_CONV, P_ENTER) = range(7)
NAMES = ["Song select", "Assign seq", "Repeats", "Insert before", "Delete step", "Convert to seq", "ENTER DEFAULT"]
STEPPAGES = [P_ASSIGN, P_REP, P_INS, P_DEL]

pg = PageAddon("song1", open_press=None)
pg.other_menu("Song")
pg.far_hend = True
pg.far_fire = True
pg.msg_fires = 0x300
PARAM = pg.choice("", 0, 0, [n.ljust(16) for n in NAMES], default=P_ASSIGN)
R1 = pg.dlabel(0, 1, 16)                                                  # row 1 text (drawn first)
NAMEL = pg.dlabel(6, 1, 10)                                               # "-MYSONG" / "-VERSE" (after the numbers)
ENDL = pg.dlabel(4, 1, 9)                                                 # "PRESS PAD" (Assign) / "END" (the empty step)
NAMEC = pg.dlabel(8, 1, 8)                                                # "-VERSE" after "To sq:10"
SONGF = pg.value("", 3, 1, fmt="00", lo=1, hi=NSONGS, default=1)          # Sg:01 (zero padded, as the MPC)
STEPF = pg.value("", 0, 1, fmt="000", lo=1, hi=NSTEPS, default=1)         # 001
SEQF = pg.value("", 4, 1, fmt="00", lo=0, hi=NSEQ, default=1)             # :05 (0 = END)
REPF = pg.choice("", 10, 1, ["HOLD"] + ["%4d" % i for i in range(100)], default=2)   # Times (index = repeats + 1)
CONVF = pg.value("", 6, 1, fmt="00", lo=1, hi=NSEQ, default=1)            # To sq:10
pg.message()
CURSOR = [PARAM, SONGF, STEPF, SEQF, CONVF, REPF]
DIGW = {SONGF: 2, STEPF: 3, SEQF: 2, CONVF: 2}                            # number fields: width
def fid(slot): return [it[1] for it in pg.items if it[0] in ('choice', 'value') and it[-1] == slot][0]
def W(slot): return pg.addr['values'][fid(slot)]
(LSONGF, LSTEPF, LSEQF, LREPF, LCONVF, DIG, LFOC, PREVPG, LASTF, KT, LKT, NSTEP, CUR, CAUGHT, SIDE, SS, SF, SINIT,
 SLO, SHI, CONF, PEND, LSTATE, PPOS, LPAGE) = [pg.alloc_slot() for _ in range(25)]
PBUF = pg.alloc_buf(8)                                                    # {?, value} for the OS setter
pg.defaults[SLO] = SL_LO; pg.defaults[SHI] = SL_HI; pg.defaults[SS] = 0xFF; pg.defaults[LKT] = 0xFF
pg.defaults[SIDE] = 0xFF; pg.defaults[LCONVF] = 1; pg.defaults[LPAGE] = 0xFF
K_NONE, K_SONG, K_SEQ, K_REP, K_STEP, K_CONV = range(6)

def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()
def jmpl(m, lbl): m.a.movl_pool(lbl, 2); m.a.jmp(2); m.a.nop()
def osc(m, fn): m.imm32(fn, 3); m.a.jsr(3); m.a.nop()
def rdb(m, addr, r): m.imm32(addr, r); m.a.movb_ld(r, r); m.a.extu_b(r, r)
def wrb(m, rv, addr): m.imm32(addr, 1); m.a.movb_st(rv, 1)
def redraw(m): m.imm32(EVQ + 0x70, 1); m.a.mov_imm(1, 0); m.a.movl_st(0, 1)
def in_set(m, r, vals, lbl):
    for v in vals: m.a.mov_imm(v, 0); m.a.cmpeq(0, r); m.bt_far(lbl)
def setter(m, ident, rv):
    """the MPC's own group-6 setter: id, value in rv (rv != 1, 2, 4, 5)"""
    m.imm32(PBUF, 1); m.a.movl_st_disp(rv, 4, 1); m.a.mov_imm(ident, 4); m.imm32(PBUF, 5); osc(m, SETTER)
def songptr(m, r): m.imm32(CURSONG, r); m.a.movl_ld(r, r)
def step_addr(m, r, off):
    """r = song + off + current step. Uses r0, r1"""
    a = m.a
    songptr(m, r); rdb(m, CURSTEP, 0); a.add_rr(0, r); m.imm32(off, 1); a.add_rr(1, r)

def fire(m):
    a = m.a
    call(m, 'sg_bg')
    call(m, 'sg_slider')
    # page (re)entered or changed: the next pad goes into the step shown
    rdb(m, PARAM, 0); ld(m, LPAGE, 1); a.cmpeq(1, 0); np_ = m.newlabel('np'); m.bt_far(np_)
    st(m, 0, LPAGE); rdb(m, CURSTEP, 0); st(m, 0, PPOS)
    a.deflabel(np_)
    call(m, 'sg_wheel')                                                   # first: a field the wheel moved
    call(m, 'sg_sync')                                                    # the song / step -> the fields
    call(m, 'sg_vis')
    call(m, 'sg_kt')
    ld(m, KT, 0); ld(m, LKT, 1); a.cmpeq(1, 0); nk = m.newlabel('nk'); m.bt_far(nk)
    st(m, 0, LKT); call(m, 'sg_ref')
    a.deflabel(nk)
    disp = m.newlabel('disp')
    ld(m, NSTEP, 0); a.tst_rr(0, 0); m.bt_far(disp)
    fol, cat = m.newlabel('fol'), m.newlabel('cat')
    ld(m, CAUGHT, 0); a.tst_rr(0, 0); m.bf_far(fol)
    ld(m, CUR, 9); ld(m, SS, 10); a.cmpeq(9, 10); m.bt_far(cat)
    a.cmphi(9, 10); a.movt(2); ld(m, SIDE, 0); a.cmpeq(2, 0); m.bf_far(cat)
    jmpl(m, disp); m.pool_jump()
    a.deflabel(cat); sti(m, 1, CAUGHT)
    a.deflabel(fol)
    ld(m, CUR, 9); ld(m, SS, 10); a.cmpeq(9, 10); m.bt_far(disp)
    st(m, 10, CUR); a.mov_rm_rn(10, 9); call(m, 'sg_apply')
    a.deflabel(disp)
    call(m, 'sg_disp')
    jmpl(m, '__sg_end'); m.pool_jump()
    subs(m)
    a.deflabel('__sg_end')
pg.on_fire(fire)

def inactive(m):
    call(m, 'sg_bg'); sti(m, 0, CONF); sti(m, 0xFF, LPAGE)                             # back on the page: PPOS again
    sti(m, 0xFF, LKT); sti(m, 0, SINIT)                                                # left the page: the slider may be elsewhere on return, so it has to catch the value again (user 2026-11-08: jumps on re-entry)
pg.on_inactive(inactive)

def subs(m):
    a = m.a
    # ---- sg_bg (every fire, any page): MODE + the Song pad -> this page ----
    a.deflabel('sg_bg'); a.stsl_pr()
    same = m.newlabel('same')
    m.imm32(EVQ + 0x68, 1); a.movl_ld(1, 9)
    ld(m, PREVPG, 0); a.cmpeq(9, 0); m.bt_far(same)
    a.mov_rm_rn(0, 8); st(m, 9, PREVPG)
    m.imm32(MODEPG, 1); a.cmpeq(1, 8); m.bf_far(same)
    m.imm32(OSSONG, 1); a.cmpeq(1, 9); m.bf_far(same)
    a.movl_ld_disp(9, 0x1C, 0); m.imm32(VT_PAGE, 1); a.cmpeq(1, 0); m.bf_far(same)
    a.movl_ld_disp(9, 0x30, 0); m.imm32(OSSONG_H, 1); a.cmpeq(1, 0); m.bf_far(same)
    m.imm32(EVQ, 4); a.mov_imm(0, 5); a.mov_imm(2, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()
    m.imm32(EVQ, 4); m.imm32(pg.addr['page'], 5); a.mov_imm(0, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()
    m.imm32(pg.addr['page'], 0); st(m, 0, PREVPG)
    a.deflabel(same)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- sg_sync: song number, step, its sequence and repeats -> the fields (and the copies the wheel uses) ----
    a.deflabel('sg_sync'); a.stsl_pr()
    m.imm32(SONGNUM, 1); a.movw_ld(1, 0); a.extu_w(0, 0); st(m, 0, SONGF); st(m, 0, LSONGF)
    rdb(m, CURSTEP, 0); a.add_imm(1, 0); st(m, 0, STEPF); st(m, 0, LSTEPF)
    songptr(m, 0); a.tst_rr(0, 0); ns = m.newlabel('ns'); m.bt_far(ns)
    step_addr(m, 6, STEPS_OFF); a.movb_ld(6, 0); a.extu_b(0, 0); st(m, 0, SEQF); st(m, 0, LSEQF)
    step_addr(m, 6, REP_OFF); a.movb_ld(6, 0); a.add_imm(1, 0); a.extu_b(0, 0)        # -1 (HOLD) .. 99 -> 0 .. 100
    m.imm32(100, 1); r1 = m.newlabel('r1'); a.cmphi(1, 0); a.bf_lbl(r1); a.mov_rm_rn(1, 0); a.deflabel(r1)
    wrb(m, 0, REPF); st(m, 0, LREPF)
    a.deflabel(ns)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- sg_set: r8 = what (K_*), r9 = value -> the MPC (its own setter; END directly + recalculation) ----
    a.deflabel('sg_set'); a.stsl_pr()
    sx = m.newlabel('sx')
    lb = {k: m.newlabel('st') for k in (K_SONG, K_SEQ, K_REP, K_STEP, K_CONV)}
    for k, l in lb.items(): a.mov_imm(k, 0); a.cmpeq(0, 8); m.bt_far(l)
    jmpl(m, sx); m.pool_jump()
    a.deflabel(lb[K_SONG]); setter(m, ID_SONG, 9); jmpl(m, sx)
    a.deflabel(lb[K_STEP]); setter(m, ID_STEP, 9); rdb(m, CURSTEP, 0); st(m, 0, PPOS); jmpl(m, sx)   # pads go here
    a.deflabel(lb[K_REP]); a.add_imm(-1, 9); setter(m, ID_REP, 9); jmpl(m, sx)
    a.deflabel(lb[K_CONV]); st(m, 9, CONVF); st(m, 9, LCONVF); jmpl(m, sx)
    m.pool_jump()
    a.deflabel(lb[K_SEQ])
    ne = m.newlabel('ne'); a.tst_rr(9, 9); m.bf_far(ne)
    step_addr(m, 6, STEPS_OFF); a.mov_imm(0, 0); a.movb_st(0, 6); osc(m, RECALC); jmpl(m, sx)   # END
    a.deflabel(ne); setter(m, ID_SEQ, 9)
    a.deflabel(sx)
    call(m, 'sg_sync'); redraw(m)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- sg_pad: r9 = sequence (1..48) -> step PPOS (shown afterwards); the END follows; PPOS = the next step ----
    a.deflabel('sg_pad'); a.stsl_pr()
    songptr(m, 0); a.tst_rr(0, 0); px = m.newlabel('px'); m.bt_far(px)
    a.mov_rm_rn(9, 13); ld(m, PPOS, 9); a.add_imm(1, 9); setter(m, ID_STEP, 9); a.mov_rm_rn(13, 9)   # step = PPOS
    step_addr(m, 6, STEPS_OFF); a.movb_ld(6, 10); a.extu_b(10, 10)                    # r10 = 0: it was the END
    setter(m, ID_SEQ, 9)
    a.tst_rr(10, 10); nend = m.newlabel('nend'); m.bf_far(nend)
    rdb(m, CURSTEP, 11); m.imm32(NSTEPS - 1, 0); a.cmpeq(0, 11); m.bt_far(nend)
    step_addr(m, 6, STEPS_OFF); a.mov_imm(0, 0); a.add_imm(1, 6); a.movb_st(0, 6)     # step + 1 = END
    step_addr(m, 6, REP_OFF); a.mov_imm(1, 0); a.movb_st(0, 6)                       # this step plays once
    osc(m, RECALC)
    a.deflabel(nend)
    rdb(m, CURSTEP, 0); a.add_imm(1, 0); m.imm32(NSTEPS - 1, 1); c1 = m.newlabel('c1'); a.cmpgt(1, 0); a.bf_lbl(c1)
    a.mov_rm_rn(1, 0); a.deflabel(c1); st(m, 0, PPOS)                                 # the next pad: the next step
    sti(m, 0xFF, LKT); call(m, 'sg_sync'); redraw(m)
    a.deflabel(px)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- sg_clear: every step = END, repeats 1, step 1 ----
    a.deflabel('sg_clear'); a.stsl_pr()
    songptr(m, 6); a.tst_rr(6, 6); cx = m.newlabel('cx'); m.bt_far(cx)
    m.imm32(STEPS_OFF, 1); a.add_rr(1, 6); m.imm32(NSTEPS, 7); a.mov_imm(0, 0); a.mov_imm(1, 2)
    cl = m.newlabel('cl'); a.deflabel(cl)
    a.movb_st(0, 6); a.mov_rm_rn(6, 1); m.imm32(REP_OFF - STEPS_OFF, 3); a.add_rr(3, 1); a.movb_st(2, 1)
    a.add_imm(1, 6); a.dt(7); a.bf_lbl(cl)
    osc(m, RECALC)
    a.mov_imm(1, 9); setter(m, ID_STEP, 9); sti(m, 0, PPOS)
    sti(m, 0xFF, LKT); call(m, 'sg_sync'); redraw(m)
    a.deflabel(cx)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- sg_ndig (leaf): r4 = number, r5 = width -> r0 = its last digit (digits - 1). r0, r1, r3 ----
    a.deflabel('sg_ndig')
    nl, nx = m.newlabel('ndl'), m.newlabel('ndx')
    a.mov_imm(0, 0); a.mov_imm(10, 1); a.add_imm(-1, 5)
    a.deflabel(nl); a.cmpeq(5, 0); a.bt_lbl(nx); a.cmphs(1, 4); a.bf_lbl(nx)
    a.add_imm(1, 0); a.mov_rm_rn(1, 3); a.shll2(1); a.add_rr(3, 1); a.shll(1)
    a.bra_lbl(nl); a.nop()
    a.deflabel(nx); a.add_imm(1, 5); a.rts(); a.nop()
    m.pool_jump()

    # ---- sg_wheel: a field changed by the wheel (numbers: by the digit under the cursor) -> the MPC ----
    a.deflabel('sg_wheel'); a.stsl_pr()
    wx, wch = m.newlabel('wx'), m.newlabel('wch')
    m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 9); ld(m, LFOC, 0); a.cmpeq(0, 9); nf = m.newlabel('nf'); m.bt_far(nf)
    st(m, 9, LFOC); sti(m, 0, DIG)                                                    # another field: the ones
    a.deflabel(nf)
    for F, LF, kind, lo, hi in ((SONGF, LSONGF, K_SONG, 1, NSONGS), (STEPF, LSTEPF, K_STEP, 1, NSTEPS),
                                (SEQF, LSEQF, K_SEQ, 0, NSEQ), (CONVF, LCONVF, K_CONV, 1, NSEQ)):
        nx_, asis, go = m.newlabel('wn'), m.newlabel('wa'), m.newlabel('wg')
        ld(m, F, 4); ld(m, LF, 11); a.cmpeq(4, 11); m.bt_far(nx_)
        a.mov_rm_rn(4, 6); a.sub(11, 6)                                               # r6 = d
        ld(m, DIG, 3); a.mov_imm(1, 7); pw = m.newlabel('pw'); pz = m.newlabel('pz'); a.deflabel(pw)
        a.tst_rr(3, 3); a.bt_lbl(pz); a.mov_rm_rn(7, 0); a.shll2(7); a.add_rr(0, 7); a.shll(7); a.add_imm(-1, 3)
        a.bra_lbl(pw); a.nop(); a.deflabel(pz)                                        # r7 = 10^DIG
        a.mov_imm(1, 0); a.cmpeq(0, 7); m.bt_far(asis)
        a.mov_rm_rn(6, 4); a.cmppz(4); ab = m.newlabel('ab'); a.bt_lbl(ab); a.neg(4, 4); a.deflabel(ab)
        a.mov_rm_rn(7, 5); pg._hcall(m, '__udiv'); a.tst_rr(1, 1); m.bt_far(asis)
        a.mul_l(7, 6); a.sts_macl(9); a.add_rr(11, 9); jmpl(m, go)
        a.deflabel(asis); ld(m, F, 9)
        a.deflabel(go)
        a.mov_imm(lo, 0); k1 = m.newlabel('k1'); a.cmpgt(9, 0); a.bf_lbl(k1); a.mov_rm_rn(0, 9); a.deflabel(k1)
        m.imm32(hi, 0); k2 = m.newlabel('k2'); a.cmpgt(0, 9); a.bf_lbl(k2); a.mov_rm_rn(0, 9); a.deflabel(k2)
        a.mov_imm(kind, 8); call(m, 'sg_set'); jmpl(m, wch)
        a.deflabel(nx_); m.pool_jump()
    nr = m.newlabel('nr')
    rdb(m, REPF, 9); ld(m, LREPF, 0); a.cmpeq(9, 0); m.bt_far(nr)
    a.mov_imm(K_REP, 8); call(m, 'sg_set'); jmpl(m, wch)
    a.deflabel(nr); jmpl(m, wx)
    m.pool_jump()
    a.deflabel(wch)
    sti(m, 0xFF, LKT); sti(m, 0, CAUGHT); sti(m, 0xFF, SIDE)
    a.deflabel(wx)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- sg_kt: KT = what the slider edits on this page ----
    a.deflabel('sg_kt'); a.stsl_pr()
    rdb(m, PARAM, 6); a.mov_imm(K_NONE, 9)
    for p, k in ((P_SONG, K_SONG), (P_ASSIGN, K_SEQ), (P_REP, K_REP), (P_INS, K_STEP), (P_DEL, K_STEP), (P_CONV, K_CONV)):
        n = m.newlabel('kt'); a.mov_imm(p, 0); a.cmpeq(0, 6); a.bf_lbl(n); a.mov_imm(k, 9); a.deflabel(n)
    songptr(m, 0); a.tst_rr(0, 0); kk = m.newlabel('kk'); a.bf_lbl(kk); a.mov_imm(K_NONE, 9); a.deflabel(kk)
    ld(m, CONF, 0); a.tst_rr(0, 0); k2 = m.newlabel('k2'); a.bt_lbl(k2); a.mov_imm(K_NONE, 9); a.deflabel(k2)
    st(m, 9, KT)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- sg_ref: steps and current value of what the slider edits (KT); caught only if the slider is there ----
    a.deflabel('sg_ref'); a.stsl_pr()
    rset = m.newlabel('rset')
    ld(m, KT, 8); a.mov_imm(0, 3); a.mov_imm(0, 9)
    for k, slot, n_, off in ((K_SONG, SONGF, NSONGS - 1, 1), (K_SEQ, SEQF, NSEQ - 1, 1), (K_STEP, STEPF, NSTEPS - 1, 1),
                             (K_CONV, CONVF, NSEQ - 1, 1)):
        n = m.newlabel('rf'); a.mov_imm(k, 0); a.cmpeq(0, 8); m.bf_far(n)
        ld(m, slot, 9); a.add_imm(-off, 9); m.imm32(n_, 3); jmpl(m, rset); a.deflabel(n); m.pool_jump()
    n = m.newlabel('rr'); a.mov_imm(K_REP, 0); a.cmpeq(0, 8); m.bf_far(n); rdb(m, REPF, 9); a.mov_imm(100, 3); a.deflabel(n)
    a.deflabel(rset)
    a.cmppz(9); z = m.newlabel('z'); a.bt_lbl(z); a.mov_imm(0, 9); a.deflabel(z)       # (END: below the first)
    ld(m, NSTEP, 0); a.cmpeq(3, 0); sn = m.newlabel('sn'); m.bt_far(sn)
    st(m, 3, NSTEP); m.imm32(0xFF, 0); st(m, 0, SS); call(m, 'sg_slider')
    a.deflabel(sn)
    st(m, 9, CUR); ld(m, SS, 10); a.cmpeq(9, 10); a.movt(0); st(m, 0, CAUGHT)
    a.cmphi(9, 10); a.movt(0); st(m, 0, SIDE)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- sg_apply: r9 = slider step -> the value of KT ----
    a.deflabel('sg_apply'); a.stsl_pr()
    ld(m, KT, 8)
    n = m.newlabel('ar'); a.mov_imm(K_REP, 0); a.cmpeq(0, 8); a.bt_lbl(n); a.add_imm(1, 9); a.deflabel(n)   # 1-based
    call(m, 'sg_set')
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- sg_slider: SS = 0..NSTEP (filtered, learned range, hysteresis, exact ends) ----
    a.deflabel('sg_slider'); a.stsl_pr()
    m.imm32(SLIDER, 1); a.movl_ld(1, 2); m.imm32(0x3FF, 1); a.and_rr(1, 2); a.shll2(2); a.shll2(2)
    ld(m, SINIT, 0); a.tst_rr(0, 0); g0 = m.newlabel('g0'); a.bf_lbl(g0); st(m, 2, SF); sti(m, 1, SINIT); a.deflabel(g0)
    ld(m, SF, 0); a.sub(0, 2); a.shar(2); a.shar(2); a.shar(2); a.add_rr(2, 0); st(m, 0, SF)
    a.mov_rm_rn(0, 6); a.shar(6); a.shar(6); a.shar(6); a.shar(6)
    ld(m, SLO, 0); c1 = m.newlabel('c1'); a.cmpgt(6, 0); a.bf_lbl(c1); st(m, 6, SLO); a.deflabel(c1)
    ld(m, SHI, 0); c2 = m.newlabel('c2'); a.cmpgt(0, 6); a.bf_lbl(c2); st(m, 6, SHI); a.deflabel(c2)
    ld(m, SLO, 7); a.sub(7, 6); ld(m, SHI, 5); a.sub(7, 5); a.mov_rm_rn(5, 7)
    a.mov_rm_rn(6, 4); ld(m, NSTEP, 0); a.mul_l(0, 4); a.sts_macl(4)
    a.mov_rm_rn(7, 0); a.shlr(0); a.add_rr(0, 4); pg._hcall(m, '__udiv')
    ld(m, NSTEP, 1); c3 = m.newlabel('c3'); a.cmpgt(1, 0); a.bf_lbl(c3); a.mov_rm_rn(1, 0); a.deflabel(c3)
    a.mov_rm_rn(0, 3)
    sx2, setv = m.newlabel('sx'), m.newlabel('set')
    ld(m, SS, 0); a.cmpeq(3, 0); m.bt_far(sx2)
    ld(m, NSTEP, 1); a.cmphi(1, 0); m.bt_far(setv)
    a.tst_rr(3, 3); m.bt_far(setv); ld(m, NSTEP, 1); a.cmpeq(1, 3); m.bt_far(setv)
    a.mov_rm_rn(0, 4); a.mul_l(7, 4); a.sts_macl(4); ld(m, NSTEP, 5)
    a.movl_st_dec(3, 15); pg._hcall(m, '__udiv'); a.movl_ld_inc(15, 3)
    a.sub(0, 6); a.cmppz(6); ab = m.newlabel('ab'); a.bt_lbl(ab); a.neg(6, 6); a.deflabel(ab)
    a.mov_rm_rn(7, 4); ld(m, NSTEP, 5); a.shll(5); a.movl_st_dec(3, 15); a.movl_st_dec(6, 15)
    pg._hcall(m, '__udiv'); a.movl_ld_inc(15, 6); a.movl_ld_inc(15, 3); a.add_imm(HYST, 0)
    a.cmphi(0, 6); m.bf_far(sx2)
    a.deflabel(setv); st(m, 3, SS)
    a.deflabel(sx2); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- sg_vis: the fields of the page; the cursor skips hidden fields ----
    a.deflabel('sg_vis'); a.stsl_pr()
    WL = [W(s) for s in CURSOR]
    DL = lambda i: pg.addr['dlabels'][i] - 0x28
    a.mov_imm(0, 14)
    rdb(m, PARAM, 4)
    a.mov_imm(0, 5); songptr(m, 0); a.tst_rr(0, 0); ok0 = m.newlabel('ok0'); a.bt_lbl(ok0)
    ld(m, CONF, 0); a.tst_rr(0, 0); a.movt(5)                                         # r5 = a song, no question
    a.deflabel(ok0)
    ld(m, SEQF, 6)                                                                    # r6 = 0: END
    def vis(w, params, need=True, endc=None):
        n1, n2, yes = m.newlabel('v1'), m.newlabel('v2'), m.newlabel('vy')
        m.imm32(w + 0x17, 1); a.movb_ld(1, 0); a.extu_b(0, 0); a.mov_rm_rn(0, 3); a.and_imm(0x7F)
        if need: a.tst_rr(5, 5); m.bt_far(n1)
        if endc is not None:                                                          # only when (not) the END
            a.tst_rr(6, 6); m.bt_far(n1) if endc is False else m.bf_far(n1)
        for p in params: a.mov_imm(p, 2); a.cmpeq(2, 4); m.bt_far(yes)
        jmpl(m, n1)
        a.deflabel(yes); a.or_imm(0x80)
        a.deflabel(n1); a.cmpeq(0, 3); a.bt_lbl(n2); a.movb_st(0, 1); a.mov_imm(1, 14); a.deflabel(n2)
        m.pool_jump()
    vis(W(SONGF), [P_SONG]); vis(W(STEPF), STEPPAGES); vis(W(SEQF), [P_ASSIGN, P_INS, P_DEL], endc=False)
    vis(W(REPF), [P_REP]); vis(W(CONVF), [P_CONV])
    vis(DL(ENDL), [P_ASSIGN, P_INS, P_DEL], endc=True)
    vis(DL(NAMEL), [P_SONG, P_ASSIGN, P_INS, P_DEL]); vis(DL(NAMEC), [P_CONV])
    vdone, vpick, vback, vgo = m.newlabel('vd'), m.newlabel('vp'), m.newlabel('vb'), m.newlabel('vg')
    def index(rs, rd_):
        a.mov_imm(-1, rd_)
        for i, w in enumerate(WL):
            n = m.newlabel('ix'); m.imm32(w, 1); a.cmpeq(1, rs); a.bf_lbl(n); a.mov_imm(i, rd_); a.deflabel(n)
    m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 9)
    index(9, 11); a.cmppz(11); m.bf_far(vdone)
    m.pool_jump()
    a.mov_rm_rn(9, 1); a.add_imm(0x17, 1); a.movb_ld(1, 0); a.tst_imm(0x80); m.bf_far(vdone)
    ld(m, LASTF, 10); index(10, 12)
    m.pool_jump()
    a.mov_imm(0, 13)
    a.cmppz(12); m.bf_far(vpick)
    a.cmpgt(12, 11); m.bf_far(vback)
    for i in reversed(range(len(WL))):
        n = m.newlabel('fw'); a.mov_imm(i, 0); a.cmpgt(12, 0); a.bf_lbl(n)
        m.imm32(WL[i] + 0x17, 1); a.movb_ld(1, 0); a.tst_imm(0x80); a.bt_lbl(n); m.imm32(WL[i], 13); a.deflabel(n)
    jmpl(m, vpick); m.pool_jump()
    a.deflabel(vback)
    for i in range(len(WL)):
        n = m.newlabel('bw'); a.mov_imm(i, 0); a.cmpgt(0, 12); a.bf_lbl(n)
        m.imm32(WL[i] + 0x17, 1); a.movb_ld(1, 0); a.tst_imm(0x80); a.bt_lbl(n); m.imm32(WL[i], 13); a.deflabel(n)
    m.pool_jump()
    a.deflabel(vpick)
    a.tst_rr(13, 13); m.bf_far(vgo)
    a.mov_rm_rn(10, 13); a.cmppz(12); m.bt_far(vgo); m.imm32(WL[0], 13)
    a.deflabel(vgo)
    m.imm32(pg.addr['page'], 4); a.mov_rm_rn(13, 5); m.imm32(SET_FOCUS, 2); a.jsr(2); a.nop(); a.mov_imm(1, 14)
    a.deflabel(vdone)
    m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 0); st(m, 0, LASTF)
    a.tst_rr(14, 14); vr = m.newlabel('vr'); m.bt_far(vr); redraw(m)
    a.deflabel(vr); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- sg_disp: row 1 texts, names, digit cursor; redraw only when something changed ----
    a.deflabel('sg_disp'); a.stsl_pr()
    a.mov_imm(0, 14)
    def cput(s):
        for ch in s:
            n = m.newlabel('cp'); a.movb_ld(12, 1); a.mov_imm(ord(ch), 0); a.cmpeq(0, 1); a.bt_lbl(n)
            a.movb_st(0, 12); a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
    def cputr(r):
        n = m.newlabel('cr'); a.movb_ld(12, 1); a.cmpeq(r, 1); a.bt_lbl(n); a.mov_rm_rn(r, 0); a.movb_st(0, 12)
        a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
    def name_from(rp, n, dash=True):
        """('-' +) the name at rp, n characters in all (blanks when rp = 0). r5, r7, r13"""
        nm, nl_, usp, put, ed = (m.newlabel(s) for s in ('nm', 'nl', 'us', 'pu', 'ed'))
        a.tst_rr(rp, rp); m.bf_far(nm); cput(" " * n); jmpl(m, ed)
        a.deflabel(nm)
        if dash: cput("-")
        a.mov_imm(0, 5); a.mov_imm(n - 1 if dash else n, 7)
        a.deflabel(nl_)
        a.movb_ldinc(rp, 13); a.tst_rr(5, 5); a.bf_lbl(usp); a.tst_rr(13, 13); a.bf_lbl(put); a.mov_imm(1, 5)
        a.deflabel(usp); a.mov_imm(32, 13)
        a.deflabel(put); cputr(13); a.dt(7); a.bf_lbl(nl_)
        a.deflabel(ed)
    rdb(m, PARAM, 8)
    # row 1 text
    m.imm32(pg.addr['dlabels'][R1], 12)
    rd_, rq, rns = m.newlabel('rd'), m.newlabel('rq'), m.newlabel('rns')
    ld(m, CONF, 0); a.tst_rr(0, 0); m.bf_far(rq)
    songptr(m, 0); a.tst_rr(0, 0); m.bt_far(rns)
    rs, rstep, rrep, rcv, ren = (m.newlabel(s) for s in ('rs', 'rst', 'rre', 'rcv', 'ren'))
    a.mov_imm(P_SONG, 0); a.cmpeq(0, 8); m.bt_far(rs)
    a.mov_imm(P_REP, 0); a.cmpeq(0, 8); m.bt_far(rrep)
    a.mov_imm(P_CONV, 0); a.cmpeq(0, 8); m.bt_far(rcv)
    a.mov_imm(P_ENTER, 0); a.cmpeq(0, 8); m.bt_far(ren)
    cput("   :            "); jmpl(m, rd_)                                                # step pages: "001:"
    m.pool_jump()
    a.deflabel(rs); cput("Sg:  -          "); jmpl(m, rd_)
    a.deflabel(rrep); cput("   :Times:      "); jmpl(m, rd_)
    a.deflabel(rcv); cput("To sq:          "); jmpl(m, rd_)
    a.deflabel(ren); cput("MPC SONG MODE   "); jmpl(m, rd_)
    m.pool_jump()
    a.deflabel(rq); cput("CLEAR ALL?      "); jmpl(m, rd_)
    a.deflabel(rns); cput("NO SONG         ")
    a.deflabel(rd_)
    m.imm32(pg.addr['dlabels'][ENDL], 12); ea, ed2 = m.newlabel('ea'), m.newlabel('ed2')
    a.mov_imm(P_ASSIGN, 0); a.cmpeq(0, 8); m.bt_far(ea); cput("END      "); jmpl(m, ed2)
    a.deflabel(ea); cput("PRESS PAD")
    a.deflabel(ed2)
    # name: song (Song select, x6), sequence (step pages x6, Convert x8)
    nsg, nsq, nnone, ndone = m.newlabel('nsg'), m.newlabel('nsq'), m.newlabel('nno'), m.newlabel('ndo')
    m.imm32(pg.addr['dlabels'][NAMEC], 12)                                         # Convert: its own label
    ld(m, CONVF, 5); m.imm32(SEQTAB, 4); osc(m, SEQOBJ); a.mov_rm_rn(0, 6)
    c6 = m.newlabel('c6'); a.tst_rr(6, 6); a.bt_lbl(c6); a.add_imm(4, 6); a.deflabel(c6)
    m.imm32(pg.addr['dlabels'][NAMEC], 12); name_from(6, 8)
    m.pool_jump()
    m.imm32(pg.addr['dlabels'][NAMEL], 12)
    songptr(m, 0); a.tst_rr(0, 0); m.bt_far(nnone)
    a.mov_imm(P_SONG, 0); a.cmpeq(0, 8); m.bt_far(nsg)
    ld(m, SEQF, 5); a.tst_rr(5, 5); m.bt_far(nnone)                                  # END: no name
    a.deflabel(nsq); m.imm32(SEQTAB, 4); osc(m, SEQOBJ); a.mov_rm_rn(0, 6); a.tst_rr(6, 6); m.bt_far(nnone)
    a.add_imm(4, 6); m.imm32(pg.addr['dlabels'][NAMEL], 12); name_from(6, 10); jmpl(m, ndone)
    a.deflabel(nsg); songptr(m, 4); osc(m, SONGNAME); a.mov_rm_rn(0, 6)
    m.imm32(pg.addr['dlabels'][NAMEL], 12); name_from(6, 10, dash=False); jmpl(m, ndone)
    m.pool_jump()
    a.deflabel(nnone); m.imm32(pg.addr['dlabels'][NAMEL], 12); cput(" " * 10)
    a.deflabel(ndone)
    m.pool_jump()
    # digit fields: the cursor column (only on digits the number has)
    for F, wdt in DIGW.items():
        dg, c0 = m.newlabel('dg'), m.newlabel('c0')
        m.imm32(0x7FFFFFFF, 4); a.mov_imm(wdt, 5); call(m, 'sg_ndig')                # zero padded: every digit
        ld(m, DIG, 3); a.cmphi(0, 3); m.bf_far(c0); a.mov_rm_rn(0, 3)
        m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 1); m.imm32(W(F), 5); a.cmpeq(5, 1); m.bf_far(c0)
        st(m, 3, DIG)
        a.deflabel(c0); a.mov_imm(wdt - 1, 0); a.sub(3, 0)
        m.imm32(W(F) + 0x24, 1); a.movl_ld(1, 2)
        a.mov_rm_rn(0, 3); a.mov_rm_rn(2, 1); a.add_imm(1, 1); a.movb_ld(1, 0); a.extu_b(0, 0); a.cmpeq(3, 0)
        a.bt_lbl(dg); a.mov_rm_rn(3, 0); a.movb_st_disp(1, 2); a.mov_imm(1, 14); a.deflabel(dg)
        m.pool_jump()
    a.tst_rr(14, 14); px = m.newlabel('px'); m.bt_far(px); redraw(m)
    a.deflabel(px); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

def doit(m):
    a = m.a
    dx = m.newlabel('dx')
    # CLEAR ALL? -> the song is emptied
    nq = m.newlabel('nq'); ld(m, CONF, 0); a.tst_rr(0, 0); m.bt_far(nq)
    sti(m, 0, CONF); call(m, 'sg_clear'); pg.emit_message(m, "SONG CLEARED"); jmpl(m, dx)
    a.deflabel(nq)
    rdb(m, PARAM, 9)
    ne = m.newlabel('ne'); a.mov_imm(P_ENTER, 0); a.cmpeq(0, 9); m.bf_far(ne)
    bad = m.newlabel('bad')
    m.imm32(OSSONG, 8)
    a.movl_ld_disp(8, 0x1C, 0); m.imm32(VT_PAGE, 1); a.cmpeq(1, 0); m.bf_far(bad)
    a.movl_ld_disp(8, 0x30, 0); m.imm32(OSSONG_H, 1); a.cmpeq(1, 0); m.bf_far(bad)
    m.imm32(EVQ, 4); a.mov_rm_rn(8, 5); a.mov_imm(0, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop(); jmpl(m, dx)
    a.deflabel(bad); pg.emit_message(m, "NOT AVAILABLE"); jmpl(m, dx)
    m.pool_jump()
    a.deflabel(ne)
    songptr(m, 0); a.tst_rr(0, 0); hs = m.newlabel('hs'); m.bf_far(hs); pg.emit_message(m, "NO SONG"); jmpl(m, dx)
    a.deflabel(hs)
    ni, nd, nc = m.newlabel('ni'), m.newlabel('nd'), m.newlabel('nc')
    a.mov_imm(P_INS, 0); a.cmpeq(0, 9); m.bf_far(ni)
    osc(m, OS_INSERT); pg.emit_message(m, "STEP INSERTED"); jmpl(m, dx)
    a.deflabel(ni); a.mov_imm(P_DEL, 0); a.cmpeq(0, 9); m.bf_far(nd)
    m.imm32(SONGNUM, 1); a.movw_ld(1, 4); a.extu_w(4, 4); rdb(m, CURSTEP, 5); osc(m, OS_DELSTEP)
    pg.emit_message(m, "STEP DELETED"); jmpl(m, dx)
    m.pool_jump()
    a.deflabel(nd); a.mov_imm(P_CONV, 0); a.cmpeq(0, 9); m.bf_far(dx)
    m.imm32(SONGREG, 4); m.imm32(SONGNUM, 1); a.movw_ld(1, 5); a.extu_w(5, 5); ld(m, CONVF, 6); osc(m, OS_CONVERT)
    a.tst_rr(0, 0); m.bf_far(nc); pg.emit_message(m, "SONG CONVERTED"); jmpl(m, dx)
    a.deflabel(nc); pg.emit_message(m, "CAN'T CONVERT")
    a.deflabel(dx)
    sti(m, 0xFF, LKT); call(m, 'sg_sync')
pg.on_doit(doit)

def ev_pad(m, hret0, hret1):
    """Assign seq: a pad = its sequence (A1 = 1 .. D12 = 48) on the step, silent; elsewhere the OS plays it"""
    a = m.a
    a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 9); a.extu_w(9, 9); a.mov_imm(12, 0); a.cmphs(0, 9); m.bt_far(hret0)
    rdb(m, PARAM, 0); a.mov_imm(P_ASSIGN, 1); a.cmpeq(1, 0); m.bf_far(hret0)
    ld(m, CONF, 0); a.tst_rr(0, 0); m.bf_far(hret1)                                  # (CLEAR ALL? asked: nothing)
    a.movl_ld_disp(8, 4, 0); a.tst_rr(0, 0); m.bt_far(hret1)                         # release: consumed, silent
    m.imm32(BANKOBJ, 1); a.movb_ld(1, 0); a.and_imm(3); a.mov_imm(12, 2); a.mul_l(2, 0); a.sts_macl(0); a.add_rr(0, 9)
    a.add_imm(1, 9); call(m, 'sg_pad'); jmpl(m, hret1)
    m.pool_jump()
pg.on_event(0x38, ev_pad)

def cancel(m, hret1):
    """Assign seq: CANCEL asks CLEAR ALL? (CANCEL again keeps the song); elsewhere it leaves as usual"""
    a = m.a
    lv = m.newlabel('lv')
    ld(m, CONF, 0); a.tst_rr(0, 0); q = m.newlabel('q'); m.bt_far(q)
    sti(m, 0, CONF); sti(m, 0xFF, LKT); call(m, 'sg_disp'); redraw(m); jmpl(m, hret1)
    a.deflabel(q)
    rdb(m, PARAM, 0); a.mov_imm(P_ASSIGN, 1); a.cmpeq(1, 0); m.bf_far(lv)
    songptr(m, 0); a.tst_rr(0, 0); m.bt_far(lv)
    sti(m, 1, CONF); call(m, 'sg_vis'); call(m, 'sg_disp'); redraw(m); jmpl(m, hret1)
    m.pool_jump()
    a.deflabel(lv)
pg.cancel_fn = cancel

def ev_key(m, hret0, hret1):
    """LEFT / RIGHT on a number: the digit (only the digits the number has); past them, the OS (next field)"""
    a = m.a
    kx, kl, kr, kd = m.newlabel('kx'), m.newlabel('kl'), m.newlabel('kr'), m.newlabel('kd')
    m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 12)                              # (r9 = event type: keep)
    a.mov_imm(-1, 11)
    for F, wdt in DIGW.items():
        n = m.newlabel('kf'); m.imm32(W(F), 1); a.cmpeq(1, 12); a.bf_lbl(n)
        m.imm32(0x7FFFFFFF, 4); a.mov_imm(wdt, 5); call(m, 'sg_ndig'); a.mov_rm_rn(0, 11); a.deflabel(n)   # zero padded
    a.cmppz(11); m.bf_far(kx)
    a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 10); a.extu_w(10, 10)
    a.mov_imm(K_LEFT, 0); a.cmpeq(0, 10); m.bt_far(kl)
    a.mov_imm(K_RIGHT, 0); a.cmpeq(0, 10); m.bt_far(kr)
    jmpl(m, kx)
    m.pool_jump()
    a.deflabel(kl); ld(m, DIG, 0); a.cmpeq(11, 0); m.bt_far(kx); a.add_imm(1, 0); jmpl(m, kd)
    a.deflabel(kr); ld(m, DIG, 0); a.tst_rr(0, 0); m.bt_far(kx); a.add_imm(-1, 0)
    a.deflabel(kd); st(m, 0, DIG); call(m, 'sg_disp'); redraw(m)
    jmpl(m, hret1)
    m.pool_jump()
    a.deflabel(kx)
pg.on_event(2, ev_key)


# =====================================================================================================================
def selftest(out):
    from validate import Harness
    from shasm import SHAsm
    from mpcmod import HOOKS
    from pages import ENABLE_DOIT, ENABLE_CANCEL
    from testkit import PageTest, FIRMWARE, EV_KEY
    VT_LABEL, VT_VALUE, VT_COMPOSITE = 0x88507C44, 0x88507C8C, 0x88507D1C
    LOG = 0x88E00000
    SONGS, SSTRIDE, SEQB, NAMEB = 0x881D89F8, 0x214, 0x88310000, 0x88318000   # the MPC's song table (Delete step)

    class OSTest(PageTest):
        """PageTest with the OS image: the REAL song setter / Insert / Delete step run; the sequencer parts around
        them that need a booted MPC are small stand-ins (songs and sequences in test memory)"""
        def __init__(self, pg, wav):
            self.pg = pg
            self.h = h = Harness(FIRMWARE); h.fill_scratch(); self.e = e = h.emu
            fw = open(FIRMWARE, 'rb').read(); OSB = 0x8849495C
            keep = [(HOOKS[h.hook][1], 4), (ENABLE_DOIT, 10), (ENABLE_CANCEL, 10)]
            saved = [(ad, [e.rd(ad + i, 2) for i in range(0, n, 2)]) for ad, n in keep]
            buf, off = e._region(OSB); buf[off:off + len(fw)] = fw
            for ad, ws in saved:
                for i, w in enumerate(ws): e.wr(ad + 2 * i, w, 2)
            for ad in (0x884C0076, 0x884C67BA, 0x884BEA8A, 0x884B7E5C, 0x884B84EE): self.stub(ad)
            self.stub(SET_FOCUS, lambda s: (s.movl_st_disp(5, 0x24, 4), s.rts(), s.nop()))
            self.stub(OS_PUSH_PAGE, lambda s: (s.movl_pool(LOG, 1), s.movl_ld(1, 0), s.add_imm(1, 0), s.movl_st(0, 1),
                                               s.shll2(0), s.shll2(0), s.add_rr(0, 1), s.movl_st(5, 1),
                                               s.movl_st_disp(6, 4, 1), s.rts(), s.nop()))
            self.stub(RECALC, lambda s: (s.movl_pool(LOG + 0x10, 1), s.movl_ld(1, 0), s.add_imm(1, 0), s.movl_st(0, 1), s.rts(), s.nop()))
            self.stub(0x884B7EE2, lambda s: (s.movl_pool(CURSTEP, 1), s.rts(), s.movb_st(5, 1)))          # current step
            self.stub(0x884BEA40, lambda s: (s.movl_pool(SONGNUM, 1), s.movw_st(5, 1), s.movl_pool(SSTRIDE, 0), s.mul_l(5, 0),
                                             s.sts_macl(0), s.movl_pool(SONGS, 1), s.add_rr(1, 0),
                                             s.movl_pool(CURSONG, 1), s.rts(), s.movl_st(0, 1)))            # song select
            def seqobj(s):      # r5 = n: 1..5 -> SEQB + n * 0x40, else 0
                s.mov_imm(0, 0); s.mov_imm(1, 1); s.cmphs(1, 5); s.bf_lbl('z'); s.mov_imm(5, 1); s.cmphs(5, 1); s.bf_lbl('z')
                s.mov_rm_rn(5, 0); s.shll2(0); s.shll2(0); s.shll2(0); s.movl_pool(SEQB, 1); s.add_rr(1, 0)
                s.deflabel('z'); s.rts(); s.nop()
            self.stub(SEQOBJ, seqobj)
            self.stub(SONGNAME, lambda s: (s.movl_pool(SONGNUM, 1), s.movw_ld(1, 0), s.extu_w(0, 0), s.shll2(0), s.shll2(0),
                                           s.movl_pool(NAMEB, 1), s.rts(), s.add_rr(1, 0)))                 # name: test table
            self.stub(OS_CONVERT, lambda s: (s.movl_pool(LOG + 0x20, 1), s.movl_st(5, 1), s.movl_st_disp(6, 4, 1),
                                             s.rts(), s.mov_imm(0, 0)))
            h.load_hub(); h.load_wav(wav); h.fire(10)

    t = OSTest(pg, out); h, e = t.h, t.e
    R32, W32 = h.ram_r32, h.ram_w32
    def pushes(): return [(R32(LOG + 16 * (k + 1)), R32(LOG + 16 * (k + 1) + 4)) for k in range(R32(LOG))]
    for k in range(1, NSONGS + 1):
        b = SONGS + k * SSTRIDE
        for i in range(SSTRIDE): e.wr(b + i, 0, 1)
        e.wr(b + 0x11, 1, 1)
        for i, c in enumerate(b"SONG%02d" % k): e.wr(NAMEB + 16 * k + i, c, 1)
    for n, nm in enumerate([b"VERSE", b"CHORUS", b"BRIDGE", b"INTRO", b"OUTRO"], 1):
        for i, c in enumerate(nm): e.wr(SEQB + n * 0x40 + 4 + i, c, 1)
    W32(CURSONG, SONGS + SSTRIDE); h.ram_w16(SONGNUM, 1); e.wr(CURSTEP, 0, 1)
    song = lambda: R32(CURSONG)
    def steps(n=8, b=None): b = b or song(); return [e.rd(b + STEPS_OFF + i, 1) for i in range(n)]
    def reps(n=8, b=None): b = b or song(); return [e.rd(b + REP_OFF + i, 1) for i in range(n)]
    cur = lambda: e.rd(CURSTEP, 1)
    WID = {s: pg.values[fid(s)] for s in CURSOR}
    focus = lambda: R32(pg.page + 0x24)
    def at(s): W32(pg.page + 0x24, WID[s]); t.fire(3)
    key = lambda k: t.event(EV_KEY, k)
    def cstr(p, k): return bytes(e.rd(p + i, 1) for i in range(k)).split(b'\0')[0].decode('latin-1')
    VALW = {pg.values[it[1]]: (it[-1], len(it[5])) for it in pg.items if it[0] == 'value'}
    def screen():
        rows = [[' '] * 16 for _ in range(2)]
        w = R32(pg.page + 0xC)
        while w:
            if e.rd(w + 0x17, 1) & 0x80:
                r = e.rd(w + 0x10, 2); x, y, wd = r >> 11, (r >> 9) & 3, (r >> 4) & 31
                vt = R32(w + 0x1C)
                if vt == VT_LABEL: txt = cstr(R32(w + 0x24), wd)
                elif vt == VT_COMPOSITE:
                    mo = R32(w + 0x24); idx = e.rd(R32(mo + 0xC), 1); desc = R32(mo + 0x20); p = R32(desc + 4)
                    for _ in range(idx): p += len(cstr(p, 64)) + 1
                    txt = cstr(p, wd)
                elif vt == VT_VALUE and w in VALW: sl, wd_ = VALW[w]; txt = ("%0" + str(wd_) + "d") % R32(sl)
                else: txt = ''
                for i, ch in enumerate(txt[:wd]): rows[y][x + i] = ch
            w = R32(w + 8)
        return [''.join(r) for r in rows]
    def setp(p): e.wr(PARAM, p, 1); t.fire(4)
    def slider(k, fires=60):
        lo, hi = t.slot(SLO), t.slot(SHI); W32(SLIDER, lo + round(k * (hi - lo) / max(1, t.slot(NSTEP)))); t.fire(fires)
    shots = {}
    def shot(tag): shots[tag] = screen()

    # ===== Assign seq: pads build the song; the step entered stays on the screen =====
    W32(SLIDER, SL_LO); t.open(); t.fire(10)
    assert e.rd(PARAM, 1) == P_ASSIGN and screen() == ["Assign seq      ", "001:PRESS PAD   "], screen()
    shot("Assign seq (empty song)")
    assert t.pad(0, 0) == (1, 1), "pad consumed: silent"
    t.fire(2); assert steps(3) == [1, 0, 0] and reps(2) == [1, 0] and cur() == 0, (steps(3), reps(2), cur())
    assert screen()[1] == "001:01-VERSE    ", screen(); shot("Assign seq (A1)")
    t.pad(0, 1); t.fire(2); assert cur() == 1 and screen()[1] == "002:02-CHORUS   ", screen(); shot("Assign seq (A2)")
    t.pad(0, 2); t.pad(1, 0); t.fire(2)                                               # A3, B1 (= 13)
    assert steps(6) == [1, 2, 3, 13, 0, 0] and reps(5) == [1, 1, 1, 1, 0] and cur() == 3, (steps(6), reps(5), cur())
    assert screen()[1] == "004:13          ", screen()                                # (13 has no sequence in the test)
    t.pad(3, 11); assert steps(6) == [1, 2, 3, 13, 48, 0] and cur() == 4, ("D12 = 48", steps(6))
    print("  ok pads: A1 A2 A3 B1 D12 -> steps 001..005 = 1, 2, 3, 13, 48, each shown as entered; END follows; silent")
    # the step number: the next pad goes there (it replaces it), then on to the next step
    at(STEPF); W32(STEPF, 1); t.fire(3); assert cur() == 0 and screen()[1] == "001:01-VERSE    ", screen()
    t.pad(0, 4); t.fire(2); assert steps(6) == [5, 2, 3, 13, 48, 0] and cur() == 0 and screen()[1] == "001:05-OUTRO    ", (steps(6), screen())
    t.pad(0, 2); t.fire(2); assert steps(6) == [5, 3, 3, 13, 48, 0] and cur() == 1 and screen()[1] == "002:03-BRIDGE   ", (steps(6), screen())
    print("  ok step 001 chosen with the wheel: A5 replaces it (shown), A3 goes to 002; the END stays after step 5")
    # sequence field: wheel; below 1 = the empty step ("PRESS PAD")
    at(SEQF); W32(SEQF, 2); t.fire(3); assert steps(3)[1] == 2 and screen()[1] == "002:02-CHORUS   ", screen()
    W32(SEQF, 1); t.fire(3); W32(SEQF, 0); t.fire(3); assert steps(3)[1] == 0 and screen()[1] == "002:PRESS PAD   ", screen()
    assert not (e.rd(WID[SEQF] + 0x17, 1) & 0x80), "empty: the number is hidden, the cursor goes elsewhere"
    at(STEPF); W32(STEPF, 3); t.fire(3); W32(STEPF, 2); t.fire(3)
    t.pad(0, 1); t.fire(2); assert steps(3)[:2] == [5, 2] and screen()[1] == "002:02-CHORUS   ", "pad on the empty step"
    print("  ok sequence field: wheel 2, 1, 0 = PRESS PAD; a pad fills it again")
    # digits: the step number (zero padded: every digit)
    at(STEPF); W32(STEPF, 3); t.fire(3)
    assert key(K_LEFT) == 1 and key(K_LEFT) == 1 and key(K_LEFT) == 0, "003: tens, hundreds, then the OS"
    t.fire(1); W32(STEPF, 4); t.fire(3); assert cur() == 102, ("hundreds digit: 003 -> 103", cur())
    key(K_RIGHT); key(K_RIGHT); t.fire(1); W32(STEPF, 102); t.fire(3); assert cur() == 101
    # slider: the step's sequence
    at(PARAM); e.wr(CURSTEP, 2, 1); t.fire(3); assert t.slot(KT) == K_SEQ
    slider(t.slot(CUR)); slider(3); assert steps(4)[2] == 4, steps(4)
    slider(0); assert steps(4)[2] == 1, steps(4)
    # page left, slider moved on another page, page entered again: the value stays until the slider catches it (user 2026-11-08)
    t.close(); W32(SLIDER, t.slot(SHI)); t.fire(3); t.open(); t.fire(60); assert steps(4)[2] == 1, ("re-entry keeps the sequence", steps(4))
    slider(0); assert steps(4)[2] == 1, steps(4)
    print("  ok slider on Assign seq: the step's sequence (4, then 1); re-entry keeps it (catch-up again)")
    # CANCEL -> CLEAR ALL? ; CANCEL keeps ; DO IT clears
    t.cancel(); assert screen()[1] == "CLEAR ALL?      " and t.slot(CONF) == 1, screen(); shot("CANCEL")
    t.pad(0, 6); assert steps(3) == [5, 2, 1], "no pad while asking"
    t.cancel(); assert t.slot(CONF) == 0 and steps(3) == [5, 2, 1] and screen()[1] != "CLEAR ALL?      "
    t.cancel(); t.doit(); assert steps(8) == [0] * 8 and reps(3) == [1, 1, 1] and cur() == 0 and t.slot(PPOS) == 0, (steps(8), cur())
    assert t.msg() == "SONG CLEARED"; t.fire(pg.msg_fires + 10)
    assert screen()[1] == "001:PRESS PAD   ", screen()
    t.pad(0, 0); t.pad(0, 1); assert steps(3) == [1, 2, 0] and cur() == 1
    print("  ok CANCEL -> CLEAR ALL? (pads ignored; CANCEL keeps); DO IT -> every step END, step 001; pads again")

    # ===== Repeats =====
    setp(P_REP); e.wr(CURSTEP, 0, 1); t.fire(3)
    assert screen() == ["Repeats         ", "001:Times:   1  "], screen(); shot("Repeats")
    at(REPF); e.wr(REPF, 3, 1); t.fire(3); assert reps(1) == [2]
    e.wr(REPF, 0, 1); t.fire(3); assert reps(1) == [0xFF] and screen()[1] == "001:Times:HOLD  ", screen()
    at(PARAM); slider(t.slot(CUR)); slider(5); assert reps(1) == [4], reps(1)
    print("  ok Repeats: wheel 2, HOLD (-1), slider 4")

    # ===== Insert before / Delete step (the MPC's own) =====
    setp(P_INS); e.wr(CURSTEP, 1, 1); t.fire(3); assert screen() == ["Insert before   ", "002:02-CHORUS   "], screen()
    shot("Insert before")
    h.ram_w16(SEQTAB + 0x96E, 3)                                                  # the current sequence: 3
    t.doit(); assert steps(4) == [1, 3, 2, 0] and t.msg() == "STEP INSERTED", (steps(4), t.msg())
    t.fire(pg.msg_fires + 10)
    setp(P_DEL); e.wr(CURSTEP, 1, 1); t.fire(3); assert screen()[1] == "002:03-BRIDGE   ", screen()
    t.doit(); assert steps(4)[:3] == [1, 2, 0] and t.msg() == "STEP DELETED", (steps(4), t.msg())
    t.fire(pg.msg_fires + 10)
    print("  ok Insert before (sequence 3 before step 2), Delete step (it again)")

    # ===== Song select =====
    setp(P_SONG); assert screen() == ["Song select     ", "Sg:01-SONG01    "], screen(); shot("Song select")
    at(SONGF); W32(SONGF, 2); t.fire(3); assert e.rd(SONGNUM, 2) == 2 and song() == SONGS + 2 * SSTRIDE and screen()[1] == "Sg:02-SONG02    "
    at(PARAM); slider(t.slot(CUR)); slider(4); assert e.rd(SONGNUM, 2) == 5, e.rd(SONGNUM, 2)
    slider(0); assert e.rd(SONGNUM, 2) == 1
    print("  ok Song select: wheel 2, slider 5, 1 (the MPC's song select)")

    # ===== Convert to seq =====
    setp(P_CONV); assert screen() == ["Convert to seq  ", "To sq:01-VERSE  "], screen(); shot("Convert")
    at(CONVF); W32(CONVF, 6); t.fire(3); assert screen()[1] == "To sq:06        ", screen()
    t.doit(); assert (R32(LOG + 0x20), R32(LOG + 0x24)) == (1, 6) and t.msg() == "SONG CONVERTED", (R32(LOG + 0x20), t.msg())
    t.fire(pg.msg_fires + 10)
    print("  ok Convert to seq: song 1 -> sequence 6 (the MPC's Convert song2sq)")

    # ===== ENTER DEFAULT, MODE + Song pad =====
    setp(P_ENTER); assert screen() == ["ENTER DEFAULT   ", "MPC SONG MODE   "], screen(); shot("ENTER DEFAULT")
    W32(LOG, 0); t.doit(); assert pushes() == [] and t.msg() == "NOT AVAILABLE"
    W32(OSSONG + 0x1C, VT_PAGE); W32(OSSONG + 0x30, OSSONG_H)
    W32(LOG, 0); t.doit(); assert pushes() == [(OSSONG, 0)], pushes()
    h.ram_w32(EVQ + 0x68, OSSONG); t.fire(3); assert pushes() == [(OSSONG, 0)], "opened from here: not redirected"
    h.ram_w32(EVQ + 0x68, MODEPG); t.fire(2); W32(LOG, 0); h.ram_w32(EVQ + 0x68, OSSONG); t.fire(1)
    assert pushes() == [(0, 2), (pg.page, 0)], pushes()
    print("  ok ENTER DEFAULT opens the MPC Song page (object checked); MODE + Song pad -> this page")
    h.ram_w32(EVQ + 0x68, pg.page); t.fire(3)
    setp(P_SONG); assert t.pad(0, 0)[0] == 0, "other pages: pads play as usual"
    print("\nScreens (16x2):")
    for k, (r0, r1) in shots.items(): print("  %-24s |%s|\n  %-24s |%s|" % (k, r0, "", r1))
    print("OK: SONG.WAV built and tested")


if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', '..', 'SONG.WAV'); pg.build_wav(out); selftest(out)