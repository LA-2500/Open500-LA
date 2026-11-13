# emulator test of printfx.py (run by "python sdk/examples/printfx.py"; executed inside its __main__)
from validate import Harness
from shasm import SHAsm
from mpcmod import HOOKS
from pages import ENABLE_DOIT, ENABLE_CANCEL
from testkit import PageTest, FIRMWARE
import random, time

class OSTest(PageTest):
    def __init__(self, pg, wav, before_hub=None):
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
        if before_hub: before_hub(self)
        h.load_hub(); h.load_wav(wav); h.fire(10)

LOG, PLOG = 0x88E20000, 0x88E28000
TEMPO_VAR = 0x88E30000
def stubs(t):
    def logp(s, tag, log=LOG):
        s.movl_pool(log, 1); s.movl_ld(1, 0); s.add_imm(1, 0); s.movl_st(0, 1); s.shll2(0); s.shll2(0); s.add_rr(0, 1)
        s.mov_imm(tag, 0); s.movl_st(0, 1); s.movl_st_disp(5, 4, 1); s.movl_st_disp(6, 8, 1); s.movl_st_disp(7, 12, 1)
        s.rts(); s.nop()
    t.stub(AUD_PLAY, lambda s: logp(s, 1)); t.stub(AUD_STOP, lambda s: logp(s, 2))
    t.stub(OS_PUSH_PAGE, lambda s: logp(s, 3, PLOG))
    t.stub(SET_FOCUS, lambda s: (s.movl_st_disp(5, 0x24, 4), s.rts(), s.nop()))
    # the OS parameter manager: get tempo -> msg+4 = tempo x 10 from TEMPO_VAR (master.py's test)
    t.stub(SYS_PARAM, lambda s: (s.movl_pool(TEMPO_VAR, 1), s.movl_ld(1, 0), s.movl_st_disp(0, 4, 4), s.rts(), s.nop()))
    if 'noguard' in sys.argv[1:]: t.stub(LOOP_GUARD)
t = OSTest(pg, out, before_hub=stubs); h, e = t.h, t.e
R32, W32 = h.ram_r32, h.ram_w32
def pushes():
    n = R32(PLOG); r = [(R32(PLOG + 16 * i + 20), R32(PLOG + 16 * i + 24)) for i in range(n)]; W32(PLOG, 0); return r
def s16(v): return v - 0x10000 if v & 0x8000 else v
rnd = random.Random(10)
def mkblock(name, L, R=None):
    n = len(L); stereo = R is not None
    pcm = b''.join(struct.pack('<h', v) for v in L) + (b''.join(struct.pack('<h', v) for v in R) if stereo else b'')
    blk = h.add_sample(pcm, name=name)
    W32(blk + 0x18, n); W32(blk + 0x1C, n if stereo else 0); W32(blk + 0x20, 0); W32(blk + 0x24, n - 1); W32(blk + 0x28, 0)
    h.ram_w16(blk + 0x92, 2 if stereo else 1); return blk
SN = [int(14000 * math.exp(-i / 90.0) * (rnd.random() * 2 - 1)) for i in range(300)]   # a short noisy "snare"
B_S = mkblock(b"SNARE", SN)
EQS = [int(16000 * math.sin(i * 2 * math.pi * 80 / FS) + 9000 * (rnd.random() * 2 - 1)) for i in range(1500)]  # bass + noise
B_Q = mkblock(b"EQTEST", EQS)
def list_end():
    p = R32(MGR)
    while R32(p): p += R32(p)
    return p
h.ram_w16(PROG_IDX, 1); PROG = PROG_BASE + PROG_STRIDE
def lay(p, l): return PROG + PAD_OFF + p * PAD_SIZE + l * LAYER
for p in range(48):
    for l in range(4):
        for i in range(0x18): e.wr(lay(p, l) + i, 0, 1)
