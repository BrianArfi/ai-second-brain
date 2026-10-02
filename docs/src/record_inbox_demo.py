"""Record docs/inbox-demo.gif: the real local dashboard's Inbox tab, with sample data.

What it does, all on 127.0.0.1:
  1. Clones this repo into a scratch folder (a fresh clone, nothing of yours in it).
  2. Writes sample inbox items to <scratch>/journal/state/inbox.json, in the same shape
     .agent/skills/inbox-hub/scripts/inbox_sweep.py writes: three people waiting on you
     (Dina on Slack, Sam by email, Budi in a thread) and one FYI from Maya. Two of them
     carry a drafted reply, as the inbox digest leaves them.
  3. Starts dashboard/server.py on port 3799 in that clone.
  4. Drives Chromium with Playwright: the Inbox tab, opens Dina's thread,
     scrolls to the drafted reply and rests the cursor on "Approve & send" without
     clicking it. Nothing is sent: the scratch clone has no Slack token, and the
     recording never presses the button.
  5. Turns the video into docs/inbox-demo.gif with ffmpeg.

Usage:  python docs/src/record_inbox_demo.py
Needs:  pip install playwright && python -m playwright install chromium; ffmpeg; git.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parent
REPO = SRC.parent.parent
OUT = SRC.parent / 'inbox-demo.gif'
WORK = Path(tempfile.mkdtemp(prefix='asb-inbox-demo-'))
PORT = 3799
W, H = 1200, 720
FPS = 10

# Shared cursor for the family's GIFs: an ink dot with a lime ring.
CURSOR = """
window.addEventListener('DOMContentLoaded', () => {
  const c = document.createElement('div');
  c.style.cssText = 'position:fixed;left:0;top:0;width:22px;height:22px;border-radius:50%;background:#072B27;border:4px solid #C8F751;box-shadow:0 0 0 1.5px #072B27,0 2px 6px rgba(0,0,0,.25);z-index:2147483647;pointer-events:none;transform:translate(-100px,-100px);transition:transform .03s linear;box-sizing:border-box';
  document.documentElement.appendChild(c);
  window.addEventListener('mousemove', e => { c.style.transform = `translate(${e.clientX - 11}px,${e.clientY - 11}px)`; }, true);
});
"""

CAPTION = """(text) => {
  let t = document.getElementById('__cap');
  if (!t) { t = document.createElement('div'); t.id = '__cap'; document.body.appendChild(t); }
  t.textContent = text;
  t.style.cssText = 'position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:2147483646;background:#C8F751;color:#072B27;border:3px solid #072B27;border-radius:10px;padding:8px 16px;font:700 19px/1.2 "JetBrains Mono",Consolas,monospace;white-space:nowrap;box-shadow:4px 4px 0 #072B27';
}"""

def item(iid, source, ago_h, frm, text, triage, **kw):
    ts = time.time() - ago_h * 3600
    it = {
        'id': iid, 'source': source, 'ts': ts, 'from': frm, 'from_id': '',
        'channel': '', 'title': '', 'text': text, 'permalink': '',
        'status': 'open', 'status_changed_at': None, 'prev_status': None,
        'linked_ticket': None, 'first_seen': ts,
        'triage': triage, 'priority_hi': False, 'draft_reply': None,
        'draft_source': None, 'messages': [{'from': frm, 'ts': ts, 'text': text}],
        'msg_count': 1, 'send_channel': None, 'send_thread_ts': None,
        'sent_permalink': None, 'sent_at': None,
    }
    it.update(kw)
    return it

def sample_inbox():
    now = time.time()
    dina_1 = 'Hi! Is the final checkout copy ready? Design review is at 3 today.'
    dina_2 = 'I only need the payment screen strings, the rest can wait.'
    items = [
        item('slack:dm-dina', 'slack', 2.2, 'Dina', dina_2, 'reply',
             channel='DM', title='Final checkout copy for the 3 pm design review', priority_hi=True,
             messages=[{'from': 'Dina', 'ts': now - 2.6 * 3600, 'text': dina_1},
                       {'from': 'Dina', 'ts': now - 2.2 * 3600, 'text': dina_2}],
             msg_count=2, send_channel='dm-dina',
             draft_reply=('Hi Dina, the payment screen strings are final. I will drop them in this '
                          'thread by 1 pm, so you have them well before the 3 pm review. '
                          'The rest of the checkout copy follows tomorrow.'),
             draft_source='claude'),
        item('gmail:sam-partner-api', 'gmail', 5.0, 'Sam',
             'Can we raise the partner API limit before launch? We hit it twice in testing.',
             'reply', channel='inbox', title='Partner API limit before launch',
             draft_reply=('Hi Sam, thanks for flagging it. I am checking the cost of a higher limit '
                          'with the platform team today and will come back to you by Friday.'),
             draft_source='claude'),
        item('slack:launch-budi', 'slack', 26.0, 'Budi',
             'Any update on the refund rules one-pager? Support wants it before the weekend.',
             'reply', channel='#launch', title='Refund rules one-pager for support',
             send_channel='launch'),
        item('slack:launch-maya', 'slack', 1.0, 'Maya',
             'Heads up: release notes draft is pinned in the channel.',
             'fyi', channel='#launch', title='Release notes draft is pinned'),
    ]
    return {'last_sweep': now - 600, 'items': {i['id']: i for i in items},
            'sources': {'slack': {'ok': True}, 'gmail': {'ok': True}}}

def wait_up(url, secs=20):
    end = time.time() + secs
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:
            time.sleep(.3)
    sys.exit(f'dashboard did not start at {url}')

def record(base):
    from playwright.sync_api import sync_playwright
    vid = WORK / 'video'
    shutil.rmtree(vid, ignore_errors=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={'width': W, 'height': H}, record_video_dir=str(vid),
                            record_video_size={'width': W, 'height': H},
                            # The dashboard honours reduced motion: its header colour cycle stops,
                            # which keeps the GIF small. Nothing else on the page changes.
                            reduced_motion='reduce')
        ctx.add_init_script(CURSOR)
        p = ctx.new_page()
        t0 = time.time()
        p.goto(base + '/#inbox')
        p.wait_for_selector('.ibx-row')
        p.mouse.move(640, 560)
        p.wait_for_timeout(700)
        start = time.time() - t0
        p.evaluate(CAPTION, 'Who is waiting on you, with replies drafted')
        p.wait_for_timeout(2600)
        row = p.locator('.ibx-row .row-title.ibx-open-detail').first.bounding_box()
        p.mouse.move(row['x'] + 120, row['y'] + row['height'] / 2, steps=18)
        p.wait_for_timeout(300)
        p.mouse.click(row['x'] + 120, row['y'] + row['height'] / 2)
        p.wait_for_selector('.ibx-draft-area')
        p.evaluate(CAPTION, "Dina's thread, and the reply it drafted")
        p.wait_for_timeout(1800)
        ta = p.locator('.ibx-draft-area')
        ta.scroll_into_view_if_needed()
        p.wait_for_timeout(600)
        tb = ta.bounding_box()
        p.mouse.move(tb['x'] + tb['width'] * .4, tb['y'] + tb['height'] / 2, steps=14)
        p.wait_for_timeout(1400)
        btn = p.locator('.ibx-approve-send').bounding_box()
        p.mouse.move(btn['x'] + btn['width'] * .9, btn['y'] + btn['height'] * .75, steps=16)
        p.evaluate(CAPTION, 'Nothing is sent until you press Approve')
        p.mouse.move(btn['x'] + btn['width'] * .92, btn['y'] + btn['height'] * .8)
        p.wait_for_timeout(4000)
        ctx.close()
        b.close()
    return next(vid.glob('*.webm')), start

def to_gif(webm, start):
    fc = (f'[0:v]trim=start={start:.2f},setpts=PTS-STARTPTS,fps={FPS},scale=1000:-1:flags=lanczos,split[x][y];'
          '[x]palettegen=max_colors=96:stats_mode=diff[p];'
          '[y][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle')
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(webm), '-filter_complex', fc,
                    '-loop', '0', str(OUT)], check=True)
    print('wrote', OUT, round(OUT.stat().st_size / 1e6, 2), 'MB')

def main():
    clone = WORK / 'clone'
    subprocess.run(['git', 'clone', '-q', str(REPO), str(clone)], check=True)
    state = clone / 'journal' / 'state'
    state.mkdir(parents=True, exist_ok=True)
    (state / 'inbox.json').write_text(json.dumps(sample_inbox(), indent=1), encoding='utf-8')
    # PYTHONIOENCODING: server.py prints emoji at startup, which crashes on a cp1252 Windows stdout.
    env = dict(os.environ, DASHBOARD_PORT=str(PORT), PYTHONIOENCODING='utf-8')
    srv = subprocess.Popen([sys.executable, 'dashboard/server.py'], cwd=str(clone), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f'http://127.0.0.1:{PORT}'
        wait_up(base + '/api/inbox')
        to_gif(*record(base))
    finally:
        srv.terminate()

if __name__ == '__main__':
    main()
