#!/usr/bin/env python3
"""TRACK - replaces the MPC's Track page (the TRACK button): track type and MIDI channel instead of the tempo.

    python sdk/examples/track.py          -> builds TRACK.WAV and tests it in the emulator

On the MPC500: copy TRACK.WAV to the CF card, LOAD it (after HUB.WAV). The TRACK button opens it (MODE -> OTHER ->
"Track" too):
    DRUM    Ch:  1
    Tr:01-Track01
  DRUM / MIDI   the track type (the MPC's own Track type setting)
  Ch:           the track's MIDI channel: OFF, 1..16 (the MPC's own MIDI channel setting)
  Tr:01         the track (wheel, or the slider: catch-up, it takes over once it passes the track shown); the name follows
  PARAMS        opens the MPC's Track params page, as on the MPC's Track page. SEQUENCE leaves as usual.
The wheel changes the field under the cursor; the values are the MPC's own (changed elsewhere = shown here).

OS addresses (MPC500 OS 1.31; "fw" = read in the firmware 2026-11-03, not yet confirmed on hardware):
  fw: parameter manager 0x884E5CD0 (TC 3000): msg {u16 0x10 get / 0x11 set, u16 0, s32 value, 0, u32 id}; group 2
  (sequence) getter 0x884F11AE / setter 0x884F0DBC: 0x20010 track (value = number - 1; setter clamps, -> u16
  0x881D3218 + 0x970 = number), 0x20017 MIDI channel (0 OFF, 1..16; track + 0x25), 0x20018 track type (0 DRUM,
  1 MIDI; track + 0x24). Track = *(0x8847CBC8) + 0xFD0 + number * 0x30, name at +0 (as the getter of 0x20014).
  fw: Main page object 0x8848FE04 (handler 0x884CB176); its view 0x881C2B20 (2 = track): the TRACK button
  (0x884CB3C6) sets 2 and pushes the Main page; PARAMS there pushes Track params 0x8849BCE8 (mode 0). Checked at run
  time (+0x1C = VT_PAGE, +0x30 = handler) before it is redirected; Track params: +0x1C = VT_PAGE. PARAMS key code 0x15.
"""
import sys, os, struct
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import PageAddon, ld, st, sti
from mpcmod import EVQ, OS_PUSH_PAGE

SYS_PARAM = 0x884E5CD0
ID_TRK, ID_CH, ID_TYPE = 0x20010, 0x20017, 0x20018
CURSEQ = 0x8847CBC8
TRKNUM = 0x881D3218 + 0x970
TRK_OFF, TRK_SIZE, TYPE_OFF, CH_OFF = 0xFD0, 0x30, 0x24, 0x25
ARENA_LO, ARENA_HI = 0x881DB59C, 0x881DB59C + 0x1A0000
MAINPG, MAIN_H, VIEW, V_TRACK = 0x8848FE04, 0x884CB176, 0x881C2B20, 2
TRKPARAMS, VT_PAGE = 0x8849BCE8, 0x88507BA8
K_PARAMS = 0x15
NTRK, NAMEW = 64, 10

pg = PageAddon("track1", open_press=None)
pg.other_menu("Track")
pg.far_hend = True
pg.far_fire = True
pg.no_doit = True                                                           # no DO IT here: 12 LEVELS stays off (user 2026-11-07)
TYPEF = pg.choice("", 0, 0, ["DRUM", "MIDI"], default=0)
CHF = pg.choice("Ch:", 8, 0, ["OFF"] + ["%3d" % i for i in range(1, 17)], default=1)
TRF = pg.value("Tr:", 0, 1, fmt="00", lo=1, hi=NTRK, default=1)
NAMEL = pg.dlabel(5, 1, 1 + NAMEW)                                         # "-Track01"
(LTYPE, LCH, LTRF, ACT, CHG) = [pg.alloc_slot() for _ in range(5)]
(NSTEP, CUR, CAUGHT, SIDE, SS, SF, SINIT, SLO, SHI) = [pg.alloc_slot() for _ in range(9)]
MSGB = [pg.alloc_slot() for _ in range(4)]
pg.defaults[LTYPE] = 0xFF; pg.defaults[LCH] = 0xFF
SLIDER = 0x8848C3E4                                                         # Q-Link slider (Song, Program)
SL_LO, SL_HI, HYST, MAXTRK = 64, 960, 3, 48                                 # 48 = the MPC's last track (setter)
pg.defaults[SLO] = SL_LO; pg.defaults[SHI] = SL_HI; pg.defaults[SS] = 0xFF; pg.defaults[NSTEP] = MAXTRK - 1
pg.defaults[CUR] = 0xFFFF

