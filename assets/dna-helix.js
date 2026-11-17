/**
 * 页脚 DNA：两条**斜向**双螺旋，交叉成 X
 * 每条螺旋 = 一条斜轴 + 沿轴做正弦扭转的两条链 + 之间的碱基横档。
 * 自转：链的粗细/亮度由 cos(扭角 + ωt) 驱动。
 */
(function () {
  var canvas = document.getElementById('dnaHelix');
  if (!canvas) { return; }
  var ctx = canvas.getContext('2d');
  var DPR = Math.min(2, window.devicePixelRatio || 1);
  var W = 0, H = 0, MID = 0;

  var TWIST = 150;      // 沿轴的扭转周期(px)，越小螺旋越密
  var SPEED = 0.0009;   // 自转速度
  var SEP = 13;         // 两条链相对轴线的偏移幅度
  var STEP = 3;

  function isDark() { return document.documentElement.dataset.theme === 'dark'; }

  function resize() {
    var rect = canvas.parentElement.getBoundingClientRect();
    W = Math.max(1, Math.round(rect.width));
    H = Math.max(1, Math.round(rect.height));
    canvas.width = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    MID = H / 2;
  }

  // dir: -1 = 从左上到右下；+1 = 从左下到右上（两者交叉成 X）
  function drawHelix(dir, t) {
    var y0 = MID - dir * (H * 0.36);
    var y1 = MID + dir * (H * 0.36);
    var dx = W, dy = y1 - y0;
    var len = Math.hypot(dx, dy);
    var ux = dx / len, uy = dy / len;          // 轴向单位向量
    var nx = -uy, ny = ux;                     // 垂直方向（链的偏移方向）
    var k = (2 * Math.PI) / TWIST;
    var omega = t * SPEED;

    for (var s = 0; s < len; s += STEP) {
      var cx = s * ux, cy = y0 + s * uy;       // 轴线上的点
      var ang = k * s + omega;
      var off = SEP * Math.sin(ang);
      var depth = Math.cos(ang);               // >0 表示外侧(=视觉前方)

      var ax = cx + nx * off, ay = cy + ny * off;
      var bx = cx - nx * off, by = cy - ny * off;

      var wBase = 1.6 + 2.4 * Math.max(0, depth);
      var aBase = 0.35 + 0.6 * Math.max(0, depth);
      var aBack = 0.35 + 0.6 * Math.max(0, -depth);

      // 碱基横档（每 9 步一根）
      if (Math.round(s / STEP) % 9 === 0) {
        ctx.beginPath();
        ctx.moveTo(ax, ay); ctx.lineTo(bx, by);
        ctx.strokeStyle = 'rgba(56,183,159,' + (0.18 + 0.45 * Math.abs(depth)).toFixed(3) + ')';
        ctx.lineWidth = 1 + 0.8 * Math.abs(depth);
        ctx.stroke();
      }

      // 两条链（逐段）
      var s2 = Math.min(len, s + STEP);
      var ang2 = k * s2 + omega;
      var off2 = SEP * Math.sin(ang2);
      var ax2 = s2 * ux + nx * off2, ay2 = y0 + s2 * uy + ny * off2;
      var bx2 = s2 * ux - nx * off2, by2 = y0 + s2 * uy - ny * off2;

      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(ax2, ay2);
      ctx.strokeStyle = 'rgba(143,240,221,' + aBase.toFixed(3) + ')';
      ctx.lineWidth = wBase; ctx.lineCap = 'round'; ctx.stroke();

      ctx.beginPath(); ctx.moveTo(bx, by); ctx.lineTo(bx2, by2);
      ctx.strokeStyle = 'rgba(47,158,138,' + aBack.toFixed(3) + ')';
      ctx.lineWidth = 1.6 + 2.4 * Math.max(0, -depth); ctx.stroke();
    }
  }

  function frame(t) {
    requestAnimationFrame(frame);
    ctx.clearRect(0, 0, W, H);
    if (!isDark()) { return; }
    drawHelix(1, t);    // 左下 -> 右上
    drawHelix(-1, t);   // 左上 -> 右下
  }

  resize();
  window.addEventListener("resize", resize);
  requestAnimationFrame(frame);
})();
