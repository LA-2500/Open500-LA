#!/usr/bin/env python3
"""SEQ EDIT -> SEQEDIT.WAV: replaces the MPC's Seq Edit page (MODE + pad 10) with quick bar tools for the current sequence.

    python sdk/examples/seqedit.py          -> builds SEQEDIT.WAV and tests it in the emulator

On the MPC500: copy SEQEDIT.WAV to the CF card, LOAD it (after HUB.WAV). MODE + pad 10 opens it (MODE -> OTHER ->
"Seq Edit" too). The cursor on the page name + the wheel chooses the page:
  Sequence length  "Sequence length" / "Now= 16 New:  32" (user layout 2026-11-03). DO IT:
             longer -> "DUPLICATE?  YES" (wheel YES / NO) -> DO IT: YES = bars 1..n repeated until the new length
             (16 -> 32: 1-16 to 17-32; 8 -> 12: 1-4 to 9-12; 16 -> 64: 1-16 three times), all tracks; NO = empty bars
             (time signature of the last bar). Shorter -> "DEL 009-012 ?" -> DO IT deletes those bars and their notes.
             CANCEL on a question: nothing changes.
  COPY       "COPY:001-016" / "AFTER:016   x01"     DO IT: bars FIRST..LAST (all tracks) inserted after bar AFTER
             (0 = at the start), x COPIES times; the bars after it move later (like the MPC's Copy bars).
  ENTER DEFAULT "MPC SEQ EDIT"                         DO IT: the MPC's own Seq Edit page.
Numbers: wheel on the field under the cursor, LEFT / RIGHT pick the digit; the Q-Link slider moves the field under
the cursor (catch-up: only after passing the value shown). 999 bars at most.

Sequence memory (official Seq Tools / Grid Seq RE, HW): sequence = *(u32*)0x8847CBC8, a block in the arena
  0x881DB59C..+0x1A0000 (blocks [size], 0 = end); +0x1C u16 bars; +0x30 bar table u32 (tick & 0xFFFFFF, high byte =
  time signature of the bar starting there; entry n = end); events 16 B from +0x1C10 sorted by tick, END tick
  0x7FFFFFFF; play pointer 0x8847CBD0. Tracks at +0xFD0 (Track add-on) -> the bar table holds entries 0..999.
  Moves of the arena with interrupts masked (Seq Tools).
MODE + pad 10: a page opened straight from the MODE page (0x8849C9C4) whose display line 1 (LCD shadow 0x88483E9C)
  starts with a Seq Edit function name (fw strings 0x88505C0C.. "Copy events" .. "Fix tempo"; user's MPC 2026-11-03:
  "Copy events" / "From S:01  T:A1-D") -> back + this page; that page is remembered for ENTER DEFAULT (as Pad FX).
"""
import sys, os, struct
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import PageAddon, ld, st, sti, SET_FOCUS
from mpcmod import EVQ, OS_PUSH_PAGE, LCD_SHADOW

CURSEQ, PLAYPTR = 0x8847CBC8, 0x8847CBD0
EV_OFF, BARS_OFF, BART_OFF = 0x1C10, 0x1C, 0x30
ARENA_LO, ARENA_HI = 0x881DB59C, 0x881DB59C + 0x1A0000
END, MAXBARS, TMASK = 0x7FFFFFFF, 999, 0xFFFFFF
MODEPG = 0x8849C9C4
WATCH = 40
SLIDER = 0x8848C3E4
SL_LO, SL_HI, HYST = 64, 960, 3
K_LEFT, K_RIGHT = 0x02, 0x03
SEQEDIT_NAMES = ["Copy events", "Move events", "Transpose", "Shift timing", "Edit velocity", "Edit duration",
                 "Timing correct", "Track move", "Copy bars", "Insert bars", "Delete bars", "Fix tempo"]

(P_LEN, P_COPY, P_ENTER) = range(3)
P_SET = P_LEN
NAMES = ["Sequence length ", "COPY            ", "ENTER DEFAULT   "]
Q_DEL, Q_DUP = 1, 2                                       # CONF: the question asked

pg = PageAddon("seqedt1", open_press=None)
pg.other_menu("Seq Edit")
pg.far_hend = True
pg.far_fire = True
pg.msg_fires = 0x300
pg.lean_pools = True                                     # 2026-11-04: fewer repeated literals -> one 4 KB page less
PARAM = pg.choice("", 0, 0, NAMES, default=P_LEN)
R0 = pg.dlabel(4, 0, 8)                                   # COPY: ":   -   "
R1 = pg.dlabel(0, 1, 16)                                  # row 1 text (drawn first, the fields over it)
FIRSTF = pg.value("", 5, 0, fmt="000", lo=0, hi=MAXBARS, default=1)
LASTF = pg.value("", 9, 0, fmt="000", lo=0, hi=MAXBARS, default=1)
AFTF = pg.value("", 6, 1, fmt="000", lo=0, hi=MAXBARS, default=0)
DUPF = pg.choice("", 12, 1, ["YES", " NO"], default=0)
NEWF = pg.value("", 13, 1, fmt="  0", lo=1, hi=MAXBARS, default=1)
COPF = pg.value("", 13, 1, fmt="00", lo=1, hi=99, default=1)
pg.message()
CURSOR = [PARAM, FIRSTF, LASTF, AFTF, DUPF, NEWF, COPF]
NUMF = [COPF, NEWF, FIRSTF, LASTF, AFTF]                  # KT = index + 1
DIGW = {COPF: 2, NEWF: 3, FIRSTF: 3, LASTF: 3, AFTF: 3}
def fid(slot): return [it[1] for it in pg.items if it[0] in ('choice', 'value') and it[-1] == slot][0]
def W(slot): return pg.addr['values'][fid(slot)]
LF = {F: pg.alloc_slot() for F in NUMF}                   # last values seen (wheel detection)
(DIG, LFOC, PREVPG, CAND, CWAIT, SEQPG, LASTF_, KT, LKT, NSTEP, CUR, CAUGHT, SIDE, SS, SF, SINIT, SLO, SHI, CONF,
 LSEQ, LBARS, OLDR) = [pg.alloc_slot() for _ in range(22)]
# engine variables
(vF, vLa, vA, vC, vM, vS, vE, vLc, vP, vNN, viS, viE, vK, vIns, vG, vTmp, vCK, vCLc, vCM, vN, vSR) = \
    [pg.alloc_slot() for _ in range(21)]
pg.defaults[SLO] = SL_LO; pg.defaults[SHI] = SL_HI; pg.defaults[SS] = 0xFF; pg.defaults[LKT] = 0xFF
LPAGE, vR = pg.alloc_slot(), pg.alloc_slot()
pg.defaults[SIDE] = 0xFF; pg.defaults[LSEQ] = 0xFFFFFFFF; pg.defaults[LPAGE] = 0xFF

def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()            # clobbers r2
def jmpl(m, lbl): m.a.movl_pool(lbl, 2); m.a.jmp(2); m.a.nop()
def rdb(m, addr, r): m.imm32(addr, r); m.a.movb_ld(r, r); m.a.extu_b(r, r)
def redraw(m): m.imm32(EVQ + 0x70, 1); m.a.mov_imm(1, 0); m.a.movl_st(0, 1)
def push(m, rpage):
    m.imm32(EVQ, 4); m.a.mov_rm_rn(rpage, 5); m.a.mov_imm(0, 6); m.imm32(OS_PUSH_PAGE, 2); m.a.jsr(2); m.a.nop()

def fire(m):
    a = m.a
    call(m, 'se_bg')
    call(m, 'se_init')                                                    # sequence / bars changed: default fields
    call(m, 'se_slider')
    call(m, 'se_wheel')
    call(m, 'se_clamp')
    call(m, 'se_vis')
    call(m, 'se_kt')
    ld(m, KT, 0); ld(m, LKT, 1); a.cmpeq(1, 0); nk = m.newlabel('nk'); m.bt_far(nk)
    st(m, 0, LKT); call(m, 'se_ref')
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
    st(m, 10, CUR); a.mov_rm_rn(10, 9); call(m, 'se_apply')
    a.deflabel(disp)
    call(m, 'se_disp')
    jmpl(m, '__se_end'); m.pool_jump()
    subs(m)
    a.deflabel('__se_end')
pg.on_fire(fire)

def inactive(m):
    call(m, 'se_bg'); sti(m, 0, CONF); sti(m, 0xFFFFFFFF, LSEQ)            # back on the page: fields from the sequence
    sti(m, 0xFF, LKT); sti(m, 0, SINIT)                                    # left the page: the slider may be elsewhere on return, so it has to catch the value again (user 2026-11-08: jumps on re-entry)
pg.on_inactive(inactive)

