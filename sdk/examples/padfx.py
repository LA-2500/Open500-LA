#!/usr/bin/env python3
"""PAD FX -> PADFX.WAV: a menu of effect add-ons that MODE + pad 9 (the MPC's "Eff Send" page) opens instead.

    python sdk/examples/padfx.py           -> builds PADFX.WAV and tests it in the emulator

On the MPC500: copy PADFX.WAV to the CF card, LOAD it (after HUB.WAV, with the effect add-ons). MODE + pad 9 (or
MODE -> OTHER -> "Pad FX") opens:
    row 0 "REVERB" ... "ENTER DEFAULT"  the list (wheel): the effect add-ons that are loaded, then the MPC page
    row 1 "DO IT = SELECT" on the first item only (user), "MPC FX MODE" under ENTER DEFAULT, blank otherwise
  DO IT opens the item; CANCEL there comes back here (OS page stack). MPC EFFECTS = the MPC's own Eff Send page.
  Effect add-ons are found in the HUB slot table by their PageAddon name (loaded later = listed at once); one not
  loaded is skipped by the wheel. To add one: a line in ITEMS (label, PageAddon name).

MODE + pad 9: a page opened straight from the MODE page (0x8849C9C4, page-objects / S500 menu, HW) whose first display
  line, within WATCH fires, starts "Eff send" (user's MPC 2026-11-03: "Eff Send       A" / "A9:Off"; fw string
  0x88504980 "Eff send", compared with the S case-free) -> back + this menu, and that page is remembered (next time it is
  swapped at once; MPC EFFECTS opens it, and opened from here it stays). Display = LCD shadow 0x88483E9C (32 chars sent
  to the LCD, MIDI Screen, HW). Until it was seen once, MPC EFFECTS says "PRESS MODE+PAD9".
  HUB slot table: S = *(0x8848ED34) + 0x100: INST +0x44+4k (1 = installed), PAGE +0xC4+4k, NAME +0x1C4+8k.
"""
import sys, os, struct
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import PageAddon, ld, st, sti
from mpcmod import EVQ, OS_PUSH_PAGE, LCD_SHADOW

MODEPG = 0x8849C9C4
LDR_B = 0x8848ED34
WATCH = 40
# the effect add-ons, in list order: (label, PageAddon name, published page). MPC EFFECTS is always last.
# published page = None: the add-on's page (HUB PAGE[k]); an offset: a second page of that add-on, whose address the
# add-on writes at that offset of its scratch (HUB SCR[k]): INPUT FX inside MASTER.WAV (scratch + 0x44, 2026-11-04).
# 2026-11-06: PRINT FX = PRINTFX.WAV (reverb + EQ; HUB name still reverb1)
ITEMS = [("PRINT FX", "reverb1", None), ("INPUT FX", "master1", 0x44), ("MASTER FX", "master1", None)]
LABELS = [l.ljust(16) for l, _, _ in ITEMS] + ["ENTER DEFAULT   "]
NA = len(ITEMS); I_MPC = NA

pg = PageAddon("padfx1", open_press=None)
pg.other_menu("Pad FX")
SELI = pg.choice("", 0, 0, LABELS, default=0)
R1 = pg.dlabel(0, 1, 16)                                   # "DO IT = SELECT" (first item) / "MPC FX MODE" / blank
pg.message()
pg.far_hend = True
pg.lean_pools = True                                       # 2026-11-04: INPUT FX item; stays in one 4 KB page
PREVPG, CAND, CWAIT, EFFPG, LASTSEL = [pg.alloc_slot() for _ in range(5)]
APG = [pg.alloc_slot() for _ in range(NA)]                 # page of each effect add-on (0 = not loaded)

def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()          # clobbers r2
def jmpl(m, lbl): m.a.movl_pool(lbl, 2); m.a.jmp(2); m.a.nop()
def push(m, rpage):
    m.imm32(EVQ, 4); m.a.mov_rm_rn(rpage, 5); m.a.mov_imm(0, 6); m.imm32(OS_PUSH_PAGE, 2); m.a.jsr(2); m.a.nop()

