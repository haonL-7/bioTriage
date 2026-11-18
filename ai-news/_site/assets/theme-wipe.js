/**
 * 主题切换：上下两片幕布向中间合拢，前沿带一条青色高光，
 * 合拢瞬间完成换肤，随后整体淡出。避免全屏纯白/纯黑闪屏。
 */
(function () {
  var CSS = [
    '.theme-wipe{position:fixed;inset:0;z-index:99999;pointer-events:none;opacity:0}',
    '.theme-wipe i{position:absolute;left:0;right:0;height:50%;background:inherit;transform:scaleY(0);will-change:transform}',
    '.theme-wipe i.top{top:0;transform-origin:top center}',
    '.theme-wipe i.bot{bottom:0;transform-origin:bottom center}',
    '.theme-wipe i::after{content:"";position:absolute;left:0;right:0;height:1px;',
    'background:linear-gradient(90deg,rgba(127,227,204,0),rgba(127,227,204,.85) 45%,rgba(127,227,204,.85) 55%,rgba(127,227,204,0));',
    'box-shadow:0 0 12px rgba(127,227,204,.55)}',
    '.theme-wipe i.top::after{bottom:-1px}',
    '.theme-wipe i.bot::after{top:-1px}',
    '.theme-wipe.run{animation:tw-fade 1040ms cubic-bezier(.33,0,.2,1) forwards !important}',
    '.theme-wipe.run i{animation:tw-half 1040ms cubic-bezier(.33,0,.2,1) forwards !important}',
    '@keyframes tw-half{0%{transform:scaleY(0)}42%{transform:scaleY(1)}100%{transform:scaleY(1)}}',
    '@keyframes tw-fade{0%{opacity:1}54%{opacity:1}100%{opacity:0}}'
  ].join('');

  var style = document.createElement('style');
  style.textContent = CSS;
  document.head.appendChild(style);

  var overlay = document.createElement('div');
  overlay.className = 'theme-wipe';
  overlay.innerHTML = '<i class="top"></i><i class="bot"></i>';

  function paint(isDark) {
    overlay.style.background = isDark
      ? 'linear-gradient(160deg, #171a20 0%, #14161a 55%, #101318 100%)'
      : 'linear-gradient(160deg, #ffffff 0%, #f8fafc 55%, #f2f6fa 100%)';
  }

  function mount() {
    if (!document.body) { requestAnimationFrame(mount); return; }
    document.body.appendChild(overlay);
    paint(document.documentElement.dataset.theme === 'dark');
  }

  var obs = new MutationObserver(function (records) {
    for (var i = 0; i < records.length; i++) {
      if (records[i].attributeName !== 'data-theme') { continue; }
      paint(document.documentElement.dataset.theme === 'dark');
      overlay.classList.remove('run');
      void overlay.offsetWidth;
      overlay.classList.add('run');
    }
  });

  if (document.body) { mount(); } else { document.addEventListener('DOMContentLoaded', mount); }
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
})();