def subs(m):
    a = m.a
    # ================================ sequence memory ================================
    # ---- se_ctx (leaf): r8 = sequence (0 = none), r9 = bars. r0-r1 ----
    a.deflabel('se_ctx')
    m.imm32(CURSEQ, 1); a.movl_ld(1, 8); a.mov_imm(0, 9)
    m.imm32(ARENA_LO, 1); bad = m.newlabel('bad'); a.cmphs(1, 8); a.bf_lbl(bad); m.imm32(ARENA_HI, 1); a.cmphs(1, 8); a.bt_lbl(bad)
    a.mov_rm_rn(8, 1); a.add_imm(BARS_OFF, 1); a.movw_ld(1, 9); a.rts(); a.extu_w(9, 9)
    a.deflabel(bad); a.mov_imm(0, 8); a.rts(); a.nop()
    m.pool_jump()
    # ---- se_end (leaf): r10 = the size-0 block closing the arena; T = found. r0, r3 ----
    a.deflabel('se_end')
    m.imm32(ARENA_LO, 10); m.imm32(ARENA_HI, 3)
    el, ee = m.newlabel('el'), m.newlabel('ee'); a.deflabel(el)
    a.movl_ld(10, 0); a.tst_rr(0, 0); a.bt_lbl(ee); a.add_rr(0, 10); a.cmphs(3, 10); a.bf_lbl(el)
    a.rts(); a.clrt()
    a.deflabel(ee); a.rts(); a.sett()
    m.pool_jump()
    # ---- se_tk (leaf): r4 = bar index -> r0 = its tick, r1 = &entry. r8 = sequence ----
    a.deflabel('se_tk')
    a.mov_rm_rn(4, 1); a.shll2(1); a.add_rr(8, 1); a.add_imm(BART_OFF, 1); a.movl_ld(1, 0)
    m.imm32(TMASK, 3); a.rts(); a.and_rr(3, 0)
    m.pool_jump()
    # ---- se_find (leaf): r4 = tick -> r0 = index of the first event at or after it (END stops). r1, r2 ----
    a.deflabel('se_find')
    m.imm32(EV_OFF, 1); a.add_rr(8, 1); a.mov_imm(0, 0)
    fl, fx = m.newlabel('fl'), m.newlabel('fx'); a.deflabel(fl)
    a.movl_ld(1, 2); a.cmphs(4, 2); a.bt_lbl(fx); a.add_imm(16, 1); a.bra_lbl(fl); a.add_imm(1, 0)
    a.deflabel(fx); a.rts(); a.nop()
    m.pool_jump()
    # ---- se_ev (leaf): r4 = event index -> r0 = its address. ----
    a.deflabel('se_ev')
    a.mov_rm_rn(4, 0); a.shll2(0); a.shll2(0); a.add_rr(8, 0); m.imm32(EV_OFF, 3); a.rts(); a.add_rr(3, 0)
    m.pool_jump()
    # ---- se_grow: r4 = address, r5 = bytes: [r4, arena end + 4) moves up by r5; block size, play pointer ----
    a.deflabel('se_grow'); a.stsl_pr()
    a.bsr_lbl('se_end'); a.nop()
    a.mov_rm_rn(10, 1); a.add_imm(4, 1); a.mov_rm_rn(1, 2); a.add_rr(5, 2)
    ml, mx = m.newlabel('ml'), m.newlabel('mx'); a.deflabel(ml)
    a.cmphs(1, 4); a.bt_lbl(mx); a.add_imm(-4, 1); a.add_imm(-4, 2); a.movl_ld(1, 0); a.movl_st(0, 2); a.bra_lbl(ml); a.nop()
    a.deflabel(mx)
    a.movl_ld(8, 0); a.add_rr(5, 0); a.movl_st(0, 8)
    m.imm32(PLAYPTR, 1); a.movl_ld(1, 0); a.cmphs(4, 0); pp = m.newlabel('pp'); a.bf_lbl(pp); a.add_rr(5, 0); a.movl_st(0, 1); a.deflabel(pp)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- se_cut: r4 = address, r5 = bytes: [r4 + r5, arena end + 4) moves down to r4; block size, play pointer ----
    a.deflabel('se_cut'); a.stsl_pr()
    a.bsr_lbl('se_end'); a.nop()
    a.mov_rm_rn(4, 2); a.mov_rm_rn(4, 1); a.add_rr(5, 1); a.mov_rm_rn(10, 3); a.add_imm(4, 3)
    rl, rx = m.newlabel('rl'), m.newlabel('rx'); a.deflabel(rl)
    a.cmphs(3, 1); a.bt_lbl(rx); a.movl_ld(1, 0); a.movl_st(0, 2); a.add_imm(4, 1); a.add_imm(4, 2); a.bra_lbl(rl); a.nop()
    a.deflabel(rx)
    a.movl_ld(8, 0); a.sub(5, 0); a.movl_st(0, 8)
    m.imm32(PLAYPTR, 1); a.movl_ld(1, 0); a.cmphi(4, 0); pq = m.newlabel('pq'); a.bf_lbl(pq)     # after the cut start
    a.mov_rm_rn(4, 2); a.add_rr(5, 2); a.cmphs(2, 0); pin = m.newlabel('pin'); a.bf_lbl(pin); a.sub(5, 0); a.bra_lbl('__sepst'); a.nop()
    a.deflabel(pin); a.mov_rm_rn(4, 0)                                    # inside the cut: on the cut point
    a.deflabel('__sepst'); a.movl_st(0, 1)
    a.deflabel(pq)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- se_mask / se_unmask: interrupts off around the moves (SR saved in vSR). r0, r7 ----
    a.deflabel('se_mask')
    a.stc_sr(7); st(m, 7, vSR); a.mov_rm_rn(7, 0); a.or_imm(0xF0); a.ldc_sr(0); a.rts(); a.nop()
    a.deflabel('se_unmask')
    ld(m, vSR, 7); a.ldc_sr(7); a.rts(); a.nop()
    m.pool_jump()

    # ---- se_extend: r8 = sequence, r9 = bars, vN = new bars (> bars): bar table entries bars..vN ----
    a.deflabel('se_extend'); a.stsl_pr()
    a.mov_rm_rn(9, 4); a.add_imm(-1, 4); call(m, 'se_tk'); a.mov_rm_rn(0, 6); a.movl_ld(1, 7)    # r6 = t[n-1]
    m.imm32(0xFF000000, 0); a.and_rr(0, 7)                                 # r7 = its time signature
    a.mov_rm_rn(9, 4); call(m, 'se_tk'); a.mov_rm_rn(0, 5); a.sub(6, 0); a.mov_rm_rn(0, 6)    # r5 = t[n], r6 = L
    a.mov_rm_rn(1, 11)                                                     # r11 = &t[n] (ld uses r1)
    ld(m, vN, 3); a.sub(9, 3); a.add_imm(1, 3)                             # r3 = entries n..N
    xl = m.newlabel('xl'); a.deflabel(xl)
    a.mov_rm_rn(5, 0); a.or_rr(7, 0); a.movl_st(0, 11); a.add_imm(4, 11); a.add_rr(6, 5); a.dt(3); a.bf_lbl(xl)
    ld(m, vN, 0); a.mov_rm_rn(8, 1); a.add_imm(BARS_OFF, 1); a.movw_st(0, 1)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- se_shrink: vN = new bars (< bars): events from bar vN+1 on are deleted, bars = vN ----
    a.deflabel('se_shrink'); a.stsl_pr()
    ld(m, vN, 4); call(m, 'se_tk'); a.mov_rm_rn(0, 4); call(m, 'se_find')   # r0 = first cut
    a.mov_rm_rn(0, 4); call(m, 'se_ev'); a.mov_rm_rn(0, 11)                            # r11 = its address
    a.mov_rm_rn(11, 1); sl_, sx_ = m.newlabel('sl'), m.newlabel('sx'); m.imm32(END, 3)
    a.deflabel(sl_); a.movl_ld(1, 0); a.cmpeq(3, 0); a.bt_lbl(sx_); a.add_imm(16, 1); a.bra_lbl(sl_); a.nop()
    a.deflabel(sx_); a.mov_rm_rn(1, 5); a.sub(11, 5)                                             # r5 = bytes to cut
    a.mov_rm_rn(11, 4); a.tst_rr(5, 5); nc = m.newlabel('nc'); a.bt_lbl(nc); a.bsr_lbl('se_cut'); a.nop(); a.deflabel(nc)
    ld(m, vN, 0); a.mov_rm_rn(8, 1); a.add_imm(BARS_OFF, 1); a.movw_st(0, 1)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- se_free (leaf-ish): r0 = free bytes after the arena (0 if its end is not found). r3, r10 ----
    a.deflabel('se_free'); a.stsl_pr()
    a.bsr_lbl('se_end'); a.nop(); a.mov_imm(0, 0); fz = m.newlabel('fz'); a.bf_lbl(fz)
    m.imm32(ARENA_HI, 0); a.sub(10, 0); a.add_imm(-8, 0)
    a.deflabel(fz); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- se_copy: r8 = sequence, r9 = bars, vF vLa vA vC set. T = done; r0 = 1 too many bars, 2 memory full ----
    a.deflabel('se_copy'); a.stsl_pr()
    cx_ok, cx_tm, cx_mf, cx = m.newlabel('cok'), m.newlabel('ctm'), m.newlabel('cmf'), m.newlabel('cx')
    st(m, 9, vN)
    ld(m, vLa, 0); ld(m, vF, 1); a.sub(1, 0); a.add_imm(1, 0); st(m, 0, vM)                       # m
    ld(m, vC, 1); a.mul_l(1, 0); a.sts_macl(0); st(m, 0, vCM); a.add_rr(9, 0); st(m, 0, vNN)      # C*m, n'
    m.imm32(MAXBARS, 1); a.cmpgt(1, 0); m.bt_far(cx_tm)
    ld(m, vF, 4); a.add_imm(-1, 4); call(m, 'se_tk'); st(m, 0, vS)
    ld(m, vLa, 4); call(m, 'se_tk'); st(m, 0, vE)
    ld(m, vS, 1); a.sub(1, 0); st(m, 0, vLc); ld(m, vC, 1); a.mul_l(1, 0); a.sts_macl(0); st(m, 0, vCLc)
    ld(m, vA, 4); call(m, 'se_tk'); st(m, 0, vP)
    ld(m, vS, 4); call(m, 'se_find'); st(m, 0, viS)
    ld(m, vE, 4); call(m, 'se_find'); st(m, 0, viE)
    ld(m, vP, 4); call(m, 'se_find'); st(m, 0, vIns)
    ld(m, viE, 0); ld(m, viS, 1); a.sub(1, 0); st(m, 0, vK)
    ld(m, vC, 1); a.mul_l(1, 0); a.sts_macl(0); st(m, 0, vCK); a.shll2(0); a.shll2(0); st(m, 0, vG)   # g bytes
    m.pool_jump()
    call(m, 'se_free'); ld(m, vM, 3); ld(m, vG, 1); a.shll2(3); a.add_rr(3, 1); a.add_imm(16, 1)
    a.cmphs(1, 0); m.bf_far(cx_mf)                                         # free >= g + 4m + 16
    call(m, 'se_mask')
    # 1. room for the copies at the insertion point
    ld(m, vIns, 4); call(m, 'se_ev'); a.mov_rm_rn(0, 4); ld(m, vG, 5)
    a.tst_rr(5, 5); ng = m.newlabel('ng'); m.bt_far(ng); call(m, 'se_grow'); a.deflabel(ng)
    # 2. the source bar entries (tick | signature) saved after the arena
    a.bsr_lbl('se_end'); a.nop(); a.add_imm(8, 10); st(m, 10, vTmp)
    ld(m, vM, 3); ld(m, vTmp, 2)
    ld(m, vF, 4); a.add_imm(-1, 4); a.mov_rm_rn(4, 1); a.shll2(1); a.add_rr(8, 1); a.add_imm(BART_OFF, 1)
    tl = m.newlabel('tl'); a.deflabel(tl); a.movl_ld(1, 0); a.movl_st(0, 2); a.add_imm(4, 1); a.add_imm(4, 2); a.dt(3); a.bf_lbl(tl)
    m.pool_jump()
    # 3. the moved events (after the copies, before END): tick + C*Lc
    ld(m, vCLc, 5); ld(m, vIns, 4); ld(m, vCK, 0); a.add_rr(0, 4); call(m, 'se_ev'); a.mov_rm_rn(0, 1)
    m.imm32(END, 3)
    sl2, sx2 = m.newlabel('s2'), m.newlabel('x2'); a.deflabel(sl2)
    a.movl_ld(1, 0); a.cmpeq(3, 0); a.bt_lbl(sx2); a.add_rr(5, 0); a.movl_st(0, 1); a.add_imm(16, 1); a.bra_lbl(sl2); a.nop()
    a.deflabel(sx2)
    m.pool_jump()
    # 4. the copies: for j < C, source i in iS..iE-1 -> index ins + j*k + (i - iS), tick - S + P + j*Lc
    #    (a source at or after ins was moved: index + C*k, tick - C*Lc)
    ld(m, vK, 0); a.tst_rr(0, 0); nk_ = m.newlabel('nk'); m.bt_far(nk_)
    ld(m, vIns, 4); call(m, 'se_ev'); a.mov_rm_rn(0, 11)                               # r11 = destination
    ld(m, vP, 13); ld(m, vS, 0); a.sub(0, 13)                                                    # r13 = P - S (+ j*Lc)
    ld(m, vC, 14)                                                                                # r14 = copies left
    jl = m.newlabel('jl'); a.deflabel(jl)
    ld(m, viS, 12)                                                                               # r12 = i
    il = m.newlabel('il'); a.deflabel(il)
    a.mov_rm_rn(12, 4); a.mov_imm(0, 6); ld(m, vIns, 0); a.cmphs(0, 12); bf_ = m.newlabel('bf'); a.bf_lbl(bf_)
    ld(m, vCK, 0); a.add_rr(0, 4); ld(m, vCLc, 6)                                                # moved source
    a.deflabel(bf_); call(m, 'se_ev')                                                 # r0 = source
    a.movl_ld(0, 1); a.sub(6, 1); a.add_rr(13, 1); a.movl_st(1, 11)
    for d in (4, 8, 12): a.movl_ld_disp(0, d, 1); a.movl_st_disp(1, d, 11)
    a.add_imm(16, 11); a.add_imm(1, 12); ld(m, viE, 0); a.cmpeq(0, 12); m.bf_far(il)
    ld(m, vLc, 0); a.add_rr(0, 13); a.dt(14); m.bf_far(jl)
    m.pool_jump()
    a.deflabel(nk_)
    # 5. bar table: entries from A + C*m up = old (index - C*m) + C*Lc (signature kept), from the top down
    #    (registers only inside the loops: ld uses r1)
    ld(m, vNN, 12); ld(m, vCM, 9); ld(m, vCLc, 10); ld(m, vA, 11); a.add_rr(9, 11)              # r11 = A + C*m (lowest)
    m.imm32(TMASK, 5); a.not_rr(5, 6)                                                            # r5 mask, r6 = signature
    bl = m.newlabel('bl'); a.deflabel(bl)
    a.mov_rm_rn(12, 1); a.sub(9, 1); a.shll2(1); a.add_rr(8, 1); a.add_imm(BART_OFF, 1); a.movl_ld(1, 0)
    a.mov_rm_rn(0, 3); a.and_rr(5, 3); a.add_rr(10, 3); a.and_rr(5, 3); a.and_rr(6, 0); a.or_rr(3, 0)
    a.mov_rm_rn(12, 1); a.shll2(1); a.add_rr(8, 1); a.add_imm(BART_OFF, 1); a.movl_st(0, 1)
    a.cmpeq(11, 12); bx = m.newlabel('bx'); a.bt_lbl(bx); a.add_imm(-1, 12); a.bra_lbl(bl); a.nop()
    a.deflabel(bx)
    m.pool_jump()
    # 6. the copied bars: entry A + j*m + b = signature of b | (P + j*Lc + t[b] - S), from the saved entries
    ld(m, vP, 13); ld(m, vS, 0); a.sub(0, 13); ld(m, vC, 14); ld(m, vLc, 10); ld(m, vM, 9); ld(m, vTmp, 7)   # r13 = P - S
    ld(m, vA, 11); a.shll2(11); a.add_rr(8, 11); a.add_imm(BART_OFF, 11)                        # r11 = &t[A]
    m.imm32(TMASK, 5); a.not_rr(5, 6)
    cl = m.newlabel('cl'); a.deflabel(cl)
    a.mov_rm_rn(7, 2); a.mov_rm_rn(9, 3)
    bl2 = m.newlabel('b2'); a.deflabel(bl2)
    a.movl_ld(2, 0); a.mov_rm_rn(0, 4); a.and_rr(5, 4); a.add_rr(13, 4); a.and_rr(5, 4); a.and_rr(6, 0); a.or_rr(4, 0)
    a.movl_st(0, 11); a.add_imm(4, 11); a.add_imm(4, 2); a.dt(3); a.bf_lbl(bl2)
    a.add_rr(10, 13); a.dt(14); a.bf_lbl(cl)
    m.pool_jump()
    # 7. bars
    ld(m, vNN, 0); a.mov_rm_rn(8, 1); a.add_imm(BARS_OFF, 1); a.movw_st(0, 1)
    call(m, 'se_unmask')
    a.sett(); jmpl(m, cx)
    a.deflabel(cx_tm); a.mov_imm(1, 0); a.clrt(); jmpl(m, cx)
    a.deflabel(cx_mf); a.mov_imm(2, 0); a.clrt()
    a.deflabel(cx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ================================ redirect ================================
    # ---- se_isname: T = the display's first line starts with a Seq Edit function name (8 chars). r0, r1, r3 ----
    a.deflabel('se_isname')
    yes = m.newlabel('yes')
    for nm in SEQEDIT_NAMES:
        nx = m.newlabel('nn')
        for i, ch in enumerate(nm[:8]):
            m.imm32(LCD_SHADOW + i, 1); a.movb_ld(1, 0); m.imm32(ord(ch), 3); a.cmpeq(3, 0); m.bf_far(nx)
        jmpl(m, yes)
        a.deflabel(nx); m.pool_jump()
    a.rts(); a.clrt()
    a.deflabel(yes); a.rts(); a.sett()
    m.pool_jump()
    # ---- se_bg (every fire, any page): MODE + pad 10 -> this page ----
    a.deflabel('se_bg'); a.stsl_pr()
    m.imm32(EVQ + 0x68, 1); a.movl_ld(1, 9)
    ld(m, PREVPG, 0); a.cmpeq(9, 0); same = m.newlabel('same'); m.bt_far(same)
    a.mov_rm_rn(0, 8); st(m, 9, PREVPG); sti(m, 0, CWAIT)
    m.imm32(MODEPG, 1); a.cmpeq(1, 8); m.bf_far(same)
    m.imm32(pg.addr['page'], 1); a.cmpeq(1, 9); m.bt_far(same)
    ld(m, SEQPG, 0); a.cmpeq(9, 0); known = m.newlabel('known'); m.bt_far(known)
    st(m, 9, CAND); sti(m, WATCH, CWAIT)
    a.deflabel(same)
    ld(m, CWAIT, 0); a.tst_rr(0, 0); nw = m.newlabel('nw'); m.bt_far(nw)
    a.add_imm(-1, 0); st(m, 0, CWAIT)
    ld(m, CAND, 0); a.cmpeq(9, 0); m.bf_far(nw)
    call(m, 'se_isname'); m.bf_far(nw)
    st(m, 9, SEQPG)
    a.deflabel(known)
    sti(m, 0, CWAIT)
    m.imm32(EVQ, 4); a.mov_imm(0, 5); a.mov_imm(2, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()   # back
    m.imm32(pg.addr['page'], 8); push(m, 8)
    m.imm32(pg.addr['page'], 0); st(m, 0, PREVPG)
    a.deflabel(nw)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ================================ fields ================================
    # ---- se_init: another sequence or bar count -> NEW = bars, FIRST 1, LAST = bars, AFTER = bars ----
    a.deflabel('se_init'); a.stsl_pr()
    call(m, 'se_ctx')
    ix = m.newlabel('ix')
    ld(m, LSEQ, 0); a.cmpeq(8, 0); d1 = m.newlabel('d1'); a.bf_lbl(d1); ld(m, LBARS, 0); a.cmpeq(9, 0); m.bt_far(ix)
    a.deflabel(d1); st(m, 8, LSEQ); st(m, 9, LBARS); sti(m, 0, CONF)
    a.mov_rm_rn(9, 0); a.tst_rr(0, 0); z1 = m.newlabel('z1'); a.bf_lbl(z1); a.mov_imm(1, 0); a.deflabel(z1)
    for F, v in ((NEWF, 0), (LASTF, 0), (AFTF, 9)):
        st(m, v, F); st(m, v, LF[F])
    a.mov_imm(1, 0); st(m, 0, FIRSTF); st(m, 0, LF[FIRSTF])
    sti(m, 0xFF, LKT); redraw(m)
    a.deflabel(ix)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- se_rng: r8 = KT (1..5) -> r4 = lo, r5 = hi (bars from the sequence). r0, r1 ----
    a.deflabel('se_rng'); a.stsl_pr()
    a.movl_st_dec(8, 15); a.movl_st_dec(9, 15)
    a.mov_rm_rn(8, 3); call(m, 'se_ctx'); a.mov_rm_rn(9, 5); a.mov_rm_rn(3, 8)
    a.tst_rr(5, 5); rz = m.newlabel('rz'); a.bf_lbl(rz); a.mov_imm(1, 5); a.deflabel(rz)                # r5 = bars (>= 1)
    a.mov_imm(1, 4)
    r_new, r_cop, r_aft, r_dn = m.newlabel('rn'), m.newlabel('rc'), m.newlabel('ra'), m.newlabel('rd')
    a.mov_imm(1 + NUMF.index(COPF), 0); a.cmpeq(0, 8); a.bt_lbl(r_cop)
    a.mov_imm(1 + NUMF.index(NEWF), 0); a.cmpeq(0, 8); a.bt_lbl(r_new)
    a.mov_imm(1 + NUMF.index(AFTF), 0); a.cmpeq(0, 8); a.bt_lbl(r_aft)
    a.bra_lbl(r_dn); a.nop()
    a.deflabel(r_cop); a.mov_imm(99, 5); a.bra_lbl(r_dn); a.nop()
    a.deflabel(r_new); m.imm32(MAXBARS, 5); a.bra_lbl(r_dn); a.nop()
    a.deflabel(r_aft); a.mov_imm(0, 4)
    a.deflabel(r_dn)
    a.movl_ld_inc(15, 9); a.movl_ld_inc(15, 8)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- se_clamp: every field in its range; LAST >= FIRST ----
    a.deflabel('se_clamp'); a.stsl_pr()
    a.mov_imm(0, 14)
    for k, F in enumerate(NUMF):
        a.mov_imm(k + 1, 8); call(m, 'se_rng')
        ld(m, F, 9)
        if F == LASTF: ld(m, FIRSTF, 4)
        c1, c2, cs = m.newlabel('c1'), m.newlabel('c2'), m.newlabel('cs')
        a.cmpgt(9, 4); a.bf_lbl(c1); a.mov_rm_rn(4, 9); a.mov_imm(1, 14); a.deflabel(c1)
        a.cmpgt(5, 9); a.bf_lbl(c2); a.mov_rm_rn(5, 9); a.mov_imm(1, 14); a.deflabel(c2)
        st(m, 9, F); st(m, 9, LF[F])
        m.pool_jump()
    a.tst_rr(14, 14); cr = m.newlabel('cr'); a.bt_lbl(cr); redraw(m); a.deflabel(cr)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- se_wheel: a field the wheel moved (by the digit under the cursor) ----
    a.deflabel('se_wheel'); a.stsl_pr()
    wx, wch = m.newlabel('wx'), m.newlabel('wch')
    m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 9); ld(m, LFOC, 0); a.cmpeq(0, 9); nf = m.newlabel('nf'); m.bt_far(nf)
    st(m, 9, LFOC); sti(m, 0, DIG)
    a.deflabel(nf)
    for F in NUMF:
        nx_, asis, go = m.newlabel('wn'), m.newlabel('wa'), m.newlabel('wg')
        ld(m, F, 4); ld(m, LF[F], 11); a.cmpeq(4, 11); m.bt_far(nx_)
        a.mov_rm_rn(4, 6); a.sub(11, 6)
        ld(m, DIG, 3); a.mov_imm(1, 7); pw = m.newlabel('pw'); pz = m.newlabel('pz'); a.deflabel(pw)
        a.tst_rr(3, 3); a.bt_lbl(pz); a.mov_rm_rn(7, 0); a.shll2(7); a.add_rr(0, 7); a.shll(7); a.add_imm(-1, 3)
        a.bra_lbl(pw); a.nop(); a.deflabel(pz)
        a.mov_imm(1, 0); a.cmpeq(0, 7); m.bt_far(asis)
        a.mov_rm_rn(6, 4); a.cmppz(4); ab = m.newlabel('ab'); a.bt_lbl(ab); a.neg(4, 4); a.deflabel(ab)
        a.mov_rm_rn(7, 5); pg._hcall(m, '__udiv'); a.tst_rr(1, 1); m.bt_far(asis)
        a.mul_l(7, 6); a.sts_macl(9); a.add_rr(11, 9); jmpl(m, go)
        a.deflabel(asis); ld(m, F, 9)
        a.deflabel(go)
        a.cmppz(9); k0 = m.newlabel('k0'); a.bt_lbl(k0); a.mov_imm(0, 9); a.deflabel(k0)      # (clamped later)
        st(m, 9, F); st(m, 9, LF[F]); jmpl(m, wch)
        a.deflabel(nx_); m.pool_jump()
    jmpl(m, wx)
    a.deflabel(wch)
    sti(m, 0xFF, LKT); sti(m, 0, CAUGHT); sti(m, 0xFF, SIDE); sti(m, 0, CONF); redraw(m)
    a.deflabel(wx)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- se_kt: KT = the number field under the cursor (0 = none / question asked) ----
    a.deflabel('se_kt')
    m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 3); a.mov_imm(0, 9)
    for k, F in enumerate(NUMF):
        n = m.newlabel('kt'); m.imm32(W(F), 1); a.cmpeq(1, 3); a.bf_lbl(n); a.mov_imm(k + 1, 9); a.deflabel(n)
    ld(m, CONF, 0); a.tst_rr(0, 0); k2 = m.newlabel('k2'); a.bt_lbl(k2); a.mov_imm(0, 9); a.deflabel(k2)
    st(m, 9, KT); a.rts(); a.nop()
    m.pool_jump()
    # ---- se_ref: slider steps and current value of the KT field; caught only once the slider gets there ----
    a.deflabel('se_ref'); a.stsl_pr()
    ld(m, KT, 8); a.mov_imm(0, 3); a.mov_imm(0, 9); a.tst_rr(8, 8); rset = m.newlabel('rset'); m.bt_far(rset)
    call(m, 'se_rng'); a.mov_rm_rn(5, 3); a.sub(4, 3)                    # steps = hi - lo
    for k, F in enumerate(NUMF):
        n = m.newlabel('rf'); a.mov_imm(k + 1, 0); a.cmpeq(0, 8); a.bf_lbl(n); ld(m, F, 9); a.deflabel(n)
    a.sub(4, 9)
    a.deflabel(rset)
    a.cmppz(9); z = m.newlabel('z'); a.bt_lbl(z); a.mov_imm(0, 9); a.deflabel(z)
    ld(m, NSTEP, 0); a.cmpeq(3, 0); sn = m.newlabel('sn'); m.bt_far(sn)
    st(m, 3, NSTEP); m.imm32(0xFF, 0); st(m, 0, SS); call(m, 'se_slider')
    a.deflabel(sn)
    st(m, 9, CUR); ld(m, SS, 10); a.cmpeq(9, 10); a.movt(0); st(m, 0, CAUGHT)
    a.cmphi(9, 10); a.movt(0); st(m, 0, SIDE)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- se_apply: r9 = slider step -> the KT field = lo + step ----
    a.deflabel('se_apply'); a.stsl_pr()
    ld(m, KT, 8); a.tst_rr(8, 8); ax = m.newlabel('ax'); m.bt_far(ax)
    a.movl_st_dec(9, 15); call(m, 'se_rng'); a.movl_ld_inc(15, 9); a.add_rr(4, 9)
    for k, F in enumerate(NUMF):
        n = m.newlabel('ap'); a.mov_imm(k + 1, 0); a.cmpeq(0, 8); a.bf_lbl(n); st(m, 9, F); st(m, 9, LF[F]); a.deflabel(n)
    sti(m, 0, CONF); redraw(m)
    a.deflabel(ax); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- se_slider: SS = 0..NSTEP (Song add-on: filtered, learned range, hysteresis, exact ends) ----
    a.deflabel('se_slider'); a.stsl_pr()
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
    sx2_, setv = m.newlabel('sx'), m.newlabel('set')
    ld(m, SS, 0); a.cmpeq(3, 0); m.bt_far(sx2_)
    ld(m, NSTEP, 1); a.cmphi(1, 0); m.bt_far(setv)
    a.tst_rr(3, 3); m.bt_far(setv); ld(m, NSTEP, 1); a.cmpeq(1, 3); m.bt_far(setv)
    a.mov_rm_rn(0, 4); a.mul_l(7, 4); a.sts_macl(4); ld(m, NSTEP, 5)
    a.movl_st_dec(3, 15); pg._hcall(m, '__udiv'); a.movl_ld_inc(15, 3)
    a.sub(0, 6); a.cmppz(6); ab = m.newlabel('ab'); a.bt_lbl(ab); a.neg(6, 6); a.deflabel(ab)
    a.mov_rm_rn(7, 4); ld(m, NSTEP, 5); a.shll(5); a.movl_st_dec(3, 15); a.movl_st_dec(6, 15)
    pg._hcall(m, '__udiv'); a.movl_ld_inc(15, 6); a.movl_ld_inc(15, 3); a.add_imm(HYST, 0)
    a.cmphi(0, 6); m.bf_far(sx2_)
    a.deflabel(setv); st(m, 3, SS)
    a.deflabel(sx2_); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- se_vis: the fields of the page; the cursor skips hidden fields (Song add-on) ----
    a.deflabel('se_vis'); a.stsl_pr()
    WL = [W(s) for s in CURSOR]
    a.mov_imm(0, 14)
    rdb(m, PARAM, 4)
    ld(m, LPAGE, 0); a.cmpeq(4, 0); lp_ = m.newlabel('lp'); a.bt_lbl(lp_); st(m, 4, LPAGE); sti(m, 0, CONF)   # another page: no question
    a.deflabel(lp_)
    a.mov_imm(0, 5); a.mov_imm(0, 13); call(m, 'se_ctx'); a.tst_rr(8, 8); ok0 = m.newlabel('ok0'); a.bt_lbl(ok0)
    ld(m, CONF, 0); a.tst_rr(0, 0); a.movt(5)                                      # r5 = a sequence, no question
    a.mov_imm(Q_DUP, 1); a.cmpeq(1, 0); a.movt(13)                                 # r13 = the DUPLICATE? question
    a.deflabel(ok0)
    rdb(m, PARAM, 4)
    def vis(w, params, cond=5):
        n1, n2, yes_ = m.newlabel('v1'), m.newlabel('v2'), m.newlabel('vy')
        m.imm32(w + 0x17, 1); a.movb_ld(1, 0); a.extu_b(0, 0); a.mov_rm_rn(0, 3); a.and_imm(0x7F)
        a.tst_rr(cond, cond); m.bt_far(n1)
        for p in params: a.mov_imm(p, 2); a.cmpeq(2, 4); m.bt_far(yes_)
        jmpl(m, n1)
        a.deflabel(yes_); a.or_imm(0x80)
        a.deflabel(n1); a.cmpeq(0, 3); a.bt_lbl(n2); a.movb_st(0, 1); a.mov_imm(1, 14); a.deflabel(n2)
        m.pool_jump()
    vis(W(NEWF), [P_LEN]); vis(W(COPF), [P_COPY]); vis(W(FIRSTF), [P_COPY]); vis(W(LASTF), [P_COPY]); vis(W(AFTF), [P_COPY])
    vis(W(DUPF), [P_LEN], cond=13); vis(pg.addr['dlabels'][R0] - 0x28, [P_COPY])
    vdone, vpick, vback, vgo = m.newlabel('vd'), m.newlabel('vp'), m.newlabel('vb'), m.newlabel('vg')
    def index(rs, rd_):
        a.mov_imm(-1, rd_)
        for i, w in enumerate(WL):
            n = m.newlabel('ix'); m.imm32(w, 1); a.cmpeq(1, rs); a.bf_lbl(n); a.mov_imm(i, rd_); a.deflabel(n)
    m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 9)
    index(9, 11); a.cmppz(11); m.bf_far(vdone)
    m.pool_jump()
    a.mov_rm_rn(9, 1); a.add_imm(0x17, 1); a.movb_ld(1, 0); a.tst_imm(0x80); m.bf_far(vdone)
    ld(m, LASTF_, 10); index(10, 12)
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
    m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 0); st(m, 0, LASTF_)
    a.tst_rr(14, 14); vr = m.newlabel('vr'); m.bt_far(vr); redraw(m)
    a.deflabel(vr); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- se_ndig (leaf): r4 = number, r5 = width -> r0 = its last digit index. r0, r1, r3 ----
    a.deflabel('se_ndig')
    nl, nx = m.newlabel('ndl'), m.newlabel('ndx')
    a.mov_imm(0, 0); a.mov_imm(10, 1); a.add_imm(-1, 5)
    a.deflabel(nl); a.cmpeq(5, 0); a.bt_lbl(nx); a.cmphs(1, 4); a.bf_lbl(nx)
    a.add_imm(1, 0); a.mov_rm_rn(1, 3); a.shll2(1); a.add_rr(3, 1); a.shll(1)
    a.bra_lbl(nl); a.nop()
    a.deflabel(nx); a.add_imm(1, 5); a.rts(); a.nop()
    m.pool_jump()
    # ---- se_disp: row 0 right / row 1 texts, the digit cursor; redraw only when something changed ----
    a.deflabel('se_disp'); a.stsl_pr()
    a.mov_imm(0, 14)
    def cput(s):
        for ch in s:
            n = m.newlabel('cp'); a.movb_ld(12, 1); a.mov_imm(ord(ch), 0); a.cmpeq(0, 1); a.bt_lbl(n)
            a.movb_st(0, 12); a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
    def cputr(r):
        n = m.newlabel('cr'); a.movb_ld(12, 1); a.cmpeq(r, 1); a.bt_lbl(n); a.mov_rm_rn(r, 0); a.movb_st(0, 12)
        a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
    def num3(r, pad='0'):
        """r (0..999) as 3 characters (leading zeros or spaces). r4, r5, r10, r11, r13"""
        a.mov_rm_rn(r, 4); a.mov_imm(100, 5); pg._hcall(m, '__udiv'); a.mov_rm_rn(1, 11); a.mov_rm_rn(0, 13)
        a.mov_rm_rn(11, 4); a.mov_imm(10, 5); pg._hcall(m, '__udiv'); a.mov_rm_rn(1, 10); a.mov_rm_rn(0, 11)
        if pad == '0':
            for rr in (13, 11, 10): a.add_imm(0x30, rr); cputr(rr)
        else:
            s1, s2 = m.newlabel('s1'), m.newlabel('s2')
            a.mov_rm_rn(13, 4); a.tst_rr(4, 4); a.add_imm(0x30, 13); a.bf_lbl(s1); a.mov_imm(32, 13); a.deflabel(s1); cputr(13)
            a.mov_rm_rn(11, 5); a.or_rr(4, 5); a.tst_rr(5, 5); a.add_imm(0x30, 11); a.bf_lbl(s2); a.mov_imm(32, 11); a.deflabel(s2); cputr(11)
            a.add_imm(0x30, 10); cputr(10)
    rdb(m, PARAM, 8)
    call(m, 'se_ctx')                                                   # r8 = seq, r9 = bars
    rdb(m, PARAM, 6)
    # row 0 (COPY): ":   -   " between the bar numbers (hidden on the other pages)
    m.imm32(pg.addr['dlabels'][R0], 12); cput(":   -   ")
    # row 1
    m.imm32(pg.addr['dlabels'][R1], 12)
    rd_, rq, rns = m.newlabel('rd'), m.newlabel('rq'), m.newlabel('rns')
    rdb(m, PARAM, 6)
    a.mov_imm(P_ENTER, 0); a.cmpeq(0, 6); ren = m.newlabel('ren'); m.bt_far(ren)
    a.tst_rr(8, 8); m.bt_far(rns)
    ld(m, CONF, 0); a.tst_rr(0, 0); m.bf_far(rq)
    rs_ = m.newlabel('rs')
    a.mov_imm(P_LEN, 0); a.cmpeq(0, 6); m.bt_far(rs_)
    cput("AFTER:      x   "); jmpl(m, rd_)
    m.pool_jump()
    a.deflabel(rs_); cput("Now="); num3(9, ' '); cput(" New:    "); jmpl(m, rd_)
    m.pool_jump()
    a.deflabel(rq); a.mov_imm(Q_DUP, 1); a.cmpeq(1, 0); rqd = m.newlabel('rqd'); m.bf_far(rqd)
    cput("DUPLICATE?      "); jmpl(m, rd_)
    a.deflabel(rqd); cput("DEL "); ld(m, NEWF, 9); a.add_imm(1, 9); num3(9); cput("-")
    call(m, 'se_ctx'); num3(9); cput(" ?   "); jmpl(m, rd_)
    m.pool_jump()
    a.deflabel(ren); cput("MPC SEQ EDIT    "); jmpl(m, rd_)
    a.deflabel(rns); cput("NO SEQUENCE     ")
    a.deflabel(rd_)
    m.pool_jump()
    # digit cursor (only on the digits the number has: zero padded = every digit)
    for F, wdt in DIGW.items():
        dg, c0 = m.newlabel('dg'), m.newlabel('c0')
        m.imm32(0x7FFFFFFF, 4); a.mov_imm(wdt, 5); call(m, 'se_ndig')
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

    # ---- se_msgbars: message "<prefix>NNN BARS" with r9 = bars ----
    a.deflabel('se_msgok'); a.stsl_pr()
    m.imm32(pg.addr['msgt'], 12)
    for ch in "DONE: ": a.mov_imm(ord(ch), 0); a.movb_st(0, 12); a.add_imm(1, 12)
    a.mov_rm_rn(9, 4); a.mov_imm(100, 5); pg._hcall(m, '__udiv'); a.mov_rm_rn(1, 11); a.mov_rm_rn(0, 13)
    a.mov_rm_rn(11, 4); a.mov_imm(10, 5); pg._hcall(m, '__udiv')
    for r in (13, 0, 1): a.add_imm(0x30, r); a.movb_st(r, 12); a.add_imm(1, 12)
    for ch in " BARS": a.mov_imm(ord(ch), 0); a.movb_st(0, 12); a.add_imm(1, 12)
    a.mov_imm(0, 0); a.movb_st(0, 12); pg._hcall(m, '__showmsg')
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