def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()
def jmpl(m, lbl): m.a.movl_pool(lbl, 2); m.a.jmp(2); m.a.nop()
def rdb(m, addr, r): m.imm32(addr, r); m.a.movb_ld(r, r); m.a.extu_b(r, r)
def wrb(m, rv, addr): m.imm32(addr, 1); m.a.movb_st(rv, 1)
def redraw(m): m.imm32(EVQ + 0x70, 1); m.a.mov_imm(1, 0); m.a.movl_st(0, 1)

def fire(m):
    a = m.a
    call(m, 'tk_bg')
    sti(m, 0, CHG)
    call(m, 'tk_slider')
    ld(m, ACT, 0); a.tst_rr(0, 0); en = m.newlabel('en'); wd = m.newlabel('wd'); m.bt_far(en)
    call(m, 'tk_wheel'); jmpl(m, wd)                                       # first: a field the wheel moved
    m.pool_jump()
    a.deflabel(en); sti(m, 1, ACT); sti(m, 1, CHG); m.imm32(0xFFFF, 0); st(m, 0, CUR)   # just opened: MPC values only
    a.deflabel(wd)
    call(m, 'tk_sync')                                                     # the MPC's values -> the fields
    # slider = the track, catch-up: it takes over only once it passes (or sits on) the track shown
    ld(m, TRF, 9); a.add_imm(-1, 9); ld(m, CUR, 0); a.cmpeq(9, 0); nrf = m.newlabel('nrf'); m.bt_far(nrf)
    st(m, 9, CUR); ld(m, SS, 10); a.cmpeq(9, 10); a.movt(0); st(m, 0, CAUGHT)   # track changed elsewhere: catch again
    a.cmphi(10, 9); a.movt(0); st(m, 0, SIDE)
    a.deflabel(nrf)
    fol, cat, sdone = m.newlabel('fol'), m.newlabel('cat'), m.newlabel('sdone')
    ld(m, CAUGHT, 0); a.tst_rr(0, 0); m.bf_far(fol)
    ld(m, CUR, 9); ld(m, SS, 10); a.cmpeq(9, 10); m.bt_far(cat)
    a.cmphi(10, 9); a.movt(2); ld(m, SIDE, 0); a.cmpeq(2, 0); m.bf_far(cat)
    jmpl(m, sdone); m.pool_jump()
    a.deflabel(cat); sti(m, 1, CAUGHT)
    a.deflabel(fol)
    ld(m, CUR, 9); ld(m, SS, 10); a.cmpeq(9, 10); m.bt_far(sdone)
    st(m, 10, CUR); a.mov_rm_rn(10, 5); m.imm32(ID_TRK, 4); call(m, 'tk_pset'); call(m, 'tk_sync')
    a.deflabel(sdone)
    call(m, 'tk_name')
    ld(m, CHG, 0); a.tst_rr(0, 0); nr = m.newlabel('nr'); m.bt_far(nr); redraw(m); a.deflabel(nr)
    jmpl(m, '__tk_end'); m.pool_jump()
    subs(m)
    a.deflabel('__tk_end')
pg.on_fire(fire)

def inactive(m): sti(m, 0, ACT); sti(m, 0, SINIT); call(m, 'tk_bg')     # SINIT: the slider's real position at once on return (ACT = 0 makes it catch again)
pg.on_inactive(inactive)

