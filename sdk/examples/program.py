#!/usr/bin/env python3
"""PROGRAM - replaces the MPC's own Program page (MODE + pad 6): every pad parameter of the current program on one
add-on page, edited with the Q-Link slider (catch-up) or the data wheel, one pad at a time or the whole program.

    python sdk/examples/program.py          -> builds PROGRAM.WAV and tests it in the emulator

On the MPC500: copy PROGRAM.WAV to the CF card, LOAD it (after HUB.WAV). MODE + pad 6 opens it (MODE -> OTHER ->
"Program" too). The cursor on the page name + the wheel chooses the page, in this order:
  Program Select "Program Select" / "  3:BEATS      ^": the programs in memory (slots 1..128 in use); the wheel (cursor
                 on the program) or the slider changes the current program, like the MPC (OS select + refresh).
  Sample assign  "Sample assign  A" / " A5:L1:SNARE   ^"  as the MPC (pad " A5" / "A12"): layer L1..L4; the slider, or the wheel on the
                 name, scrolls the samples in memory (add-ons are not listed; "--" = none).
Every page answers the Q-Link slider (with catch-up, no on-screen hint: the user asked for none) AND the data wheel on the
field under the cursor. VEL RANGE: the slider moves the value the cursor is on (low / high). Filter type and decay mode
are wheel only (the slider moves the page's value).
  LEVEL, PITCH, FILTER (type, cutoff, "R:" resonance: the cursor on R: = wheel and slider on it), PAN, PLAY (one shot / note on), VOICE (poly / mono), MUTE (group): as before.
  ATTACK, DECAY (+ END / STRT decay mode), VEL RANGE (" A5:L2:  0-127", per layer), SAMPLE LEVEL (per layer),
  V>LEVEL, V>FILT, V>TUNE, V>ATK, V>START (velocity modulation): 0..100.
  PURGE          deletes the samples no pad of any program uses, like the MPC's Purge, but NEVER the add-ons (their
                 WAVs are samples: the MPC's own Purge would delete them and the HUB would uninstall them).
                 "DO IT:  7 UNUSED" -> DO IT -> "DEL   7? DO IT" -> DO IT -> "DELETING   3/  7" (one sample per
                 frame) -> "DELETED   7". CANCEL steps back / stops.
  ENTER DEFAULT  "ENTER DEFAULT" / "MPC PROGRAM MODE": DO IT opens the MPC's own Program page (not redirected).
Row 0 of the parameter pages: "ATTACK  PAD  A05" (page . PAD / PROG . pad). Row 1: the value (the slider changes it only once it has
passed the value shown: catch-up). Before a pad is hit: "HIT A PAD". Hit a pad (it plays as usual) -> it is the pad
shown. PAD: only that pad changes. PROG: every pad of the program that has a sample (and the pad shown): 0..100 values
move by the same amount (balance kept, each pad stops at its limit and comes back exactly), PLAY, VOICE, MUTE, filter
type and decay mode are SET on all of them. Sample assign, VEL RANGE and SAMPLE LEVEL are per pad (no PROG).
Changes go straight into the CURRENT program (save the program as usual to keep them).

OS addresses (MPC500 OS 1.31; "fw" = read in the firmware 2026-11-03, not yet confirmed on hardware):
  Current program 0x8837B5AC + idx*0x1F98, idx = u16 at 0x8847C0E2 (SDK README); pad block = program + 0x18 + pad*0xA4;
  4 layers x 0x18: name16 (+0, empty = 0), +0x11 sample level, +0x12/+0x13 velocity range, +0x14 tune s16 cents, +0x16
  play mode (SDK README, Pitch, S500, Audio Track). Pad: +0x8F level, +0x90 pan, +0x72 filter cutoff (Mixer, HW);
  +0x62 voice, +0x63 mute group (SDK README, HW); +0x71 filter type 0..3 ("--" "LP" "BP" "HP", 0x8850572C).
  fw: the OS parameter getter 0x884F04AE reads, in the order of the MPC's Program menu: layer +0x11, +0x14, +0x16,
  +0x12, +0x13, pad +0x71, +0x72, +0x73 (resonance), +0x66 (attack), +0x67 (decay), +0x68 (decay mode), +0x6B
  (vel>level), +0x78 (vel>filter), +0x70 (vel>tune), +0x69 (vel>attack), +0x6A (vel>start), +0x62, +0x63; the setter
  0x884EFxxx clamps them to 0..100 (decay mode 0..1 "End" / "Strt"). PAD_INIT 0x884B52D0 defaults match.
  fw: Program Select = OS select 0x884B4B8E(r4 = 0x8837B5AC, r5 = slot; only if the slot's size word != 0) then OS
  refresh 0x884B48C0(r4 = 0x8837B5AC), the calls the MPC's own Program Select wheel makes (callback 0x884D9604).
  fw: Sample assign = the 16-byte name copied into the layer (OS assign 0x884B491E). Samples: MGR 0x8848ED30 (+0 list
  start, +4 limit, +8 current), block +0 size, +4 name16, PCM +0x100; add-on WAVs carry "MPCADDN3" / "MPCHUB_3" at +0x100
  (hub.py). "Used" = the OS test 0x884B5096(r4 = 0x8837B5AC, r5 = block) that the OS Purge 0x884C51AA uses (programs
  1..128, every pad and layer, by name). Delete = OS 0x884C5FFC on the current sample (S500).
  fw: MPC Program page object 0x884976B8 (built by 0x884DA2xx with descriptor 0x881C49E0 = handler 0x884D90CC, the
  Program menu's DO IT code); checked at run time (+0x1C = VT_PAGE, +0x30 = 0x884D90CC) before it is redirected or
  opened. MODE page 0x8849C9C4 (S500 menu, HW). A page opened straight from the MODE page that is the Program page ->
  back + this page (as the S500 menu does for MODE + pad 4, HW 2026-11-02).
  Pad events 0x38, bank byte 0 of 0x8849C98C, Q-Link slider 0x8848C3E4 (Master FX, Mixer), SET_FOCUS 0x884E9B4E.
"""
import sys, os, struct
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import PageAddon, ld, st, sti, SET_FOCUS
from mpcmod import EVQ, OS_PUSH_PAGE

PROG_BASE, PROG_IDX, PROG_STRIDE = 0x8837B5AC, 0x8847C0E2, 0x1F98
PAD_OFF, PAD_SIZE, LAYER, TUNE_OFF, PLAY_OFF = 0x18, 0xA4, 0x18, 0x14, 0x16
SLVL_OFF, VLO_OFF, VHI_OFF = 0x11, 0x12, 0x13
FTYPE_OFF, DMODE_OFF = 0x71, 0x68
NPADS = 48
BANKOBJ = 0x8849C98C
SLIDER = 0x8848C3E4
SL_LO, SL_HI, HYST = 64, 960, 3
TUNE_MAX = 3600
MGR, DELETE_CMD, ISUSED = 0x8848ED30, 0x884C5FFC, 0x884B5096
MODEPG, OSPROG, OSPROG_H, VT_PAGE = 0x8849C9C4, 0x884976B8, 0x884D90CC, 0x88507BA8
NONE, UNKNOWN = 0xFF, 0xFFFF
DONE_FIRES = 600
PSELECT, PREFRESH, NPROGS = 0x884B4B8E, 0x884B48C0, 128

(P_PGM, P_ASSIGN, P_LEVEL, P_PITCH, P_FILTER, P_PAN, P_PLAY, P_VOICE, P_MUTE, P_ATTACK, P_DECAY, P_VRANGE, P_SLEVEL,
 P_VLEV, P_VFILT, P_VTUNE, P_VATK, P_VSTART, P_PURGE, P_ENTER) = range(20)
P_RESO = 20                                                 # not a page: FILTER with the cursor on "R:"
NAMES = ["Program", "Sample", "LEVEL", "PITCH", "FILTER", "PAN", "PLAY", "VOICE", "MUTE", "ATTACK", "DECAY", "VEL RAN",
         "SAMPLE", "V>LEVEL", "V>FILT", "V>TUNE", "V>ATK", "V>START", "PURGE", "ENTER D"]
TITLE2 = {P_PGM: " Select", P_ASSIGN: "assign", P_VRANGE: "GE", P_SLEVEL: "LEVEL", P_ENTER: "EFAULT"}   # x7..15
K_REL, K_PITCH, K_PLAY, K_ABS, K_PGM, K_SMP = range(6)
PINFO = {P_PGM: (K_PGM, None, 0), P_ASSIGN: (K_SMP, None, 0), P_VRANGE: (K_ABS, None, 127),
         P_LEVEL: (K_REL, 0x8F, 100), P_PITCH: (K_PITCH, None, 24), P_FILTER: (K_REL, 0x72, 100),
         P_PAN: (K_REL, 0x90, 100), P_PLAY: (K_PLAY, None, 1), P_VOICE: (K_ABS, 0x62, 1), P_MUTE: (K_ABS, 0x63, 32),
         P_ATTACK: (K_REL, 0x66, 100), P_DECAY: (K_REL, 0x67, 100), P_SLEVEL: (K_REL, SLVL_OFF, 100),
         P_RESO: (K_REL, 0x73, 100), P_VLEV: (K_REL, 0x6B, 100), P_VFILT: (K_REL, 0x78, 100),
         P_VTUNE: (K_REL, 0x70, 100), P_VATK: (K_REL, 0x69, 100), P_VSTART: (K_REL, 0x6A, 100)}
SCOPED = [P_LEVEL, P_PITCH, P_FILTER, P_PAN, P_PLAY, P_VOICE, P_MUTE, P_ATTACK, P_DECAY, P_VLEV, P_VFILT,
          P_VTUNE, P_VATK, P_VSTART]                       # pages with PAD / PROG
LAYERED = [P_ASSIGN, P_VRANGE, P_SLEVEL]                    # "05:L1:" pages (per pad)
NOPAD = [P_PURGE, P_ENTER]                                  # no pad, row 1 = one text
SPIN = [P_PGM, P_ASSIGN]                                    # a name scrolled by the wheel / slider
TITLES = {p: NAMES[p].ljust(7) + TITLE2.get(p, "") for p in range(20)}
assert TITLES[P_ASSIGN] == "Sample assign" and TITLES[P_ENTER] == "ENTER DEFAULT" and TITLES[P_VRANGE] == "VEL RANGE"
assert TITLES[P_PGM] == "Program Select"

pg = PageAddon("program", open_press=None)
pg.other_menu("Program")
pg.far_hend = True
pg.far_fire = True                                         # fire code > 4 KB
pg.message()
def pan_txt(v): return "  C" if v == 50 else ("L%2d" % (50 - v) if v < 50 else "R%2d" % (v - 50))
FTYPES = ["--", "LP", "BP", "HP"]
# row 0: page . rest of the title / scope . pad (drawn in this order: the scope covers the title's tail)
PARAM = pg.choice("", 0, 0, [n.ljust(7) for n in NAMES], default=P_LEVEL)   # user 2026-11-03: opens on LEVEL (order unchanged)
T2 = pg.dlabel(7, 0, 9)
SCOPE = pg.choice("", 8, 0, ["PAD ", "PROG"], default=0)
PADL = pg.dlabel(13, 0, 3)
# row 1: texts first, then the fields (hidden fields leave the texts' spaces)
HINT = pg.dlabel(0, 1, 10)                                 # "HIT A PAD" / "05:" / "05:      -"
MOVEL = pg.dlabel(10, 1, 6)                                # blank (VEL RANGE: its "-")
R1 = pg.dlabel(0, 1, 16)                                   # PURGE / ENTER DEFAULT
PGMF = pg.choice("", 0, 1, [" " * 15] * 3, default=1)      # spinner: the wheel moves it off 1, we step and put it back
TYPE = pg.choice("", 0, 1, FTYPES, default=1)              # FILTER
LAYF = pg.choice("", 4, 1, ["L1:", "L2:", "L3:", "L4:"], default=0)  # after " A1:" / "A12:"
V100 = pg.choice("", 2, 1, ["%3d" % v for v in range(101)], default=100, share=True)   # share: one 0..100 table (2026-11-04: -808 B = 1 page less)
VPIT = pg.choice("", 2, 1, ["%+3d" % (i - 12) if i != 12 else "  0" for i in range(25)], default=12)
VPAN = pg.choice("", 2, 1, [pan_txt(v) for v in range(101)], default=50)
VPLAY = pg.choice("", 2, 1, ["ONE SHOT", "NOTE ON "], default=0)
VVOICE = pg.choice("", 2, 1, ["POLY", "MONO"], default=0)
VMUTE = pg.choice("", 2, 1, ["OFF"] + ["%3d" % g for g in range(1, 33)], default=0)
VSL = pg.choice("", 7, 1, ["%3d" % v for v in range(101)], default=100, share=True)
VRES = pg.choice("", 8, 1, ["%3d" % v for v in range(101)], default=0, share=True)     # FILTER "R:" (resonance)
DMODE = pg.choice("", 12, 1, ["END ", "STRT"], default=0)  # where the MPC shows it
SMPF = pg.choice("", 7, 1, [" " * 8] * 3, default=1)       # spinner, as PGMF
VLO = pg.choice("", 7, 1, ["%3d" % v for v in range(128)], default=0)
VHI = pg.choice("", 11, 1, ["%3d" % v for v in range(128)], default=127)   # x10 = "-" (MOVEL text)
VF = {p: V100 for p in PINFO if p not in (P_PGM, P_ASSIGN, P_VRANGE)}   # value field of each parameter
VF.update({P_PITCH: VPIT, P_PAN: VPAN, P_PLAY: VPLAY, P_VOICE: VVOICE, P_MUTE: VMUTE, P_SLEVEL: VSL, P_RESO: VRES})
VOPTS = {V100: ["%3d" % v for v in range(101)], VPIT: ["%+3d" % (i - 12) if i != 12 else "  0" for i in range(25)],
         VPAN: [pan_txt(v) for v in range(101)], VPLAY: ["ONE SHOT", "NOTE ON "], VVOICE: ["POLY", "MONO"],
         VMUTE: ["OFF"] + ["%3d" % g for g in range(1, 33)], VSL: ["%3d" % v for v in range(101)],
         VRES: ["%3d" % v for v in range(101)]}
CURSOR = [PARAM, SCOPE, PGMF, TYPE, LAYF, V100, VPIT, VPAN, VPLAY, VVOICE, VMUTE, VSL, VRES, DMODE, SMPF, VLO, VHI]
def fid(slot): return [it[1] for it in pg.items if it[0] == 'choice' and it[-1] == slot][0]
FIDS = [fid(s) for s in CURSOR]
(PEND, SEL, BASEV, LASTV, CAUGHT, SIDE, SS, SF, SINIT, SLO, SHI, LPARAM, LSCOPE, LPROG, NSTEP, LASTF,
 EP, ESC, LLAY, LTYPE, LDMODE, LVLO, LVHI, SIDX, PREVPG, PST, PCUR, PCNT, PUNUSED, PDEL, PTIM, LHI, LEP, LDUMMY) = \
    [pg.alloc_slot() for _ in range(34)]