for i, c in enumerate(b"SNARE"): e.wr(lay(4, 0) + i, c, 1)
e.wr(lay(4, 0) + 0x11, 90, 1); e.wr(lay(4, 0) + 0x13, 127, 1)
for i, c in enumerate(b"EQTEST"): e.wr(lay(6, 0) + i, c, 1)
e.wr(lay(6, 0) + 0x11, 100, 1); e.wr(lay(6, 0) + 0x13, 127, 1)
# fast test (default): the tails table in the loaded add-on is shortened to SHORT samples, the model follows.
FULL = 'full' in sys.argv[1:]
if not FULL:
    SHORT = 1500
    pat = b''.join(struct.pack('<I', v) for v in TAILS)
    lo_ = ST - 0x8000                                          # the code lies below its scratch slots
    buf_, off_ = e._region(lo_); k = bytes(buf_[off_:off_ + 0x8000]).find(pat)
    assert k >= 0, "tails table not found in memory"
    hit = lo_ + k
    delta = (SHORT * len(TAILS) - sum(TAILS)) & 0xFFFFFFFF
    TAILS[:] = [SHORT] * len(TAILS)
    for i in range(len(TAILS)): W32(hit + 4 * i, SHORT)
    L_ = h.hub_lay(); k_ = [k for k in range(16) if R32(L_.INST(k)) == 1]; assert len(k_) == 1, k_
    W32(L_.SUMC(k_[0]), (R32(L_.SUMC(k_[0])) + delta) & 0xFFFFFFFF)      # the HUB watchdog checksums the code
    print("  (fast test: tails shortened to %d samples; 'full' for real tails)" % SHORT)
def jobs(limit=None):
    limit = limit or (200000 if FULL else 4000)
    for k in range(limit):
        if t.slot(ST) != ST_JOB: return k
        t.fire(1)
    raise AssertionError("job did not finish: PHASE %d POS %d NOUT %d TOTU %d DONEU %d" % (
        t.slot(PHASE), t.slot(POS), t.slot(NOUT), t.slot(TOTU), t.slot(DONEU)))
def lab(p, k, w): return bytes(e.rd(p.dlabels[k] + i, 1) for i in range(w)).decode('latin-1')

# ---- layer tuning (rv_pow with the one-octave table): 2^(c/1200) and 2^(-c/1200), -36..+60 semitones ----
L_ = h.hub_lay(); kk = [k for k in range(16) if R32(L_.INST(k)) == 1][0]
POWA = R32(L_.BASE(kk)) + (pg.a.labels['rv_pow'] - pg.a.base)
wu = wd = 0                                                    # (old 121-entry table: 0.23 / 1.10 cents)
for c in list(range(-3600, 6001, 37)) + [-3600, -1, 0, 1, 99, 100, 1200, 6000]:
    regs = [0x55550000 + i for i in range(16)]; regs[15] = 0x88400000 - 256; regs[4] = c & 0xFFFFFFFF
    e.r = regs[:]; e.pr = 0xDEADBEEE; e.pc = POWA; e.maxsteps = 100_000
    assert e.run(stop_pc=0xDEADBEEE), "rv_pow did not return"
    up, dn = e.r[0] & 0xFFFFFFFF, e.r[1] & 0xFFFFFFFF
    wu = max(wu, abs(up / 65536 / 2 ** (c / 1200) - 1)); wd = max(wd, abs(dn / 65536 / 2 ** (-c / 1200) - 1))
assert wu < 2.1e-4 and wd < 8.3e-4, (wu, wd)
print("  ok layer tuning (one-octave table): -36..+60 semitones, pitch within %.2f cents (length estimate %.2f)"
      % (1200 * math.log2(1 + wu), 1200 * math.log2(1 + wd)))
# ---- PRINT FX list ----
t.pg = pg; t.open(); pushes()
e.wr(TYPE, X_REV, 1); t.doit(); assert pushes() == [(0, 2), (pgR.page, 0)]
e.wr(TYPE, X_EQ, 1); t.doit(); assert pushes() == [(0, 2), (pgE.page, 0)]
print("  ok PRINT FX: REVERB -> its page %08x, EQ -> its page %08x (in place of the list: back + push)" % (pgR.page, pgE.page))