def fire(m):
    a = m.a
    call(m, '__pfbg')
    # the wheel skips add-ons that are not loaded, in the direction it turned
    # (the direction is decided ONCE: down if the wheel went down, else up; below the first entry -> up)
    lp = m.newlabel('wl'); ok = m.newlabel('ok'); ds_ = m.newlabel('ds')
    m.imm32(SELI, 1); a.movb_ld(1, 9); a.extu_b(9, 9); a.mov_rm_rn(9, 11)       # r11 = as found
    a.mov_imm(1, 10); ld(m, LASTSEL, 0); a.cmpgt(9, 0); a.bf_lbl(ds_); a.mov_imm(-1, 10); a.deflabel(ds_)   # last > sel: down
    a.deflabel(lp)
    a.mov_imm(I_MPC, 0); a.cmpeq(0, 9); m.bt_far(ok)
    a.mov_rm_rn(9, 0); a.shll2(0); m.imm32(APG[0], 1); a.add_rr(0, 1); a.movl_ld(1, 0); a.tst_rr(0, 0); m.bf_far(ok)
    a.add_rr(10, 9); a.cmppz(9); nz = m.newlabel('nz'); a.bt_lbl(nz); a.mov_imm(0, 9); a.mov_imm(1, 10); a.deflabel(nz)
    jmpl(m, lp)
    m.pool_jump()
    a.deflabel(ok); st(m, 9, LASTSEL)
    a.cmpeq(9, 11); same_ = m.newlabel('same'); m.bt_far(same_)
    m.imm32(SELI, 1); a.movb_st(9, 1); m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(same_)
    row1(m)
    jmpl(m, '__pf_end'); m.pool_jump()
    subs(m)
    a.deflabel('__pf_end')
pg.on_fire(fire)

def row1(m):
    """row 1 for the item in r9: the first listed add-on "DO IT = SELECT", ENTER DEFAULT "MPC FX MODE", else blank"""
    a = m.a
    dn = m.newlabel('r1d'); txt = m.newlabel('r1t')
    def put(s):                                   # write s (16) into R1 where it differs; r14 = 1 if anything changed
        m.imm32(pg.addr['dlabels'][R1], 12)
        for ch in s.ljust(16):
            n = m.newlabel('cp'); a.movb_ld(12, 1); a.mov_imm(ord(ch), 0); a.cmpeq(0, 1); a.bt_lbl(n)
            a.movb_st(0, 12); a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
        m.pool_jump()
    a.mov_imm(0, 14)
    blank, sel, mpc = m.newlabel('bl'), m.newlabel('se'), m.newlabel('mp')
    a.mov_imm(I_MPC, 0); a.cmpeq(0, 9); m.bt_far(mpc)
    for i in range(NA):
        nx = m.newlabel('ri'); a.mov_imm(i, 0); a.cmpeq(0, 9); m.bf_far(nx)
        for j in range(i):                         # an earlier add-on is listed: not the first page
            ld(m, APG[j], 0); a.tst_rr(0, 0); m.bf_far(blank)
        jmpl(m, sel)
        a.deflabel(nx)
    a.deflabel(blank); put(""); jmpl(m, dn)
    a.deflabel(sel); put("DO IT = SELECT"); jmpl(m, dn)
    a.deflabel(mpc); put("MPC FX MODE")
    a.deflabel(dn)
    a.tst_rr(14, 14); px = m.newlabel('px'); m.bt_far(px); m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(px)

def inactive(m):
    call(m, '__pfbg')
pg.on_inactive(inactive)

