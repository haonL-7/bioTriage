/**
 * Cell field hero — bokeh/particle 思路：
 * 大量尺寸不一的"细胞"缓慢上浮 + 正弦横摆，出屏后回收到下方，
 * 每帧按径向渐变绘制，模拟显微镜视野里的流动感。
 */
(function () {
  var canvas = document.getElementById('cellHeroCanvas');
  if (!canvas) { return; }
  var ctx = canvas.getContext('2d');
  var DPR = Math.min(2, window.devicePixelRatio || 1);
  var W = 0, H = 0, cells = [], N = 16;
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function rnd(a, b) { return a + Math.random() * (b - a); }

  function make(y) {
    var r = rnd(26, 108);
    return {
      x: rnd(0, W),
      y: y === undefined ? rnd(0, H) : y,
      r: r,
      vy: -rnd(4, 13) * (r / 80),          // 大细胞更慢，形成纵深
      swayA: rnd(6, 22),
      swayW: rnd(0.00025, 0.0006),
      phase: rnd(0, 6.283),
      a: rnd(0.05, 0.15),
      hue: rnd(148, 176),
      nuc: Math.random() < 0.55 ? rnd(2.5, 7) : 0
    };
  }

  function resize() {
    var rect = canvas.parentElement.getBoundingClientRect();
    W = rect.width; H = rect.height;
    canvas.width = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }

  function init() {
    resize();
    cells = [];
    for (var i = 0; i < N; i++) { cells.push(make()); }
  }

  function frame(t) {
    ctx.clearRect(0, 0, W, H);
    for (var i = 0; i < cells.length; i++) {
      var c = cells[i];
      if (!reduce) { c.y += c.vy * 0.016; }
      var x = c.x + Math.sin(t * c.swayW + c.phase) * c.swayA;

      if (c.y + c.r < -30) {
        c.y = H + c.r + rnd(0, 80);
        c.x = rnd(0, W);
      }

      var g = ctx.createRadialGradient(x, c.y, c.r * 0.08, x, c.y, c.r);
      g.addColorStop(0, 'hsla(' + c.hue + ',58%,74%,' + (c.a * 1.6) + ')');
      g.addColorStop(0.55, 'hsla(' + c.hue + ',54%,56%,' + (c.a * 0.75) + ')');
      g.addColorStop(1, 'hsla(' + c.hue + ',50%,46%,0)');
      ctx.beginPath(); ctx.arc(x, c.y, c.r, 0, 6.2832); ctx.fillStyle = g; ctx.fill();

      if (c.nuc) {
        ctx.beginPath();
        ctx.arc(x + c.r * 0.14, c.y - c.r * 0.12, c.nuc, 0, 6.2832);
        ctx.fillStyle = 'hsla(' + c.hue + ',70%,90%,' + Math.min(0.9, c.a * 2.6) + ')';
        ctx.fill();
      }
    }
    requestAnimationFrame(frame);
  }

  init();
  requestAnimationFrame(frame);
  window.addEventListener('resize', init);
})();