def subs(m):
    a = m.a
    # ---- tk_pget: r4 = id -> r0 = value; tk_pset: r4 = id, r5 = value. Through the MPC's parameter manager ----
    for nm, typ in (('tk_pget', 0x10), ('tk_pset', 0x11)):
        a.deflabel(nm); a.stsl_pr()
        m.imm32(MSGB[0], 1); m.imm32(typ, 0); a.movl_st(0, 1)
        if typ == 0x11: a.movl_st_disp(5, 4, 1)
        else: a.mov_imm(0, 0); a.movl_st_disp(0, 4, 1)
        a.mov_imm(0, 0); a.movl_st_disp(0, 8, 1); a.movl_st_disp(4, 12, 1)
        a.mov_rm_rn(1, 4); m.imm32(SYS_PARAM, 2); a.jsr(2); a.nop()
        m.imm32(MSGB[1], 1); a.movl_ld(1, 0)
        a.ldsl_pr(); a.rts(); a.nop()
        m.pool_jump()

    # ---- tk_bg (every fire, any page): the TRACK button (Main page in track view) -> this page ----
    a.deflabel('tk_bg'); a.stsl_pr()
    bx = m.newlabel('bx')
    m.imm32(EVQ + 0x68, 1); a.movl_ld(1, 9)
    m.imm32(MAINPG, 1); a.cmpeq(1, 9); m.bf_far(bx)
    m.imm32(VIEW, 1); a.movl_ld(1, 0); a.mov_imm(V_TRACK, 1); a.cmpeq(1, 0); m.bf_far(bx)
    a.movl_ld_disp(9, 0x1C, 0); m.imm32(VT_PAGE, 1); a.cmpeq(1, 0); m.bf_far(bx)
    a.movl_ld_disp(9, 0x30, 0); m.imm32(MAIN_H, 1); a.cmpeq(1, 0); m.bf_far(bx)
    m.imm32(EVQ, 4); a.mov_imm(0, 5); a.mov_imm(2, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()
    m.imm32(EVQ, 4); m.imm32(pg.addr['page'], 5); a.mov_imm(0, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()
    a.deflabel(bx)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tk_wheel: a field the wheel changed -> the MPC ----
    a.deflabel('tk_wheel'); a.stsl_pr()
    for F, L, ident, byte in ((TYPEF, LTYPE, ID_TYPE, True), (CHF, LCH, ID_CH, True), (TRF, LTRF, ID_TRK, False)):
        n = m.newlabel('wn')
        if byte: rdb(m, F, 5)
        else: ld(m, F, 5)
        ld(m, L, 0); a.cmpeq(5, 0); m.bt_far(n)
        if not byte: a.add_imm(-1, 5)                                          # track: the setter takes number - 1
        m.imm32(ident, 4); call(m, 'tk_pset')
        a.deflabel(n)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tk_sync: type, channel, track number -> the fields (CHG = 1 when something changed) ----
    a.deflabel('tk_sync'); a.stsl_pr()
    for F, L, ident, hi in ((TYPEF, LTYPE, ID_TYPE, 1), (CHF, LCH, ID_CH, 16)):
        m.imm32(ident, 4); call(m, 'tk_pget')
        c = m.newlabel('c'); a.mov_imm(hi, 1); a.cmphi(1, 0); a.bf_lbl(c); a.mov_imm(0, 0); a.deflabel(c)
        a.mov_rm_rn(0, 3)
        n = m.newlabel('sn'); ld(m, L, 1); a.cmpeq(1, 3); m.bt_far(n); sti(m, 1, CHG); a.deflabel(n)
        wrb(m, 3, F); st(m, 3, L)
    m.imm32(TRKNUM, 1); a.movw_ld(1, 3); a.extu_w(3, 3)
    n = m.newlabel('sn'); ld(m, LTRF, 1); a.cmpeq(1, 3); m.bt_far(n); sti(m, 1, CHG); a.deflabel(n)
    st(m, 3, TRF); st(m, 3, LTRF)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tk_name: "-" + the track's name (10 characters, spaces after it); CHG = 1 when it changed ----
    a.deflabel('tk_name'); a.stsl_pr()
    m.imm32(pg.addr['dlabels'][NAMEL], 12); a.mov_imm(0, 13)                  # r13 = 0: no name (spaces)
    m.imm32(CURSEQ, 1); a.movl_ld(1, 6)
    nn = m.newlabel('nn')
    m.imm32(ARENA_LO, 1); a.cmphs(1, 6); m.bf_far(nn); m.imm32(ARENA_HI, 1); a.cmphs(1, 6); m.bt_far(nn)
    m.imm32(TRKNUM, 1); a.movw_ld(1, 0); a.extu_w(0, 0); a.mov_imm(TRK_SIZE, 1); a.mul_l(1, 0); a.sts_macl(0)
    a.add_rr(0, 6); m.imm32(TRK_OFF, 1); a.add_rr(1, 6); a.mov_imm(1, 13)    # r6 = the track, its name
    a.deflabel(nn)
    a.mov_imm(ord('-'), 0); a.tst_rr(13, 13); h_ = m.newlabel('h'); a.bf_lbl(h_); a.mov_imm(ord(' '), 0); a.deflabel(h_)
    a.mov_rm_rn(12, 7); call(m, 'tk_put')
    a.add_imm(1, 12); a.mov_imm(NAMEW, 11)
    lp, sp_, put = m.newlabel('lp'), m.newlabel('sp'), m.newlabel('put')
    a.deflabel(lp)
    a.tst_rr(13, 13); a.bt_lbl(sp_)
    a.movb_ld(6, 0); a.extu_b(0, 0); a.add_imm(1, 6); a.tst_rr(0, 0); a.bf_lbl(put)
    a.mov_imm(0, 13)                                                          # end of the name: spaces after it
    a.deflabel(sp_); a.mov_imm(ord(' '), 0)
    a.deflabel(put); a.mov_rm_rn(12, 7); call(m, 'tk_put'); a.add_imm(1, 12)
    a.add_imm(-1, 11); a.tst_rr(11, 11); a.bf_lbl(lp)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tk_slider: SS = 0..NSTEP (filtered, learned range, hysteresis, exact ends; as Song / Program) ----
    a.deflabel('tk_slider'); a.stsl_pr()
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

    # ---- tk_put (leaf): r0 = character -> @r7; CHG = 1 when it differs. r1, r2 ----
    a.deflabel('tk_put')
    a.movb_ld(7, 1); a.extu_b(1, 1); a.cmpeq(0, 1); pz = m.newlabel('pz'); a.bt_lbl(pz)
    a.movb_st(0, 7); m.imm32(CHG, 2); a.mov_imm(1, 1); a.movl_st(1, 2)
    a.deflabel(pz); a.rts(); a.nop()
    m.pool_jump()

def ev_key(m, hret0, hret1):
    """PARAMS: the MPC's Track params page (as on the MPC's Track page)"""
    a = m.a
    kx = m.newlabel('kx')
    a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 10); a.extu_w(10, 10)       # (r9 = event type: keep)
    a.mov_imm(K_PARAMS, 0); a.cmpeq(0, 10); m.bf_far(kx)
    m.imm32(TRKPARAMS + 0x1C, 1); a.movl_ld(1, 0); m.imm32(VT_PAGE, 1); a.cmpeq(1, 0); m.bf_far(kx)
    m.imm32(EVQ, 4); m.imm32(TRKPARAMS, 5); a.mov_imm(0, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()
    jmpl(m, hret1)
    m.pool_jump()
    a.deflabel(kx)
pg.on_event(2, ev_key)


# =====================================================================================================================
def selftest(out):
    from validate import Harness
    from mpcmod import HOOKS
    from pages import ENABLE_DOIT, ENABLE_CANCEL
    from testkit import PageTest, FIRMWARE, EV_KEY
    VT_LABEL, VT_VALUE, VT_COMPOSITE = 0x88507C44, 0x88507C8C, 0x88507D1C
    LOG = 0x88E00000
    SEQ = ARENA_LO + 0x1000

    class OSTest(PageTest):
        """PageTest with the OS image: the MPC's REAL parameter manager / sequence getter and setter run"""
        def __init__(self, pg, wav):
            self.pg = pg
            self.h = h = Harness(FIRMWARE); h.fill_scratch(); self.e = e = h.emu
            fw = open(FIRMWARE, 'rb').read(); OSB = 0x8849495C
            keep = [(HOOKS[h.hook][1], 4), (ENABLE_DOIT, 10), (ENABLE_CANCEL, 10)]
            saved = [(ad, [e.rd(ad + i, 2) for i in range(0, n, 2)]) for ad, n in keep]
            buf, off = e._region(OSB); buf[off:off + len(fw)] = fw
            for ad, ws in saved:
                for i, w in enumerate(ws): e.wr(ad + 2 * i, w, 2)
            for ad in (0x884C0076, 0x884C67BA, 0x884B0D84, 0x884B3C32): self.stub(ad)       # undo / refresh after a set
            self.stub(OS_PUSH_PAGE, lambda s: (s.movl_pool(LOG, 1), s.movl_ld(1, 0), s.add_imm(1, 0), s.movl_st(0, 1),
                                               s.shll2(0), s.shll2(0), s.add_rr(0, 1), s.movl_st(5, 1),
                                               s.movl_st_disp(6, 4, 1), s.rts(), s.nop()))
            h.load_hub(); h.load_wav(wav); h.fire(10)

    t = OSTest(pg, out); h, e = t.h, t.e
    R32, W32 = h.ram_r32, h.ram_w32
    def pushes(): return [(R32(LOG + 16 * (k + 1)), R32(LOG + 16 * (k + 1) + 4)) for k in range(R32(LOG))]
    # a sequence with named tracks; track 2 = MIDI on channel 10
    W32(CURSEQ, SEQ)
    for n in range(1, 49):
        b = SEQ + TRK_OFF + n * TRK_SIZE
        for i in range(TRK_SIZE): e.wr(b + i, 0, 1)
        for i, c in enumerate(b"Track%02d" % n if n != 3 else b"BassLine Long"): e.wr(b + i, c, 1)
    trk = lambda n: SEQ + TRK_OFF + n * TRK_SIZE
    e.wr(trk(2) + TYPE_OFF, 1, 1); e.wr(trk(2) + CH_OFF, 10, 1)
    h.ram_w16(TRKNUM, 1)
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
    shots = {}

    # ===== the TRACK button: Main page in track view -> this page =====
    W32(MAINPG + 0x1C, VT_PAGE); W32(MAINPG + 0x30, MAIN_H)
    W32(VIEW, 0); W32(LOG, 0); h.ram_w32(EVQ + 0x68, MAINPG); t.fire(3)
    assert pushes() == [], ("Main page, sequence view: left alone", pushes())
    W32(VIEW, V_TRACK); t.fire(1); assert pushes() == [(0, 2), (pg.page, 0)], pushes()
    W32(MAINPG + 0x30, 0x12345678); W32(LOG, 0); t.fire(2); assert pushes() == [], "another object there: left alone"
    W32(MAINPG + 0x30, MAIN_H); W32(VIEW, 0)
    print("  ok TRACK button: the Main page in track view is replaced (back, then this page); sequence view untouched")

    # ===== the page shows the MPC's values =====
    t.open(); t.fire(3)
    assert screen() == ["DRUM    Ch:OFF  ", "Tr:01-Track01   "], screen(); shots["track 1"] = screen()
    W32(TRF, 2); t.fire(3)
    assert R32(TRKNUM) & 0xFFFF == 2 and screen() == ["MIDI    Ch: 10  ", "Tr:02-Track02   "], (R32(TRKNUM), screen())
    shots["track 2 (MIDI)"] = screen()
    W32(TRF, 3); t.fire(3); assert screen()[1] == "Tr:03-BassLine L", screen()
    W32(TRF, 70); t.fire(3); assert R32(TRKNUM) & 0xFFFF == 48 and R32(TRF) == 48, ("past the last track: the MPC's limit", R32(TRKNUM) & 0xFFFF)
    W32(TRF, 1); t.fire(3); assert R32(TRKNUM) & 0xFFFF == 1 and screen()[1] == "Tr:01-Track01   ", screen()
    print("  ok track: wheel 2 (MIDI, ch 10, name follows), 3 (long name cut at 10), 70 -> the MPC's last track, 1")

    # ===== wheel on type / channel -> the MPC's settings =====
    e.wr(TYPEF, 1, 1); t.fire(3); assert e.rd(trk(1) + TYPE_OFF, 1) == 1 and screen()[0].startswith("MIDI"), screen()
    e.wr(CHF, 5, 1); t.fire(3); assert e.rd(trk(1) + CH_OFF, 1) == 5 and screen()[0] == "MIDI    Ch:  5  ", screen()
    e.wr(CHF, 16, 1); t.fire(3); assert e.rd(trk(1) + CH_OFF, 1) == 16 and screen()[0] == "MIDI    Ch: 16  ", screen()
    e.wr(CHF, 0, 1); e.wr(TYPEF, 0, 1); t.fire(3)
    assert e.rd(trk(1) + CH_OFF, 1) == 0 and e.rd(trk(1) + TYPE_OFF, 1) == 0 and screen()[0] == "DRUM    Ch:OFF  ", screen()
    assert e.rd(trk(2) + TYPE_OFF, 1) == 1 and e.rd(trk(2) + CH_OFF, 1) == 10, "other tracks untouched"
    # changed elsewhere (Track params): shown here
    e.wr(trk(1) + CH_OFF, 3, 1); e.wr(trk(1), ord('K'), 1); t.fire(3)
    assert screen() == ["DRUM    Ch:  3  ", "Tr:01-Krack01   "], screen()
    print("  ok type DRUM/MIDI and channel OFF/5/16 written with the MPC's own setter; changes made elsewhere shown")

    # ===== slider: the track (catch-up) =====
    def slider(k, fires=60):
        lo, hi = t.slot(SLO), t.slot(SHI); W32(SLIDER, lo + round(k * (hi - lo) / (MAXTRK - 1))); t.fire(fires)
    trk_now = lambda: R32(TRKNUM) & 0xFFFF
    slider(0); assert trk_now() == 1
    slider(5); assert trk_now() == 6 and screen()[1] == "Tr:06-Track06   ", (trk_now(), screen())
    slider(MAXTRK - 1); assert trk_now() == MAXTRK, trk_now()
    slider(2); assert trk_now() == 3 and screen()[1] == "Tr:03-BassLine L", screen()
    W32(TRF, 20); t.fire(3); assert trk_now() == 20
    slider(10); assert trk_now() == 20, ("wheel moved it: the slider below waits (catch-up)", trk_now())
    slider(19); assert trk_now() == 20
    slider(25); assert trk_now() == 26, ("passed 20: it follows again", trk_now())
    slider(0); assert trk_now() == 1 and screen()[1] == "Tr:01-Krack01   ", screen()
    # page left, slider moved on another page, page entered again: the value stays until the slider catches it (user 2026-11-08)
    slider(5); assert trk_now() == 6
    t.close(); W32(SLIDER, t.slot(SHI)); t.fire(20); t.open(); t.fire(60); assert trk_now() == 6, ("re-entry keeps the track", trk_now())
    slider(20); assert trk_now() == 6, ("above it: not caught yet", trk_now())
    slider(3); assert trk_now() == 4, ("passed it: caught", trk_now())
    slider(0); assert trk_now() == 1
    print("  ok slider: tracks 1, 6, 48, 3; after the wheel (20) it waits until it passes 20, then 26, 1")

    # ===== PARAMS -> Track params =====
    W32(LOG, 0); assert t.event(EV_KEY, K_PARAMS) == 0 and pushes() == [], "Track params not there: the OS handles PARAMS"
    W32(TRKPARAMS + 0x1C, VT_PAGE)
    W32(LOG, 0); assert t.event(EV_KEY, K_PARAMS) == 1 and pushes() == [(TRKPARAMS, 0)], pushes()
    print("  ok PARAMS opens the MPC's Track params page")
    # no sequence: no name, nothing breaks
    W32(CURSEQ, 0); t.fire(3); assert screen()[1] == "Tr:01           ", screen()
    print("\nScreens (16x2):")
    for k, (r0, r1) in shots.items(): print("  %-24s |%s|\n  %-24s |%s|" % (k, r0, "", r1))
    print("OK: TRACK.WAV built and tested")


if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', '..', 'TRACK.WAV'); pg.build_wav(out); selftest(out)
