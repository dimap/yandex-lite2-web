// Shared lightweight background for the live panel and the model gallery.
// size — во сколько раз дальше от центра расходится узор, alpha — насколько он ярче обычного
// color — свой цвет узора вместо акцента темы (например, цвет обложки), null — акцент
// Вид берётся из window.__vizStyle (как и у визуализатора за 3D-моделью).
export function startAsciiBackground(canvas, { playing = () => false, spectrum = () => null, disabled = () => false, size = 1, alpha = 1, color: colorOf = () => null } = {}) {
  const ctx = canvas?.getContext('2d');
  if (!ctx) return;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const bands = [0, 0, 0, 0], ramp = ' .·:-=+*%#@';
  const rings = [];                    // кольца, пущенные по пикам (а не по таймеру)
  let lastRing = 0, bassAvg = 0;
  let width = 0, height = 0, last = -Infinity, visible = true, color = '#e8590c';
  canvas.style.pointerEvents = 'none'; canvas.style.zIndex = '0';
  const readColor = () => { color = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || color; };
  readColor(); window.addEventListener('themechange', readColor);
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', readColor);
  new ResizeObserver(() => {
    const rect = canvas.getBoundingClientRect(), ratio = Math.min(devicePixelRatio, 2);
    width = rect.width; height = rect.height;
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0); last = -Infinity;
  }).observe(canvas);
  new IntersectionObserver(entries => { visible = entries[0].isIntersecting; }).observe(canvas);
  function frame(now) {
    requestAnimationFrame(frame);
    if (document.hidden || !visible || !width || now - last < (reduced.matches ? 1000 : 50)) return;
    last = now; ctx.clearRect(0, 0, width, height);
    if (disabled()) return;
    const t = reduced.matches ? 0 : now / 1000, active = playing(), sample = spectrum();
    const live = sample?.bands?.length === 4 && now - sample.t < 700;
    const beat = Math.exp(-((t * 2) % 1) * 5);
    // Живой спектр — быстро сглаживаем; без него, пока играет, — честная имитация по биту.
    if (live) sample.bands.forEach((b, k) => { bands[k] += (b - bands[k]) * .5; });
    else bands.forEach((_, k) => { bands[k] = active && !reduced.matches ? .15 + .85 * beat * (.55 + .45 * Math.sin(t * (1.3 + k * .7) + k * 2)) : 0; });
    const loud = (bands[0] + bands[1] + bands[2] + bands[3]) / 4;
    // Кольца — по всплескам низа (или по биту, если живого спектра нет), а не по таймеру.
    bassAvg += (bands[0] - bassAvg) * (live ? .05 : .12);
    const onPeak = live ? bands[0] - bassAvg > .1 && bands[0] > .3 : beat > .85;
    const stale = live && now - lastRing > 1200 && loud > .15;   // давно не было пиков — всё равно пускаем
    if (active && (onPeak || stale) && now - lastRing > (live ? 220 : 420)) {
      rings.push({ t0: now, band: live ? bands[0] : .6 });
      lastRing = now;
    }
    if (rings.length > 8) rings.splice(0, rings.length - 8);
    for (let i = rings.length - 1; i >= 0; i--) if (now - rings[i].t0 > 1400) rings.splice(i, 1);

    const style = window.__vizStyle || 'rays';
    const fs = 13, col = fs * .62, row = fs * 1.1;
    const cx = width / 2, cy = height / 2, radius = Math.min(width, height) * .63 * size;
    const minHalf = Math.min(width, height) / 2;   // кольца и веер считаем по меньшей стороне
    ctx.font = `${fs}px monospace`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = colorOf() || color;
    for (let y = row / 2; y < height; y += row) for (let x = col / 2; x < width; x += col) {
      const dx = x - cx, dy = y - cy, dist = Math.hypot(dx, dy);
      const r = dist / radius, rn = dist / minHalf, a = Math.atan2(dy, dx);
      const u = Math.abs(a) / Math.PI * 3, k = Math.min(2, Math.floor(u)), f = u - k;
      const band = bands[k] * (1 - f) + bands[k + 1] * f;
      const qa = Math.min(Math.abs(a), Math.PI - Math.abs(a));
      const uq = qa / (Math.PI / 2) * 3, kq = Math.min(2, Math.floor(uq)), fq = uq - kq;
      const bandQuad = bands[kq] * (1 - fq) + bands[kq + 1] * fq;
      const wave = .78 + .12 * Math.sin(a * 3 + t * .8) + .07 * Math.sin(a * 7 - t);
      const passive = Math.max(0, 1 - (r / wave) ** 2);      // спокойный фон, когда музыка не играет
      let v = 0;
      if (!active) v = passive;
      else if (style === 'rings') {
        for (const rg of rings) {
          const age = (now - rg.t0) / 1000;
          const rad = .6 + age * .35, wide = .02 + .08 * rg.band;
          const alpha = (.55 + .45 * rg.band) * Math.max(0, Math.min(1, (1.06 - rad) / .12))
            * Math.min(1, age / .08);
          const d = Math.abs(rn - rad);
          if (d < wide) v = Math.max(v, Math.pow(1 - d / wide, .7) * alpha);
        }
      } else if (style === 'orbit') {
        const inner = .70, len = .08 + .28 * bandQuad;
        if (rn > inner && rn < inner + len) {
          const t = (rn - inner) / len;
          const endFade = Math.max(0, Math.min(1, (1.02 - rn) / .12));
          v = (.25 + .75 * bandQuad) * (1 - .45 * t) * endFade;
        }
        if (Math.abs(rn - inner) < .012) v = Math.max(v, .14 * (.4 + .6 * bandQuad));
      } else {
        const reach = wave + .08 + band * .45;
        v = Math.max(0, 1 - (r / reach) ** 2);
      }
      if (v < .07) continue;
      ctx.globalAlpha = Math.min(.8 * alpha, v * (active ? .45 + .9 * loud : .5) * alpha);
      ctx.fillText(ramp[Math.min(ramp.length - 1, Math.floor(v * ramp.length))], x, y);
    }
    ctx.globalAlpha = 1;
  }
  requestAnimationFrame(frame);
}
