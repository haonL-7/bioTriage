# -*- coding: utf-8 -*-
"""跨频道文献日报/周报：聚合 8 个 lightweight 频道的产出，生成静态页。

    python scripts/build_digest.py --dry-run
    python scripts/build_digest.py

与 evidence 管线无关：本脚本只读 _site/<channel>/data/papers.json，
不涉及 SCFA 主题，也不调用任何模型。
"""
import argparse
import datetime
import glob
import html
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, '_site')
CHANNELS = [
    ('computational-genomics', 'Computational Genomics'),
    ('metagenomics-informatics', 'Metagenomics'),
    ('structural-bioinformatics', 'Structural Bioinformatics'),
    ('systems-biology', 'Systems Biology'),
    ('ml-biology', 'Machine Learning'),
    ('single-cell-omics', 'Single-cell & Spatial'),
    ('databases-knowledge-graphs', 'Databases & KG'),
    ('ai-drug-discovery', 'AI Drug Discovery'),
]
SIGN_RANK = {'High': 3, 'Medium': 2, 'Low': 1}
MON = {m: i + 1 for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'])}

CSS = """:root { --bg:#fff; --bg-alt:#f7f8fa; --ink:#16181c; --ink-md:#4a5058; --ink-xlt:#8a9199;
  --border:#e3e6ea; --accent:#1a4d80; --max:820px; --serif: Georgia,'Times New Roman',serif;
  --sans: -apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; }
:root[data-theme="dark"] { color-scheme: dark; --bg:#14161a; --bg-alt:#191c21; --ink:#e9e7e2;
  --ink-md:#b8b5ae; --ink-xlt:#6e6b66; --border:#2a2e35; --accent:#9cc6ea; }
* { box-sizing:border-box; margin:0; padding:0; }
html.theme-anim, html.theme-anim * { transition: background-color .45s cubic-bezier(.4,0,.2,1),
  color .45s cubic-bezier(.4,0,.2,1), border-color .45s cubic-bezier(.4,0,.2,1) !important; }
body { font-family:var(--sans); background:var(--bg); color:var(--ink); line-height:1.65; font-size:15px; }
a { color:var(--accent); text-decoration:none; }
.top-bar { display:flex; justify-content:space-between; align-items:center; gap:12px;
  padding:10px 20px; border-bottom:1px solid var(--border); font-size:13px; }
.tb-brand { display:flex; align-items:center; gap:10px; color:var(--ink-md); }
.mini-logo { font-family:var(--serif); font-size:15px; color:var(--ink); }
.mini-logo em { font-style:normal; color:var(--accent); }
.wrap { max-width:var(--max); margin:0 auto; padding:32px 20px 60px; }
h1 { font-family:var(--serif); font-size:26px; font-weight:400; margin-bottom:6px; }
.lede { color:var(--ink-md); font-size:14px; margin-bottom:4px; }
.meta { color:var(--ink-xlt); font-size:12px; margin-bottom:28px; }
.ch { font-family:var(--sans); font-size:12px; letter-spacing:.6px; text-transform:uppercase;
  color:var(--ink-xlt); border-top:1px solid var(--border); padding-top:14px; margin:28px 0 12px; }
.item { padding:14px 0; border-bottom:1px solid var(--border); }
.item h3 { font-family:var(--serif); font-size:15px; font-weight:400; line-height:1.45; margin-bottom:4px; }
.item h3 a { color:var(--ink); }
.item h3 a:hover { color:var(--accent); }
.row { display:flex; gap:10px; align-items:center; font-size:11.5px; color:var(--ink-xlt); margin-bottom:4px; }
.sig { padding:1px 7px; border-radius:2px; font-size:10px; text-transform:uppercase; letter-spacing:.4px; }
.sig-high { background:#e8f5e9; color:#1a6b3c; } .sig-medium { background:#f2f3f5; color:#4a5058; }
.sig-low { background:#faf1f1; color:#a09090; }
.sum { font-size:13px; color:var(--ink-md); }
.theme-btn, .nav-link { font-family:var(--sans); font-size:12px; color:var(--ink-xlt);
  background:transparent; border:1px solid var(--border); border-radius:999px; padding:4px 10px; cursor:pointer; }
footer { border-top:1px solid var(--border); color:var(--ink-xlt); font-size:11.5px; text-align:center;
  padding:22px 20px; }
.empty { color:var(--ink-xlt); font-size:13px; padding:20px 0; }
.layout { display: flex; align-items: flex-start; max-width: 1100px; margin: 0 auto; }
.sidebar { position: sticky; top: 60px; width: 200px; flex: 0 0 200px; padding: 28px 12px 28px 20px; }
.side-nav { display: flex; flex-direction: column; gap: 2px; }
.side-label { font-size: 10.5px; letter-spacing: 1.2px; text-transform: uppercase; color: var(--ink-xlt); padding: 0 10px 8px; }
.side-item { display: block; padding: 8px 10px; border-radius: 4px; font-size: 13px; color: var(--ink-md); }
.side-item:hover { background: var(--bg-alt); color: var(--ink); }
.side-item.is-active { background: var(--accent); color: #fff; }
.page { flex: 1; min-width: 0; }
@media (max-width: 900px) {
  .layout { display: block; }
  .sidebar { position: static; width: 100%; flex: none; padding: 16px 20px 0; }
}
@media (max-width:640px) { h1 { font-size:21px; } }
"""

HEAD_JS = """<script>(function(){try{var s=localStorage.getItem('bt-theme');var sys=window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches;document.documentElement.dataset.theme=(s==='dark'||s==='light')?s:(sys?'dark':'light');}catch(e){}})();</script>"""

TOGGLE_JS = """<script>
(function () {
  var btn = document.getElementById('theme-toggle');
  if (!btn) { return; }
  var mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  var ICONS = { light: '\\u2600', dark: '\\u263e', auto: '\\u25d0' };
  function stored() { try { var v = localStorage.getItem('bt-theme'); return (v === 'dark' || v === 'light') ? v : 'auto'; } catch (e) { return 'auto'; } }
  function resolve(m) { return (m === 'dark' || m === 'light') ? m : ((mq && mq.matches) ? 'dark' : 'light'); }
  function paint(mode, soft) {
    var root = document.documentElement;
    if (soft) { root.classList.add('theme-anim'); window.setTimeout(function () { root.classList.remove('theme-anim'); }, 620); }
    root.dataset.theme = resolve(mode);
    btn.textContent = ICONS[mode] || ICONS.auto;
  }
  btn.addEventListener('click', function () {
    var order = ['light', 'dark', 'auto'];
    var next = order[(order.indexOf(stored()) + 1) % order.length];
    try { if (next === 'auto') { localStorage.removeItem('bt-theme'); } else { localStorage.setItem('bt-theme', next); } } catch (e) {}
    paint(next, true);
  });
  if (mq && mq.addEventListener) { mq.addEventListener('change', function () { if (stored() === 'auto') { paint('auto', true); } }); }
  paint(stored());
})();
</script>"""


def parse_date(value):
    s = str(value or '')[:11]
    m = re.match(r'^(\d{4})-(\w{3})-(\d{1,2})', s)
    if m and m.group(2).lower() in MON:
        try:
            return datetime.date(int(m.group(1)), MON[m.group(2).lower()], int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def load(window_days, today, per_channel=6, fallback_per_channel=2):
    """按窗口取每频道论文；窗口内没有的频道退化为其最新几条，避免整页空白。"""
    cutoff = today - datetime.timedelta(days=window_days)
    by_channel, newest = {}, None
    for key, label in CHANNELS:
        path = os.path.join(SITE, key, 'data', 'papers.json')
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding='utf-8') as f:
                rows = json.load(f).get('papers', [])
        except (ValueError, OSError):
            continue
        dated = []
        for row in rows:
            d = parse_date(row.get('pub_date'))
            if d is not None:
                dated.append((d, row))
                if newest is None or d > newest:
                    newest = d
        dated.sort(key=lambda x: x[0], reverse=True)
        picked = [r for d, r in dated if cutoff <= d <= today][:per_channel]
        if not picked:
            picked = [r for _, r in dated[:fallback_per_channel]]
        if picked:
            by_channel[label] = picked
    return by_channel, newest


def sidebar_html(root, active):
    items = [
        ('Home', root, 'home'),
        ('Gut SCFA Monitor', root + 'evidence/', 'evidence'),
        ('Literature Daily', root + 'daily/', 'daily'),
        ('Literature Weekly', root + 'weekly/', 'weekly'),
        ('Methodology', root + 'ms1/', 'method'),
        ('My Stars', root + 'account/', 'stars'),
    ]
    out = ['<aside class="sidebar"><nav class="side-nav">',
           '<span class="side-label">Navigate</span>']
    for label, href, key in items:
        cls = 'side-item is-active' if key == active else 'side-item'
        out.append('<a class="%s" href="%s">%s</a>' % (cls, href, html.escape(label)))
    out.append('<a class="side-item" href="mailto:haon.liu@outlook.com">Contact</a>')
    out.append('</nav></aside>')
    return '\n'.join(out)


def render(title, lede, meta, by_channel, today, nav, active):
    total = sum(len(v) for v in by_channel.values())
    blocks = []
    for label, rows in by_channel.items():
        items = []
        for r in rows:
            sig = str(r.get('significance') or 'Medium')
            items.append(
                '<article class="item">'
                '<div class="row"><span class="sig sig-%s">%s</span><span>%s</span><span>%s</span></div>'
                '<h3><a href="%s" target="_blank" rel="noopener">%s</a></h3>'
                '<p class="sum">%s</p></article>' % (
                    sig.lower(), html.escape(sig), html.escape(str(r.get('journal') or '')),
                    html.escape(str(r.get('pub_date') or '')),
                    html.escape(str(r.get('url') or '#'), quote=True), html.escape(str(r.get('title') or '')),
                    html.escape(str(r.get('summary') or '')[:320])))
        blocks.append('<div class="ch">%s</div>%s' % (html.escape(label), ''.join(items)))
    body = ''.join(blocks) or '<p class="empty">No papers in this window.</p>'
    return """<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>%(title)s — bioTriage-Open</title>
%(head_js)s
<style>%(css)s</style>
</head>
<body>
<nav class="top-bar">
  <span class="tb-brand"><a href="%(root)s"><span class="mini-logo">bioTriage<em>-Open</em></span></a><span>Cut through the noise. Anchor in evidence.</span></span>
  <span style="display:flex;gap:8px;align-items:center">
    <button id="theme-toggle" class="theme-btn" type="button" aria-label="Theme">&#9680;</button>
    <a class="nav-link" href="%(root)sdaily/">Daily</a>
    <a class="nav-link" href="%(root)sweekly/">Weekly</a>
    <a class="nav-link" href="mailto:haon.liu@outlook.com">Contact</a>
  </span>
</nav>
<div class="layout">
%(sidebar)s
<div class="page">
<div class="wrap">
  <h1>%(title)s</h1>
  <p class="lede">%(lede)s</p>
  <p class="meta">%(meta)s</p>
  %(body)s
</div>
</div>
</div>
<footer>&copy; 2026 bioTriage-Open &middot; Independent project &middot; AI-curated summaries; verify before citing.</footer>
%(toggle_js)s
</body>
</html>""" % dict(title=title, lede=lede, total=total, nch=len(by_channel), today=today.isoformat(), meta=meta,
                   body=body, css=CSS, head_js=HEAD_JS, toggle_js=TOGGLE_JS, root=nav,
                   sidebar=sidebar_html(nav, active))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    today = datetime.date.today()

    daily, newest_d = load(3, today)
    weekly, newest_w = load(7, today)
    newest = newest_d or newest_w
    fresh = ('newest paper: %s' % newest.isoformat()) if newest else 'no dated papers found'
    pages = {
        'daily': render('Literature Daily',
                        'Newest papers from every channel, highest significance first.',
                        'daily window: 3 days &middot; %s &middot; generated %s' % (fresh, today.isoformat()),
                        daily, today, '../', 'daily'),
        'weekly': render('Literature Weekly',
                         'The week across every channel, highest significance first.',
                         'weekly window: 7 days &middot; %s &middot; generated %s' % (fresh, today.isoformat()),
                         weekly, today, '../', 'weekly'),
    }
    for name, out in pages.items():
        n = sum(len(v) for v in (daily if name == 'daily' else weekly).values())
        print('  %-7s channels=%-2d papers=%d bytes=%d' % (
            name, len(daily if name == 'daily' else weekly), n, len(out.encode('utf-8'))))
        if not args.dry_run:
            d = os.path.join(SITE, name)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, 'index.html'), 'w', encoding='utf-8') as f:
                f.write(out)
    print('DRY-RUN' if args.dry_run else 'WRITTEN -> _site/daily, _site/weekly')


if __name__ == '__main__':
    main()