TXT = pg.alloc_buf(16)                                    # text of a spinner field being built
pg.defaults[SLO] = SL_LO; pg.defaults[SHI] = SL_HI; pg.defaults[SS] = 0xFF; pg.defaults[NSTEP] = 100
pg.defaults[EP] = NONE; pg.defaults[PUNUSED] = UNKNOWN
REF = pg.alloc_buf(NPADS)                                 # byte value of each pad when the reference was taken
INCL = pg.alloc_buf(NPADS)                                # 1 = this pad changes
REFT = pg.alloc_buf(NPADS * 8)                            # tune (s16) of each layer of each pad at the reference

def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()
def jmpl(m, lbl): m.a.movl_pool(lbl, 2); m.a.jmp(2); m.a.nop()
def rdb(m, addr, r): m.imm32(addr, r); m.a.movb_ld(r, r); m.a.extu_b(r, r)
def wrb(m, rv, addr): m.imm32(addr, 1); m.a.movb_st(rv, 1)
def redraw(m): m.imm32(EVQ + 0x70, 1); m.a.mov_imm(1, 0); m.a.movl_st(0, 1)
def prog(m, rd_):
    """rd_ = first pad block of the current program. Uses r1"""
    a = m.a
    m.imm32(PROG_IDX, 1); a.movw_ld(1, rd_); a.extu_w(rd_, rd_); m.imm32(PROG_STRIDE, 1); a.mul_l(1, rd_); a.sts_macl(rd_)
    m.imm32(PROG_BASE + PAD_OFF, 1); a.add_rr(1, rd_)
def padblk(m, rd_):
    """rd_ = block of the pad shown (SEL - 1). Uses r0, r1"""
    a = m.a
    prog(m, rd_); ld(m, SEL, 0); a.add_imm(-1, 0); m.imm32(PAD_SIZE, 1); a.mul_l(1, 0); a.sts_macl(0); a.add_rr(0, rd_)
def layblk(m, rd_):
    """rd_ = layer LAYF of the pad shown. Uses r0, r1"""
    a = m.a
    padblk(m, rd_); rdb(m, LAYF, 0); a.mov_imm(LAYER, 1); a.mul_l(1, 0); a.sts_macl(0); a.add_rr(0, rd_)
def in_set(m, r, vals, lbl):
    """jump to lbl if r is one of vals. Uses r0 (r != 0)"""
    for v in vals: m.a.mov_imm(v, 0); m.a.cmpeq(0, r); m.bt_far(lbl)
def isep(m, p, lbl):
    """jump to lbl if the effective parameter EP == p. Uses r0, r1"""
    ld(m, EP, 0); m.a.mov_imm(p, 1); m.a.cmpeq(1, 0); m.bt_far(lbl)
def poff(m, r):
    """r = offset of EP's byte in the pad block (SAMPLE LEVEL: in layer LAYF). Uses r0, r1"""
    a = m.a
    ld(m, EP, 0); m.imm32(0x8F, r)
    for p, (k, o, n) in PINFO.items():
        if o is None or p == P_LEVEL: continue
        nx = m.newlabel('po'); a.mov_imm(p, 1); a.cmpeq(1, 0); a.bf_lbl(nx); m.imm32(o, r); a.deflabel(nx)
    nx = m.newlabel('pl'); a.mov_imm(P_SLEVEL, 1); a.cmpeq(1, 0); a.bf_lbl(nx)
    rdb(m, LAYF, 0); a.mov_imm(LAYER, 1); a.mul_l(1, 0); a.sts_macl(0); a.add_rr(0, r); a.deflabel(nx)
    nx = m.newlabel('pv'); ld(m, EP, 0); a.mov_imm(P_VRANGE, 1); a.cmpeq(1, 0); a.bf_lbl(nx)   # VEL RANGE: low / high
    ld(m, LHI, 0); m.imm32(VLO_OFF, r); a.add_rr(0, r)
    rdb(m, LAYF, 0); a.mov_imm(LAYER, 1); a.mul_l(1, 0); a.sts_macl(0); a.add_rr(0, r); a.deflabel(nx)
def vaddr(m):
    """r2 = address of EP's value field (VEL RANGE: the one with the cursor). Uses r0, r1"""
    a = m.a
    ld(m, EP, 0); m.imm32(V100, 2)
    for p, f in VF.items():
        if f == V100: continue
        nx = m.newlabel('va'); a.mov_imm(p, 1); a.cmpeq(1, 0); a.bf_lbl(nx); m.imm32(f, 2); a.deflabel(nx)
    nx = m.newlabel('vv'); a.mov_imm(P_VRANGE, 1); a.cmpeq(1, 0); a.bf_lbl(nx)
    m.imm32(VLO, 2); ld(m, LHI, 0); a.tst_rr(0, 0); a.bt_lbl(nx); m.imm32(VHI, 2); a.deflabel(nx)
def hiflag(m, r):
    """r = 1 when on VEL RANGE with the cursor on the high value. Uses r0, r1 (r != 0, 1)"""
    a = m.a; nx = m.newlabel('hf')
    a.mov_imm(0, r); rdb(m, PARAM, 0); a.mov_imm(P_VRANGE, 1); a.cmpeq(1, 0); a.bf_lbl(nx)
    m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 0); m.imm32(pg.addr['values'][fid(VHI)], 1); a.cmpeq(1, 0)
    a.movt(r); a.deflabel(nx)
def needsel(m, skip):
    """go to skip when no pad was hit yet (Program Select needs none). Uses r0, r1"""
    a = m.a; ok = m.newlabel('hs')
    ld(m, SEL, 0); a.tst_rr(0, 0); m.bf_far(ok)
    rdb(m, PARAM, 0); a.mov_imm(P_PGM, 1); a.cmpeq(1, 0); m.bf_far(skip)
    a.deflabel(ok)
def nsteps(m, r):
    """r = steps of EP (wheel / slider). Uses r0, r1"""
    a = m.a
    ld(m, EP, 0); a.mov_imm(100, r)
    for p, (k, o, n) in PINFO.items():
        if n == 100: continue
        nx = m.newlabel('ns'); a.mov_imm(p, 1); a.cmpeq(1, 0); a.bf_lbl(nx); a.mov_imm(n, r); a.deflabel(nx)
def isaddon(m, rb, yes):
    """jump to yes if the sample block rb is an add-on WAV ("MPC" at +0x100). Uses r0, r1, r2, r3"""
    a = m.a; no = m.newlabel('nad')
    m.imm32(0x100, 3); a.add_rr(rb, 3)
    for ch in "MPC": a.movb_ldinc(3, 0); a.mov_imm(ord(ch), 1); a.cmpeq(1, 0); a.bf_lbl(no)
    jmpl(m, yes)
    a.deflabel(no)

def fire(m):
    a = m.a
    call(m, 'pr_bg')
    call(m, 'pr_ep')
    call(m, 'pr_vis')
    call(m, 'pr_slider')
    sel, disp, nr = m.newlabel('sel'), m.newlabel('disp'), m.newlabel('nr')
    rdb(m, PARAM, 9); a.mov_imm(P_PURGE, 0); a.cmpeq(0, 9); npu = m.newlabel('npu'); m.bf_far(npu)
    call(m, 'pu_fire'); jmpl(m, disp)
    a.deflabel(npu)
    ld(m, PEND, 0); a.tst_rr(0, 0); m.bf_far(sel)
    needsel(m, disp)
    # page, scope, layer, low/high cursor or program changed -> new reference
    rdb(m, PARAM, 0); ld(m, LPARAM, 1); a.cmpeq(1, 0); m.bf_far(sel)
    ld(m, ESC, 0); ld(m, LSCOPE, 1); a.cmpeq(1, 0); m.bf_far(sel)
    rdb(m, LAYF, 0); ld(m, LLAY, 1); a.cmpeq(1, 0); m.bf_far(sel)
    hiflag(m, 9); ld(m, LHI, 0); a.cmpeq(9, 0); m.bf_far(sel)
    ld(m, EP, 0); ld(m, LEP, 1); a.cmpeq(1, 0); m.bf_far(sel)                         # FILTER: cutoff <-> "R:"
    m.imm32(PROG_IDX, 1); a.movw_ld(1, 0); a.extu_w(0, 0); ld(m, LPROG, 1); a.cmpeq(1, 0); m.bt_far(nr)
    a.deflabel(sel); call(m, 'pr_select')
    a.deflabel(nr)
    needsel(m, disp)
    m.pool_jump()
    rdb(m, PARAM, 9)
    n2 = m.newlabel('n2'); a.mov_imm(P_VRANGE, 0); a.cmpeq(0, 9); m.bf_far(n2); call(m, 'vr_fire')
    a.deflabel(n2)
    ld(m, EP, 0); m.imm32(NONE, 1); a.cmpeq(1, 0); m.bt_far(disp)
    slid = m.newlabel('slid')
    # program / sample name: the wheel moved the field off 1 -> one step (as the slider would), field back to 1
    for F, p in ((PGMF, P_PGM), (SMPF, P_ASSIGN)):
        nx, sk, dw, ap = m.newlabel('sn'), m.newlabel('sk'), m.newlabel('sd'), m.newlabel('sa')
        ld(m, EP, 0); a.mov_imm(p, 1); a.cmpeq(1, 0); m.bf_far(nx)
        rdb(m, F, 9); a.mov_imm(1, 0); a.cmpeq(0, 9); m.bt_far(slid)
        wrb(m, 0, F)                                                                  # r0 = 1
        ld(m, LASTV, 10); ld(m, NSTEP, 2)
        a.mov_imm(2, 0); a.cmpeq(0, 9); a.bf_lbl(dw)
        a.cmphi(10, 2); m.bf_far(slid); a.add_imm(1, 10); a.bra_lbl(ap); a.nop()      # up, not past the last
        a.deflabel(dw); a.tst_rr(10, 10); m.bt_far(slid); a.add_imm(-1, 10)          # down, not below the first
        a.deflabel(ap); st(m, 10, LASTV); call(m, 'pr_apply'); sti(m, 0, CAUGHT)
        ld(m, LASTV, 9); ld(m, SS, 10); a.cmphi(9, 10); a.movt(0); st(m, 0, SIDE)
        jmpl(m, slid)
        a.deflabel(nx); m.pool_jump()
    # filter type / decay mode changed with the wheel (put back when it is not their page)
    for F, LF, off, p in ((TYPE, LTYPE, FTYPE_OFF, P_FILTER), (DMODE, LDMODE, DMODE_OFF, P_DECAY)):
        sk = m.newlabel('ax')
        rdb(m, F, 9); ld(m, LF, 0); a.cmpeq(9, 0); m.bt_far(sk)
        m.imm32(off, 10); m.imm32(F, 11); m.imm32(LF, 12); a.mov_imm(p, 13); call(m, 'pr_aux')
        a.deflabel(sk)
    m.pool_jump()
    # wheel: the OS changed the field -> apply, the slider has to catch the new value
    vaddr(m); a.movb_ld(2, 9); a.extu_b(9, 9)
    ld(m, LASTV, 0); a.cmpeq(9, 0); nw = m.newlabel('nw'); m.bt_far(nw)
    st(m, 9, LASTV); call(m, 'pr_apply'); sti(m, 0, CAUGHT)
    ld(m, LASTV, 9); ld(m, SS, 10); a.cmphi(9, 10); a.movt(0); st(m, 0, SIDE)
    a.deflabel(nw)
    # slider, catch-up
    a.deflabel(slid)
    fol = m.newlabel('fol'); cat = m.newlabel('cat')
    ld(m, CAUGHT, 0); a.tst_rr(0, 0); m.bf_far(fol)
    ld(m, LASTV, 9); ld(m, SS, 10); a.cmpeq(9, 10); m.bt_far(cat)
    a.cmphi(9, 10); a.movt(2); ld(m, SIDE, 0); a.cmpeq(2, 0); m.bf_far(cat)          # passed the value: caught
    jmpl(m, disp); m.pool_jump()
    a.deflabel(cat); sti(m, 1, CAUGHT)
    a.deflabel(fol)
    ld(m, LASTV, 9); ld(m, SS, 10); a.cmpeq(9, 10); m.bt_far(disp)
    st(m, 10, LASTV); a.mov_rm_rn(10, 9); call(m, 'pr_setf'); call(m, 'pr_apply')
    redraw(m)                                                                        # the OS redraws the value field
    a.deflabel(disp)
    call(m, 'pr_disp')
    jmpl(m, '__pr_end'); m.pool_jump()
    subs(m)
    a.deflabel('__pr_end')
pg.on_fire(fire)

def inactive(m):
    a = m.a
    call(m, 'pr_bg')
    sti(m, 0xFF, LPARAM); sti(m, 0, SINIT)                                           # left the page: the slider may be elsewhere on return, so it has to catch the value again (user 2026-11-08: jumps on re-entry)
    ld(m, PST, 0); a.tst_rr(0, 0); x = m.newlabel('ix'); m.bt_far(x)                 # left the page: Purge stops
    sti(m, 0, PST); sti(m, 0, PCUR); sti(m, UNKNOWN, PUNUSED)
    a.deflabel(x)
pg.on_inactive(inactive)

