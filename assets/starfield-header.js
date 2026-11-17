/**
 * Starfield Header v8 — big comets + top-down glass fade + cosmic ambient glow
 */
(function () {
  var canvas, ctx, W, H, H2, animId;
  var mouse = { x: 0.5, y: 0.5, tx: 0.5, ty: 0.5 };
  var explosions = [], comets = [];
  var lastComet = 0, cometCooldown = 4000;
  var lastShowerCheck = 0, inShower = false, showerEnd = 0;

  var layers = [
    { count: 200, rMin: 0.4, rMax: 1.2, parallax: 0.6, stars: [] },
    { count: 100, rMin: 0.8, rMax: 2.2, parallax: 0.4, stars: [] },
    { count: 40,  rMin: 1.5, rMax: 3.0, parallax: 0.18, stars: [] }
  ];

  var cons = [
    { name: 'Orion', bx: 0.68, by: 0.33, scale: 0.55,
      pts: [[0,0],[9,2],[16,1],[6,9],[-10,17],[-3,4],[4,-6]],
      lines: [[0,1],[1,2],[3,4],[0,3],[1,4],[5,6]] },
    { name: 'Big Dipper', bx: 0.22, by: 0.22, scale: 0.45,
      pts: [[0,0],[11,-3],[20,1],[31,6],[25,16],[12,13],[3,9]],
      lines: [[0,1],[1,2],[2,3],[3,4],[4,5],[5,6],[6,0]] },
    { name: 'Cassiopeia', bx: 0.4, by: 0.58, scale: 0.45,
      pts: [[0,6],[7,0],[14,9],[21,0],[28,6]],
      lines: [[0,1],[1,2],[2,3],[3,4]] },
    { name: 'Pleiades', bx: 0.75, by: 0.28, scale: 0.4,
      pts: [[0,0],[3,3],[-2,5],[6,-2],[3,7],[-4,2],[5,4],[-3,-3],[2,-4]],
      lines: [] },
    { name: 'Andromeda', bx: 0.3, by: 0.48, scale: 0.45,
      pts: [[0,0],[4,2],[8,-1],[12,3],[16,1],[20,4]],
      lines: [[0,1],[1,2],[2,3],[3,4],[4,5]] }
  ];

  function init() {
    canvas = document.getElementById('sf-canvas');
    if (!canvas) return;
    ctx = canvas.getContext('2d');
    var header = document.querySelector('.starfield-header');
    var el = header || canvas;
    el.addEventListener('mousemove', function (e) {
      var r = canvas.getBoundingClientRect();
      mouse.tx = (e.clientX - r.left) / W;
      mouse.ty = (e.clientY - r.top) / H2;
    });
    el.addEventListener('click', function (e) {
      var r = canvas.getBoundingClientRect();
      triggerSupernova(e.clientX - r.left, e.clientY - r.top);
    });
    resize();
    for (var l = 0; l < layers.length; l++) createStars(l);
    window.addEventListener('resize', function () {
      for (var l = 0; l < layers.length; l++) layers[l].stars.length = 0;
      resize();
      for (var l = 0; l < layers.length; l++) createStars(l);
    });
    animate();
  }

  function resize() {
    var hdr = document.querySelector('.starfield-header');
    W = hdr ? hdr.offsetWidth : canvas.parentElement.offsetWidth;
    H2 = hdr ? hdr.offsetHeight : canvas.parentElement.offsetHeight;
    H = Math.round(H2 * 1.25);
    canvas.width = W; canvas.height = H;
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  }

  function createStars(layerIdx) {
    var layer = layers[layerIdx];
    for (var i = 0; i < layer.count; i++) {
      layer.stars.push({
        x: Math.random() * W, y: Math.random() * H,
        r: Math.random() * (layer.rMax - layer.rMin) + layer.rMin,
        twinkle: Math.random() * Math.PI * 2,
        ts: Math.random() * 0.01 + 0.003,
        hue: Math.random() < 0.08 ? 40 + Math.random() * 20 : 210 + Math.random() * 30,
        rebirth: 0
      });
    }
  }

  // ============ COMETS — shooting stars, right-to-left with tilt ============

  function spawnComet(now) {
    var angle = (Math.random() - 0.5) * 0.25; // ±~7° tilt
    var speed = 800 + Math.random() * 500;    // 800-1300 px/s
    comets.push({
      x: W + 40,
      y: Math.random() * H2 * 0.7 + H2 * 0.05,
      vx: -Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      life: 0,
      maxLife: 0.6 + Math.random() * 0.5,
      hue: 200 + Math.random() * 30,
      r: 1 + Math.random(),
      startTime: now,
      tail: []
    });
  }

  function spawnMeteorShower(now) {
    inShower = true;
    showerEnd = now + 3500;
    // Radiant point — somewhere in upper-right sky
    var rx = W * (0.7 + Math.random() * 0.3);
    var ry = H2 * (0.05 + Math.random() * 0.2);
    var baseAngle = Math.atan2(ry - H2 * 0.8, rx - (-100)); // toward bottom-left
    var count = 12 + Math.floor(Math.random() * 14);
    for (var i = 0; i < count; i++) {
      var spread = (Math.random() - 0.5) * 0.5; // spread from radiant
      var angle = baseAngle + spread;
      var speed = 500 + Math.random() * 600;
      var dist = 200 + Math.random() * 400;
      comets.push({
        x: rx + Math.cos(angle - spread * 0.5) * dist,
        y: ry + Math.sin(angle - spread * 0.5) * dist * 0.5,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed,
        life: 0,
        maxLife: 0.5 + Math.random() * 0.5,
        hue: 195 + Math.random() * 35,
        r: 0.8 + Math.random() * 1.2,
        startTime: now + i * 60 + Math.random() * 80,
        tail: []
      });
    }
  }

  function drawComets(t) {
    for (var c = comets.length - 1; c >= 0; c--) {
      var cm = comets[c];
      if (t < cm.startTime) continue;
      cm.life += 0.016;
      var progress = cm.life / cm.maxLife;
      if (progress > 1 || cm.x < -120 || cm.y < -120 || cm.y > H + 120) {
        comets.splice(c, 1); continue;
      }

      cm.x += cm.vx * 0.016;
      cm.y += cm.vy * 0.016;
      cm.tail.push({ x: cm.x, y: cm.y });
      if (cm.tail.length > 30) cm.tail.shift();

      var fadeIn = Math.min(1, progress * 4);
      var fadeOut = progress > 0.6 ? 1 - (progress - 0.6) / 0.4 : 1;
      var alpha = fadeIn * fadeOut;

      // Elegant thin tail — sharp core + soft glow
      for (var ti = 1; ti < cm.tail.length; ti++) {
        var t0 = cm.tail[ti - 1], t1 = cm.tail[ti];
        var tRatio = ti / cm.tail.length;

        // Soft wide glow
        ctx.beginPath();
        ctx.moveTo(t0.x, t0.y);
        ctx.lineTo(t1.x, t1.y);
        ctx.strokeStyle = 'hsla(' + cm.hue + ',40%,80%,' + (tRatio * alpha * 0.35) + ')';
        ctx.lineWidth = cm.r * tRatio * 6;
        ctx.lineCap = 'round';
        ctx.stroke();

        // Sharp bright core
        ctx.beginPath();
        ctx.moveTo(t0.x, t0.y);
        ctx.lineTo(t1.x, t1.y);
        ctx.strokeStyle = 'hsla(' + cm.hue + ',20%,95%,' + (tRatio * alpha * 0.7) + ')';
        ctx.lineWidth = cm.r * tRatio * 1.2;
        ctx.stroke();
        ctx.lineCap = 'butt';
      }

      // Bright point head
      var grad = ctx.createRadialGradient(cm.x, cm.y, 0, cm.x, cm.y, cm.r * 5);
      grad.addColorStop(0, 'rgba(255,255,255,' + (alpha * 0.95) + ')');
      grad.addColorStop(0.15, 'hsla(' + cm.hue + ',60%,90%,' + (alpha * 0.5) + ')');
      grad.addColorStop(0.5, 'hsla(' + cm.hue + ',40%,70%,' + (alpha * 0.1) + ')');
      grad.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.beginPath();
      ctx.arc(cm.x, cm.y, cm.r * 5, 0, Math.PI * 2);
      ctx.fillStyle = grad;
      ctx.fill();
    }
  }

  // ============ SUPERNOVA (unchanged) ============

  function triggerSupernova(cx, cy) {
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) { return; }
    var best = null, bestDist = 45;
    for (var l = 0; l < layers.length; l++) {
      for (var i = 0; i < layers[l].stars.length; i++) {
        var s = layers[l].stars[i];
        if (s.rebirth > 0) continue;
        var px = (mouse.x - 0.5) * 100 * layers[l].parallax;
        var py = (mouse.y - 0.5) * 100 * layers[l].parallax;
        var d = Math.hypot(cx - (s.x + px), cy - (s.y + py));
        if (d < bestDist && s.r > 1.2) { bestDist = d; best = { star: s, layer: l }; }
      }
    }
    if (best) {
      var px = (mouse.x - 0.5) * 100 * layers[best.layer].parallax;
      var py = (mouse.y - 0.5) * 100 * layers[best.layer].parallax;
      var bx = best.star.x + px, by = best.star.y + py;
      best.star.rebirth = 1; best.star.rx = Math.random() * W; best.star.ry = Math.random() * H; best.star.r0 = best.star.r;
      while (explosions.length >= 3) { explosions.shift(); }
      explosions.push({ x: bx, y: by, life: 0, maxLife: 2.5, hue: best.star.hue,
        rings: [{ r:0,mR:50,a:0.7,w:3,d:0},{r:0,mR:90,a:0.5,w:2,d:0.1},{r:0,mR:160,a:0.3,w:1,d:0.25}],
        particles: createExplosionParticles(bx, by, best.star.hue) });
    }
  }

  function createExplosionParticles(x, y, hue) {
    var p = [];
    for (var i = 0; i < 110; i++) {
      var a = Math.random() * Math.PI * 2, s = Math.random() * 80 + 30;
      p.push({x,y,vx:Math.cos(a)*s,vy:Math.sin(a)*s,life:1,decay:Math.random()*0.5+0.5,r:Math.random()*3+1.5,hue:hue+(Math.random()-0.5)*40,trail:[]});
    }
    for (var i = 0; i < 32; i++) {
      var a = Math.random() * Math.PI * 2;
      p.push({x,y,vx:Math.cos(a)*(Math.random()*25+5),vy:Math.sin(a)*(Math.random()*25+5),life:1,decay:Math.random()*0.3+0.2,r:Math.random()*2+2,hue:30+Math.random()*20,trail:[]});
    }
    return p;
  }

  function eo(t){return 1-Math.pow(1-t,3)}
  function eiq(t){return t<0.5?2*t*t:-1+(4-2*t)*t}

  function drawExplosions() {
    ctx.globalCompositeOperation = 'lighter';
    for (var e = explosions.length - 1; e >= 0; e--) {
      var ex = explosions[e]; ex.life += 0.016;
      var pr = ex.life / ex.maxLife; if (pr > 1) { explosions.splice(e,1); continue; }
      var ga = pr < 0.15 ? eiq(pr/0.15) : pr > 0.7 ? 1-eo((pr-0.7)/0.3) : 1;
      for (var r = 0; r < ex.rings.length; r++) {
        var ri = ex.rings[r], rp = Math.max(0,(ex.life-ri.d)/(ex.maxLife-ri.d));
        if (rp<=0) continue;
        ri.r = ri.mR*eo(Math.min(1,rp)); var ra = ri.a*ga*(1-eo(rp));
        ctx.beginPath();ctx.arc(ex.x,ex.y,ri.r,0,Math.PI*2);
        ctx.strokeStyle='rgba(255,230,200,'+ra+')';ctx.lineWidth=ri.w*(1-rp*0.7);ctx.stroke();
        if(r===0){ctx.beginPath();ctx.arc(ex.x,ex.y,ri.r*0.7,0,Math.PI*2);ctx.strokeStyle='rgba(180,210,255,'+(ra*0.6)+')';ctx.lineWidth=ri.w*1.2*(1-rp*0.7);ctx.stroke()}
      }
      var gR=30+pr*200,grd=ctx.createRadialGradient(ex.x,ex.y,0,ex.x,ex.y,gR);
      grd.addColorStop(0,'rgba(255,255,240,'+(ga*0.95)+')');grd.addColorStop(0.05,'rgba(255,220,160,'+(ga*0.7)+')');
      grd.addColorStop(0.2,'rgba(255,150,60,'+(ga*0.3)+')');grd.addColorStop(0.5,'rgba(100,150,230,'+(ga*0.08)+')');grd.addColorStop(1,'rgba(0,0,0,0)');
      ctx.beginPath();ctx.arc(ex.x,ex.y,gR,0,Math.PI*2);ctx.fillStyle=grd;ctx.fill();
      if (pr < 0.22) {
        var fa = 1 - pr / 0.22;
        var fRad = 30 + 120 * (1 - fa);
        var fg = ctx.createRadialGradient(ex.x, ex.y, 0, ex.x, ex.y, fRad);
        fg.addColorStop(0, 'rgba(255,255,255,' + (fa * 1.0) + ')');
        fg.addColorStop(0.25, 'rgba(226,242,255,' + (fa * 0.55) + ')');
        fg.addColorStop(1, 'rgba(170,205,255,0)');
        ctx.beginPath();ctx.arc(ex.x,ex.y,fRad,0,Math.PI*2);ctx.fillStyle=fg;ctx.fill();
      }
      for(var i=0;i<ex.particles.length;i++){var p=ex.particles[i];p.trail.push({x:p.x,y:p.y,life:p.life});if(p.trail.length>12)p.trail.shift();p.x+=p.vx*0.016;p.y+=p.vy*0.016;p.vx*=0.985;p.vy*=0.985;p.life-=p.decay*0.016;if(p.life<=0)continue;
        for(var ti=0;ti<p.trail.length-1;ti++){var tr=p.trail[ti],tr2=p.trail[ti+1],ta=(ti/p.trail.length)*p.life*ga*0.75;ctx.beginPath();ctx.moveTo(tr.x,tr.y);ctx.lineTo(tr2.x,tr2.y);ctx.strokeStyle='hsla('+p.hue+',60%,75%,'+ta+')';ctx.lineWidth=p.r*0.5*(ti/p.trail.length);ctx.stroke()}
        var pg=ctx.createRadialGradient(p.x,p.y,0,p.x,p.y,p.r*2);pg.addColorStop(0,'hsla('+p.hue+',40%,90%,'+(p.life*ga)+')');pg.addColorStop(1,'rgba(0,0,0,0)');ctx.beginPath();ctx.arc(p.x,p.y,p.r*2,0,Math.PI*2);ctx.fillStyle=pg;ctx.fill()}
    }
    ctx.globalCompositeOperation = 'source-over';
  }

  // ============ GLASS + COSMIC GLOW ============

  var cosmicGlowCanvas = null;
  function drawCosmicGlow() {
    if (!cosmicGlowCanvas) {
      cosmicGlowCanvas = document.createElement('canvas');
      cosmicGlowCanvas.width = W; cosmicGlowCanvas.height = H;
      var gctx = cosmicGlowCanvas.getContext('2d');
      // Deep space ambient: blue-purple nebula wash
      var g = gctx.createRadialGradient(W*0.3, H2*0.4, 0, W*0.5, H2*0.5, Math.max(W,H)*0.7);
      g.addColorStop(0, 'rgba(30,60,120,0.15)');
      g.addColorStop(0.3, 'rgba(20,40,80,0.08)');
      g.addColorStop(0.6, 'rgba(10,20,50,0.03)');
      g.addColorStop(1, 'rgba(0,0,0,0)');
      gctx.fillStyle = g; gctx.fillRect(0, 0, W, H);
      // Second warm accent
      var g2 = gctx.createRadialGradient(W*0.7, H2*0.3, 0, W*0.65, H2*0.35, Math.max(W,H)*0.5);
      g2.addColorStop(0, 'rgba(60,40,80,0.1)');
      g2.addColorStop(1, 'rgba(0,0,0,0)');
      gctx.fillStyle = g2; gctx.fillRect(0, 0, W, H);
    }
    ctx.drawImage(cosmicGlowCanvas, 0, 0);
  }

  function drawGradient() {
    // Bottom edge blend to page bg only — no frosted glass
    var edgeTop = H2 * 0.92;
    var eg = ctx.createLinearGradient(0, edgeTop, 0, H);
    eg.addColorStop(0, 'rgba(0,0,0,0)');
    eg.addColorStop(0.5, 'rgba(246,248,251,0.5)');
    eg.addColorStop(1, '#f6f8fb');
    ctx.fillStyle = eg;
    ctx.fillRect(0, edgeTop, W, H - edgeTop);
  }

  // ============ CONSTELLATIONS ============

  function drawConstellations(t) {
    var px = (mouse.x - 0.5) * 30, py = (mouse.y - 0.5) * 30;
    for (var k = 0; k < cons.length; k++) {
      var c = cons[k], cx = c.bx * W + px * 0.3, cy = c.by * H + py * 0.3;
      var sc = H * 0.007 * c.scale, pts = [];
      for (var j = 0; j < c.pts.length; j++) pts.push([cx + c.pts[j][0] * sc, cy + c.pts[j][1] * sc]);
      if (c.lines && c.lines.length) {
        ctx.strokeStyle = 'rgba(140,185,230,0.08)'; ctx.lineWidth = 0.4;
        for (var j = 0; j < c.lines.length; j++) {
          var l = c.lines[j]; ctx.beginPath(); ctx.moveTo(pts[l[0]][0], pts[l[0]][1]); ctx.lineTo(pts[l[1]][0], pts[l[1]][1]); ctx.stroke();
        }
      }
      for (var j = 0; j < pts.length; j++) {
        var twinkle = 0.35 + Math.sin(t * 2 + j * 0.7) * 0.06;
        var grad = ctx.createRadialGradient(pts[j][0], pts[j][1], 0, pts[j][0], pts[j][1], 5);
        grad.addColorStop(0, 'rgba(255,255,255,' + (twinkle * 0.9) + ')');
        grad.addColorStop(0.3, 'rgba(190,215,250,' + (twinkle * 0.15) + ')');
        grad.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.beginPath(); ctx.arc(pts[j][0], pts[j][1], 5, 0, Math.PI * 2); ctx.fillStyle = grad; ctx.fill();
      }
    }
  }

  function drawStar(s, rx, ry) {
    var alpha = Math.max(0, s.rebirth > 0 ? (1 - s.rebirth) : 1);
    var a = (0.7 + 0.3 * Math.sin(s.twinkle));
    if (s.rebirth > 0) {
      var scale = 0.1 + 0.9 * (1 - s.rebirth);
      rx = s.rx + (rx - s.rx) * scale; ry = s.ry + (ry - s.ry) * scale;
      var sr = s.r0 * scale;
      a *= (1 - s.rebirth) * 0.5 + 0.5;
    } else { var sr = s.r; }
    if (s.rebirth > 0 || sr > 1.5) {
      var ga = s.rebirth > 0 ? a * (1 - s.rebirth) * 1.5 : a * 0.5;
      var grad = ctx.createRadialGradient(rx, ry, 0, rx, ry, sr * 2.5);
      grad.addColorStop(0, 'hsla(' + s.hue + ',40%,85%,' + ga + ')');
      grad.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.beginPath(); ctx.arc(rx, ry, sr * 2.5, 0, Math.PI * 2); ctx.fillStyle = grad; ctx.fill();
    }
    ctx.beginPath();
    ctx.fillStyle = 'hsla(' + s.hue + ',30%,85%,' + a + ')';
    ctx.arc(rx, ry, sr, 0, Math.PI * 2); ctx.fill();
  }

  // ============ MAIN LOOP ============

  function animate() {
    var t = performance.now() * 0.001;
    var now = performance.now();

    // Comet spawning: 1-3 every 4-5s
    if (!inShower && now - lastComet > cometCooldown) {
      var count = 1 + Math.floor(Math.random() * 3);
      for (var i = 0; i < count; i++) spawnComet(now + i * 250);
      cometCooldown = 4000 + Math.random() * 1000;
      lastComet = now;
    }

    // Meteor shower: every 28s, 1/3 chance
    if (!inShower && now - lastShowerCheck > 28000) {
      lastShowerCheck = now;
      if (Math.random() < 0.33) spawnMeteorShower(now);
    }
    if (inShower && now > showerEnd) { inShower = false; lastComet = now; }

    mouse.x += (mouse.tx - mouse.x) * 0.05;
    mouse.y += (mouse.ty - mouse.y) * 0.05;
    ctx.clearRect(0, 0, W, H);

    // Cosmic ambient glow first
    drawCosmicGlow();

    for (var l = 0; l < layers.length; l++) {
      var layer = layers[l];
      var px = (mouse.x - 0.5) * 100 * layer.parallax;
      var py = (mouse.y - 0.5) * 100 * layer.parallax;
      for (var i = 0; i < layer.stars.length; i++) {
        var s = layer.stars[i];
        if (s.rebirth > 0) { s.rebirth -= 0.004; if (s.rebirth < 0) s.rebirth = 0; }
        s.x += Math.sin(t * 0.3 + i) * 0.008;
        s.y += Math.cos(t * 0.4 + i) * 0.008;
        if (s.x < -20) s.x = W + 20; if (s.x > W + 20) s.x = -20;
        if (s.y < -20) s.y = H + 20; if (s.y > H + 20) s.y = -20;
        s.twinkle += s.ts;
        drawStar(s, s.x + px, s.y + py);
      }
    }

    drawConstellations(t);
    drawComets(now);
    drawExplosions();
    drawGradient();
    animId = requestAnimationFrame(animate);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
