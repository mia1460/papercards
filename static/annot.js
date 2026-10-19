// PaperCards 图片标注：矩形 / 椭圆 / 箭头 / 画笔 / 马克笔 / 文字 / 马赛克
// 原图保留在 image_orig，标注以图形数据存在 annot（JSON），image 存渲染后的图片，可反复编辑。
(function () {
  const TOOLS = [
    ['rect', '▭', '矩形', 'R'], ['ellipse', '◯', '椭圆', 'E'], ['arrow', '↗', '箭头', 'A'],
    ['pen', '✎', '画笔', 'P'], ['marker', '▌', '马克笔', 'M'], ['text', 'T', '文字', 'T'], ['mosaic', '▦', '马赛克', 'X'],
  ];
  const COLORS = ['#ff3b30', '#ffcc00', '#34c759', '#0a84ff', '#1c1c1e', '#ffffff'];
  const SIZES = [['s', 1], ['m', 2], ['l', 3.5]];
  let imeEndAt = 0;
  document.addEventListener('compositionend', () => { imeEndAt = performance.now(); }, true);

  const css = `
  .an-dlg { border: 0; padding: 0; background: var(--panel, #fff); color: var(--ink, #222); border-radius: 12px; width: min(1200px, 96vw); max-width: 96vw; max-height: 96vh; }
  .an-dlg::backdrop { background: rgba(0,0,0,.55); }
  .an-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; padding: 10px 12px; border-bottom: 1px solid var(--line, #ddd); }
  .an-bar .grp { display: flex; gap: 2px; background: var(--chip, #eee); border-radius: 8px; padding: 2px; }
  .an-bar button { border: 0; background: none; color: inherit; font: inherit; font-size: 14px; min-width: 30px; height: 28px; border-radius: 6px; cursor: pointer; padding: 0 6px; }
  .an-bar button.on { background: var(--panel, #fff); box-shadow: 0 1px 3px rgba(0,0,0,.15); font-weight: 600; }
  .an-bar button:disabled { opacity: .35; cursor: default; }
  .an-bar .sw { width: 22px; min-width: 22px; height: 22px; border-radius: 50%; margin: 3px; padding: 0; border: 2px solid transparent; box-shadow: inset 0 0 0 1px rgba(0,0,0,.2); }
  .an-bar .sw.on { border-color: var(--accent, #b4532a); }
  .an-bar .sp { flex: 1; }
  .an-bar .btn { border: 1px solid var(--line, #ddd); background: var(--panel, #fff); padding: 0 12px; }
  .an-bar .btn.primary { background: var(--accent, #b4532a); border-color: var(--accent, #b4532a); color: #fff; font-weight: 600; }
  .an-stage { position: relative; display: flex; justify-content: center; align-items: center; padding: 14px; background: repeating-conic-gradient(rgba(127,127,127,.08) 0 25%, transparent 0 50%) 0 0 / 16px 16px; overflow: auto; max-height: calc(96vh - 60px); }
  .an-stage canvas { display: block; max-width: 100%; max-height: calc(96vh - 100px); cursor: crosshair; box-shadow: 0 2px 12px rgba(0,0,0,.2); touch-action: none; }
  .an-text { position: absolute; border: 1px dashed var(--accent, #b4532a); background: rgba(255,255,255,.85); outline: none; font-family: -apple-system, "PingFang SC", sans-serif; padding: 0 2px; min-width: 60px; }
  .an-hint { font-size: 11px; color: var(--muted, #888); }`;

  function openAnnotator(card, onSaved) {
    if (!document.getElementById('an-style')) {
      const st = document.createElement('style'); st.id = 'an-style'; st.textContent = css; document.head.appendChild(st);
    }
    const origName = card.image_orig || card.image;
    let shapes = [];
    try { shapes = JSON.parse(card.annot || '[]') || []; } catch (e) { shapes = []; }
    let redo = [], tool = 'rect', color = COLORS[0], size = 'm', drawing = null, dirty = false;

    const dlg = document.createElement('dialog');
    dlg.className = 'an-dlg';
    dlg.innerHTML = `
      <div class="an-bar">
        <div class="grp tools">${TOOLS.map(([k, ic, name, key]) => `<button data-tool="${k}" title="${name}（${key}）">${ic}</button>`).join('')}</div>
        <div class="grp colors">${COLORS.map(c => `<button class="sw" data-color="${c}" style="background:${c}" title="颜色"></button>`).join('')}</div>
        <div class="grp sizes">${SIZES.map(([k]) => `<button data-size="${k}" title="粗细">${{ s: '细', m: '中', l: '粗' }[k]}</button>`).join('')}</div>
        <div class="grp"><button data-act="undo" title="撤销（⌘Z）">↶</button><button data-act="redo" title="重做（⌘⇧Z）">↷</button><button data-act="clear" title="清除全部标注，恢复原图">清空</button></div>
        <span class="sp"></span>
        <span class="an-hint">⌘S 保存 · Esc 取消</span>
        <button class="btn" data-act="cancel">取消</button><button class="btn primary" data-act="save">保存</button>
      </div>
      <div class="an-stage"><canvas></canvas></div>`;
    document.body.appendChild(dlg);
    const canvas = dlg.querySelector('canvas'), ctx = canvas.getContext('2d'), stage = dlg.querySelector('.an-stage');
    const img = new Image();
    img.onload = () => { canvas.width = img.naturalWidth; canvas.height = img.naturalHeight; draw(); };
    img.src = '/images/' + origName;

    const base = () => Math.max(2, Math.round(Math.min(canvas.width, canvas.height) / 260));
    const lw = s => base() * (SIZES.find(x => x[0] === s) || SIZES[1])[1];

    function syncBar() {
      dlg.querySelectorAll('[data-tool]').forEach(b => b.classList.toggle('on', b.dataset.tool === tool));
      dlg.querySelectorAll('[data-color]').forEach(b => b.classList.toggle('on', b.dataset.color === color));
      dlg.querySelectorAll('[data-size]').forEach(b => b.classList.toggle('on', b.dataset.size === size));
      dlg.querySelector('[data-act="undo"]').disabled = !shapes.length;
      dlg.querySelector('[data-act="redo"]').disabled = !redo.length;
    }

    function drawShape(c, sh) {
      c.save();
      c.strokeStyle = c.fillStyle = sh.color; c.lineWidth = lw(sh.size); c.lineCap = c.lineJoin = 'round';
      const x = Math.min(sh.x1, sh.x2), y = Math.min(sh.y1, sh.y2), w = Math.abs(sh.x2 - sh.x1), h = Math.abs(sh.y2 - sh.y1);
      if (sh.t === 'rect') c.strokeRect(x, y, w, h);
      else if (sh.t === 'ellipse') { c.beginPath(); c.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2); c.stroke(); }
      else if (sh.t === 'arrow') {
        const ang = Math.atan2(sh.y2 - sh.y1, sh.x2 - sh.x1), head = c.lineWidth * 4;
        c.beginPath(); c.moveTo(sh.x1, sh.y1);
        c.lineTo(sh.x2 - Math.cos(ang) * head * 0.8, sh.y2 - Math.sin(ang) * head * 0.8); c.stroke();
        c.beginPath(); c.moveTo(sh.x2, sh.y2);
        c.lineTo(sh.x2 - head * Math.cos(ang - 0.45), sh.y2 - head * Math.sin(ang - 0.45));
        c.lineTo(sh.x2 - head * Math.cos(ang + 0.45), sh.y2 - head * Math.sin(ang + 0.45)); c.closePath(); c.fill();
      } else if (sh.t === 'pen' || sh.t === 'marker') {
        if (sh.t === 'marker') { c.globalAlpha = 0.35; c.lineWidth = lw(sh.size) * 5; c.lineCap = 'butt'; }
        c.beginPath(); sh.pts.forEach(([px, py], i) => i ? c.lineTo(px, py) : c.moveTo(px, py)); c.stroke();
      } else if (sh.t === 'text') {
        const fs = lw(sh.size) * 7;
        c.font = `600 ${fs}px -apple-system, "PingFang SC", sans-serif`; c.textBaseline = 'top';
        c.lineWidth = Math.max(2, fs / 7); c.strokeStyle = sh.color === '#ffffff' ? 'rgba(0,0,0,.6)' : 'rgba(255,255,255,.9)';
        sh.text.split('\n').forEach((line, i) => { c.strokeText(line, sh.x1, sh.y1 + i * fs * 1.25); c.fillText(line, sh.x1, sh.y1 + i * fs * 1.25); });
      } else if (sh.t === 'mosaic' && w > 2 && h > 2) {
        const cell = Math.max(6, base() * 4), tw = Math.max(1, Math.round(w / cell)), th = Math.max(1, Math.round(h / cell));
        const tmp = document.createElement('canvas'); tmp.width = tw; tmp.height = th;
        tmp.getContext('2d').drawImage(img, x, y, w, h, 0, 0, tw, th);
        c.imageSmoothingEnabled = false; c.drawImage(tmp, 0, 0, tw, th, x, y, w, h);
      }
      c.restore();
    }

    function render(c) {
      c.drawImage(img, 0, 0);
      shapes.filter(s => s.t === 'mosaic').forEach(s => drawShape(c, s));   // 马赛克先画，避免盖住其他标注
      shapes.filter(s => s.t !== 'mosaic').forEach(s => drawShape(c, s));
    }
    function draw() {
      render(ctx);
      if (drawing) drawShape(ctx, drawing);
      syncBar();
    }

    const pos = e => {
      const r = canvas.getBoundingClientRect();
      return [(e.clientX - r.left) * canvas.width / r.width, (e.clientY - r.top) * canvas.height / r.height];
    };
    canvas.addEventListener('pointerdown', e => {
      if (e.button !== 0) return;
      const [x, y] = pos(e);
      if (tool === 'text') { e.preventDefault(); return startText(e, x, y); }
      canvas.setPointerCapture(e.pointerId);
      drawing = { t: tool, color, size, x1: x, y1: y, x2: x, y2: y, pts: [[x, y]] };
    });
    canvas.addEventListener('pointermove', e => {
      if (!drawing) return;
      const [x, y] = pos(e);
      drawing.x2 = x; drawing.y2 = y;
      if (drawing.t === 'pen' || drawing.t === 'marker') drawing.pts.push([x, y]);
      else if (e.shiftKey && (drawing.t === 'rect' || drawing.t === 'ellipse')) {   // ⇧ 正方形 / 正圆
        const d = Math.max(Math.abs(x - drawing.x1), Math.abs(y - drawing.y1));
        drawing.x2 = drawing.x1 + Math.sign(x - drawing.x1 || 1) * d; drawing.y2 = drawing.y1 + Math.sign(y - drawing.y1 || 1) * d;
      }
      draw();
    });
    canvas.addEventListener('pointerup', () => {
      if (!drawing) return;
      const sh = drawing; drawing = null;
      const moved = Math.abs(sh.x2 - sh.x1) + Math.abs(sh.y2 - sh.y1) > 3 || sh.pts.length > 2;
      if (moved) {
        if (sh.t !== 'pen' && sh.t !== 'marker') delete sh.pts;
        shapes.push(sh); redo = []; dirty = true;
      }
      draw();
    });

    function startText(e, x, y) {
      const r = canvas.getBoundingClientRect(), sr = stage.getBoundingClientRect(), scale = r.width / canvas.width;
      const inp = document.createElement('textarea');
      inp.className = 'an-text'; inp.rows = 1;
      const fs = lw(size) * 7 * scale;
      Object.assign(inp.style, {
        left: (e.clientX - sr.left + stage.scrollLeft) + 'px', top: (e.clientY - sr.top + stage.scrollTop) + 'px',
        fontSize: fs + 'px', color: color === '#ffffff' ? '#000' : color, lineHeight: 1.25,
      });
      stage.appendChild(inp);
      setTimeout(() => inp.focus(), 0);
      let done = false;
      const commit = ok => {
        if (done) return; done = true;
        const t = inp.value.replace(/\s+$/, '');
        inp.remove();
        if (ok && t) { shapes.push({ t: 'text', color, size, x1: x, y1: y, x2: x, y2: y, text: t }); redo = []; dirty = true; draw(); }
      };
      inp.addEventListener('keydown', ev => {
        ev.stopPropagation();
        if (ev.isComposing || ev.keyCode === 229 || performance.now() - imeEndAt < 120) return;   // 输入法的回车 / Esc
        if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); commit(true); }
        if (ev.key === 'Escape') { ev.preventDefault(); commit(false); }
      });
      inp.addEventListener('input', () => { inp.style.height = 'auto'; inp.style.height = inp.scrollHeight + 'px'; });
      inp.addEventListener('blur', () => commit(true));
    }

    function close() { document.removeEventListener('keydown', onKey, true); dlg.close(); dlg.remove(); }
    function cancel() {
      if (dirty && !confirm('放弃这次的标注修改？')) return;
      close();
    }
    async function save() {
      const btn = dlg.querySelector('[data-act="save"]'); btn.disabled = true; btn.textContent = '保存中…';
      try {
        let body;
        if (!shapes.length) {
          body = { image: origName, image_orig: origName, annot: null };   // 没有标注：恢复原图
        } else {
          const out = document.createElement('canvas'); out.width = canvas.width; out.height = canvas.height;
          render(out.getContext('2d'));
          const blob = await new Promise(r => out.toBlob(r, 'image/png'));
          const up = await fetch('/api/upload', { method: 'POST', body: blob }).then(r => r.json());
          body = { image: up.image, image_orig: origName, annot: JSON.stringify(shapes) };
        }
        const saved = await fetch('/api/cards/' + card.id, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json());
        close();
        onSaved && onSaved(saved);
      } catch (e) {
        btn.disabled = false; btn.textContent = '保存'; alert('保存失败：' + e);
      }
    }

    dlg.addEventListener('click', e => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.dataset.tool) tool = b.dataset.tool;
      if (b.dataset.color) color = b.dataset.color;
      if (b.dataset.size) size = b.dataset.size;
      const act = b.dataset.act;
      if (act === 'undo' && shapes.length) { redo.push(shapes.pop()); dirty = true; }
      if (act === 'redo' && redo.length) { shapes.push(redo.pop()); dirty = true; }
      if (act === 'clear' && shapes.length && confirm('清除全部标注，恢复原图？')) { redo = []; shapes = []; dirty = true; }
      if (act === 'cancel') return cancel();
      if (act === 'save') return save();
      draw();
    });
    const onKey = e => {
      if (e.target.classList && e.target.classList.contains('an-text')) return;
      const k = e.key.toLowerCase();
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); return cancel(); }
      if (e.metaKey && k === 's') { e.preventDefault(); return save(); }
      if (e.metaKey && k === 'z') { e.preventDefault(); dlg.querySelector(`[data-act="${e.shiftKey ? 'redo' : 'undo'}"]`).click(); return; }
      if (!e.metaKey && !e.ctrlKey && !e.altKey) {
        const t = TOOLS.find(x => x[3].toLowerCase() === k);
        if (t) { tool = t[0]; draw(); }
      }
    };
    document.addEventListener('keydown', onKey, true);
    dlg.addEventListener('cancel', e => { e.preventDefault(); cancel(); });
    dlg.showModal();
    syncBar();
  }

  window.openAnnotator = openAnnotator;
})();
