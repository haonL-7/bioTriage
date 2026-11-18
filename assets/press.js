/**
 * 物理按压：pointerdown 进入压缩态，抬起后播放衰减振荡回弹。
 */
(function () {
  var SEL = 'button, .btn, .star-btn, a.nav-link, .side-item, .ch, .entry-card, .chip, .src-badge, .aa-domain';
  var KEYS = { ' ': 1, Enter: 1 };   // 空格 / 回车触发
  var current = null;

  function isTarget(el) {
    return el && el.closest && el.closest(SEL);
  }

  function press(el) {
    if (!el || el === current) { return; }
    release();
    current = el;
    el.classList.remove('spring');
    el.classList.add('press');
  }

  function release() {
    var el = current;
    current = null;
    if (!el) { return; }
    el.classList.remove('press');
    el.classList.add('spring');
    var done = function () {
      el.classList.remove('spring');
      el.removeEventListener('animationend', done);
    };
    el.addEventListener('animationend', done);
    // 兜底：动画被禁用或未触发时也能恢复
    setTimeout(done, 700);
  }

  document.addEventListener('pointerdown', function (e) {
    var el = isTarget(e.target);
    if (el && !e.button) { press(el); }
  }, true);

  document.addEventListener('pointerup', release, true);
  document.addEventListener('pointercancel', release, true);
  document.addEventListener('pointerleave', release, true);

  document.addEventListener('keydown', function (e) {
    if (!KEYS[e.key]) { return; }
    var el = isTarget(e.target);
    if (el) { press(el); }
  }, true);
  document.addEventListener('keyup', function (e) {
    if (KEYS[e.key]) { release(); }
  }, true);
})();
