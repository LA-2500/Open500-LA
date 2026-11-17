#!/usr/bin/env python3
"""TC 3000 - MPC3000 timing correct on the MPC500 -> TC3000.WAV. While it is loaded:
  * the TC button opens this TC page instead of the OS one:   "TC:1/16  SW:66%"  /  "DO IT = EDIT"
    TC = the OS Timing Correct (1/8 1/8T 1/16 1/16T 1/32 1/32T OFF), SW = the OS Swing% in the MPC3000 steps
    50 54 58 62 66 71 75. Both are the OS's own settings (parameters 0x10019 / 0x1001A), not copies.
  * notes RECORDED while it is loaded land on MPC3000 swing positions: the OS snaps them (MPC500 rule), then the
    add-on moves each NEW swung note to the MPC3000 tick once the playhead has passed it (old notes never touched).
  * Note Repeat swings like an MPC3000 (two small patches in the OS code, inside the OS code itself: nothing calls
    into the add-on, so they are harmless if the add-on goes away; power off = original OS).
  * DO IT on the TC page -> EDIT page for existing notes:  "BAR  1-  4 TK  1"  /  "LATER    +2  ALL"
    bars from-to (opens on the whole sequence), track, SHIFT (EARLIER -24 .. NO SHIFT .. LATER +24 ticks), pads: ALL,
    or the pads you hit (each hit adds the pad, hitting it again takes it out; " A05" one pad, " 3PD" three pads; no
    pad = ALL; every time the page opens = ALL). DO IT: every note of the track in the bars gets the TC/SW of the TC page (MPC3000; TC OFF = untouched) and
    then the shift (wrapping round the sequence). CANCEL goes back to the TC page.
  * v3.0 (LA v3.0, 2026-11-13): PARAMS on the TC page or the EDIT page -> SWING TYPE page:  "SWING TYPE"  /  "MPC3000  96 PPQN"
    or "SP-1200  24 PPQN" (wheel). PARAMS, DO IT or CANCEL go back to the page it came from (EDIT keeps its bars/pads).
    Default MPC3000 (every time the add-on loads). SP-1200 (from the user's SP-1200 grooves + MPC 600 S manual ch. 4):
    - the same swing placement as MPC3000 (1/16: 24 26 28 30 32 34 ticks, measured within 0.15 tick), but only the SP's
      six settings 50 54 58 63 67 71 (63 = 62.5%, 67 = 66.7%: the same ticks as MPC 62 / 66); no 75%.
    - TC OFF: every recorded note lands on the 24 PPQN grid (4 ticks), as the SP's own sequencer records; DO IT on the
      EDIT page with TC OFF snaps the notes to that grid too (MPC3000 mode: TC OFF = untouched, as before).
    Switching type moves the OS Swing% to the nearest step of the new type (75 -> 71, 62 <-> 63, 66 <-> 67: same timing).
    The TC page shows "SP" at the end of row 2 in SP-1200 mode.

MPC3000 swing (96 ticks per quarter; swung note = odd grid step, offset after it):
    1/16: 0 2 4 6 8 10 12   1/8: 0 4 8 12 16 20 24   1/32: 0 1 2 3 4 5 6     (SW 50 54 58 62 66 71 75)
    = ((SW - 48) >> 2) << log2(grid / 12): the 1/32 offset rounded to the nearest tick, doubled per grid size (gives
    the user's MPC3000 data: 66% = exact 2:1 at 1/16 and 1/8). MPC500 (OS 0x884B94C0): trunc((SW-50) * grid / 50).
    Snap: step = (tick + grid/2 - 1) / grid (OS rule), tick = step*grid (+ offset on odd steps, straight grids only).

OS addresses (MPC500 OS 1.31, firmware read 2026-11-02, see memory mpc500-swing):
  TC page object 0x8849BA24 (pushed by the TC key handler 0x884E5126); push_page 0x884E718E mode 2 = back (mpcmod).
  System parameters 0x884E5CD0: msg {u16 type 0x10 get / 0x11 set, u16 0, s32 value, 0, u32 id}; group 1 index - 3 =
  case: 0x10019 TC 0..6 = 48/32/24/16/12/8/1 ticks, 0x1001A Swing 50..75 (Grid uses this for the tempo).
  Sequence: README / Seq Tools (*(u32*)0x8847CBC8, events 16 B from +0x1C10 {tick, track, 0x90, note, vel, dur},
  END 0x7FFFFFFF, bar table +0x30, bars u16 +0x1C), PLAYPTR 0x8847CBD0 = next event to play. Pad note: program +
  0x1ED8 + pad (Grid). Note Repeat swing: 0x884B9560 (grid r14 -> r6) and 0x884BA99A (grid r13 -> r14).
"""
import sys, os, struct
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import PageAddon, ld, st, sti
from mpcmod import EVQ, OS_PUSH_PAGE
from shasm import SHAsm

SYS_PARAM, ID_TC, ID_SW = 0x884E5CD0, 0x10019, 0x1001A
OS_TCPAGE = 0x8849BA24
CURSEQ, PLAYPTR = 0x8847CBC8, 0x8847CBD0
EV_OFF, BARS_OFF, BART_OFF, END = 0x1C10, 0x1C, 0x30, 0x7FFFFFFF
ARENA_LO, ARENA_HI = 0x881DB59C, 0x881DB59C + 0x1A0000
PROG_BASE, PROG_IDX, PROG_STRIDE, NOTE_A = 0x8837B5AC, 0x8847C0E2, 0x1F98, 0x1ED8
BANKOBJ = 0x8849C98C
TICKS = [48, 32, 24, 16, 12, 8, 1]
TC_OPTS = ["1/8  ", "1/8T ", "1/16 ", "1/16T", "1/32 ", "1/32T", "OFF  "]
SWSTEPS = [50, 54, 58, 62, 66, 71, 75]
SWSTEPS_SP = [50, 54, 58, 63, 67, 71, 71]                    # SP-1200: six settings (the 7th entry is never kept: -> 71 = #5)
SW_TH = ([54, 58, 62, 66, 71, 75], [54, 58, 62, 66, 70, 127])   # OS Swing% >= TH[i-1] -> step i (MPC3000, SP-1200)
SP_GRID, K_PARAMS = 4, 0x15                                   # SP-1200 TC OFF: 24 PPQN = 4 ticks; PARAMS key code (printfx, HW)
def _swstr(steps): return b''.join(b'%d%%\0' % s for s in steps)
assert len(_swstr(SWSTEPS)) == len(_swstr(SWSTEPS_SP)) == 28
SHIFT_OPTS = ["EARLIER %3d" % (i - 24) if i < 24 else ("NO SHIFT   " if i == 24 else "LATER   +%2d" % (i - 24)) for i in range(49)]
NS, BEHIND, NPEND = 40, 8, 8         # snapshot: 8 events behind the playhead + 32 ahead; pending new notes
# Note Repeat patch sites: (address, original words, new code(asm) )
def _site(addr, grid_r, out_r):
    s = SHAsm(addr)
    s.add_imm(-48, 0); s.shlr2(0); s.mov_rm_rn(grid_r, 6); s.shlr2(6); s.shlr2(6); s.mov_rm_rn(6, 1); s.shlr(1)
    s.sub(1, 6); s.shad(6, 0); s.mov_rm_rn(0, out_r)
    return s
NR1_ORIG = [0x70ce, 0x0e07, 0x061a, 0x326d, 0xe2fc, 0x060a, 0x462c, 0x6063, 0x4004, 0xc901, 0x360c]
NR2_ORIG = [0x70ce, 0x0d07, 0x061a, 0x326d, 0x0e0a, 0x4e3c, 0x60e3, 0x4004, 0xc901, 0x3e0c]
def _words(s, n):
    b = s.assemble(); w = [struct.unpack_from('<H', b, i)[0] for i in range(0, len(b), 2)]
    return (w + [0x0009] * n)[:n]
NR1_NEW = _words(_site(0x884B9560, 14, 6), 11)
NR2_NEW = _words(_site(0x884BA99A, 13, 14), 10)
NR_SITES = [(0x884B9560, NR1_ORIG, NR1_NEW), (0x884BA99A, NR2_ORIG, NR2_NEW)]

pg = PageAddon("tc3000", open_press=None)
pg.other_menu("TC 3000")
pg.far_hend = True
pg.far_fire = True
TCI = pg.choice("TC:", 0, 0, TC_OPTS, default=2)
SWI = pg.choice(" SW:", 8, 0, ["%d%%" % s for s in SWSTEPS], default=0)
pg.label("DO IT = EDIT", 0, 1)
MODEL = pg.dlabel(14, 1, 2)                                   # "SP" in SP-1200 mode, blank in MPC3000 mode
pe = pg.add_page("edit")
pe.label("BAR", 0, 0)
BF = pe.value("", 3, 0, fmt="  0", lo=1, hi=999, default=1)
BT = pe.value("-", 6, 0, fmt="  0", lo=1, hi=999, default=4)
TRK = pe.choice(" TK", 10, 0, ["  A"] + ["%3d" % i for i in range(1, 65)], default=1)   # A = all tracks   # 0 = all tracks
SHI = pe.choice("", 0, 1, SHIFT_OPTS, default=24)
PADL = pe.dlabel(12, 1, 4)
pe.message()
ps = pg.add_page("swing")                                     # v3.0: SWING TYPE (PARAMS on the TC / EDIT page)
ps.label("SWING TYPE", 0, 0)
STY = ps.choice("", 0, 1, ["MPC3000  96 PPQN", "SP-1200  24 PPQN"], default=0)   # 0 = MPC3000, 1 = SP-1200
(LMODE, RET, EKEEP, LSEQ) = [pg.alloc_slot() for _ in range(4)]    # mode applied; page PARAMS came from; EDIT: keep its fields
(ACT, LTCI, LSWI, GTI, GSW, G, OFF, PP0, SNN, SNLO, SNHI, PN, WRAP, LASTTRK, EACT, PADSEL, CNT, PTK) = \
    [pg.alloc_slot() for _ in range(18)]
MSGB = [pg.alloc_slot() for _ in range(4)]
SNB = pg.alloc_buf(NS * 8)
PND = pg.alloc_buf(NPEND * 12)
PSB = pg.alloc_buf(48)              # EDIT: 1 byte per pad A1..D12, 1 = chosen (none = ALL)
NTB = pg.alloc_buf(128)             # DO IT: 1 byte per note, 1 = note of a chosen pad
(LTRK, NSTEP, CUR, CAUGHT, SIDE, SS, SF, SINIT, SLO, SHH, SREF) = [pg.alloc_slot() for _ in range(11)]
# (SHH, not SHI: SHI is the SHIFT field; the clash made DO IT shift by the slider's range byte - HW bug 2026-11-03)
assert len({SHI, SHH, SLO, SS, SF, CUR, TRK, BF, BT}) == 9
SLIDER = 0x8848C3E4                 # Q-Link slider (Song, Program, Track)
SL_LO, SL_HI, HYST, MAXTRK = 64, 960, 3, 48                                 # slider: tracks 1..48 (A: wheel only)
pg.defaults[SLO] = SL_LO; pg.defaults[SHH] = SL_HI; pg.defaults[SS] = 0xFF; pg.defaults[NSTEP] = MAXTRK - 1
pg.defaults[CUR] = 0xFFFF

def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()
def jmpl(m, lbl): m.a.movl_pool(lbl, 2); m.a.jmp(2); m.a.nop()
def rdb(m, addr, r): m.imm32(addr, r); m.a.movb_ld(r, r); m.a.extu_b(r, r)
def fidp(p, slot): return [it[1] for it in p.items if it[0] in ('choice', 'value') and it[-1] == slot][0]   # (printfx)