def doit(m):
    a = m.a
    dx = m.newlabel('dx'); ok = m.newlabel('ok'); res = m.newlabel('res')
    rdb(m, PARAM, 9)
    ne = m.newlabel('ne'); a.mov_imm(P_ENTER, 0); a.cmpeq(0, 9); m.bf_far(ne)
    ld(m, SEQPG, 8); a.tst_rr(8, 8); nr = m.newlabel('nr'); m.bt_far(nr); push(m, 8); jmpl(m, dx)
    a.deflabel(nr); pg.emit_message(m, "PRESS MODE+PAD10"); jmpl(m, dx)
    m.pool_jump()
    a.deflabel(ne)
    a.movl_st_dec(9, 15); call(m, 'se_ctx'); a.mov_rm_rn(9, 10); a.movl_ld_inc(15, 9)   # r10 = bars
    a.tst_rr(8, 8); hs = m.newlabel('hs'); m.bf_far(hs); pg.emit_message(m, "NO SEQUENCE"); jmpl(m, dx)
    a.deflabel(hs)
    a.tst_rr(10, 10); hb = m.newlabel('hb'); m.bf_far(hb); pg.emit_message(m, "EMPTY SEQUENCE"); jmpl(m, dx)
    a.deflabel(hb)
    m.pool_jump()
    def focus(F): m.imm32(pg.addr['page'], 4); m.imm32(W(F), 5); m.imm32(SET_FOCUS, 2); a.jsr(2); a.nop()
    nlen = m.newlabel('nlen')
    a.mov_imm(P_LEN, 0); a.cmpeq(0, 9); m.bf_far(nlen)
    # ---- Sequence length ----
    a.mov_rm_rn(10, 9)
    ld(m, NEWF, 0); st(m, 0, vN); a.cmpeq(9, 0); same = m.newlabel('same'); m.bf_far(same)
    pg.emit_message(m, "NO CHANGE"); jmpl(m, dx)
    a.deflabel(same)
    a.cmpgt(9, 0); shr = m.newlabel('shr'); m.bf_far(shr)
    # longer: "DUPLICATE?" YES / NO first
    ld(m, CONF, 0); a.mov_imm(Q_DUP, 1); a.cmpeq(1, 0); qd = m.newlabel('qd'); m.bt_far(qd)
    sti(m, Q_DUP, CONF); m.imm32(DUPF, 1); a.mov_imm(0, 0); a.movb_st(0, 1); focus(DUPF); redraw(m); jmpl(m, dx)
    m.pool_jump()
    a.deflabel(qd); sti(m, 0, CONF); focus(NEWF)
    m.imm32(DUPF, 1); a.movb_ld(1, 0); a.tst_rr(0, 0); dup = m.newlabel('dup'); m.bt_far(dup)
    call(m, 'se_ctx'); call(m, 'se_mask'); call(m, 'se_extend'); call(m, 'se_unmask'); jmpl(m, ok)   # NO: empty bars
    m.pool_jump()
    # YES: bars 1..n again and again: q whole copies, then bars 1..r
    a.deflabel(dup)
    call(m, 'se_ctx'); ld(m, vN, 4); a.sub(9, 4); a.mov_rm_rn(9, 5); pg._hcall(m, '__udiv')   # r0 = q, r1 = r
    m.imm32(vR, 3); a.movl_st(1, 3)                                              # (st() would use r1)
    a.tst_rr(0, 0); nq = m.newlabel('nq'); m.bt_far(nq)
    st(m, 0, vC); a.mov_imm(1, 0); st(m, 0, vF); call(m, 'se_ctx'); st(m, 9, vLa); st(m, 9, vA)
    call(m, 'se_copy'); m.bf_far('se_cpfail')
    a.deflabel(nq)
    ld(m, vR, 0); a.tst_rr(0, 0); m.bt_far(ok)
    st(m, 0, vLa); a.mov_imm(1, 0); st(m, 0, vF); st(m, 0, vC); call(m, 'se_ctx'); st(m, 9, vA)
    call(m, 'se_copy'); m.bf_far('se_cpfail'); jmpl(m, ok)
    m.pool_jump()
    # shorter: "DEL ... ?" first
    a.deflabel(shr)
    ld(m, CONF, 0); a.mov_imm(Q_DEL, 1); a.cmpeq(1, 0); conf = m.newlabel('conf'); m.bt_far(conf)
    sti(m, Q_DEL, CONF); redraw(m); jmpl(m, dx)
    a.deflabel(conf); sti(m, 0, CONF)
    call(m, 'se_ctx'); call(m, 'se_mask'); call(m, 'se_shrink'); call(m, 'se_unmask'); jmpl(m, ok)
    m.pool_jump()
    a.deflabel(nlen)
    # ---- COPY ----
    ld(m, FIRSTF, 0); st(m, 0, vF); ld(m, LASTF, 0); st(m, 0, vLa); ld(m, AFTF, 0); st(m, 0, vA); ld(m, COPF, 0); st(m, 0, vC)
    a.mov_rm_rn(10, 9); call(m, 'se_copy'); m.bt_far(ok)
    a.deflabel('se_cpfail')
    a.mov_imm(1, 1); a.cmpeq(1, 0); mf = m.newlabel('mf'); m.bf_far(mf)
    pg.emit_message(m, "TOO MANY BARS"); jmpl(m, dx)
    a.deflabel(mf); pg.emit_message(m, "MEMORY FULL"); jmpl(m, dx)
    m.pool_jump()
    a.deflabel(ok)
    call(m, 'se_ctx'); call(m, 'se_msgok')
    sti(m, 0xFFFFFFFF, LSEQ)                                                      # fields from the new length
    a.deflabel(dx)
