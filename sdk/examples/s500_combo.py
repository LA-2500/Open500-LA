#!/usr/bin/env python3
"""S500 (combined) -> S500.WAV: S500 Input (sampling, s500_input.py) and S500 Convert (pad conversion, s500.py) in ONE
add-on, plus a link to the Audio Track add-on and to the MPC's own Record page. Built like Chop Shop
(sdk/addons/wav_chopshop.py): a menu page + the two S500 pages hosted unchanged (pages.HOST + page_or_addon), same code
and same tests (each module's selftest runs on this WAV).

    python sdk/examples/s500_combo.py      -> builds S500.WAV and runs all the tests on it

  menu page:  row 0 "AUDIO TRACK" / "S500 INPUT" / "S500 CONVERT" / "RESAMPLE" / "MPC RECORD" (wheel), row 1 "DO IT = SELECT"
    AUDIO TRACK only when the Audio Track add-on is loaded (found in the HUB slot table by its name "audtrk1"; loaded
    later = there at once). MPC RECORD = the OS Record page. DO IT opens it; CANCEL there comes back (OS page stack).
    RESAMPLE = the second page of AUDTRACK.WAV (its address is at that add-on's scratch + 0x44).
    S500 INPUT goes through "SELECT INPUT" / REC IN or MAIN OUT: DO IT opens S500 Input with that Record source. It only
    applies to S500 Input: the sampler's own setting comes back when the page is left and no take is running.
  MODE + pad 4 (Record) opens this menu instead of the OS Record page: a page opened straight from the MODE page
    (0x8849C9C4, the page the OS key handler 0x884E4E74 pushes for MODE: emulator run 2026-11-02) that switches the
    OS sampler on (SMP+8 0 -> 1 within WATCH fires) is the Record page: we go back and open the menu (and give the
    sampler back), and remember that page for MPC RECORD. If the guess is wrong on the MPC: nothing happens (the OS
    Record page shows as before). Until it was seen once, MPC RECORD says "PRESS MODE+PAD4".
  HUB slot table (hub.py Lay): S = B + 0x100, B = *(0x8848ED34): INST +0x44+4k, PAGE +0xC4+4k, NAME +0x1C4+8k.
"""
import sys, os, struct
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pages
from pages import PageAddon, ld, st, sti
from mpcmod import EVQ, OS_PUSH_PAGE, SLOT_CODE_MAX

MODEPG = 0x8849C9C4
SMP, SMP_EXIT = 0x8849D114, 0x884EC0EE
LDR_B = 0x8848ED34
AT_NAME = b"audtrk1 "                    # HUB NAME[k]: the PageAddon name, space padded
WATCH = 40

host = PageAddon("s500", open_press=None)
host.other_menu("S500")
host.lean_pools = True
MODES = ["AUDIO TRACK ", "S500 INPUT  ", "S500 CONVERT", "RESAMPLE    ", "MPC RECORD  "]
M_RESAMPLE, M_MPC = 3, 4
MODE = host.choice("", 0, 0, MODES, default=0)
host.label("DO IT = SELECT", 0, 1)
host.message()
host.far_hend = True
pages.HOST = host
import s500 as S9, s500_input as SI
pages.HOST = None
assert all(mod.pg.host is host for mod in (S9, SI)), "a module was already imported on its own: import s500_combo first"
PREVPG, CAND, CWAIT, RECPG, GIVEBACK, ATPG, LASTSMP, RSPG, SVSRC, SVON = [host.alloc_slot() for _ in range(10)]
pgS = host.add_page("selin")                 # the input of S500 Input
pgS.label("SELECT INPUT", 0, 0)
SRCS = pgS.choice("", 0, 1, ["REC IN  ", "MAIN OUT"], default=0)

def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()
def jmpl(m, lbl): m.a.movl_pool(lbl, 2); m.a.jmp(2); m.a.nop()
def push(m, rpage):
    m.imm32(EVQ, 4); m.a.mov_rm_rn(rpage, 5); m.a.mov_imm(0, 6); m.imm32(OS_PUSH_PAGE, 2); m.a.jsr(2); m.a.nop()

