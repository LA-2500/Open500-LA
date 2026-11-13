#!/usr/bin/env python3
"""Builds the standalone firmware patcher page (factory Akai OS 1.31 -> LA firmware).

Usage: python build_patcher.py <factory mpc500.bin> <updated MPC500.bin> <older Open500 MPC500.bin>

The page holds only Open500 data: the loader, written over an all-zero region of the OS, and the 4-byte hook pointer.
The OS CRC is recomputed in the page. Re-run this after any firmware change.
"""
import hashlib, json, os, struct, sys, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
PATCHER_JS = os.path.join(HERE, '..', 'docs', 'patcher.js')
OUT = os.path.join(HERE, 'Open500 LA Firmware Patcher.html')
FIRMWARE_NAME = 'LA 2.xx firmware (or newer)'
FACTORY_SHA256 = '7ba01305b1b2c89701e0d7c1d92973920a76c13d0232d9cbc579767332c2940d'
SIZE, OS_HDR, CRC_OFF, HOOK_OFF = 514732, 0x10000, 0x10010, 0x2B328

PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Open500 LA Firmware Patcher</title>
<style>
:root { --bg:#f4f3ef; --panel:#fff; --text:#1d1d1b; --muted:#66645e; --line:#b9b6ad; --accent:#a8321f; --on-accent:#fff; --ok:#1f6f43; --bad:#a8321f; }
@media (prefers-color-scheme: dark) { :root { --bg:#171716; --panel:#232321; --text:#efede7; --muted:#a8a59d; --line:#55534d; --accent:#ec7a63; --on-accent:#1d1d1b; --ok:#71c997; --bad:#f09380; } }
* { box-sizing:border-box; }
body { margin:0; padding:48px 16px; background:var(--bg); color:var(--text); font:16px/1.5 system-ui, "Segoe UI", sans-serif; }
main { max-width:520px; margin:0 auto; }
h1 { margin:0 0 4px; font-size:26px; line-height:1.2; }
p { margin:0; }
.sub { color:var(--muted); margin-bottom:24px; }
#drop { display:block; padding:40px 20px; background:var(--panel); border:2px dashed var(--line); border-radius:8px; text-align:center; cursor:pointer; }
#drop.over, #drop:hover { border-color:var(--accent); }
#drop:focus-within { outline:2px solid var(--accent); outline-offset:2px; }
#drop strong { display:block; }
#drop span { color:var(--muted); font-size:14px; }
#drop input { position:absolute; opacity:0; width:1px; height:1px; }
#status { min-height:24px; margin-top:16px; }
#status[data-state="ok"] { color:var(--ok); font-weight:600; }
#status[data-state="bad"] { color:var(--bad); font-weight:600; }
#save { display:none; margin-top:12px; padding:12px 20px; background:var(--accent); color:var(--on-accent); border-radius:6px; font-weight:600; text-decoration:none; }
#save.show { display:inline-block; }
.note { margin-top:32px; padding-top:16px; border-top:1px solid var(--line); color:var(--muted); font-size:14px; }
</style>
</head>
<body>
<main>
<h1>Open500 LA Firmware Patcher</h1>
<p class="sub">Updates the factory Akai MPC500 OS 1.31 to @@FIRMWARE@@.</p>
<label id="drop">
  <input id="file" type="file" accept=".bin">
  <strong>Drop the factory mpc500.bin here</strong>
  <span>or click to choose it</span>
</label>
<p id="status" role="status"></p>
<a id="save" download="MPC500.bin">Save MPC500.bin</a>
<p class="note">Your file stays on this computer. The patcher only accepts the unmodified Akai OS 1.31 file, and it checks that the result matches the updated firmware byte for byte.</p>
</main>
<script>
@@PATCHER@@
</script>
<script>
var PATCH = @@PATCH@@;
var OLDER_OPEN500_CRC32 = @@OLDER_CRC@@;
var input = document.getElementById('file'), drop = document.getElementById('drop');
var statusEl = document.getElementById('status'), save = document.getElementById('save'), url = null;

function show(state, text) { statusEl.dataset.state = state; statusEl.textContent = text; }
function patch(buf) {
  var u = new Uint8Array(buf);
  if (u.length === PATCH.size && Open500Patcher.crc32(u) === OLDER_OPEN500_CRC32)
    return show('bad', 'This is an older Open500 firmware. Choose the factory Akai mpc500.bin instead.');
  var r = Open500Patcher.applyPatch(u, PATCH);
  if (!r.ok) return show('bad', r.error);
  url = URL.createObjectURL(new Blob([r.data], { type: 'application/octet-stream' }));
  save.href = url; save.classList.add('show');
  show('ok', 'Patched and verified. Save the file below.');
}
function handle(file) {
  if (!file) return;
  if (url) { URL.revokeObjectURL(url); url = null; }
  save.classList.remove('show'); save.removeAttribute('href');
  show('', 'Checking ' + file.name + '...');
  var rd = new FileReader();
  rd.onload = function () { patch(rd.result); };
  rd.onerror = function () { show('bad', 'That file could not be read.'); };
  rd.readAsArrayBuffer(file);
}
input.addEventListener('change', function () { handle(input.files[0]); input.value = ''; });
['dragenter', 'dragover'].forEach(function (t) { window.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add('over'); }); });
window.addEventListener('dragleave', function (e) { if (!e.relatedTarget) drop.classList.remove('over'); });
window.addEventListener('drop', function (e) { e.preventDefault(); drop.classList.remove('over'); handle(e.dataTransfer.files[0]); });
</script>
</body>
</html>
'''


def os_crc(b):
    n = struct.unpack('<I', b[OS_HDR + 0xC:OS_HDR + 0x10])[0]
    t = bytearray(b[OS_HDR:OS_HDR + n])
    t[CRC_OFF - OS_HDR:CRC_OFF - OS_HDR + 4] = bytes(4)
    return zlib.crc32(bytes(t)) & 0xffffffff


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    factory, updated, older = (open(p, 'rb').read() for p in sys.argv[1:])
    assert hashlib.sha256(factory).hexdigest() == FACTORY_SHA256, 'first file is not the factory OS 1.31'
    assert len(factory) == len(updated) == len(older) == SIZE, 'unexpected file size'
    assert os_crc(updated) == struct.unpack('<I', updated[CRC_OFF:CRC_OFF + 4])[0], 'updated firmware has a bad OS CRC'

    blocks = []
    for i in range(SIZE):                 # gaps under 16 bytes are merged: some loader bytes equal the zeros they replace
        if factory[i] != updated[i] and not CRC_OFF <= i < CRC_OFF + 4:
            if blocks and i - blocks[-1][1] < 16:
                blocks[-1][1] = i + 1
            else:
                blocks.append([i, i + 1])
    for a, b in blocks:
        assert set(factory[a:b]) <= {0} or a == HOOK_OFF, 'patch would overwrite Akai data at %#x' % a

    rebuilt = bytearray(factory)
    for a, b in blocks:
        rebuilt[a:b] = updated[a:b]
    rebuilt[CRC_OFF:CRC_OFF + 4] = struct.pack('<I', os_crc(rebuilt))
    assert bytes(rebuilt) == updated, 'factory + patch does not equal the updated firmware'

    patch = {
        'name': FIRMWARE_NAME, 'target': 'Akai MPC500 OS 1.31 (mpc500.bin)', 'size': SIZE,
        'base_crc32': zlib.crc32(factory) & 0xffffffff, 'base_sha256': FACTORY_SHA256,
        'result_crc32': zlib.crc32(updated) & 0xffffffff, 'result_sha256': hashlib.sha256(updated).hexdigest(),
        'os_header': OS_HDR, 'crc_off': CRC_OFF, 'os_size': struct.unpack('<I', factory[OS_HDR + 0xC:OS_HDR + 0x10])[0],
        'patches': [{'off': a, 'orig': factory[a:b].hex(), 'data': updated[a:b].hex()} for a, b in blocks],
    }
    patcher = open(PATCHER_JS, encoding='utf-8').read()
    assert '</script' not in patcher
    html = (PAGE.replace('@@FIRMWARE@@', FIRMWARE_NAME).replace('@@PATCHER@@', patcher)
            .replace('@@PATCH@@', json.dumps(patch)).replace('@@OLDER_CRC@@', str(zlib.crc32(older) & 0xffffffff)))
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write(html)
    print('wrote', OUT)
    print('%d blocks, %d bytes; crc32 %08x -> %08x' % (len(blocks), sum(b - a for a, b in blocks), patch['base_crc32'], patch['result_crc32']))


if __name__ == '__main__':
    main()