pg.on_doit(doit)

def cancel(m, hret1):
    """a question (DUPLICATE? / DEL ... ?): CANCEL drops it, nothing changes; otherwise CANCEL leaves as usual"""
    a = m.a
    lv = m.newlabel('lv')
    ld(m, CONF, 0); a.tst_rr(0, 0); m.bt_far(lv)
    sti(m, 0, CONF); pg.emit_message(m, "CANCELLED")
    m.imm32(pg.addr['page'], 4); m.imm32(W(NEWF), 5); m.imm32(SET_FOCUS, 2); a.jsr(2); a.nop()
    redraw(m); jmpl(m, hret1)
    m.pool_jump()
    a.deflabel(lv)
pg.cancel_fn = cancel

def ev_key(m, hret0, hret1):
    """LEFT / RIGHT on a number: the digit; past them, the OS (next field)"""
    a = m.a
    kx, kl, kr, kd = m.newlabel('kx'), m.newlabel('kl'), m.newlabel('kr'), m.newlabel('kd')
    m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 12)
    a.mov_imm(-1, 11)
    for F, wdt in DIGW.items():
        n = m.newlabel('kf'); m.imm32(W(F), 1); a.cmpeq(1, 12); a.bf_lbl(n)
        m.imm32(0x7FFFFFFF, 4); a.mov_imm(wdt, 5); call(m, 'se_ndig'); a.mov_rm_rn(0, 11); a.deflabel(n)
    a.cmppz(11); m.bf_far(kx)
    a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 10); a.extu_w(10, 10)
    a.mov_imm(K_LEFT, 0); a.cmpeq(0, 10); m.bt_far(kl)
    a.mov_imm(K_RIGHT, 0); a.cmpeq(0, 10); m.bt_far(kr)
    jmpl(m, kx)
    m.pool_jump()
    a.deflabel(kl); ld(m, DIG, 0); a.cmpeq(11, 0); m.bt_far(kx); a.add_imm(1, 0); jmpl(m, kd)
    a.deflabel(kr); ld(m, DIG, 0); a.tst_rr(0, 0); m.bt_far(kx); a.add_imm(-1, 0)
    a.deflabel(kd); st(m, 0, DIG); call(m, 'se_disp'); redraw(m)
    jmpl(m, hret1)
    m.pool_jump()
    a.deflabel(kx)