def fire(m):
    a = m.a
    call(m, '__s9bg0')
    # AUDIO TRACK only when loaded: the wheel skips it
    ld(m, ATPG, 0); a.tst_rr(0, 0); ok = m.newlabel('ok'); m.bf_far(ok)
    m.imm32(MODE, 1); a.movb_ld(1, 0); a.tst_rr(0, 0); m.bf_far(ok)
    a.mov_imm(1, 0); a.movb_st(0, 1); m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.deflabel(ok)
    jmpl(m, '__s9m_end0'); m.pool_jump()
    subs(m)
    a.deflabel('__s9m_end0')
host.on_fire(fire)

def inactive(m):
    call(m, '__s9bg0')
host.on_inactive(inactive)

def subs(m):
    a = m.a
    # ---- __s9at0: ATPG = the Audio Track page from the HUB slot table (0 = not loaded). r0-r7 ----
    a.deflabel('__s9at0')
    nf, lp, nx, nf2, rok = m.newlabel('nf'), m.newlabel('lp'), m.newlabel('nx'), m.newlabel('nf2'), m.newlabel('rok')
    a.mov_imm(0, 7)
    m.imm32(LDR_B, 1); a.movl_ld(1, 6); a.tst_rr(6, 6); m.bt_far(nf)
    m.imm32(0x100, 0); a.add_rr(0, 6)                                                    # r6 = HUB slot tables
    w0, w1 = struct.unpack('<II', AT_NAME)
    a.mov_imm(0, 5)                                                                      # r5 = k
    a.deflabel(lp)
    a.mov_rm_rn(5, 4); a.shll2(4); a.add_rr(6, 4)
    m.imm32(0x44, 0); a.add_rr(4, 0); a.movl_ld(0, 0)
    a.mov_imm(1, 1); a.cmpeq(1, 0); m.bf_far(nx)                                         # INST[k] == 1
    a.mov_rm_rn(5, 4); a.shll2(4); a.shll(4); a.add_rr(6, 4); m.imm32(0x1C4, 0); a.add_rr(0, 4)   # NAME[k]
    a.movl_ld(4, 0); m.imm32(w0, 1); a.cmpeq(1, 0); m.bf_far(nx)
    a.movl_ld_disp(4, 4, 0); m.imm32(w1, 1); a.cmpeq(1, 0); m.bf_far(nx)
    a.mov_rm_rn(5, 4); a.shll2(4); a.add_rr(6, 4); m.imm32(0xC4, 0); a.add_rr(0, 4); a.movl_ld(4, 7)   # PAGE[k]
    a.mov_rm_rn(5, 4); a.shll2(4); a.add_rr(6, 4); m.imm32(0x384, 0); a.add_rr(0, 4); a.movl_ld(4, 3)   # SCR[k]
    m.imm32(0x44, 0); a.add_rr(0, 3); a.movl_ld(3, 3)                                    # the Resample page
    a.bra_lbl(nf2); a.nop()
    a.deflabel(nx); a.add_imm(1, 5); a.mov_imm(16, 0); a.cmpeq(0, 5); m.bf_far(lp)
    a.deflabel(nf); a.mov_imm(0, 3)
    a.deflabel(nf2)
    m.imm32(0x88000000, 0); a.cmphs(0, 3); a.bt_lbl(rok); a.mov_imm(0, 3); a.deflabel(rok)   # an older Audio Track keeps a setting there
    m.imm32(ATPG, 1); a.movl_st(7, 1); m.imm32(RSPG, 1); a.movl_st(3, 1)
    a.rts(); a.nop()
    m.pool_jump()

    # ---- __s9bg0 (every fire, any page): Audio Track lookup; MODE + pad 4 -> this menu ----
    a.deflabel('__s9bg0'); a.stsl_pr()
    a.bsr_lbl('__s9at0'); a.nop()
    m.imm32(EVQ + 0x68, 1); a.movl_ld(1, 9)                                              # r9 = current page
    # S500 Input left and no take running: the sampler's own Record source back
    sx = m.newlabel('sx')
    ld(m, SVON, 0); a.tst_rr(0, 0); m.bt_far(sx)
    m.imm32(SI.pg.addr['page'], 1); a.cmpeq(1, 9); m.bt_far(sx)
    ld(m, SI.ST, 0); a.mov_imm(SI.ST_ARM, 1); a.cmpeq(1, 0); m.bt_far(sx); a.mov_imm(SI.ST_REC, 1); a.cmpeq(1, 0); m.bt_far(sx)
    ld(m, SVSRC, 0); m.imm32(SMP + 0x10, 1); a.movl_st(0, 1); sti(m, 0, SVON)
    a.deflabel(sx)
    # the page changed: opened straight from the MODE page, sampler off now -> candidate
    ld(m, PREVPG, 0); a.cmpeq(9, 0); same = m.newlabel('same'); m.bt_far(same)
    a.mov_rm_rn(0, 8); st(m, 9, PREVPG); sti(m, 0, CWAIT)                               # r8 = the page before
    m.imm32(MODEPG, 1); a.cmpeq(1, 8); m.bf_far(same)
    for pg_ in (host, S9.pg, SI.pg):
        m.imm32(pg_.addr['page'], 1); a.cmpeq(1, 9); m.bt_far(same)
    ld(m, RECPG, 0); a.cmpeq(9, 0); known = m.newlabel('known'); m.bt_far(known)           # already known: swap now
    ld(m, LASTSMP, 0); a.tst_rr(0, 0); m.bf_far(same)                                     # sampler was on at MODE: not it
    m.imm32(SMP + 8, 1); a.movl_ld(1, 0); a.tst_rr(0, 0); nyet = m.newlabel('nyet'); m.bt_far(nyet)
    st(m, 9, RECPG); jmpl(m, known)                                                       # switched on while opening
    a.deflabel(nyet); st(m, 9, CAND); sti(m, WATCH, CWAIT)
    a.deflabel(same)
    # watching a candidate: the sampler goes on -> it is the Record page
    ld(m, CWAIT, 0); a.tst_rr(0, 0); nw = m.newlabel('nw'); m.bt_far(nw)
    a.add_imm(-1, 0); st(m, 0, CWAIT)
    ld(m, CAND, 0); a.cmpeq(9, 0); m.bf_far(nw)
    m.imm32(SMP + 8, 1); a.movl_ld(1, 0); a.tst_rr(0, 0); m.bt_far(nw)
    st(m, 9, RECPG)
    a.deflabel(known)
    sti(m, 0, CWAIT); sti(m, 1, GIVEBACK)
    m.imm32(EVQ, 4); a.mov_imm(0, 5); a.mov_imm(2, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()   # back
    m.imm32(host.addr['page'], 8); push(m, 8)
    m.imm32(host.addr['page'], 0); st(m, 0, PREVPG)
    a.deflabel(nw)
    # the Record page had switched the sampler on: give it back once our menu shows
    ld(m, GIVEBACK, 0); a.tst_rr(0, 0); ng = m.newlabel('ng'); m.bt_far(ng)
    m.imm32(host.addr['page'], 1); a.cmpeq(1, 9); m.bf_far(ng)
    sti(m, 0, GIVEBACK)
    m.imm32(SMP + 8, 1); a.movl_ld(1, 0); a.tst_rr(0, 0); m.bt_far(ng)
    m.imm32(SMP, 4); m.imm32(SMP_EXIT, 3); a.jsr(3); a.nop()
    a.deflabel(ng)
    m.imm32(SMP + 8, 1); a.movl_ld(1, 0); st(m, 0, LASTSMP)                              # sampler state of this fire
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

def doit(m):
    """DO IT = open the chosen page (CANCEL there comes back here)"""
    a = m.a
    ex = m.newlabel('ex')
    m.imm32(MODE, 1); a.movb_ld(1, 9); a.extu_b(9, 9)
    # AUDIO TRACK
    n0 = m.newlabel('n0'); a.tst_rr(9, 9); m.bf_far(n0)
    ld(m, ATPG, 8); a.tst_rr(8, 8); nl = m.newlabel('nl'); m.bt_far(nl); push(m, 8); jmpl(m, ex)
    a.deflabel(nl); host.emit_message(m, "NOT LOADED"); jmpl(m, ex)
    m.pool_jump()
    a.deflabel(n0)
    for k, page in ((1, pgS), (2, S9.pg)):
        nx = m.newlabel('md'); a.mov_imm(k, 0); a.cmpeq(0, 9); m.bf_far(nx)
        m.imm32(page.addr['page'], 8); push(m, 8); jmpl(m, ex)
        a.deflabel(nx)
    # RESAMPLE (a page of AUDTRACK.WAV)
    nrs = m.newlabel('nrs'); a.mov_imm(M_RESAMPLE, 0); a.cmpeq(0, 9); m.bf_far(nrs)
    ld(m, RSPG, 8); a.tst_rr(8, 8); m.bt_far(nl); push(m, 8); jmpl(m, ex)
    m.pool_jump()
    a.deflabel(nrs)
    # MPC RECORD
    ld(m, RECPG, 8); a.tst_rr(8, 8); nr = m.newlabel('nr'); m.bt_far(nr); push(m, 8); jmpl(m, ex)
    a.deflabel(nr); host.emit_message(m, "PRESS MODE+PAD4")
    a.deflabel(ex)
host.on_doit(doit)

def sel_doit(m):
    """DO IT on SELECT INPUT: the Record source for S500 Input (not while the sampler is in use), then S500 Input in place"""
    a = m.a
    ns_ = m.newlabel('ns'); hv = m.newlabel('hv')
    m.imm32(SMP + 8, 1); a.movl_ld(1, 0); a.tst_rr(0, 0); m.bf_far(ns_)
    ld(m, SVON, 0); a.tst_rr(0, 0); m.bf_far(hv)                                         # keep the setting saved first
    m.imm32(SMP + 0x10, 1); a.movl_ld(1, 0); st(m, 0, SVSRC); sti(m, 1, SVON)
    a.deflabel(hv)
    m.imm32(SRCS, 1); a.movb_ld(1, 0); a.extu_b(0, 0); m.imm32(SMP + 0x10, 1); a.movl_st(0, 1)
    a.deflabel(ns_)
    m.imm32(EVQ, 4); a.mov_imm(0, 5); a.mov_imm(2, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()   # back
    m.imm32(SI.pg.addr['page'], 8); push(m, 8)
pgS.on_doit(sel_doit)


def selftest(out):
    from validate import Harness
    from pages import SET_FOCUS, ENABLE_DOIT
    from shasm import SHAsm
    import audit_static
    magic, name, code, rel, ln = audit_static.payload(out)
    end = len(code); rend = end + 4 + 4 * len(rel)
    assert rend <= SLOT_CODE_MAX, "code + objects + relocations fit one add-on block"
    # the build's second copy (base + 0x1000) must not reach the scratch window, or imm32 takes object addresses there
    # for scratch slots and relocates them twice (2026-11-03: hit at 25.7 KB) -> keep code + objects <= 0x6000
    assert end <= SLOT_CODE_MAX - 0x1000, ("code + objects over 24 KB", end)
    hdr = open(out, 'rb').read(); hdr = hdr[hdr.find(b'data') + 8:]
    assert hdr[32:48].rstrip(b'\0') == b'S500', hdr[32:48]
    print("  ok one add-on: code+objects %d bytes, relocations up to %#x (block %#x)" % (end, rend, SLOT_CODE_MAX))
    LOG, MSG = 0x88E00000, 0x88E10000
    h = Harness(os.path.join(os.path.dirname(__file__), '..', 'mpc500_loader.bin')); h.fill_scratch(); e = h.emu
    def stub(addr, fn):
        s = SHAsm(addr); fn(s); blob = s.assemble()
        for i in range(0, len(blob), 2): h.ram_w16(addr + i, struct.unpack('<H', blob[i:i+2])[0])
    stub(OS_PUSH_PAGE, lambda s: (s.movl_pool(LOG, 1), s.movl_ld(1, 0), s.add_imm(1, 0), s.movl_st(0, 1), s.shll2(0), s.shll2(0),
                                  s.add_rr(0, 1), s.movl_st(5, 1), s.movl_st_disp(6, 4, 1), s.rts(), s.nop()))
    stub(ENABLE_DOIT, lambda s: (s.rts(), s.nop()))
    stub(SET_FOCUS, lambda s: (s.rts(), s.nop()))
    stub(SMP_EXIT, lambda s: (s.mov_imm(0, 0), s.movl_st_disp(0, 8, 4), s.movl_pool(LOG + 0x800, 1), s.mov_imm(1, 0), s.movl_st(0, 1), s.rts(), s.nop()))
    h.load_hub(); h.load_wav(out); h.fire(10); assert h.ram_r32(0x88F00144) == 1, "add-on not installed"
    R32, W32 = h.ram_r32, h.ram_w32
    def pushes(): return [(R32(LOG + 16 * (k + 1)), R32(LOG + 16 * (k + 1) + 4)) for k in range(R32(LOG))]
    def call(obj, type_, code=0, val=0, handler=None):
        h.ram_w32(MSG, (type_ & 0xffff) | (code << 16)); h.ram_w32(MSG+4, val & 0xffffffff); h.ram_w32(MSG+8, 0); h.ram_w32(MSG+12, 0)
        regs = [0x22220000 + i for i in range(16)]; regs[15] = 0x88400000 - 256; regs[4] = obj; regs[5] = MSG
        e.r = regs[:]; e.pr = 0xDEADBEEE; e.pc = handler or host.handler; e.maxsteps = 5_000_000
        assert e.run(stop_pc=0xDEADBEEE), "handler did not return"
        for i in range(8, 16): assert e.r[i] == regs[i], "r%d corrupted" % i
        return e.r[0]
    msg = lambda: bytes(e.rd(host.msgt + i, 1) for i in range(16)).split(b'\0')[0].decode().rstrip()
    doit = lambda: (W32(LOG, 0), call(host.page, 2, code=0x2E))
    # --- menu without Audio Track: AUDIO TRACK skipped ---
    h.ram_w32(EVQ + 0x68, host.page); h.fire(3)
    assert e.rd(MODE, 1) == 1, ("Audio Track not loaded: starts on S500 INPUT", e.rd(MODE, 1))
    e.wr(MODE, 0, 1); h.fire(2); assert e.rd(MODE, 1) == 1, "the wheel can't stay on AUDIO TRACK"
    assert [m_.strip() for m_ in MODES] == ["AUDIO TRACK", "S500 INPUT", "S500 CONVERT", "RESAMPLE", "MPC RECORD"], MODES
    for k, page in ((1, pgS), (2, S9.pg)):
        e.wr(MODE, k, 1); doit(); assert pushes()[-1][0] == page.page, (k, pushes())
    print("  ok menu order; S500 INPUT opens SELECT INPUT, S500 CONVERT its page; AUDIO TRACK hidden while Audio Track is not loaded")
    e.wr(MODE, M_RESAMPLE, 1); doit(); assert pushes() == [] and msg() == "NOT LOADED", (pushes(), msg())
    # --- SELECT INPUT: DO IT opens S500 Input with that source; leaving gives the sampler's own setting back ---
    def seldoit(): W32(LOG, 0); return call(pgS.page, 2, code=0x2E, handler=pgS.handler)
    assert e.rd(SRCS, 1) == 0
    W32(SMP + 8, 0); W32(SMP + 0x10, 0); h.ram_w32(EVQ + 0x68, pgS.page); h.fire(2)
    e.wr(SRCS, 1, 1); seldoit(); assert pushes() == [(0, 2), (SI.pg.page, 0)] and R32(SMP + 0x10) == 1 and R32(SVON) == 1, (pushes(), R32(SMP + 0x10))
    W32(SMP + 8, 1); h.ram_w32(EVQ + 0x68, SI.pg.page); h.fire(3); assert R32(SMP + 0x10) == 1 and R32(SVON) == 1, "stays Main on S500 Input"
    W32(SMP + 8, 0); W32(SMP + 0x24, 3); W32(SMP + 0x88, 0); W32(SI.ST, SI.ST_REC)                # (no OS image here: the sampler state by hand)
    h.ram_w32(EVQ + 0x68, host.page); h.fire(3); assert R32(SMP + 0x10) == 1 and R32(SVON) == 1, "take running"
    W32(SI.ST, SI.ST_IDLE); W32(SMP + 0x24, 0); h.fire(3); assert R32(SMP + 0x10) == 0 and R32(SVON) == 0, "left, no take: the old setting back"
    W32(SMP + 8, 0); e.wr(SRCS, 0, 1); seldoit(); h.ram_w32(EVQ + 0x68, host.page); h.fire(3); assert R32(SMP + 0x10) == 0
    W32(SMP + 0x10, 1); h.ram_w32(EVQ + 0x68, pgS.page); h.fire(2); seldoit(); assert R32(SMP + 0x10) == 0
    h.ram_w32(EVQ + 0x68, host.page); h.fire(3); assert R32(SMP + 0x10) == 1, "REC IN chosen once: the MPC's own Main comes back"
    W32(SMP + 0x10, 0); W32(SMP + 8, 1); e.wr(SRCS, 1, 1); h.ram_w32(EVQ + 0x68, pgS.page); h.fire(1); seldoit()
    assert R32(SMP + 0x10) == 0 and R32(SVON) == 0, "sampler in use: source not changed"; W32(SMP + 8, 0); e.wr(SRCS, 0, 1)
    h.ram_w32(EVQ + 0x68, host.page); h.fire(2)
    print("  ok SELECT INPUT: DO IT applies REC IN / MAIN OUT to S500 Input only, the sampler's setting comes back on leaving")
    e.wr(MODE, M_MPC, 1); doit(); assert pushes() == [] and msg() == "PRESS MODE+PAD4", (pushes(), msg())
    # --- Audio Track loaded later: found in the HUB table at once ---
    atwav = os.path.join(os.path.dirname(__file__), '..', '..', 'AUDTRACK.WAV')
    for ad in (0x884C6722, 0x884C672E, 0x884C67BA): stub(ad, lambda s: (s.rts(), s.nop()))      # codec (Audio Track input thru)
    h.ram_w32(EVQ + 0x68, 0x88123456); h.fire(2); h.load_wav(atwav); h.fire(20)                 # (LOAD happens on an OS page)
    L = h.hub_lay(); atk = [k for k in range(16) if R32(L.INST(k)) == 1 and bytes(e.rd(L.NAME(k) + i, 1) for i in range(8)) == AT_NAME]
    assert atk, ("Audio Track installed", os.path.exists(atwav), [(k, R32(L.INST(k)), bytes(e.rd(L.NAME(k) + i, 1) for i in range(8)), hex(R32(L.PAGE(k)))) for k in range(4)]); atpage = R32(L.PAGE(atk[0]))
    h.ram_w32(EVQ + 0x68, host.page); h.fire(2); e.wr(MODE, 0, 1); h.fire(2); assert e.rd(MODE, 1) == 0
    doit(); assert pushes() == [(atpage, 0)], (pushes(), hex(atpage))
    print("  ok Audio Track loaded later: AUDIO TRACK shown at once, DO IT opens its page (%08x, HUB slot %d)" % (atpage, atk[0]))
    rspage = R32(R32(L.SCR(atk[0])) + 0x44); assert 0x88000000 <= rspage & 0xDFFFFFFF < 0x98000000 and rspage != atpage, hex(rspage)
    e.wr(MODE, M_RESAMPLE, 1); doit(); assert pushes() == [(rspage, 0)], (pushes(), hex(rspage))
    print("  ok RESAMPLE opens the second page of AUDTRACK.WAV (%08x)" % rspage)
    # --- MODE + pad 4: a page from the MODE page that switches the sampler on = Record page -> our menu ---
    REC, PRG = 0x88490E60, 0x88491000
    W32(SMP + 8, 0); W32(LOG + 0x800, 0)
    h.ram_w32(EVQ + 0x68, MODEPG); h.fire(2)
    W32(LOG, 0); h.ram_w32(EVQ + 0x68, PRG); h.fire(5); assert pushes() == [], "another MODE pad (no sampler): left alone"
    h.ram_w32(EVQ + 0x68, MODEPG); W32(SMP + 8, 1); h.fire(2); h.ram_w32(EVQ + 0x68, PRG); h.fire(5); W32(SMP + 8, 0)
    assert pushes() == [], "sampler already on at MODE (another add-on): a MODE pad page is left alone"
    # (a candidate whose sampler comes on a few fires later is caught too)
    h.ram_w32(EVQ + 0x68, MODEPG); h.fire(2); h.ram_w32(EVQ + 0x68, REC); h.fire(2); W32(SMP + 8, 1); h.fire(1)
    assert pushes()[-2:] == [(0, 2), (host.page, 0)], pushes()
    W32(SMP + 8, 0); W32(RECPG, 0); h.ram_w32(EVQ + 0x68, host.page); h.fire(2); W32(SMP + 8, 0); h.fire(1)
    h.ram_w32(EVQ + 0x68, MODEPG); h.fire(2)
    W32(LOG, 0); h.ram_w32(EVQ + 0x68, REC); W32(SMP + 8, 1); h.fire(1)                      # sampler on WHILE it opens (HW)
    assert pushes() == [(0, 2), (host.page, 0)] and R32(RECPG) == REC, (pushes(), hex(R32(RECPG)))
    h.ram_w32(EVQ + 0x68, host.page); h.fire(2); assert R32(SMP + 8) == 0 and R32(LOG + 0x800) == 1, "sampler given back"
    print("  ok MODE+pad4: the Record page (sampler on) -> back + this menu, sampler given back; other MODE pads untouched")
    # next time it is swapped at once; MPC RECORD opens it, and from here it is not swapped
    h.ram_w32(EVQ + 0x68, MODEPG); h.fire(2); W32(LOG, 0); h.ram_w32(EVQ + 0x68, REC); h.fire(1)
    assert pushes() == [(0, 2), (host.page, 0)], pushes()
    h.ram_w32(EVQ + 0x68, host.page); h.fire(2)
    e.wr(MODE, M_MPC, 1); doit(); assert pushes() == [(REC, 0)], pushes()
    W32(LOG, 0); h.ram_w32(EVQ + 0x68, REC); W32(SMP + 8, 1); h.fire(5); assert pushes() == [], "MPC RECORD from the menu: stays"
    W32(SMP + 8, 0)
    print("  ok known Record page swapped at once; MPC RECORD opens the MPC Record page (and it stays)")
    for nm, mod in (("S500 INPUT", SI), ("S500 CONVERT", S9)):
        print("== %s (%s)" % (nm, mod.__name__)); mod.selftest(out)
    print("OK: S500 (AUDIO TRACK link + S500 INPUT + S500 CONVERT + MPC RECORD, MODE+pad4) -> %s" % os.path.basename(out))


if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', '..', 'S500.WAV'); host.build_wav(out); selftest(out)