def subs(m):
    a = m.a
    # ---- __pfat: APG[i] = page of each effect add-on from the HUB slot table (0 = not loaded). r0-r7 ----
    a.deflabel('__pfat')
    for i, (_, name, pub) in enumerate(ITEMS):
        nf, lp, nx = m.newlabel('nf'), m.newlabel('lp'), m.newlabel('nx')
        w0, w1 = struct.unpack('<II', name.encode().ljust(8))
        a.mov_imm(0, 7)
        m.imm32(LDR_B, 1); a.movl_ld(1, 6); a.tst_rr(6, 6); m.bt_far(nf)
        m.imm32(0x100, 0); a.add_rr(0, 6)                                    # r6 = HUB slot tables
        a.mov_imm(0, 5)
        a.deflabel(lp)
        a.mov_rm_rn(5, 4); a.shll2(4); a.add_rr(6, 4)
        m.imm32(0x44, 0); a.add_rr(4, 0); a.movl_ld(0, 0)
        a.mov_imm(1, 1); a.cmpeq(1, 0); m.bf_far(nx)                         # INST[k] == 1
        a.mov_rm_rn(5, 4); a.shll2(4); a.shll(4); a.add_rr(6, 4); m.imm32(0x1C4, 0); a.add_rr(0, 4)   # NAME[k]
        a.movl_ld(4, 0); m.imm32(w0, 1); a.cmpeq(1, 0); m.bf_far(nx)
        a.movl_ld_disp(4, 4, 0); m.imm32(w1, 1); a.cmpeq(1, 0); m.bf_far(nx)
        if pub is None:
            a.mov_rm_rn(5, 4); a.shll2(4); a.add_rr(6, 4); m.imm32(0xC4, 0); a.add_rr(0, 4); a.movl_ld(4, 7)   # PAGE[k]
        else:
            a.mov_rm_rn(5, 4); a.shll2(4); a.add_rr(6, 4); m.imm32(0x384, 0); a.add_rr(0, 4); a.movl_ld(4, 7)  # SCR[k]
            m.imm32(pub, 0); a.add_rr(0, 7); a.movl_ld(7, 7)                                              # published
            bad, good = m.newlabel('pb'), m.newlabel('pg')                    # an older add-on keeps a setting there
            m.imm32(0xDFFFFFFF, 0); a.and_rr(7, 0); a.mov_rm_rn(0, 3)                   # P1 or P2 alias of SDRAM
            m.imm32(0x88000000, 0); a.cmphs(0, 3); a.bf_lbl(bad); m.imm32(0x98000000, 0); a.cmphs(0, 3); a.bf_lbl(good)
            a.deflabel(bad); a.mov_imm(0, 7); a.deflabel(good)
        a.bra_lbl(nf); a.nop()
        a.deflabel(nx); a.add_imm(1, 5); a.mov_imm(16, 0); a.cmpeq(0, 5); m.bf_far(lp)
        a.deflabel(nf); m.imm32(APG[i], 1); a.movl_st(7, 1)
        m.pool_jump()
    a.rts(); a.nop()
    m.pool_jump()

    # ---- __pfeff: T = the display's first line starts "Eff send" (S either case). r0-r1 ----
    a.deflabel('__pfeff')
    no = m.newlabel('no')
    for i, ch in enumerate("Eff send"):
        m.imm32(LCD_SHADOW + i, 1); a.movb_ld(1, 0); a.extu_b(0, 0)
        if ch == 's': a.or_imm(0x20)
        m.imm32(ord(ch), 1); a.cmpeq(1, 0); a.bf_lbl(no)
    a.deflabel(no); a.rts(); a.nop()
    m.pool_jump()

    # ---- __pfbg (every fire, any page): add-on lookup; MODE + pad 9 -> this menu ----
    a.deflabel('__pfbg'); a.stsl_pr()
    a.bsr_lbl('__pfat'); a.nop()
    m.imm32(EVQ + 0x68, 1); a.movl_ld(1, 9)                                  # r9 = current page
    ld(m, PREVPG, 0); a.cmpeq(9, 0); same = m.newlabel('same'); m.bt_far(same)
    a.mov_rm_rn(0, 8); st(m, 9, PREVPG); sti(m, 0, CWAIT)                    # r8 = the page before
    m.imm32(MODEPG, 1); a.cmpeq(1, 8); m.bf_far(same)                        # opened straight from MODE
    m.imm32(pg.addr['page'], 1); a.cmpeq(1, 9); m.bt_far(same)
    ld(m, EFFPG, 0); a.cmpeq(9, 0); known = m.newlabel('known'); m.bt_far(known)   # already known: swap now
    st(m, 9, CAND); sti(m, WATCH, CWAIT)
    a.deflabel(same)
    # watching a candidate: its screen says "Eff send" -> it is the MPC's effects page
    ld(m, CWAIT, 0); a.tst_rr(0, 0); nw = m.newlabel('nw'); m.bt_far(nw)
    a.add_imm(-1, 0); st(m, 0, CWAIT)
    ld(m, CAND, 0); a.cmpeq(9, 0); m.bf_far(nw)
    a.bsr_lbl('__pfeff'); a.nop(); m.bf_far(nw)
    st(m, 9, EFFPG)
    a.deflabel(known)
    sti(m, 0, CWAIT)
    m.imm32(EVQ, 4); a.mov_imm(0, 5); a.mov_imm(2, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()   # back
    m.imm32(pg.addr['page'], 8); push(m, 8)
    m.imm32(pg.addr['page'], 0); st(m, 0, PREVPG)
    a.deflabel(nw)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

def doit(m):
    """DO IT = open the chosen page (CANCEL there comes back here)"""
    a = m.a
    ex = m.newlabel('ex')
    m.imm32(SELI, 1); a.movb_ld(1, 9); a.extu_b(9, 9)
    for i in range(NA):
        nx = m.newlabel('it'); a.mov_imm(i, 0); a.cmpeq(0, 9); m.bf_far(nx)
        ld(m, APG[i], 8); a.tst_rr(8, 8); nl = m.newlabel('nl'); m.bt_far(nl); push(m, 8); jmpl(m, ex)
        a.deflabel(nl); pg.emit_message(m, "NOT LOADED"); jmpl(m, ex)
        m.pool_jump()
        a.deflabel(nx)
    ld(m, EFFPG, 8); a.tst_rr(8, 8); nr = m.newlabel('nr'); m.bt_far(nr); push(m, 8); jmpl(m, ex)
    a.deflabel(nr); pg.emit_message(m, "PRESS MODE+PAD9")
    a.deflabel(ex)
pg.on_doit(doit)


def selftest(out):
    from validate import Harness
    from pages import SET_FOCUS, ENABLE_DOIT
    from shasm import SHAsm
    LOG, MSG = 0x88E00000, 0x88E10000
    h = Harness(os.path.join(os.path.dirname(__file__), '..', 'mpc500_loader.bin')); h.fill_scratch(); e = h.emu
    def stub(addr, fn):
        s = SHAsm(addr); fn(s); blob = s.assemble()
        for i in range(0, len(blob), 2): h.ram_w16(addr + i, struct.unpack('<H', blob[i:i+2])[0])
    stub(OS_PUSH_PAGE, lambda s: (s.movl_pool(LOG, 1), s.movl_ld(1, 0), s.add_imm(1, 0), s.movl_st(0, 1), s.shll2(0), s.shll2(0),
                                  s.add_rr(0, 1), s.movl_st(5, 1), s.movl_st_disp(6, 4, 1), s.rts(), s.nop()))
    stub(ENABLE_DOIT, lambda s: (s.rts(), s.nop()))
    stub(SET_FOCUS, lambda s: (s.rts(), s.nop()))
    stub(0x884E5CD0, lambda s: (s.movl_pool(1200, 2), s.movl_st_disp(2, 4, 4), s.mov_imm(1, 0), s.rts(), s.nop()))  # tempo (MASTER)
    h.load_hub(); h.load_wav(out); h.fire(10); assert h.ram_r32(0x88F00144) == 1, "add-on not installed"
    R32, W32 = h.ram_r32, h.ram_w32
    from mpcmod import LOAD_SCRATCH
    L = h.hub_lay(); k0 = [k for k in range(16) if bytes(e.rd(L.NAME(k) + i, 1) for i in range(8)) == b"padfx1  "][0]
    SCR = R32(L.SCR(k0))
    def V(slot): return SCR + (slot - LOAD_SCRATCH)        # the real scratch (no mirror once a 2nd add-on is in)
    def pushes(): return [(R32(LOG + 16 * (k + 1)), R32(LOG + 16 * (k + 1) + 4)) for k in range(R32(LOG))]
    def call(obj, type_, code=0, val=0):
        W32(MSG, (type_ & 0xffff) | (code << 16)); W32(MSG + 4, val & 0xffffffff); W32(MSG + 8, 0); W32(MSG + 12, 0)
        regs = [0x22220000 + i for i in range(16)]; regs[15] = 0x88400000 - 256; regs[4] = obj; regs[5] = MSG
        e.r = regs[:]; e.pr = 0xDEADBEEE; e.pc = pg.handler; e.maxsteps = 5_000_000
        assert e.run(stop_pc=0xDEADBEEE), "handler did not return"
        for i in range(8, 16): assert e.r[i] == regs[i], "r%d corrupted" % i
        return e.r[0]
    msg = lambda: bytes(e.rd(pg.msgt + i, 1) for i in range(16)).split(b'\0')[0].decode().rstrip()
    doit = lambda: (W32(LOG, 0), call(pg.page, 2, code=0x2E))
    def screen(s):
        for i, c in enumerate(s.ljust(32).encode()): e.wr(LCD_SHADOW + i, c, 1)
    # --- menu without Reverb: REVERB skipped ---
    h.ram_w32(EVQ + 0x68, pg.page); h.fire(3)
    assert e.rd(V(SELI), 1) == I_MPC, ("Reverb not loaded: starts on MPC EFFECTS", e.rd(V(SELI), 1))
    e.wr(V(SELI), 0, 1); h.fire(2); assert e.rd(V(SELI), 1) == I_MPC, "the wheel can't stay on REVERB"
    r1 = lambda: bytes(e.rd(pg.dlabels[R1] + i, 1) for i in range(16)).decode()
    assert r1() == "MPC FX MODE     ", repr(r1())
    doit(); assert pushes() == [] and msg() == "PRESS MODE+PAD9", (pushes(), msg())
    print("  ok menu: REVERB hidden while Reverb is not loaded; MPC EFFECTS unknown yet -> PRESS MODE+PAD9")
    # --- Reverb loaded later: found in the HUB table at once ---
    rvwav = os.path.join(os.path.dirname(__file__), '..', '..', 'PRINTFX.WAV')
    h.ram_w32(EVQ + 0x68, 0x88123456); h.fire(2); h.load_wav(rvwav); h.fire(20)
    L = h.hub_lay(); rk = [k for k in range(16) if R32(L.INST(k)) == 1 and bytes(e.rd(L.NAME(k) + i, 1) for i in range(8)) == b"reverb1 "]
    assert rk, "Reverb installed"; rvpage = R32(L.PAGE(rk[0]))
    h.ram_w32(EVQ + 0x68, pg.page); h.fire(2); e.wr(V(SELI), 0, 1); h.fire(2); assert e.rd(V(SELI), 1) == 0 and r1() == "DO IT = SELECT  ", repr(r1())
    doit(); assert pushes() == [(rvpage, 0)], (pushes(), hex(rvpage), msg(), hex(R32(V(APG[0]))))
    print("  ok Reverb loaded later: REVERB listed at once, DO IT opens its page (%08x, HUB slot %d)" % (rvpage, rk[0]))
    # --- Master loaded too: the second item, row 1 blank (only the first item says DO IT = SELECT) ---
    mswav = os.path.join(os.path.dirname(__file__), '..', '..', 'MASTER.WAV')
    h.ram_w32(EVQ + 0x68, 0x88123456); h.fire(2); h.load_wav(mswav); h.fire(20)
    mk = [k for k in range(16) if R32(L.INST(k)) == 1 and bytes(e.rd(L.NAME(k) + i, 1) for i in range(8)) == b"master1 "]
    assert mk, "Master installed"; mspage = R32(L.PAGE(mk[0]))
    ixpage = R32(R32(L.SCR(mk[0])) + 0x44); assert 0x88000000 <= ixpage & 0xDFFFFFFF < 0x98000000 and ixpage != mspage, hex(ixpage)
    h.ram_w32(EVQ + 0x68, pg.page); h.fire(2); e.wr(V(SELI), 2, 1); h.fire(2)
    assert e.rd(V(SELI), 1) == 2 and r1() == " " * 16, (e.rd(V(SELI), 1), repr(r1()))
    doit(); assert pushes() == [(mspage, 0)], (pushes(), hex(mspage))
    e.wr(V(SELI), 1, 1); h.fire(2); assert e.rd(V(SELI), 1) == 1 and r1() == " " * 16
    doit(); assert pushes() == [(ixpage, 0)], (pushes(), hex(ixpage))
    e.wr(V(SELI), 0, 1); h.fire(2); assert r1() == "DO IT = SELECT  ", repr(r1())
    print("  ok Master loaded: INPUT FX second (opens MASTER's INPUT FX page %08x), MASTER FX third (%08x)" % (ixpage, mspage))
    # --- MODE + pad 9: the page from MODE whose screen says "Eff Send" -> back + this menu ---
    EFF, OTH = 0x88491330, 0x88491000
    screen("  select mode"); h.ram_w32(EVQ + 0x68, MODEPG); h.fire(2)
    W32(LOG, 0); h.ram_w32(EVQ + 0x68, OTH); h.fire(2); screen("Program Select    3:BEATS"); h.fire(WATCH + 5)
    assert pushes() == [], "another MODE pad: left alone"
    screen("Eff Send       A  A9:Off"); h.fire(3); assert pushes() == [], "too late / not from MODE: left alone"
    h.ram_w32(EVQ + 0x68, MODEPG); screen("  select mode"); h.fire(2)
    h.ram_w32(EVQ + 0x68, EFF); h.fire(2); assert pushes() == [], "screen not drawn yet"
    screen("Eff Send       A  A9:Off"); h.fire(1)
    assert pushes() == [(0, 2), (pg.page, 0)] and R32(V(EFFPG)) == EFF, (pushes(), hex(R32(V(EFFPG))))
    h.ram_w32(EVQ + 0x68, pg.page); h.fire(2)
    print("  ok MODE+pad9: the page from MODE that shows \"Eff Send\" -> back + this menu; other MODE pads untouched")
    # next time it is swapped at once; MPC EFFECTS opens it, and from here it is not swapped
    h.ram_w32(EVQ + 0x68, MODEPG); screen("  select mode"); h.fire(2); W32(LOG, 0); h.ram_w32(EVQ + 0x68, EFF); h.fire(1)
    assert pushes() == [(0, 2), (pg.page, 0)], pushes()
    h.ram_w32(EVQ + 0x68, pg.page); h.fire(2)
    e.wr(V(SELI), I_MPC, 1); h.fire(1); assert r1() == "MPC FX MODE     ", repr(r1()); doit(); assert pushes() == [(EFF, 0)], pushes()
    W32(LOG, 0); h.ram_w32(EVQ + 0x68, EFF); screen("Eff Send       A  A9:Off"); h.fire(5); assert pushes() == [], "MPC EFFECTS from the menu: stays"
    print("  ok known effects page swapped at once; MPC EFFECTS opens the MPC page (and it stays)")
    # wheel down from MPC EFFECTS with Reverb loaded -> REVERB; nothing else in the HUB disturbed
    h.ram_w32(EVQ + 0x68, pg.page); h.fire(2); e.wr(V(SELI), 0, 1); h.fire(2); assert e.rd(V(SELI), 1) == 0
    assert R32(L.INST(rk[0])) == 1, "Reverb still installed"
    print("OK: PAD FX (REVERB + MPC EFFECTS, MODE+pad9) -> %s" % os.path.basename(out))


if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', '..', 'PADFX.WAV'); pg.build_wav(out); selftest(out)
