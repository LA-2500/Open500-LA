#!/usr/bin/env python3
"""TRIM - replaces the MPC's own Trim page (MODE + the Trim pad): trim a pad's sample with the Q-Link slider and the
data wheel; the slider jumps between the sample's transients, the wheel moves the point by frames. Loop, start link,
tune; DO IT extracts the trimmed range to an empty pad; ENTER DEFAULT opens the MPC's own Trim page.

    python sdk/examples/trim.py          -> builds TRIM.WAV and tests it in the emulator

On the MPC500: copy TRIM.WAV to the CF card, LOAD it (after HUB.WAV). MODE + the Trim pad opens it (MODE -> OTHER ->
"Trim" too). The cursor on the page name + the wheel chooses the page:
  Trim St:   "Trim St:   56504" / "            S:50"     End:  "End:      222815" / "            S:50"
             Slider: the page's point jumps between the transients (start points: sample start + every transient;
             end points: just before every transient + sample end). Wheel on the number: frames, by the digit under
             the cursor; LEFT / RIGHT move it over the digits the number HAS (no blanks), past them to the next field.
             S: = transient sensitivity 1..99 (wheel by digit; the slider stays on the points).  Loop st:   "Loop st:   12345" / "Loop:ON": loop start (slider: START..END in single steps, up to 250; wheel: frames by
             digit; kept inside St..End) and
             loop on/off (cursor on it: slider low/high = OFF/ON).
  St link:   "St link:ON": start link, as the MPC: ON = the loop start is the trim start and follows it.
  Tune:      "Tune: +12" / "Fine: +35": the sample's tune in semitones (-24..+24) + cents (-99..+99), max +-24.00.
  Edit:      "Edit:   Discard" / "A05:KICK_01": the MPC's own Trim edit functions on the sample shown (2026-11-07, user):
             Discard, Extract, Normaliz, Reverse, Stretch, Copy, Delete (wheel). Hit a pad = its sample; the slider
             scrolls through every sample in memory (row 1 = the name, "A05:" when it came from a pad). DO IT runs it;
             Discard and Delete ask first ("DO IT: DELETE?", DO IT again; CANCEL / anything else = no). Extract and Copy
             then open the MPC's "Assign to pad" page, Stretch its Time stretch page (as the MPC's Trim page does).
             Samples with +0x3E > 0 (sample packs): Copy / Delete only, as the MPC.
  ENTER ZONE / "ASSIGN MODE": DO IT opens the Zone add-on (zone.py) when it is loaded (HUB slot table, as the S500
             menu finds Audio Track), else "ZONE NOT LOADED". The two add-ons stay separate WAVs.
  ENTER DEFAULT / "MPC TRIM MODE": DO IT opens the MPC's own Trim page (Discard, Normalize, Reverse, Time stretch,
             Copy, Delete, Name stay there).
Every page: the slider (catch-up: it changes the value only after passing the value shown; no on-screen hint) and the
wheel on the field under the cursor. Hit a pad (it plays as usual) -> its sample (first layer with one) is analysed:
transients over the WHOLE sample (Trans-Chop detection). START always stays before END; the loop point is kept inside.
The edits go straight into the sample. DO IT -> "EXTRACT TO PAD", row 1 "MUTE GROUP OFF" (the wheel picks the group):
hitting an EMPTY pad extracts START..END into a new sample on that pad (layer 1, level 100, range 0..127); full pads are
refused; CANCEL goes back. The source stays selected. CANCEL while trimming = the previous Trim page (user 2026-11-07);
on the first (Trim St) it leaves the page (edits kept).
Transients v2 (2026-11-06; v1 = Trans-Chop's): envelope = peak |L| per block of 2^HOPSH frames (64 = 1.5 ms up to
  5.9 s, coarser on longer samples); prev = MAX of the blocks of the last 20 ms (a bass note's cycles are not hits);
  transient at block k
  when rise = E[k] - prev > max(E) * (100 - SENS) / 200 (loud vs the whole sample) OR (E[k] > max(E)/32 and
  rise > prev * (3 (100 - SENS) + 20) / 100: a big jump vs just before -> quiet hits in long music samples too), at least
  50 ms after the previous (none before the first: a drum hit right after a short silence counts). Point = first frame
  with |x| >= max(E[k]/4, prev+1) from block k-1, then back to the onset (|x| <= max(prev, E[k]/32), at most 10 ms): no
  pre-roll (v1: -5 ms). Up to MAXT - 1 transients (v1: 98). Samples shorter than 1024 frames: no transients. A SENS
  change re-picks after DEBOUNCE fires (the envelope is kept).

OS addresses (MPC500 OS 1.31; "fw" = read in the firmware 2026-11-03, not yet confirmed on hardware):
  Program/pads/bank: SDK README (program 0x8837B5AC + idx*0x1F98, idx u16 0x8847C0E2; pad = +0x18 + pad*0xA4; layer
  0x18: name16, +0x11 level, +0x12/+0x13 range, +0x14 tune). Bank byte 0x8849C98C. Pad events 0x38.
  Samples: MGR 0x8848ED30 (+8 current), find by name 0x884C5090 (S500). Block: +0x18 length, +0x20 start, +0x24 end,
  +0x28 loop point, +0x94 sample rate, PCM L from +0x100 (Trans-Chop / S500). Extract 0x884C5E90 via os_extract
  (Trans-Chop / Tap Chop, HW). Add-on WAVs in the sample list are skipped (+0x100 magic, Trans-Chop).
  fw: the OS Trim page parameters (group 5, getter 0x884F0C58 / setter 0x884F0AE8): 1 Trim st +0x20, 2 End +0x24,
  3 Loop st +0x28, 4 Loop +0x2C (u32 0/1), 5 St link = byte +0x15 (setter 0x884C5BBE: ON -> loop = start, then
  LOOP_GUARD 0x884C4F04(block)), 6 Tune +0x30 (u32, -2400..+2400 cents, setter 0x884C5C48), 7 Name.
  fw: MPC Trim page object 0x8849C2A4 (descriptor 0x881C592C: handler 0x884E4178, the Trim page's DO IT / row code);
  checked at run time (+0x1C = VT_PAGE, +0x30 = 0x884E4178) before it is redirected or opened. MODE page 0x8849C9C4:
  the Trim page opened straight from it -> back + this page (as the S500 menu for MODE + pad 4, HW).
  fw (2026-11-07, Edit page): the OS Trim page's Edit action 0x884E3CD4 (no args): byte 0x881C5841 = choice (1 Discard
  0x884C5E50, 2 Extract, 3 Normalize 0x884C5EEC, 4 Reverse 0x884C5F18, 5 Stretch page, 6 Copy, 7 Delete 0x884C5FFC; +5
  when the current sample's s16 +0x3E > 0, whose list is Select/Copy/Delete), on MGR+8, errors shown by the OS
  (0x884CD396); used here for 1 / 3 / 4 / 7 (2 when +0x3E > 0). Extract / Copy: os_extract (EXTRACT_CMD / COPY_CMD
  0x884C5FA0), then push the Assign page 0x8849C2E4; Stretch: push the Time stretch page 0x8849C324 (the OS uses
  goto_page there, which from an add-on leaves EVQ+0x68 on the old page: SDK). Pages checked (+0x1C = VT_PAGE) first.
  Q-Link slider 0x8848C3E4 (Pitch / Mixer, HW). Work buffer below the scratch with reserve = SLOT_CODE_MAX (Trans-Chop).
"""
import sys, os, struct, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pages import PageAddon, ld, st, sti, SET_FOCUS
from mpcmod import EVQ, LOAD_SCRATCH, SLOT_CODE_MAX, OS_PUSH_PAGE

PROG_BASE, PROG_IDX, PROG_STRIDE = 0x8837B5AC, 0x8847C0E2, 0x1F98
PAD_OFF, PAD_SIZE, LAYER = 0x18, 0xA4, 0x18
BANKOBJ = 0x8849C98C
MGR, FINDNAME, EXTRACT_CMD, LOOP_GUARD = 0x8848ED30, 0x884C5090, 0x884C5E90, 0x884C4F04
MAGIC_ADDON, MAGIC_HUB = 0x4143504D, 0x4843504D
SLIDER = 0x8848C3E4
SL_LO, SL_HI, HYST = 64, 960, 3
MAXT = 160                          # slider steps at most (transients + sample start); v2: 99 -> 160 (long samples)
HOP_MIN = 6                         # v2: envelope blocks of 64 frames (1.5 ms) up to 5.9 s, longer samples coarser
GAP_MS, BACK_MS, LOOK_MS = 50, 10, 20            # v2: min distance between hits (was 60 ms); onset search back from the hit
MAXBLK = 2560                       # envelope blocks (Trans-Chop: 5632; 2026-11-07: 4096 -> 2560 for the Edit page code:
                                    #  64-frame blocks up to 3.7 s, longer samples 128+)
WORK_OFF = 0x1680                   # work buffer = LOAD_SCRATCH - WORK_OFF: ENV u16[MAXBLK], POS u32[MAXT] (exactly)
POS_OFF = 2 * MAXBLK
assert POS_OFF + 4 * MAXT <= WORK_OFF
CACHE_SPAN = 0x400                  # cached kernels (checked at build: __cend - __cstart)
# Edit: Normalize / Reverse done here (2026-11-07, HW: the MPC's own did nothing from this page), with progress and a
# preview: a backup Copy first, the work in chunks per fire, DO IT keeps (backup deleted), CANCEL restores it
ES_IDLE, ES_RUN, ES_PREV, ES_UNDO = range(4)
ECHUNK = 16384                      # frames (words when restoring) per fire
MIN_N = 1024
DEBOUNCE, LEAVE_FIRES = 24, 16
ST_TRIM, ST_WAIT = 0, 1
K_LEFT, K_RIGHT = 0x02, 0x03                                              # key event codes (Grid Seq, HW)
MODEPG, OSTRIM, OSTRIM_H, VT_PAGE = 0x8849C9C4, 0x8849C2A4, 0x884E4178, 0x88507BA8
TUNE_MAX, FINE_MAX = 2400, 99
LOOP_STEPS = 250                    # Loop st slider steps at most (the slider resolves ~900 positions; hysteresis)

T_ST, T_END, T_LOOP, T_LINK, T_TUNE, T_EDIT, T_ZONE, T_ENTER = range(8)
T_TRIM = T_ST
NAMES = ["Trim St:", "End:", "Loop st:", "St link:", "Tune:", "Edit:", "ENTER ZO", "ENTER DE"]
LDR_B, ZONE_NAME = 0x8848ED34, b"zone1   "              # HUB slot table (s500_combo.py, HW) / the Zone add-on
K_NONE, K_START, K_END, K_FINE, K_SENS, K_LOOPST, K_LOOPON, K_LINK, K_SEMI, K_CENT, K_SMP = range(11)
# Edit page: our list order -> the OS Trim page's Edit choice (0 = done ourselves)
EDITS = ["Truncate", "Extract", "Normaliz", "Reverse", "Stretch", "Copy", "Delete"]
E_DISCARD, E_EXTRACT, E_NORM, E_REV, E_STRETCH, E_COPY, E_DELETE = range(7)
OS_EDITNUM = {E_DISCARD: 1, E_DELETE: 7}
OS_EDIT, OS_EDITSEL, COPY_CMD, DELETE_CMD = 0x884E3CD4, 0x881C5841, 0x884C5FA0, 0x884C5FFC
PG_ASSIGN, PG_STRETCH = 0x8849C2E4, 0x8849C324
def sgn3(v): return "%+3d" % v if v else "  0"

pg = PageAddon("trim1", open_press=None)
pg.reserve = SLOT_CODE_MAX
pg.other_menu("Trim")
pg.far_hend = True
pg.far_fire = True
pg.msg_fires = 0x400
# row 0 (drawn in this order)
PARAM = pg.choice("", 0, 0, [n.ljust(8) for n in NAMES], default=T_TRIM)
T2 = pg.dlabel(8, 0, 5)                                                   # "NE" / "FAULT": ENTER ZONE / ENTER DEFAULT
STF = pg.value("", 8, 0, fmt="       0", lo=0, hi=99999999, default=0)    # Trim St
ENDF = pg.value("", 8, 0, fmt="       0", lo=0, hi=99999999, default=0)   # End (its own page)
LOOPF = pg.value("", 8, 0, fmt="       0", lo=0, hi=99999999, default=0)  # Loop st
LINKF = pg.choice("", 8, 0, ["OFF", "ON "], default=0)                    # St link
SEMI = pg.choice("", 6, 0, [sgn3(v) for v in range(-24, 25)], default=24) # Tune (semitones)
EDITF = pg.choice("", 8, 0, [s.ljust(8) for s in EDITS], default=E_DISCARD)    # Edit: the MPC's function
# row 1: its text first, then the fields
R1 = pg.dlabel(0, 1, 16)
SENS = pg.value("", 14, 1, fmt=" 0", lo=1, hi=99, default=50)             # "S:" sensitivity
MG = pg.choice("", 13, 1, ["OFF"] + ["%3d" % g for g in range(1, 33)], default=0)    # pad +0x63: 0 = off, 1..32
LOOPON = pg.choice("", 5, 1, ["OFF", "ON "], default=0)                   # Loop
CENT = pg.choice("", 6, 1, [sgn3(v) for v in range(-FINE_MAX, FINE_MAX + 1)], default=FINE_MAX)  # Fine (cents)
pg.message()
SHOWMSG = pg.SHOWMSG
CURSOR = [PARAM, STF, ENDF, LOOPF, LINKF, SEMI, EDITF, SENS, MG, LOOPON, CENT]
POSW = {STF: 8, ENDF: 8, LOOPF: 8, SENS: 2}                                # digit fields: width
def fid(slot): return [it[1] for it in pg.items if it[0] in ('choice', 'value') and it[-1] == slot][0]
def W(slot): return pg.addr['values'][fid(slot)]                         # widgets (known once the page is built)
def set_focus(m, widget):
    m.imm32(pg.addr['page'], 4); m.imm32(widget, 5); m.imm32(SET_FOCUS, 2); m.a.jsr(2); m.a.nop()
def refocus(m):
    """the cursor back where it was before DO IT"""
    ld(m, FOCSAVE, 5); m.imm32(pg.addr['page'], 4); m.imm32(SET_FOCUS, 2); m.a.jsr(2); m.a.nop()
(ACTIVE, OUTCNT, ST, PEND, SEL, SRC, LEN, NT, NSTEP, LSENS, DEB, CUR, CAUGHT, SIDE, SS, SF, SINIT,
 SLO, SHI, DSTP, PREVCUR) = [pg.alloc_slot() for _ in range(21)]
(S0, NN, HOPSH, NB, MAXE, DTH, MINGAP, RQ, LASTK, KK) = [pg.alloc_slot() for _ in range(10)]
GATE, LWIN, BACK = [pg.alloc_slot() for _ in range(3)]                    # v2 detection (2026-11-06)
CONF, ESAVE = [pg.alloc_slot() for _ in range(2)]                         # Edit: waiting for the 2nd DO IT (function + 1)
(EST, EOP, EPH, ECUR, EDONE, ETOT, EPEAK, EGAIN, BAK, EPCT) = [pg.alloc_slot() for _ in range(10)]   # Normalize / Reverse
(KT, LKT, LP, BASEP, LST, LEND, LLOOP, LLON, LLINK, LSEMI, LCENT, LFN, FOCSAVE, PREVPG, LASTF, LPARAM, LSENW, DIG, LFOCD, LQ) = \
    [pg.alloc_slot() for _ in range(20)]
pg.defaults[SLO] = SL_LO; pg.defaults[SHI] = SL_HI; pg.defaults[SS] = 0xFF; pg.defaults[LKT] = 0xFF; pg.defaults[LSENW] = 50

def call(m, lbl): m.a.movl_pool(lbl, 2); m.a.jsr(2); m.a.nop()
def jmpl(m, lbl): m.a.movl_pool(lbl, 2); m.a.jmp(2); m.a.nop()
def osc(m, fn): m.imm32(fn, 3); m.a.jsr(3); m.a.nop()
def rdb(m, addr, r): m.imm32(addr, r); m.a.movb_ld(r, r); m.a.extu_b(r, r)
def wrb(m, rv, addr): m.imm32(addr, 1); m.a.movb_st(rv, 1)
def redraw(m): m.imm32(EVQ + 0x70, 1); m.a.mov_imm(1, 0); m.a.movl_st(0, 1)
def cached_call(m, label):
    m.a.movl_pool(label, 2); m.imm32(0xDFFFFFFF, 3); m.a.and_rr(3, 2); m.a.jsr(2); m.a.nop()
def work(m, r, off=0):
    """r = work buffer (+off) = relocated LOAD_SCRATCH - WORK_OFF. Uses r3"""
    m.imm32(LOAD_SCRATCH, r); m.imm32(WORK_OFF - off, 3); m.a.sub(3, r)
def padblk(m, rpad, rd_):
    """rd_ = pad block of pad rpad (0..47) in the current program. Uses r1, r3"""
    a = m.a
    m.imm32(PROG_IDX, 1); a.movw_ld(1, rd_); a.extu_w(rd_, rd_); m.imm32(PROG_STRIDE, 1); a.mul_l(1, rd_); a.sts_macl(rd_)
    m.imm32(PROG_BASE + PAD_OFF, 1); a.add_rr(1, rd_)
    m.imm32(PAD_SIZE, 3); a.mul_l(rpad, 3); a.sts_macl(3); a.add_rr(3, rd_)
def in_set(m, r, vals, lbl):
    for v in vals: m.a.mov_imm(v, 0); m.a.cmpeq(0, r); m.bt_far(lbl)

def fire(m):
    a = m.a
    sti(m, 0, OUTCNT)
    ld(m, ACTIVE, 0); a.tst_rr(0, 0); act = m.newlabel('act'); m.bf_far(act)
    sti(m, 1, ACTIVE); ld(m, SEL, 0); st(m, 0, PEND)                     # page (re)entered: find the pad's sample again
    a.deflabel(act)
    call(m, 'tr_bg')
    call(m, 'tr_fire')
    jmpl(m, '__tr_fend'); m.pool_jump()
    subs(m)
    a.deflabel('__tr_fend')
pg.on_fire(fire)

def inactive(m):
    a = m.a
    call(m, 'tr_bg')
    sti(m, 0xFF, LKT); sti(m, 0, SINIT)                                   # left the page: the slider may be elsewhere on return, so it has to catch the value again (user 2026-11-08: jumps on re-entry)
    call(m, 'tr_estep')                                                   # Normalize / Reverse / undo go on
    ld(m, ACTIVE, 0); a.tst_rr(0, 0); ix = m.newlabel('ix'); m.bt_far(ix)
    ld(m, OUTCNT, 0); a.add_imm(1, 0); st(m, 0, OUTCNT); a.mov_imm(LEAVE_FIRES, 3); a.cmpge(3, 0); m.bf_far(ix)
    sti(m, 0, ACTIVE); sti(m, 0, OUTCNT); sti(m, ST_TRIM, ST)            # left: no "EXTRACT TO PAD" waiting
    ikp = m.newlabel('ikp'); ld(m, EST, 0); a.mov_imm(ES_PREV, 1); a.cmpeq(1, 0); m.bf_far(ikp)
    call(m, 'tr_ekeep')                                                   # left during a preview: kept
    a.deflabel(ikp)
    a.deflabel(ix)
pg.on_inactive(inactive)