# ---- REVERB (as REVERB.WAV) ----
print("  reverb: %d ops per sample, %d variables, %d presets, longest tail %.1f s" % (len(D.ops), NV, len(PRESETS), TMAX / FS))
t.pg = pgR; t.open()
AMB = NAMES.index("AMBIENCE")
assert e.rd(PRESET, 1) == DEFP and t.slot(MIX) == MIXDEF[DEFP] and e.rd(STER, 1) == 0
e.wr(PRESET, AMB, 1); t.fire(2); assert t.slot(MIX) == MIXDEF[AMB], "preset sets its default mix"
t.doit(); assert t.msg() == "HIT A PAD FIRST", repr(t.msg())
print("  ok defaults %s / mix %d / MONO; preset -> its default mix (AMBIENCE %d)" % (NAMES[DEFP], MIXDEF[DEFP], MIXDEF[AMB]))
e.wr(STER, 1, 1); t.pad(0, 4); t.fire(2); assert t.label(PADL, 5) == "  A05"
end0 = list_end(); t0 = time.time(); t.doit(); fires = jobs()
assert t.slot(ST) == ST_PREV and t.label(PADL, 5) == " PREV", (t.slot(ST), t.label(PADL, 5))
D1 = R32(MGR + 8); assert D1 == end0
nout = R32(D1 + 0x18); assert nout == len(SN) + TAILS[AMB], (nout, len(SN) + TAILS[AMB])
dry = [(v * ((90 << 16) // 90)) >> 16 for v in SN]
pk, _, _ = model_render(dry, AMB, MIXDEF[AMB], 1, 0)
G = 0x7FFF0000 // pk if pk else 0x10000
_, ML, MR = model_render(dry, AMB, MIXDEF[AMB], 1, 1, G)
sd = R32(D1 + 0x1C); GL = [s16(e.rd(D1 + 0x100 + 2 * i, 2)) for i in range(nout)]; GR = [s16(e.rd(D1 + 0x100 + 2 * sd + 2 * i, 2)) for i in range(nout)]
bad = [i for i in range(nout) if GL[i] != ML[i] or GR[i] != MR[i]]
assert not bad, (len(bad), bad[:5], [(GL[i], ML[i], GR[i], MR[i]) for i in bad[:3]])
assert (e.rd(D1 + 0x92, 2), sd) == (2, nout + GUARD) and GL != GR, "stereo, L and R differ"
print("  ok AMBIENCE stereo: %d frames (= dry %d + tail), bit-exact with the model, %d fires (%.0f s in the emulator)"
      % (nout, len(SN), fires, time.time() - t0))
def plog4():
    n = R32(LOG); r = [tuple(R32(LOG + 16 * i + 16 + 4 * j) for j in range(4)) for i in range(n)]; W32(LOG, 0); return r
plog4(); W32(BANKOBJ + 4, 0); r0, r1 = t.pad(0, 4, vel=70); assert r0 == 1 and (1, D1, 0, 70) in plog4(), "preview played at the pad velocity"
W32(BANKOBJ + 4, 1); r0, r1 = t.pad(0, 4, vel=70); assert r0 == 1 and (1, D1, 0, 127) in plog4(), "FULL LEVEL on: 127"
W32(BANKOBJ + 4, 0)
r0, _ = t.pad(0, 6); assert r0 == 0; t.fire(2); assert t.slot(ST) == ST_IDLE and list_end() == end0 and t.slot(SEL) == 7
print("  ok preview: the chosen pad plays the rendered sample; another pad drops the preview")
t.pad(0, 4); t.fire(2); e.wr(STER, 0, 1); t.set_slot(MIX, 0); t.fire(2)
t.doit(); jobs(); D2 = R32(MGR + 8); nout2 = R32(D2 + 0x18)
GM = [s16(e.rd(D2 + 0x100 + 2 * i, 2)) for i in range(nout2)]
pk, _, _ = model_render(dry, AMB, 0, 0, 0); _, MM, _ = model_render(dry, AMB, 0, 0, 1, 0x7FFF0000 // pk)
assert GM == MM and max(abs(v) for v in GM) in (32766, 32767) and (e.rd(D2 + 0x92, 2), R32(D2 + 0x1C)) == (1, 0)
print("  ok MONO, mix 0%%: = the dry sound normalized + silence (%d frames)" % nout2)
assert t.slot(ST) == ST_PREV, t.slot(ST)
t.set_slot(MIX, 60); t.fire(DEBOUNCE + 3)
assert t.slot(ST) == ST_JOB, t.slot(ST); jobs()
GM2 = [s16(e.rd(D2 + 0x100 + 2 * i, 2)) for i in range(nout2)]
pk, _, _ = model_render(dry, AMB, 60, 0, 0); G = 0x7FFF0000 // pk if pk else 0x10000
assert GM2 == model_render(dry, AMB, 60, 0, 1, G)[1]
print("  ok mix changed during the preview -> re-rendered (mix 60%, = model)")
t.doit(); jobs(); assert t.slot(ST) == ST_IDLE and t.msg().startswith("DONE "), t.msg()
assert R32(D2) == ((0x100 + 2 * (nout2 + GUARD) + 3) & ~3) and list_end() == D2 + R32(D2), "work memory cut off"
name = bytes(e.rd(lay(4, 0) + i, 1) for i in range(16)).split(b'\0')[0]
assert name == bytes(e.rd(D2 + 4 + i, 1) for i in range(16)).split(b'\0')[0] and name != b"SNARE"
assert e.rd(lay(4, 0) + 0x11, 1) == 90 and e.rd(lay(4, 0) + 0x14, 2) == 0 and (e.rd(lay(4, 0) + 0x12, 1), e.rd(lay(4, 0) + 0x13, 1)) == (0, 127)
print("  ok DO IT keeps it: pad A05 -> %r, block cut to the PCM, level 90 kept (%s)" % (name, t.msg()))
end1 = list_end(); t.pad(0, 4); t.fire(2); t.doit(); t.fire(5); assert t.slot(ST) == ST_JOB
pushes(); t.cancel(); assert list_end() == end1 and t.slot(ST) == ST_IDLE and pushes() == [(0, 2), (pg.page, 0)]
print("  ok CANCEL while rendering: work sample deleted, back to the PRINT FX list")
for i, p in enumerate(PRESETS):
    pk, _, _ = model_render(dry[:200], i, MIXDEF[i], 1, 0, limit=3000)
    assert 0 < pk < 200000, (p[0], pk)
print("  ok all %d presets render in the model (%s)" % (len(PRESETS), ", ".join(NAMES)))

# ---- EQ ----
def eqf(): return tuple(e.rd(f_, 1) for f_ in EQFIELDS)
def eq_check(D_, what):
    n_ = R32(D_ + 0x18); got = [s16(e.rd(D_ + 0x100 + 2 * i, 2)) for i in range(n_)]
    o = eq_model(EQS, eqf()); g = eq_gain(max(abs(v) for v in o)); want = [scale16(v, g) for v in o]
    assert n_ == len(EQS) and got == want, (what, n_, [(i, got[i], want[i]) for i in range(min(n_, len(want))) if got[i] != want[i]][:4])
    assert (e.rd(D_ + 0x92, 2), R32(D_ + 0x1C)) == (1, 0), "mono"
    return g, got
t.pg = pgE; t.open()
assert e.rd(EPRE, 1) == 0 and EQ_NAMES[0] == "DEFAULT" and eqf() == EQ_PIDX[0], eqf()
assert t.slot(SEL) == 5, "pad A05 still chosen"
t.pad(0, 6); t.fire(2); assert lab(pgE, EPADL, 5) == "  A07", lab(pgE, EPADL, 5)
end2 = list_end(); t.doit(); fires = jobs(); assert t.slot(ST) == ST_PREV and lab(pgE, EPADL, 5) == " PREV"
D3 = R32(MGR + 8); g, got = eq_check(D3, "DEFAULT")
assert got == EQS and g == 0x10000, "DEFAULT = the dry sound, same level"
print("  ok EQ DEFAULT: pad A07 rendered in %d fires = the original exactly (mono, %d frames, level kept)" % (fires, len(got)))
e.wr(EPRE, EQ_NAMES.index("BASS BOOST"), 1); t.fire(2); assert eqf() == EQ_PIDX[1], eqf()
t.fire(DEBOUNCE + 3); jobs(); g, got = eq_check(D3, "BASS BOOST")
print("  ok preset BASS BOOST -> band settings %s, re-rendered = model (level x%.3f)" % (eqf(), g / 65536))
for i_ in range(2, len(EQ_PRESETS)):                                 # every preset: model sane
    o = eq_model(EQS[:400], EQ_PIDX[i_]); assert max(abs(v) for v in o) < 200000, EQ_NAMES[i_]
# the MPC's PARAMS key opens EQ PARAMS (cursor anywhere); the preview stays
from testkit import EV_KEY
W32(pgE.page + 0x24, pgE.values[fidp(pgE, EPRE)]); pushes()
assert t.event(EV_KEY, 0x15) == 1 and pushes() == [(0, 2), (pgP.page, 0)] and t.slot(ST) == ST_PREV, "PARAMS key -> EQ PARAMS"
print("  ok the PARAMS key opens EQ PARAMS, preview kept")
# DO IT on PARAMS > opens EQ PARAMS; the preview stays
fpar = pgE.values[fidp(pgE, PARF)]; W32(pgE.page + 0x24, fpar); pushes(); t.doit()
assert pushes() == [(0, 2), (pgP.page, 0)] and t.slot(ST) == ST_PREV, "PARAMS > -> EQ PARAMS"
print("  ok DO IT on 'PARAMS >' opens EQ PARAMS (%08x), preview kept" % pgP.page)
# EQ PARAMS: bands, fields, cursor
t.pg = pgP; t.open()
vis = lambda F: e.rd(pgP.values[fidp(pgP, F)] + 0x17, 1) & 0x80 != 0
qv = lambda: e.rd(pgP.dlabels[QLBL] - 0x28 + 0x17, 1) & 0x80 != 0
assert [F for F in PCURSOR if vis(F)] == [BAND, LOWF, LOWG] and not qv(), [F for F in PCURSOR if vis(F)]
e.wr(BAND, B_MID, 1); t.fire(2)
assert [F for F in PCURSOR if vis(F)] == [BAND, MIDF, MIDG, QF] and qv() and lab(pgP, QLBL, 1) == "Q"
e.wr(BAND, B_HIGH, 1); t.fire(2); assert [F for F in PCURSOR if vis(F)] == [BAND, HIGHF, HIGHG]
e.wr(BAND, B_MID, 1); t.fire(2)
W32(pgP.page + 0x24, pgP.values[fidp(pgP, BAND)]); t.fire(1)
W32(pgP.page + 0x24, pgP.values[fidp(pgP, LOWF)]); t.fire(1)            # cursor right onto a hidden field
assert R32(pgP.page + 0x24) == pgP.values[fidp(pgP, MIDF)], "the cursor skips to the next shown field"
print("  ok EQ PARAMS: LOW / MID (with Q) / HIGH show their own fields; the cursor skips the hidden ones")
# custom settings: mid 3 kHz +8 Q 1.4, low +12 (clips -> level lowered), high 14k -6
for f_, v in ((MIDF, 4), (MIDG, G0 + 8), (QF, 4), (LOWG, G0 + 12), (HIGHF, 2), (HIGHG, G0 - 6)): e.wr(f_, v, 1)
t.fire(DEBOUNCE + 3); jobs(); g, got = eq_check(D3, "custom")           # (the EQ re-renders in 3 fires)
assert g < 0x10000 and max(abs(v) for v in got) >= 32700, ("a +12 dB bass boost would clip: level lowered", g)
print("  ok bands changed in EQ PARAMS -> re-rendered = model; +12 dB low clips -> level lowered (x%.3f), peak %d"
      % (g / 65536, max(abs(v) for v in got)))
# CANCEL on EQ PARAMS: back to EQ (preview kept); keep from EQ PARAMS
pushes(); t.cancel(); assert t.slot(ST) == ST_PREV and pushes() == [(0, 2), (pgE.page, 0)], "back to EQ, preview kept"
t.doit(); jobs(); assert t.slot(ST) == ST_IDLE and t.msg().startswith("DONE "), t.msg()
assert list_end() == D3 + R32(D3) and R32(D3) == ((0x100 + 2 * (len(EQS) + GUARD) + 3) & ~3), (
    hex(list_end()), hex(D3), hex(R32(D3)), hex((0x100 + 2 * (len(EQS) + GUARD) + 3) & ~3), hex(R32(MGR + 8)))
nm6 = bytes(e.rd(lay(6, 0) + i, 1) for i in range(16)).split(b'\0')[0]
assert e.rd(lay(6, 0) + 0x11, 1) == 100 and nm6 != b"EQTEST" and nm6 == bytes(e.rd(D3 + 4 + i, 1) for i in range(16)).split(b'\0')[0], nm6
print("  ok DO IT on EQ PARAMS keeps it (%s): mono, same length, level 100 kept" % t.msg())
# EQ: CANCEL drops the preview; switching to REVERB drops an EQ preview
t.pg = pgE; t.open(); W32(pgE.page + 0x24, pgE.values[fidp(pgE, EPRE)])   # (entering: cursor on the preset)
end3 = list_end(); t.pad(0, 6); t.fire(2); t.doit(); jobs(); assert t.slot(ST) == ST_PREV
pushes(); t.cancel(); assert t.slot(ST) == ST_IDLE and list_end() == end3 and pushes() == [(0, 2), (pg.page, 0)], t.slot(ST)
t.doit(); jobs(); assert t.slot(ST) == ST_PREV
t.pg = pgR; t.open(); assert t.slot(ST) == ST_IDLE and list_end() == end3 and t.slot(EFX) == 0, "EQ preview dropped"
pushes(); t.cancel(); assert pushes() == [(0, 2), (pg.page, 0)], "REVERB: CANCEL -> the list"
print("  ok CANCEL: EQ PARAMS -> EQ (preview kept); EQ / REVERB -> the PRINT FX list (preview dropped); opening REVERB drops an EQ preview")
assert t.pad(0, 9)[0] == 0

# ---- DELAY (2026-11-07) ----
def dlf(): return tuple(e.rd(f_, 1) for f_ in (DPRE, DTIME, DFB, DMIX))
def dl_check(D_, what, bpm10):
    fpb = 26460000 // bpm10
    pre, ti, fb, mx = dlf(); o = dl_model(SN, pre, ti, fb, mx, fpb); g = eq_gain(max(abs(v) for v in o))
    want = [scale16(v, g) for v in o]; n_ = R32(D_ + 0x18); got = [s16(e.rd(D_ + 0x100 + 2 * i, 2)) for i in range(n_)]
    assert n_ == len(SN) + dl_tail(ti, fb, fpb) and got == want, (what, n_, len(want),
        [(i, got[i], want[i]) for i in range(min(n_, len(want))) if got[i] != want[i]][:4])
    assert (e.rd(D_ + 0x92, 2), R32(D_ + 0x1C)) == (1, 0), "mono"
    return g, got
W32(TEMPO_VAR, 3000)                                                   # 300 BPM: short note values, short tails
for i in range(16): e.wr(lay(4, 0) + i, b"SNARE".ljust(16, b'\0')[i], 1)   # A05 = the original SNARE again (a reverb
for l in (1, 2, 3): e.wr(lay(4, l), 0, 1)                                  #  render was kept on it above)
pushes(); t.pg = pg; t.open(); e.wr(TYPE, X_DLY, 1); t.doit(); assert pushes() == [(0, 2), (pgD.page, 0)]
t.pg = pgD; t.open(); W32(pgD.page + 0x24, pgD.values[fidp(pgD, DPRE)])
assert DL_NAMES[0] == "DUB DELAY" and dlf() == (0, IX.TI["1/4"], 15, 9), dlf()          # 1/4, FB 75, MIX 45
t.pad(0, 4); t.fire(2); assert lab(pgD, DPADL, 5) == "  A05", lab(pgD, DPADL, 5)
e.wr(DPRE, DL_NAMES.index("1/16"), 1); t.fire(2)
p16 = DLP[2]; assert dlf() == (2, IX.TI["1/16"], IX.pct_index(p16[2]), IX.pct_index(p16[3])), dlf()
end4 = list_end(); t.doit(); fires = jobs(); assert t.slot(ST) == ST_PREV and lab(pgD, DPADL, 5) == " PREV"
D4 = R32(MGR + 8); g, got = dl_check(D4, "1/16", 3000)
print("  ok DELAY: PRINT FX -> its page; DUB DELAY first (1/4, FB 75, MIX 45); preset 1/16 at 300 BPM = model "
      "(%d frames = %d + tail, %d fires)" % (len(got), len(SN), fires))
e.wr(DMIX, 16, 1); t.fire(DEBOUNCE + 3); jobs(); g, got = dl_check(D4, "mix 80", 3000)
print("  ok MIX changed during the preview -> re-rendered = model (level x%.3f)" % (g / 65536))
e.wr(DPRE, 0, 1); t.fire(2); assert dlf()[0] == 0
e.wr(DTIME, IX.TI["40ms"], 1); t.fire(DEBOUNCE + 3); jobs(); g, got = dl_check(D4, "DUB DELAY 40 ms", 3000)
assert len(got) == len(SN) + dl_tail(IX.TI["40ms"], 15, 8820) and max(abs(v) for v in got[-200:]) < 200, "faded out"
print("  ok DUB DELAY (wow, 2.5 kHz / 150 Hz on the repeats, FB 75) at 40 ms = model, %d frames, the tail fades out"
      % len(got))
W32(TEMPO_VAR, 12345); e.wr(DTIME, IX.TI["1/32"], 1); t.fire(DEBOUNCE + 3); jobs(); dl_check(D4, "tempo out of range", 1200)
print("  ok a tempo out of range -> 120 BPM")
W32(TEMPO_VAR, 3000)
t.doit(); jobs(); assert t.slot(ST) == ST_IDLE and t.msg().startswith("DONE "), t.msg()
assert list_end() == D4 + R32(D4) and R32(D4 + 0x18) == len(SN) + dl_tail(IX.TI["1/32"], 15, 22050)   # (rendered at 120)
nm5 = bytes(e.rd(lay(4, 0) + i, 1) for i in range(16)).split(b'\0')[0]
assert nm5 == bytes(e.rd(D4 + 4 + i, 1) for i in range(16)).split(b'\0')[0], nm5
print("  ok DO IT keeps it (%s): mono, dry + tail, on pad A05" % t.msg())
end5 = list_end(); t.pad(0, 4); t.fire(2); t.doit(); jobs(); assert t.slot(ST) == ST_PREV
pushes(); t.cancel(); assert t.slot(ST) == ST_IDLE and list_end() == end5 and pushes() == [(0, 2), (pg.page, 0)]
print("  ok CANCEL: the preview dropped, back to the PRINT FX list")
for i_, nm_ in enumerate(DL_NAMES):                                    # every preset at 120 BPM: model sane
    pi = DL_PDEF[i_]; o = dl_model(SN, i_, pi & 0xFF, (pi >> 8) & 0xFF, (pi >> 16) & 0xFF, 22050)
    assert 0 < max(abs(v) for v in o) < 100000 and len(o) <= len(SN) + DL_TAILMAX, nm_
print("  ok all %d delay presets render in the model (%s)" % (len(DL_NAMES), ", ".join(DL_NAMES)))
print("OK: PRINTFX.WAV built and tested")
