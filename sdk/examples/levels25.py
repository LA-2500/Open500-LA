#!/usr/bin/env python3
"""25 LEVELS - play the MPC's 12 Levels from a MIDI keyboard, 25 tune levels -> 25LEVELS.WAV

    python sdk/examples/levels25.py        -> builds 25LEVELS.WAV and tests it in the emulator

On the MPC500: copy 25LEVELS.WAV to the CF card, LOAD it (after HUB.WAV). MODE -> OTHER -> "25 Levels":
    25 LEVELS
    ROOT:C3     TUNE
  ROOT   the keyboard key that plays the pad at its own pitch (wheel; C3 = MIDI note 60)
  TUNE / VELO / OFF   what the MPC's 12 Levels is doing now (shown only)
The 12 LEVELS button switches it on and off as usual (the pad and TUNE/VELOCITY are the MPC's own 12 Levels settings).
While 12 Levels is on, on ANY page:
  TUNE      keys ROOT-12 .. ROOT+12 (25 keys) play the 12 Levels pad -12 .. +12 semitones, at the key's velocity
  VELOCITY  keys ROOT .. ROOT+11 (12 keys) play the pad at the 12 velocity levels
  the notes go through the MPC's own 12 Levels: they play and RECORD (with their tune) like the pads in 12 Levels.
  The MPC's normal MIDI input (notes, controllers, program changes) is off; MIDI clock / sync stays on. The add-on
  listens on the MPC's MIDI Receive channel (ALL = any channel). 12 Levels off -> everything back as it was.
Limit: the MPC stores a 12 Levels tune per note in 0.1 semitones in one byte -> +-12 semitones (25 levels).

OS addresses (MPC500 OS 1.31; "fw" = firmware read + emulator call trace 2026-11-03, not yet confirmed on hardware):
  fw: pad hit 0x884E5736 -> sequencer message 0x884B6AD4(0x8847C10C, 44 on / 45 off | velocity << 8 | pad 0..47 << 16)
  fw: 12 Levels transform 0x884B81B0, state 0x8849C98C (= bank object): +8 12 Levels on, +12 source pad, +16 mode
      (0 velocity: level*127+127)/12, 1 tune), +20 base s8: level = pad % 12, tune = (level - src % 12 + base) * 10
      (s8, 1/10 semitone) -> voice 0x884C2E1A (gets the tune) and the recorder (stores it with the note).
  fw: MIDI IN queue 0x8848CBF8..0x8848CC77 (MIDI Screen), write pointer 0x881C067C (interrupt).
  fw: MIDI settings 0x88483F88 (getter 0x884BF7B0): byte +23 bits 3-7 = Receive channel (0 = all); the OS parser
      0x884AFBAC ignores channel messages of another channel -> 31 = none (bits 0-2, sync, kept).
"""
import sys, os, struct
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import PageAddon, ld, st, sti
from mpcmod import EVQ