pg.on_event(2, ev_key)


# =====================================================================================================================
def ref_copy(seq, F, La, A, C):
    """Python reference of the copy (insert) of bars F..La after A, C times: returns the new sequence block"""
    b = bytearray(seq); n = struct.unpack_from('<H', b, BARS_OFF)[0]
    t = [struct.unpack_from('<I', b, BART_OFF + 4 * i)[0] for i in range(1000)]
    tk = lambda i: t[i] & TMASK
    evs = []; o = EV_OFF
    while True:
        e = bytes(b[o:o + 16]); o += 16
        if struct.unpack('<I', e[:4])[0] == END: endev = e; break
        evs.append(e)
    m_ = La - F + 1; S, E, P = tk(F - 1), tk(La), tk(A); Lc = E - S
    src = [e for e in evs if S <= struct.unpack('<I', e[:4])[0] < E]
    before = [e for e in evs if struct.unpack('<I', e[:4])[0] < P]
    after = [e for e in evs if struct.unpack('<I', e[:4])[0] >= P]
    shift = lambda e, d: struct.pack('<I', struct.unpack('<I', e[:4])[0] + d) + e[4:]
    copies = [shift(e, P - S + j * Lc) for j in range(C) for e in src]
    new = before + copies + [shift(e, C * Lc) for e in after]
    nt = t[:]
    for i in range(A + C * m_, n + C * m_ + 1):
        nt[i] = (t[i - C * m_] & 0xFF000000) | ((tk(i - C * m_) + C * Lc) & TMASK)
    for j in range(C):
        for k in range(m_):
            nt[A + j * m_ + k] = (t[F - 1 + k] & 0xFF000000) | ((P + j * Lc + tk(F - 1 + k) - S) & TMASK)
    out = bytearray(b[:EV_OFF])
    struct.pack_into('<H', out, BARS_OFF, n + C * m_)
    for i in range(1000): struct.pack_into('<I', out, BART_OFF + 4 * i, nt[i])
    out += b''.join(new) + endev
    struct.pack_into('<I', out, 0, len(out))
    return bytes(out)