def subs(m):
    a = m.a
    # ---- tr_bg (every fire, any page): MODE + the Trim pad -> this page. r0-r9 ----
    a.deflabel('tr_bg'); a.stsl_pr()
    same = m.newlabel('same')
    m.imm32(EVQ + 0x68, 1); a.movl_ld(1, 9)
    ld(m, PREVPG, 0); a.cmpeq(9, 0); m.bt_far(same)
    a.mov_rm_rn(0, 8); st(m, 9, PREVPG)
    m.imm32(MODEPG, 1); a.cmpeq(1, 8); m.bf_far(same)
    m.imm32(OSTRIM, 1); a.cmpeq(1, 9); m.bf_far(same)
    a.movl_ld_disp(9, 0x1C, 0); m.imm32(VT_PAGE, 1); a.cmpeq(1, 0); m.bf_far(same)     # really the Trim page?
    a.movl_ld_disp(9, 0x30, 0); m.imm32(OSTRIM_H, 1); a.cmpeq(1, 0); m.bf_far(same)
    m.imm32(EVQ, 4); a.mov_imm(0, 5); a.mov_imm(2, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()   # back
    m.imm32(EVQ, 4); m.imm32(pg.addr['page'], 5); a.mov_imm(0, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop()
    m.imm32(pg.addr['page'], 0); st(m, 0, PREVPG)
    a.deflabel(same)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_fire: the page's work per fire ----
    a.deflabel('tr_fire'); a.stsl_pr()
    disp = m.newlabel('disp')
    call(m, 'tr_estep')
    epk_ = m.newlabel('epk_')                                                         # preview + another page: kept
    ld(m, EST, 0); a.mov_imm(ES_PREV, 1); a.cmpeq(1, 0); m.bf_far(epk_)
    rdb(m, PARAM, 0); a.mov_imm(T_EDIT, 1); a.cmpeq(1, 0); m.bt_far(epk_); call(m, 'tr_ekeep')
    a.deflabel(epk_)
    call(m, 'tr_slider')
    ld(m, PEND, 0); a.tst_rr(0, 0); np_ = m.newlabel('np'); m.bt_far(np_); call(m, 'tr_select'); a.deflabel(np_)
    call(m, 'tr_vis')
    # Edit: a 2nd DO IT is awaited only while the page and the function stay the same
    cfx = m.newlabel('cfx'); cfc = m.newlabel('cfc')
    ld(m, CONF, 0); a.tst_rr(0, 0); m.bt_far(cfx)
    rdb(m, PARAM, 1); a.mov_imm(T_EDIT, 2); a.cmpeq(2, 1); m.bf_far(cfc)
    rdb(m, EDITF, 2); a.add_imm(1, 2); ld(m, CONF, 0); a.cmpeq(2, 0); m.bt_far(cfx)   # (ld uses r1)
    a.deflabel(cfc); sti(m, 0, CONF); sti(m, 1, SHOWMSG)
    a.deflabel(cfx)
    sk0 = m.newlabel('sk0')
    ld(m, SRC, 0); a.tst_rr(0, 0); hs0 = m.newlabel('hs0'); m.bf_far(hs0)
    rdb(m, PARAM, 0); a.mov_imm(T_EDIT, 1); a.cmpeq(1, 0); m.bf_far(disp)            # Edit: the slider scrolls anyway
    jmpl(m, sk0)
    m.pool_jump()
    a.deflabel(hs0)
    # sensitivity changed -> re-pick after DEBOUNCE fires
    ld(m, SENS, 0); ld(m, LSENS, 2); a.cmpeq(2, 0); sx = m.newlabel('sx'); m.bt_far(sx)
    st(m, 0, LSENS); sti(m, DEBOUNCE, DEB)
    a.deflabel(sx)
    ld(m, DEB, 0); a.tst_rr(0, 0); nd = m.newlabel('nd'); m.bt_far(nd)
    a.add_imm(-1, 0); st(m, 0, DEB); a.tst_rr(0, 0); m.bf_far(nd)
    call(m, 'tr_analyze'); call(m, 'tr_sync')
    a.deflabel(nd)
    a.deflabel(sk0)
    ld(m, ST, 0); a.tst_rr(0, 0); m.bf_far(disp)                                     # EXTRACT TO PAD: nothing else
    # the point last worked on: the cursor on Trim St / End
    m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 9)
    n1, n2 = m.newlabel('lp1'), m.newlabel('lp2')
    m.imm32(W(STF), 1); a.cmpeq(1, 9); a.bf_lbl(n1); sti(m, 0, LP)
    a.deflabel(n1); m.imm32(W(ENDF), 1); a.cmpeq(1, 9); a.bf_lbl(n2); sti(m, 1, LP)
    a.deflabel(n2)
    ld(m, LFOCD, 0); a.cmpeq(0, 9); nfc = m.newlabel('nfc'); m.bt_far(nfc); st(m, 9, LFOCD); sti(m, 0, DIG)
    a.deflabel(nfc)
    call(m, 'tr_wheel')
    # what the slider edits: page + cursor; changed -> new reference
    call(m, 'tr_kt')
    ld(m, KT, 0); ld(m, LKT, 1); a.cmpeq(1, 0); nk = m.newlabel('nk'); m.bt_far(nk)
    st(m, 0, LKT); call(m, 'tr_ref')
    a.deflabel(nk)
    # slider, catch-up
    ld(m, NSTEP, 0); a.tst_rr(0, 0); m.bt_far(disp)
    fol = m.newlabel('fol'); cat = m.newlabel('cat')
    ld(m, CAUGHT, 0); a.tst_rr(0, 0); m.bf_far(fol)
    ld(m, CUR, 9); ld(m, SS, 10); a.cmpeq(9, 10); m.bt_far(cat)
    a.cmphi(9, 10); a.movt(2); ld(m, SIDE, 0); a.cmpeq(2, 0); m.bf_far(cat)          # passed the value: caught
    jmpl(m, disp); m.pool_jump()
    a.deflabel(cat); sti(m, 1, CAUGHT)
    a.deflabel(fol)
    ld(m, CUR, 9); ld(m, SS, 10); a.cmpeq(9, 10); m.bt_far(disp)
    st(m, 10, CUR); a.mov_rm_rn(10, 9); ld(m, KT, 8); call(m, 'tr_apply')
    a.deflabel(disp)
    call(m, 'tr_disp')
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_slider: SS = slider step 0..NSTEP (filtered, learned range, hysteresis; Pitch / Mixer). r0-r7 ----
    a.deflabel('tr_slider'); a.stsl_pr()
    m.imm32(SLIDER, 1); a.movl_ld(1, 2); m.imm32(0x3FF, 1); a.and_rr(1, 2); a.shll2(2); a.shll2(2)
    ld(m, SINIT, 0); a.tst_rr(0, 0); go = m.newlabel('go'); a.bf_lbl(go)
    st(m, 2, SF); sti(m, 1, SINIT)
    a.deflabel(go)
    ld(m, SF, 0); a.sub(0, 2); a.shar(2); a.shar(2); a.shar(2); a.add_rr(2, 0); st(m, 0, SF)
    a.mov_rm_rn(0, 6); a.shar(6); a.shar(6); a.shar(6); a.shar(6)                    # r6 = f (0..1023)
    ld(m, SLO, 0); c1 = m.newlabel('c1'); a.cmpgt(6, 0); a.bf_lbl(c1); st(m, 6, SLO); a.deflabel(c1)
    ld(m, SHI, 0); c2 = m.newlabel('c2'); a.cmpgt(0, 6); a.bf_lbl(c2); st(m, 6, SHI); a.deflabel(c2)
    ld(m, SLO, 7); a.sub(7, 6)
    ld(m, SHI, 5); a.sub(7, 5); a.mov_rm_rn(5, 7)                                     # r5 = r7 = span
    a.mov_rm_rn(6, 4); ld(m, NSTEP, 0); a.mul_l(0, 4); a.sts_macl(4)
    a.mov_rm_rn(7, 0); a.shlr(0); a.add_rr(0, 4); pg._hcall(m, '__udiv')             # r0 = round((f-lo)*N/span)
    ld(m, NSTEP, 1); c3 = m.newlabel('c3'); a.cmpgt(1, 0); a.bf_lbl(c3); a.mov_rm_rn(1, 0); a.deflabel(c3)
    a.mov_rm_rn(0, 3)
    ld(m, SS, 0); a.cmpeq(3, 0); sx2 = m.newlabel('sx'); m.bt_far(sx2)
    ld(m, NSTEP, 1); a.cmphi(1, 0); setv = m.newlabel('set'); m.bt_far(setv)         # not set yet (0xFF)
    a.tst_rr(3, 3); m.bt_far(setv); ld(m, NSTEP, 1); a.cmpeq(1, 3); m.bt_far(setv)    # the ends: always exact
    a.mov_rm_rn(0, 4); a.mul_l(7, 4); a.sts_macl(4); ld(m, NSTEP, 5)
    a.movl_st_dec(3, 15); pg._hcall(m, '__udiv'); a.movl_ld_inc(15, 3)                # r0 = SS*span/N
    a.sub(0, 6); a.cmppz(6); ab = m.newlabel('ab'); a.bt_lbl(ab); a.neg(6, 6); a.deflabel(ab)
    a.mov_rm_rn(7, 4); ld(m, NSTEP, 5); a.shll(5); a.movl_st_dec(3, 15); a.movl_st_dec(6, 15)
    pg._hcall(m, '__udiv'); a.movl_ld_inc(15, 6); a.movl_ld_inc(15, 3); a.add_imm(HYST, 0)   # half a step + HYST
    a.cmphi(0, 6); m.bf_far(sx2)
    a.deflabel(setv); st(m, 3, SS)
    a.deflabel(sx2); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_select: PEND = pad + 1 -> SEL; SRC = the sample of its first layer that has one; analysis ----
    a.deflabel('tr_select'); a.stsl_pr()
    ld(m, PEND, 0); st(m, 0, SEL); sti(m, 0, PEND); sti(m, 0, SRC); sti(m, 0, NSTEP); sti(m, ST_TRIM, ST)
    ncf = m.newlabel('ncf'); ld(m, CONF, 0); a.tst_rr(0, 0); m.bt_far(ncf); sti(m, 0, CONF); sti(m, 1, SHOWMSG)
    a.deflabel(ncf)
    ld(m, SEL, 9); a.add_imm(-1, 9); padblk(m, 9, 11)
    found = m.newlabel('found'); sxx = m.newlabel('sxx')
    for l in range(4):
        sk = m.newlabel('sk')
        a.mov_rm_rn(11, 1); a.add_imm(l * LAYER, 1); a.movb_ld(1, 0); a.tst_rr(0, 0); m.bt_far(sk)
        m.imm32(MGR, 4); a.mov_rm_rn(11, 5); a.add_imm(l * LAYER, 5); osc(m, FINDNAME); a.tst_rr(0, 0); m.bf_far(found)
        a.deflabel(sk)
    jmpl(m, sxx)
    m.pool_jump()
    a.deflabel(found)
    m.imm32(0x100, 2); a.add_rr(0, 2); a.movl_ld(2, 2)                                # an add-on WAV: not a sound
    m.imm32(MAGIC_ADDON, 3); a.cmpeq(3, 2); m.bt_far(sxx); m.imm32(MAGIC_HUB, 3); a.cmpeq(3, 2); m.bt_far(sxx)
    st(m, 0, SRC); a.movl_ld_disp(0, 0x18, 2); st(m, 2, LEN)
    ld(m, SENS, 0); st(m, 0, LSENS); sti(m, 0, DEB)
    call(m, 'tr_envelope'); call(m, 'tr_analyze'); call(m, 'tr_sync'); sti(m, 0xFF, LKT)
    a.deflabel(sxx)
    m.imm32(EVQ + 0x70, 1); a.mov_imm(1, 0); a.movl_st(0, 1)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_slist: the samples in memory (sample list, add-on WAVs skipped). r4 = n, r5 = a block ->
    #      r0 = the n-th sample (0 = none), r1 = how many, r2 = index of r5 (-1 = not there). r0-r7 (r8, r9 kept) ----
    a.deflabel('tr_slist')
    a.movl_st_dec(8, 15); a.movl_st_dec(9, 15)
    m.imm32(MGR, 1); a.movl_ld(1, 6); a.movl_ld_disp(1, 4, 7)                      # r6 = block, r7 = limit
    a.mov_imm(0, 0); a.mov_imm(0, 1); a.mov_imm(-1, 2)
    sll, sle, ssk, snf, snn = (m.newlabel(s) for s in ('sll', 'sle', 'ssk', 'snf', 'snn'))
    a.deflabel(sll)
    a.cmphs(7, 6); a.bt_lbl(sle)                                                      # block >= limit: end
    a.movl_ld(6, 3); a.mov_imm(1, 8); a.shll8(8); a.cmphs(8, 3); a.bf_lbl(sle)       # size < 0x100: end
    a.mov_imm(3, 8); a.tst_rr(8, 3); a.bf_lbl(sle)
    a.mov_rm_rn(6, 9); a.add_rr(3, 9); a.cmphi(7, 9); a.bt_lbl(sle)
    a.mov_imm(1, 8); a.shll8(8); a.add_rr(6, 8); a.movl_ld(8, 8)
    m.imm32(MAGIC_ADDON, 9); a.cmpeq(9, 8); a.bt_lbl(ssk); m.imm32(MAGIC_HUB, 9); a.cmpeq(9, 8); a.bt_lbl(ssk)
    a.cmpeq(5, 6); a.bf_lbl(snf); a.mov_rm_rn(1, 2); a.deflabel(snf)
    a.cmpeq(4, 1); a.bf_lbl(snn); a.mov_rm_rn(6, 0); a.deflabel(snn)
    a.add_imm(1, 1)
    a.deflabel(ssk); a.add_rr(3, 6); a.bra_lbl(sll); a.nop()
    a.deflabel(sle)
    a.movl_ld_inc(15, 9); a.rts(); a.movl_ld_inc(15, 8)
    m.pool_jump()

    # ---- tr_adopt: r4 = a sample block -> the source (no pad), analysed like a pad's ----
    a.deflabel('tr_adopt'); a.stsl_pr()
    st(m, 4, SRC); sti(m, 0, SEL); sti(m, ST_TRIM, ST)
    ld(m, SRC, 0); a.movl_ld_disp(0, 0x18, 2); st(m, 2, LEN)
    ld(m, SENS, 0); st(m, 0, LSENS); sti(m, 0, DEB)
    call(m, 'tr_envelope'); call(m, 'tr_analyze'); call(m, 'tr_sync'); sti(m, 0xFF, LKT)
    redraw(m)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_envelope: S0 = 0, NN = length, HOPSH, NB, ENV[] (whole sample, Trans-Chop) ----
    a.deflabel('tr_envelope'); a.stsl_pr()
    sti(m, 0, S0); ld(m, LEN, 3); st(m, 3, NN)
    m.imm32(MIN_N, 2); a.cmpge(2, 3); short = m.newlabel('short'); m.bf_far(short)
    a.mov_imm(HOP_MIN, 4); m.imm32(MAXBLK, 5)
    hl = m.newlabel('hl'); hx = m.newlabel('hx'); a.deflabel(hl)
    a.mov_rm_rn(3, 0); a.mov_rm_rn(4, 1); a.neg(1, 1); a.shad(1, 0); a.cmphs(5, 0); a.bf_lbl(hx); a.add_imm(1, 4); a.bra_lbl(hl); a.nop()
    a.deflabel(hx)
    st(m, 4, HOPSH)
    a.mov_imm(1, 0); a.shad(4, 0); a.add_imm(-1, 0); a.add_rr(0, 3); a.neg(4, 1); a.shad(1, 3); st(m, 3, NB)
    call(m, 'icinv'); cached_call(m, 'tr_env')
    a.deflabel(short)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_analyze: POS[0] = 0 + transients (pick), NT, NSTEP; the slider step is read again ----
    a.deflabel('tr_analyze'); a.stsl_pr()
    work(m, 10, POS_OFF); a.mov_imm(0, 0); a.movl_st(0, 10); sti(m, 1, NT)
    ld(m, NN, 3); m.imm32(MIN_N, 2); a.cmpge(2, 3); sh = m.newlabel('sh'); m.bf_far(sh)
    call(m, 'icinv'); cached_call(m, 'tr_pick')
    a.deflabel(sh)
    sti(m, 0xFF, LKT)                                                                 # new reference next fire
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_point: r5 = 0 start / 1 end / 2 loop -> r0 = that point of the sample. r0, r1 ----
    a.deflabel('tr_point')
    ld(m, SRC, 1); a.mov_imm(0x20, 0); a.shll2(5); a.add_rr(5, 0); a.shlr2(5)        # +0x20 / +0x24 / +0x28
    a.add_rr(1, 0); a.rts(); a.movl_ld(0, 0)
    m.pool_jump()

    # ---- tr_cand: r4 = step, r5 = 0 start / 1 end -> r0 = its frame. START: POS[i]; END: POS[i+1]-1 / length-1.
    #      r0, r2, r3 ----
    a.deflabel('tr_cand')
    work(m, 2, POS_OFF); a.mov_rm_rn(4, 0); a.shll2(0); a.add_rr(0, 2)              # r2 = &POS[i]
    a.tst_rr(5, 5); ce = m.newlabel('ce'); a.bf_lbl(ce)
    a.rts(); a.movl_ld(2, 0)
    a.deflabel(ce)
    ld(m, NT, 0); a.add_imm(-1, 0); a.cmpgt(4, 0); cl_ = m.newlabel('cl'); a.bf_lbl(cl_)     # i < NT-1
    a.movl_ld_disp(2, 4, 0); a.rts(); a.add_imm(-1, 0)
    a.deflabel(cl_); ld(m, LEN, 0); a.rts(); a.add_imm(-1, 0)
    m.pool_jump()

    # ---- tr_curidx: r6 = frame, r5 = 0 / 1 -> r0 = last step whose frame <= r6 (0 if none). Keeps r5, r6, r8+ ----
    a.deflabel('tr_curidx'); a.stsl_pr()
    ld(m, NT, 7); a.add_imm(-1, 7)
    ci, cx = m.newlabel('ci'), m.newlabel('cx'); a.deflabel(ci)
    a.cmppl(7); m.bf_far(cx)
    a.mov_rm_rn(7, 4); call(m, 'tr_cand'); a.cmphi(6, 0); m.bf_far(cx)     # cand <= frame: found
    a.add_imm(-1, 7); jmpl(m, ci)
    m.pool_jump()
    a.deflabel(cx); a.cmppz(7); cz = m.newlabel('cz'); a.bt_lbl(cz); a.mov_imm(0, 7); a.deflabel(cz)
    a.mov_rm_rn(7, 0)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_near: r6 = frame, r5 = 0 / 1 -> r0 = the step frame nearest to it. Keeps r5, r6, r8+ ----
    a.deflabel('tr_near'); a.stsl_pr()
    call(m, 'tr_curidx'); a.mov_rm_rn(0, 7); a.mov_rm_rn(0, 4); call(m, 'tr_cand')
    nx_ = m.newlabel('nx')
    ld(m, NT, 1); a.add_imm(-1, 1); a.cmpgt(7, 1); m.bf_far(nx_)                      # a next step exists
    a.mov_rm_rn(0, 4); a.movl_st_dec(4, 15); a.mov_rm_rn(7, 4); a.add_imm(1, 4); call(m, 'tr_cand')
    a.movl_ld_inc(15, 4)                                                              # r4 = cand i, r0 = cand i+1
    a.mov_rm_rn(0, 1); a.sub(6, 1); a.mov_rm_rn(6, 2); a.sub(4, 2)                    # r1 = next - f, r2 = f - cand
    a.cmpgt(1, 2); a.bt_lbl(nx_); a.mov_rm_rn(4, 0)                                   # f - cand > next - f: next
    a.deflabel(nx_)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_setp: r4 = wanted frame, r5 = 0 start / 1 end -> clamped (START 0..END-1, END START+1..length-1),
    #      written; start link: loop = start; else the loop is kept inside START..END; the fields follow ----
    a.deflabel('tr_setp'); a.stsl_pr()
    ld(m, SRC, 8); a.cmppz(4); p0 = m.newlabel('p0'); a.bt_lbl(p0); a.mov_imm(0, 4); a.deflabel(p0)
    a.tst_rr(5, 5); pe = m.newlabel('pe'); pw = m.newlabel('pw'); m.bf_far(pe)
    a.movl_ld_disp(8, 0x24, 0); a.add_imm(-1, 0); a.cmpgt(0, 4); k1 = m.newlabel('k1'); a.bf_lbl(k1); a.mov_rm_rn(0, 4); a.deflabel(k1)
    a.cmppz(4); k0 = m.newlabel('k0'); a.bt_lbl(k0); a.mov_imm(0, 4); a.deflabel(k0)
    a.movl_st_disp(4, 0x20, 8); a.bra_lbl(pw); a.nop()
    a.deflabel(pe)
    a.movl_ld_disp(8, 0x18, 0); a.add_imm(-1, 0); a.cmpgt(0, 4); k2 = m.newlabel('k2'); a.bf_lbl(k2); a.mov_rm_rn(0, 4); a.deflabel(k2)
    a.movl_ld_disp(8, 0x20, 0); a.add_imm(1, 0); a.cmpgt(4, 0); k3 = m.newlabel('k3'); a.bf_lbl(k3); a.mov_rm_rn(0, 4); a.deflabel(k3)
    a.movl_st_disp(4, 0x24, 8)
    a.deflabel(pw)
    call(m, 'tr_loopfix')
    call(m, 'tr_sync')
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_loopfix: start link ON -> loop = start; the loop inside START..END; LOOP_GUARD when the loop is on ----
    a.deflabel('tr_loopfix'); a.stsl_pr()
    ld(m, SRC, 8)
    lk = m.newlabel('lk'); lw = m.newlabel('lw')
    a.mov_rm_rn(8, 1); a.add_imm(0x15, 1); a.movb_ld(1, 0); a.tst_rr(0, 0); a.bt_lbl(lk)
    a.movl_ld_disp(8, 0x20, 0); a.bra_lbl(lw); a.nop()
    a.deflabel(lk)
    a.movl_ld_disp(8, 0x28, 0); a.movl_ld_disp(8, 0x20, 1); a.cmpgt(0, 1); l1 = m.newlabel('l1'); a.bf_lbl(l1); a.mov_rm_rn(1, 0); a.deflabel(l1)
    a.movl_ld_disp(8, 0x24, 1); a.cmpgt(1, 0); l2 = m.newlabel('l2'); a.bf_lbl(l2); a.mov_rm_rn(1, 0); a.deflabel(l2)
    a.deflabel(lw); a.movl_st_disp(0, 0x28, 8)
    a.movl_ld_disp(8, 0x2C, 0); a.tst_rr(0, 0); ng = m.newlabel('ng'); a.bt_lbl(ng)
    a.mov_rm_rn(8, 4); osc(m, LOOP_GUARD)
    a.deflabel(ng)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_apply: r8 = what (K_*), r9 = value (slider step / field index) -> the sample ----
    a.deflabel('tr_apply'); a.stsl_pr()
    ax = m.newlabel('ax')
    labs = {k: m.newlabel('ap') for k in (K_START, K_END, K_SENS, K_LOOPST, K_LOOPON, K_LINK, K_SEMI, K_CENT, K_SMP)}
    for k, l in labs.items(): a.mov_imm(k, 0); a.cmpeq(0, 8); m.bt_far(l)
    jmpl(m, ax); m.pool_jump()
    a.deflabel(labs[K_SMP])                                                           # Edit: step = the n-th sample
    a.mov_rm_rn(9, 4); a.mov_imm(0, 5); call(m, 'tr_slist'); a.tst_rr(0, 0); m.bt_far(ax)
    ld(m, SRC, 1); a.cmpeq(1, 0); m.bt_far(ax); a.mov_rm_rn(0, 4); call(m, 'tr_adopt'); jmpl(m, ax)
    m.pool_jump()
    for k in (K_START, K_END):                                                        # a step of the point
        a.deflabel(labs[k]); a.mov_rm_rn(9, 4); a.mov_imm(k - K_START, 5); call(m, 'tr_cand')
        a.mov_rm_rn(0, 4); a.mov_imm(k - K_START, 5); call(m, 'tr_setp'); jmpl(m, ax)
        m.pool_jump()
    a.deflabel(labs[K_SENS]); a.add_imm(1, 9); st(m, 9, SENS); st(m, 9, LSENW); jmpl(m, ax)   # the debounce re-picks
    a.deflabel(labs[K_LOOPST])                                                        # loop start: START + step * LQ
    ld(m, LQ, 0); a.mul_l(9, 0); a.sts_macl(0); ld(m, SRC, 8); a.movl_ld_disp(8, 0x20, 1); a.add_rr(1, 0)
    a.movl_st_disp(0, 0x28, 8)
    a.mov_rm_rn(8, 1); a.add_imm(0x15, 1); a.mov_imm(0, 0); a.movb_st(0, 1)            # a loop start of its own: link off
    call(m, 'tr_loopfix'); call(m, 'tr_sync'); jmpl(m, ax)
    m.pool_jump()
    a.deflabel(labs[K_LOOPON])
    ld(m, SRC, 8); a.movl_st_disp(9, 0x2C, 8); call(m, 'tr_loopfix'); call(m, 'tr_sync'); jmpl(m, ax)
    a.deflabel(labs[K_LINK])
    ld(m, SRC, 8); a.mov_rm_rn(8, 1); a.add_imm(0x15, 1); a.mov_rm_rn(9, 0); a.movb_st(0, 1)
    call(m, 'tr_loopfix'); call(m, 'tr_sync'); jmpl(m, ax)
    m.pool_jump()
    for k, F in ((K_SEMI, SEMI), (K_CENT, CENT)):
        a.deflabel(labs[k]); a.mov_rm_rn(9, 0); wrb(m, 0, F); call(m, 'tr_tune'); jmpl(m, ax)
    a.deflabel(ax)
    redraw(m)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_tune: tune = (SEMI - 24) * 100 + (CENT - 99), -2400..+2400 -> sample +0x30 (u32, as the OS) ----
    a.deflabel('tr_tune'); a.stsl_pr()
    rdb(m, SEMI, 4); a.add_imm(-24, 4); a.mov_imm(100, 0); a.mul_l(0, 4); a.sts_macl(4)
    rdb(m, CENT, 0); a.add_imm(-FINE_MAX, 0); a.add_rr(0, 4)
    m.imm32(TUNE_MAX, 0); t1 = m.newlabel('t1'); a.cmpgt(0, 4); a.bf_lbl(t1); a.mov_rm_rn(0, 4); a.deflabel(t1)
    a.neg(0, 0); t2 = m.newlabel('t2'); a.cmpgt(4, 0); a.bf_lbl(t2); a.mov_rm_rn(0, 4); a.deflabel(t2)
    ld(m, SRC, 8); a.movl_st_disp(4, 0x30, 8)
    call(m, 'tr_sync')
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_sync: the sample -> every field (and the copies the wheel is compared with); F = the nudge from the
    #      transient nearest to the point last worked on (not while the cursor is on F) ----
    a.deflabel('tr_sync'); a.stsl_pr()
    ld(m, SRC, 8)
    a.movl_ld_disp(8, 0x20, 0); st(m, 0, STF); st(m, 0, LST)
    a.movl_ld_disp(8, 0x24, 0); st(m, 0, ENDF); st(m, 0, LEND)
    a.movl_ld_disp(8, 0x28, 0); st(m, 0, LOOPF); st(m, 0, LLOOP)
    a.movl_ld_disp(8, 0x2C, 0); a.tst_rr(0, 0); a.movt(0); a.mov_imm(1, 1); a.xor_rr(1, 0); wrb(m, 0, LOOPON); st(m, 0, LLON)
    a.mov_rm_rn(8, 1); a.add_imm(0x15, 1); a.movb_ld(1, 0); a.tst_rr(0, 0); a.movt(0); a.mov_imm(1, 1); a.xor_rr(1, 0)
    wrb(m, 0, LINKF); st(m, 0, LLINK)
    # tune -> semitones (towards 0) + cents
    a.movl_ld_disp(8, 0x30, 4); a.mov_imm(0, 9); a.cmppz(4); tp = m.newlabel('tp'); a.bt_lbl(tp); a.neg(4, 4); a.mov_imm(1, 9)
    a.deflabel(tp); a.mov_imm(100, 5); pg._hcall(m, '__udiv')                        # r0 = |semis|, r1 = |cents|
    tq = m.newlabel('tq'); a.tst_rr(9, 9); a.bt_lbl(tq); a.neg(0, 0); a.neg(1, 1); a.deflabel(tq); a.mov_rm_rn(1, 7); a.mov_rm_rn(1, 7)
    a.mov_imm(24, 2); tz = m.newlabel('tz'); a.cmpgt(2, 0); a.bf_lbl(tz); a.mov_rm_rn(2, 0); a.deflabel(tz)
    a.neg(2, 2); tw = m.newlabel('tw'); a.cmpgt(0, 2); a.bf_lbl(tw); a.mov_rm_rn(2, 0); a.deflabel(tw)
    a.add_imm(24, 0); wrb(m, 0, SEMI); st(m, 0, LSEMI)
    a.mov_rm_rn(7, 0); a.add_imm(FINE_MAX, 0); wrb(m, 0, CENT); st(m, 0, LCENT)
    redraw(m)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_kt: KT = what the slider edits (page + cursor) ----
    a.deflabel('tr_kt'); a.stsl_pr()
    kd = m.newlabel('kd')
    a.mov_imm(K_NONE, 9)
    ld(m, EST, 0); a.tst_rr(0, 0); m.bf_far(kd)                                      # Normalize / Reverse busy
    ke = m.newlabel('ke'); rdb(m, PARAM, 6); a.mov_imm(T_EDIT, 0); a.cmpeq(0, 6); m.bf_far(ke)
    ld(m, ST, 0); a.tst_rr(0, 0); m.bf_far(kd); a.mov_imm(K_SMP, 9); jmpl(m, kd)     # Edit: the slider = the sample
    a.deflabel(ke)
    ld(m, SRC, 0); a.tst_rr(0, 0); m.bt_far(kd)
    ld(m, ST, 0); a.tst_rr(0, 0); m.bf_far(kd)
    m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 7)                               # r7 = focused widget
    rdb(m, PARAM, 6)
    def onf(slot, k, lbl):
        n = m.newlabel('of'); m.imm32(W(slot), 1); a.cmpeq(1, 7); a.bf_lbl(n); a.mov_imm(k, 9); jmpl(m, lbl); a.deflabel(n)
    pt, pe_, pl_, pk, pu = (m.newlabel(s) for s in ('pt', 'pe', 'pl', 'pk', 'pu'))
    for p, l in ((T_ST, pt), (T_END, pe_), (T_LOOP, pl_), (T_LINK, pk), (T_TUNE, pu)):
        a.mov_imm(p, 0); a.cmpeq(0, 6); m.bt_far(l)
    jmpl(m, kd); m.pool_jump()
    a.deflabel(pt); a.mov_imm(K_START, 9); jmpl(m, kd)                               # Trim St page: the start
    a.deflabel(pe_); a.mov_imm(K_END, 9); jmpl(m, kd)                                 # End page: the end
    m.pool_jump()
    a.deflabel(pl_); onf(LOOPON, K_LOOPON, kd); a.mov_imm(K_LOOPST, 9); jmpl(m, kd)
    a.deflabel(pk); a.mov_imm(K_LINK, 9); jmpl(m, kd)
    a.deflabel(pu); onf(CENT, K_CENT, kd); a.mov_imm(K_SEMI, 9)
    a.deflabel(kd); st(m, 9, KT)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_ref: steps and current value of what the slider edits; caught only if the slider is there ----
    a.deflabel('tr_ref'); a.stsl_pr()
    rx, rset = m.newlabel('rx'), m.newlabel('rset')
    ld(m, KT, 8); a.mov_imm(0, 3); a.mov_imm(0, 9)
    lb = {k: m.newlabel('rf') for k in (K_START, K_END, K_LOOPST, K_SENS, K_LOOPON, K_LINK, K_SEMI, K_CENT, K_SMP)}
    for k, l in lb.items(): a.mov_imm(k, 0); a.cmpeq(0, 8); m.bt_far(l)
    jmpl(m, rset); m.pool_jump()
    a.deflabel(lb[K_SMP])                                                             # Edit: the samples in memory
    a.mov_imm(-1, 4); ld(m, SRC, 5); call(m, 'tr_slist')
    a.mov_rm_rn(2, 9); a.cmppz(9); q0 = m.newlabel('q0'); a.bt_lbl(q0); a.mov_imm(0, 9); a.deflabel(q0)
    a.mov_rm_rn(1, 3); a.add_imm(-1, 3); a.cmppz(3); q9 = m.newlabel('q9'); a.bt_lbl(q9); a.mov_imm(0, 3); a.deflabel(q9)
    jmpl(m, rset)
    m.pool_jump()
    a.deflabel(lb[K_LOOPST])                                                          # START..END in up to LOOP_STEPS
    ld(m, SRC, 8); a.movl_ld_disp(8, 0x20, 6); a.movl_ld_disp(8, 0x24, 7); a.sub(6, 7)   # r6 = START, r7 = span
    m.imm32(LOOP_STEPS, 3); q1 = m.newlabel('q1'); a.cmphi(7, 3); a.bf_lbl(q1); a.mov_rm_rn(7, 3); a.deflabel(q1)
    q2 = m.newlabel('q2'); a.tst_rr(3, 3); a.bf_lbl(q2); a.mov_imm(1, 3); a.deflabel(q2)   # r3 = steps (1..1000)
    a.mov_rm_rn(7, 4); a.mov_rm_rn(3, 5); pg._hcall(m, '__udiv'); a.tst_rr(0, 0); q3 = m.newlabel('q3'); a.bf_lbl(q3)
    a.mov_imm(1, 0); a.deflabel(q3); st(m, 0, LQ)                                     # LQ = frames per step
    a.movl_ld_disp(8, 0x28, 4); a.sub(6, 4); a.cmppz(4); q4 = m.newlabel('q4'); a.bt_lbl(q4); a.mov_imm(0, 4); a.deflabel(q4)
    ld(m, LQ, 5); pg._hcall(m, '__udiv'); a.mov_rm_rn(0, 9)
    q5 = m.newlabel('q5'); a.cmphi(3, 9); a.bf_lbl(q5); a.mov_rm_rn(3, 9); a.deflabel(q5)   # <= steps
    jmpl(m, rset)
    m.pool_jump()
    for k, which, pnt in ((K_START, 0, 0), (K_END, 1, 1)):
        a.deflabel(lb[k]); a.mov_imm(pnt, 5); call(m, 'tr_point'); a.mov_rm_rn(0, 6); a.mov_imm(which, 5)
        call(m, 'tr_curidx'); a.mov_rm_rn(0, 9); ld(m, NT, 3); a.add_imm(-1, 3); jmpl(m, rset)
        m.pool_jump()
    a.deflabel(lb[K_SENS]); ld(m, SENS, 9); a.add_imm(-1, 9); a.mov_imm(98, 3); jmpl(m, rset)
    a.deflabel(lb[K_LOOPON]); rdb(m, LOOPON, 9); a.mov_imm(1, 3); jmpl(m, rset)
    a.deflabel(lb[K_LINK]); rdb(m, LINKF, 9); a.mov_imm(1, 3); jmpl(m, rset)
    a.deflabel(lb[K_SEMI]); rdb(m, SEMI, 9); a.mov_imm(48, 3); jmpl(m, rset)
    a.deflabel(lb[K_CENT]); rdb(m, CENT, 9); m.imm32(2 * FINE_MAX, 3)
    m.pool_jump()
    a.deflabel(rset)
    ld(m, NSTEP, 0); a.cmpeq(3, 0); sn = m.newlabel('sn'); m.bt_far(sn)
    st(m, 3, NSTEP); m.imm32(0xFF, 0); st(m, 0, SS); call(m, 'tr_slider')             # new steps: slider step now
    a.deflabel(sn)
    st(m, 9, CUR)
    ld(m, SS, 10); a.cmpeq(9, 10); a.movt(0); st(m, 0, CAUGHT)
    a.cmphi(9, 10); a.movt(0); st(m, 0, SIDE)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_ndig (leaf): r4 = number, r5 = field width -> r0 = its last digit (digits - 1, 0..width-1). r0, r1, r3 ----
    a.deflabel('tr_ndig')
    nl, nx = m.newlabel('ndl'), m.newlabel('ndx')
    a.mov_imm(0, 0); a.mov_imm(10, 1); a.add_imm(-1, 5)
    a.deflabel(nl); a.cmpeq(5, 0); a.bt_lbl(nx); a.cmphs(1, 4); a.bf_lbl(nx)          # r4 >= 10^(r0+1): one more
    a.add_imm(1, 0); a.mov_rm_rn(1, 3); a.shll2(1); a.add_rr(3, 1); a.shll(1)               # 10^ one more
    a.bra_lbl(nl); a.nop()
    a.deflabel(nx); a.add_imm(1, 5); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_pow: r0 = 10^DIG. r0, r1, r3 ----
    a.deflabel('tr_pow')
    ld(m, DIG, 3); a.mov_imm(1, 0); pl = m.newlabel('pl'); px_ = m.newlabel('ppx'); a.deflabel(pl)
    a.tst_rr(3, 3); a.bt_lbl(px_); a.mov_rm_rn(0, 1); a.shll2(0); a.add_rr(1, 0); a.shll(0); a.add_imm(-1, 3)   # *10
    a.bra_lbl(pl); a.nop()
    a.deflabel(px_); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_wheel: a field changed by the wheel -> the sample; Trim St / End / Loop st move by the digit under the
    #      cursor (frames, LEFT / RIGHT pick the digit); the slider has to catch again ----
    a.deflabel('tr_wheel'); a.stsl_pr()
    wx, wch = m.newlabel('wx'), m.newlabel('wch')
    for F, LF, which in ((STF, LST, 0), (ENDF, LEND, 1), (LOOPF, LLOOP, 2)):
        nx, asis, go = m.newlabel('wn'), m.newlabel('wa'), m.newlabel('wg')
        ld(m, F, 4); ld(m, LF, 11); a.cmpeq(4, 11); m.bt_far(nx)
        a.mov_rm_rn(4, 6); a.sub(11, 6)                                               # r6 = d
        call(m, 'tr_pow'); a.mov_rm_rn(0, 7); a.mov_imm(1, 1); a.cmpeq(1, 7); m.bt_far(asis)   # r7 = 10^DIG
        a.mov_rm_rn(6, 4); a.cmppz(4); pz = m.newlabel('pz'); a.bt_lbl(pz); a.neg(4, 4); a.deflabel(pz)
        a.mov_rm_rn(7, 5); pg._hcall(m, '__udiv'); a.tst_rr(1, 1); m.bt_far(asis)    # |d| % step == 0: as it is
        a.mul_l(7, 6); a.sts_macl(4); a.add_rr(11, 4); jmpl(m, go)                    # last + d * 10^DIG
        a.deflabel(asis); ld(m, F, 4)
        a.deflabel(go)
        if which < 2: a.mov_imm(which, 5); call(m, 'tr_setp')
        else:
            a.cmppz(4); p0 = m.newlabel('lp0'); a.bt_lbl(p0); a.mov_imm(0, 4); a.deflabel(p0)
            ld(m, SRC, 8); a.movl_st_disp(4, 0x28, 8)                                 # loop start (kept inside)
            a.mov_rm_rn(8, 1); a.add_imm(0x15, 1); a.mov_imm(0, 0); a.movb_st(0, 1)    # its own: link off
            call(m, 'tr_loopfix'); call(m, 'tr_sync')
        jmpl(m, wch)
        a.deflabel(nx); m.pool_jump()
    # Sen changed by the wheel (the slider's own changes are in LSENW already)
    ns_, sa, sg = m.newlabel('ns'), m.newlabel('sa'), m.newlabel('sg')
    ld(m, SENS, 4); ld(m, LSENW, 11); a.cmpeq(4, 11); m.bt_far(ns_)
    a.mov_rm_rn(4, 6); a.sub(11, 6)                                                   # r6 = d
    call(m, 'tr_pow'); a.mov_rm_rn(0, 7); a.mov_imm(1, 1); a.cmpeq(1, 7); m.bt_far(sa)
    a.mov_rm_rn(6, 4); a.cmppz(4); spz = m.newlabel('spz'); a.bt_lbl(spz); a.neg(4, 4); a.deflabel(spz)
    a.mov_rm_rn(7, 5); pg._hcall(m, '__udiv'); a.tst_rr(1, 1); m.bt_far(sa)
    a.mul_l(7, 6); a.sts_macl(4); a.add_rr(11, 4); jmpl(m, sg)
    a.deflabel(sa); ld(m, SENS, 4)
    a.deflabel(sg)
    a.mov_imm(1, 0); s1 = m.newlabel('s1'); a.cmpgt(4, 0); a.bf_lbl(s1); a.mov_rm_rn(0, 4); a.deflabel(s1)   # 1..99
    a.mov_imm(99, 0); s2 = m.newlabel('s2'); a.cmpgt(0, 4); a.bf_lbl(s2); a.mov_rm_rn(0, 4); a.deflabel(s2)
    st(m, 4, SENS); st(m, 4, LSENW); jmpl(m, wch)
    a.deflabel(ns_)
    for F, LF, kind in ((LOOPON, LLON, K_LOOPON), (LINKF, LLINK, K_LINK), (SEMI, LSEMI, K_SEMI), (CENT, LCENT, K_CENT)):
        nx = m.newlabel('wo')
        rdb(m, F, 9); ld(m, LF, 0); a.cmpeq(9, 0); m.bt_far(nx)
        a.mov_imm(kind, 8); call(m, 'tr_apply'); jmpl(m, wch)
        a.deflabel(nx); m.pool_jump()
    jmpl(m, wx)
    a.deflabel(wch)
    sti(m, 0xFF, LKT)                                                                 # new reference ...
    call(m, 'tr_kt'); ld(m, KT, 0); st(m, 0, LKT); call(m, 'tr_ref'); sti(m, 0, CAUGHT)   # ... and catch again
    ld(m, CUR, 9); ld(m, SS, 10); a.cmphi(9, 10); a.movt(0); st(m, 0, SIDE)
    a.deflabel(wx)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_vis: the fields of the chosen page; the cursor skips hidden fields. r0-r7, r9-r14 ----
    a.deflabel('tr_vis'); a.stsl_pr()
    WL = [W(s) for s in CURSOR]
    DL = lambda i: pg.addr['dlabels'][i] - 0x28
    a.mov_imm(0, 14)
    rdb(m, PARAM, 4)
    a.mov_imm(0, 5); ld(m, SRC, 0); a.tst_rr(0, 0); ok0 = m.newlabel('ok0'); a.bt_lbl(ok0)   # r5 = sample, trimming
    ld(m, ST, 0); a.tst_rr(0, 0); a.movt(5)
    a.deflabel(ok0)
    ld(m, ST, 6)                                                                      # r6 = EXTRACT TO PAD
    def vis(w, params, need=True, wait=False):
        n1, n2, yes = m.newlabel('v1'), m.newlabel('v2'), m.newlabel('vy')
        m.imm32(w + 0x17, 1); a.movb_ld(1, 0); a.extu_b(0, 0); a.mov_rm_rn(0, 3); a.and_imm(0x7F)
        if wait: a.tst_rr(6, 6); m.bf_far(yes); jmpl(m, n1)
        else:
            if need: a.tst_rr(5, 5); m.bt_far(n1)
            for p in params: a.mov_imm(p, 2); a.cmpeq(2, 4); m.bt_far(yes)
            jmpl(m, n1)
        a.deflabel(yes); a.or_imm(0x80)
        a.deflabel(n1); a.cmpeq(0, 3); a.bt_lbl(n2); a.movb_st(0, 1); a.mov_imm(1, 14); a.deflabel(n2)
        m.pool_jump()
    vis(W(STF), [T_ST]); vis(W(ENDF), [T_END]); vis(W(SENS), [T_ST, T_END])
    vis(W(LOOPF), [T_LOOP]); vis(W(LOOPON), [T_LOOP]); vis(W(LINKF), [T_LINK])
    vis(W(SEMI), [T_TUNE]); vis(W(CENT), [T_TUNE]); vis(W(EDITF), [T_EDIT], need=False)
    vis(W(MG), [], wait=True)
    vis(DL(T2), [T_ZONE, T_ENTER], need=False)
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

    # ---- tr_disp: the texts (row 1 of the page, "DEFAULT", the pad); redraw only when a text changed ----
    a.deflabel('tr_disp'); a.stsl_pr()
    a.mov_imm(0, 14)
    def cput(s):
        for ch in s:
            n = m.newlabel('cp'); a.movb_ld(12, 1); a.mov_imm(ord(ch), 0); a.cmpeq(0, 1); a.bt_lbl(n)
            a.movb_st(0, 12); a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
    def cputr(r):
        n = m.newlabel('cr'); a.movb_ld(12, 1); a.cmpeq(r, 1); a.bt_lbl(n); a.mov_rm_rn(r, 0); a.movb_st(0, 12)
        a.mov_imm(1, 14); a.deflabel(n); a.add_imm(1, 12)
    m.imm32(pg.addr['dlabels'][T2], 12); t2z, t2d = m.newlabel('t2z'), m.newlabel('t2d')
    rdb(m, PARAM, 0); a.mov_imm(T_ZONE, 1); a.cmpeq(1, 0); m.bt_far(t2z); cput("FAULT"); jmpl(m, t2d)
    a.deflabel(t2z); cput("NE   ")
    a.deflabel(t2d)
    # digit fields: the cursor under the digit the wheel moves (model +1 = cursor column, OS: width - 1; HW, v1)
    for F, wdt in POSW.items():
        dg, c0 = m.newlabel('dg'), m.newlabel('c0')
        ld(m, F, 4); a.mov_imm(wdt, 5); call(m, 'tr_ndig')                           # r0 = the number's last digit
        ld(m, DIG, 3); a.cmphi(0, 3); m.bf_far(c0); a.mov_rm_rn(0, 3)                 # the number got shorter
        m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 1); m.imm32(W(F), 5); a.cmpeq(5, 1); m.bf_far(c0)
        st(m, 3, DIG)                                                                 # (its field has the cursor)
        a.deflabel(c0); a.mov_imm(wdt - 1, 0); a.sub(3, 0)                            # r0 = column
        m.imm32(W(F) + 0x24, 1); a.movl_ld(1, 2)
        a.mov_rm_rn(0, 3); a.mov_rm_rn(2, 1); a.add_imm(1, 1); a.movb_ld(1, 0); a.extu_b(0, 0); a.cmpeq(3, 0)
        a.bt_lbl(dg); a.mov_rm_rn(3, 0); a.movb_st_disp(1, 2); a.mov_imm(1, 14); a.deflabel(dg)
    m.pool_jump()
    # row 1
    m.imm32(pg.addr['dlabels'][R1], 12); rdb(m, PARAM, 8)
    rd_, rwait, rhp, rns = m.newlabel('rd'), m.newlabel('rw'), m.newlabel('rhp'), m.newlabel('rns')
    ld(m, ST, 0); a.tst_rr(0, 0); m.bf_far(rwait)
    re = m.newlabel('re'); a.mov_imm(T_ENTER, 0); a.cmpeq(0, 8); m.bf_far(re); cput("MPC TRIM MODE   "); jmpl(m, rd_)
    a.deflabel(re)
    rz = m.newlabel('rz'); a.mov_imm(T_ZONE, 0); a.cmpeq(0, 8); m.bf_far(rz); cput("ASSIGN MODE     "); jmpl(m, rd_)
    a.deflabel(rz)
    red = m.newlabel('red'); a.mov_imm(T_EDIT, 0); a.cmpeq(0, 8); m.bf_far(red)
    rhs = m.newlabel('rhs'); ld(m, SRC, 0); a.tst_rr(0, 0); m.bf_far(rhs)
    ld(m, SEL, 0); a.tst_rr(0, 0); m.bt_far(rhp); jmpl(m, rns)                       # a pad without sample: NO SAMPLE
    a.deflabel(rhs)
    ld(m, SEL, 4); a.tst_rr(4, 4); rnp = m.newlabel('rnp'); m.bt_far(rnp)             # "A05:" + 12 of the name
    a.add_imm(-1, 4); a.mov_imm(12, 5); pg._hcall(m, '__udiv')                      # r0 = bank, r1 = pad in bank
    a.mov_rm_rn(1, 11); a.mov_rm_rn(0, 2); a.add_imm(ord('A'), 2); cputr(2)          # (cputr uses r0, r1)
    a.mov_rm_rn(11, 4); a.add_imm(1, 4); a.mov_imm(10, 5); pg._hcall(m, '__udiv')
    a.mov_rm_rn(1, 11); a.mov_rm_rn(0, 2); a.add_imm(0x30, 2); cputr(2)
    a.mov_rm_rn(11, 2); a.add_imm(0x30, 2); cputr(2); cput(":")
    a.mov_imm(12, 10); rnm = m.newlabel('rnm'); jmpl(m, rnm)
    m.pool_jump()
    a.deflabel(rnp); a.mov_imm(16, 10)                                               # from the slider: the whole name
    a.deflabel(rnm)
    ld(m, SRC, 9); a.add_imm(4, 9); a.mov_imm(0, 11)                                 # r11 = 1 after the name's end
    rcl, rsp, rpt = m.newlabel('rcl'), m.newlabel('rsp'), m.newlabel('rpt')
    a.deflabel(rcl)
    a.movb_ld(9, 2); a.extu_b(2, 2); a.add_imm(1, 9)
    a.tst_rr(11, 11); a.bf_lbl(rsp)
    a.tst_rr(2, 2); a.bf_lbl(rpt); a.mov_imm(1, 11)
    a.deflabel(rsp); a.mov_imm(0x20, 2)
    a.deflabel(rpt); cputr(2)
    a.dt(10); m.bf_far(rcl)
    jmpl(m, rd_)
    m.pool_jump()
    a.deflabel(red)
    ld(m, SEL, 0); a.tst_rr(0, 0); m.bt_far(rhp)
    ld(m, SRC, 0); a.tst_rr(0, 0); m.bt_far(rns)
    rt, rl, ru = m.newlabel('rt'), m.newlabel('rl'), m.newlabel('ru')
    for p, l in ((T_ST, rt), (T_END, rt), (T_LOOP, rl), (T_TUNE, ru)):
        a.mov_imm(p, 0); a.cmpeq(0, 8); m.bt_far(l)
    cput(" " * 16); jmpl(m, rd_)
    m.pool_jump()
    a.deflabel(rt); cput("            S:  "); jmpl(m, rd_)
    a.deflabel(rl); cput("Loop:           "); jmpl(m, rd_)
    a.deflabel(ru); cput("Fine:           "); jmpl(m, rd_)
    m.pool_jump()
    a.deflabel(rwait); cput("MUTE GROUP      "); jmpl(m, rd_)
    a.deflabel(rhp); cput("HIT A PAD       "); jmpl(m, rd_)
    a.deflabel(rns); cput("NO SAMPLE       ")
    a.deflabel(rd_)
    a.tst_rr(14, 14); px = m.newlabel('px'); m.bt_far(px); redraw(m)
    a.deflabel(px); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_extract: r9 = blank pad. Extract START..END of the source into a new sample on that pad ----
    a.deflabel('tr_extract'); a.stsl_pr()
    st(m, 9, DSTP); sti(m, ST_TRIM, ST); sti(m, 1, SHOWMSG)
    ld(m, SRC, 8); a.movl_ld_disp(8, 0x20, 0); a.movl_ld_disp(8, 0x24, 1); a.cmpgt(0, 1); xok = m.newlabel('xok'); m.bt_far(xok)
    pg.emit_message(m, "BAD START/END"); xr = m.newlabel('xr'); jmpl(m, xr)              # never Extract with end <= start
    m.pool_jump()
    a.deflabel(xok)
    m.imm32(MGR, 1); a.movl_ld_disp(1, 8, 0); st(m, 0, PREVCUR)                       # (st uses r1)
    m.imm32(MGR, 1); a.movl_st_disp(8, 8, 1)                                          # current = the source
    m.os_extract(8)
    a.tst_rr(0, 0); ok = m.newlabel('ok'); m.bt_far(ok)
    ld(m, PREVCUR, 0); m.imm32(MGR, 1); a.movl_st_disp(0, 8, 1); pg.emit_message(m, "MEMORY FULL"); jmpl(m, xr)
    m.pool_jump()
    a.deflabel(ok)
    m.imm32(MGR, 1); a.movl_ld_disp(1, 8, 10)                                         # r10 = the new sample
    ld(m, PREVCUR, 0); m.imm32(MGR, 1); a.movl_st_disp(0, 8, 1)                       # current sample as it was
    ld(m, DSTP, 9); padblk(m, 9, 11)
    a.mov_rm_rn(10, 2); a.add_imm(4, 2); a.mov_rm_rn(11, 12); a.mov_imm(16, 3)
    cp = m.newlabel('cpn'); a.deflabel(cp); a.movb_ldinc(2, 0); a.movb_st(0, 12); a.add_imm(1, 12); a.dt(3); a.bf_lbl(cp)
    a.mov_rm_rn(11, 12); a.add_imm(0x10, 12); a.mov_imm(0, 0); a.movb_st_disp(0, 12)
    a.mov_imm(100, 0); a.movb_st_disp(1, 12); a.mov_imm(0, 0); a.movb_st_disp(2, 12); a.mov_imm(0x7F, 0); a.movb_st_disp(3, 12)
    a.mov_imm(0, 0); a.movb_st_disp(4, 12); a.movb_st_disp(5, 12); a.movb_st_disp(6, 12); a.movb_st_disp(7, 12)
    rdb(m, MG, 0); m.imm32(0x63, 1); a.add_rr(11, 1); a.movb_st(0, 1)               # pad +0x63 = mute group
    refocus(m)
    m.imm32(pg.addr['msgt'], 12)
    for ch in "EXTRACTED > ": a.mov_imm(ord(ch), 0); a.movb_st(0, 12); a.add_imm(1, 12)
    ld(m, DSTP, 4); a.mov_imm(12, 5); pg._hcall(m, '__udiv')
    a.add_imm(ord('A'), 0); a.movb_st(0, 12); a.add_imm(1, 12); a.add_imm(1, 1); a.mov_rm_rn(1, 4); a.mov_imm(10, 5)
    pg._hcall(m, '__udiv')
    a.add_imm(0x30, 0); a.movb_st(0, 12); a.add_imm(1, 12); a.add_imm(0x30, 1); a.mov_rm_rn(1, 0); a.movb_st(0, 12)
    a.add_imm(1, 12); a.mov_imm(0, 0); a.movb_st(0, 12)
    pg._hcall(m, '__showmsg')
    a.deflabel(xr); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- tr_edit: DO IT on the Edit page: the MPC's own function on the source (see the header) ----
    a.deflabel('tr_edit'); a.stsl_pr()
    ex = m.newlabel('ex')
    eid = m.newlabel('eid')                                                           # Normalize / Reverse going on:
    ld(m, EST, 0); a.tst_rr(0, 0); m.bt_far(eid)
    a.mov_imm(ES_PREV, 1); a.cmpeq(1, 0); m.bf_far(ex)                                # working: DO IT waits
    call(m, 'tr_ekeep'); jmpl(m, ex)                                                  # preview: DO IT keeps
    m.pool_jump()
    a.deflabel(eid)
    ld(m, SRC, 8); a.tst_rr(8, 8); eh = m.newlabel('eh'); m.bf_far(eh)
    pg.emit_message(m, "HIT A PAD FIRST"); jmpl(m, ex)
    m.pool_jump()
    a.deflabel(eh)
    rdb(m, EDITF, 9)
    # a sample-pack sample (s16 +0x3E > 0): Copy / Delete only (the MPC's list for it)
    epk = m.newlabel('epk'); eok = m.newlabel('eok')
    m.imm32(0x3E, 0); a.add_rr(8, 0); a.movw_ld(0, 10); a.cmppl(10); a.movt(10)    # r10 = 1: pack sample
    a.tst_rr(10, 10); m.bt_far(eok)
    a.mov_imm(E_COPY, 0); a.cmpeq(0, 9); m.bt_far(eok); a.mov_imm(E_DELETE, 0); a.cmpeq(0, 9); m.bt_far(eok)
    pg.emit_message(m, "COPY/DELETE ONLY"); jmpl(m, ex)
    m.pool_jump()
    a.deflabel(eok)
    # Discard / Delete: the first DO IT asks
    ecf = m.newlabel('ecf'); ego = m.newlabel('ego')
    for E, txt in ((E_DISCARD, "DO IT: TRUNCATE?"), (E_DELETE, "DO IT: DELETE?")):
        n = m.newlabel('en'); a.mov_imm(E, 0); a.cmpeq(0, 9); m.bf_far(n)
        ld(m, CONF, 0); a.mov_rm_rn(9, 1); a.add_imm(1, 1); a.cmpeq(1, 0); m.bt_far(ego)
        a.mov_rm_rn(9, 0); a.add_imm(1, 0); st(m, 0, CONF)
        pg.emit_message(m, txt); m.imm32(0x7FFFFFFF, 0); st(m, 0, SHOWMSG); jmpl(m, ex)
        a.deflabel(n); m.pool_jump()
    a.deflabel(ego); sti(m, 0, CONF); sti(m, 1, SHOWMSG)
    ld(m, SRC, 8); m.imm32(MGR, 1); a.movl_st_disp(8, 8, 1)                          # the MPC's current sample = ours
    # Extract / Copy: os_extract, then the MPC's Assign page; Stretch: the MPC's Time stretch page
    ecp, est, eos, epush = m.newlabel('ecp'), m.newlabel('est'), m.newlabel('eos'), m.newlabel('epush')
    a.mov_imm(E_COPY, 0); a.cmpeq(0, 9); m.bt_far(ecp)
    a.mov_imm(E_STRETCH, 0); a.cmpeq(0, 9); m.bt_far(est)
    enr = m.newlabel('enr'); eno = m.newlabel('eno')
    a.mov_imm(E_NORM, 0); a.cmpeq(0, 9); m.bt_far(enr); a.mov_imm(E_REV, 0); a.cmpeq(0, 9); m.bf_far(eno)
    a.deflabel(enr); a.mov_rm_rn(9, 4); call(m, 'tr_estart'); jmpl(m, ex)
    m.pool_jump()
    a.deflabel(eno)
    a.mov_imm(E_EXTRACT, 0); a.cmpeq(0, 9); m.bf_far(eos)
    a.movl_ld_disp(8, 0x20, 0); a.movl_ld_disp(8, 0x24, 1); a.cmpgt(0, 1); exo = m.newlabel('exo'); m.bt_far(exo)
    pg.emit_message(m, "BAD START/END"); jmpl(m, ex)                                  # never Extract with end <= start
    m.pool_jump()
    a.deflabel(exo); m.os_extract(8); jmpl(m, ecf)
    a.deflabel(ecp); m.os_extract(8, COPY_CMD)
    a.deflabel(ecf)
    a.tst_rr(0, 0); eok2 = m.newlabel('eok2'); m.bt_far(eok2)
    pg.emit_message(m, "MEMORY FULL"); jmpl(m, ex)
    m.pool_jump()
    a.deflabel(eok2); m.imm32(PG_ASSIGN, 8); jmpl(m, epush)
    a.deflabel(est); m.imm32(PG_STRETCH, 8)
    a.deflabel(epush)
    a.movl_ld_disp(8, 0x1C, 0); m.imm32(VT_PAGE, 1); a.cmpeq(1, 0); enp = m.newlabel('enp'); m.bf_far(enp)
    m.imm32(EVQ, 4); a.mov_rm_rn(8, 5); a.mov_imm(0, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop(); jmpl(m, ex)
    a.deflabel(enp); pg.emit_message(m, "NOT AVAILABLE"); jmpl(m, ex)
    m.pool_jump()
    # Discard / Normalize / Reverse / Delete: the MPC's Trim Edit action itself
    a.deflabel(eos)
    a.mov_imm(0, 11)
    for E, num in OS_EDITNUM.items():
        n = m.newlabel('eon'); a.mov_imm(E, 0); a.cmpeq(0, 9); a.bf_lbl(n); a.mov_imm(num, 11); a.deflabel(n)
    a.tst_rr(10, 10); epn = m.newlabel('epn'); a.bt_lbl(epn); a.add_imm(-5, 11)       # pack sample: its list (Delete = 2)
    a.deflabel(epn)
    rdb(m, OS_EDITSEL, 0); st(m, 0, ESAVE); wrb(m, 11, OS_EDITSEL)
    osc(m, OS_EDIT)
    ld(m, ESAVE, 0); wrb(m, 0, OS_EDITSEL)
    edl = m.newlabel('edl'); edm = m.newlabel('edm')                                  # HW: say what was done
    a.mov_imm(E_DELETE, 0); a.cmpeq(0, 9); m.bt_far(edl)
    pg.emit_message(m, "TRUNCATED"); jmpl(m, edm)
    a.deflabel(edl); pg.emit_message(m, "DELETED")
    a.deflabel(edm); m.pool_jump()
    # the sample may have changed or gone: the pad's sample again, else the MPC's current one
    ld(m, SEL, 0); a.tst_rr(0, 0); eap = m.newlabel('eap'); m.bt_far(eap); st(m, 0, PEND); jmpl(m, ex)
    a.deflabel(eap)
    sti(m, 0, SRC); m.imm32(MGR, 1); a.movl_ld_disp(1, 8, 5); a.tst_rr(5, 5); m.bt_far(ex)
    a.mov_imm(-1, 4); call(m, 'tr_slist'); a.cmppz(2); m.bf_far(ex)                  # still a sound in the list?
    m.imm32(MGR, 1); a.movl_ld_disp(1, 8, 4); call(m, 'tr_adopt')
    a.deflabel(ex); redraw(m); sti(m, 0xFF, LKT)                                     # samples may have come / gone
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- Normalize / Reverse (2026-11-07): backup Copy -> chunks per fire with % -> preview (DO IT keeps, CANCEL undoes) ----
    def emsg(m, txt, keep=True):
        pg.emit_message(m, txt)
        if keep: m.imm32(0x7FFFFFFF, 0); st(m, 0, SHOWMSG)
    # tr_estart: r4 = E_NORM / E_REV; MGR+8 = SRC already
    a.deflabel('tr_estart'); a.stsl_pr()
    st(m, 4, EOP); ld(m, SRC, 8)
    m.os_extract(8, COPY_CMD)
    a.tst_rr(0, 0); esk = m.newlabel('esk'); m.bt_far(esk)
    m.imm32(MGR, 1); a.movl_st_disp(8, 8, 1); emsg(m, "MEMORY FULL", keep=False)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    a.deflabel(esk)
    m.imm32(MGR, 1); a.movl_ld_disp(1, 8, 0); st(m, 0, BAK); m.imm32(MGR, 1); a.movl_st_disp(8, 8, 1)
    for s in (EPH, ECUR, EDONE, EPEAK): sti(m, 0, s)
    a.movl_ld_disp(8, 0x18, 2); a.movl_ld_disp(8, 0x1C, 3); a.tst_rr(3, 3); a.movt(3); a.mov_imm(2, 0); a.sub(3, 0)   # r0 = channels
    ld(m, EOP, 3); a.mov_imm(E_REV, 1); a.cmpeq(1, 3); erv = m.newlabel('erv'); etd = m.newlabel('etd'); a.bt_lbl(erv)
    a.shll(2); a.bra_lbl(etd); a.nop()                                                # Normalize: 2 passes
    a.deflabel(erv); a.shlr(2)                                                        # Reverse: half the frames
    a.deflabel(etd); a.mul_l(0, 2); a.sts_macl(2)
    a.tst_rr(2, 2); etz = m.newlabel('etz'); a.bf_lbl(etz); a.mov_imm(1, 2); a.deflabel(etz); st(m, 2, ETOT)
    sti(m, ES_RUN, EST); sti(m, 0xFF, EPCT); call(m, 'tr_epct'); call(m, 'icinv')
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # tr_epct: "NORMALIZE 45%" / "REVERSE 45%" / "UNDO 45%" when the % changed
    a.deflabel('tr_epct'); a.stsl_pr()
    epx = m.newlabel('epx')
    ld(m, EDONE, 4); a.mov_imm(100, 0); a.mul_l(0, 4); a.sts_macl(4); ld(m, ETOT, 5); pg._hcall(m, '__udiv')
    a.mov_imm(99, 1); ep9 = m.newlabel('ep9'); a.cmphi(1, 0); a.bf_lbl(ep9); a.mov_rm_rn(1, 0); a.deflabel(ep9)
    ld(m, EPCT, 2); a.cmpeq(2, 0); m.bt_far(epx); st(m, 0, EPCT)
    eu, en = m.newlabel('eu'), m.newlabel('en')
    ld(m, EST, 0); a.mov_imm(ES_UNDO, 1); a.cmpeq(1, 0); m.bt_far(eu)
    ld(m, EOP, 0); a.mov_imm(E_REV, 1); a.cmpeq(1, 0); m.bf_far(en)
    pg.emit_message_dec(m, "REVERSE ", EPCT, "%"); jmpl(m, epx + '_k')
    m.pool_jump()
    a.deflabel(en); pg.emit_message_dec(m, "NORMALIZE ", EPCT, "%"); jmpl(m, epx + '_k')
    a.deflabel(eu); pg.emit_message_dec(m, "UNDO ", EPCT, "%")
    a.deflabel(epx + '_k'); m.imm32(0x7FFFFFFF, 0); st(m, 0, SHOWMSG)
    a.deflabel(epx); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # tr_estep: one chunk (RUN or UNDO), every fire, page open or not
    a.deflabel('tr_estep'); a.stsl_pr()
    eret, eund = m.newlabel('eret'), m.newlabel('eund')
    ld(m, EST, 0); a.mov_imm(ES_UNDO, 1); a.cmpeq(1, 0); m.bt_far(eund)
    a.mov_imm(ES_RUN, 1); a.cmpeq(1, 0); m.bf_far(eret)
    ld(m, SRC, 8); a.movl_ld_disp(8, 0x18, 10); a.movl_ld_disp(8, 0x1C, 11)          # r10 = frames, r11 = R offset (0 mono)
    elp, enx, efin, erev, edo, esc = (m.newlabel(s) for s in ('elp', 'enx', 'efin', 'erev', 'edo', 'esc'))
    a.deflabel(elp)
    ld(m, EPH, 9); ld(m, EOP, 0); a.mov_imm(E_REV, 1); a.cmpeq(1, 0); m.bt_far(erev)
    a.mov_imm(4, 0); a.cmpge(0, 9); m.bt_far(efin)                                    # Normalize: peak L, R, scale L, R
    a.mov_rm_rn(9, 7); a.mov_imm(1, 0); a.and_rr(0, 7); a.mov_rm_rn(10, 6); jmpl(m, edo)   # r7 = channel, r6 = N
    a.deflabel(erev)
    a.mov_imm(2, 0); a.cmpge(0, 9); m.bt_far(efin)                                    # Reverse: L, R
    a.mov_rm_rn(9, 7); a.mov_rm_rn(10, 6); a.shlr(6)
    m.pool_jump()
    a.deflabel(edo)
    a.tst_rr(7, 7); ech = m.newlabel('ech'); a.bt_lbl(ech); a.tst_rr(11, 11); m.bt_far(enx)   # mono: no R
    a.deflabel(ech)
    a.mov_rm_rn(8, 12); m.imm32(0x100, 0); a.add_rr(0, 12)                           # r12 = channel start
    a.tst_rr(7, 7); ecl = m.newlabel('ecl'); a.bt_lbl(ecl); a.mov_rm_rn(11, 0); a.shll(0); a.add_rr(0, 12); a.deflabel(ecl)
    ld(m, ECUR, 13); a.mov_rm_rn(6, 5); a.sub(13, 5)                                  # r5 = left in this phase
    a.cmppl(5); m.bf_far(enx)
    m.imm32(ECHUNK, 0); a.cmpgt(0, 5); ecc = m.newlabel('ecc'); a.bf_lbl(ecc); a.mov_rm_rn(0, 5); a.deflabel(ecc)
    a.mov_rm_rn(5, 14)                                                                # r14 = n
    a.mov_rm_rn(13, 4); a.shll(4); a.add_rr(12, 4)                                    # r4 = channel + 2 * done
    ld(m, EOP, 0); a.mov_imm(E_REV, 1); a.cmpeq(1, 0); m.bt_far(esc + '_r')
    a.mov_imm(2, 0); a.cmpge(0, 9); m.bt_far(esc)
    ld(m, EPEAK, 6); cached_call(m, 'tr_kpeak'); st(m, 6, EPEAK); jmpl(m, esc + '_d')
    m.pool_jump()
    a.deflabel(esc); ld(m, EGAIN, 6); cached_call(m, 'tr_kscale'); jmpl(m, esc + '_d')
    a.deflabel(esc + '_r')
    a.mov_rm_rn(10, 5); a.add_imm(-1, 5); a.sub(13, 5); a.shll(5); a.add_rr(12, 5)     # r5 = channel + 2 * (N - 1 - done)
    a.mov_rm_rn(14, 6); cached_call(m, 'tr_krev')
    a.deflabel(esc + '_d')
    ld(m, ECUR, 0); a.add_rr(14, 0); st(m, 0, ECUR); ld(m, EDONE, 0); a.add_rr(14, 0); st(m, 0, EDONE)
    call(m, 'tr_epct'); jmpl(m, eret)
    m.pool_jump()
    a.deflabel(enx)                                                                   # next phase
    sti(m, 0, ECUR); ld(m, EPH, 0); a.add_imm(1, 0); st(m, 0, EPH)                  # (sti uses r0)
    ld(m, EOP, 1); a.mov_imm(E_NORM, 2); a.cmpeq(2, 1); m.bf_far(elp)
    a.mov_imm(2, 1); a.cmpeq(1, 0); m.bf_far(elp)                                     # peaks done: the gain (Q12)
    ld(m, EPEAK, 5); m.imm32(4096, 0); a.tst_rr(5, 5); egz = m.newlabel('egz'); a.bt_lbl(egz)
    m.imm32(32767 << 12, 4); pg._hcall(m, '__udiv')
    a.deflabel(egz); st(m, 0, EGAIN); jmpl(m, elp)
    m.pool_jump()
    a.deflabel(efin)                                                                  # done: preview
    ld(m, EOP, 0); a.mov_imm(E_REV, 1); a.cmpeq(1, 0); epv = m.newlabel('epv'); m.bf_far(epv)
    a.mov_rm_rn(10, 4); a.add_imm(-1, 4)                                              # points mirrored
    a.movl_ld_disp(8, 0x20, 0); a.movl_ld_disp(8, 0x24, 1); a.mov_rm_rn(4, 2); a.sub(1, 2); a.movl_st_disp(2, 0x20, 8)
    a.mov_rm_rn(4, 2); a.sub(0, 2); a.movl_st_disp(2, 0x24, 8)
    a.movl_ld_disp(8, 0x28, 0); a.mov_rm_rn(4, 2); a.sub(0, 2); a.movl_st_disp(2, 0x28, 8)
    call(m, 'tr_loopfix')
    a.deflabel(epv)
    sti(m, ES_PREV, EST); call(m, 'tr_envelope'); call(m, 'tr_analyze'); call(m, 'tr_sync'); sti(m, 0xFF, LKT)
    emsg(m, "DOIT=KEEP CANCEL"); jmpl(m, eret)
    m.pool_jump()
    # UNDO: the backup's header + audio back into the sample, then the backup goes
    a.deflabel(eund)
    ld(m, ECUR, 13); ld(m, ETOT, 5); a.sub(13, 5); a.cmppl(5); eud = m.newlabel('eud'); m.bf_far(eud)
    m.imm32(ECHUNK, 0); a.cmpgt(0, 5); euc = m.newlabel('euc'); a.bf_lbl(euc); a.mov_rm_rn(0, 5); a.deflabel(euc)
    a.mov_rm_rn(5, 14); a.mov_rm_rn(5, 6); a.mov_rm_rn(13, 0); a.shll2(0); a.add_imm(0x18, 0)
    ld(m, SRC, 4); a.add_rr(0, 4); ld(m, BAK, 5); a.add_rr(0, 5); cached_call(m, 'tr_kcopy')
    ld(m, ECUR, 0); a.add_rr(14, 0); st(m, 0, ECUR); st(m, 0, EDONE)
    call(m, 'tr_epct'); jmpl(m, eret)
    m.pool_jump()
    a.deflabel(eud)
    call(m, 'tr_edrop'); ld(m, SRC, 8); call(m, 'tr_loopfix')
    call(m, 'tr_envelope'); call(m, 'tr_analyze'); call(m, 'tr_sync'); sti(m, 0xFF, LKT)
    emsg(m, "UNDONE", keep=False)
    a.deflabel(eret); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # tr_edrop: delete the backup (the last sample: the source does not move), source current again, idle
    a.deflabel('tr_edrop'); a.stsl_pr()
    ld(m, BAK, 0); m.imm32(MGR, 1); a.movl_st_disp(0, 8, 1); m.imm32(MGR, 4); osc(m, DELETE_CMD)
    ld(m, SRC, 0); m.imm32(MGR, 1); a.movl_st_disp(0, 8, 1)
    sti(m, 0, BAK); sti(m, ES_IDLE, EST)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # tr_ekeep: preview kept
    a.deflabel('tr_ekeep'); a.stsl_pr()
    call(m, 'tr_edrop')
    ekr = m.newlabel('ekr'); ekd = m.newlabel('ekd')
    ld(m, EOP, 0); a.mov_imm(E_REV, 1); a.cmpeq(1, 0); m.bt_far(ekr)
    emsg(m, "NORMALIZED", keep=False); jmpl(m, ekd)
    a.deflabel(ekr); emsg(m, "REVERSED", keep=False)
    a.deflabel(ekd); a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # tr_eundo: CANCEL while working or in the preview -> restore from the backup
    a.deflabel('tr_eundo'); a.stsl_pr()
    sti(m, ES_UNDO, EST); sti(m, 0, ECUR); sti(m, 0, EDONE); sti(m, 0xFF, EPCT)
    ld(m, SRC, 0); a.movl_ld(0, 2); ld(m, BAK, 0); a.movl_ld(0, 3)                   # block sizes: the smaller
    a.cmphi(2, 3); eus = m.newlabel('eus'); a.bt_lbl(eus); a.mov_rm_rn(3, 2); a.deflabel(eus)
    a.add_imm(-0x18, 2); a.shlr2(2); st(m, 2, ETOT); call(m, 'tr_epct'); call(m, 'icinv')
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()

    # ---- icinv: invalidate the cache lines of our kernels (Sample FX / Trans-Chop) ----
    a.deflabel('icinv')
    a.movl_pool('__cstart', 4); m.imm32(0xFFFFFFF0, 1); a.and_rr(1, 4)
    m.imm32(CACHE_SPAN // 16 + 1, 5); m.imm32(0xF0000008, 6); m.imm32(0x1FFFFC00, 7); m.imm32(0xFF0, 3)
    icl = m.newlabel('icl'); a.deflabel(icl)
    a.mov_rm_rn(4, 0); a.and_rr(3, 0); a.or_rr(6, 0); a.mov_rm_rn(4, 1); a.and_rr(7, 1); a.movl_st(1, 0)
    a.add_imm(16, 4); a.dt(5); a.bf_lbl(icl)
    a.rts(); a.nop()
    m.pool_jump()

    # =========================== kernels from the cached alias (Trans-Chop env / pick) ===========================
    a.deflabel('__cstart')
    def absr0():
        l = m.newlabel('ab'); a.cmppz(0); a.bt_lbl(l); a.neg(0, 0); a.deflabel(l)
    # ---- tr_env: ENV[b] = max |x| in block b (L), MAXE = max(ENV) ----
    a.deflabel('tr_env'); a.stsl_pr()
    ld(m, SRC, 8); ld(m, S0, 10); a.shll(10); a.add_rr(8, 10); m.imm32(0x100, 1); a.add_rr(1, 10)
    ld(m, NN, 11); ld(m, HOPSH, 4); a.mov_imm(1, 12); a.shad(4, 12)
    work(m, 13); a.mov_imm(0, 14)
    bl = m.newlabel('bl'); bx = m.newlabel('bx'); a.deflabel(bl)
    a.cmppl(11); m.bf_far(bx)
    a.mov_rm_rn(12, 9); a.cmpgt(11, 9); c = m.newlabel('c'); a.bf_lbl(c); a.mov_rm_rn(11, 9); a.deflabel(c)
    a.sub(9, 11); a.mov_imm(0, 7)
    il = m.newlabel('il'); a.deflabel(il)
    a.movw_ld(10, 0); a.add_imm(2, 10); absr0(); a.cmpgt(7, 0); p = m.newlabel('p'); a.bf_lbl(p); a.mov_rm_rn(0, 7); a.deflabel(p)
    a.dt(9); a.bf_lbl(il)
    a.movw_st(7, 13); a.add_imm(2, 13)
    a.cmpgt(14, 7); q = m.newlabel('q'); a.bf_lbl(q); a.mov_rm_rn(7, 14); a.deflabel(q)
    a.bra_lbl(bl); a.nop()
    a.deflabel(bx); st(m, 14, MAXE)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- tr_pick: transients -> POS[1..NT) (POS[0] = S0 already) ----
    # v2 (2026-11-06, user: "jump to the start of a drum hit that has space at the start" + "move through transients on
    # a longer music sample"): see ref_analyze
    a.deflabel('tr_pick'); a.stsl_pr()
    ld(m, SENS, 3); m.imm32(100, 2); a.sub(3, 2)                                          # r2 = 100 - S
    a.mov_rm_rn(2, 0); a.add_rr(2, 0); a.add_rr(2, 0); a.add_imm(20, 0); st(m, 0, RQ)    # RQ = 3 (100 - S) + 20
    ld(m, MAXE, 4); a.mul_l(2, 4); a.sts_macl(4); m.imm32(200, 5); pg._hcall(m, '__udiv'); st(m, 0, DTH)
    ld(m, MAXE, 0); a.shlr2(0); a.shlr2(0); a.shlr(0); st(m, 0, GATE)                    # GATE = MAXE / 32
    ld(m, SRC, 8); m.imm32(0x94, 2); a.add_rr(8, 2); a.movl_ld(2, 4)                     # rate
    m.imm32(GAP_MS, 2); a.mul_l(2, 4); a.sts_macl(4); m.imm32(1000, 5); pg._hcall(m, '__udiv')
    ld(m, HOPSH, 1); a.neg(1, 1); a.shad(1, 0); a.tst_rr(0, 0); g1 = m.newlabel('g1'); a.bf_lbl(g1); a.mov_imm(1, 0); a.deflabel(g1); st(m, 0, MINGAP)
    a.neg(0, 0); st(m, 0, LASTK)                                                          # the first hit: anywhere
    ld(m, SRC, 8); m.imm32(0x94, 2); a.add_rr(8, 2); a.movl_ld(2, 4); m.imm32(1000 // BACK_MS, 5); pg._hcall(m, '__udiv'); st(m, 0, BACK)
    ld(m, SRC, 8); m.imm32(0x94, 2); a.add_rr(8, 2); a.movl_ld(2, 4); m.imm32(LOOK_MS, 2); a.mul_l(2, 4); a.sts_macl(4)
    m.imm32(1000, 5); pg._hcall(m, '__udiv'); ld(m, HOPSH, 1); a.neg(1, 1); a.shad(1, 0)
    a.mov_imm(2, 1); a.cmpge(1, 0); lw2 = m.newlabel('lw2'); a.bt_lbl(lw2); a.mov_imm(2, 0); a.deflabel(lw2)
    st(m, 0, LWIN)                                                                        # LW = 20 ms of blocks, >= 2
    sti(m, 1, KK)
    kl = m.newlabel('kl'); kx = m.newlabel('kx'); kn = m.newlabel('kn'); a.deflabel(kl)
    ld(m, KK, 9); ld(m, NB, 0); a.cmpge(0, 9); m.bt_far(kx)
    ld(m, NT, 0); m.imm32(MAXT, 1); a.cmpge(1, 0); m.bt_far(kx)
    ld(m, LASTK, 0); a.mov_rm_rn(9, 2); a.sub(0, 2); ld(m, MINGAP, 0); a.cmpge(0, 2); m.bf_far(kn)
    work(m, 13); a.mov_rm_rn(9, 0); a.shll(0); a.add_rr(0, 13); a.movw_ld(13, 4); a.extu_w(4, 4)   # r4 = E[k]
    # r5 = prev = max E[max(k - j, 0)], j = 1..LW (the level of the last 20 ms)
    work(m, 12); a.mov_imm(0, 5); ld(m, LWIN, 1); a.mov_rm_rn(9, 3)                      # r12 = ENV (work uses r3)
    pl = m.newlabel('pl'); a.deflabel(pl)
    a.add_imm(-1, 3); a.cmppz(3); p0 = m.newlabel('p0'); a.bt_lbl(p0); a.mov_imm(0, 3); a.deflabel(p0)
    a.mov_rm_rn(3, 0); a.shll(0); a.add_rr(12, 0); a.movw_ld(0, 6); a.extu_w(6, 6)
    a.cmpgt(5, 6); nm_ = m.newlabel('nm'); a.bf_lbl(nm_); a.mov_rm_rn(6, 5); a.deflabel(nm_)
    a.dt(1); a.bf_lbl(pl)
    a.mov_rm_rn(4, 7); a.sub(5, 7)                                                        # r7 = rise
    yes = m.newlabel('yes'); ld(m, DTH, 0); a.cmpgt(0, 7); m.bt_far(yes)                  # loud vs the whole sample
    ld(m, GATE, 0); a.cmpgt(0, 4); m.bf_far(kn)                                           # or: above the gate and
    a.mov_imm(100, 0); a.mul_l(7, 0); a.sts_macl(2); ld(m, RQ, 0); a.mul_l(5, 0); a.sts_macl(0)   # (ld uses r1)
    a.cmpgt(0, 2); m.bf_far(kn)                                                         # a big rise vs just before
    a.deflabel(yes)
    a.mov_rm_rn(4, 14); a.shlr2(4)                                                        # r14 = E[k]; t = max(E/4, prev+1)
    a.mov_rm_rn(5, 0); a.add_imm(1, 0); a.cmpgt(4, 0); t_ = m.newlabel('t'); a.bf_lbl(t_); a.mov_rm_rn(0, 4); a.deflabel(t_)
    ld(m, HOPSH, 12)
    a.mov_rm_rn(9, 3); a.add_imm(-1, 3); a.shad(12, 3)                                    # r3 = scan from (k-1) * hop
    a.mov_rm_rn(9, 6); a.add_imm(1, 6); a.shad(12, 6)
    ld(m, NN, 1); a.cmpgt(1, 6); fe = m.newlabel('fe'); a.bf_lbl(fe); a.mov_rm_rn(1, 6); a.deflabel(fe)
    a.mov_rm_rn(9, 11); a.shad(12, 11)                                                    # r11 = f (default k * hop)
    ld(m, SRC, 8); a.mov_rm_rn(3, 10); a.shll(10); a.add_rr(8, 10); m.imm32(0x100, 1); a.add_rr(1, 10)
    sl = m.newlabel('sl'); sf = m.newlabel('sf'); sxk = m.newlabel('sxk'); a.deflabel(sl)
    a.cmpge(6, 3); a.bt_lbl(sxk)
    a.movw_ld(10, 0); a.add_imm(2, 10); absr0(); a.cmpge(4, 0); a.bt_lbl(sf)
    a.add_imm(1, 3); a.bra_lbl(sl); a.nop()
    a.deflabel(sf); a.mov_rm_rn(3, 11)
    a.deflabel(sxk)
    # back from f to the onset: while |x| > floor = max(prev, E/32), not before f - BACK
    a.mov_rm_rn(14, 0); a.shlr2(0); a.shlr2(0); a.shlr(0); a.cmpgt(5, 0); fl_ = m.newlabel('fl'); a.bf_lbl(fl_); a.mov_rm_rn(0, 5); a.deflabel(fl_)
    ld(m, BACK, 0); a.mov_rm_rn(11, 6); a.sub(0, 6); a.cmppz(6); lo_ = m.newlabel('lo'); a.bt_lbl(lo_); a.mov_imm(0, 6); a.deflabel(lo_)
    a.mov_rm_rn(11, 10); a.shll(10); a.add_rr(8, 10); m.imm32(0x100, 1); a.add_rr(1, 10)
    wb = m.newlabel('wb'); wx = m.newlabel('wx'); a.deflabel(wb)
    a.cmpgt(6, 11); a.bf_lbl(wx)
    a.movw_ld(10, 0); absr0(); a.cmpgt(5, 0); a.bf_lbl(wx)
    a.add_imm(-1, 11); a.add_imm(-2, 10); a.bra_lbl(wb); a.nop()
    a.deflabel(wx)
    work(m, 10, POS_OFF); ld(m, NT, 1); a.shll2(1); a.add_rr(1, 10)
    a.mov_rm_rn(10, 1); a.add_imm(-4, 1); a.movl_ld(1, 0)
    a.mov_imm(1, 1); a.shad(12, 1); a.add_rr(1, 0); a.cmpgt(0, 11); m.bf_far(kn)
    a.movl_st(11, 10); ld(m, NT, 0); a.add_imm(1, 0); st(m, 0, NT); st(m, 9, LASTK)
    a.deflabel(kn); ld(m, KK, 9); a.add_imm(1, 9); st(m, 9, KK); jmpl(m, kl)
    m.pool_jump()
    a.deflabel(kx)
    a.ldsl_pr(); a.rts(); a.nop()
    m.pool_jump()
    # ---- Edit kernels (leaf, r0-r7 only; n > 0) ----
    # tr_kpeak: r4 = s16 *, r5 = n, r6 = max |x| so far -> r6
    a.deflabel('tr_kpeak')
    kl1, kp1, kq1 = m.newlabel('kl1'), m.newlabel('kp1'), m.newlabel('kq1')
    a.deflabel(kl1); a.movw_ld(4, 0); a.add_imm(2, 4); a.cmppz(0); a.bt_lbl(kp1); a.neg(0, 0); a.deflabel(kp1)
    a.cmpgt(6, 0); a.bf_lbl(kq1); a.mov_rm_rn(0, 6); a.deflabel(kq1); a.dt(5); a.bf_lbl(kl1)
    a.rts(); a.nop()
    # tr_kscale: r4 = s16 *, r5 = n, r6 = gain Q12 (|x| <= peak: no overflow, no clipping)
    a.deflabel('tr_kscale')
    kl2 = m.newlabel('kl2'); m.imm32(2048, 7); a.mov_imm(-12, 3)
    a.deflabel(kl2); a.movw_ld(4, 0); a.mul_l(6, 0); a.sts_macl(0); a.add_rr(7, 0); a.shad(3, 0); a.movw_st(0, 4)
    a.add_imm(2, 4); a.dt(5); a.bf_lbl(kl2)
    a.rts(); a.nop()
    # tr_krev: r4 = first, r5 = last, r6 = pairs
    a.deflabel('tr_krev')
    kl3 = m.newlabel('kl3')
    a.deflabel(kl3); a.movw_ld(4, 0); a.movw_ld(5, 1); a.movw_st(1, 4); a.movw_st(0, 5); a.add_imm(2, 4); a.add_imm(-2, 5)
    a.dt(6); a.bf_lbl(kl3)
    a.rts(); a.nop()
    # tr_kcopy: r4 = dst, r5 = src, r6 = words (32-bit)
    a.deflabel('tr_kcopy')
    kl4 = m.newlabel('kl4')
    a.deflabel(kl4); a.movl_ld_inc(5, 0); a.movl_st(0, 4); a.add_imm(4, 4); a.dt(6); a.bf_lbl(kl4)
    a.rts(); a.nop()
    m.pool_jump()
    a.deflabel('__cend')

def doit(m):
    a = m.a
    dx, ne, bad = m.newlabel('dx'), m.newlabel('ne'), m.newlabel('bad')
    rdb(m, PARAM, 9); nedit = m.newlabel('nedit'); a.mov_imm(T_EDIT, 0); a.cmpeq(0, 9); m.bf_far(nedit)
    call(m, 'tr_edit'); jmpl(m, dx)
    a.deflabel(nedit)
    rdb(m, PARAM, 9); a.mov_imm(T_ENTER, 0); a.cmpeq(0, 9); m.bf_far(ne)               # ENTER DEFAULT
    m.imm32(OSTRIM, 8)
    a.movl_ld_disp(8, 0x1C, 0); m.imm32(VT_PAGE, 1); a.cmpeq(1, 0); m.bf_far(bad)
    a.movl_ld_disp(8, 0x30, 0); m.imm32(OSTRIM_H, 1); a.cmpeq(1, 0); m.bf_far(bad)
    m.imm32(EVQ, 4); a.mov_rm_rn(8, 5); a.mov_imm(0, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop(); jmpl(m, dx)
    a.deflabel(bad); pg.emit_message(m, "NOT AVAILABLE"); jmpl(m, dx)
    m.pool_jump()
    a.deflabel(ne)
    nz, zf, zn, znx = m.newlabel('nz'), m.newlabel('zf'), m.newlabel('zn'), m.newlabel('znx')
    a.mov_imm(T_ZONE, 0); a.cmpeq(0, 9); m.bf_far(nz)                                  # ENTER ZONE ASSIGN MODE
    m.imm32(LDR_B, 1); a.movl_ld(1, 6); a.tst_rr(6, 6); m.bt_far(zn)
    m.imm32(0x100, 0); a.add_rr(0, 6); a.mov_imm(0, 5)                                # r6 = HUB slot tables, r5 = k
    w0, w1 = struct.unpack('<II', ZONE_NAME)
    zl = m.newlabel('zl'); a.deflabel(zl)
    a.mov_rm_rn(5, 4); a.shll2(4); a.add_rr(6, 4); m.imm32(0x44, 0); a.add_rr(4, 0); a.movl_ld(0, 0)
    a.mov_imm(1, 1); a.cmpeq(1, 0); m.bf_far(znx)                                     # INST[k] == 1
    a.mov_rm_rn(5, 4); a.shll2(4); a.shll(4); a.add_rr(6, 4); m.imm32(0x1C4, 0); a.add_rr(0, 4)   # NAME[k]
    a.movl_ld(4, 0); m.imm32(w0, 1); a.cmpeq(1, 0); m.bf_far(znx)
    a.movl_ld_disp(4, 4, 0); m.imm32(w1, 1); a.cmpeq(1, 0); m.bf_far(znx)
    a.mov_rm_rn(5, 4); a.shll2(4); a.add_rr(6, 4); m.imm32(0xC4, 0); a.add_rr(0, 4); a.movl_ld(4, 8)   # PAGE[k]
    m.imm32(EVQ, 4); a.mov_rm_rn(8, 5); a.mov_imm(0, 6); m.imm32(OS_PUSH_PAGE, 2); a.jsr(2); a.nop(); jmpl(m, dx)
    a.deflabel(znx); a.add_imm(1, 5); a.mov_imm(16, 0); a.cmpeq(0, 5); m.bf_far(zl)
    a.deflabel(zn); pg.emit_message(m, "ZONE NOT LOADED"); jmpl(m, dx)
    m.pool_jump()
    a.deflabel(nz)
    ld(m, SRC, 0); a.tst_rr(0, 0); hs = m.newlabel('hs'); m.bf_far(hs)
    pg.emit_message(m, "HIT A PAD FIRST"); jmpl(m, dx)
    m.pool_jump()
    a.deflabel(hs)
    m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 0); st(m, 0, FOCSAVE)
    sti(m, ST_WAIT, ST); pg.emit_message(m, "EXTRACT TO PAD"); m.imm32(0x7FFFFFFF, 0); st(m, 0, SHOWMSG)
    call(m, 'tr_vis'); call(m, 'tr_disp'); set_focus(m, W(MG))                       # the wheel picks the mute group
    a.deflabel(dx)
pg.on_doit(doit)

def ev_pad(m, hret0, hret1):
    """trimming: the pad becomes the source (the OS plays it). EXTRACT TO PAD: an empty pad gets the extracted range"""
    a = m.a
    a.movl_ld_disp(8, 4, 0); a.tst_rr(0, 0); rel = m.newlabel('rel'); m.bt_far(rel)
    a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 9); a.extu_w(9, 9); a.mov_imm(12, 0); a.cmphs(0, 9); m.bt_far(hret0)
    m.imm32(BANKOBJ, 1); a.movb_ld(1, 0); a.and_imm(3); a.mov_imm(12, 2); a.mul_l(2, 0); a.sts_macl(0); a.add_rr(0, 9)
    ld(m, EST, 0); a.tst_rr(0, 0); m.bf_far(hret0)                           # Normalize / Reverse busy: pads only play
    ld(m, ST, 0); a.mov_imm(ST_WAIT, 1); a.cmpeq(1, 0); wt = m.newlabel('wt'); m.bt_far(wt)
    a.add_imm(1, 9); st(m, 9, PEND); jmpl(m, hret0)
    m.pool_jump()
    a.deflabel(wt)
    padblk(m, 9, 11)
    a.mov_imm(0, 2)
    for off in (0, 0x18, 0x30, 0x48):
        a.mov_rm_rn(11, 1); a.add_imm(off, 1); a.movb_ld(1, 0); a.or_rr(0, 2)
    a.extu_b(2, 2); a.tst_rr(2, 2); emp = m.newlabel('emp'); m.bt_far(emp)
    pg.emit_message(m, "PAD NOT EMPTY"); m.imm32(0x7FFFFFFF, 0); st(m, 0, SHOWMSG); jmpl(m, hret1)
    m.pool_jump()
    a.deflabel(emp); call(m, 'tr_extract'); jmpl(m, hret1)
    a.deflabel(rel)
    ld(m, ST, 0); a.mov_imm(ST_WAIT, 1); a.cmpeq(1, 0); m.bt_far(hret1); jmpl(m, hret0)
    m.pool_jump()
pg.on_event(0x38, ev_pad)

def cancel(m, hret1):
    a = m.a
    lv = m.newlabel('lv'); ncq = m.newlabel('ncq'); nes = m.newlabel('nes')
    ld(m, EST, 0); a.tst_rr(0, 0); m.bt_far(nes)                             # Normalize / Reverse: CANCEL = undo
    a.mov_imm(ES_UNDO, 1); a.cmpeq(1, 0); m.bt_far(hret1); call(m, 'tr_eundo'); jmpl(m, hret1)
    m.pool_jump()
    a.deflabel(nes)
    ld(m, CONF, 0); a.tst_rr(0, 0); m.bt_far(ncq)                            # Edit asking: CANCEL = no
    sti(m, 0, CONF); pg.emit_message(m, "CANCELLED"); jmpl(m, hret1)
    m.pool_jump()
    a.deflabel(ncq)
    ld(m, ST, 0); a.tst_rr(0, 0); npg = m.newlabel('npg'); m.bf_far(npg)
    # trimming (user 2026-11-07): CANCEL = the previous Trim page; only on the first (Trim St) it leaves (edits kept)
    rdb(m, PARAM, 0); a.tst_rr(0, 0); m.bt_far(lv)
    a.add_imm(-1, 0); wrb(m, 0, PARAM); set_focus(m, W(PARAM)); call(m, 'tr_vis'); call(m, 'tr_disp'); redraw(m)
    jmpl(m, hret1)
    m.pool_jump()
    a.deflabel(npg)
    sti(m, ST_TRIM, ST); sti(m, 1, SHOWMSG); call(m, 'tr_vis'); call(m, 'tr_disp'); refocus(m)   # EXTRACT TO PAD: back
    jmpl(m, hret1)
    m.pool_jump()
    a.deflabel(lv)
pg.cancel_fn = cancel

def ev_key(m, hret0, hret1):
    """LEFT / RIGHT on Trim St / End / Loop st: the cursor moves between its digits (the wheel then moves the point by
    that digit). Past the last digit on either side, and on every other field, the keys go to the OS (next field)."""
    a = m.a
    kx, kl, kr, kd = m.newlabel('kx'), m.newlabel('kl'), m.newlabel('kr'), m.newlabel('kd')
    ld(m, ST, 0); a.tst_rr(0, 0); m.bf_far(kx)
    m.imm32(pg.addr['page'] + 0x24, 1); a.movl_ld(1, 12)                              # (r9 = event type: keep)
    a.mov_imm(-1, 11)
    for F, wdt in POSW.items():
        n = m.newlabel('kf'); m.imm32(W(F), 1); a.cmpeq(1, 12); a.bf_lbl(n)
        ld(m, F, 4); a.mov_imm(wdt, 5); call(m, 'tr_ndig'); a.mov_rm_rn(0, 11); a.deflabel(n)
    a.cmppz(11); m.bf_far(kx)                                                         # r11 = the number's last digit
    a.mov_rm_rn(8, 1); a.add_imm(2, 1); a.movw_ld(1, 10); a.extu_w(10, 10)
    a.mov_imm(K_LEFT, 0); a.cmpeq(0, 10); m.bt_far(kl)
    a.mov_imm(K_RIGHT, 0); a.cmpeq(0, 10); m.bt_far(kr)
    jmpl(m, kx)
    m.pool_jump()
    a.deflabel(kl); ld(m, DIG, 0); a.cmpeq(11, 0); m.bt_far(kx); a.add_imm(1, 0); jmpl(m, kd)   # bigger digit
    a.deflabel(kr); ld(m, DIG, 0); a.tst_rr(0, 0); m.bt_far(kx); a.add_imm(-1, 0)              # smaller digit
    a.deflabel(kd); st(m, 0, DIG); call(m, 'tr_disp'); redraw(m)
    jmpl(m, hret1)
    m.pool_jump()
    a.deflabel(kx)
pg.on_event(2, ev_key)


# =====================================================================================================================
def ref_analyze(L, sens, rate):
    """the detection model on the whole sample: [0] + transients (MAXT steps at most). v2 (2026-11-06)"""
    n = len(L)
    if n < MIN_N: return [0]
    hopsh = HOP_MIN
    while (n >> hopsh) >= MAXBLK: hopsh += 1
    hop = 1 << hopsh; NB = (n + hop - 1) >> hopsh
    E = [max(abs(x) for x in L[b * hop: min((b + 1) * hop, n)]) for b in range(NB)]
    mx = max(E); D = mx * (100 - sens) // 200; rq = 3 * (100 - sens) + 20; gate = mx >> 5
    lw = max(2, (rate * LOOK_MS // 1000) >> hopsh)
    mingap = max(1, (rate * GAP_MS // 1000) >> hopsh); back = rate // (1000 // BACK_MS)
    pos = [0]; last = -mingap
    for k in range(1, NB):
        if len(pos) >= MAXT: break
        if k - last < mingap: continue
        prev = max(E[max(k - j, 0)] for j in range(1, lw + 1))
        rise = E[k] - prev
        if not (rise > D or (E[k] > gate and rise * 100 > prev * rq)): continue
        t = max(E[k] >> 2, prev + 1); f = k * hop
        for i in range((k - 1) * hop, min((k + 1) * hop, n)):
            if abs(L[i]) >= t: f = i; break
        fl = max(prev, E[k] >> 5); lo = max(f - back, 0); p = f
        while p > lo and abs(L[p]) > fl: p -= 1
        if p > pos[-1] + hop: pos.append(p); last = k
    return pos



def selftest(out):
    from validate import Harness
    from shasm import SHAsm
    from mpcmod import HOOKS
    from pages import ENABLE_DOIT, ENABLE_CANCEL
    from testkit import PageTest, FIRMWARE
    import random
    VT_LABEL, VT_VALUE, VT_COMPOSITE = 0x88507C44, 0x88507C8C, 0x88507D1C
    LOG = 0x88E00000
    LOG2 = 0x88E00400                    # OS Edit action calls: count, choice byte, MGR+8 of the last one

    class OSTest(PageTest):
        """PageTest with the OS code image in SDRAM, so the REAL Extract / find-by-name / loop guard run (S500)"""
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
            self.stub(SET_FOCUS, lambda s: (s.movl_st_disp(5, 0x24, 4), s.rts(), s.nop()))   # page +0x24 = focus
            self.stub(OS_PUSH_PAGE, lambda s: (s.movl_pool(LOG, 1), s.movl_ld(1, 0), s.add_imm(1, 0), s.movl_st(0, 1),
                                               s.shll2(0), s.shll2(0), s.add_rr(0, 1), s.movl_st(5, 1),
                                               s.movl_st_disp(6, 4, 1), s.rts(), s.nop()))
            # the OS Trim Edit action: logged (count, choice byte, MGR+8) instead of run (its OS objects are not built)
            self.stub(OS_EDIT, lambda s: (s.movl_pool(LOG2, 1), s.movl_pool(OS_EDITSEL, 2), s.movb_ld(2, 0), s.movl_st_disp(0, 4, 1),
                                          s.movl_pool(MGR, 2), s.movl_ld_disp(2, 8, 0), s.movl_st_disp(0, 8, 1),
                                          s.movl_ld(1, 0), s.add_imm(1, 0), s.movl_st(0, 1), s.rts(), s.nop()))
            h.load_hub(); h.load_wav(wav); h.fire(10)

    import audit_static
    magic, name, code, rel, ln = audit_static.payload(out)
    assert len(code) <= SLOT_CODE_MAX - WORK_OFF, ("code + objects run into the work buffer", len(code), SLOT_CODE_MAX - WORK_OFF)
    print("  ok code + objects %d bytes, work buffer from %d" % (len(code), SLOT_CODE_MAX - WORK_OFF))
    t = OSTest(pg, out); h, e = t.h, t.e
    R32, W32 = h.ram_r32, h.ram_w32
    def s16(v): return v - 0x10000 if v & 0x8000 else v
    def s32(v): return v - (1 << 32) if v & 0x80000000 else v
    def pushes(): return [(R32(LOG + 16 * (k + 1)), R32(LOG + 16 * (k + 1) + 4)) for k in range(R32(LOG))]
    # a 2-bar break at 95 BPM: kick / snare on the beats, hats on the off-beats (stereo, R = L/2) (Trans-Chop test)
    rate = 44100; beat = int(rate * 60 / 95); n = beat * 8; rnd = random.Random(3); L = [0] * n
    def hit(at, amp, dec, freq, noise):
        for i in range(min(int(rate * 0.25), n - at)):
            x = amp * math.exp(-i / (rate * dec)) * ((1 - noise) * math.sin(2 * math.pi * freq * i / rate) + noise * rnd.uniform(-1, 1))
            L[at + i] = max(-32768, min(32767, L[at + i] + int(x)))
    for b in range(8):
        t0 = b * beat + 1000
        hit(t0, 28000, 0.08, 60, 0.1) if b % 2 == 0 else hit(t0, 22000, 0.05, 200, 0.7)
        hit(t0 + beat // 2, 6000, 0.02, 8000, 0.95)
    GUARD = 256                         # the OS keeps 256 spare frames per channel after the audio (LOOP_GUARD writes there)
    def mkblock(name, L, R=None):
        nn = len(L); stereo = R is not None; pad = [0] * GUARD
        pcm = b''.join(struct.pack('<h', v) for v in L + pad) + (b''.join(struct.pack('<h', v) for v in R + pad) if stereo else b'')
        blk = h.add_sample(pcm, name=name)
        W32(blk + 0x18, nn); W32(blk + 0x1C, nn + GUARD if stereo else 0); W32(blk + 0x20, 0); W32(blk + 0x24, nn - 1); W32(blk + 0x28, 0)
        W32(blk + 0x2C, 0); e.wr(blk + 0x15, 0, 1); W32(blk + 0x30, 0)
        h.ram_w16(blk + 0x92, 2 if stereo else 1); W32(blk + 0x94, rate); W32(blk + 0x98, 176400 if stereo else 88200)
        h.ram_w16(blk + 0x9C, 4 if stereo else 2)
        return blk
    B = mkblock(b"BREAK01", L, [x // 2 for x in L])
    SH = [int(9000 * math.sin(i * 0.05)) for i in range(600)]; B2 = mkblock(b"SHORT", SH)
    h.ram_w16(PROG_IDX, 1); PROG = PROG_BASE + PROG_STRIDE
    def lay(p, l): return PROG + PAD_OFF + p * PAD_SIZE + l * LAYER
    for p in range(48):
        for l in range(4):
            for i in range(0x18): e.wr(lay(p, l) + i, 0, 1)
    def name_pad(p, nm, l=0):
        for i, c in enumerate(nm): e.wr(lay(p, l) + i, c, 1)
    name_pad(0, b"BREAK01"); name_pad(1, b"SHORT", l=1); name_pad(2, b"GONE")
    def list_end():
        p = R32(MGR)
        while R32(p): p += R32(p)
        return p
    hdr = lambda b=B: (R32(b + 0x20), R32(b + 0x24), R32(b + 0x28))
    def slider(k, fires=60):
        lo, hi = t.slot(SLO), t.slot(SHI)                                       # the range the add-on has learned
        W32(SLIDER, lo + round(k * (hi - lo) / max(1, t.slot(NSTEP)))); t.fire(fires)
    posv = lambda: [R32(LOAD_SCRATCH - WORK_OFF + POS_OFF + 4 * i) for i in range(t.slot(NT))]
    WID = {s: pg.values[fid(s)] for s in CURSOR}
    vis = lambda s: e.rd(WID[s] + 0x17, 1) >> 7
    focus = lambda: R32(pg.page + 0x24)
    def cur(s): W32(pg.page + 0x24, WID[s]); t.fire(3)
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
                elif vt == VT_VALUE and w in VALW: sl, wd_ = VALW[w]; txt = ("%" + str(wd_) + "d") % R32(sl)
                else: txt = ''
                for i, ch in enumerate(txt[:wd]): rows[y][x + i] = ch
            w = R32(w + 8)
        return [''.join(r) for r in rows]
    def setp(p): e.wr(PARAM, p, 1); t.fire(4)
    def sounds():
        """the samples in memory, as the Edit page's slider lists them (add-on WAVs skipped)"""
        out = []; p = R32(MGR); lim = R32(MGR + 4)
        while p < lim:
            sz = R32(p)
            if sz < 0x100 or sz & 3 or p + sz > lim: break
            if R32(p + 0x100) not in (MAGIC_ADDON, MAGIC_HUB): out.append(p)
            p += sz
        return out
    shots = {}
    def shot(tag): shots[tag] = screen()

    W32(SLIDER, SL_LO); t.open(); t.fire(30)
    assert screen() == ["Trim St:        ", "HIT A PAD       "] and t.slot(SENS) == 50, screen(); shot("Trim (before a pad)")
    t.doit(); assert t.msg() == "HIT A PAD FIRST", t.msg(); t.fire(pg.msg_fires + 10)
    print("  ok page: 'Trim St:' / 'HIT A PAD'; DO IT without a pad refused")

    # --- A01: analysis of the whole sample; both points shown ---
    t.pad(0, 0); t.fire(3)
    exp = ref_analyze(L, 50, rate); cs = exp; ce = [p - 1 for p in exp[1:]] + [n - 1]
    assert posv() == exp and t.slot(NT) == len(exp) >= 8, (posv()[:6], exp[:6], [(nm_, t.slot(s_)) for nm_, s_ in (('HOPSH', HOPSH), ('LWIN', LWIN), ('GATE', GATE), ('RQ', RQ), ('BACK', BACK), ('MINGAP', MINGAP), ('DTH', DTH), ('MAXE', MAXE), ('NB', NB))])
    assert screen() == ["Trim St:       0", "            S:50"], screen(); shot("Trim St")
    assert t.slot(KT) == K_START
    print("  ok A01 BREAK01: %d steps = model; 'Trim St:       0' / '            S:50'" % (len(exp)))
    # --- cursor on the page name: the slider jumps START through the transients (no T x/y shown) ---
    slider(3); assert hdr()[0] == cs[3] and screen()[0] == "Trim St:%8d" % cs[3], (hdr(), screen())
    slider(5); assert hdr()[0] == cs[5] and hdr()[2] == cs[5], "loop point pulled up to START (was 0)"
    slider(2); assert hdr()[0] == cs[2] and hdr()[2] == cs[5], "START back: the loop point stays (still inside)"
    # page left, slider moved on another page, page entered again: the value stays until the slider catches it (user 2026-11-08)
    t.close(); W32(SLIDER, SL_HI); t.fire(2); t.open(); t.fire(60); assert hdr()[0] == cs[2], ("re-entry keeps START", hdr()[0], cs[2])
    slider(5); assert hdr()[0] == cs[2], ("above it: not caught yet", hdr()[0])
    slider(1); assert hdr()[0] == cs[1], ("passed it: caught", hdr()[0])
    slider(2); assert hdr()[0] == cs[2]
    print("  ok slider: START on transients 4, 6, 3 (loop point pulled up, then left inside); re-entry keeps it (catch-up again)")
    # --- cursor on Trim St: the wheel moves it by frames, LEFT / RIGHT pick the digit ---
    from testkit import EV_KEY
    key = lambda k: t.event(EV_KEY, k)
    col = lambda F: e.rd(R32(WID[F] + 0x24) + 1, 1)
    cur(STF); assert t.slot(KT) == K_START and t.slot(DIG) == 0 and col(STF) == 7
    p0 = hdr()[0]
    W32(STF, p0 + 1); t.fire(3); assert hdr()[0] == p0 + 1, (hdr()[0], p0)
    W32(STF, p0 - 1); t.fire(3); assert hdr()[0] == p0 - 1, "two clicks down"
    assert key(K_RIGHT) == 0, "last digit: RIGHT goes to the OS"
    for _ in range(3): assert key(K_LEFT) == 1
    t.fire(1); assert t.slot(DIG) == 3 and col(STF) == 4, (t.slot(DIG), col(STF))
    p1 = hdr()[0]; W32(STF, p1 + 1); t.fire(3); assert hdr()[0] == p1 + 1000, (hdr()[0], p1)
    W32(STF, p1 + 1000 - 2); t.fire(3); assert hdr()[0] == p1 - 1000, "two clicks down = -2000"
    W32(STF, p1 - 1000 + 1000); t.fire(3); assert hdr()[0] == p1, "already a multiple of 1000 (OS stepping): as it is"
    assert hdr()[0] == p1 and len(str(p1)) == 5, p1
    key(K_LEFT); t.fire(1); assert t.slot(DIG) == 4 and col(STF) == 3, (t.slot(DIG), col(STF))
    assert key(K_LEFT) == 0, "first digit of the number (5 digits, no blanks): LEFT goes to the OS"
    while hdr()[0] >= 10000: W32(STF, hdr()[0] - 1); t.fire(3)                     # -10000 per click
    assert hdr()[0] < 10000 and t.slot(DIG) == 3 and col(STF) == 4, \
        ("4 digits now: the cursor comes onto the first of them", hdr()[0], t.slot(DIG))
    cur(PARAM); cur(STF); assert t.slot(DIG) == 0, "another field and back: the ones again"
    print("    ok cursor on the digits the number has: 5 digits -> LEFT stops at the 5th; 4 digits -> back on the 4th")
    W32(STF, cs[2]); t.fire(3); assert hdr()[0] == cs[2], "typed back on transient 3"
    shot("Trim St (cursor on the number)")
    print("  ok wheel on Trim St: +1 / -1 frame; LEFT x3 -> +1000 / -2000 frames; ends go to the OS; slider = transients")
    # --- End ---
    setp(T_END); assert t.slot(KT) == K_END and screen() == ["End:      %6d" % hdr()[1], "            S:50"], screen()
    shot("End")
    cur(ENDF); assert t.slot(KT) == K_END and col(ENDF) == 7
    slider(t.slot(NSTEP)); slider(6); assert hdr()[1] == ce[6] == cs[7] - 1, (hdr(), ce[6])
    e1 = hdr()[1]; W32(ENDF, e1 - 1); t.fire(3); assert hdr()[1] == e1 - 1, "wheel: one frame"
    key(K_LEFT); key(K_LEFT); t.fire(1); W32(ENDF, e1 - 1 + 1); t.fire(3); assert hdr()[1] == e1 - 1 + 100, hdr()
    slider(6); slider(1); assert hdr()[1] == cs[2] + 1, "END never before START + 1"
    slider(5); assert hdr()[1] == ce[5]
    cur(PARAM); assert t.slot(KT) == K_END, "End page: the slider moves the end"
    print("  ok End page: slider before transient 8, wheel -1 / +100 frames, clamped at START + 1")

    # --- S: (sensitivity) on the same page ---
    cur(SENS); assert t.slot(KT) == K_END and col(SENS) == 1, "S: the slider still moves the page's point (End)"
    W32(SENS, 90); t.fire(DEBOUNCE + 3); exp90 = ref_analyze(L, 90, rate)
    assert posv() == exp90 and len(exp90) >= len(exp) and screen()[1][12:] == "S:90", (len(posv()), len(exp90), screen())
    key(K_LEFT); t.fire(1); assert t.slot(DIG) == 1 and col(SENS) == 0
    W32(SENS, 91); t.fire(3); assert t.slot(SENS) == 99, ("tens digit: 90 + 10 -> 99 (top)", t.slot(SENS))
    W32(SENS, 98); t.fire(3); assert t.slot(SENS) == 89, t.slot(SENS)
    key(K_RIGHT); t.fire(1); W32(SENS, 50); t.fire(DEBOUNCE + 3); assert t.slot(SENS) == 50 and posv() == exp
    slider(5); slider(3); assert t.slot(SENS) == 50 and hdr()[1] == ce[3], ("the slider moves End, not S:", hdr())
    slider(t.slot(CUR)); cur(PARAM)
    print("  ok S: on the Trim St / End pages: wheel 90 (%d steps), tens digit 99 / 89, back to 50 (%d); the slider stays on the points"
          % (len(exp90), len(exp)))

    # --- Loop st / Loop ---
    setp(T_LOOP); st_, en_ = hdr()[:2]
    assert screen() == ["Loop st:%8d" % hdr()[2], "Loop:OFF        "], screen(); shot("Loop st")
    assert t.slot(KT) == K_LOOPST
    N = t.slot(NSTEP); q = t.slot(LQ); assert N == min(LOOP_STEPS, en_ - st_) and q == (en_ - st_) // N, (N, q)
    W32(SLIDER, 0); t.fire(60); slider(N // 2); assert hdr()[2] == st_ + t.slot(CUR) * q and abs(t.slot(CUR) - N // 2) <= 10, (hdr(), t.slot(CUR))
    slider(N); assert hdr()[2] <= en_ and hdr()[2] >= en_ - q, "top = END"
    cur(LOOPF); lp0 = hdr()[2]; W32(LOOPF, lp0 - 1); t.fire(3); assert hdr()[2] == lp0 - 1, "wheel: one frame"
    cur(PARAM)
    cur(LOOPON); assert t.slot(KT) == K_LOOPON
    slider(0); slider(1); assert R32(B + 0x2C) == 1 and screen()[1] == "Loop:ON         ", screen()
    e.wr(LOOPON, 0, 1); t.fire(3); assert R32(B + 0x2C) == 0
    e.wr(LOOPON, 1, 1); t.fire(3); assert R32(B + 0x2C) == 1
    cur(PARAM)
    print("  ok Loop st: slider START..END in single steps (up to 250), wheel by frames; Loop OFF/ON by slider and wheel (+0x2C)")
    # --- St link ---
    setp(T_LINK); assert screen()[0] == "St link:OFF     ", screen()
    e.wr(LINKF, 1, 1); t.fire(3); assert e.rd(B + 0x15, 1) == 1 and hdr()[2] == hdr()[0], hdr()
    shot("St link")
    setp(T_TRIM); cur(STF); W32(STF, t.slot(STF) - 1); t.fire(3); assert hdr()[2] == hdr()[0], "the loop follows the start"
    setp(T_LINK); e.wr(LINKF, 0, 1); t.fire(3); assert e.rd(B + 0x15, 1) == 0
    print("  ok St link: ON -> loop start = trim start and follows it; OFF")
    # --- Tune ---
    setp(T_TUNE); assert screen() == ["Tune:   0       ", "Fine:   0       "], screen()
    e.wr(SEMI, 24 + 12, 1); t.fire(3); assert s32(R32(B + 0x30)) == 1200
    cur(CENT); e.wr(CENT, FINE_MAX + 35, 1); t.fire(3); assert s32(R32(B + 0x30)) == 1235
    assert screen() == ["Tune: +12       ", "Fine: +35       "], screen(); shot("Tune")
    slider(FINE_MAX + 30); slider(FINE_MAX + 50); assert abs(s32(R32(B + 0x30)) - 1250) <= 1, s32(R32(B + 0x30))
    cur(PARAM); assert t.slot(KT) == K_SEMI
    slider(t.slot(CUR)); slider(0); assert s32(R32(B + 0x30)) == -2400, ("max -24.00", s32(R32(B + 0x30)))
    e.wr(SEMI, 24 - 3, 1); t.fire(3); e.wr(CENT, FINE_MAX + 35, 1); t.fire(3)
    assert s32(R32(B + 0x30)) == -265 and screen() == ["Tune:  -2       ", "Fine: -65       "], (s32(R32(B + 0x30)), screen())
    e.wr(SEMI, 24, 1); t.fire(3); e.wr(CENT, FINE_MAX, 1); t.fire(3); assert R32(B + 0x30) == 0
    print("  ok Tune: +12 st, +35 / +50 cents (wheel, slider), -24.00 limit, -2.65 shown -2 / -65 (+0x30 u32)")

    # --- DO IT: EXTRACT TO PAD ---
    setp(T_TRIM); cur(STF); st_, en_ = hdr()[0], hdr()[1]; cur0 = R32(MGR + 8); end0 = list_end()
    t.doit(); assert t.msg() == "EXTRACT TO PAD" and t.slot(ST) == ST_WAIT, t.msg()
    t.fire(2); assert screen()[1] == "MUTE GROUP   OFF" and focus() == WID[MG] and not vis(STF), screen(); shot("DO IT")
    t.cancel(); t.fire(2); assert t.slot(ST) == ST_TRIM and focus() == WID[STF] and vis(STF) and vis(SENS) and not vis(ENDF), "CANCEL: back"
    t.doit(); e.wr(MG, 7, 1); t.fire(2); t.pad(0, 0); assert t.msg() == "PAD NOT EMPTY" and t.slot(ST) == ST_WAIT, t.msg()
    t.pad(0, 5); assert t.msg() == "EXTRACTED > A06", t.msg()
    assert e.rd(PROG + PAD_OFF + 5 * PAD_SIZE + 0x63, 1) == 7 and focus() == WID[STF], "mute group 7, cursor back"
    nm = bytes(e.rd(lay(5, 0) + i, 1) for i in range(16)).split(b'\0')[0]
    assert R32(end0 + 0x18) == en_ - st_ + 1, (R32(end0 + 0x18), en_ - st_ + 1)
    assert [s16(e.rd(end0 + 0x100 + 2 * i, 2)) for i in range(0, en_ - st_ + 1, 7)] == L[st_:en_ + 1:7], "PCM = START..END"
    assert nm == bytes(e.rd(end0 + 4 + i, 1) for i in range(16)).split(b'\0')[0] and nm != b"BREAK01", nm
    assert (e.rd(lay(5, 0) + 0x11, 1), e.rd(lay(5, 0) + 0x12, 1), e.rd(lay(5, 0) + 0x13, 1)) == (100, 0, 127)
    assert R32(MGR + 8) == cur0 and hdr()[:2] == (st_, en_) and t.slot(ST) == ST_TRIM and t.slot(SRC) == B
    print("  ok DO IT -> 'EXTRACT TO PAD' / 'MUTE GROUP   OFF' (CANCEL back, full pad refused) -> A06 = %r, %d frames"
          % (nm, en_ - st_ + 1))
    assert t.pad(0, 0)[0] == 0, "trimming: pads play"

    # --- Edit: the MPC's own Trim functions (2026-11-07) ---
    t.fire(pg.msg_fires + 10); t.fire(3)
    calls = lambda: (R32(LOG2), R32(LOG2 + 4) & 0xFF, R32(LOG2 + 8))
    def edit(k): e.wr(EDITF, k, 1); t.fire(3)
    setp(T_EDIT); assert t.slot(SRC) == B and t.slot(SEL) == 1
    assert screen() == ["Edit:   Truncate", "A01:BREAK01     "], screen(); shot("Edit")
    assert vis(EDITF) and not vis(STF) and not vis(SENS)
    # Normalize / Reverse: done here (HW: the MPC's own did nothing from this page), % then a preview
    nn = R32(B + 0x18)
    def pcm(b, ch):
        o = b + 0x100 + (2 * R32(b + 0x1C) if ch else 0)
        return [s16(e.rd(o + 2 * i, 2)) for i in range(nn)]
    def run_edit(limit=2000):
        seen = set()
        for _ in range(limit):
            if t.slot(EST) not in (ES_RUN, ES_UNDO): return seen
            seen.add(t.msg()); t.fire(1)
        raise AssertionError("Normalize / Reverse never finished")
    L0, R0, h0, n0 = pcm(B, 0), pcm(B, 1), hdr(), len(sounds())
    edit(E_NORM); t.doit()
    assert t.slot(EST) == ES_RUN and len(sounds()) == n0 + 1 and R32(MGR + 8) == B, "backup copy made, source current"
    seen = run_edit(); assert t.slot(EST) == ES_PREV and t.msg() == "DOIT=KEEP CANCEL", (t.slot(EST), t.msg())
    assert {"NORMALIZE 00%", "NORMALIZE 50%"} <= seen or len([s for s in seen if s.startswith("NORMALIZE ")]) >= 10, seen
    peak = max(abs(x) for x in L0 + R0); g = (32767 << 12) // peak
    assert pcm(B, 0) == [(x * g + 2048) >> 12 for x in L0] and pcm(B, 1) == [(x * g + 2048) >> 12 for x in R0], "model"
    assert 32700 <= max(abs(x) for x in pcm(B, 0)) <= 32767                          # (integer gain: just under full)
    assert t.pad(0, 0)[0] == 0 and t.slot(SRC) == B, "preview: the pad plays it, still the same source"
    t.doit(); assert t.slot(EST) == ES_IDLE and len(sounds()) == n0 and t.msg() == "NORMALIZED" and R32(MGR + 8) == B
    print("  ok Normalize: backup, %d progress steps, preview = model (peak -> 32767), DO IT keeps (backup gone)" % len(seen))
    L1, R1, h1 = pcm(B, 0), pcm(B, 1), hdr()
    edit(E_REV); t.doit(); run_edit(); assert t.slot(EST) == ES_PREV
    assert pcm(B, 0) == L1[::-1] and pcm(B, 1) == R1[::-1] and hdr()[:2] == (nn - 1 - h1[1], nn - 1 - h1[0]), hdr()
    t.cancel(); assert t.slot(EST) == ES_UNDO; seen = run_edit()
    assert t.slot(EST) == ES_IDLE and pcm(B, 0) == L1 and pcm(B, 1) == R1 and hdr() == h1 and len(sounds()) == n0
    assert t.msg() == "UNDONE" and any(s.startswith("UNDO ") for s in seen), (t.msg(), seen)
    edit(E_NORM); t.doit(); t.fire(3); assert t.slot(EST) == ES_RUN; t.cancel(); run_edit()
    assert pcm(B, 0) == L1 and hdr() == h1 and len(sounds()) == n0 and t.slot(EST) == ES_IDLE, "CANCEL while working"
    print("  ok Reverse: preview reversed, points mirrored; CANCEL (in the preview or while working) = the original back")
    edit(E_REV); t.doit(); run_edit(); setp(T_TRIM); t.fire(3)
    assert t.slot(EST) == ES_IDLE and t.msg() == "REVERSED" and pcm(B, 0) == L1[::-1] and len(sounds()) == n0
    setp(T_EDIT); edit(E_REV); t.doit(); run_edit(); t.doit(); assert pcm(B, 0) == L1 and len(sounds()) == n0
    print("  ok another page during the preview = kept (and reversed back)")
    W32(LOG2, 0)
    edit(E_DISCARD); t.doit(); assert t.msg() == "DO IT: TRUNCATE?" and R32(LOG2) == 0, t.msg()
    t.doit(); assert calls() == (1, 1, B) and t.slot(CONF) == 0 and t.msg() == "TRUNCATED", (calls(), t.msg())
    assert e.rd(OS_EDITSEL, 1) == 0, "the MPC's own Edit choice put back"
    assert t.slot(SRC) == B and screen()[1] == "A01:BREAK01     ", "the pad's sample again"
    edit(E_DELETE); t.doit(); assert t.msg() == "DO IT: DELETE?" and t.slot(CONF) == E_DELETE + 1
    t.cancel(); assert t.slot(CONF) == 0 and t.msg() == "CANCELLED" and R32(LOG2) == 1 and t.slot(SRC) == B
    t.doit(); assert t.slot(CONF) == E_DELETE + 1; edit(E_REV); assert t.slot(CONF) == 0, "another function: no"
    edit(E_DELETE); t.doit(); t.pad(0, 0); t.fire(3); assert t.slot(CONF) == 0, "a pad: no"
    t.doit(); t.doit(); assert calls() == (2, 7, B) and t.msg() == "DELETED", (calls(), t.msg())
    print("  ok Discard / Delete ask first (DO IT again = done + DISCARDED / DELETED; CANCEL, another function or a pad = no)")
    W32(LOG, 0); edit(E_STRETCH); t.doit(); assert pushes() == [] and t.msg() == "NOT AVAILABLE", (pushes(), t.msg())
    for P in (PG_STRETCH, PG_ASSIGN): W32(P + 0x1C, VT_PAGE)
    t.doit(); assert pushes() == [(PG_STRETCH, 0)] and R32(MGR + 8) == B, pushes()
    n0 = len([b for b in sounds()]); W32(LOG, 0); edit(E_COPY); t.doit()
    assert pushes() == [(PG_ASSIGN, 0)] and len(sounds()) == n0 + 1 and R32(MGR + 8) != B, (pushes(), len(sounds()))
    newc = R32(MGR + 8); assert R32(newc + 0x18) == R32(B + 0x18), "a whole copy"
    W32(LOG, 0); edit(E_EXTRACT); t.doit(); assert pushes() == [(PG_ASSIGN, 0)] and len(sounds()) == n0 + 2
    assert R32(R32(MGR + 8) + 0x18) == R32(B + 0x24) - R32(B + 0x20) + 1, "the trimmed range"
    print("  ok Stretch -> the MPC's Time stretch page; Copy / Extract -> new sample + the MPC's Assign page")
    h.ram_w16(B + 0x3E, 1); edit(E_NORM); W32(LOG2, 0); t.doit(); assert t.msg() == "COPY/DELETE ONLY" and R32(LOG2) == 0
    edit(E_DELETE); t.doit(); t.doit(); assert calls() == (1, 2, B), calls()
    h.ram_w16(B + 0x3E, 0)
    print("  ok sample-pack sample: Copy / Delete only (Delete = the MPC's choice 2)")
    # the slider scrolls through every sample in memory (add-on WAVs skipped); a pad picks its own again
    ss = sounds(); assert len(ss) >= 3 and t.slot(KT) == K_SMP and t.slot(NSTEP) == len(ss) - 1, (len(ss), t.slot(NSTEP))
    for k in (1, 0, len(ss) - 1):
        slider(k); assert t.slot(SRC) == ss[k] and t.slot(SEL) == 0, (k, hex(t.slot(SRC)), [hex(x) for x in ss])
        nm_ = cstr(ss[k] + 4, 16); assert screen()[1] == nm_.ljust(16), (screen(), nm_)
    shot("Edit (slider)")
    W32(LOG2, 0); edit(E_DISCARD); t.doit(); t.doit()
    assert calls() == (1, 1, ss[-1]) and t.slot(SRC) == ss[-1], "no pad: the same sample"
    t.pad(0, 0); t.fire(3); assert t.slot(SRC) == B and screen()[1] == "A01:BREAK01     "
    print("  ok slider: %d samples in memory, name shown; a pad picks its sample again" % len(ss))
    edit(E_DISCARD)
    # HW 2026-11-07: Edit used, back to Trim St, SEQUENCE, MODE + Trim pad -> came back on Edit (must stay Trim St)
    edit(E_DISCARD); t.doit(); t.doit(); setp(T_TRIM); t.fire(5); assert e.rd(PARAM, 1) == T_TRIM
    osv = (R32(OSTRIM + 0x1C), R32(OSTRIM + 0x30)); W32(OSTRIM + 0x1C, VT_PAGE); W32(OSTRIM + 0x30, OSTRIM_H)
    h.ram_w32(EVQ + 0x68, 0x8848FE04); t.fire(LEAVE_FIRES + 4)
    h.ram_w32(EVQ + 0x68, MODEPG); t.fire(2); W32(LOG, 0); h.ram_w32(EVQ + 0x68, OSTRIM); t.fire(1)
    assert pushes() == [(0, 2), (pg.page, 0)], pushes()
    h.ram_w32(EVQ + 0x68, pg.page); t.fire(pg.msg_fires + 10)
    assert e.rd(PARAM, 1) == T_TRIM and screen()[0].startswith("Trim St:"), screen()
    W32(OSTRIM + 0x1C, osv[0]); W32(OSTRIM + 0x30, osv[1])
    # CANCEL = the previous Trim page; on Trim St it is the OS's (leaves the page)
    setp(T_EDIT); assert t.cancel() == 1 and e.rd(PARAM, 1) == T_TUNE and focus() == WID[PARAM], (e.rd(PARAM, 1), hex(focus()))
    assert t.cancel() == 1 and e.rd(PARAM, 1) == T_LINK and screen()[0].startswith("St link:"), screen()
    setp(T_ENTER); assert t.cancel() == 1 and e.rd(PARAM, 1) == T_ZONE
    setp(T_TRIM); W32(LOG, 0); t.cancel(); assert pushes() == [(0, 2)] and e.rd(PARAM, 1) == T_TRIM, ("Trim St: leaves", pushes())
    W32(LOG, 0); setp(T_TUNE); t.cancel(); assert pushes() == [], "other pages: no leaving"
    print("  ok CANCEL: Edit -> Tune -> St link, ENTER DEFAULT -> ENTER ZONE; on Trim St it leaves")
    print("  ok Edit used, Trim St, SEQUENCE, MODE + Trim pad -> back on Trim St (emulator)")

    # --- ENTER ZONE ASSIGN MODE: the Zone add-on's page (found in the HUB table) ---
    t.fire(pg.msg_fires + 10)
    setp(T_ZONE); assert screen() == ["ENTER ZONE      ", "ASSIGN MODE     "], screen(); shot("ENTER ZONE ASSIGN")
    W32(LOG, 0); t.doit(); assert pushes() == [] and t.msg() == "ZONE NOT LOADED", (pushes(), t.msg())
    zwav = os.path.join(os.path.dirname(__file__), '..', '..', 'ZONE.WAV')
    h.ram_w32(EVQ + 0x68, 0x88123456); t.fire(2); h.load_wav(zwav); t.fire(20)       # (LOAD happens on an OS page)
    Lh = h.hub_lay(); zk = [k for k in range(16) if R32(Lh.INST(k)) == 1 and bytes(e.rd(Lh.NAME(k) + i, 1) for i in range(8)) == ZONE_NAME]
    assert zk, "Zone installed"; zpage = R32(Lh.PAGE(zk[0]))
    h.ram_w32(EVQ + 0x68, pg.page); t.fire(pg.msg_fires + 10); setp(T_ZONE)
    W32(LOG, 0); t.doit(); assert pushes() == [(zpage, 0)], (pushes(), hex(zpage))
    print("  ok ENTER ZONE ASSIGN MODE: 'ZONE NOT LOADED' without Zone; Zone loaded later -> DO IT opens its page")    # --- ENTER DEFAULT MPC TRIM MODE ---
    t.fire(pg.msg_fires + 10)
    setp(T_ENTER); assert screen() == ["ENTER DEFAULT   ", "MPC TRIM MODE   "], screen(); shot("ENTER DEFAULT")
    W32(LOG, 0); t.doit(); assert pushes() == [] and t.msg() == "NOT AVAILABLE", (pushes(), t.msg())
    W32(OSTRIM + 0x1C, VT_PAGE); W32(OSTRIM + 0x30, OSTRIM_H)
    W32(LOG, 0); t.doit(); assert pushes() == [(OSTRIM, 0)], pushes()
    h.ram_w32(EVQ + 0x68, OSTRIM); t.fire(5); assert pushes() == [(OSTRIM, 0)], "opened from here: not redirected"
    h.ram_w32(EVQ + 0x68, MODEPG); t.fire(2); W32(LOG, 0); h.ram_w32(EVQ + 0x68, OSTRIM); t.fire(1)
    assert pushes() == [(0, 2), (pg.page, 0)], pushes()
    h.ram_w32(EVQ + 0x68, MODEPG); t.fire(2); W32(LOG, 0); h.ram_w32(EVQ + 0x68, 0x88490E60); t.fire(3)
    assert pushes() == [], "another MODE pad: left alone"
    print("  ok ENTER DEFAULT opens the MPC Trim page (object checked); MODE + Trim pad -> this page")

    # --- leaving keeps the trim; short sample; pad without sample ---
    h.ram_w32(EVQ + 0x68, pg.page); t.fire(pg.msg_fires + 10); setp(T_TRIM)
    trimmed = hdr(); t.close(); t.fire(LEAVE_FIRES + 4); assert hdr() == trimmed
    t.open(); t.fire(4); assert screen()[0] == "Trim St:%8d" % trimmed[0], screen()
    t.pad(0, 1); t.fire(3); assert t.slot(SRC) == B2 and t.slot(NT) == 1 and t.slot(NSTEP) == 0
    cur(STF); W32(SLIDER, SL_HI); t.fire(20); assert hdr(B2)[0] == 0, "no transients: the slider does nothing"
    W32(STF, 40); t.fire(3); assert hdr(B2)[0] == 40, "the wheel: frames"
    t.pad(0, 2); t.fire(3); assert screen()[1] == "NO SAMPLE       ", screen()
    t.doit(); assert t.msg() == "HIT A PAD FIRST"
    print("  ok left / back: trim kept; SHORT (no transients): wheel only; A03 (missing sample) -> NO SAMPLE")
    print("\nScreens (16x2):")
    for k, (r0, r1) in shots.items(): print("  %-22s |%s|\n  %-22s |%s|" % (k, r0, "", r1))
    print("OK: TRIM.WAV built and tested")


if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', '..', 'TRIM.WAV'); pg.build_wav(out)
    span = pg.a.labels['__cend'] - pg.a.labels['__cstart']
    assert span <= CACHE_SPAN, "kernels %d B > CACHE_SPAN" % span
    print("  kernels %d B (cache span %d)" % (span, CACHE_SPAN))
    selftest(out)