LV = 0x8849C98C                                    # 12 Levels state
LV_ON, LV_SRC, LV_MODE, LV_BASE = LV + 8, LV + 12, LV + 16, LV + 20
MIDISET = 0x88483F88; RXCH = MIDISET + 23          # bits 3-7 Receive channel
RXQ, RXQ_END, RXWR = 0x8848CBF8, 0x8848CC77, 0x881C067C
SEQOBJ, SEQ_MSG, MSG_ON, MSG_OFF = 0x8847C10C, 0x884B6AD4, 44, 45
NPAD = 48
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
def note_name(n): return "%-4s" % (NOTES[n % 12] + str(n // 12 - 2))

pg = PageAddon("25levels", open_press=None)
pg.other_menu("25 Levels")
pg.far_hend = True
pg.far_fire = True
pg.label("25 LEVELS", 0, 0)
ROOT = pg.choice("ROOT:", 0, 1, [note_name(n) for n in range(128)], default=60)
STL = pg.dlabel(11, 1, 5)
(OWN, SAVCH, RD, RS, D1, NB, SAVB, LMODE, LSTAT) = [pg.alloc_slot() for _ in range(9)]
pg.defaults[LSTAT] = 0xFF
KEYS = pg.alloc_buf(NPAD)                          # pad index k in use: note + 1 (0 = free)
STATUS = [" OFF ", " VELO", " TUNE"]

def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()
def jmpl(m, lbl): m.a.movl_pool(lbl, 2); m.a.jmp(2); m.a.nop()
def rdb(m, addr, r): m.imm32(addr, r); m.a.movb_ld(r, r); m.a.extu_b(r, r)
def rdl(m, addr, r): m.imm32(addr, r); m.a.movl_ld(r, r)
def wrb(m, rv, addr): m.imm32(addr, 1); m.a.movb_st(rv, 1)
def redraw(m): m.imm32(EVQ + 0x70, 1); m.a.mov_imm(1, 0); m.a.movl_st(0, 1)

def fire(m):
    a = m.a
    call(m, 'ml_bg')
    # status: 0 OFF, 1 VELO, 2 TUNE (other 12 Levels modes: OFF, the add-on leaves them alone)
    s = m.newlabel('s'); a.mov_imm(0, 9)
    rdl(m, LV_ON, 0); a.tst_rr(0, 0); m.bt_far(s)
    rdl(m, LV_MODE, 0); a.mov_imm(1, 9); a.tst_rr(0, 0); m.bt_far(s)
    a.mov_imm(2, 9); a.mov_imm(1, 1); a.cmpeq(1, 0); m.bt_far(s); a.mov_imm(0, 9)
    a.deflabel(s)
    ld(m, LSTAT, 0); a.cmpeq(9, 0); sx = m.newlabel('sx'); m.bt_far(sx)
    st(m, 9, LSTAT)
    for code, txt in enumerate(STATUS):
        n = m.newlabel('st'); a.mov_imm(code, 0); a.cmpeq(9, 0); m.bf_far(n)
        m.imm32(pg.addr['dlabels'][STL], 1)
        for ch in txt: a.mov_imm(ord(ch), 0); a.movb_st(0, 1); a.add_imm(1, 1)
        a.deflabel(n)
    redraw(m)
    a.deflabel(sx)
    jmpl(m, '__ml_end'); m.pool_jump()
    subs(m)
    a.deflabel('__ml_end')
pg.on_fire(fire)

def inactive(m): call(m, 'ml_bg')
pg.on_inactive(inactive)

def mkmsg(m, msg, rvel, rk):
    """r5 = msg | velocity << 8 | pad << 16"""
    a = m.a
    a.mov_rm_rn(rk, 5); a.shll16(5); a.mov_rm_rn(rvel, 0); a.shll8(0); a.or_rr(0, 5); a.mov_imm(msg, 0); a.or_rr(0, 5)

def subs(m):
    a = m.a
    # ---- ml_send: r5 = message -> the MPC's sequencer (plays, records) ----
    a.deflabel('ml_send'); a.stsl_pr()
    m.imm32(SEQOBJ, 4); m.imm32(SEQ_MSG, 2); a.jsr(2); a.nop()
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- ml_bg (every fire, any page): 12 Levels on -> MPC MIDI input off, keyboard -> 12 Levels; off -> back ----
    a.deflabel('ml_bg'); a.stsl_pr()
    off_, own_, par_, end_ = m.newlabel('off'), m.newlabel('own'), m.newlabel('par'), m.newlabel('end')
    rdl(m, LV_ON, 0); a.tst_rr(0, 0); m.bt_far(off_)
    ld(m, OWN, 0); a.tst_rr(0, 0); m.bf_far(own_)
    rdb(m, RXCH, 0); st(m, 0, SAVCH); a.or_imm(0xF8); wrb(m, 0, RXCH)               # receive channel 31 = none
    sti(m, 1, OWN); rdl(m, RXWR, 0); st(m, 0, RD); sti(m, 0, RS); sti(m, 0, NB)
    rdl(m, LV_MODE, 0); st(m, 0, LMODE)
    m.imm32(KEYS, 1); a.mov_imm(0, 0)
    for k in range(0, NPAD, 4): a.movl_st_disp(0, k, 1)
    jmpl(m, par_)
    m.pool_jump()
    a.deflabel(own_)
    rdb(m, RXCH, 0); a.mov_rm_rn(0, 2); a.and_imm(0xF8); m.imm32(0xF8, 1); a.cmpeq(1, 0); kp = m.newlabel('kp'); m.bt_far(kp)
    st(m, 2, SAVCH); a.mov_rm_rn(2, 0); a.or_imm(0xF8); wrb(m, 0, RXCH)              # changed meanwhile: keep it
    a.deflabel(kp)
    rdl(m, LV_MODE, 0); ld(m, LMODE, 2); a.cmpeq(2, 0); sm = m.newlabel('sm'); m.bt_far(sm)
    st(m, 0, LMODE); call(m, 'ml_alloff')                                            # TUNE <-> VELOCITY
    a.deflabel(sm)
    a.deflabel(par_); call(m, 'ml_parse'); jmpl(m, end_)
    m.pool_jump()
    a.deflabel(off_)
    ld(m, OWN, 0); a.tst_rr(0, 0); m.bt_far(end_)
    call(m, 'ml_alloff')
    rdb(m, RXCH, 0); a.and_imm(7); ld(m, SAVCH, 2); a.mov_imm(-8, 1); a.and_rr(1, 2); a.or_rr(2, 0); wrb(m, 0, RXCH)
    sti(m, 0, OWN)
    a.deflabel(end_); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- ml_parse: new bytes of the MIDI IN queue (max 64 per fire): note on / off of the receive channel ----
    a.deflabel('ml_parse'); a.stsl_pr()
    pl, nx, dn = m.newlabel('pl'), m.newlabel('nx'), m.newlabel('dn')
    rdl(m, RXWR, 0); m.imm32(RXQ, 1); a.cmphs(1, 0); m.bf_far(dn); m.imm32(RXQ_END, 1); a.cmphi(1, 0); m.bt_far(dn)
    a.mov_imm(64, 8)
    a.deflabel(pl)
    rdl(m, RXWR, 0); ld(m, RD, 9); a.cmpeq(0, 9); m.bt_far(dn)
    a.movb_ld(9, 10); a.extu_b(10, 10)                                               # r10 = byte
    inc, sv = m.newlabel('inc'), m.newlabel('sv')
    m.imm32(RXQ_END, 1); a.cmpeq(1, 9); a.bf_lbl(inc); m.imm32(RXQ, 9); a.bra_lbl(sv); a.nop()
    a.deflabel(inc); a.add_imm(1, 9)
    a.deflabel(sv); st(m, 9, RD)
    data, clr = m.newlabel('data'), m.newlabel('clr')
    m.imm32(0xF8, 0); a.cmphs(0, 10); m.bt_far(nx)                                    # realtime: ignored
    m.imm32(0x80, 0); a.cmphs(0, 10); m.bf_far(data)
    m.imm32(0xF0, 0); a.cmphs(0, 10); m.bt_far(clr)
    st(m, 10, RS); sti(m, 0, NB); jmpl(m, nx)
    a.deflabel(clr); sti(m, 0, RS); jmpl(m, nx)
    m.pool_jump()
    a.deflabel(data)
    ld(m, RS, 11); a.tst_rr(11, 11); m.bt_far(nx)
    a.mov_rm_rn(11, 0); a.and_imm(0xE0); m.imm32(0x80, 1); a.cmpeq(1, 0); m.bf_far(nx)  # 0x8n / 0x9n only
    sec = m.newlabel('sec')
    ld(m, NB, 0); a.tst_rr(0, 0); m.bf_far(sec)
    st(m, 10, D1); sti(m, 1, NB); jmpl(m, nx)
    a.deflabel(sec); sti(m, 0, NB)
    chok, noff = m.newlabel('chok'), m.newlabel('noff')
    ld(m, SAVCH, 0); a.shlr2(0); a.shlr(0); a.and_imm(0x1F); a.tst_rr(0, 0); m.bt_far(chok)
    a.mov_rm_rn(11, 2); a.mov_imm(15, 1); a.and_rr(1, 2); a.add_imm(1, 2); a.cmpeq(2, 0); m.bf_far(nx)
    a.deflabel(chok)
    ld(m, D1, 4)
    a.mov_rm_rn(11, 0); a.and_imm(0xF0); m.imm32(0x90, 1); a.cmpeq(1, 0); m.bf_far(noff)
    a.tst_rr(10, 10); m.bt_far(noff)
    a.mov_rm_rn(10, 5); call(m, 'ml_on'); jmpl(m, nx)
    a.deflabel(noff); call(m, 'ml_off')
    a.deflabel(nx); a.dt(8); m.bf_far(pl)
    a.deflabel(dn); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- ml_off: r4 = note -> its pad index released (message 45). Uses r9-r12 ----
    a.deflabel('ml_off'); a.stsl_pr()
    fl, ff, fx = m.newlabel('fl'), m.newlabel('ff'), m.newlabel('fx')
    a.mov_rm_rn(4, 10); a.add_imm(1, 10); m.imm32(KEYS, 11); a.mov_imm(0, 9)
    a.deflabel(fl)
    a.mov_rm_rn(11, 1); a.add_rr(9, 1); a.movb_ld(1, 0); a.extu_b(0, 0); a.cmpeq(10, 0); a.bt_lbl(ff)
    a.add_imm(1, 9); a.mov_imm(NPAD, 0); a.cmpeq(0, 9); a.bf_lbl(fl); a.bra_lbl(fx); a.nop()
    a.deflabel(ff); a.mov_imm(0, 0); a.movb_st(0, 1)
    a.mov_imm(0, 12); mkmsg(m, MSG_OFF, 12, 9); call(m, 'ml_send')
    a.deflabel(fx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- ml_alloff: every held key released ----
    a.deflabel('ml_alloff'); a.stsl_pr()
    al, an = m.newlabel('al'), m.newlabel('an')
    m.imm32(KEYS, 11); a.mov_imm(0, 9)
    a.deflabel(al)
    a.mov_rm_rn(11, 1); a.add_rr(9, 1); a.movb_ld(1, 0); a.tst_rr(0, 0); a.bt_lbl(an)
    a.mov_imm(0, 0); a.movb_st(0, 1); a.mov_imm(0, 12); mkmsg(m, MSG_OFF, 12, 9); call(m, 'ml_send')
    a.deflabel(an); a.add_imm(1, 9); a.mov_imm(NPAD, 0); a.cmpeq(0, 9); a.bf_lbl(al)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- ml_on: r4 = note, r5 = velocity -> the 12 Levels pad, through a free pad index. Uses r9-r14 ----
    a.deflabel('ml_on'); a.stsl_pr()
    ret, velo, tune = m.newlabel('ret'), m.newlabel('velo'), m.newlabel('tune')
    a.mov_rm_rn(4, 13); a.mov_rm_rn(5, 12)
    call(m, 'ml_off')                                                                # same key still held: off first
    rdb(m, ROOT, 0); a.mov_rm_rn(13, 10); a.sub(0, 10)                               # r10 = key - root
    m.imm32(KEYS, 11)
    rdl(m, LV_MODE, 0); a.mov_imm(1, 1); a.cmpeq(1, 0); m.bt_far(tune)
    a.tst_rr(0, 0); m.bt_far(velo); jmpl(m, ret)
    m.pool_jump()
    # TUNE: -12..+12; pad index from 47 down; base = (key - root) - level + src % 12
    a.deflabel(tune)
    a.mov_imm(-12, 0); a.cmpge(0, 10); m.bf_far(ret); a.mov_imm(12, 0); a.cmpgt(0, 10); m.bt_far(ret)
    tl, tf = m.newlabel('tl'), m.newlabel('tf')
    a.mov_imm(NPAD - 1, 9)
    a.deflabel(tl)
    a.mov_rm_rn(11, 1); a.add_rr(9, 1); a.movb_ld(1, 0); a.tst_rr(0, 0); a.bt_lbl(tf)
    a.add_imm(-1, 9); a.cmppz(9); a.bt_lbl(tl); jmpl(m, ret)
    a.deflabel(tf)
    a.mov_rm_rn(9, 4); a.mov_imm(12, 5); pg._hcall(m, '__udiv'); a.mov_rm_rn(1, 14)  # r14 = level
    rdl(m, LV_SRC, 4); a.mov_imm(12, 5); pg._hcall(m, '__udiv')                       # r1 = src % 12
    a.mov_rm_rn(10, 6); a.sub(14, 6); a.add_rr(1, 6)                                  # r6 = base
    rdl(m, LV_BASE, 0); st(m, 0, SAVB); m.imm32(LV_BASE, 1); a.movl_st(6, 1)
    a.mov_rm_rn(11, 1); a.add_rr(9, 1); a.mov_rm_rn(13, 0); a.add_imm(1, 0); a.movb_st(0, 1)
    mkmsg(m, MSG_ON, 12, 9); call(m, 'ml_send')
    ld(m, SAVB, 0); m.imm32(LV_BASE, 1); a.movl_st(0, 1)
    jmpl(m, ret)
    m.pool_jump()
    # VELOCITY: keys root..root+11 = levels 0..11; pad index = level + 12 * bank (D down to A)
    a.deflabel(velo)
    a.cmppz(10); m.bf_far(ret); a.mov_imm(11, 0); a.cmpgt(0, 10); m.bt_far(ret)
    vl, vf = m.newlabel('vl'), m.newlabel('vf')
    a.mov_rm_rn(10, 9); a.add_imm(36, 9)
    a.deflabel(vl)
    a.mov_rm_rn(11, 1); a.add_rr(9, 1); a.movb_ld(1, 0); a.tst_rr(0, 0); a.bt_lbl(vf)
    a.add_imm(-12, 9); a.cmppz(9); a.bt_lbl(vl); jmpl(m, ret)
    a.deflabel(vf)
    a.mov_rm_rn(11, 1); a.add_rr(9, 1); a.mov_rm_rn(13, 0); a.add_imm(1, 0); a.movb_st(0, 1)
    mkmsg(m, MSG_ON, 12, 9); call(m, 'ml_send')
    a.deflabel(ret); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()


# =====================================================================================================================
def selftest(out):
    from validate import Harness
    from mpcmod import HOOKS
    from pages import ENABLE_DOIT, ENABLE_CANCEL
    from testkit import PageTest, FIRMWARE
    VLOG = 0x88E20000

    class OSTest(PageTest):
        """PageTest with the OS image: the MPC's REAL sequencer message / 12 Levels code runs; the voice is logged"""
        def __init__(self, pg, wav):
            self.pg = pg
            self.h = h = Harness(FIRMWARE); h.fill_scratch(); self.e = e = h.emu
            fw = open(FIRMWARE, 'rb').read(); OSB = 0x8849495C
            keep = [(HOOKS[h.hook][1], 4), (ENABLE_DOIT, 10), (ENABLE_CANCEL, 10)]
            saved = [(ad, [e.rd(ad + i, 2) for i in range(0, n, 2)]) for ad, n in keep]
            buf, off = e._region(OSB); buf[off:off + len(fw)] = fw
            for ad, ws in saved:
                for i, w in enumerate(ws): e.wr(ad + 2 * i, w, 2)
            for ad in (0x884C0076, 0x884C67BA, 0x884C6722, 0x884C672E): self.stub(ad)
            def vlog(s):     # voice 0x884C2E1A: log (pad, velocity, tune) = r7 low byte, stack bytes 0 / 8
                s.movl_pool(VLOG, 1); s.movl_ld(1, 0); s.add_imm(1, 0); s.movl_st(0, 1); s.shll2(0); s.shll2(0); s.add_rr(0, 1)
                s.movl_st(7, 1); s.movl_ld_disp(15, 0, 0); s.movl_st_disp(0, 4, 1); s.movl_ld_disp(15, 8, 0); s.movl_st_disp(0, 8, 1)
                s.rts(); s.nop()
            self.stub(0x884C2E1A, vlog)
            for i in range(6): h.ram_w32(LV + 4 * i, 0)                         # 12 Levels off (as after the boot)
            h.load_hub(); h.load_wav(wav); h.fire(10)

    t = OSTest(pg, out); h, e = t.h, t.e
    R32, W32 = h.ram_r32, h.ram_w32
    e.wr(MIDISET + 22, e.rd(MIDISET + 22, 1) | 0x40, 1)                        # internal sounds on
    PROG = 0x8837B5AC + e.rd(0x8847C0E2, 2) * 0x1F98
    for p in range(48): e.wr(PROG + 0x1ED8 + p, 35 + p, 1)
    W32(RXWR, RXQ)
    for i in range(6): W32(LV + 4 * i, 0)                                       # 12 Levels off (as after the boot)
    def midi(*bs):
        w = R32(RXWR)
        for b_ in bs: e.wr(w, b_, 1); w = RXQ if w == RXQ_END else w + 1
        W32(RXWR, w)
    def voices():
        n = R32(VLOG); r = []
        for k in range(n):
            q = VLOG + 16 * (k + 1); tune = e.rd(q + 8, 1); r.append((e.rd(q, 1), e.rd(q + 4, 1), tune - 256 if tune > 127 else tune))
        W32(VLOG, 0); return r
    keys = lambda: [e.rd(KEYS + i, 1) for i in range(NPAD)]
    W32(VLOG, 0)
    assert KEYS % 4 == 0
    e.wr(RXCH, (1 << 3) | 2, 1)                                                 # MPC receive channel 1, sync bits 2
    t.open(); t.fire(3)
    assert t.label(STL, 5) == " OFF ", t.label(STL, 5)
    midi(0x90, 60, 100); t.fire(3)
    v_ = voices(); assert v_ == [] and e.rd(RXCH, 1) == (1 << 3) | 2, (v_, hex(e.rd(RXCH, 1)))
    print("  ok 12 Levels off: keyboard ignored by the add-on, the MPC's MIDI receive channel untouched")

    # ---- TUNE ----
    W32(LV_ON, 1); W32(LV_SRC, 14); W32(LV_MODE, 1); W32(LV_BASE, 0); t.fire(3)
    assert e.rd(RXCH, 1) == 0xFA and t.label(STL, 5) == " TUNE", (hex(e.rd(RXCH, 1)), t.label(STL, 5))
    print("  ok 12 Levels TUNE on: the MPC's MIDI receive channel -> none (sync bits kept), page shows TUNE")
    midi(0x90, 60, 100); t.fire(1); assert voices() == [(14, 100, 0)]
    midi(0x90, 63, 80); t.fire(1); assert voices() == [(14, 80, 30)]
    midi(0x90, 48, 90, 0x90, 72, 127, 0x90, 55, 64); t.fire(1)
    assert voices() == [(14, 90, -120), (14, 127, 120), (14, 64, -50)], "chord in one fire, running status"
    assert R32(LV_BASE) == 0, "12 Levels base put back"
    midi(0x90, 47, 100, 0x90, 73, 100); t.fire(1); assert voices() == [], "outside +-12: nothing"
    held = sorted(x for x in keys() if x); assert held == [49, 56, 61, 64, 73], held
    midi(0x80, 63, 0, 0x90, 48, 0); t.fire(1); assert sorted(x for x in keys() if x) == [56, 61, 73]
    print("  ok TUNE: C3 = pad as it is, D#3 +3 (30), C2 -12, C4 +12, G2 -5 (chord in one fire), +-13 ignored, note off frees")
    midi(0x91, 64, 100); t.fire(1); assert voices() == [], "channel 2 while the MPC listens on 1"
    e.wr(RXCH, (0 << 3) | 2 | 0xF8, 1)                                           # (still 31 while on)
    W32(SAVCH, (0 << 3) | 2); midi(0x92, 64, 100); t.fire(1); assert voices() == [(14, 100, 40)], "receive ALL: any channel"
    W32(SAVCH, (1 << 3) | 2)
    e.wr(ROOT, 48, 1); midi(0x90, 50, 100); t.fire(1); assert voices() == [(14, 100, 20)], "ROOT C2: D2 = +2"
    e.wr(ROOT, 60, 1)
    midi(0xF8, 0x90, 0xF8, 61, 0xFE, 100); t.fire(1); assert voices() == [(14, 100, 10)], "clock bytes in between"
    midi(0xB0, 1, 64, 0xC0, 5, 0xF0, 0x7D, 1, 2, 0xF7, 62, 100); t.fire(1); assert voices() == [], "CC / PC / SysEx: no notes"
    print("  ok receive channel 1: channel 2 ignored; ALL: any channel; ROOT C2; clock / CC / SysEx in the stream")
    # queue wraps
    W32(RXWR, RXQ_END - 1); t.fire(1); midi(0x90, 65, 100); t.fire(1); assert voices() == [(14, 100, 50)], "queue wrap"

    # ---- VELOCITY ----
    W32(LV_MODE, 0); t.fire(2); assert t.label(STL, 5) == " VELO" and all(x == 0 for x in keys()), "mode change: all off"
    voices()
    midi(0x90, 60, 5, 0x90, 63, 5, 0x90, 71, 127, 0x90, 72, 100); t.fire(1)
    assert voices() == [(14, 10, 0), (14, 42, 0), (14, 127, 0)], "levels 0, 3, 11; C4 (12) ignored"
    print("  ok VELOCITY: keys C3, D#3, B3 -> levels 1, 4, 12 (velocity 10, 42, 127), C4 ignored; page shows VELO")
    # ---- off ----
    W32(LV_ON, 0); t.fire(2)
    assert e.rd(RXCH, 1) == (1 << 3) | 2 and all(x == 0 for x in keys()) and t.label(STL, 5) == " OFF "
    midi(0x90, 60, 100); t.fire(2); assert voices() == []
    print("  ok 12 Levels off: the MPC's MIDI receive channel back (1), held keys released, keyboard ignored")
    # in the background (another page)
    h.ram_w32(EVQ + 0x68, 0x88123456); W32(LV_ON, 1); W32(LV_MODE, 1); t.fire(3)
    midi(0x90, 62, 100); t.fire(1); assert voices() == [(14, 100, 20)] and e.rd(RXCH, 1) == 0xFA
    W32(LV_ON, 0); t.fire(2); assert e.rd(RXCH, 1) == (1 << 3) | 2
    print("  ok on any page: 12 Levels on -> the keyboard plays; off -> MIDI input back")
    print("OK: 25LEVELS.WAV built and tested")


if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', '..', '25LEVELS.WAV'); pg.build_wav(out); selftest(out)