def selftest(out):
    WT = lambda F: W(F) - (pg.addr['page'] - pg.page)                        # pg.addr = the last (relocation) build
    from validate import Harness
    from shasm import SHAsm
    from pages import ENABLE_DOIT
    from testseq import make_seq, events as tevents
    LOG, MSG = 0x88E00000, 0x88E10000
    h = Harness(os.path.join(os.path.dirname(__file__), '..', 'mpc500_loader.bin')); h.fill_scratch(); e = h.emu
    def stub(addr, fn):
        s = SHAsm(addr); fn(s); blob = s.assemble()
        for i in range(0, len(blob), 2): h.ram_w16(addr + i, struct.unpack('<H', blob[i:i+2])[0])
    stub(OS_PUSH_PAGE, lambda s: (s.movl_pool(LOG, 1), s.movl_ld(1, 0), s.add_imm(1, 0), s.movl_st(0, 1), s.shll2(0), s.shll2(0),
                                  s.add_rr(0, 1), s.movl_st(5, 1), s.movl_st_disp(6, 4, 1), s.rts(), s.nop()))
    stub(ENABLE_DOIT, lambda s: (s.rts(), s.nop()))
    stub(SET_FOCUS, lambda s: (s.movl_st_disp(5, 0x24, 4), s.rts(), s.nop()))
    R32, W32 = h.ram_r32, h.ram_w32
    # a sequence with a 3/4 bar (bar 3) and a second track, then another block after it in the arena
    evs = tevents(8) + [(384 * k + 200, 2, 60 + k, 90, 30) for k in range(8)]
    evs.sort(key=lambda x: x[0])
    seq = bytearray(make_seq(bars=8, evs=evs))
    t = [(0x60 << 24) | (i * 384) for i in range(1000)]
    for i in range(3, 1000): t[i] = (t[i] & 0xFF000000) | ((i * 384 - 96) & TMASK)          # bar 3 is 3/4
    t[2] = (0x48 << 24) | (2 * 384)
    for i in range(1000): struct.pack_into('<I', seq, BART_OFF + 4 * i, t[i])
    evs2 = [x for x in evs if not (2 * 384 + 288 <= x[0] < 3 * 384)]                          # nothing in bar 3's lost beat
    evs2 = [(x[0] - 96 if x[0] >= 3 * 384 else x[0],) + x[1:] for x in evs2]
    seq = bytearray(make_seq(bars=8, evs=evs2))
    for i in range(1000): struct.pack_into('<I', seq, BART_OFF + 4 * i, t[i])
    seq = bytes(seq)
    P = ARENA_LO; blk2 = struct.pack('<I', 64) + bytes(range(60))
    def load_arena(sq):
        arena = sq + blk2 + bytes(4)
        for i in range(0, len(arena), 4): W32(P + i, struct.unpack('<I', arena[i:i+4])[0])
    def arena_seq(): n = R32(P); return bytes(e.rd(P + i, 1) for i in range(n))
    def after_ok():
        n = R32(P); return bytes(e.rd(P + n + i, 1) for i in range(64)) == blk2 and R32(P + n + 64) == 0
    load_arena(seq); W32(CURSEQ, P); W32(PLAYPTR, P + EV_OFF + 16 * 30)
    h.load_hub(); h.load_wav(out); h.fire(5); assert R32(0x88F00144) == 1, "add-on not installed"
    def pushes(): return [(R32(LOG + 16 * (k + 1)), R32(LOG + 16 * (k + 1) + 4)) for k in range(R32(LOG))]
    def call(type_, code=0, val=0):
        W32(MSG, (type_ & 0xffff) | (code << 16)); W32(MSG + 4, val & 0xffffffff); W32(MSG + 8, 0); W32(MSG + 12, 0)
        regs = [0x22220000 + i for i in range(16)]; regs[15] = 0x88400000 - 256; regs[4] = pg.page; regs[5] = MSG
        e.r = regs[:]; e.pr = 0xDEADBEEE; e.pc = pg.handler; e.maxsteps = 80_000_000
        assert e.run(stop_pc=0xDEADBEEE), "handler did not return"
        for i in range(8, 16): assert e.r[i] == regs[i], "r%d corrupted" % i
        return e.r[0]
    msg = lambda: bytes(e.rd(pg.msgt + i, 1) for i in range(16)).split(b'\0')[0].decode().rstrip()
    lab = lambda d, w: bytes(e.rd(pg.dlabels[d] + i, 1) for i in range(w)).decode()
    def doit(): W32(LOG, 0); call(2, 0x2E); h.fire(2)
    def page(p): e.wr(PARAM, p, 1); h.fire(3)
    bars = lambda: e.rd(P + BARS_OFF, 2)
    W32(EVQ + 0x68, pg.page); h.fire(4)
    assert R32(NEWF) == 8 and R32(FIRSTF) == 1 and R32(LASTF) == 8 and R32(AFTF) == 8, [R32(x) for x in (NEWF, FIRSTF, LASTF, AFTF)]
    assert lab(R1, 16) == "Now=  8 New:    ", repr(lab(R1, 16))
    print("  ok opens on Sequence length: 'Now=  8 New:' 8; COPY defaults bars 1-8 after 8")
    focusw = lambda: R32(pg.page + 0x24)
    def length(new, dup=None):
        """Sequence length -> new: DO IT (question), [wheel YES/NO], DO IT"""
        load_arena(seq); W32(PLAYPTR, P + EV_OFF + 16 * 30); h.fire(3); page(P_LEN); W32(NEWF, new); h.fire(2); doit()
        if dup is not None:
            assert R32(CONF) == Q_DUP and lab(R1, 16)[:10] == "DUPLICATE?" and focusw() == WT(DUPF), (lab(R1, 16), hex(focusw()), {n: hex(WT(F)) for n, F in (('PARAM', PARAM), ('FIRST', FIRSTF), ('LAST', LASTF), ('AFT', AFTF), ('DUP', DUPF), ('NEW', NEWF), ('COP', COPF))}, e.rd(WT(DUPF) + 0x17, 1))
            e.wr(DUPF, 0 if dup else 1, 1); h.fire(2)
        doit()
    # --- longer, DUPLICATE YES: bars 1..n again and again ---
    for new, want in ((16, ref_copy(seq, 1, 8, 8, 1)), (12, ref_copy(seq, 1, 4, 8, 1)), (24, ref_copy(seq, 1, 8, 8, 2)),
                      (20, ref_copy(ref_copy(seq, 1, 8, 8, 1), 1, 4, 16, 1))):
        length(new, dup=True); got = arena_seq()
        assert got == want and after_ok() and msg() == "DONE: %03d BARS" % new, (new, len(got), len(want), msg())
        assert focusw() == WT(NEWF) and lab(R1, 16) == "Now=%3d New:    " % new, (hex(focusw()), lab(R1, 16))
    assert R32(PLAYPTR) == P + EV_OFF + 16 * 30, "play pointer before the insertion point stays"
    print("  ok Sequence length + DUPLICATE YES: 8->16 (1-8 copied), 8->12 (1-4 -> 9-12), 8->24 (twice), 8->20 (1-8, then 1-4): = reference")
    # --- longer, DUPLICATE NO: empty bars ---
    length(12, dup=False)
    assert bars() == 12 and msg() == "DONE: 012 BARS", (bars(), msg())
    tt = [R32(P + BART_OFF + 4 * i) for i in range(13)]
    assert all((tt[i + 1] & TMASK) - (tt[i] & TMASK) == 384 and tt[i] >> 24 == t[7] >> 24 for i in range(8, 12)), [hex(x) for x in tt]
    assert arena_seq()[EV_OFF:] == seq[EV_OFF:] and after_ok(), "events unchanged"
    print("  ok DUPLICATE NO: 8 -> 12 with 4 empty bars in the last bar's signature, events untouched")
    # --- the question: CANCEL changes nothing ---
    load_arena(seq); h.fire(3); page(P_LEN); W32(NEWF, 16); h.fire(2); doit(); assert R32(CONF) == Q_DUP
    W32(LOG, 0); call(2, 0x2D); h.fire(2); assert bars() == 8 and msg() == "CANCELLED" and R32(CONF) == 0 and arena_seq() == seq, msg()
    # --- shorter: DEL ... ? ---
    length(12, dup=False); W32(NEWF, 5); h.fire(2); doit(); assert bars() == 12 and lab(R1, 16) == "DEL 006-012 ?   ", repr(lab(R1, 16))
    W32(LOG, 0); call(2, 0x2D); h.fire(2); assert bars() == 12 and msg() == "CANCELLED", msg()
    W32(NEWF, 5); h.fire(2); doit(); doit()
    cut = (struct.unpack_from('<I', seq, BART_OFF + 4 * 5)[0]) & TMASK
    keep = [x for x in evs2 if x[0] < cut]
    ev_ = []; o = P + EV_OFF
    while R32(o) != END: ev_.append(R32(o)); o += 16
    assert bars() == 5 and ev_ == [x[0] for x in keep] and after_ok() and msg() == "DONE: 005 BARS", (bars(), len(ev_), len(keep), msg())
    print("  ok shorter 12 -> 5: asks 'DEL 006-012 ?', CANCEL keeps, DO IT deletes the notes from bar 6 on; DUPLICATE? + CANCEL: nothing")
    # --- COPY: "COPY:001-016" / "AFTER:016   x01" ---
    load_arena(seq); W32(PLAYPTR, P + EV_OFF + 16 * 30); h.fire(3)
    page(P_COPY); W32(FIRSTF, 2); W32(LASTF, 3); W32(AFTF, 1); W32(COPF, 2); h.fire(2)
    assert lab(R0, 8) == ":   -   " and lab(R1, 16) == "AFTER:      x   ", (lab(R0, 8), lab(R1, 16))
    want = ref_copy(seq, 2, 3, 1, 2); doit()
    got = arena_seq(); assert got == want and after_ok(), (len(got), len(want), msg())
    assert R32(PLAYPTR) > P + EV_OFF + 16 * 30, "play pointer after the insertion moved"
    print("  ok COPY bars 2-3 after 1 x2: 8 -> 12 bars, = reference (later bars moved), msg %r" % msg())
    for F_, L_, A_, C_ in ((1, 4, 2, 1), (3, 8, 0, 1), (8, 8, 8, 3)):
        load_arena(seq); h.fire(3); page(P_COPY); W32(FIRSTF, F_); W32(LASTF, L_); W32(AFTF, A_); W32(COPF, C_); h.fire(2)
        want = ref_copy(seq, F_, L_, A_, C_); doit(); got = arena_seq()
        assert got == want and after_ok(), ((F_, L_, A_, C_), len(got), len(want))
    print("  ok COPY 1-4 after 2, 3-8 after 0, 8 after 8 x3: = reference")
    # --- limits ---
    load_arena(seq); e.wr(P + BARS_OFF, 500, 2); h.fire(3); page(P_COPY); W32(FIRSTF, 1); W32(LASTF, 500); W32(AFTF, 500); W32(COPF, 1); h.fire(2); doit()
    assert msg() == "TOO MANY BARS" and arena_seq()[EV_OFF:] == seq[EV_OFF:], msg()
    load_arena(seq); h.fire(3); page(P_COPY)
    W32(LASTF, 0); W32(FIRSTF, 5); h.fire(2); assert R32(LASTF) == 5, "LAST follows FIRST"
    W32(AFTF, 50); h.fire(2); assert R32(AFTF) == 8, "AFTER within the sequence"
    W32(CURSEQ, 0); h.fire(2); doit(); assert msg() == "NO SEQUENCE" and lab(R1, 16) == "NO SEQUENCE     ", msg()
    W32(CURSEQ, P); h.fire(3)
    print("  ok copy of 500 bars after 500 -> TOO MANY BARS (nothing changed); LAST >= FIRST; AFTER <= bars; no sequence")
    # --- wheel by digit + slider ---
    page(P_LEN)
    W32(pg.page + 0x24, WT(NEWF)); h.fire(2); W32(NEWF, R32(NEWF) + 1); h.fire(2); assert R32(NEWF) == 9, R32(NEWF)
    W32(DIG, 1); W32(NEWF, R32(NEWF) + 1); h.fire(2); assert R32(NEWF) == 19, R32(NEWF)
    W32(DIG, 2); W32(NEWF, R32(NEWF) - 1); h.fire(2); assert R32(NEWF) == 1, ("below 1: clamped", R32(NEWF))
    # page left, slider moved on another page, page entered again: the value stays until the slider catches it (user 2026-11-08)
    def sl(k): lo_, hi_, n_ = R32(SLO), R32(SHI), R32(NSTEP); W32(SLIDER, lo_ + round(k * (hi_ - lo_) / n_)); h.fire(60)
    W32(DIG, 0); W32(NEWF, 200); h.fire(3); sl(100); sl(400); v_ = R32(NEWF); assert 380 < v_ < 420, ("slider follows (coarse: 999 values on the slider)", v_)
    assert R32(CAUGHT) == 1, "caught"
    W32(EVQ + 0x68, 0x88123456); h.fire(3); W32(SLIDER, R32(SHI)); h.fire(3); W32(EVQ + 0x68, pg.page); h.fire(60)
    assert R32(NEWF) != 1 + R32(NSTEP), ("re-entry: not at the slider's top", R32(NEWF)); assert R32(NEWF) == R32(CUR) + 1, (R32(NEWF), R32(CUR))
    print("  ok re-entering with the slider elsewhere keeps the value (catch-up again)")
    print("  ok wheel on NEW BARS: ones, tens (9 -> 19), hundreds (clamped to 1)")
    # --- MODE + pad 10 -> this page; ENTER DEFAULT ---
    def screen(s):
        for i, c in enumerate(s.ljust(32).encode()): e.wr(LCD_SHADOW + i, c, 1)
    SQE, OTH = 0x88498DB0, 0x88491000
    page(P_ENTER); doit(); assert pushes() == [] and msg() == "PRESS MODE+PAD10", msg()
    screen("  select mode"); W32(EVQ + 0x68, MODEPG); h.fire(2)
    W32(LOG, 0); W32(EVQ + 0x68, OTH); h.fire(2); screen("Eff Send       A  A9:Off"); h.fire(WATCH + 3); assert pushes() == []
    W32(EVQ + 0x68, MODEPG); screen("  select mode"); h.fire(2); W32(EVQ + 0x68, SQE); h.fire(2); assert pushes() == []
    screen("Insert bars     Edit Sq:01"); h.fire(1)
    assert pushes() == [(0, 2), (pg.page, 0)] and R32(SEQPG) == SQE, (pushes(), hex(R32(SEQPG)))
    W32(EVQ + 0x68, pg.page); h.fire(2)
    W32(EVQ + 0x68, MODEPG); h.fire(2); W32(LOG, 0); W32(EVQ + 0x68, SQE); h.fire(1); assert pushes() == [(0, 2), (pg.page, 0)]
    W32(EVQ + 0x68, pg.page); h.fire(2); page(P_ENTER); assert lab(R1, 16) == "MPC SEQ EDIT    "
    doit(); assert pushes() == [(SQE, 0)], pushes()
    W32(LOG, 0); W32(EVQ + 0x68, SQE); h.fire(5); assert pushes() == [], "opened from here: stays"
    print("  ok MODE+pad10: the page from MODE showing a Seq Edit function -> back + this page; ENTER DEFAULT opens it")
    print("OK: SEQ EDIT -> %s" % os.path.basename(out))


if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', '..', 'SEQEDIT.WAV'); pg.build_wav(out); selftest(out)