# ---- v3.0: PARAMS -> SWING TYPE page, and back ----
def params_key(frm, keep):
    """PARAMS on page 'frm' opens SWING TYPE (remembers 'frm'); other keys fall through to CANCEL / DO IT (r9 kept)"""
    def fn(m, hret0, hret1):
        a = m.a; kx = m.newlabel('kx')
        a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 10); a.extu_w(10, 10)
        a.mov_imm(K_PARAMS, 0); a.cmpeq(0, 10); m.bf_far(kx)
        m.imm32(frm.addr['page'], 0); st(m, 0, RET)
        if keep: sti(m, 1, EKEEP)
        m.imm32(ps.addr['page'], 5); call(m, '__tcgo0'); jmpl(m, hret1)
        m.pool_jump()
        a.deflabel(kx)
    return fn
def go_back(m):
    """back to the page PARAMS came from (TC page if none)"""
    a = m.a; ok = m.newlabel('ok')
    ld(m, RET, 5); a.tst_rr(5, 5); a.bf_lbl(ok); m.imm32(pg.addr['page'], 5); a.deflabel(ok)
    call(m, '__tcgo0')
def s_key(m, hret0, hret1):
    a = m.a; kx = m.newlabel('kx')
    a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 10); a.extu_w(10, 10)
    a.mov_imm(K_PARAMS, 0); a.cmpeq(0, 10); m.bf_far(kx)
    go_back(m); jmpl(m, hret1)
    m.pool_jump()
    a.deflabel(kx)
def s_cancel(m, hret1):
    go_back(m); jmpl(m, hret1); m.pool_jump()
pg.on_event(2, params_key(pg, False))
pe.on_event(2, params_key(pe, True))
ps.on_event(2, s_key)
ps.cancel_fn = s_cancel
ps.on_doit(go_back)

def emit_snap(m):
    """'__tcsnap0': r4 = tick, r5 = grid, r6 = swing offset -> r0 = MPC3000 position. r0-r7"""
    a = m.a
    a.deflabel('__tcsnap0'); a.stsl_pr()
    rt = m.newlabel('rt'); ev = m.newlabel('ev')
    a.mov_imm(1, 0); a.cmpeq(5, 0); a.bt_lbl(rt)                                           # TC OFF: as it is
    a.movl_st_dec(4, 15); a.movl_st_dec(5, 15); a.movl_st_dec(6, 15)
    a.mov_rm_rn(5, 0); a.shlr(0); a.add_rr(0, 4); a.add_imm(-1, 4); pg._hcall(m, '__udiv')   # r0 = step
    a.movl_ld_inc(15, 6); a.movl_ld_inc(15, 5); a.movl_ld_inc(15, 4)
    a.mul_l(5, 0); a.sts_macl(2); a.tst_imm(1); a.bt_lbl(ev); a.add_rr(6, 2); a.deflabel(ev)
    a.mov_rm_rn(2, 0); a.ldsl_pr(); a.rts(); a.nop()
    a.deflabel(rt); a.mov_rm_rn(4, 0); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

def emit_off(m, rg, rs, rd):
    """rd = MPC3000 swing offset for grid rg, swing rs (straight grids 48/24/12, else 0). r0, r1, rd"""
    a = m.a
    z, x = m.newlabel('oz'), m.newlabel('ox')
    a.mov_rm_rn(rg, 0)
    ok = m.newlabel('ook')
    a.mov_imm(48, 1); a.cmpeq(1, 0); a.bt_lbl(ok); a.mov_imm(24, 1); a.cmpeq(1, 0); a.bt_lbl(ok)
    a.mov_imm(12, 1); a.cmpeq(1, 0); a.bf_lbl(z)
    a.deflabel(ok)
    a.mov_rm_rn(rs, rd); a.add_imm(-48, rd); a.shlr2(rd)                                  # 1/32 offset
    a.shlr2(0); a.shlr2(0); a.mov_rm_rn(0, 1); a.shlr(1); a.sub(1, 0); a.shad(0, rd)       # << log2(grid/12)
    a.bra_lbl(x); a.nop()
    a.deflabel(z); a.mov_imm(0, rd)
    a.deflabel(x)

def fire(m):
    a = m.a
    call(m, '__tcbg0')
    # TC page: on entry the fields show the OS settings; a field changed with the wheel goes to the OS
    ld(m, ACT, 0); a.tst_rr(0, 0); ent = m.newlabel('ent'); m.bf_far(ent)
    ld(m, GTI, 0); m.imm32(TCI, 1); a.movb_st(0, 1); st(m, 0, LTCI)
    sti(m, 0, EKEEP)
    ld(m, GSW, 4); call(m, '__tcsidx0')
    m.imm32(SWI, 1); a.mov_rm_rn(5, 0); a.movb_st(0, 1); st(m, 5, LSWI); sti(m, 1, ACT)
    m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(ent)
    rdb(m, TCI, 9); ld(m, LTCI, 0); a.cmpeq(9, 0); nt = m.newlabel('nt'); m.bt_far(nt)
    st(m, 9, LTCI); m.imm32(ID_TC, 4); a.mov_rm_rn(9, 5); call(m, '__tcpset0')
    a.deflabel(nt)
    rdb(m, SWI, 9)
    # SP-1200: six steps; the wheel past 71% (the 7th entry, also "71%") goes back to 71% = step 5
    nc7 = m.newlabel('nc7'); ld(m, LMODE, 0); a.tst_rr(0, 0); m.bt_far(nc7); a.mov_imm(6, 0); a.cmpeq(0, 9); m.bf_far(nc7)
    a.mov_imm(5, 9); m.imm32(SWI, 1); a.mov_rm_rn(9, 0); a.movb_st(0, 1); m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(nc7)
    ld(m, LSWI, 0); a.cmpeq(9, 0); ns_ = m.newlabel('ns'); m.bt_far(ns_)
    st(m, 9, LSWI); call(m, '__tcswt0'); a.mov_rm_rn(9, 0); a.shll2(0); a.add_rr(0, 2); a.movl_ld(2, 5)
    m.imm32(ID_SW, 4); call(m, '__tcpset0')
    a.deflabel(ns_)
    jmpl(m, '__tc_end0'); m.pool_jump()
    subs(m)
    a.deflabel('__tc_end0')
pg.on_fire(fire)

def inactive(m):
    sti(m, 0, ACT); call(m, '__tcbg0')
pg.on_inactive(inactive)

def doit(m):
    m.imm32(EVQ, 4); m.imm32(pe.addr['page'], 5); m.a.mov_imm(0, 6); m.imm32(OS_PUSH_PAGE, 2); m.a.jsr(2); m.a.nop()
pg.on_doit(doit)

def init(m):
    """Note Repeat patches (only if the OS code is exactly the expected one)"""
    a = m.a
    for addr, orig, new in NR_SITES:
        skip = m.newlabel('np')
        for i, w in enumerate(orig):
            m.imm32(addr + 2 * i, 1); a.movw_ld(1, 0); a.extu_w(0, 0); m.imm32(w, 2); a.cmpeq(2, 0); m.bf_far(skip)
        for i, w in enumerate(new):
            m.imm32(addr + 2 * i, 1); m.imm32(w, 0); a.movw_st(0, 1)
        a.deflabel(skip); m.pool_jump()
pg.init_fn = init

