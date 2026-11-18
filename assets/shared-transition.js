/**
 * 共享元素转场：
 * 点击卡片 → 给卡片挂 view-transition-name="card"，
 * 目标页读取 sessionStorage 标记，把主内容容器挂同名，
 * 浏览器据此在两次文档之间做连续缩放补间。
 */
(function () {
  var KEY = 'bt-vt-card';
  var supportsVT = typeof document.startViewTransition === 'function' || CSS.supports('view-transition-name: none');
  if (!supportsVT) { return; }

  function hostOf(el) {
    return el.closest('.ch, .paper-card, .entry-card, .stat-cell');
  }

  document.addEventListener('click', function (e) {
    var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (!a) { return; }
    var host = hostOf(a);
    if (!host) { return; }
    if (a.target === '_blank' || e.metaKey || e.ctrlKey || e.shiftKey) { return; }
    var href = a.getAttribute('href') || '';
    if (!href || href.charAt(0) === '#' || href.indexOf('mailto:') === 0) { return; }
    try { sessionStorage.setItem(KEY, '1'); } catch (err) {}
    host.style.viewTransitionName = 'card';
  }, true);

  // 目标页：把主内容区接管为同一个共享元素
  function reveal() {
    var flag = null;
    try { flag = sessionStorage.getItem(KEY); sessionStorage.removeItem(KEY); } catch (err) {}
    if (!flag) { return; }
    var target = document.querySelector('.wrap, .page, .container, main');
    if (target) { target.style.viewTransitionName = 'card'; }
  }
  window.addEventListener('pagereveal', reveal);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', reveal);
  } else {
    reveal();
  }
})();