def subs(m):
    a = m.a
    # ---- pr_bg (every fire, any page): MODE + pad 6 -> this page. r0-r9 ----
    a.deflabel('pr_bg'); a.stsl_pr()
    same = m.newlabel('same')
    m.imm32(EVQ + 0x68, 1); a.movl_ld(1, 9)                                          # r9 = current page
    ld(m, PREVPG, 0); a.cmpeq(9, 0); m.bt_far(same)
    a.mov_rm_rn(0, 8); st(m, 9, PREVPG)                                              # r8 = the page before
    m.imm32(MODEPG, 1); a.cmpeq(1, 8); m.bf_far(same)
    m.imm32(OSPROG, 1); a.cmpeq(1, 9); m.bf_far(same)
    a.movl_ld_disp(9, 0x1C, 0); m.imm32(VT_PAGE, 1); a.cmpeq(1, 0); m.bf_far(same)     # really the Program page?
    a.movl_ld_disp(9, 0x30, 0); m.imm32(OSPROG_H, 1); a.cmpeq(1, 0); m.bf_far(same)
    m.imm32(EVQ, 4); a.mov_imm(0, 5); a.mov_imm(2, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()   # back
    m.imm32(EVQ, 4); m.imm32(pg.addr['page'], 5); a.mov_imm(0, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()
    m.imm32(pg.addr['page'], 0); st(m, 0, PREVPG)
    a.deflabel(same)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- pr_ep: EP = the parameter the slider / value machinery edits (PURGE, ENTER: NONE), ESC = scope ----
    a.deflabel('pr_ep'); a.stsl_pr()
    rdb(m, PARAM, 9); m.imm32(NONE, 2)
    for p in PINFO:
        nx = m.newlabel('ep'); a.mov_imm(p, 0); a.cmpeq(0, 9); a.bf_lbl(nx); a.mov_imm(p, 2); a.deflabel(nx)
    nx = m.newlabel('er'); a.mov_imm(P_FILTER, 0); a.cmpeq(0, 9); a.bf_lbl(nx)          # FILTER, cursor on "R:"
    m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 0); m.imm32(pg.addr['values'][fid(VRES)], 1); a.cmpeq(1, 0)
    a.bf_lbl(nx); a.mov_imm(P_RESO, 2); a.deflabel(nx)
    st(m, 2, EP)
    a.mov_imm(0, 3); yes, no = m.newlabel('sy'), m.newlabel('sn')
    in_set(m, 9, SCOPED, yes); jmpl(m, no)
    a.deflabel(yes); rdb(m, SCOPE, 3)
    a.deflabel(no); st(m, 3, ESC)
    kp = m.newlabel('kp'); a.mov_imm(P_PURGE, 0); a.cmpeq(0, 9); m.bt_far(kp)          # not on PURGE: its state resets
    sti(m, 0, PST); sti(m, 0, PCUR); sti(m, UNKNOWN, PUNUSED)
    a.deflabel(kp)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- pr_vis: fields and texts of the chosen page; the cursor skips hidden fields (it goes on in the direction
    #      it was moving, or back where it was). r0-r7, r9-r14 ----
    a.deflabel('pr_vis'); a.stsl_pr()
    W = [pg.addr['values'][f] for f in FIDS]
    DL = lambda i: pg.addr['dlabels'][i] - 0x28                                       # the text follows its widget
    a.mov_imm(0, 14)
    rdb(m, PARAM, 4); ld(m, SEL, 5)
    def vis(w, params, need_sel=True, invert=False):
        n1, n2, yes = m.newlabel('v1'), m.newlabel('v2'), m.newlabel('vy')
        m.imm32(w + 0x17, 1); a.movb_ld(1, 0); a.extu_b(0, 0); a.mov_rm_rn(0, 3); a.and_imm(0x7F)
        if need_sel: a.tst_rr(5, 5); m.bt_far(n1)
        for p in params: a.mov_imm(p, 2); a.cmpeq(2, 4); m.bt_far(n1 if invert else yes)
        if not invert: jmpl(m, n1)
        a.deflabel(yes); a.or_imm(0x80)
        a.deflabel(n1); a.cmpeq(0, 3); a.bt_lbl(n2); a.movb_st(0, 1); a.mov_imm(1, 14); a.deflabel(n2)
        m.pool_jump()
    vis(W[1], SCOPED, need_sel=False)
    vis(W[CURSOR.index(PGMF)], [P_PGM], need_sel=False)
    vis(W[CURSOR.index(TYPE)], [P_FILTER]); vis(W[CURSOR.index(LAYF)], LAYERED)
    for k, f in enumerate(CURSOR):
        if f in VOPTS and f != VRES: vis(W[k], [p for p in VF if VF[p] == f])
    vis(W[CURSOR.index(VRES)], [P_FILTER])
    vis(W[CURSOR.index(DMODE)], [P_DECAY]); vis(W[CURSOR.index(SMPF)], [P_ASSIGN])
    vis(W[CURSOR.index(VLO)], [P_VRANGE]); vis(W[CURSOR.index(VHI)], [P_VRANGE])
    vis(DL(HINT), NOPAD, need_sel=False, invert=True); vis(DL(MOVEL), NOPAD, need_sel=False, invert=True)
    vis(DL(R1), NOPAD, need_sel=False)
    vis(DL(PADL), [P_PGM], need_sel=False, invert=True)                              # "Program Select" is 14 long
    vdone, vpick, vback, vgo = m.newlabel('vd'), m.newlabel('vp'), m.newlabel('vb'), m.newlabel('vg')
    def index(rs, rd_):
        a.mov_imm(-1, rd_)
        for i, w in enumerate(W):
            n = m.newlabel('ix'); m.imm32(w, 1); a.cmpeq(1, rs); a.bf_lbl(n); a.mov_imm(i, rd_); a.deflabel(n)
    m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 9)                               # r9 = focused widget
    index(9, 11); a.cmppz(11); m.bf_far(vdone)                                        # not one of ours
    m.pool_jump()
    a.mov_rm_rn(9, 1); a.add_imm(0x17, 1); a.movb_ld(1, 0); a.tst_imm(0x80); m.bf_far(vdone)   # shown: fine
    ld(m, LASTF, 10); index(10, 12)                                                   # r12 = where it was
    m.pool_jump()
    a.mov_imm(0, 13)
    a.cmppz(12); m.bf_far(vpick)
    a.cmpgt(12, 11); m.bf_far(vback)                                                  # moving right: next shown one
    for i in reversed(range(len(W))):
        n = m.newlabel('fw'); a.mov_imm(i, 0); a.cmpgt(12, 0); a.bf_lbl(n)
        m.imm32(W[i] + 0x17, 1); a.movb_ld(1, 0); a.tst_imm(0x80); a.bt_lbl(n); m.imm32(W[i], 13); a.deflabel(n)
    jmpl(m, vpick); m.pool_jump()
    a.deflabel(vback)                                                                 # moving left: previous shown one
    for i in range(len(W)):
        n = m.newlabel('bw'); a.mov_imm(i, 0); a.cmpgt(0, 12); a.bf_lbl(n)
        m.imm32(W[i] + 0x17, 1); a.movb_ld(1, 0); a.tst_imm(0x80); a.bt_lbl(n); m.imm32(W[i], 13); a.deflabel(n)
    m.pool_jump()
    a.deflabel(vpick)
    a.tst_rr(13, 13); m.bf_far(vgo)
    a.mov_rm_rn(10, 13); a.cmppz(12); m.bt_far(vgo); m.imm32(W[0], 13)                # none: back where it was / PARAM
    a.deflabel(vgo)
    m.imm32(pg.addr['page'], 4); a.mov_rm_rn(13, 5); m.imm32(SET_FOCUS, 2); a.jsr(2); a.nop(); a.mov_imm(1, 14)
    a.deflabel(vdone)
    m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 0); st(m, 0, LASTF)
    a.tst_rr(14, 14); vr = m.newlabel('vr'); m.bt_far(vr); redraw(m)
    a.deflabel(vr); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- pr_slider: SS = slider step 0..NSTEP (filtered, learned range, hysteresis), as Mixer. r0-r7 ----
    a.deflabel('pr_slider'); a.stsl_pr()
    m.imm32(SLIDER, 1); a.movl_ld(1, 2); m.imm32(0x3FF, 1); a.and_rr(1, 2); a.shll2(2); a.shll2(2)   # r2 = raw*16
    ld(m, SINIT, 0); a.tst_rr(0, 0); go = m.newlabel('go'); a.bf_lbl(go)
    st(m, 2, SF); sti(m, 1, SINIT)
    a.deflabel(go)
    ld(m, SF, 0); a.sub(0, 2); a.shar(2); a.shar(2); a.shar(2); a.add_rr(2, 0); st(m, 0, SF)
    a.mov_rm_rn(0, 6); a.shar(6); a.shar(6); a.shar(6); a.shar(6)                    # r6 = f (0..1023)
    ld(m, SLO, 0); c1 = m.newlabel('c1'); a.cmpgt(6, 0); a.bf_lbl(c1); st(m, 6, SLO); a.deflabel(c1)
    ld(m, SHI, 0); c2 = m.newlabel('c2'); a.cmpgt(0, 6); a.bf_lbl(c2); st(m, 6, SHI); a.deflabel(c2)
    ld(m, SLO, 7); a.sub(7, 6)                                                        # r6 = f - lo (>= 0)
    ld(m, SHI, 5); a.sub(7, 5); a.mov_rm_rn(5, 7)                                     # r7 = span
    a.mov_rm_rn(6, 4); ld(m, NSTEP, 0); a.mul_l(0, 4); a.sts_macl(4); a.mov_rm_rn(7, 5)
    a.mov_rm_rn(7, 0); a.shlr(0); a.add_rr(0, 4); pg._hcall(m, '__udiv')             # r0 = round((f-lo)*N/span)
    ld(m, NSTEP, 1); c3 = m.newlabel('c3'); a.cmpgt(1, 0); a.bf_lbl(c3); a.mov_rm_rn(1, 0); a.deflabel(c3)
    a.mov_rm_rn(0, 3)                                                                 # r3 = target
    ld(m, SS, 0); a.cmpeq(3, 0); sx = m.newlabel('sx'); m.bt_far(sx)
    ld(m, NSTEP, 1); a.cmphi(1, 0); setv = m.newlabel('set'); m.bt_far(setv)         # not set yet (> N)
    a.tst_rr(3, 3); m.bt_far(setv); ld(m, NSTEP, 1); a.cmpeq(1, 3); m.bt_far(setv)    # the ends: always exact
    # hysteresis: leave the current step only when f is more than half a step + HYST from its centre
    a.mov_rm_rn(0, 4); a.mul_l(7, 4); a.sts_macl(4); ld(m, NSTEP, 5)
    a.movl_st_dec(3, 15); pg._hcall(m, '__udiv'); a.movl_ld_inc(15, 3)                # r0 = SS*span/N
    a.sub(0, 6); a.cmppz(6); ab = m.newlabel('ab'); a.bt_lbl(ab); a.neg(6, 6); a.deflabel(ab)   # r6 = |f - centre|
    a.mov_rm_rn(7, 4); ld(m, NSTEP, 5); a.shll(5); a.movl_st_dec(3, 15); a.movl_st_dec(6, 15)
    pg._hcall(m, '__udiv'); a.movl_ld_inc(15, 6); a.movl_ld_inc(15, 3); a.add_imm(HYST, 0)   # r0 = span/2N + HYST
    a.cmphi(0, 6); m.bf_far(sx)
    a.deflabel(setv); st(m, 3, SS)
    a.deflabel(sx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- pr_select: (PEND = pad + 1 -> SEL) the pad's layer fields, filter type, decay mode, sample name; then for
    #      the value pages: steps, reference of all pads, which pads change, the pad's value in its field ----
    a.deflabel('pr_select'); a.stsl_pr()
    ld(m, PEND, 0); a.tst_rr(0, 0); kp = m.newlabel('kp'); a.bt_lbl(kp); st(m, 0, SEL); sti(m, 0, PEND)
    a.deflabel(kp)
    rdb(m, PARAM, 0); st(m, 0, LPARAM); ld(m, ESC, 0); st(m, 0, LSCOPE)
    m.imm32(PROG_IDX, 1); a.movw_ld(1, 0); a.extu_w(0, 0); st(m, 0, LPROG)
    rdb(m, LAYF, 0); st(m, 0, LLAY); hiflag(m, 2); st(m, 2, LHI); ld(m, EP, 0); st(m, 0, LEP)
    def loadf(rb, off, top, F, LS):
        n = m.newlabel('lf'); a.mov_rm_rn(rb, 1); m.imm32(off, 0); a.add_rr(0, 1); a.movb_ld(1, 0); a.extu_b(0, 0)
        a.mov_imm(top, 1); a.cmphi(1, 0); a.bf_lbl(n); a.mov_rm_rn(1, 0); a.deflabel(n)
        wrb(m, 0, F); st(m, 0, LS)
    layblk(m, 6)
    loadf(6, VLO_OFF, 127, VLO, LVLO); loadf(6, VHI_OFF, 127, VHI, LVHI)
    padblk(m, 11)
    loadf(11, FTYPE_OFF, 3, TYPE, LTYPE); loadf(11, DMODE_OFF, 1, DMODE, LDMODE)
    loadf(11, 0x73, 100, VRES, LDUMMY)                                                # FILTER shows both values
    m.pool_jump()
    nf = m.newlabel('nf'); rdb(m, PARAM, 0); a.mov_imm(P_FILTER, 1); a.cmpeq(1, 0); m.bf_far(nf)
    loadf(11, 0x72, 100, V100, LDUMMY)
    a.deflabel(nf)
    na = m.newlabel('na'); rdb(m, PARAM, 0); a.mov_imm(P_ASSIGN, 1); a.cmpeq(1, 0); m.bf_far(na); call(m, 'sp_load')
    a.deflabel(na)
    sdone = m.newlabel('sdone')
    ld(m, EP, 0); m.imm32(NONE, 1); a.cmpeq(1, 0); m.bt_far(sdone)
    dpit, dply, dv = m.newlabel('dpit'), m.newlabel('dply'), m.newlabel('dv')
    xp, xs = m.newlabel('xp'), m.newlabel('xs')
    isep(m, P_PGM, xp); isep(m, P_ASSIGN, xs)
    nsteps(m, 3); call(m, 'pr_nstep')
    m.pool_jump()
    poff(m, 13)                                                                       # r13 = parameter offset
    prog(m, 11)                                                                       # r11 = pad block 0
    ld(m, ESC, 14)                                                                    # r14 = 1: whole program
    ld(m, SEL, 12); a.add_imm(-1, 12)                                                 # r12 = pad shown
    m.imm32(REF, 7); m.imm32(INCL, 6); m.imm32(REFT, 5); a.mov_imm(0, 9)              # r9 = pad
    lp = m.newlabel('lp'); a.deflabel(lp)
    a.mov_rm_rn(11, 1); a.add_rr(13, 1); a.movb_ld(1, 0); a.movb_st(0, 7)             # REF[p] = byte
    for l in range(4):                                                                # REFT[p][l] = tune
        a.mov_rm_rn(11, 1); a.add_imm(l * LAYER + TUNE_OFF, 1); a.movw_ld(1, 0); a.movw_st(0, 5); a.add_imm(2, 5)
    a.mov_imm(0, 2)
    for l in range(4): a.mov_rm_rn(11, 1); a.add_imm(l * LAYER, 1); a.movb_ld(1, 0); a.or_rr(0, 2)
    a.extu_b(2, 2); a.tst_rr(2, 2); a.movt(2); a.mov_imm(1, 0); a.xor_rr(0, 2)        # r2 = has a sample
    a.and_rr(14, 2)                                                                   # ... and whole program
    a.cmpeq(12, 9); sp = m.newlabel('sp'); a.bf_lbl(sp); a.mov_imm(1, 2); a.deflabel(sp)   # the pad shown always
    a.movb_st(2, 6)
    m.imm32(PAD_SIZE, 0); a.add_rr(0, 11); a.add_imm(1, 7); a.add_imm(1, 6); a.add_imm(1, 9)
    a.mov_imm(NPADS, 0); a.cmpeq(0, 9); m.bf_far(lp)
    m.pool_jump()
    padblk(m, 11)                                                                     # r11 = block of the pad shown
    isep(m, P_PITCH, dpit); isep(m, P_PLAY, dply)
    m.imm32(REF, 1); a.add_rr(12, 1); a.movb_ld(1, 9); a.extu_b(9, 9); jmpl(m, dv)   # byte parameters
    m.pool_jump()
    a.deflabel(dpit)                                                                  # PITCH: first layer with a sample
    a.mov_imm(0, 4); a.mov_imm(0, 6)
    for l in reversed(range(4)):
        n = m.newlabel('pl'); a.mov_rm_rn(11, 1); a.add_imm(l * LAYER, 1); a.movb_ld(1, 0); a.tst_rr(0, 0); a.bt_lbl(n)
        a.mov_rm_rn(11, 1); a.add_imm(l * LAYER + TUNE_OFF, 1); a.movw_ld(1, 4); a.deflabel(n)
    a.mov_imm(0, 13); a.cmppz(4); pz = m.newlabel('pz'); a.bt_lbl(pz); a.neg(4, 4); a.mov_imm(1, 13); a.deflabel(pz)
    a.add_imm(50, 4); a.mov_imm(100, 5); pg._hcall(m, '__udiv')                      # rounded semitones
    a.tst_rr(13, 13); ng = m.newlabel('ng'); a.bt_lbl(ng); a.neg(0, 0); a.deflabel(ng)
    a.mov_imm(12, 1); a.cmpgt(1, 0); h1 = m.newlabel('h1'); a.bf_lbl(h1); a.mov_rm_rn(1, 0); a.deflabel(h1)
    a.mov_imm(-12, 1); a.cmpgt(0, 1); h2 = m.newlabel('h2'); a.bf_lbl(h2); a.mov_rm_rn(1, 0); a.deflabel(h2)
    a.add_imm(12, 0); a.mov_rm_rn(0, 9); jmpl(m, dv)
    m.pool_jump()
    a.deflabel(dply)                                                                  # PLAY: first layer with a sample
    a.mov_rm_rn(11, 1); a.add_imm(PLAY_OFF, 1); a.movb_ld(1, 9)
    for l in reversed(range(4)):
        n = m.newlabel('ql'); a.mov_rm_rn(11, 1); a.add_imm(l * LAYER, 1); a.movb_ld(1, 0); a.tst_rr(0, 0); a.bt_lbl(n)
        a.mov_rm_rn(11, 1); a.add_imm(l * LAYER + PLAY_OFF, 1); a.movb_ld(1, 9); a.deflabel(n)
    a.extu_b(9, 9); jmpl(m, dv)
    m.pool_jump()
    a.deflabel(xp)                                                                    # Program Select: its rank
    call(m, 'pg_info'); a.mov_rm_rn(2, 3); call(m, 'pr_nstep'); jmpl(m, dv)
    a.deflabel(xs)                                                                    # Sample assign: 0 = none, 1..N
    a.mov_imm(0, 4); a.mov_imm(0, 5); call(m, 'sm_walk'); a.mov_rm_rn(1, 3); call(m, 'pr_nstep')
    ld(m, SIDX, 9)
    m.pool_jump()
    a.deflabel(dv)
    ld(m, NSTEP, 0); a.cmphi(0, 9); ok = m.newlabel('ok'); a.bf_lbl(ok); a.mov_rm_rn(0, 9); a.deflabel(ok)   # 0..N
    st(m, 9, BASEV); st(m, 9, LASTV); call(m, 'pr_setf')
    ld(m, LASTV, 9); ld(m, SS, 10); a.cmpeq(9, 10); a.movt(0); st(m, 0, CAUGHT)
    a.cmphi(9, 10); a.movt(0); st(m, 0, SIDE)
    a.deflabel(sdone)
    redraw(m)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- pr_setf: value field of EP = r9. r0-r2 ----
    a.deflabel('pr_setf'); a.stsl_pr()
    sfx, sfp, sfs = m.newlabel('sfx'), m.newlabel('sfp'), m.newlabel('sfs')
    isep(m, P_PGM, sfp); isep(m, P_ASSIGN, sfs)
    vaddr(m); a.mov_rm_rn(9, 0); a.movb_st(0, 2); jmpl(m, sfx)
    a.deflabel(sfp); call(m, 'pg_text'); jmpl(m, sfx)                               # spinners: their text
    a.deflabel(sfs); call(m, 'sp_text')
    a.deflabel(sfx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- pr_nstep: r3 = steps of the value (not r2: call() uses it); new steps -> the slider's step now.
    #      Keeps r8-r14 ----
    a.deflabel('pr_nstep'); a.stsl_pr()
    ld(m, NSTEP, 0); a.cmpeq(3, 0); sn = m.newlabel('sn'); m.bt_far(sn)
    st(m, 3, NSTEP); m.imm32(0xFF, 0); st(m, 0, SS); call(m, 'pr_slider')
    a.deflabel(sn); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- pr_apply: LASTV -> every pad that changes (Program Select: the program; Sample assign: the layer) ----
    a.deflabel('pr_apply'); a.stsl_pr()
    apit, aply, aabs, ax = m.newlabel('apit'), m.newlabel('aply'), m.newlabel('aabs'), m.newlabel('ax')
    apg, asm_ = m.newlabel('apg'), m.newlabel('asm')
    isep(m, P_PGM, apg); isep(m, P_ASSIGN, asm_)
    isep(m, P_PITCH, apit); isep(m, P_PLAY, aply); isep(m, P_VOICE, aabs); isep(m, P_MUTE, aabs)
    isep(m, P_VRANGE, aabs)
    m.pool_jump()
    # 0..100 values: reference + (value - value of the pad shown), 0..100
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
    jmpl(m, ax); m.pool_jump()
    # PITCH: every layer with a sample = its cents at the reference + (value - value of the pad shown) * 100
    a.deflabel(apit)
    ld(m, LASTV, 12); ld(m, BASEV, 0); a.sub(0, 12); a.mov_imm(100, 0); a.mul_l(0, 12); a.sts_macl(12)
    prog(m, 11); m.imm32(REFT, 7); m.imm32(INCL, 6); a.mov_imm(NPADS, 9); m.imm32(TUNE_MAX, 5); a.neg(5, 4)
    tl, tk = m.newlabel('tl'), m.newlabel('tk'); a.deflabel(tl)
    a.movb_ld(6, 0); a.tst_rr(0, 0); m.bt_far(tk)
    for l in range(4):
        n = m.newlabel('tn'); a.mov_rm_rn(11, 1); a.add_imm(l * LAYER, 1); a.movb_ld(1, 0); a.tst_rr(0, 0); a.bt_lbl(n)
        a.mov_rm_rn(7, 1); a.add_imm(2 * l, 1); a.movw_ld(1, 0); a.add_rr(12, 0)
        q1 = m.newlabel('q1'); a.cmpgt(5, 0); a.bf_lbl(q1); a.mov_rm_rn(5, 0); a.deflabel(q1)
        q2 = m.newlabel('q2'); a.cmpgt(0, 4); a.bf_lbl(q2); a.mov_rm_rn(4, 0); a.deflabel(q2)
        a.mov_rm_rn(11, 1); a.add_imm(l * LAYER + TUNE_OFF, 1); a.movw_st(0, 1)
        a.deflabel(n)
    a.deflabel(tk)
    m.imm32(PAD_SIZE, 0); a.add_rr(0, 11); a.add_imm(8, 7); a.add_imm(1, 6); a.dt(9); m.bf_far(tl)
    jmpl(m, ax); m.pool_jump()
    # PLAY: every layer with a sample = the value
    a.deflabel(aply)
    ld(m, LASTV, 12); prog(m, 11); m.imm32(INCL, 6); a.mov_imm(NPADS, 9)
    yl, yk = m.newlabel('yl'), m.newlabel('yk'); a.deflabel(yl)
    a.movb_ld(6, 0); a.tst_rr(0, 0); a.bt_lbl(yk)
    for l in range(4):
        n = m.newlabel('yn'); a.mov_rm_rn(11, 1); a.add_imm(l * LAYER, 1); a.movb_ld(1, 0); a.tst_rr(0, 0); a.bt_lbl(n)
        a.mov_rm_rn(11, 1); a.add_imm(l * LAYER + PLAY_OFF, 1); a.mov_rm_rn(12, 0); a.movb_st(0, 1); a.deflabel(n)
    a.deflabel(yk)
    m.imm32(PAD_SIZE, 0); a.add_rr(0, 11); a.add_imm(1, 6); a.dt(9); a.bf_lbl(yl)
    jmpl(m, ax); m.pool_jump()
    # VOICE, MUTE: the byte = the value
    a.deflabel(aabs)
    ld(m, LASTV, 12); poff(m, 13); prog(m, 11); m.imm32(INCL, 6); a.mov_imm(NPADS, 9)
    bl, bk = m.newlabel('bl'), m.newlabel('bk'); a.deflabel(bl)
    a.movb_ld(6, 0); a.tst_rr(0, 0); a.bt_lbl(bk)
    a.mov_rm_rn(11, 1); a.add_rr(13, 1); a.mov_rm_rn(12, 0); a.movb_st(0, 1)
    a.deflabel(bk)
    m.imm32(PAD_SIZE, 0); a.add_rr(0, 11); a.add_imm(1, 6); a.dt(9); a.bf_lbl(bl)
    jmpl(m, ax)
    a.deflabel(apg); call(m, 'pg_apply'); jmpl(m, ax)
    a.deflabel(asm_); ld(m, LASTV, 4); call(m, 'sp_set')
    a.deflabel(ax); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- pr_aux: r9 = new filter type / decay mode, r10 = its pad offset, r11 = field, r12 = last-value slot,
    #      r13 = its page. On its page -> every pad that changes; elsewhere the field is put back ----
    a.deflabel('pr_aux'); a.stsl_pr()
    ap, axx = m.newlabel('ap'), m.newlabel('axx')
    rdb(m, PARAM, 0); a.cmpeq(13, 0); a.bt_lbl(ap)
    a.movl_ld(12, 0); a.movb_st(0, 11); jmpl(m, axx)
    m.pool_jump()
    a.deflabel(ap)
    a.movl_st(9, 12); prog(m, 5); m.imm32(INCL, 6); a.mov_imm(NPADS, 4)
    fl, fk = m.newlabel('fl'), m.newlabel('fk'); a.deflabel(fl)
    a.movb_ld(6, 0); a.tst_rr(0, 0); a.bt_lbl(fk)
    a.mov_rm_rn(5, 1); a.add_rr(10, 1); a.mov_rm_rn(9, 0); a.movb_st(0, 1)
    a.deflabel(fk)
    m.imm32(PAD_SIZE, 0); a.add_rr(0, 5); a.add_imm(1, 6); a.dt(4); a.bf_lbl(fl)
    a.deflabel(axx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- sm_walk: r4 = k (1-based, 0 = none), r5 = name to find (0 = none) -> r0 = k-th sample block (0 = none),
    #      r1 = number of samples, r2 = index of the sample named r5 (0 = none). Add-on WAVs are skipped.
    #      r0-r7; r8-r11 kept ----
    a.deflabel('sm_walk')
    for r in (8, 9, 10, 11): a.movl_st_dec(r, 15)
    wl, wn, we, wnad, whit = m.newlabel('wl'), m.newlabel('wn'), m.newlabel('we'), m.newlabel('wnad'), m.newlabel('wh')
    m.imm32(MGR, 3); a.movl_ld(3, 6); a.movl_ld_disp(3, 4, 7)                        # r6 = first block, r7 = limit
    a.mov_imm(0, 0); a.mov_imm(0, 1); a.mov_imm(0, 2)
    a.deflabel(wl)
    a.cmphs(7, 6); m.bt_far(we)                                                       # r6 >= limit: end
    a.movl_ld(6, 3); a.cmppl(3); m.bf_far(we)                                         # size <= 0: end
    m.imm32(0x100, 8); a.add_rr(6, 8)
    for ch in "MPC": a.movb_ldinc(8, 9); a.mov_imm(ord(ch), 10); a.cmpeq(10, 9); a.bf_lbl(wnad)
    a.bra_lbl(wn); a.nop()                                                            # an add-on: skip it (r2 is a result)
    a.deflabel(wnad)
    a.add_imm(1, 1)
    nk = m.newlabel('nk'); a.cmpeq(4, 1); a.bf_lbl(nk); a.mov_rm_rn(6, 0); a.deflabel(nk)
    a.tst_rr(5, 5); m.bt_far(wn); a.tst_rr(2, 2); m.bf_far(wn)
    a.mov_rm_rn(6, 8); a.add_imm(4, 8); a.mov_rm_rn(5, 9); a.mov_imm(16, 10)
    cl = m.newlabel('cl'); a.deflabel(cl)
    a.movb_ldinc(8, 11); a.movb_ldinc(9, 3); a.cmpeq(11, 3); m.bf_far(wn)
    a.tst_rr(11, 11); a.bt_lbl(whit); a.dt(10); a.bf_lbl(cl)
    a.deflabel(whit); a.mov_rm_rn(1, 2)
    a.deflabel(wn); a.movl_ld(6, 3); a.add_rr(3, 6); a.bra_lbl(wl); a.nop()
    m.pool_jump()
    a.deflabel(we)
    for r in (11, 10, 9, 8): a.movl_ld_inc(15, r)
    a.rts(); a.nop()
    m.pool_jump()

    def put_name(rs, n):
        """n characters of the name at rs -> @r12+ (after its end: spaces). r0, r5, r7"""
        nl, usp, put = m.newlabel('nl'), m.newlabel('usp'), m.newlabel('put')
        a.mov_imm(0, 5); a.mov_imm(n, 7)
        a.deflabel(nl)
        a.movb_ldinc(rs, 0); a.tst_rr(5, 5); a.bf_lbl(usp); a.tst_rr(0, 0); a.bf_lbl(put); a.mov_imm(1, 5)
        a.deflabel(usp); a.mov_imm(32, 0)
        a.deflabel(put); a.movb_st(0, 12); a.add_imm(1, 12); a.dt(7); a.bf_lbl(nl)
    def spin_out(slot, w):
        """the 3 option texts of the spinner field 'slot' = TXT[0..w); redraw. r0, r1, r3, r4, r7"""
        W_ = pg.addr['values'][fid(slot)]
        m.imm32(W_ + 0x24, 1); a.movl_ld(1, 1); a.movl_ld_disp(1, 0x20, 1); a.movl_ld_disp(1, 4, 3)   # r3 = texts
        m.imm32(TXT, 4); a.mov_imm(w, 7)
        c = m.newlabel('so'); a.deflabel(c)
        a.movb_ld(4, 0); a.movb_st(0, 3); a.mov_rm_rn(3, 1); a.add_imm(w + 1, 1); a.movb_st(0, 1)
        a.add_imm(w + 1, 1); a.movb_st(0, 1); a.add_imm(1, 3); a.add_imm(1, 4); a.dt(7); a.bf_lbl(c)
        redraw(m)
    def put3(rv):
        """the number in rv (0..999) -> 3 characters at @r12+, leading spaces. r0-r2, r4, r5, r9-r11"""
        a.mov_rm_rn(rv, 4); a.mov_imm(100, 5); pg._hcall(m, '__udiv')                 # r0 = hundreds
        a.mov_rm_rn(0, 9); a.mov_rm_rn(1, 4); a.mov_imm(10, 5); pg._hcall(m, '__udiv')
        a.mov_rm_rn(0, 10); a.mov_rm_rn(1, 11); a.add_imm(0x30, 11)
        z1, z2, d1 = m.newlabel('z1'), m.newlabel('z2'), m.newlabel('d1')
        a.tst_rr(9, 9); a.bt_lbl(z1); a.add_imm(0x30, 9); a.add_imm(0x30, 10); a.bra_lbl(d1); a.nop()
        a.deflabel(z1); a.mov_imm(32, 9); a.tst_rr(10, 10); a.bt_lbl(z2); a.add_imm(0x30, 10); a.bra_lbl(d1); a.nop()
        a.deflabel(z2); a.mov_imm(32, 10)
        a.deflabel(d1)
        for r in (9, 10, 11): a.mov_rm_rn(r, 0); a.movb_st(0, 12); a.add_imm(1, 12)

    # ---- sp_text: the sample field shows the layer's name (8 characters, "--" = none). r0-r7, r12 ----
    a.deflabel('sp_text'); a.stsl_pr()
    layblk(m, 6); m.imm32(TXT, 12)
    named, cp = m.newlabel('named'), m.newlabel('cp')
    a.movb_ld(6, 0); a.tst_rr(0, 0); m.bf_far(named)
    for ch in "--      ": a.mov_imm(ord(ch), 0); a.movb_st(0, 12); a.add_imm(1, 12)
    jmpl(m, cp)
    a.deflabel(named); put_name(6, 8)
    a.deflabel(cp); spin_out(SMPF, 8)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- sp_load: SIDX = position of the layer's sample in the list; the field shows its name ----
    a.deflabel('sp_load'); a.stsl_pr()
    layblk(m, 5); a.movb_ld(5, 0); a.tst_rr(0, 0); hn = m.newlabel('hn'); a.bf_lbl(hn); a.mov_imm(0, 5)
    a.deflabel(hn); a.mov_imm(0, 4); call(m, 'sm_walk'); st(m, 2, SIDX)
    a.mov_imm(1, 0); wrb(m, 0, SMPF); call(m, 'sp_text')
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- sp_set: r4 = k -> the layer gets the k-th sample (0 = emptied, past the last = unchanged) ----
    a.deflabel('sp_set'); a.stsl_pr()
    wdone, wclr, wtxt = m.newlabel('wd'), m.newlabel('wc'), m.newlabel('wt')
    a.mov_imm(0, 5); call(m, 'sm_walk')                                               # r0 = block of the k-th sample
    a.tst_rr(4, 4); m.bt_far(wclr)
    a.tst_rr(0, 0); m.bt_far(wdone)
    st(m, 4, SIDX); a.mov_rm_rn(0, 8); a.add_imm(4, 8); layblk(m, 6); a.mov_imm(16, 7)
    c16 = m.newlabel('c16'); a.deflabel(c16); a.movb_ldinc(8, 0); a.movb_st(0, 6); a.add_imm(1, 6); a.dt(7); a.bf_lbl(c16)
    jmpl(m, wtxt)
    a.deflabel(wclr)                                                                  # "--": the layer is emptied
    st(m, 4, SIDX); layblk(m, 6); a.mov_imm(16, 7); a.mov_imm(0, 2)
    z16 = m.newlabel('z16'); a.deflabel(z16); a.movb_st(2, 6); a.add_imm(1, 6); a.dt(7); a.bf_lbl(z16)
    a.deflabel(wtxt); call(m, 'sp_text')
    a.deflabel(wdone); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- pg_info (leaf): r2 = last rank (programs in use - 1, >= 0), r9 = rank of the current program. r0-r7 ----
    a.deflabel('pg_info')
    gl, gn, gc, gz = m.newlabel('gl'), m.newlabel('gn'), m.newlabel('gc'), m.newlabel('gz')
    m.imm32(PROG_IDX, 1); a.movw_ld(1, 6); a.extu_w(6, 6)
    m.imm32(PROG_BASE + PROG_STRIDE, 3); m.imm32(PROG_STRIDE, 5); m.imm32(NPROGS + 1, 7)
    a.mov_imm(1, 4); a.mov_imm(0, 2); a.mov_imm(0, 9)
    a.deflabel(gl)
    a.movl_ld(3, 0); a.tst_rr(0, 0); a.bt_lbl(gn)                                     # size word 0: free slot
    a.cmpeq(4, 6); a.bf_lbl(gc); a.mov_rm_rn(2, 9); a.deflabel(gc); a.add_imm(1, 2)
    a.deflabel(gn); a.add_rr(5, 3); a.add_imm(1, 4); a.cmpeq(7, 4); a.bf_lbl(gl)
    a.tst_rr(2, 2); a.bt_lbl(gz); a.add_imm(-1, 2); a.deflabel(gz)
    a.rts(); a.nop()
    m.pool_jump()

    # ---- pg_apply: the program of rank LASTV becomes current (OS select + refresh, as the MPC's own wheel) ----
    a.deflabel('pg_apply'); a.stsl_pr()
    al_, an_, ah_, at_, ad_ = (m.newlabel(s) for s in ('pal', 'pan', 'pah', 'pat', 'pad'))
    ld(m, LASTV, 9)
    m.imm32(PROG_BASE + PROG_STRIDE, 3); m.imm32(PROG_STRIDE, 5); m.imm32(NPROGS + 1, 7)
    a.mov_imm(1, 4); a.mov_imm(0, 2)
    a.deflabel(al_)
    a.movl_ld(3, 0); a.tst_rr(0, 0); a.bt_lbl(an_)
    a.cmpeq(2, 9); a.bt_lbl(ah_); a.add_imm(1, 2)
    a.deflabel(an_); a.add_rr(5, 3); a.add_imm(1, 4); a.cmpeq(7, 4); a.bf_lbl(al_)
    jmpl(m, ad_)
    a.deflabel(ah_)                                                                   # r4 = its slot
    m.imm32(PROG_IDX, 1); a.movw_ld(1, 0); a.extu_w(0, 0); a.cmpeq(4, 0); m.bt_far(at_)
    a.mov_rm_rn(4, 5); m.imm32(PROG_BASE, 4); m.imm32(PSELECT, 2); a.jsr(2); a.nop()
    m.imm32(PROG_BASE, 4); m.imm32(PREFRESH, 2); a.jsr(2); a.nop()
    a.deflabel(at_); call(m, 'pg_text')
    a.deflabel(ad_); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- pg_text: the program field shows "  3:BEATS" (number, ':', 11 characters of the name) ----
    a.deflabel('pg_text'); a.stsl_pr()
    m.imm32(TXT, 12); m.imm32(PROG_IDX, 1); a.movw_ld(1, 8); a.extu_w(8, 8)
    put3(8); a.mov_imm(ord(':'), 0); a.movb_st(0, 12); a.add_imm(1, 12)
    m.imm32(PROG_STRIDE, 1); a.mov_rm_rn(8, 6); a.mul_l(1, 6); a.sts_macl(6); m.imm32(PROG_BASE + 4, 1); a.add_rr(1, 6)
    put_name(6, 11)
    spin_out(PGMF, 15)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- vr_fire: VEL RANGE fields -> the layer (low never above high) ----
    a.deflabel('vr_fire'); a.stsl_pr()
    layblk(m, 6)
    for F, LS, off, O, OTHER_LOW in ((VLO, LVLO, VLO_OFF, VHI, False), (VHI, LVHI, VHI_OFF, VLO, True)):
        n1, ok = m.newlabel('vn'), m.newlabel('vk')
        rdb(m, F, 9); ld(m, LS, 0); a.cmpeq(9, 0); m.bt_far(n1)
        rdb(m, O, 2)
        if OTHER_LOW: a.cmphi(9, 2)                                                   # high below low: = low
        else: a.cmphi(2, 9)                                                           # low above high: = high
        a.bf_lbl(ok); a.mov_rm_rn(2, 9); wrb(m, 9, F); a.deflabel(ok)
        a.mov_rm_rn(6, 1); a.add_imm(off, 1); a.mov_rm_rn(9, 0); a.movb_st(0, 1); st(m, 9, LS); redraw(m)
        a.deflabel(n1)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- pu_fire (PURGE page): count the unused samples one block per fire; while deleting, one block per fire ----
    a.deflabel('pu_fire'); a.stsl_pr()
    pdx, run, scan, have, pend, snext = (m.newlabel(s) for s in ('pdx', 'run', 'scan', 'have', 'pend', 'snx'))
    rh, rend, rnext = m.newlabel('rh'), m.newlabel('rend'), m.newlabel('rnx')
    ld(m, PST, 0); a.mov_imm(2, 1); a.cmpeq(1, 0); m.bt_far(run)
    a.mov_imm(3, 1); a.cmpeq(1, 0); m.bf_far(scan)
    ld(m, PTIM, 0); a.add_imm(-1, 0); st(m, 0, PTIM); a.tst_rr(0, 0); m.bf_far(pdx)  # "DELETED" shown for a while
    sti(m, 0, PST); sti(m, 0, PCUR); sti(m, UNKNOWN, PUNUSED); jmpl(m, pdx)
    m.pool_jump()
    a.deflabel(scan)
    ld(m, PCUR, 6); a.tst_rr(6, 6); m.bf_far(have)
    m.imm32(MGR, 1); a.movl_ld(1, 6); st(m, 6, PCUR); sti(m, 0, PCNT)
    a.deflabel(have)
    m.imm32(MGR, 1); a.movl_ld_disp(1, 4, 7); a.cmphs(7, 6); m.bt_far(pend)
    a.movl_ld(6, 3); a.cmppl(3); m.bf_far(pend)
    isaddon(m, 6, snext)
    m.imm32(PROG_BASE, 4); a.mov_rm_rn(6, 5); m.imm32(ISUSED, 2); a.jsr(2); a.nop()
    a.tst_rr(0, 0); m.bf_far(snext)
    ld(m, PCNT, 0); a.add_imm(1, 0); st(m, 0, PCNT)
    a.deflabel(snext)
    ld(m, PCUR, 6); a.movl_ld(6, 3); a.add_rr(3, 6); st(m, 6, PCUR); jmpl(m, pdx)
    a.deflabel(pend)
    ld(m, PCNT, 0); st(m, 0, PUNUSED); sti(m, 0, PCUR); jmpl(m, pdx)
    m.pool_jump()
    a.deflabel(run)
    ld(m, PCUR, 6); a.tst_rr(6, 6); m.bf_far(rh)
    m.imm32(MGR, 1); a.movl_ld(1, 6); st(m, 6, PCUR)
    a.deflabel(rh)
    m.imm32(MGR, 1); a.movl_ld_disp(1, 4, 7); a.cmphs(7, 6); m.bt_far(rend)
    a.movl_ld(6, 3); a.cmppl(3); m.bf_far(rend)
    isaddon(m, 6, rnext)
    m.imm32(PROG_BASE, 4); a.mov_rm_rn(6, 5); m.imm32(ISUSED, 2); a.jsr(2); a.nop()
    a.tst_rr(0, 0); m.bf_far(rnext)
    ld(m, PCUR, 0); m.imm32(MGR, 1); a.movl_st_disp(0, 8, 1)                          # current sample = it
    m.imm32(MGR, 4); m.imm32(DELETE_CMD, 2); a.jsr(2); a.nop()                        # delete (the next one moves here)
    ld(m, PDEL, 0); a.add_imm(1, 0); st(m, 0, PDEL); redraw(m); jmpl(m, pdx)
    m.pool_jump()
    a.deflabel(rnext)
    ld(m, PCUR, 6); a.movl_ld(6, 3); a.add_rr(3, 6); st(m, 6, PCUR); jmpl(m, pdx)
    a.deflabel(rend)
    sti(m, 3, PST); sti(m, DONE_FIRES, PTIM); sti(m, 0, PCUR); redraw(m)
    a.deflabel(pdx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- pr_disp: the texts of the page; redraw only when a text changed ----
    a.deflabel('pr_disp'); a.stsl_pr()
    a.mov_imm(0, 14)                                                                  # r14 = changed
    def cput(s):
        for ch in s:
            n = m.newlabel('cp'); a.movb_ld(12, 1); a.mov_imm(ord(ch), 0); a.cmpeq(0, 1); a.bt_lbl(n)
            a.movb_st(0, 12); a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
    def cputr(r):
        n = m.newlabel('cr'); a.movb_ld(12, 1); a.cmpeq(r, 1); a.bt_lbl(n); a.mov_rm_rn(r, 0); a.movb_st(0, 12)
        a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
    def num3(slot):
        ld(m, slot, 4); a.mov_imm(100, 5); pg._hcall(m, '__udiv')                    # r0 = hundreds
        a.mov_rm_rn(0, 9); a.mov_rm_rn(1, 4); a.mov_imm(10, 5); pg._hcall(m, '__udiv')   # r0 = tens, r1 = units
        a.mov_rm_rn(0, 10); a.mov_rm_rn(1, 11); a.add_imm(0x30, 11)
        z1, z2, d1 = m.newlabel('z1'), m.newlabel('z2'), m.newlabel('d1')
        a.tst_rr(9, 9); a.bt_lbl(z1); a.add_imm(0x30, 9); a.add_imm(0x30, 10); a.bra_lbl(d1); a.nop()
        a.deflabel(z1); a.mov_imm(32, 9); a.tst_rr(10, 10); a.bt_lbl(z2); a.add_imm(0x30, 10); a.bra_lbl(d1); a.nop()
        a.deflabel(z2); a.mov_imm(32, 10)
        a.deflabel(d1); cputr(9); cputr(10); cputr(11)
        m.pool_jump()
    rdb(m, PARAM, 8)                                                                  # r8 = page
    # r10 = bank letter, r11 r13 = pad number (2 digits) of the pad shown
    nod = m.newlabel('nod')
    ld(m, SEL, 9); a.tst_rr(9, 9); m.bt_far(nod)
    a.add_imm(-1, 9); a.mov_rm_rn(9, 4); a.mov_imm(12, 5); pg._hcall(m, '__udiv')     # r0 = bank, r1 = pad
    a.add_imm(ord('A'), 0); a.mov_rm_rn(0, 10); a.add_imm(1, 1); a.mov_rm_rn(1, 4); a.mov_imm(10, 5)
    pg._hcall(m, '__udiv'); a.add_imm(0x30, 0); a.mov_rm_rn(0, 11); a.add_imm(0x30, 1); a.mov_rm_rn(1, 13)
    a.deflabel(nod)
    m.pool_jump()
    # row 0: rest of the title
    m.imm32(pg.addr['dlabels'][T2], 12); t2d = m.newlabel('t2d')
    for p, txt in TITLE2.items():
        n = m.newlabel('t2'); a.mov_imm(p, 0); a.cmpeq(0, 8); m.bf_far(n); cput(txt.ljust(9)); jmpl(m, t2d)
        a.deflabel(n); m.pool_jump()
    cput(" " * 9)
    a.deflabel(t2d)
    # row 0: the pad ("A05", "  A" on the layer pages)
    m.imm32(pg.addr['dlabels'][PADL], 12); pb, pd, pl = m.newlabel('pb'), m.newlabel('pd'), m.newlabel('pll')
    ld(m, SEL, 0); a.tst_rr(0, 0); m.bt_far(pb)
    in_set(m, 8, NOPAD, pb); in_set(m, 8, LAYERED, pl)
    cputr(10); cputr(11); cputr(13); jmpl(m, pd)
    a.deflabel(pl); cput("  "); cputr(10); jmpl(m, pd)
    a.deflabel(pb); cput("   ")
    a.deflabel(pd)
    m.pool_jump()
    # row 1: "HIT A PAD" / "05:" (layer pages) / "05:      -" (VEL RANGE)
    m.imm32(pg.addr['dlabels'][HINT], 12); hb, hd, hs, hl, hl2 = (m.newlabel(s) for s in ('hb', 'hd', 'hs', 'hl', 'hl2'))
    in_set(m, 8, NOPAD + [P_PGM], hb)
    ld(m, SEL, 0); a.tst_rr(0, 0); m.bf_far(hs)
    cput("HIT A PAD "); jmpl(m, hd)
    a.deflabel(hs); in_set(m, 8, LAYERED, hl)
    hf = m.newlabel('hf'); a.mov_imm(P_FILTER, 0); a.cmpeq(0, 8); m.bf_far(hf); cput("      R:  "); jmpl(m, hd)
    a.deflabel(hf); jmpl(m, hb)
    a.deflabel(hl)                                                                    # " A1:" / "A12:" as the MPC
    a.mov_imm(ord('0'), 0); a.cmpeq(0, 11); m.bf_far(hl2)
    cput(" "); cputr(10); cputr(13); cput(":      "); jmpl(m, hd)
    a.deflabel(hl2); cputr(10); cputr(11); cputr(13); cput(":      "); jmpl(m, hd)
    m.pool_jump()
    a.deflabel(hb); cput(" " * 10)
    a.deflabel(hd)
    m.pool_jump()
    # row 1, x10..15: blank (no catch hint, user's choice); VEL RANGE: its "-" between low and high
    m.imm32(pg.addr['dlabels'][MOVEL], 12)
    dn, bv = m.newlabel('dn'), m.newlabel('bv')
    a.mov_imm(P_VRANGE, 0); a.cmpeq(0, 8); m.bf_far(bv); cput("-     "); jmpl(m, dn)
    a.deflabel(bv); cput("      ")
    a.deflabel(dn)
    m.pool_jump()
    # row 1 of PURGE / ENTER DEFAULT
    m.imm32(pg.addr['dlabels'][R1], 12)
    r1p, r1b, r1d = m.newlabel('r1p'), m.newlabel('r1b'), m.newlabel('r1d')
    a.mov_imm(P_ENTER, 0); a.cmpeq(0, 8); m.bf_far(r1p); cput("MPC PROGRAM MODE"); jmpl(m, r1d)
    a.deflabel(r1p); a.mov_imm(P_PURGE, 0); a.cmpeq(0, 8); m.bf_far(r1b)
    s1, s2, s3 = m.newlabel('s1'), m.newlabel('s2'), m.newlabel('s3')
    ld(m, PST, 0); a.mov_imm(1, 1); a.cmpeq(1, 0); m.bt_far(s1)
    a.mov_imm(2, 1); a.cmpeq(1, 0); m.bt_far(s2)
    a.mov_imm(3, 1); a.cmpeq(1, 0); m.bt_far(s3)
    cnt, zero = m.newlabel('cnt'), m.newlabel('zero')
    ld(m, PUNUSED, 0); m.imm32(UNKNOWN, 1); a.cmpeq(1, 0); m.bt_far(cnt)
    a.tst_rr(0, 0); m.bt_far(zero)
    cput("DO IT:"); num3(PUNUSED); cput(" UNUSED"); jmpl(m, r1d)
    a.deflabel(cnt); cput("COUNTING...     "); jmpl(m, r1d)
    a.deflabel(zero); cput("NOTHING UNUSED  "); jmpl(m, r1d)
    m.pool_jump()
    a.deflabel(s1); cput("DEL "); num3(PUNUSED); cput("? DO IT  "); jmpl(m, r1d)
    a.deflabel(s2); cput("DELETING "); num3(PDEL); cput("/"); num3(PUNUSED); jmpl(m, r1d)
    a.deflabel(s3); cput("DELETED "); num3(PDEL); cput("     "); jmpl(m, r1d)
    m.pool_jump()
    a.deflabel(r1b); cput(" " * 16)
    a.deflabel(r1d)
    a.tst_rr(14, 14); px = m.newlabel('px'); m.bt_far(px); redraw(m)
    a.deflabel(px); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

def doit(m):
    """ENTER DEFAULT: the MPC's own Program page. PURGE: count -> confirm -> delete"""
    a = m.a
    ex, ne, bad, p1, go1 = (m.newlabel(s) for s in ('ex', 'ne', 'bad', 'p1', 'go1'))
    rdb(m, PARAM, 9)
    a.mov_imm(P_ENTER, 0); a.cmpeq(0, 9); m.bf_far(ne)
    m.imm32(OSPROG, 8)
    a.movl_ld_disp(8, 0x1C, 0); m.imm32(VT_PAGE, 1); a.cmpeq(1, 0); m.bf_far(bad)
    a.movl_ld_disp(8, 0x30, 0); m.imm32(OSPROG_H, 1); a.cmpeq(1, 0); m.bf_far(bad)
    m.imm32(EVQ, 4); a.mov_rm_rn(8, 5); a.mov_imm(0, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop(); jmpl(m, ex)
    a.deflabel(bad); pg.emit_message(m, "NOT AVAILABLE"); jmpl(m, ex)
    m.pool_jump()
    a.deflabel(ne)
    a.mov_imm(P_PURGE, 0); a.cmpeq(0, 9); m.bf_far(ex)
    ld(m, PST, 0); a.tst_rr(0, 0); m.bf_far(p1)
    ld(m, PUNUSED, 0); m.imm32(UNKNOWN, 1); a.cmpeq(1, 0); m.bt_far(ex)              # still counting
    a.tst_rr(0, 0); m.bf_far(go1); pg.emit_message(m, "NOTHING TO PURGE"); jmpl(m, ex)
    a.deflabel(go1); sti(m, 1, PST); redraw(m); jmpl(m, ex)
    m.pool_jump()
    a.deflabel(p1)
    a.mov_imm(1, 1); a.cmpeq(1, 0); m.bf_far(ex)
    sti(m, 2, PST); sti(m, 0, PDEL); sti(m, 0, PCUR); redraw(m)
    a.deflabel(ex)
pg.on_doit(doit)

def on_cancel(m, hret1):
    """PURGE: CANCEL on the question or while deleting = back to the count (consumed); otherwise leave as usual"""
    a = m.a
    cx = m.newlabel('cx')
    rdb(m, PARAM, 0); a.mov_imm(P_PURGE, 1); a.cmpeq(1, 0); m.bf_far(cx)
    ld(m, PST, 0); a.tst_rr(0, 0); m.bt_far(cx); a.mov_imm(3, 1); a.cmpeq(1, 0); m.bt_far(cx)
    sti(m, 0, PST); sti(m, 0, PCUR); sti(m, UNKNOWN, PUNUSED); redraw(m); jmpl(m, hret1)
    a.deflabel(cx)
pg.cancel_fn = on_cancel

def ev_pad(m, hret0, hret1):
    """a pad hit chooses the pad shown (fire does the work); the OS plays it as usual"""
    a = m.a
    ret = m.newlabel('ret')
    a.movl_ld_disp(8, 4, 0); a.tst_rr(0, 0); a.bt_lbl(ret)                            # release
    a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 9); a.extu_w(9, 9); a.mov_imm(12, 0); a.cmphs(0, 9); a.bt_lbl(ret)
    m.imm32(BANKOBJ, 1); a.movb_ld(1, 0); a.and_imm(3); a.mov_imm(12, 2); a.mul_l(2, 0); a.sts_macl(0); a.add_rr(0, 9)
    a.add_imm(1, 9); st(m, 9, PEND)
    a.deflabel(ret); jmpl(m, hret0)
    m.pool_jump()
pg.on_event(0x38, ev_pad)


# =====================================================================================================================
def selftest(out):
    from validate import Harness
    from shasm import SHAsm
    from mpcmod import HOOKS
    from pages import ENABLE_DOIT, ENABLE_CANCEL
    from testkit import PageTest, FIRMWARE
    import random
    VT_LABEL, VT_COMPOSITE = 0x88507C44, 0x88507D1C
    LOG = 0x88E00000

    class OSTest(PageTest):
        """PageTest with the OS code image in SDRAM, so the REAL "is it used" test and Delete run (as S500's test)."""
        def __init__(self, pg, wav):
            self.pg = pg
            self.h = h = Harness(FIRMWARE); h.fill_scratch(); self.e = e = h.emu
            fw = open(FIRMWARE, 'rb').read(); OSB = 0x8849495C
            keep = [(HOOKS[h.hook][1], 4), (ENABLE_DOIT, 10), (ENABLE_CANCEL, 10)]
            saved = [(ad, [e.rd(ad + i, 2) for i in range(0, n, 2)]) for ad, n in keep]
            buf, off = e._region(OSB); buf[off:off + len(fw)] = fw
            for ad, ws in saved:
                for i, w in enumerate(ws): e.wr(ad + 2 * i, w, 2)
            s = SHAsm(0x884C4FE0)
            s.movl_ld(4, 1); s.movl_ld_disp(4, 4, 3); s.deflabel('l'); s.cmphs(3, 1); s.bt_lbl('z')
            s.movl_ld(1, 0); s.tst_rr(0, 0); s.bt_lbl('f'); s.add_rr(0, 1); s.bra_lbl('l'); s.nop()
            s.deflabel('z'); s.mov_imm(0, 1); s.deflabel('f'); s.rts(); s.mov_rm_rn(1, 0)
            blob = s.assemble()[:26]
            for i in range(0, 26, 2): e.wr(0x884C4FE0 + i, struct.unpack('<H', blob[i:i + 2])[0], 2)
            for ad in (0x884C0076, 0x884C67BA): self.stub(ad)
            self.stub(ENABLE_DOIT)
            self.stub(SET_FOCUS, lambda s: (s.movl_st_disp(5, 0x24, 4), s.rts(), s.nop()))     # focus = page +0x24
            self.stub(OS_PUSH_PAGE, lambda s: (s.movl_pool(LOG, 1), s.movl_ld(1, 0), s.add_imm(1, 0), s.movl_st(0, 1),
                                               s.shll2(0), s.shll2(0), s.add_rr(0, 1), s.movl_st(5, 1),
                                               s.movl_st_disp(6, 4, 1), s.rts(), s.nop()))
            self.stub(PREFRESH, lambda s: (s.movl_pool(LOG + 0x800, 1), s.movl_ld(1, 0), s.add_imm(1, 0), s.movl_st(0, 1),
                                           s.rts(), s.nop()))     # OS refresh (voices, display): counted
            h.load_hub(); h.load_wav(wav); h.fire(10)

    t = OSTest(pg, out); h, e = t.h, t.e
    R32, W32 = h.ram_r32, h.ram_w32
    assert R32(0x88F00144) == 1, "add-on not installed"
    def pushes(): return [(R32(LOG + 16 * (k + 1)), R32(LOG + 16 * (k + 1) + 4)) for k in range(R32(LOG))]
    for i in (HINT, MOVEL, R1, T2, PADL):
        assert R32(pg.dlabels[i] - 0x28 + 0x24) == pg.dlabels[i], "dlabel widget is right before its text"
    # --- samples and the program ---
    def mkblock(name, n=600):
        blk = h.add_sample(b''.join(struct.pack('<h', (i * 37) % 2000 - 1000) for i in range(n)), name=name)
        W32(blk + 0x18, n); W32(blk + 0x1C, 0); W32(blk + 0x20, 0); W32(blk + 0x24, n - 1); h.ram_w16(blk + 0x92, 1)
        return blk
    for nm in (b"KICK", b"SNARE", b"HAT", b"CLAP", b"RIDE"): mkblock(nm)
    def blocks():
        p, out_ = R32(MGR), []
        while p < R32(MGR + 4) and R32(p) and R32(p) < 0x10000000:
            out_.append((p, bytes(e.rd(p + 4 + i, 1) for i in range(16)).split(b'\0')[0], bytes(e.rd(p + 0x100 + i, 1) for i in range(3))))
            p += R32(p)
        return out_
    names = lambda: [n for p, n, mg in blocks() if mg != b"MPC"]
    addons = lambda: [n for p, n, mg in blocks() if mg == b"MPC"]
    assert names() == [b"KICK", b"SNARE", b"HAT", b"CLAP", b"RIDE"] and len(addons()) >= 1, (names(), addons())
    h.ram_w16(PROG_IDX, 1); PROG = PROG_BASE + PROG_STRIDE
    W32(PROG, PROG_STRIDE)                                                 # program 1 is in use (size word) ...
    for i, ch in enumerate(b"TEST"): e.wr(PROG + 4 + i, ch, 1)            # ... and has a name (the OS test skips unnamed)
    for k, nm in ((3, b"BEATS"), (5, b"KEYS")):                           # two more programs in memory
        W32(PROG_BASE + k * PROG_STRIDE, PROG_STRIDE)
        for i, ch in enumerate(nm): e.wr(PROG_BASE + k * PROG_STRIDE + 4 + i, ch, 1)
    def pad(p, base=None): return (base or PROG) + PAD_OFF + p * PAD_SIZE
    def lay(p, l, base=None): return pad(p, base) + l * LAYER
    def tune(p, l): v = e.rd(lay(p, l) + TUNE_OFF, 2); return v - 0x10000 if v & 0x8000 else v
    def lname(p, l): return bytes(e.rd(lay(p, l) + i, 1) for i in range(16)).split(b'\0')[0]
    def setpad(p, layers, vol, lpf, pan, base=None, ftype=1, voice=0, mg=0):
        b = pad(p, base)
        for i in range(PAD_SIZE): e.wr(b + i, 0, 1)
        for l in range(4): e.wr(b + l * LAYER + SLVL_OFF, 100, 1); e.wr(b + l * LAYER + VHI_OFF, 127, 1)
        for l, nm in layers.items():
            nm_, tn, pm = (nm + (0, 0))[:3] if isinstance(nm, tuple) else (nm, 0, 0)
            for i, ch in enumerate(nm_.encode()): e.wr(b + l * LAYER + i, ch, 1)
            e.wr(b + l * LAYER + TUNE_OFF, tn & 0xFFFF, 2); e.wr(b + l * LAYER + PLAY_OFF, pm, 1)
        e.wr(b + 0x8F, vol, 1); e.wr(b + 0x72, lpf, 1); e.wr(b + 0x90, pan, 1); e.wr(b + FTYPE_OFF, ftype, 1)
        e.wr(b + 0x62, voice, 1); e.wr(b + 0x63, mg, 1); e.wr(b + 0x67, 5, 1); e.wr(b + 0x6B, 100, 1)
    for p in range(NPADS): setpad(p, {}, 100, 100, 50)
    setpad(4, {1: ("SNARE", 650, 0), 2: ("HAT", 100, 0)}, 80, 60, 30, mg=2)        # A05: layers 2 and 3
    setpad(5, {0: ("HAT", -250, 1)}, 100, 100, 70, ftype=0, voice=1, mg=1)
    setpad(6, {0: ("KICK", 3500, 0)}, 50, 40, 50, ftype=3)
    setpad(20, {0: ("GONE", 0, 0)}, 20, 10, 95, mg=7)                              # B09: sample not in memory
    e.wr(pad(9) + 0x8F, 77, 1)                                                      # A10: no sample, level 77
    PS = (4, 5, 6, 20, 9)
    vals = lambda off, ps=PS: tuple(e.rd(pad(p) + off, 1) for p in ps)
    snap = lambda base=PROG: bytes(e.rd(base + i, 1) for i in range(PROG_STRIDE))
    def slider(k, n_=100, fires=60):     # put the slider on step k (0..n_)
        W32(SLIDER, SL_LO + round(k * (SL_HI - SL_LO) / n_)); t.fire(fires)
    WID = {s: pg.values[fid(s)] for s in CURSOR}
    vis = lambda w: e.rd(w + 0x17, 1) >> 7
    shown = lambda: [s for s in CURSOR if vis(WID[s])]
    focus = lambda: R32(pg.page + 0x24)
    def cstr(p, n): return bytes(e.rd(p + i, 1) for i in range(n)).split(b'\0')[0].decode('latin-1')
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
                else: txt = ''
                for i, ch in enumerate(txt[:wd]): rows[y][x + i] = ch
            w = R32(w + 8)
        return [''.join(r) for r in rows]
    def setp(p): e.wr(PARAM, p, 1); t.fire(3)
    def wheel(v, f=None):
        f = f or VF[e.rd(PARAM, 1)]; e.wr(f, v, 1); t.fire(3)
    def field(p=None):
        p = e.rd(PARAM, 1) if p is None else p
        return VOPTS[VF[p]][e.rd(VF[p], 1)].strip()
    shots = {}
    def shot(tag): shots[tag] = screen()

    cur = lambda: e.rd(PROG_IDX, 2)
    refreshes = lambda: R32(LOG + 0x800)
    # ===== Program Select: the first page; programs 1 TEST, 3 BEATS, 5 KEYS =====
    W32(SLIDER, (SL_LO + SL_HI) // 2); t.open(); slider(50)
    assert e.rd(PARAM, 1) == P_LEVEL, ("first opened on LEVEL", e.rd(PARAM, 1))
    print("  ok first opened: LEVEL page (page order unchanged)")
    setp(P_PGM); slider(50)
    assert e.rd(PARAM, 1) == P_PGM and R32(NSTEP) == 2
    assert screen() == ["Program Select  ", "  1:TEST        "], screen(); shot("Program Select")
    assert shown() == [PARAM, PGMF], shown()
    wheel(2, PGMF); assert cur() == 3 and refreshes() == 1 and screen()[1][:15] == "  3:BEATS      ", (cur(), screen())
    wheel(2, PGMF); wheel(2, PGMF); assert cur() == 5 and e.rd(PGMF, 1) == 1, "past the last program: stays"
    for _ in range(3): wheel(0, PGMF)
    assert cur() == 1 and screen()[1][:15] == "  1:TEST       ", screen()
    slider(0, 2); assert cur() == 1
    slider(2, 2); assert cur() == 5 and screen()[1][:15] == "  5:KEYS       ", ("slider follows after catching", cur(), screen())
    slider(1, 2); assert cur() == 3
    slider(0, 2); assert cur() == 1 and refreshes() >= 5, refreshes()
    print("  ok Program Select: '  1:TEST'; wheel and slider step through programs 1, 3, 5 (OS select + refresh), stop at the ends")

    # ===== Sample assign, as the MPC =====
    setp(P_ASSIGN)
    assert screen() == ["Sample assign   ", "HIT A PAD       "], screen()
    assert shown() == [PARAM], shown()
    before = snap(); slider(2, 2); slider(0, 2); assert snap() == before, "no pad: nothing changes"
    shot("Sample assign (before a pad)")
    t.pad(0, 4); t.fire(3)
    assert screen()[0] == "Sample assign  A" and screen()[1][:15] == " A5:L1:--      ", screen()   # A05 layer 1 is empty
    e.wr(LAYF, 1, 1); t.fire(3)
    assert screen()[0] == "Sample assign  A" and screen()[1][:15] == " A5:L2:SNARE   ", screen(); shot("Sample assign")
    assert shown() == [PARAM, LAYF, SMPF] and R32(NSTEP) == 5, (shown(), R32(NSTEP))
    lvl = vals(0x8F)
    wheel(2, SMPF); assert lname(4, 1) == b"HAT" and screen()[1][:15] == " A5:L2:HAT     ", (lname(4, 1), screen())
    wheel(2, SMPF); wheel(2, SMPF); assert lname(4, 1) == b"RIDE", lname(4, 1)
    wheel(2, SMPF); assert lname(4, 1) == b"RIDE" and e.rd(SMPF, 1) == 1, "past the last sample: stays (add-ons never offered)"
    for _ in range(4): wheel(0, SMPF)
    assert lname(4, 1) == b"KICK" and screen()[1][:15] == " A5:L2:KICK    ", screen()
    wheel(0, SMPF); assert lname(4, 1) == b"" and screen()[1][:15] == " A5:L2:--      " and bytes(e.rd(lay(4, 1) + i, 1) for i in range(16)) == bytes(16)
    wheel(0, SMPF); assert lname(4, 1) == b"", "below none: stays"
    wheel(2, SMPF); wheel(2, SMPF); assert lname(4, 1) == b"SNARE"
    assert [lname(4, l) for l in range(4)] == [b"", b"SNARE", b"HAT", b""], "other layers untouched"
    e.wr(LAYF, 3, 1); t.fire(3); assert screen()[1][:15] == " A5:L4:--      "; e.wr(LAYF, 1, 1); t.fire(3)
    t.pad(0, 11); t.fire(3); assert screen()[1][:15] == "A12:L2:--      ", ("pad A12", screen())
    shot("Sample assign (pad A12)")
    t.pad(0, 4); t.fire(3); assert screen()[1][:15] == " A5:L2:SNARE   ", screen()
    # the slider scrolls the samples too (0 = "--", 1..5), after catching the current one (SNARE = 2)
    slider(0, 5); assert lname(4, 1) == b"SNARE", "catch-up: not moved yet"
    slider(4, 5); assert lname(4, 1) == b"CLAP" and screen()[1][:15] == " A5:L2:CLAP    ", (lname(4, 1), screen())
    slider(5, 5); assert lname(4, 1) == b"RIDE"
    slider(0, 5); assert lname(4, 1) == b""
    slider(2, 5); assert lname(4, 1) == b"SNARE"
    assert vals(0x8F) == lvl, "the slider no longer touches the level"
    print("  ok Sample assign: 'Sample assign  A' / ' A5:L2:SNARE' (A12: 'A12:L2:'); wheel AND slider scroll the samples in memory "
          "(add-ons skipped), '--' empties the layer; other layers and the level untouched")

    # ===== LEVEL (as Mixer) =====
    setp(P_LEVEL); slider(50); e.wr(pad(4) + 0x8F, 80, 1); t.pad(0, 4); t.fire(2)
    assert field() == "80" and screen() == ["LEVEL   PAD  A05", "   80           "], screen()
    shot("LEVEL")
    before = snap()
    for k in (60, 75): slider(k); assert snap() == before and field() == "80", k
    slider(80); assert snap() == before and screen()[1].endswith("      "), "caught at the pad's value"
    W32(EVQ + 0x70, 0); slider(85, fires=40); assert field() == "85" and R32(EVQ + 0x70) == 1
    W32(EVQ + 0x70, 0); t.fire(20); assert R32(EVQ + 0x70) == 0, "no redraw while nothing changes"
    slider(95); assert vals(0x8F) == (95, 100, 50, 20, 77), vals(0x8F)
    rnd = random.Random(7)
    for k in (40, 41):
        slider(k); vk = SL_LO + round(k * (SL_HI - SL_LO) / 100)
        for _ in range(200): W32(SLIDER, vk + rnd.randint(-4, 4)); t.fire(1)
        assert field() == str(k), (k, field())
    wheel(30); assert vals(0x8F)[0] == 30 and screen()[1] == "   30           ", screen()
    slider(35); assert vals(0x8F)[0] == 30, "wheel: must catch again"
    print("  ok LEVEL PAD: A05 80, catch-up, follows the slider, noise ignored, wheel 30")

    # --- cursor skips hidden fields ---
    W32(pg.page + 0x24, WID[SCOPE]); t.fire(1)
    W32(pg.page + 0x24, WID[TYPE]); t.fire(1); assert focus() == WID[V100], "RIGHT from scope over hidden fields -> value"
    W32(pg.page + 0x24, WID[VPIT]); t.fire(1); assert focus() == WID[V100], "RIGHT from the value: stays"
    W32(pg.page + 0x24, WID[LAYF]); t.fire(1); assert focus() == WID[SCOPE], "LEFT from the value -> scope"
    W32(pg.page + 0x24, WID[PARAM]); t.fire(1)
    print("  ok cursor: hidden fields skipped both ways")

    # ===== PITCH (as Pitch): A05 layers 2, 3 = 650, 100 cents -> +7 =====
    setp(P_PITCH); assert field() == "+7" and shown() == [PARAM, SCOPE, VPIT] and R32(NSTEP) == 24, (field(), shown())
    shot("PITCH")
    wheel(12 + 3); assert (tune(4, 0), tune(4, 1), tune(4, 2), tune(4, 3)) == (0, 250, -300, 0), [tune(4, l) for l in range(4)]
    assert tune(5, 0) == -250 and tune(6, 0) == 3500
    wheel(12 - 12); assert (tune(4, 1), tune(4, 2)) == (650 - 1900, 100 - 1900)
    wheel(12 + 7); assert (tune(4, 1), tune(4, 2)) == (650, 100), "back to +7: exact"
    slider(19, 24); slider(15, 24); assert field() == "+3" and tune(4, 1) == 250, (field(), tune(4, 1))
    wheel(12 + 7); e.wr(SCOPE, 1, 1); t.fire(2); assert field() == "+7"
    wheel(12 + 10)
    assert (tune(4, 1), tune(4, 2), tune(5, 0), tune(6, 0), tune(20, 0)) == (950, 400, 50, 3600, 300), \
        (tune(4, 1), tune(4, 2), tune(5, 0), tune(6, 0), tune(20, 0))
    wheel(12 + 7); assert (tune(4, 1), tune(5, 0), tune(6, 0), tune(20, 0)) == (650, -250, 3500, 0), "back: exact"
    e.wr(SCOPE, 0, 1); t.fire(2)
    # page left, slider moved on another page, page entered again: the value stays until the slider catches it (user 2026-11-08)
    e.wr(lay(4, 1) + TUNE_OFF, 700, 2); t.pad(0, 4); t.fire(3)                     # whole semitones (650 cents reads back rounded)
    slider(24, 24); slider(10, 24); assert field() == "-2", field()
    before = snap(); t.close(); W32(SLIDER, SL_HI); t.fire(20); t.open(); t.fire(60)
    assert field() == "-2" and snap() == before, ("re-entry keeps the pitch", field())
    slider(20, 24); assert field() == "-2", ("above the value: not caught yet", field())
    slider(9, 24); assert field() == "-3", ("passed it: caught", field())
    wheel(12 + 7); assert tune(4, 1) == 700, tune(4, 1)
    e.wr(lay(4, 1) + TUNE_OFF, 650, 2); e.wr(lay(4, 2) + TUNE_OFF, 100, 2); t.pad(0, 4); t.fire(3)
    print("  ok re-entering with the slider elsewhere keeps the pitch (catch-up again)")
    print("  ok PITCH: A05 +7, wheel +3 -> 250/-300 cents, slider (24 steps); PROG +3 on all (A07 stops at +36), back exact")

    # ===== FILTER: cutoff + type =====
    setp(P_FILTER); assert field() == "60" and shown() == [PARAM, SCOPE, TYPE, V100, VRES] and e.rd(TYPE, 1) == 1, (field(), shown())
    assert screen()[1][:11] == "LP 60 R:  0", screen(); shot("FILTER")
    wheel(25); assert vals(0x72) == (25, 100, 40, 10, 100), vals(0x72)
    e.wr(TYPE, 3, 1); t.fire(2); assert vals(FTYPE_OFF) == (3, 0, 3, 1, 1), vals(FTYPE_OFF)
    e.wr(SCOPE, 1, 1); t.fire(2); e.wr(TYPE, 2, 1); t.fire(2)
    assert vals(FTYPE_OFF) == (2, 2, 2, 2, 1), ("PROG: type set on every pad with a sample", vals(FTYPE_OFF))
    wheel(35); assert vals(0x72) == (35, 100, 50, 20, 100), vals(0x72)
    e.wr(SCOPE, 0, 1); t.fire(2)
    # resonance: only with the cursor on "R:" (wheel AND slider); elsewhere the slider is the cutoff
    W32(pg.page + 0x24, WID[VRES]); t.fire(3); assert R32(EP) == P_RESO
    wheel(40, VRES); assert vals(0x73) == (40, 0, 0, 0, 0) and screen()[1][:11] == "BP 35 R: 40", (vals(0x73), screen())
    shot("FILTER (cursor on R:)")
    cut = vals(0x72)
    slider(40); slider(60, fires=40); assert vals(0x73)[0] == 60 and vals(0x72) == cut, (vals(0x73), vals(0x72))
    W32(pg.page + 0x24, WID[TYPE]); t.fire(3); assert R32(EP) == P_FILTER
    slider(35); slider(50, fires=40); assert vals(0x72)[0] == 50 and vals(0x73)[0] == 60, (vals(0x72), vals(0x73))
    W32(pg.page + 0x24, WID[PARAM]); t.fire(3)
    slider(55, fires=40); assert vals(0x72)[0] == 55 and vals(0x73)[0] == 60, "cursor on the page name: cutoff"
    W32(pg.page + 0x24, WID[VRES]); t.fire(3); e.wr(SCOPE, 1, 1); t.fire(2); wheel(50, VRES)
    assert vals(0x73) == (50, 0, 0, 0, 0), ("PROG: -10 together, each pad stops at 0", vals(0x73))
    wheel(60, VRES); assert vals(0x73) == (60, 0, 0, 0, 0)
    e.wr(SCOPE, 0, 1); W32(pg.page + 0x24, WID[PARAM]); t.fire(3)
    print("  ok FILTER: cutoff 60 -> 25, type LP -> HP on A05; PROG: type BP everywhere, cutoff +10 together; "
          "'R:' resonance (+0x73) by wheel and slider only with the cursor on it, slider = cutoff elsewhere")

    # ===== PAN, PLAY, VOICE, MUTE =====
    setp(P_PAN); assert field() == "L20", field(); shot("PAN")
    wheel(50); assert field() == "C" and vals(0x90)[0] == 50
    wheel(100); assert field() == "R50" and vals(0x90)[0] == 100
    setp(P_PLAY); assert field() == "ONE SHOT" and screen()[1] == "  ONE SHOT      ", screen(); shot("PLAY")
    wheel(1); assert [e.rd(lay(4, l) + PLAY_OFF, 1) for l in range(4)] == [0, 1, 1, 0], "Note on on layers 2, 3"
    slider(0, 1); slider(1, 1); slider(0, 1); assert field() == "ONE SHOT" and e.rd(lay(4, 1) + PLAY_OFF, 1) == 0, field()
    setp(P_VOICE); assert field() == "POLY"; wheel(1); assert vals(0x62) == (1, 1, 0, 0, 0), vals(0x62); shot("VOICE")
    setp(P_MUTE); assert field() == "2" and R32(NSTEP) == 32; shot("MUTE")
    wheel(0); assert field() == "OFF" and vals(0x63)[0] == 0
    slider(0, 32); slider(32, 32); assert field() == "32" and vals(0x63)[0] == 32, field()
    e.wr(SCOPE, 1, 1); t.fire(2); wheel(9); assert vals(0x63) == (9, 9, 9, 9, 0), vals(0x63)
    e.wr(SCOPE, 0, 1); t.fire(2)
    print("  ok PAN L20/C/R50; PLAY NOTE ON on A05's sample layers; VOICE MONO; MUTE OFF..32, PROG group 9")

    # ===== ATTACK, DECAY (+ mode), RESO, velocity: 0..100 bytes, PAD and PROG =====
    for p, off, start in ((P_ATTACK, 0x66, 0), (P_DECAY, 0x67, 5), (P_VLEV, 0x6B, 100),
                          (P_VFILT, 0x78, 0), (P_VTUNE, 0x70, 0), (P_VATK, 0x69, 0), (P_VSTART, 0x6A, 0)):
        setp(p); assert field() == str(start) and shown()[:3] == [PARAM, SCOPE, V100], (NAMES[p], field(), shown())
        assert screen()[0] == TITLES[p].ljust(8)[:8] + "PAD  A05", screen(); shot(TITLES[p].strip())
        wheel(start + 20 if start < 80 else start - 20)
        v = start + 20 if start < 80 else start - 20
        assert vals(off) == (v, start, start, start, start), (NAMES[p], vals(off))
        slider(v); slider(v + 5 if v < 95 else v - 5, fires=40)
        assert vals(off)[0] == (v + 5 if v < 95 else v - 5), (NAMES[p], vals(off))
        e.wr(SCOPE, 1, 1); t.fire(2); w0 = e.rd(pad(4) + off, 1); wheel(w0 - 3)
        assert vals(off) == (w0 - 3, max(start - 3, 0), max(start - 3, 0), max(start - 3, 0), start), (NAMES[p], vals(off))
        wheel(w0); assert vals(off) == (w0, start, start, start, start), (NAMES[p], "back exact", vals(off))
        e.wr(SCOPE, 0, 1); t.fire(2)
    setp(P_DECAY); assert shown() == [PARAM, SCOPE, V100, DMODE] and screen()[1][12:] == "END ", (shown(), screen())
    assert screen()[1][10:12] == "  ", ("no catch hint", screen())
    e.wr(DMODE, 1, 1); t.fire(2); assert vals(DMODE_OFF) == (1, 0, 0, 0, 0) and screen()[1][12:] == "STRT", screen()
    shot("DECAY")
    e.wr(SCOPE, 1, 1); t.fire(2); e.wr(DMODE, 0, 1); t.fire(2); e.wr(DMODE, 1, 1); t.fire(2)
    assert vals(DMODE_OFF) == (1, 1, 1, 1, 0), vals(DMODE_OFF)
    e.wr(SCOPE, 0, 1); t.fire(2)
    print("  ok ATTACK +0x66, DECAY +0x67 (+ END/STRT +0x68), V>LEVEL +0x6B, V>FILT +0x78, V>TUNE +0x70, "
          "V>ATK +0x69, V>START +0x6A: wheel, slider, PROG relative and back exact")

    # ===== VEL RANGE, SAMPLE LEVEL (per layer) =====
    setp(P_VRANGE); e.wr(LAYF, 3, 1); t.fire(3)
    assert screen()[0] == "VEL RANGE      A" and screen()[1][:14] == " A5:L4:  0-127", screen(); shot("VEL RANGE")
    assert shown() == [PARAM, LAYF, VLO, VHI], shown()
    W32(pg.page + 0x24, WID[VLO]); t.fire(3)                                       # cursor on the low value
    slider(0, 127); slider(30, 127); assert e.rd(lay(4, 3) + VLO_OFF, 1) == 30 and e.rd(VLO, 1) == 30
    W32(pg.page + 0x24, WID[VHI]); t.fire(3)                                       # cursor on the high value
    assert R32(LHI) == 1
    slider(127, 127); slider(100, 127)
    assert e.rd(lay(4, 3) + VLO_OFF, 1) == 30 and abs(e.rd(lay(4, 3) + VHI_OFF, 1) - 100) <= 1, \
        "slider moves the value with the cursor (127 steps on the slider: +-1)"
    slider(10, 127); assert (e.rd(lay(4, 3) + VLO_OFF, 1), e.rd(lay(4, 3) + VHI_OFF, 1)) == (30, 30), "high never below low"
    W32(pg.page + 0x24, WID[PARAM]); t.fire(3)
    e.wr(LAYF, 1, 1); t.fire(3)
    e.wr(VLO, 40, 1); t.fire(2); assert (e.rd(lay(4, 1) + VLO_OFF, 1), e.rd(lay(4, 1) + VHI_OFF, 1)) == (40, 127)
    e.wr(VHI, 30, 1); t.fire(2); assert e.rd(lay(4, 1) + VHI_OFF, 1) == 40 and e.rd(VHI, 1) == 40, "high never below low"
    e.wr(VHI, 90, 1); t.fire(2); e.wr(VLO, 100, 1); t.fire(2); assert (e.rd(lay(4, 1) + VLO_OFF, 1), e.rd(VLO, 1)) == (90, 90)
    assert screen()[1][:14] == " A5:L2: 90- 90", screen()
    e.wr(LAYF, 2, 1); t.fire(3); assert screen()[1][:14] == " A5:L3:  0-127", "layer 3 has its own range"
    e.wr(SCOPE, 1, 1); t.fire(2); e.wr(VLO, 10, 1); t.fire(2)
    assert [e.rd(lay(p, 2) + VLO_OFF, 1) for p in (4, 5, 6)] == [10, 0, 0], "per pad only"
    e.wr(SCOPE, 0, 1)
    setp(P_SLEVEL); e.wr(LAYF, 2, 1); t.fire(3)
    assert screen()[0] == "SAMPLE LEVEL   A" and screen()[1][:11] == " A5:L3:100 ", screen(); shot("SAMPLE LEVEL")
    wheel(60); assert [e.rd(lay(4, l) + SLVL_OFF, 1) for l in range(4)] == [100, 100, 60, 100]
    slider(60); slider(70, fires=40); assert e.rd(lay(4, 2) + SLVL_OFF, 1) == 70 and screen()[1] == " A5:L3: 70      ", screen()
    e.wr(LAYF, 1, 1); t.fire(3); assert field() == "100" and screen()[1].startswith(" A5:L2:100"), screen()
    print("  ok VEL RANGE 'VEL RANGE      A' / '05:L2:  0-127' per layer (low <= high), slider moves the value under the "
          "cursor; SAMPLE LEVEL per layer (+0x11)")

    # ===== bank B, program change =====
    setp(P_LEVEL); h.ram_w8(BANKOBJ, 1); t.pad(1, 8); t.fire(2); h.ram_w8(BANKOBJ, 0)
    assert screen()[0].endswith("B09") and field() == "20", screen()
    PROG2 = PROG_BASE + 2 * PROG_STRIDE
    for p in range(NPADS): setpad(p, {}, 100, 100, 50, base=PROG2)
    setpad(20, {0: "BASS"}, 60, 100, 70, base=PROG2)
    s1 = snap(); h.ram_w16(PROG_IDX, 2); t.fire(2); assert field() == "60", field()
    wheel(65); assert e.rd(pad(20, PROG2) + 0x8F, 1) == 65 and snap() == s1, "only program 2 changed"
    h.ram_w16(PROG_IDX, 1); t.fire(2); t.pad(0, 4); t.fire(2)
    print("  ok B09 from bank B; program changed: only the current program is edited")

    # ===== only the parameter bytes are ever written, all in range =====
    mask = {pad(p) + o for p in range(NPADS) for o in (0x8F, 0x72, 0x90, 0x62, 0x63, FTYPE_OFF, 0x66, 0x67, 0x68, 0x73,
                                                         0x6B, 0x78, 0x70, 0x69, 0x6A)}
    mask |= {lay(p, l) + o for p in range(NPADS) for l in range(4) for o in (TUNE_OFF, TUNE_OFF + 1, PLAY_OFF, SLVL_OFF, VLO_OFF, VHI_OFF)}
    s0 = snap(); e.wr(SCOPE, 1, 1); t.fire(2)
    for prm in PINFO:
        if prm in (P_SLEVEL, P_PGM, P_ASSIGN, P_VRANGE, P_RESO): continue
        setp(prm); n_ = PINFO[prm][2]; slider(10 * n_ // 100, n_); slider(n_, n_); wheel(n_ // 3)
    assert all(a_ == b_ for i, (a_, b_) in enumerate(zip(s0, snap())) if PROG + i not in mask), "other bytes changed"
    assert all(e.rd(pad(p) + o, 1) <= 100 for p in range(NPADS) for o in (0x8F, 0x72, 0x90, 0x66, 0x67, 0x73, 0x6B, 0x78, 0x70, 0x69, 0x6A))
    assert all(e.rd(pad(p) + 0x63, 1) <= 32 and e.rd(pad(p) + 0x62, 1) <= 1 and e.rd(pad(p) + FTYPE_OFF, 1) <= 3 for p in range(NPADS))
    assert all(abs(tune(p, l)) <= TUNE_MAX for p in range(NPADS) for l in range(4))
    assert t.pad(0, 7)[0] == 0, "pads still play (event left to the OS)"
    e.wr(SCOPE, 0, 1); t.fire(2)
    print("  ok nothing but the parameter bytes written, all in range; pads still play")

    # ===== PURGE: unused samples only, never the add-ons =====
    inst = [R32(0x88F00144 + 4 * k) for k in range(16)]
    setp(P_PURGE); assert screen()[0] == "PURGE           " and screen()[1] == "COUNTING...     ", screen()
    assert shown() == [PARAM], shown()
    t.fire(40)
    # used: SNARE (A05 L2), HAT (A05 L3, A06), KICK (A07); unused: CLAP, RIDE
    assert screen()[1] == "DO IT:  2 UNUSED", screen(); shot("PURGE")
    t.doit(); assert screen()[1] == "DEL   2? DO IT  ", screen(); shot("PURGE (DO IT)")
    t.cancel(); t.fire(40); assert screen()[1] == "DO IT:  2 UNUSED" and names() == [b"KICK", b"SNARE", b"HAT", b"CLAP", b"RIDE"]
    t.doit(); t.doit(); t.fire(1); sh = screen()[1]
    assert sh.startswith("DELETING ") and sh.endswith("/  2"), sh
    for _ in range(40): t.fire(1)
    assert screen()[1] == "DELETED   2     ", screen(); shot("PURGE (done)")
    assert names() == [b"KICK", b"SNARE", b"HAT"] and len(addons()) >= 1, (names(), addons())
    t.fire(30); assert [R32(0x88F00144 + 4 * k) for k in range(16)] == inst, "add-ons still installed"
    t.fire(DONE_FIRES + 50); assert screen()[1] == "NOTHING UNUSED  ", screen()
    t.doit(); assert t.msg() == "NOTHING TO PURGE", t.msg()
    print("  ok PURGE: 2 unused (CLAP, RIDE) counted, DO IT asks, CANCEL keeps all; DO IT DO IT deletes them; "
          "used samples and every add-on WAV kept, add-ons still installed")

    # ===== ENTER DEFAULT MPC PROGRAM MODE =====
    t.fire(pg.msg_fires + 10)                                                       # the message goes away
    setp(P_ENTER); assert screen() == ["ENTER DEFAULT   ", "MPC PROGRAM MODE"], screen(); shot("ENTER DEFAULT")
    W32(LOG, 0); t.doit(); assert pushes() == [] and t.msg() == "NOT AVAILABLE", (pushes(), t.msg())
    W32(OSPROG + 0x1C, VT_PAGE); W32(OSPROG + 0x30, OSPROG_H)                    # the MPC's page object, as built
    t.fire(300); W32(LOG, 0); t.doit(); assert pushes() == [(OSPROG, 0)], pushes()
    h.ram_w32(EVQ + 0x68, OSPROG); t.fire(5); assert pushes() == [(OSPROG, 0)], "opened from here: not redirected"
    print("  ok ENTER DEFAULT: DO IT opens the MPC's Program page (only when its object checks out) and it stays")

    # ===== MODE + pad 6 -> this page =====
    h.ram_w32(EVQ + 0x68, MODEPG); t.fire(2); W32(LOG, 0); h.ram_w32(EVQ + 0x68, OSPROG); t.fire(1)
    assert pushes() == [(0, 2), (pg.page, 0)], pushes()
    h.ram_w32(EVQ + 0x68, pg.page); t.fire(2)
    h.ram_w32(EVQ + 0x68, MODEPG); t.fire(2); W32(LOG, 0); h.ram_w32(EVQ + 0x68, 0x88490E60); t.fire(3)
    assert pushes() == [], "another MODE pad: left alone"
    W32(OSPROG + 0x30, 0x884D0000); h.ram_w32(EVQ + 0x68, MODEPG); t.fire(2); W32(LOG, 0); h.ram_w32(EVQ + 0x68, OSPROG); t.fire(2)
    assert pushes() == [], "object does not check out: left alone"
    print("  ok MODE+pad6: the MPC Program page opened from MODE -> back + this page; other MODE pads untouched")

    print("\nScreens (16x2):")
    for k, (r0, r1) in shots.items(): print("  %-28s |%s|\n  %-28s |%s|" % (k, r0, "", r1))
    print("OK: PROGRAM.WAV built and tested")


if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', '..', 'PROGRAM.WAV'); pg.build_wav(out); selftest(out)
