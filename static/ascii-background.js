// Shared lightweight background for the live panel and the model gallery.
// size — во сколько раз дальше от центра расходится узор, alpha — насколько он ярче обычного
// color — свой цвет узора вместо акцента темы (например, цвет обложки), null — акцент
export function startAsciiBackground(canvas, {playing = () => false, spectrum = () => null, disabled = () => false, size = 1, alpha = 1, color: colorOf = () => null} = {}) {
  const ctx = canvas?.getContext('2d');
  if (!ctx) return;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const bands = [0, 0, 0, 0], ramp = ' .·:-=+*%#@';
  let width = 0, height = 0, last = -Infinity, visible = true, color = '#6c47ff';
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
    // Настоящий спектр есть только первые секунды трека (эквалайзер Quasar на часах). Без него, пока играет музыка, —
    // честная имитация: удары ~120 в минуту, полосы со сдвигом и своей «частотой»; на паузе узор просто переливается.
    const beat = Math.exp(-((t * 2) % 1) * 5);
    const fake = i => active && !reduced.matches ? .25 + .55 * beat * (.6 + .4 * Math.sin(t * (1.3 + i * .7) + i * 2)) : 0;
    bands.forEach((_, i) => { bands[i] += ((live ? sample.bands[i] : fake(i)) - bands[i]) * .3; });
    const fs = 13, col = fs * .62, row = fs * 1.1;
    const cx = width / 2, cy = height / 2, radius = Math.min(width, height) * .63 * size;
    ctx.font = `${fs}px monospace`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = colorOf() || color;
    for (let y = row / 2; y < height; y += row) for (let x = col / 2; x < width; x += col) {
      const dx = x - cx, dy = y - cy, r = Math.hypot(dx, dy) / radius, a = Math.atan2(dy, dx);
      const wave = .78 + .12 * Math.sin(a * 3 + t * .8) + .07 * Math.sin(a * 7 - t);
      const band = bands[Math.min(3, Math.floor(Math.abs(a) / Math.PI * 4))];
      const reach = wave + (active ? .08 : 0) + band * .2;
      const v = Math.max(0, 1 - (r / reach) ** 2);
      if (v < .07) continue;
      ctx.globalAlpha = Math.min(.65 * alpha, v * (active ? .65 : .5) * alpha);
      ctx.fillText(ramp[Math.min(ramp.length - 1, Math.floor(v * ramp.length))], x, y);
    }
    ctx.globalAlpha = 1;
  }
  requestAnimationFrame(frame);
}