def subs(m):
    a = m.a
    # ---- __tcpget0: r4 = id -> r0 = value; __tcpset0: r4 = id, r5 = value. Through the OS parameter manager ----
    for nm, typ in (('__tcpget0', 0x10), ('__tcpset0', 0x11)):
        a.deflabel(nm); a.stsl_pr()
        m.imm32(MSGB[0], 1); m.imm32(typ, 0); a.movl_st(0, 1)
        if typ == 0x11: a.movl_st_disp(5, 4, 1)
        else: a.mov_imm(0, 0); a.movl_st_disp(0, 4, 1)
        a.mov_imm(0, 0); a.movl_st_disp(0, 8, 1); a.movl_st_disp(4, 12, 1)
        a.mov_rm_rn(1, 4); m.imm32(SYS_PARAM, 2); a.jsr(2); a.nop()
        m.imm32(MSGB[1], 1); a.movl_ld(1, 0)
        a.ldsl_pr(); a.rts(); a.nop()
        m.pool_jump()

    # ---- __tcbg0 (every fire, any page): settings, TC button -> our page, new recorded notes -> MPC3000 ----
    a.deflabel('__tcbg0'); a.stsl_pr()
    # the OS TC page just opened (TC button): back, then ours
    m.imm32(EVQ + 0x68, 1); a.movl_ld(1, 0); m.imm32(OS_TCPAGE, 1); a.cmpeq(1, 0); nsw = m.newlabel('nsw'); m.bf_far(nsw)
    m.imm32(EVQ, 4); a.mov_imm(0, 5); a.mov_imm(2, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()
    m.imm32(EVQ, 4); m.imm32(pg.addr['page'], 5); a.mov_imm(0, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()
    a.deflabel(nsw)
    m.imm32(ID_TC, 4); call(m, '__tcpget0'); a.mov_rm_rn(0, 9)
    a.mov_imm(6, 1); a.cmphi(1, 9); ti = m.newlabel('ti'); a.bf_lbl(ti); a.mov_imm(6, 9); a.deflabel(ti)   # 0..6
    st(m, 9, GTI)
    a.movl_pool('tc_ticks', 2); a.mov_rm_rn(9, 0); a.shll2(0); a.add_rr(0, 2); a.movl_ld(2, 10); st(m, 10, G)   # r10 = grid
    m.imm32(ID_SW, 4); call(m, '__tcpget0'); a.mov_rm_rn(0, 11)
    a.mov_imm(50, 1); c1 = m.newlabel('c1'); a.cmpge(1, 11); a.bt_lbl(c1); a.mov_imm(50, 11); a.deflabel(c1)
    a.mov_imm(75, 1); c2 = m.newlabel('c2'); a.cmpgt(1, 11); a.bf_lbl(c2); a.mov_imm(75, 11); a.deflabel(c2)
    st(m, 11, GSW)
    call(m, '__tcmode0')                                                                 # v3.0: swing type changed?
    # SP-1200 with TC OFF: the 24 PPQN grid (4 ticks)
    sg_ = m.newlabel('sg'); ld(m, LMODE, 0); a.tst_rr(0, 0); a.bt_lbl(sg_); a.mov_imm(1, 0); a.cmpeq(0, 10); a.bf_lbl(sg_)
    a.mov_imm(SP_GRID, 10); a.deflabel(sg_); st(m, 10, G)
    emit_off(m, 10, 11, 12); st(m, 12, OFF)
    # notes to fix: straight grids with SW > 50, or SP-1200 TC OFF (24 PPQN grid)
    fx, fgo = m.newlabel('fx'), m.newlabel('fgo')
    a.tst_rr(12, 12); a.bf_lbl(fgo); a.mov_imm(SP_GRID, 0); a.cmpeq(0, 10); m.bf_far(fx)
    a.deflabel(fgo)
    call(m, '__tcfix0')
    a.deflabel(fx)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- __tcfix0: snapshot of the events around the playhead; a note that appears there = recorded now -> pending;
    #      a pending note once behind the playhead (or after a wrap) -> MPC3000 tick, in place (order kept) ----
    a.deflabel('__tcfix0'); a.stsl_pr()
    bad, ret = m.newlabel('bad'), m.newlabel('ret')
    m.imm32(CURSEQ, 1); a.movl_ld(1, 8)                                                  # r8 = sequence
    m.imm32(ARENA_LO, 1); a.cmphs(1, 8); m.bf_far(bad); m.imm32(ARENA_HI, 1); a.cmphs(1, 8); m.bt_far(bad)
    # v3.0: another sequence = start over (its notes are not new recordings; SP-1200 TC OFF fixes on every grid)
    ld(m, LSEQ, 0); a.cmpeq(8, 0); sq = m.newlabel('sq'); a.bt_lbl(sq); st(m, 8, LSEQ); jmpl(m, bad)
    a.deflabel(sq)
    m.imm32(EV_OFF, 9); a.add_rr(8, 9)                                                    # r9 = first event
    m.imm32(PLAYPTR, 1); a.movl_ld(1, 10)                                                 # r10 = playhead
    a.cmphs(9, 10); m.bf_far(bad)
    # wrap (playhead moved back): every pending note may be fixed now
    ld(m, PP0, 0); a.cmphi(10, 0); nwr = m.newlabel('nwr'); m.bf_far(nwr)
    m.imm32(PND + 8, 2); ld(m, PN, 3); a.mov_imm(1, 0)
    wl, wx = m.newlabel('wl'), m.newlabel('wx'); a.deflabel(wl); a.tst_rr(3, 3); a.bt_lbl(wx); a.movl_st(0, 2); a.add_imm(12, 2); a.add_imm(-1, 3); a.bra_lbl(wl); a.nop()
    a.deflabel(wx); sti(m, 1, WRAP); wd = m.newlabel('wd'); a.bra_lbl(wd); a.nop()
    a.deflabel(nwr); sti(m, 0, WRAP)
    a.deflabel(wd); st(m, 10, PP0)
    # r11 = window start (8 events behind, not before the first), r13 = 1 if clipped at the start
    a.mov_rm_rn(10, 11); m.imm32(16 * BEHIND, 0); a.sub(0, 11); a.mov_imm(0, 13)
    cl = m.newlabel('cl'); a.cmphs(9, 11); a.bt_lbl(cl); a.mov_rm_rn(9, 11); a.mov_imm(1, 13); a.deflabel(cl)
    # detect
    nod = m.newlabel('nod')
    ld(m, SNN, 0); a.tst_rr(0, 0); m.bt_far(nod); ld(m, WRAP, 0); a.tst_rr(0, 0); m.bf_far(nod)
    m.imm32(SNB, 12); ld(m, SNN, 7); a.shll2(7); a.shll(7); a.add_rr(12, 7)               # r12 = i, r7 = end
    a.mov_rm_rn(11, 6); m.imm32(NS, 5)                                                    # r6 = j, r5 = count
    dl, nj = m.newlabel('dl'), m.newlabel('nj')
    a.deflabel(dl)
    a.movl_ld(6, 0); m.imm32(END, 1); a.cmpeq(1, 0); m.bt_far(nod)
    ld(m, SNLO, 1); a.cmpgt(1, 0); m.bf_far(nj)                                            # t > SNLO
    ld(m, SNHI, 1); a.cmpgt(0, 1); m.bf_far(nj)                                            # t < SNHI
    av, avx = m.newlabel('av'), m.newlabel('avx'); a.deflabel(av)
    a.cmphs(7, 12); a.bt_lbl(avx); a.movl_ld(12, 1); a.cmpge(0, 1); a.bt_lbl(avx); a.add_imm(8, 12); a.bra_lbl(av); a.nop()
    a.deflabel(avx)
    a.mov_rm_rn(12, 3); a.movl_ld_disp(6, 4, 4)                                           # r4 = key
    sg, nf, fnd = m.newlabel('sg'), m.newlabel('nf'), m.newlabel('fnd')
    a.deflabel(sg); a.cmphs(7, 3); a.bt_lbl(nf); a.movl_ld(3, 1); a.cmpeq(0, 1); a.bf_lbl(nf)
    a.movl_ld_disp(3, 4, 1); a.cmpeq(4, 1); m.bt_far(nj); a.add_imm(8, 3); a.bra_lbl(sg); a.nop()
    a.deflabel(nf)                                                                         # new: a note? room?
    a.mov_rm_rn(6, 1); a.add_imm(5, 1); a.movb_ld(1, 1); a.extu_b(1, 1); m.imm32(0x90, 2); a.cmpeq(2, 1); m.bf_far(nj)
    ld(m, PN, 1); a.mov_imm(NPEND, 2); a.cmpge(2, 1); m.bt_far(nj)
    a.mov_rm_rn(1, 2); a.shll2(2); a.mov_rm_rn(2, 3); a.shll(3); a.add_rr(3, 2); m.imm32(PND, 3); a.add_rr(3, 2)   # 12*PN
    a.movl_st(0, 2); a.movl_st_disp(4, 4, 2); a.mov_imm(0, 3); a.movl_st_disp(3, 8, 2)
    a.add_imm(1, 1); a.mov_rm_rn(1, 2); st(m, 2, PN)
    a.mov_rm_rn(6, 2); a.add_imm(4, 2); a.movb_ld(2, 2); a.extu_b(2, 2); st(m, 2, LASTTRK)
    a.deflabel(nj); a.add_imm(16, 6); a.dt(5); m.bf_far(dl)
    m.pool_jump()
    a.deflabel(nod)
    # new snapshot of the window
    a.mov_rm_rn(11, 6); m.imm32(SNB, 12); m.imm32(NS, 5); a.mov_imm(0, 4); a.mov_imm(0, 3)
    rl, rx, re_ = m.newlabel('rl'), m.newlabel('rx'), m.newlabel('re')
    a.deflabel(rl); a.movl_ld(6, 0); m.imm32(END, 1); a.cmpeq(1, 0); a.bt_lbl(re_)
    a.movl_st(0, 12); a.movl_ld_disp(6, 4, 1); a.movl_st_disp(1, 4, 12); a.add_imm(8, 12); a.add_imm(1, 4); a.add_imm(16, 6)
    a.dt(5); a.bf_lbl(rl); a.bra_lbl(rx); a.nop()
    a.deflabel(re_); a.mov_imm(1, 3)
    a.deflabel(rx); st(m, 4, SNN)
    lo1, lo2 = m.newlabel('lo1'), m.newlabel('lo2')
    a.mov_imm(-1, 0); a.tst_rr(13, 13); a.bf_lbl(lo1); m.imm32(SNB, 1); a.movl_ld(1, 0); a.deflabel(lo1); st(m, 0, SNLO)
    m.imm32(END, 0); a.tst_rr(3, 3); a.bf_lbl(lo2)
    a.mov_rm_rn(12, 1); a.add_imm(-8, 1); a.movl_ld(1, 0)
    a.deflabel(lo2); st(m, 0, SNHI)
    m.pool_jump()
    # pending: drop the ones not in the window; fix the fixable one with the highest address (chords: one per fire)
    a.mov_imm(0, 13); a.mov_imm(0, 12)                                                    # r13 = best entry, r12 = its event
    m.imm32(PND, 7); ld(m, PN, 14)
    pl, pnx, pdone = m.newlabel('pl'), m.newlabel('pnx'), m.newlabel('pdone')
    a.deflabel(pl); a.tst_rr(14, 14); m.bt_far(pdone)
    a.movl_ld(7, 0); a.movl_ld_disp(7, 4, 4)                                              # r0 = tick, r4 = key
    a.mov_rm_rn(11, 6); m.imm32(NS, 5); a.mov_imm(0, 3)
    fl, fz, fe = m.newlabel('fl'), m.newlabel('fz'), m.newlabel('fe')
    a.deflabel(fl); a.movl_ld(6, 1); a.cmpeq(0, 1); a.bf_lbl(fz); a.movl_ld_disp(6, 4, 1); a.cmpeq(4, 1); a.bf_lbl(fz)
    a.mov_rm_rn(6, 3); a.bra_lbl(fe); a.nop()
    a.deflabel(fz); m.imm32(END, 2); a.movl_ld(6, 1); a.cmpeq(2, 1); a.bt_lbl(fe); a.add_imm(16, 6); a.dt(5); a.bf_lbl(fl)
    a.deflabel(fe)
    rm = m.newlabel('rm')
    a.tst_rr(3, 3); m.bt_far(rm)                                                           # gone: drop it
    a.cmphi(3, 10); fk = m.newlabel('fk'); a.bt_lbl(fk)                                     # behind the playhead
    a.movl_ld_disp(7, 8, 0); a.tst_rr(0, 0); m.bt_far(pnx)                                  # or after a wrap
    a.deflabel(fk); a.cmphi(12, 3); m.bf_far(pnx); a.mov_rm_rn(3, 12); a.mov_rm_rn(7, 13); jmpl(m, pnx)
    m.pool_jump()
    a.deflabel(rm)                                                                          # remove entry r7: last -> here
    ld(m, PN, 0); a.add_imm(-1, 0); st(m, 0, PN); a.mov_rm_rn(0, 1); a.shll2(1); a.mov_rm_rn(1, 2); a.shll(2); a.add_rr(2, 1)
    m.imm32(PND, 2); a.add_rr(2, 1)
    for o in (0, 4, 8): a.movl_ld_disp(1, o, 2); a.movl_st_disp(2, o, 7)
    a.add_imm(-1, 14); jmpl(m, pl)
    a.deflabel(pnx); a.add_imm(12, 7); a.add_imm(-1, 14); jmpl(m, pl)
    m.pool_jump()
    a.deflabel(pdone)
    a.tst_rr(13, 13); m.bt_far(ret)
    # fix the event r12 (entry r13): MPC3000 tick, only if the order stays (else it keeps the OS tick)
    a.movl_ld(12, 4); ld(m, G, 5); ld(m, OFF, 6); call(m, '__tcsnap0'); a.mov_rm_rn(0, 6)     # r6 = new tick
    a.movl_ld(12, 4); a.cmpeq(4, 6); dn = m.newlabel('dn'); m.bt_far(dn)
    a.mov_rm_rn(8, 1); a.add_imm(BART_OFF, 1); a.mov_rm_rn(8, 2); a.add_imm(BARS_OFF, 2); a.movw_ld(2, 2); a.extu_w(2, 2)
    a.shll2(2); a.add_rr(2, 1); a.movl_ld(1, 1); m.imm32(0xFFFFFF, 2); a.and_rr(2, 1)          # r1 = length
    a.cmpgt(6, 1); m.bf_far(dn)                                                            # past the end: leave it
    a.cmpgt(4, 6); lt_ = m.newlabel('lt'); m.bf_far(lt_)
    a.movl_ld_disp(12, 16, 1); a.cmpge(6, 1); m.bf_far(dn); jmpl(m, 'tc_wr')                 # later: next >= new
    a.deflabel(lt_)
    a.cmpeq(9, 12); m.bt_far('tc_wr'); a.mov_rm_rn(12, 1); a.add_imm(-16, 1); a.movl_ld(1, 1); a.cmpge(1, 6); m.bf_far(dn)
    a.deflabel('tc_wr'); a.movl_st(6, 12)
    a.deflabel(dn)
    a.mov_imm(0, 0); a.movl_st_disp(0, 0, 13); a.movl_st_disp(0, 4, 13)                    # tick 0 key 0: dropped next fire
    jmpl(m, ret)
    m.pool_jump()
    a.deflabel(bad); sti(m, 0, SNN); sti(m, 0, PN); sti(m, 0, PP0)
    a.deflabel(ret); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- v3.0 swing type ----
    # __tcswt0: r2 = Swing% table of the type in use (MPC3000 / SP-1200). r0
    a.deflabel('__tcswt0'); ld(m, LMODE, 0); a.tst_rr(0, 0); a.movl_pool('tc_swt', 2); w0 = m.newlabel('w0'); a.bt_lbl(w0)
    a.movl_pool('__tcswsp1', 2); a.deflabel(w0); a.rts(); a.nop()
    m.pool_jump()
    # __tcsidx0: r4 = OS Swing% -> r5 = step of the type in use (the step at or below; SP 62 -> 63, 66 -> 67). r0
    a.deflabel('__tcsidx0'); a.mov_imm(0, 5); ld(m, LMODE, 0); a.tst_rr(0, 0); spi = m.newlabel('spi'); m.bf_far(spi)
    for mode in (0, 1):
        if mode: a.deflabel(spi)
        for i, s in enumerate(SW_TH[mode], 1):
            n = m.newlabel('sw'); a.mov_imm(s, 0); a.cmpge(0, 4); a.bf_lbl(n); a.mov_imm(i, 5); a.deflabel(n)
        a.rts(); a.nop()
    m.pool_jump()
    # __tcgo0: r5 = page -> opens it IN PLACE of the open one (OS back, then push: the page stack stays as deep; printfx px_go)
    a.deflabel('__tcgo0'); a.stsl_pr(); a.movl_st_dec(5, 15)
    m.imm32(EVQ, 4); a.mov_imm(0, 5); a.mov_imm(2, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()
    a.movl_ld_inc(15, 5); m.imm32(EVQ, 4); a.mov_imm(0, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # __tcmode0 (bg, r11 = OS Swing% in, out): the SWING TYPE field changed -> OS Swing% to the nearest step of the new
    # type, the TC page's SW strings and the "SP" mark. r0-r7, r13 (r10 kept)
    a.deflabel('__tcmode0'); a.stsl_pr()
    mr = m.newlabel('mr')
    rdb(m, STY, 13); a.mov_imm(1, 0); a.cmphi(0, 13); mk = m.newlabel('mk'); a.bf_lbl(mk); a.mov_imm(1, 13); a.deflabel(mk)
    ld(m, LMODE, 0); a.cmpeq(13, 0); m.bt_far(mr)
    st(m, 13, LMODE)
    a.mov_rm_rn(11, 4); call(m, '__tcsidx0')                                              # r5 = step
    m.imm32(SWI, 1); a.mov_rm_rn(5, 0); a.movb_st(0, 1); st(m, 5, LSWI)
    call(m, '__tcswt0'); a.mov_rm_rn(5, 0); a.shll2(0); a.add_rr(0, 2); a.movl_ld(2, 11); st(m, 11, GSW)
    m.imm32(ID_SW, 4); a.mov_rm_rn(11, 5); call(m, '__tcpset0')
    # SW strings (7 x "NN%\0", in the choice's string table: widget +0x24 model, +0x20 desc, +4 strings)
    m.imm32(pg.addr['values'][fidp(pg, SWI)], 1); a.movl_ld_disp(1, 0x24, 1); a.movl_ld_disp(1, 0x20, 1); a.movl_ld_disp(1, 4, 1)
    a.tst_rr(13, 13); a.movl_pool('__tcstr0', 2); s0 = m.newlabel('s0'); a.bt_lbl(s0); a.movl_pool('__tcstr1', 2); a.deflabel(s0)
    a.mov_imm(28, 3); cl_ = m.newlabel('cl')
    a.deflabel(cl_); a.movb_ld(2, 0); a.movb_st(0, 1); a.add_imm(1, 2); a.add_imm(1, 1); a.dt(3); a.bf_lbl(cl_)
    m.imm32(pg.addr['dlabels'][MODEL], 1); a.mov_imm(ord(' '), 0); a.mov_imm(ord(' '), 3); a.tst_rr(13, 13); s1 = m.newlabel('s1')
    a.bt_lbl(s1); a.mov_imm(ord('S'), 0); a.mov_imm(ord('P'), 3); a.deflabel(s1); a.movb_st(0, 1); a.add_imm(1, 1); a.movb_st(3, 1)
    m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(mr); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    emit_snap(m)
    # ---- tables ----
    skip = m.newlabel('tskip'); a.bra_lbl(skip); a.nop()
    while (len(a.words) * 2 + a.base) & 3: a.nop()
    a.deflabel('tc_ticks')
    for w in TICKS: a._emit(w & 0xFFFF); a._emit(w >> 16)
    a.deflabel('tc_swt')
    for w in SWSTEPS: a._emit(w & 0xFFFF); a._emit(w >> 16)
    a.deflabel('__tcswsp1')
    for w in SWSTEPS_SP: a._emit(w & 0xFFFF); a._emit(w >> 16)
    for lbl, steps in (('__tcstr0', SWSTEPS), ('__tcstr1', SWSTEPS_SP)):
        a.deflabel(lbl); bs_ = _swstr(steps)
        for i in range(0, len(bs_), 2): a._emit(bs_[i] | (bs_[i + 1] << 8))
    a.deflabel(skip)


# ===================================== EDIT page =====================================
def e_slider(m):
    """slider = the track 1..48 (bottom = track 1; A only with the wheel), catch-up: it takes over once it passes (or
    sits on) the track shown; on A, as soon as it moves. Inline"""
    a = m.a
    # SS = 0..NSTEP (filtered, learned range, hysteresis, exact ends; as Song / Program / Track)
    m.imm32(SLIDER, 1); a.movl_ld(1, 2); m.imm32(0x3FF, 1); a.and_rr(1, 2); a.shll2(2); a.shll2(2)
    ld(m, SINIT, 0); a.tst_rr(0, 0); g0 = m.newlabel('g0'); a.bf_lbl(g0); st(m, 2, SF); sti(m, 1, SINIT); a.deflabel(g0)
    ld(m, SF, 0); a.sub(0, 2); a.shar(2); a.shar(2); a.shar(2); a.add_rr(2, 0); st(m, 0, SF)
    a.mov_rm_rn(0, 6); a.shar(6); a.shar(6); a.shar(6); a.shar(6)
    ld(m, SLO, 0); c1 = m.newlabel('c1'); a.cmpgt(6, 0); a.bf_lbl(c1); st(m, 6, SLO); a.deflabel(c1)
    ld(m, SHH, 0); c2 = m.newlabel('c2'); a.cmpgt(0, 6); a.bf_lbl(c2); st(m, 6, SHH); a.deflabel(c2)
    ld(m, SLO, 7); a.sub(7, 6); ld(m, SHH, 5); a.sub(7, 5); a.mov_rm_rn(5, 7)
    a.mov_rm_rn(6, 4); ld(m, NSTEP, 0); a.mul_l(0, 4); a.sts_macl(4)
    a.mov_rm_rn(7, 0); a.shlr(0); a.add_rr(0, 4); pe._hcall(m, '__udiv')
    ld(m, NSTEP, 1); c3 = m.newlabel('c3'); a.cmpgt(1, 0); a.bf_lbl(c3); a.mov_rm_rn(1, 0); a.deflabel(c3)
    a.mov_rm_rn(0, 3)
    sx2, setv = m.newlabel('sx'), m.newlabel('set')
    ld(m, SS, 0); a.cmpeq(3, 0); m.bt_far(sx2)
    ld(m, NSTEP, 1); a.cmphi(1, 0); m.bt_far(setv)
    a.tst_rr(3, 3); m.bt_far(setv); ld(m, NSTEP, 1); a.cmpeq(1, 3); m.bt_far(setv)
    a.mov_rm_rn(0, 4); a.mul_l(7, 4); a.sts_macl(4); ld(m, NSTEP, 5)
    a.movl_st_dec(3, 15); pe._hcall(m, '__udiv'); a.movl_ld_inc(15, 3)
    a.sub(0, 6); a.cmppz(6); ab = m.newlabel('ab'); a.bt_lbl(ab); a.neg(6, 6); a.deflabel(ab)
    a.mov_rm_rn(7, 4); ld(m, NSTEP, 5); a.shll(5); a.movl_st_dec(3, 15); a.movl_st_dec(6, 15)
    pe._hcall(m, '__udiv'); a.movl_ld_inc(15, 6); a.movl_ld_inc(15, 3); a.add_imm(HYST, 0)
    a.cmphi(0, 6); m.bf_far(sx2)
    a.deflabel(setv); st(m, 3, SS)
    a.deflabel(sx2)
    m.pool_jump()
    # catch-up
    rdb(m, TRK, 9); a.add_imm(-1, 9); ld(m, CUR, 0); a.cmpeq(9, 0); nrf = m.newlabel('nrf'); m.bt_far(nrf)   # step = track - 1
    st(m, 9, CUR); ld(m, SS, 10); st(m, 10, SREF); a.cmpeq(9, 10); a.movt(0); st(m, 0, CAUGHT)   # changed elsewhere
    a.cmphi(10, 9); a.movt(0); st(m, 0, SIDE)
    a.deflabel(nrf)
    fol, cat, sdone = m.newlabel('fol'), m.newlabel('cat'), m.newlabel('sdone')
    ld(m, CAUGHT, 0); a.tst_rr(0, 0); m.bf_far(fol)
    ld(m, CUR, 9); a.cmppz(9); na = m.newlabel('na'); m.bt_far(na)                         # A (-1): when it moves
    ld(m, SS, 10); ld(m, SREF, 0); a.cmpeq(0, 10); m.bf_far(cat); jmpl(m, sdone)
    a.deflabel(na)
    ld(m, CUR, 9); ld(m, SS, 10); a.cmpeq(9, 10); m.bt_far(cat)
    a.cmphi(10, 9); a.movt(2); ld(m, SIDE, 0); a.cmpeq(2, 0); m.bf_far(cat)
    jmpl(m, sdone); m.pool_jump()
    a.deflabel(cat); sti(m, 1, CAUGHT)
    a.deflabel(fol)
    ld(m, CUR, 9); ld(m, SS, 10); a.cmpeq(9, 10); m.bt_far(sdone)
    st(m, 10, CUR); a.add_imm(1, 10); m.imm32(TRK, 1); a.movb_st(10, 1); m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(sdone)
    m.pool_jump()

def e_fire(m):
    a = m.a
    ld(m, EACT, 0); a.tst_rr(0, 0); ent = m.newlabel('ent'); m.bf_far(ent)
    sti(m, 1, EACT)
    # back from SWING TYPE: bars, track and pads as they were (only the redraw)
    kp = m.newlabel('kp'); ld(m, EKEEP, 0); a.tst_rr(0, 0); m.bt_far(kp); sti(m, 0, EKEEP)
    m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1); jmpl(m, ent)
    m.pool_jump()
    a.deflabel(kp)
    ld(m, LASTTRK, 0); a.tst_rr(0, 0); nl = m.newlabel('nl'); a.bt_lbl(nl); a.mov_imm(64, 1); a.cmphi(1, 0); a.bt_lbl(nl)
    m.imm32(TRK, 1); a.movb_st(0, 1); a.deflabel(nl)
    m.imm32(CURSEQ, 1); a.movl_ld(1, 8); a.mov_rm_rn(8, 1); a.add_imm(BARS_OFF, 1); a.movw_ld(1, 2); a.extu_w(2, 2)
    m.imm32(ARENA_LO, 1); bs = m.newlabel('bs'); a.cmphs(1, 8); a.bf_lbl(bs); a.tst_rr(2, 2); a.bt_lbl(bs)
    st(m, 2, BT); sti(m, 1, BF)                                                            # whole sequence
    a.deflabel(bs)
    m.imm32(PSB, 1); a.mov_imm(0, 0)                                                       # no pads = ALL
    for k in range(0, 48, 4): a.movl_st_disp(0, k, 1)
    rdb(m, TRK, 0); st(m, 0, LTRK); m.imm32(0xFFFF, 0); st(m, 0, CUR)                      # slider: catch again
    m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(ent)
    e_slider(m)
    # the track changed (wheel or slider): pads back to ALL
    rdb(m, TRK, 0); ld(m, LTRK, 1); a.cmpeq(1, 0); tn = m.newlabel('tn'); m.bt_far(tn)
    st(m, 0, LTRK); m.imm32(PSB, 1); a.mov_imm(0, 0)
    for k in range(0, 48, 4): a.movl_st_disp(0, k, 1)
    m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(tn)
    # bars: every fire FROM and TO stay within the sequence (1..bars) and FROM <= TO (TO follows FROM up)
    bx = m.newlabel('bx'); a.mov_imm(0, 14)
    m.imm32(CURSEQ, 1); a.movl_ld(1, 8)
    m.imm32(ARENA_LO, 1); a.cmphs(1, 8); m.bf_far(bx); m.imm32(ARENA_HI, 1); a.cmphs(1, 8); m.bt_far(bx)
    a.mov_rm_rn(8, 1); a.add_imm(BARS_OFF, 1); a.movw_ld(1, 2); a.extu_w(2, 2); a.tst_rr(2, 2); m.bt_far(bx)   # r2 = bars
    for slot in (BT, BF):
        n = m.newlabel('bc'); ld(m, slot, 0); a.cmpgt(2, 0); a.bf_lbl(n); st(m, 2, slot); a.mov_imm(1, 14); a.deflabel(n)
    n = m.newlabel('bo'); ld(m, BF, 0); ld(m, BT, 3); a.cmpgt(3, 0); a.bf_lbl(n); st(m, 0, BT); a.mov_imm(1, 14); a.deflabel(n)
    a.tst_rr(14, 14); m.bt_far(bx); m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(bx)
    # pads chosen: r10 = how many, r11 = first one + 1; PADSEL = r10 << 8 | r11 (redraw when it changes)
    m.imm32(PSB, 1); a.mov_imm(0, 10); a.mov_imm(0, 11); a.mov_imm(0, 3)
    sc, sn = m.newlabel('sc'), m.newlabel('sn')
    a.deflabel(sc)
    a.movb_ld(1, 0); a.tst_rr(0, 0); a.bt_lbl(sn); a.add_imm(1, 10); a.tst_rr(11, 11); a.bf_lbl(sn)
    a.mov_rm_rn(3, 11); a.add_imm(1, 11)
    a.deflabel(sn); a.add_imm(1, 1); a.add_imm(1, 3); a.mov_imm(48, 0); a.cmpeq(0, 3); a.bf_lbl(sc)
    a.mov_rm_rn(10, 0); a.shll8(0); a.or_rr(11, 0); st(m, 0, PADSEL)
    # pad label: " ALL" / " A05" / " 3PD"
    m.imm32(pe.addr['dlabels'][PADL], 12); a.mov_rm_rn(11, 9)
    al, dn, one = m.newlabel('al'), m.newlabel('dn'), m.newlabel('one')
    a.tst_rr(10, 10); m.bt_far(al)
    a.mov_imm(1, 0); a.cmpeq(0, 10); m.bt_far(one)
    a.mov_rm_rn(10, 4); a.mov_imm(10, 5); pe._hcall(m, '__udiv')                          # r0 tens, r1 ones
    tz = m.newlabel('tz'); a.mov_imm(ord(' '), 2); a.tst_rr(0, 0); a.bt_lbl(tz); a.mov_rm_rn(0, 2); a.add_imm(0x30, 2)
    a.deflabel(tz); a.movb_st(2, 12); a.add_imm(1, 12); a.add_imm(0x30, 1); a.movb_st(1, 12); a.add_imm(1, 12)
    for ch in "PD": a.mov_imm(ord(ch), 0); a.movb_st(0, 12); a.add_imm(1, 12)
    a.bra_lbl(dn); a.nop()
    a.deflabel(one)
    a.add_imm(-1, 9); a.mov_rm_rn(9, 4); a.mov_imm(12, 5); pe._hcall(m, '__udiv')
    a.mov_rm_rn(1, 10); a.mov_imm(ord(' '), 2); a.movb_st(2, 12); a.add_imm(ord('A'), 0)
    a.add_imm(1, 12); a.movb_st(0, 12); a.add_imm(1, 12)
    a.mov_rm_rn(10, 4); a.add_imm(1, 4); a.mov_imm(10, 5); pe._hcall(m, '__udiv')
    a.add_imm(0x30, 0); a.movb_st(0, 12); a.add_imm(1, 12); a.add_imm(0x30, 1); a.movb_st(1, 12)
    a.bra_lbl(dn); a.nop()
    a.deflabel(al)
    for ch in " ALL": a.mov_imm(ord(ch), 0); a.movb_st(0, 12); a.add_imm(1, 12)
    a.deflabel(dn)
    m.imm32(EVQ + 0x70, 1); ld(m, PADSEL, 0); ld(m, PTK, 2); a.cmpeq(0, 2); np_ = m.newlabel('np'); a.bt_lbl(np_)
    st(m, 0, PTK); m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(np_)
pe.on_fire(e_fire)

def e_inactive(m): sti(m, 0, EACT); sti(m, 0xFF, CUR); sti(m, 0, SINIT)   # left the page: the slider may be elsewhere on return, so it has to catch the value again (user 2026-11-08: jumps on re-entry)
pe.on_inactive(e_inactive)

def e_pad(m, hret0, hret1):
    """a pad = only that pad (again = ALL); the OS plays it as usual"""
    a = m.a
    ret = m.newlabel('ret')
    a.movl_ld_disp(8, 4, 0); a.tst_rr(0, 0); a.bt_lbl(ret)
    a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 9); a.extu_w(9, 9); a.mov_imm(12, 0); a.cmphs(0, 9); a.bt_lbl(ret)
    m.imm32(BANKOBJ, 1); a.movb_ld(1, 0); a.and_imm(3); a.mov_imm(12, 2); a.mul_l(2, 0); a.sts_macl(0); a.add_rr(0, 9)
    m.imm32(PSB, 1); a.add_rr(9, 1); a.movb_ld(1, 0); a.mov_imm(1, 2); a.xor_rr(2, 0); a.movb_st(0, 1)   # in / out
    a.deflabel(ret); jmpl(m, hret0)
    m.pool_jump()
pe.on_event(0x38, e_pad)

def e_doit(m):
    """DO IT: the notes of the track in the bars -> MPC3000 TC/SW of the TC page + shift (wrap), then sorted"""
    a = m.a
    ex, nos = m.newlabel('ex'), m.newlabel('nos')
    m.imm32(CURSEQ, 1); a.movl_ld(1, 8)
    m.imm32(ARENA_LO, 1); a.cmphs(1, 8); m.bf_far(nos); m.imm32(ARENA_HI, 1); a.cmphs(1, 8); m.bt_far(nos)
    a.mov_rm_rn(8, 1); a.add_imm(BARS_OFF, 1); a.movw_ld(1, 7); a.extu_w(7, 7); a.tst_rr(7, 7); m.bt_far(nos)   # r7 = bars
    # r10 = start tick, r11 = end tick, r12 = length
    ld(m, BF, 2); ld(m, BT, 3); c1 = m.newlabel('c1'); a.cmpgt(7, 3); a.bf_lbl(c1); a.mov_rm_rn(7, 3); a.deflabel(c1)
    c2 = m.newlabel('c2'); a.cmpgt(3, 2); a.bf_lbl(c2); a.mov_rm_rn(3, 2); a.deflabel(c2)
    m.imm32(0xFFFFFF, 6)
    a.mov_rm_rn(8, 1); a.add_imm(BART_OFF, 1); a.mov_rm_rn(2, 0); a.add_imm(-1, 0); a.shll2(0); a.add_rr(1, 0); a.movl_ld(0, 10); a.and_rr(6, 10)
    a.mov_rm_rn(3, 0); a.shll2(0); a.add_rr(1, 0); a.movl_ld(0, 11); a.and_rr(6, 11)
    a.mov_rm_rn(7, 0); a.shll2(0); a.add_rr(1, 0); a.movl_ld(0, 12); a.and_rr(6, 12)
    # r13 = note filter (-1 = all), r14 = track
    # NTB[note] = 1 for the notes of the pads chosen; r13 = how many pads (-1 = none = all notes)
    m.imm32(NTB, 1); a.mov_imm(0, 0)
    for k in range(0, 128, 4): a.movl_st(0, 1); a.add_imm(4, 1)
    m.imm32(PROG_IDX, 1); a.movw_ld(1, 2); a.extu_w(2, 2); m.imm32(PROG_STRIDE, 1); a.mul_l(1, 2); a.sts_macl(2)
    m.imm32(PROG_BASE + NOTE_A, 1); a.add_rr(1, 2)
    m.imm32(PSB, 3); a.mov_imm(0, 13); a.mov_imm(48, 4); m.imm32(NTB, 6)
    pl, ps, nf = m.newlabel('pl'), m.newlabel('ps'), m.newlabel('nf')
    a.deflabel(pl)
    a.movb_ld(3, 0); a.tst_rr(0, 0); a.bt_lbl(ps)
    a.movb_ld(2, 0); a.extu_b(0, 0); a.mov_rm_rn(6, 1); a.add_rr(0, 1); a.mov_imm(1, 0); a.movb_st(0, 1); a.add_imm(1, 13)
    a.deflabel(ps); a.add_imm(1, 3); a.add_imm(1, 2); a.add_imm(-1, 4); a.tst_rr(4, 4); a.bf_lbl(pl)
    a.tst_rr(13, 13); a.bf_lbl(nf); a.mov_imm(-1, 13)
    a.deflabel(nf); rdb(m, TRK, 14)                                                        # 0 = all tracks
    # grid / offset of the TC page settings
    ld(m, G, 5); ld(m, GSW, 4); emit_off(m, 5, 4, 6); st(m, 6, OFF)
    sti(m, 0, CNT)
    # playhead: remember the tick it points at
    m.imm32(PLAYPTR, 1); a.movl_ld(1, 0); a.movl_ld(0, 0); st(m, 0, PTK)
    a.stc_sr(0); st(m, 0, WRAP); a.or_imm(0xF0); a.ldc_sr(0)                               # interrupts off (WRAP = sr)
    m.imm32(EV_OFF, 9); a.add_rr(8, 9)
    lp, nx, dl = m.newlabel('lp'), m.newlabel('nx'), m.newlabel('dl')
    a.deflabel(lp)
    a.movl_ld(9, 4); m.imm32(END, 1); a.cmpeq(1, 4); m.bt_far(dl)
    a.mov_rm_rn(9, 1); a.add_imm(5, 1); a.movb_ld(1, 0); a.extu_b(0, 0); m.imm32(0x90, 1); a.cmpeq(1, 0); m.bf_far(nx)
    at_ = m.newlabel('at'); a.tst_rr(14, 14); a.bt_lbl(at_)
    a.mov_rm_rn(9, 1); a.add_imm(4, 1); a.movb_ld(1, 0); a.extu_b(0, 0); a.cmpeq(14, 0); m.bf_far(nx)
    a.deflabel(at_)
    a.cmpge(10, 4); m.bf_far(nx); a.cmpgt(4, 11); m.bf_far(nx)                              # start <= t < end
    a.cmppz(13); nn = m.newlabel('nn'); a.bf_lbl(nn)
    a.mov_rm_rn(9, 1); a.add_imm(6, 1); a.movb_ld(1, 0); a.extu_b(0, 0)
    m.imm32(NTB, 1); a.add_rr(0, 1); a.movb_ld(1, 0); a.tst_rr(0, 0); m.bt_far(nx)          # not a chosen pad's note
    a.deflabel(nn)
    ld(m, G, 5); ld(m, OFF, 6); call(m, '__tcsnap0')
    rdb(m, SHI, 1); a.add_imm(-24, 1); a.add_rr(1, 0)                                       # + shift
    w1, w2 = m.newlabel('w1'), m.newlabel('w2')
    a.cmppz(0); a.bt_lbl(w1); a.add_rr(12, 0); a.deflabel(w1)
    a.cmpge(12, 0); a.bf_lbl(w2); a.sub(12, 0); a.deflabel(w2)
    a.movl_st(0, 9); ld(m, CNT, 0); a.add_imm(1, 0); st(m, 0, CNT)
    a.deflabel(nx); a.add_imm(16, 9); jmpl(m, lp)
    m.pool_jump()
    a.deflabel(dl)
    # insertion sort by tick (stable; the END event has the biggest tick and stays last)
    m.imm32(EV_OFF + 16, 9); a.add_rr(8, 9)
    sl, sn, sx = m.newlabel('sl'), m.newlabel('sn'), m.newlabel('sx')
    a.mov_rm_rn(8, 1); m.imm32(EV_OFF, 0); a.add_rr(0, 1); a.movl_ld(1, 0); m.imm32(END, 1); a.cmpeq(1, 0); m.bt_far(sx)
    a.deflabel(sl)
    a.movl_ld(9, 4); a.mov_rm_rn(9, 1); a.add_imm(-16, 1); a.movl_ld(1, 0); a.cmpgt(4, 0); m.bf_far(sn)   # prev > t?
    a.movl_ld_disp(9, 4, 5); a.movl_ld_disp(9, 8, 6); a.movl_ld_disp(9, 12, 7)
    a.mov_rm_rn(9, 3)
    il, ix = m.newlabel('il'), m.newlabel('ix')
    m.imm32(EV_OFF, 2); a.add_rr(8, 2)
    a.deflabel(il)
    a.cmpeq(2, 3); a.bt_lbl(ix); a.mov_rm_rn(3, 1); a.add_imm(-16, 1); a.movl_ld(1, 0); a.cmpgt(4, 0); a.bf_lbl(ix)
    for o in (0, 4, 8, 12): a.movl_ld_disp(1, o, 0); a.movl_st_disp(0, o, 3)
    a.mov_rm_rn(1, 3); a.bra_lbl(il); a.nop()
    a.deflabel(ix)
    a.movl_st(4, 3); a.movl_st_disp(5, 4, 3); a.movl_st_disp(6, 8, 3); a.movl_st_disp(7, 12, 3)
    a.deflabel(sn)
    a.add_imm(16, 9); a.movl_ld(9, 0); m.imm32(END, 1); a.cmpeq(1, 0); m.bf_far(sl)
    a.deflabel(sx)
    # playhead: first event at or after its tick
    m.imm32(EV_OFF, 9); a.add_rr(8, 9); ld(m, PTK, 4)
    pl, px = m.newlabel('pl'), m.newlabel('px')
    a.deflabel(pl); a.movl_ld(9, 0); a.cmpge(4, 0); a.bt_lbl(px); a.add_imm(16, 9); a.bra_lbl(pl); a.nop()
    a.deflabel(px); m.imm32(PLAYPTR, 1); a.movl_st(9, 1)
    ld(m, WRAP, 0); a.ldc_sr(0)
    sti(m, 0, WRAP); sti(m, 0, SNN); sti(m, 0, PN)                                          # (the fixer starts over)
    pe.emit_message_dec(m, "DONE ", CNT, " NOTES"); jmpl(m, ex)
    m.pool_jump()
    a.deflabel(nos); pe.emit_message(m, "NO SEQUENCE")
    a.deflabel(ex)
pe.on_doit(e_doit)


# =====================================================================================================================
def ref_off(g, s):
    return ((s - 48) >> 2) << ({48: 2, 24: 1, 12: 0}[g]) if g in (48, 24, 12) else 0

def ref_snap(t, g, s):
    if g == 1: return t
    step = (t + g // 2 - 1) // g
    return step * g + (ref_off(g, s) if step & 1 else 0)

def ref_mpc500(g, s):
    return int((s - 50) * g / 50) if g in (48, 24, 12) else 0


if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', '..', 'TC3000.WAV'); pg.build_wav(out)
    from validate import Harness
    from mpcmod import HOOKS
    from pages import ENABLE_DOIT, ENABLE_CANCEL, SET_FOCUS
    from testkit import PageTest, FIRMWARE
    # the MPC3000 table we promised
    assert [ref_off(24, s) for s in SWSTEPS] == [0, 2, 4, 6, 8, 10, 12]
    assert [ref_off(48, s) for s in SWSTEPS] == [0, 4, 8, 12, 16, 20, 24]
    assert [24 + ref_off(24, s) for s in SWSTEPS] == [24, 26, 28, 30, 32, 34, 36]
    assert [48 + ref_off(48, s) for s in SWSTEPS] == [48, 52, 56, 60, 64, 68, 72]

    class OSTest(PageTest):
        """the OS code image in SDRAM (as the boot copy does), so the real OS code runs where we need it"""
        def __init__(self, pg, wav):
            self.pg = pg
            self.h = h = Harness(FIRMWARE); h.fill_scratch(); self.e = e = h.emu
            fw = open(FIRMWARE, 'rb').read()
            keep = [(HOOKS[h.hook][1], 4), (ENABLE_DOIT, 10), (ENABLE_CANCEL, 10)]
            saved = [(ad, [e.rd(ad + i, 2) for i in range(0, n, 2)]) for ad, n in keep]
            buf, off = e._region(0x8849495C); buf[off:off + len(fw)] = fw
            for ad, ws in saved:
                for i, w in enumerate(ws): e.wr(ad + 2 * i, w, 2)
            self.stub(SET_FOCUS)
    t = OSTest(pg, out); h, e = t.h, t.e
    R32, W32 = h.ram_r32, h.ram_w32
    SETTINGS = 0x88483F88
    PSTORE = 0x88E30000
    def pstub(s):
        # r4 = msg: type @0 (u16), value @4, id @12. TC -> PSTORE, SW -> PSTORE+4
        s.movl_ld_disp(4, 12, 1); s.movl_pool(PSTORE, 2); s.movl_pool(ID_SW, 3); s.cmpeq(3, 1); s.bf_lbl('a'); s.add_imm(4, 2)
        s.deflabel('a'); s.movw_ld(4, 0); s.extu_w(0, 0);
        s.mov_imm(0x11, 3); s.cmpeq(3, 0); s.bf_lbl('g'); s.movl_ld_disp(4, 4, 0); s.movl_st(0, 2); s.bra_lbl('x'); s.nop()
        s.deflabel('g'); s.movl_ld(2, 0); s.movl_st_disp(0, 4, 4)
        s.deflabel('x'); s.mov_imm(1, 0); s.rts(); s.nop()
    t.stub(SYS_PARAM, pstub)
    PUSHLOG = 0x88E31000
    t.stub(OS_PUSH_PAGE, lambda s: (s.movl_pool(PUSHLOG, 1), s.movl_ld(1, 0), s.add_imm(1, 0), s.movl_st(0, 1), s.shll2(0), s.shll2(0),
                                    s.add_rr(0, 1), s.movl_st(5, 1), s.movl_st_disp(6, 4, 1), s.rts(), s.nop()))
    h.load_hub(); h.load_wav(out); h.fire(10)
    # --- 1. the OS Note Repeat swing code, before the patch = the MPC500 table ---
    def nr_run(site, g, s):
        """run the OS code from the settings read to the offset; returns the offset register"""
        start, stop, rg, rout = ((0x884B9548, 0x884B9576, 14, 6) if site == 1 else (0x884BA984, 0x884BA9AE, 13, 14))
        e.wr(SETTINGS + 0x1A, (e.rd(SETTINGS + 0x1A, 2) & ~(0x7F << 6)) | (s << 6), 2)
        regs = [0x22220000 + i for i in range(16)]; regs[15] = 0x88400000 - 512; regs[rg] = g
        e.r = regs[:]; e.pr = 0xDEADBEEE; e.pc = start; e.maxsteps = 100000
        assert e.run(stop_pc=stop), "OS swing code did not reach %08x" % stop
        return e.r[rout] - (1 << 32) if e.r[rout] & 0x80000000 else e.r[rout]
    def nr_table(site): return {g: [nr_run(site, g, s) for s in SWSTEPS] for g in (48, 24, 12)}
    # (the add-on's init already patched the OS: put the original back for the 'before' run)
    patched = {a_: [e.rd(a_ + 2 * i, 2) for i in range(len(o))] for a_, o, n in NR_SITES}
    assert all(patched[a_] == n for a_, o, n in NR_SITES), "init patched both Note Repeat sites"
    for a_, o, n in NR_SITES:
        for i, w in enumerate(o): e.wr(a_ + 2 * i, w, 2)
    for site in (1, 2):
        got = nr_table(site)
        want = {g: [ref_mpc500(g, s) for s in SWSTEPS] for g in (48, 24, 12)}
        assert got == want, (site, got, want)
    print("  ok OS Note Repeat swing, unpatched (both sites, run in the emulator): MPC500 = trunc((SW-50)*grid/50):")
    print("     1/16 %s   1/8 %s" % ([24 + v for v in nr_table(1)[24]], [48 + v for v in nr_table(1)[48]]))
    for a_, o, n in NR_SITES:
        for i, w in enumerate(n): e.wr(a_ + 2 * i, w, 2)
    for site in (1, 2):
        got = nr_table(site)
        assert got == {g: [ref_off(g, s) for s in SWSTEPS] for g in (48, 24, 12)}, (site, got)
    print("  ok patched: MPC3000  1/16 %s   1/8 %s   1/32 %s" % ([24 + v for v in nr_table(1)[24]], [48 + v for v in nr_table(1)[48]], [12 + v for v in nr_table(1)[12]]))
    # --- 2. TC button and TC page (parameters: a model of the OS parameter manager, which needs the booted OS) ---
    W32(PSTORE, 2); W32(PSTORE + 4, 50); W32(PUSHLOG, 0)
    # TC button: the OS TC page appears -> back + ours
    h.ram_w32(EVQ + 0x68, OS_TCPAGE); h.fire(1)
    pushes = [(R32(PUSHLOG + 16 * (k + 1)), R32(PUSHLOG + 16 * (k + 1) + 4)) for k in range(R32(PUSHLOG))]
    assert pushes[:2] == [(0, 2), (pg.page, 0)], pushes
    print("  ok TC button: the OS TC page is replaced (back, then the TC 3000 page)")
    t.open(); assert e.rd(TCI, 1) == 2 and e.rd(SWI, 1) == 0, "page shows the OS TC 1/16, SW 50"
    e.wr(SWI, 4, 1); t.fire(2); assert R32(PSTORE + 4) == 66, R32(PSTORE + 4)
    e.wr(TCI, 0, 1); t.fire(2); assert R32(PSTORE) == 0
    W32(PSTORE, 2); t.close(); t.open(); assert e.rd(TCI, 1) == 2 and e.rd(SWI, 1) == 4
    print("  ok TC page: shows the OS settings, SW 66% -> OS 66, TC 1/8 -> OS 0; reopened shows the OS values")
    assert R32(G) == 24 and R32(OFF) == 8 and R32(GSW) == 66
    # --- 3. recording: a sequence; notes appear near the playhead as the OS records them ---
    import testseq
    P = 0x881E0000; W32(CURSEQ, P)
    old = [(0, 1, 36, 100, 48), (31, 1, 42, 90, 10), (96, 1, 36, 100, 48), (127, 1, 42, 90, 10)]     # old 500-swung hats
    seq = testseq.make_seq(bars=2, evs=old)
    for i in range(0, len(seq), 4): W32(P + i, struct.unpack_from('<I', seq, i)[0])
    def evs():
        out_, a_ = [], P + EV_OFF
        while R32(a_) != END: out_.append((R32(a_), e.rd(a_ + 4, 1), e.rd(a_ + 6, 1))); a_ += 16
        return out_
    def os_record(tick, note, track=1):
        """what the OS does when a note is recorded: insert sorted (after equal ticks), playhead past it if behind"""
        lst = []; a_ = P + EV_OFF
        while True:
            lst.append(bytes(e.rd(a_ + i, 1) for i in range(16)))
            if R32(a_) == END: break
            a_ += 16
        k = next(i for i, b in enumerate(lst) if struct.unpack_from('<I', b)[0] > tick)
        new = struct.pack('<IBBBBH6x', tick, track, 0x90, note, 100, 0)
        lst.insert(k, new)
        for i, b in enumerate(lst):
            for j in range(0, 16, 4): W32(P + EV_OFF + 16 * i + j, struct.unpack_from('<I', b, j)[0])
        pp = R32(PLAYPTR)
        if P + EV_OFF + 16 * k < pp: W32(PLAYPTR, pp + 16)
    def play_to(idx): W32(PLAYPTR, P + EV_OFF + 16 * idx); t.fire(2)
    t.close(); play_to(0); t.fire(3)
    # hit a snare (38) on the 2nd 16th of beat 2: the OS (MPC500 66%) puts it at 96+31 = 127; the playhead is at it
    play_to(3); os_record(127, 38); t.fire(1)
    assert R32(PN) == 1, ("new note pending", R32(PN))
    assert (127, 1, 38) in evs(), "not moved while the playhead is on it"
    play_to(evs().index((127, 1, 38)) + 1); t.fire(3)
    assert (128, 1, 38) in evs() and (127, 1, 42) in evs() and (31, 1, 42) in evs(), evs()
    print("  ok recorded note: 127 (MPC500 66%%) -> 128 (MPC3000) once passed; old 500-swung hats untouched: %s" % evs())
    # a chord recorded together (kick + hat at 31 on track 2), then the loop wraps
    play_to(1); os_record(31, 36, 2); os_record(31, 47, 2); t.fire(1); assert R32(PN) == 2, R32(PN)
    play_to(0); t.fire(4)                                                                  # wrap: playhead back
    ev_ = evs(); assert (32, 2, 36) in ev_ and (32, 2, 47) in ev_ and (31, 1, 42) in ev_, ev_
    assert [x[0] for x in ev_] == sorted(x[0] for x in ev_)
    print("  ok chord at 31 on track 2 -> both 32 (order kept), fixed after the loop wrap; old hat on track 1 still 31")
    # --- 4. EDIT page: existing notes -> MPC3000 + shift ---
    assert R32(LASTTRK) == 2
    old2 = [(0, 1, 36, 100, 48), (31, 1, 42, 90, 10), (55, 1, 42, 90, 10), (96, 1, 36, 100, 48), (127, 1, 42, 90, 10), (384, 1, 36, 100, 48), (415, 1, 42, 90, 10), (420, 2, 50, 90, 10)]
    seq = testseq.make_seq(bars=2, evs=old2)
    for i in range(0, len(seq), 4): W32(P + i, struct.unpack_from('<I', seq, i)[0])
    W32(PLAYPTR, P + EV_OFF); t.fire(2)
    h.ram_w32(EVQ + 0x68, pe.page); t.fire(3); t.pg = pe                               # events / message of the EDIT page
    assert PSB % 4 == 0
    assert e.rd(TRK, 1) == 2 and (R32(BF), R32(BT)) == (1, 2), (e.rd(TRK, 1), R32(BF), R32(BT))
    print("  ok EDIT opens on the whole sequence: BAR 1-2 (2-bar sequence)")
    W32(BT, 9); t.fire(1); assert R32(BT) == 2, ("TO past the end -> last bar", R32(BT))
    W32(BF, 5); t.fire(1); assert R32(BF) == 2, ("FROM past the end -> last bar", R32(BF))
    W32(BF, 1); W32(BT, 1); t.fire(1); W32(BF, 2); t.fire(1); assert (R32(BF), R32(BT)) == (2, 2), "TO follows FROM up"
    print("  ok EDIT bars stay within the 2-bar sequence (TO 9 -> 2, FROM 5 -> 2), FROM never past TO")
    e.wr(TRK, 1, 1); W32(BF, 1); W32(BT, 1); e.wr(SHI, 24, 1); t.fire(2)
    r = t.event(2, 0x2E); t.fire(1)
    ev_ = evs(); assert t.msg() == "DONE 05 NOTES", (t.msg(), r, ev_, R32(CNT), hex(R32(EVQ + 0x68)), hex(pe.page))
    assert ev_ == [(0, 1, 36), (32, 1, 42), (48, 1, 42), (96, 1, 36), (128, 1, 42), (384, 1, 36), (415, 1, 42), (420, 2, 50)], ev_
    print("  ok EDIT bar 1 track 1: 31 -> 32, 55 (late 16th) -> 48 grid, 127 -> 128; bar 2 and track 2 untouched")
    e.wr(SHI, 24 - 3, 1); t.fire(1); t.pad(0, 0); t.fire(2); assert t.label(PADL, 4) == " A01"
    t.pad(0, 0); t.fire(2); assert t.label(PADL, 4) == " ALL"
    t.event(2, 0x2E); t.fire(1); ev_ = evs()
    assert sorted(ev_) == ev_ or [x[0] for x in ev_] == sorted(x[0] for x in ev_)
    assert (765, 1, 36) in ev_ and (29, 1, 42) in ev_ and (45, 1, 42) in ev_ and (125, 1, 42) in ev_ and (93, 1, 36) in ev_, ev_
    print("  ok EDIT EARLIER -3 (all pads): notes 3 ticks early, the one at 0 wraps to the end (765); list re-sorted: %s" % ev_[:5])
    # only one pad: A02 plays note 42 (the hats) -> LATER +2 moves only the hats
    e.wr(PROG_BASE + e.rd(PROG_IDX, 2) * PROG_STRIDE + NOTE_A + 1, 42, 1)
    before = evs(); e.wr(SHI, 24 + 2, 1); t.pad(0, 1); t.fire(2); assert t.label(PADL, 4) == " A02"
    t.event(2, 0x2E); t.fire(1); ev_ = evs()
    hats = sorted(x for x in before if x[2] == 42 and x[1] == 1 and x[0] < 384); others = [x for x in before if x not in hats]
    assert sorted(x for x in ev_ if x[2] == 42 and x[1] == 1 and x[0] < 386) == sorted((ref_snap(x[0], 24, 66) + 2, 1, 42) for x in hats), ev_
    assert all(x in ev_ for x in others), ev_
    print("  ok EDIT pad A02 only (note 42), LATER +2: hats 29/45/125 -> 34/50/130 (MPC3000 + 2), kicks untouched")
    # several pads: A01 (36) + A02 (42) -> " 2PD", both move; A03 (note 50, track 2 only) changes nothing on track 1
    e.wr(PROG_BASE + e.rd(PROG_IDX, 2) * PROG_STRIDE + NOTE_A + 0, 36, 1)
    e.wr(PROG_BASE + e.rd(PROG_IDX, 2) * PROG_STRIDE + NOTE_A + 2, 50, 1)
    t.pad(0, 0); t.fire(2); assert t.label(PADL, 4) == " 2PD", t.label(PADL, 4)
    t.pad(0, 2); t.fire(2); assert t.label(PADL, 4) == " 3PD", t.label(PADL, 4)
    t.pad(0, 2); t.fire(2); assert t.label(PADL, 4) == " 2PD", "hit again = out"
    before = evs(); e.wr(SHI, 24 + 1, 1); t.event(2, 0x2E); t.fire(1); ev_ = evs()
    want = sorted(((ref_snap(x[0], 24, 66) + 1) % 768 if x[1] == 1 and x[0] < 384 and x[2] in (36, 42) else x[0], x[1], x[2]) for x in before)
    assert sorted(ev_) == want, (ev_, want)
    t.pad(0, 0); t.fire(2); assert t.label(PADL, 4) == " A02", "A01 out: only A02 left"
    print("  ok EDIT pads A01+A02 (' 2PD'): kicks and hats of bar 1 both moved; A03 in/out; A01 out -> ' A02'")
    # changing the track: pads back to ALL
    t.pad(0, 2); t.fire(2); assert t.label(PADL, 4) == " 2PD"
    e.wr(TRK, 2, 1); t.fire(2); assert t.label(PADL, 4) == " ALL", "wheel: another track -> ALL"
    # slider = the track (A, 1..48), catch-up
    def slider(k, fires=60):
        lo, hi = R32(SLO), R32(SHH); W32(SLIDER, lo + round((k - 1) * (hi - lo) / (MAXTRK - 1))); t.fire(fires)
    slider(1); assert e.rd(TRK, 1) == 2, "slider below the track shown: waits"
    slider(5); assert e.rd(TRK, 1) == 5, ("passed it: follows", e.rd(TRK, 1))
    slider(1); assert e.rd(TRK, 1) == 1, ("all the way down = track 1, never A", e.rd(TRK, 1))
    slider(MAXTRK); assert e.rd(TRK, 1) == MAXTRK
    t.pad(0, 0); t.fire(2); assert t.label(PADL, 4) == " A01"
    slider(10); assert e.rd(TRK, 1) == 10 and t.label(PADL, 4) == " ALL", (e.rd(TRK, 1), t.label(PADL, 4))
    e.wr(TRK, 20, 1); t.fire(2); slider(15); assert e.rd(TRK, 1) == 20, "wheel moved it: slider waits"
    slider(22); assert e.rd(TRK, 1) == 22
    e.wr(TRK, 0, 1); t.fire(60); assert e.rd(TRK, 1) == 0, "A with the wheel: stays while the slider rests"
    slider(18); assert e.rd(TRK, 1) == 18, ("A: the slider takes over when it moves", e.rd(TRK, 1))
    # page left, slider moved on another page, page entered again: the value stays until the slider catches it (user 2026-11-08)
    h.ram_w32(EVQ + 0x68, 0x88123456); t.fire(3); W32(SLIDER, R32(SHH)); t.fire(3); h.ram_w32(EVQ + 0x68, pe.page); t.fire(60)
    v_ = e.rd(TRK, 1); assert v_ != MAXTRK, ("re-entry: not the slider's position", v_)      # (the page opens on its last track)
    slider(25); assert e.rd(TRK, 1) == v_, ("above it: not caught yet", e.rd(TRK, 1))
    slider(1); assert e.rd(TRK, 1) == 1, ("passed it: caught", e.rd(TRK, 1))
    slider(18); assert e.rd(TRK, 1) == 18
    print("  ok EDIT track: wheel/slider change -> pads back to ALL; slider 1..48 (bottom = 1), catch-up; A by wheel only")
    # ALL tracks: bar 2, LATER +1 -> track 1 and track 2 notes both move
    e.wr(TRK, 0, 1); W32(BF, 2); W32(BT, 2); e.wr(SHI, 25, 1); t.pad(0, 1); t.fire(2); assert t.label(PADL, 4) == " ALL"
    before = evs(); t.event(2, 0x2E); t.fire(1); ev_ = evs()
    want = sorted((((ref_snap(x[0], 24, 66) + 1) % 768) if 384 <= x[0] < 768 else x[0], x[1], x[2]) for x in before)
    assert sorted(ev_) == want and (417, 2, 50) in ev_ and (385, 1, 36) in ev_, (ev_, want)
    print("  ok EDIT ALL tracks, bar 2, LATER +1: track 1 and track 2 both moved (416 -> 417 on track 2; 765 -> 768 -> wraps to 1) (%s)" % t.msg())
    # --- 5. realistic: a played 4-bar pattern, new TC applied to the whole sequence, then it plays 2 loops ---
    import random
    rnd = random.Random(7); pat = []
    for b in range(4):
        for s16 in range(16):
            t0 = b * 384 + s16 * 24 + rnd.randint(-7, 7)
            if t0 < 0: continue
            pat.append((t0, 1, 42 if s16 % 2 else 36, 100, 10))
            if s16 % 4 == 2: pat.append((t0 + rnd.randint(-3, 3), 2, 38, 100, 10))
    pat.sort(key=lambda x: x[0])
    seq = testseq.make_seq(bars=4, evs=pat)
    for i in range(0, len(seq), 4): W32(P + i, struct.unpack_from('<I', seq, i)[0])
    W32(PLAYPTR, P + EV_OFF); t.fire(2)
    W32(PSTORE, 2); W32(PSTORE + 4, 58); h.ram_w32(EVQ + 0x68, pg.page); t.pg = pg; t.fire(3)    # TC 1/16, SW 58
    h.ram_w32(EVQ + 0x68, pe.page); t.pg = pe; t.fire(3)
    assert (R32(BF), R32(BT), e.rd(TRK, 1)) == (1, 4, e.rd(TRK, 1))
    e.wr(SHI, 24, 1); t.fire(3)                                                    # NO SHIFT, set before the slider
    W32(SLIDER, R32(SLO) + 40); t.fire(60); W32(SLIDER, R32(SLO)); t.fire(60)        # user slides down to track 1
    assert e.rd(TRK, 1) == 1 and e.rd(SHI, 1) == 24, ("slider must not touch SHIFT", e.rd(TRK, 1), e.rd(SHI, 1))
    assert t.label(PADL, 4) == " ALL"
    before = evs(); t.event(2, 0x2E); t.fire(1); after = evs()
    want = sorted(((ref_snap(x[0], 24, 58) % 1536) if x[1] == 1 else x[0], x[1], x[2]) for x in before)
    assert sorted(after) == want, [(a_, b_) for a_, b_ in zip(sorted(after), want) if a_ != b_][:8]
    print("  ok played 4-bar pattern, TC 1/16 SW 58 on track 1, whole sequence: every note on the MPC3000 grid, track 2 kept")
    n = len(after)
    for loop in range(2):
        for k in range(n + 1): play_to(k % n)
    assert evs() == after, [(a_, b_) for a_, b_ in zip(evs(), after) if a_ != b_][:8]
    print("  ok two loops of playback after DO IT: nothing moved again")

    # --- 6. v3.0: SWING TYPE (PARAMS), SP-1200 ---
    # the SP-1200 grooves (sp1200/*.agr): swung 1/16 measured at 25.92 27.91 29.95 31.93 33.95 ticks -> the MPC3000 ticks
    assert [24 + ref_off(24, s) for s in SWSTEPS_SP[:6]] == [24, 26, 28, 30, 32, 34]
    def pushes(): return [(R32(PUSHLOG + 16 * (k + 1)), R32(PUSHLOG + 16 * (k + 1) + 4)) for k in range(R32(PUSHLOG))]
    def swstrs():
        w = pg.values[fidp(pg, SWI)]; d_ = R32(R32(R32(w + 0x24) + 0x20) + 4)
        return bytes(e.rd(d_ + i, 1) for i in range(28))
    def mark(): return bytes(e.rd(pg.dlabels[MODEL] + i, 1) for i in range(2)).decode()
    def show(p_): h.ram_w32(EVQ + 0x68, p_.page); t.pg = p_; t.fire(3)
    assert R32(LMODE) == 0 and e.rd(STY, 1) == 0 and swstrs() == _swstr(SWSTEPS) and mark() == "  ", "default MPC3000"
    W32(PSTORE, 2); W32(PSTORE + 4, 75); show(pg); assert e.rd(SWI, 1) == 6
    W32(PUSHLOG, 0); assert t.event(2, K_PARAMS) == 1 and pushes() == [(0, 2), (ps.page, 0)], pushes()
    show(ps); assert R32(RET) == pg.page
    e.wr(STY, 1, 1); t.fire(2)
    assert R32(LMODE) == 1 and R32(PSTORE + 4) == 71 and swstrs() == _swstr(SWSTEPS_SP) and mark() == "SP", (R32(PSTORE + 4), swstrs())
    print("  ok PARAMS on the TC page -> SWING TYPE; SP-1200: OS Swing 75 -> 71, SW steps %s, 'SP' on the TC page" % swstrs().split(b'\0')[:6])
    W32(PUSHLOG, 0); assert t.event(2, K_PARAMS) == 1 and pushes() == [(0, 2), (pg.page, 0)], pushes()
    show(pg); assert e.rd(SWI, 1) == 5, e.rd(SWI, 1)
    e.wr(SWI, 6, 1); t.fire(2); assert e.rd(SWI, 1) == 5 and R32(PSTORE + 4) == 71, "past 71%: back to 71"
    e.wr(SWI, 3, 1); t.fire(2); assert R32(PSTORE + 4) == 63 and R32(OFF) == 6 and R32(G) == 24, (R32(PSTORE + 4), R32(OFF))
    e.wr(SWI, 4, 1); t.fire(2); assert R32(PSTORE + 4) == 67 and R32(OFF) == 8
    print("  ok PARAMS again -> back to the TC page on 71%; wheel past 71 stays 71; 63% -> OS 63 (1/16 +6 = MPC 62), 67% -> +8")
    # Note Repeat (the patched OS code) with the SP values: the same ticks as MPC3000
    for s_sp, s_mpc in ((63, 62), (67, 66), (54, 54), (71, 71)):
        assert nr_run(1, 24, s_sp) == nr_run(1, 24, s_mpc) == ref_off(24, s_sp) and nr_run(2, 48, s_sp) == ref_off(48, s_sp)
    print("  ok Note Repeat in SP-1200 mode: 63/67 swing like MPC 62/66 (1/16 +6/+8, 1/8 +12/+16)")
    # SP-1200, TC OFF: recorded notes -> 24 PPQN grid (4 ticks); old notes untouched
    W32(PSTORE, 6); t.fire(2); assert R32(G) == SP_GRID and R32(OFF) == 0, (R32(G), R32(OFF))
    # the user selects another sequence (off-grid old notes) while it plays: they are NOT taken as new recordings
    P = 0x881F0000
    seq = testseq.make_seq(bars=2, evs=[(0, 1, 36, 100, 48), (31, 1, 42, 90, 10), (96, 1, 36, 100, 48), (127, 1, 42, 90, 10)])
    for i in range(0, len(seq), 4): W32(P + i, struct.unpack_from('<I', seq, i)[0])
    t.close(); W32(CURSEQ, P); play_to(2); t.fire(3); play_to(0); t.fire(3)
    assert R32(PN) == 0 and (31, 1, 42) in evs() and (127, 1, 42) in evs(), (R32(PN), evs())
    play_to(3); os_record(127, 38); t.fire(1); assert R32(PN) == 1, R32(PN)
    play_to(evs().index((127, 1, 38)) + 1); t.fire(3)
    play_to(4); os_record(125, 40); t.fire(3)                                  # recorded just behind the playhead
    ev_ = evs(); assert (128, 1, 38) in ev_ and (124, 1, 40) in ev_ and (31, 1, 42) in ev_ and (127, 1, 42) in ev_, ev_
    print("  ok SP-1200 TC OFF: recorded 127 -> 128, 125 -> 124 (24 PPQN); old notes 31 / 127 untouched: %s" % ev_)
    # EDIT -> PARAMS -> SWING TYPE -> CANCEL: EDIT as it was
    seq = testseq.make_seq(bars=4, evs=pat)
    for i in range(0, len(seq), 4): W32(P + i, struct.unpack_from('<I', seq, i)[0])
    W32(PLAYPTR, P + EV_OFF); show(pg); show(pe)
    assert (R32(BF), R32(BT)) == (1, 4)
    W32(BF, 2); W32(BT, 3); e.wr(TRK, 0, 1); t.fire(2)
    W32(PUSHLOG, 0); assert t.event(2, K_PARAMS) == 1 and pushes() == [(0, 2), (ps.page, 0)] and R32(RET) == pe.page
    show(ps); W32(PUSHLOG, 0); assert t.event(2, 0x2D) == 1 and pushes() == [(0, 2), (pe.page, 0)], pushes()
    show(pe); assert (R32(BF), R32(BT), e.rd(TRK, 1)) == (2, 3, 0), (R32(BF), R32(BT), e.rd(TRK, 1))
    print("  ok EDIT: PARAMS -> SWING TYPE, CANCEL -> EDIT with its bars 2-3 and track kept")
    # EDIT DO IT, SP-1200, TC OFF: every note of bars 2-3 on the 24 PPQN grid
    e.wr(SHI, 24, 1); t.fire(2)
    before = evs(); t.event(2, 0x2E); t.fire(1); after = evs()
    want = sorted(((ref_snap(x[0], SP_GRID, 50) % 1536) if 384 <= x[0] < 1152 else x[0], x[1], x[2]) for x in before)
    assert sorted(after) == want and all(x[0] % 4 == 0 for x in after if 384 <= x[0] < 1152), [(a_, b_) for a_, b_ in zip(sorted(after), want) if a_ != b_][:8]
    assert any(x[0] % 4 for x in before if 384 <= x[0] < 1152) and any(x[0] % 4 for x in after if x[0] < 384)
    print("  ok EDIT DO IT, SP-1200 TC OFF: bars 2-3 on the 24 PPQN grid, bars 1 and 4 untouched (%s)" % t.msg())
    # back to MPC3000 (DO IT on SWING TYPE): 63 -> 62, TC OFF = untouched again, strings back
    W32(PSTORE, 2); W32(PSTORE + 4, 63); t.fire(2)
    t.event(2, K_PARAMS); show(ps); e.wr(STY, 0, 1); t.fire(2)
    assert R32(LMODE) == 0 and R32(PSTORE + 4) == 62 and swstrs() == _swstr(SWSTEPS) and mark() == "  ", R32(PSTORE + 4)
    W32(PUSHLOG, 0); t.event(2, 0x2E); assert pushes() == [(0, 2), (pe.page, 0)], ("DO IT -> back to EDIT", pushes())
    W32(PSTORE, 6); show(pe); assert R32(G) == 1 and R32(OFF) == 0
    before = evs(); W32(BF, 1); W32(BT, 4); t.fire(1); t.event(2, 0x2E); t.fire(1); assert evs() == before, "MPC3000 TC OFF: untouched"
    print("  ok MPC3000 again: OS 63 -> 62, SW strings / mark back; DO IT on SWING TYPE -> EDIT; TC OFF DO IT changes nothing")
    print("OK: TC 3000 -> TC3000.WAV")
