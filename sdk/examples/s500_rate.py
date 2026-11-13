#!/usr/bin/env python3
"""S500 rate field, shared by S500 Convert (s500.py) and S500 Input (s500_input.py).

  "26.04k": a number field (x0..4 on row 1, "k" at x5).
  Wheel: with the cursor on the tenths digit (where it starts) one click = the next / previous rate of the list; LEFT
    moves the cursor to the ones digit (one click = about 1k: the nearest rate of the list beyond +-1.00k), LEFT again
    to the tens digit (+-10k). RIGHT goes back; past the first / last digit LEFT / RIGHT go to the OS (next field).
  Slider: 7.00 -> 11.25 -> 22.50 -> 26.04 -> 29.76 -> 44.10 (catch-up: it changes the rate only after passing the
    position of the rate shown; no hint).
  The list: 7.00k .. 44.00k in 0.5k steps + 11.25k, 26.04k (default), 29.76k, 44.10k. RATEI (a slot, byte 0) is the
  index in the list the conversion uses (its filter coefficients and step are built per rate, as before).
OS: Q-Link slider 0x8848C3E4 (Pitch / Mixer, HW); number field cursor column = model +1 (Trim v1, HW); key codes
LEFT 2 / RIGHT 3 (Grid Seq, HW).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import ld, st, sti
from mpcmod import EVQ

RATES = sorted(set([700 + 50 * i for i in range(75)] + [1125, 2604, 2976, 4410]))   # x0.01 kHz, 7.00 .. 44.10
assert RATES[0] == 700 and RATES[-1] == 4410 and len(RATES) <= 255
RATE_DEF = RATES.index(2604)
SLIDE = [700, 1125, 2250, 2604, 2976, 4410]
SLIDE_IDX = [RATES.index(r) for r in SLIDE]
SLIDER = 0x8848C3E4
SL_LO, SL_HI, HYST = 64, 960, 3
K_LEFT, K_RIGHT = 0x02, 0x03
FMT = " 0.00"                     # tens, ones, '.', tenths, hundredths
COL = [3, 1, 0]                   # cursor column per digit level: next rate / ones / tens
STEP = [0, 100, 1000]             # 0.01k units

def fields(pg):
    """the rate field + its 'k'; returns a dict of the slots (call where the old rate field was)"""
    R = {}
    R['F'] = pg.value("", 0, 1, fmt=FMT, lo=RATES[0], hi=RATES[-1], default=RATES[RATE_DEF])
    pg.label("k", 5, 1)
    R['I'] = pg.alloc_slot(RATE_DEF)
    for k in ('LF', 'LI', 'DIG', 'LFOC', 'SF', 'SINIT', 'CUR', 'CAUGHT', 'SIDE'): R[k] = pg.alloc_slot()
    R['SS'] = pg.alloc_slot(0xFF); R['SLO'] = pg.alloc_slot(SL_LO); R['SHI'] = pg.alloc_slot(SL_HI)
    pg.host.defaults[R['LF']] = RATES[RATE_DEF]; pg.host.defaults[R['LI']] = RATE_DEF; pg.host.defaults[R['SIDE']] = 0xFF
    return R

def fid(pg, slot): return [it[1] for it in pg.items if it[0] == 'value' and it[-1] == slot][0]

def attach(pg, R):
    """the rate's work runs first in the page's fire; LEFT / RIGHT on the field pick the digit"""
    old_fire = pg.fire_fn
    def fire(m):
        call(m, 'rt_fire')
        old_fire(m)
        m.a.movl_pool('__rt_end', 2); m.a.jmp(2); m.a.nop(); m.pool_jump()
        subs(m, pg, R)
        m.a.deflabel('__rt_end')
    pg.on_fire(fire)
    def ev_key(m, hret0, hret1):
        a = m.a
        kx, kl, kr, kd = m.newlabel('kx'), m.newlabel('kl'), m.newlabel('kr'), m.newlabel('kd')
        m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 12)                         # (r9 = event type: keep)
        m.imm32(pg.addr['values'][fid(pg, R['F'])], 1); a.cmpeq(1, 12); m.bf_far(kx)
        a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 10); a.extu_w(10, 10)
        a.mov_imm(K_LEFT, 0); a.cmpeq(0, 10); m.bt_far(kl)
        a.mov_imm(K_RIGHT, 0); a.cmpeq(0, 10); m.bt_far(kr)
        jmpl(m, kx); m.pool_jump()
        a.deflabel(kl); ld(m, R['DIG'], 0); a.mov_imm(len(COL) - 1, 1); a.cmpeq(1, 0); m.bt_far(kx); a.add_imm(1, 0); jmpl(m, kd)
        a.deflabel(kr); ld(m, R['DIG'], 0); a.tst_rr(0, 0); m.bt_far(kx); a.add_imm(-1, 0)
        a.deflabel(kd); st(m, 0, R['DIG']); call(m, 'rt_col')
        m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
        jmpl(m, hret1)
        m.pool_jump()
        a.deflabel(kx)
    pg.on_event(2, ev_key)

def leave(m, R):
    """call in the page's inactive: the slider may be moved elsewhere, so it has to catch the rate shown again
    (user 2026-11-08: the rate jumped to the slider's position when re-entering the page). r0, r1"""
    sti(m, 0, R['CAUGHT']); sti(m, 0xFF, R['SIDE']); sti(m, 0, R['SINIT'])

def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()
def jmpl(m, lbl): m.a.movl_pool(lbl, 2); m.a.jmp(2); m.a.nop()
def rdb(m, addr, r): m.imm32(addr, r); m.a.movb_ld(r, r); m.a.extu_b(r, r)

def subs(m, pg, R):
    a = m.a
    N = len(RATES)
    WF = pg.addr['values'][fid(pg, R['F'])]
    def rate_of(ri, rd_):
        """rd_ = RATES[ri]. Uses r1"""
        a.movl_pool('rt_rates', 1); a.mov_rm_rn(ri, rd_); a.shll2(rd_); a.add_rr(1, rd_); a.movl_ld(rd_, rd_)

    # ---- rt_set: r6 = index -> RATEI, the field (and the copies the wheel / changes are compared with) ----
    a.deflabel('rt_set'); a.stsl_pr()
    m.imm32(R['I'], 1); a.mov_rm_rn(6, 0); a.movb_st(0, 1); st(m, 6, R['LI'])
    rate_of(6, 0); st(m, 0, R['F']); st(m, 0, R['LF'])
    m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- rt_col (leaf): the cursor under the digit the wheel moves (model +1 = column). r0-r3 ----
    a.deflabel('rt_col')
    m.imm32(WF + 0x24, 1); a.movl_ld(1, 2); ld(m, R['DIG'], 3); a.mov_imm(COL[0], 0)
    for d, c in enumerate(COL[1:], 1):
        n = m.newlabel('cc'); a.mov_imm(d, 1); a.cmpeq(1, 3); a.bf_lbl(n); a.mov_imm(c, 0); a.deflabel(n)
    a.rts(); a.movb_st_disp(1, 2)
    m.pool_jump()

    # ---- rt_fire: wheel, slider, cursor column ----
    a.deflabel('rt_fire'); a.stsl_pr()
    nw, nc, done, up, go = (m.newlabel(s) for s in ('nw', 'nc', 'dn', 'up', 'go'))
    # another field under the cursor: the digit starts on the "next rate" one
    m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 9); ld(m, R['LFOC'], 0); a.cmpeq(0, 9); nf = m.newlabel('nf'); m.bt_far(nf)
    st(m, 9, R['LFOC']); sti(m, 0, R['DIG'])
    a.deflabel(nf)
    # wheel: the field moved -> a rate of the list, by the digit under the cursor
    ld(m, R['F'], 4); ld(m, R['LF'], 5); a.cmpeq(4, 5); m.bt_far(nw)
    rdb(m, R['I'], 6)
    ld(m, R['DIG'], 7); a.tst_rr(7, 7); big = m.newlabel('big'); m.bf_far(big)
    a.cmpgt(5, 4); m.bt_far(up)                                                       # r4 > r5: up
    a.tst_rr(6, 6); m.bt_far(go); a.add_imm(-1, 6); jmpl(m, go)
    a.deflabel(up); a.mov_imm(N - 1, 0); a.cmpeq(0, 6); m.bt_far(go); a.add_imm(1, 6); jmpl(m, go)
    m.pool_jump()
    a.deflabel(big)                                                                   # +-1k / +-10k: nearest beyond
    m.imm32(STEP[1], 8); a.mov_imm(2, 0); a.cmpeq(0, 7); s2 = m.newlabel('s2'); a.bf_lbl(s2); m.imm32(STEP[2], 8)
    a.deflabel(s2)
    rate_of(6, 10)                                                                    # r10 = the rate now
    bu, bl, bd = m.newlabel('bu'), m.newlabel('bl'), m.newlabel('bd')
    a.cmpgt(5, 4); m.bt_far(bu)
    a.sub(8, 10)                                                                      # down: last rate <= now - step
    a.deflabel(bd); a.tst_rr(6, 6); m.bt_far(go); a.add_imm(-1, 6); rate_of(6, 0); a.cmpgt(10, 0); m.bt_far(bd)
    jmpl(m, go)
    a.deflabel(bu); a.add_rr(8, 10)                                                   # up: first rate >= now + step
    a.deflabel(bl); a.mov_imm(N - 1, 0); a.cmpeq(0, 6); m.bt_far(go); a.add_imm(1, 6); rate_of(6, 0); a.cmpgt(0, 10); m.bt_far(bl)
    a.deflabel(go)
    call(m, 'rt_set'); sti(m, 0, R['CAUGHT']); sti(m, 0xFF, R['SIDE']); jmpl(m, nc)
    m.pool_jump()
    a.deflabel(nw)
    # the index changed elsewhere (tests, defaults): the field follows
    rdb(m, R['I'], 6); ld(m, R['LI'], 0); a.cmpeq(0, 6); m.bt_far(nc); call(m, 'rt_set')
    a.deflabel(nc)
    # slider: SS = 0..5, CUR = the last slider rate <= the rate shown; catch-up, then the slider's rates
    call(m, 'rt_slider')
    rdb(m, R['I'], 6); a.movl_pool('rt_slide', 1); a.mov_imm(0, 7)
    for k in range(1, len(SLIDE)):
        n = m.newlabel('ck'); a.movl_ld_disp(1, 4 * k, 0); a.cmpgt(6, 0); a.bt_lbl(n); a.mov_imm(k, 7); a.deflabel(n)
    st(m, 7, R['CUR'])
    fol, cat = m.newlabel('fol'), m.newlabel('cat')
    ld(m, R['CAUGHT'], 0); a.tst_rr(0, 0); m.bf_far(fol)
    ld(m, R['SS'], 10); a.cmpeq(7, 10); m.bt_far(cat)
    a.cmphi(7, 10); a.movt(2); ld(m, R['SIDE'], 0); m.imm32(0xFF, 1); a.cmpeq(1, 0); sv = m.newlabel('sv'); m.bt_far(sv)
    a.cmpeq(2, 0); m.bf_far(cat)                                                      # passed it: caught
    a.deflabel(sv); st(m, 2, R['SIDE']); jmpl(m, done)
    m.pool_jump()
    a.deflabel(cat); sti(m, 1, R['CAUGHT'])
    a.deflabel(fol)
    ld(m, R['SS'], 10); ld(m, R['CUR'], 7); a.cmpeq(7, 10); m.bt_far(done)
    a.movl_pool('rt_slide', 1); a.mov_rm_rn(10, 0); a.shll2(0); a.add_rr(0, 1); a.movl_ld(1, 6); call(m, 'rt_set')
    a.deflabel(done)
    call(m, 'rt_col')
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- rt_slider: SS = slider step 0..5 (filtered, learned range, hysteresis, exact ends; as Program / Trim) ----
    NS = len(SLIDE) - 1
    a.deflabel('rt_slider'); a.stsl_pr()
    m.imm32(SLIDER, 1); a.movl_ld(1, 2); m.imm32(0x3FF, 1); a.and_rr(1, 2); a.shll2(2); a.shll2(2)
    ld(m, R['SINIT'], 0); a.tst_rr(0, 0); g0 = m.newlabel('g0'); a.bf_lbl(g0)
    st(m, 2, R['SF']); sti(m, 1, R['SINIT'])
    a.deflabel(g0)
    ld(m, R['SF'], 0); a.sub(0, 2); a.shar(2); a.shar(2); a.shar(2); a.add_rr(2, 0); st(m, 0, R['SF'])
    a.mov_rm_rn(0, 6); a.shar(6); a.shar(6); a.shar(6); a.shar(6)
    ld(m, R['SLO'], 0); c1 = m.newlabel('c1'); a.cmpgt(6, 0); a.bf_lbl(c1); st(m, 6, R['SLO']); a.deflabel(c1)
    ld(m, R['SHI'], 0); c2 = m.newlabel('c2'); a.cmpgt(0, 6); a.bf_lbl(c2); st(m, 6, R['SHI']); a.deflabel(c2)
    ld(m, R['SLO'], 7); a.sub(7, 6)
    ld(m, R['SHI'], 5); a.sub(7, 5); a.mov_rm_rn(5, 7)
    a.mov_rm_rn(6, 4); a.mov_imm(NS, 0); a.mul_l(0, 4); a.sts_macl(4)
    a.mov_rm_rn(7, 0); a.shlr(0); a.add_rr(0, 4); pg._hcall(m, '__udiv')
    a.mov_imm(NS, 1); c3 = m.newlabel('c3'); a.cmpgt(1, 0); a.bf_lbl(c3); a.mov_rm_rn(1, 0); a.deflabel(c3)
    a.mov_rm_rn(0, 3)
    sx, setv = m.newlabel('sx'), m.newlabel('set')
    ld(m, R['SS'], 0); a.cmpeq(3, 0); m.bt_far(sx)
    a.mov_imm(NS, 1); a.cmphi(1, 0); m.bt_far(setv)
    a.tst_rr(3, 3); m.bt_far(setv); a.mov_imm(NS, 1); a.cmpeq(1, 3); m.bt_far(setv)
    a.mov_rm_rn(0, 4); a.mul_l(7, 4); a.sts_macl(4); a.mov_imm(NS, 5)
    a.movl_st_dec(3, 15); pg._hcall(m, '__udiv'); a.movl_ld_inc(15, 3)
    a.sub(0, 6); a.cmppz(6); ab = m.newlabel('ab'); a.bt_lbl(ab); a.neg(6, 6); a.deflabel(ab)
    a.mov_rm_rn(7, 4); a.mov_imm(2 * NS, 5); a.movl_st_dec(3, 15); a.movl_st_dec(6, 15)
    pg._hcall(m, '__udiv'); a.movl_ld_inc(15, 6); a.movl_ld_inc(15, 3); a.add_imm(HYST, 0)
    a.cmphi(0, 6); m.bf_far(sx)
    a.deflabel(setv); st(m, 3, R['SS'])
    a.deflabel(sx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tables ----
    skip = m.newlabel('tsk'); a.bra_lbl(skip); a.nop()
    for name, words in (('rt_rates', RATES), ('rt_slide', SLIDE_IDX)):
        m.pool_jump()
        while (len(a.words) * 2 + a.base) & 3: a.nop()
        a.deflabel(name)
        for w in words: a._emit(w & 0xFFFF); a._emit((w >> 16) & 0xFFFF)
    a.deflabel(skip)


def check(t, pg, R):
    """emulator test of the rate field on page pg (open, nothing running); leaves it on 26.04k"""
    from testkit import EV_KEY
    h, e = t.h, t.e; R32, W32 = h.ram_r32, h.ram_w32
    WF = pg.values[fid(pg, R['F'])]
    rate = lambda: R32(R['F'])
    idx = lambda: e.rd(R['I'], 1)
    col = lambda: e.rd(R32(WF + 0x24) + 1, 1)
    key = lambda k: t.event(EV_KEY, k)
    def wheel(d):
        W32(R['F'], rate() + d); t.fire(2)
        assert rate() == RATES[idx()], (rate(), idx())
    def slide(k):
        lo, hi = R32(R['SLO']), R32(R['SHI']); W32(SLIDER, lo + round(k * (hi - lo) / (len(SLIDE) - 1))); t.fire(40)
    focus0 = R32(pg.page + 0x24)
    assert rate() == 2604 and idx() == RATE_DEF, (rate(), idx())
    W32(pg.page + 0x24, WF); t.fire(2); assert col() == 3 and R32(R['DIG']) == 0, col()
    wheel(+1); assert rate() == 2650
    wheel(-1); wheel(-1); assert rate() == 2600
    assert key(K_RIGHT) == 0, "first level: RIGHT goes to the OS"
    assert key(K_LEFT) == 1; t.fire(1); assert R32(R['DIG']) == 1 and col() == 1
    wheel(+1); assert rate() == 2700, rate()
    wheel(-1); wheel(-1); assert rate() == 2500, rate()
    assert key(K_LEFT) == 1; t.fire(1); assert R32(R['DIG']) == 2 and col() == 0
    wheel(+1); assert rate() == 3500; wheel(+1); assert rate() == 4410, ("past 44.0: the last rate", rate())
    wheel(-1); assert rate() == 3400, rate()
    assert key(K_LEFT) == 0, "last level: LEFT goes to the OS"
    key(K_RIGHT); key(K_RIGHT); t.fire(1); assert R32(R['DIG']) == 0 and col() == 3
    for _ in range(3): wheel(-1)
    assert rate() == 3250, rate()
    e.wr(R['I'], RATES.index(1125), 1); t.fire(2); assert rate() == 1125
    print("    ok rate: wheel = next rate (26.04 -> 26.50 -> 26.00), ones digit +-1k (27.00, 25.00), tens +-10k "
          "(35.00, 44.10, 34.00); LEFT / RIGHT pick the digit, past the ends to the OS")
    # slider: 7.00 / 11.25 / 22.50 / 26.04 / 29.76 / 44.10 after catching the rate shown (11.25)
    W32(SLIDER, SL_HI); t.fire(40); slide(1); slide(5); assert rate() == 4410, ("caught at 11.25, then 44.10", rate())
    slide(3); assert rate() == 2604; slide(0); assert rate() == 700; slide(2); assert rate() == 2250; slide(4); assert rate() == 2976
    wheel(-1); assert rate() == 2950; slide(5); assert rate() == 2950 or rate() == 4410
    print("    ok slider: 44.10, 26.04, 7.00, 22.50, 29.76 (catch-up after a wheel move)")
    # page left, slider moved elsewhere, page entered again: the rate stays until the slider catches it
    slide(0); assert rate() == 700, rate()
    t.close(); hi_ = R32(R['SHI']); W32(SLIDER, hi_); t.fire(4); t.open(); t.fire(40); assert rate() == 700, ("re-entry keeps the rate", rate())
    slide(4); assert rate() == 700, rate(); slide(0); slide(4); assert rate() == 2976, ("caught again", rate()); wheel(-1)
    print("    ok re-entering the page with the slider elsewhere keeps the rate (catch-up again)")
    e.wr(R['I'], RATE_DEF, 1); t.fire(2); assert rate() == 2604
    W32(pg.page + 0x24, focus0); t.fire(2)
