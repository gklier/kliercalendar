/* Weather + place glyphs. Flat multicolor, SF Symbols-inspired. No gradients. */
(function () {
  "use strict";

  const C = {
    sun: "#FFD60A",
    moon: "#E5E5EA",
    cloud: "#FFFFFF",
    cloudDim: "#D1D1D6",
    rain: "#64D2FF",
    bolt: "#FFD60A",
  };

  const CLOUD = "M10 26h13.2a5.6 5.6 0 0 0 .7-11.16A7.6 7.6 0 0 0 9.3 15.1 5.5 5.5 0 0 0 10 26z";

  function sun(cx, cy, r, rays) {
    let s = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${C.sun}"/>`;
    if (rays) {
      for (let i = 0; i < 8; i++) {
        const a = (i * Math.PI) / 4;
        const x1 = cx + Math.cos(a) * (r + 2.6), y1 = cy + Math.sin(a) * (r + 2.6);
        const x2 = cx + Math.cos(a) * (r + 5.4), y2 = cy + Math.sin(a) * (r + 5.4);
        s += `<line x1="${x1.toFixed(2)}" y1="${y1.toFixed(2)}" x2="${x2.toFixed(2)}" y2="${y2.toFixed(2)}" stroke="${C.sun}" stroke-width="2.2" stroke-linecap="round"/>`;
      }
    }
    return s;
  }

  function moon(cx, cy, r) {
    // Crescent: the moon disc minus a second disc offset up and to the right.
    const r2 = r * 0.86;
    const d = r * 0.78;
    const ang = -Math.PI / 4;
    const dx = Math.cos(ang), dy = Math.sin(ang);
    // Distance along the centre line to the chord shared by both circles
    const a = (d * d + r * r - r2 * r2) / (2 * d);
    const h = Math.sqrt(Math.max(r * r - a * a, 0));
    const mx = cx + a * dx, my = cy + a * dy;
    const p1 = [mx + h * -dy, my + h * dx];
    const p2 = [mx - h * -dy, my - h * dx];
    const f = (n) => n.toFixed(2);
    return `<path d="M${f(p1[0])} ${f(p1[1])}A${f(r)} ${f(r)} 0 1 1 ${f(p2[0])} ${f(p2[1])}A${f(r2)} ${f(r2)} 0 0 0 ${f(p1[0])} ${f(p1[1])}z" fill="${C.moon}"/>`;
  }

  function cloud(dy, fill) {
    return `<path class="${fill ? "wd" : "wc"}" d="${CLOUD}" fill="${fill || C.cloud}" transform="translate(0 ${dy || 0})"/>`;
  }

  function drops(n) {
    const xs = n === 2 ? [12, 19] : [10.5, 16, 21.5];
    return xs.map((x) => `<line x1="${x}" y1="24.5" x2="${x - 1.6}" y2="29" stroke="${C.rain}" stroke-width="2.2" stroke-linecap="round"/>`).join("");
  }

  function flakes() {
    return [10.5, 16, 21.5].map((x, i) => `<circle class="wc" cx="${x}" cy="${i % 2 ? 28.2 : 26.4}" r="1.6" fill="${C.cloud}"/>`).join("");
  }

  function sleet() {
    return `<line x1="12" y1="24.5" x2="10.4" y2="29" stroke="${C.rain}" stroke-width="2.2" stroke-linecap="round"/>` +
      `<circle class="wc" cx="17.5" cy="27.4" r="1.6" fill="${C.cloud}"/>` +
      `<line x1="23" y1="24.5" x2="21.4" y2="29" stroke="${C.rain}" stroke-width="2.2" stroke-linecap="round"/>`;
  }

  function bolt() {
    return `<path d="M17.4 22.2 13.6 27.6h3.2l-1.6 4.4 5.2-6.4h-3.3l1.9-3.4z" fill="${C.bolt}"/>`;
  }

  function fogLines() {
    return [21, 25, 29].map((y, i) => `<line class="wsd" x1="${6 + i * 2}" y1="${y}" x2="${26 - i}" y2="${y}" stroke="${C.cloudDim}" stroke-width="2.2" stroke-linecap="round"/>`).join("");
  }

  function wind() {
    return `<path class="ws" d="M5 13h15a3.5 3.5 0 1 0-3.5-3.5" fill="none" stroke="${C.cloud}" stroke-width="2.4" stroke-linecap="round"/>` +
      `<path class="ws" d="M5 19h20a3.5 3.5 0 1 1-3.5 3.5" fill="none" stroke="${C.cloud}" stroke-width="2.4" stroke-linecap="round"/>` +
      `<path class="wsd" d="M5 16h9" fill="none" stroke="${C.cloudDim}" stroke-width="2.4" stroke-linecap="round"/>`;
  }

  /** Normalise any Tempest icon name (or our own) into a kind + day/night. */
  function classify(name) {
    const n = String(name || "").toLowerCase();
    const night = n.includes("night");
    const possibly = n.includes("possibly");
    let kind = "cloudy";
    if (n.includes("thunder")) kind = "thunder";
    else if (n.includes("sleet")) kind = "sleet";
    else if (n.includes("snow")) kind = "snow";
    else if (n.includes("rain") || n.includes("drizzle") || n.includes("shower")) kind = "rain";
    else if (n.includes("fog") || n.includes("haz") || n.includes("mist")) kind = "fog";
    else if (n.includes("wind")) kind = "wind";
    else if (n.includes("partly")) kind = "partly";
    else if (n.includes("clear") || n.includes("sun")) kind = "clear";
    return { kind, night, possibly };
  }

  function weatherSVG(name, opts) {
    const { kind, night, possibly } = classify(name);
    const label = (opts && opts.label) || kind;
    let body = "";
    const peek = possibly || kind === "partly";
    const celestial = night ? moon(23.5, 8.5, 7) : sun(22.5, 9.5, 4.6, true);

    switch (kind) {
      case "clear":
        body = night ? moon(16, 16, 10) : sun(16, 16, 7, true);
        break;
      case "partly":
        body = celestial + cloud(2);
        break;
      case "cloudy":
        body = cloud(-4, C.cloudDim) + cloud(2);
        break;
      case "rain":
        body = (peek ? celestial : "") + cloud(-4) + drops(3);
        break;
      case "snow":
        body = (peek ? celestial : "") + cloud(-4) + flakes();
        break;
      case "sleet":
        body = (peek ? celestial : "") + cloud(-4) + sleet();
        break;
      case "thunder":
        body = (peek ? celestial : "") + cloud(-5) + bolt();
        break;
      case "fog":
        body = cloud(-7) + fogLines();
        break;
      case "wind":
        body = wind();
        break;
    }
    return `<svg class="wx" viewBox="0 0 32 32" role="img" aria-label="${label}">${body}</svg>`;
  }

  const PLACE = {
    home: '<path d="M3 9.5 10 3.5l7 6V17H12.5v-4.5h-5V17H3z" fill="#fff"/>',
    work: '<rect x="2.5" y="6" width="15" height="10" rx="2.2" fill="#fff"/><path d="M7 6V4.6A1.6 1.6 0 0 1 8.6 3h2.8A1.6 1.6 0 0 1 13 4.6V6" fill="none" stroke="#fff" stroke-width="1.8"/>',
    school: '<path d="M10 3 1.5 7.5 10 12l8.5-4.5z" fill="#fff"/><path d="M5 10v3.6c0 1.5 2.3 2.9 5 2.9s5-1.4 5-2.9V10l-5 2.6z" fill="#fff"/>',
    cart: '<path d="M2 3h2.6l2 9.2h9L17.6 6H5.4" fill="none" stroke="#fff" stroke-width="1.9" stroke-linejoin="round" stroke-linecap="round"/><circle cx="8" cy="16" r="1.6" fill="#fff"/><circle cx="14.5" cy="16" r="1.6" fill="#fff"/>',
    heart: '<path d="M10 17s-7-4.3-7-9.2A3.8 3.8 0 0 1 10 5.6a3.8 3.8 0 0 1 7 2.2C17 12.7 10 17 10 17z" fill="#fff"/>',
    fitness: '<rect x="1.5" y="7" width="3" height="6" rx="1" fill="#fff"/><rect x="15.5" y="7" width="3" height="6" rx="1" fill="#fff"/><rect x="4" y="5" width="3" height="10" rx="1.2" fill="#fff"/><rect x="13" y="5" width="3" height="10" rx="1.2" fill="#fff"/><rect x="7" y="9" width="6" height="2" fill="#fff"/>',
    plane: '<path d="M18 9.2c0-.7-.6-1.2-1.3-1.2H12L8 2.5H6.2L8.3 8H4.4L3 6H1.6l.9 4-.9 4H3l1.4-2h3.9L6.2 17.5H8L12 12h4.7c.7 0 1.3-.5 1.3-1.2z" fill="#fff"/>',
    pin: '<path d="M10 18s-6-5.6-6-10a6 6 0 0 1 12 0c0 4.4-6 10-6 10z" fill="#fff"/><circle cx="10" cy="8" r="2.2" fill="rgba(0,0,0,.35)"/>',
    star: '<path d="m10 2.2 2.3 5 5.4.6-4 3.7 1.1 5.4L10 14.2l-4.8 2.7 1.1-5.4-4-3.7 5.4-.6z" fill="#fff"/>',
  };

  function placeSVG(kind) {
    return `<svg viewBox="0 0 20 20" aria-hidden="true">${PLACE[kind] || PLACE.pin}</svg>`;
  }

  window.Icons = { weatherSVG, placeSVG, classify };
})();
