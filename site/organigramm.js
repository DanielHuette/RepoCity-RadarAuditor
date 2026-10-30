// Organigramm tab: draws the track between the eight stations and sends a signal along it.
// The track is measured from the laid-out cards, so it follows the grid on wide screens
// (stations 1–4 left to right, 5–8 back from right to left) and a rail on narrow ones.
// After station 8 the signal takes the monthly-audit loop back into the queue (station 2).
// With reduced motion the track is drawn and nothing moves.
(function () {
  "use strict";

  var wurzel = document.querySelector("[data-org]");
  if (!wurzel) return;
  var svg = wurzel.querySelector(".org__spur");
  var stationen = Array.prototype.slice.call(wurzel.querySelectorAll(".station"));
  var schleifeText = wurzel.querySelector("[data-schleife]");
  var NS = "http://www.w3.org/2000/svg";
  var reduziert = window.matchMedia("(prefers-reduced-motion: reduce)");
  var breit = window.matchMedia("(min-width: 1000px)");
  var NODE_Y = 30;          // vertical position of a station's connection point below its top edge
  var VERWEILEN = 1100;     // ms the signal rests at a station
  var TEMPO = 0.42;         // px per ms while travelling

  var abschnitte = [];      // { pfad, laenge, ziel } in route order
  var punkt = null;
  var laeuft = false;
  var schritt = 0;
  var phase = "ruht";
  var phaseStart = 0;
  var rafId = 0;

  function el(name, attrs) {
    var n = document.createElementNS(NS, name);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  }

  // Connection points relative to the org box: left, right, top-centre and bottom-centre of each card.
  function punkte() {
    var basis = wurzel.getBoundingClientRect();
    return stationen.map(function (s) {
      var r = s.getBoundingClientRect();
      var x = r.left - basis.left;
      var y = r.top - basis.top;
      return { l: x, r: x + r.width, o: y, u: y + r.height, m: x + r.width / 2, k: y + NODE_Y };
    });
  }

  function wegeBreit(p) {
    var d = [];
    for (var i = 0; i < 7; i++) {
      var a = p[i];
      var b = p[i + 1];
      if (i < 3) d.push("M" + a.r + "," + a.k + " H" + b.l);
      else if (i === 3) d.push("M" + a.m + "," + a.u + " V" + b.o);
      else d.push("M" + a.l + "," + a.k + " H" + b.r);
    }
    var acht = p[7];
    var zwei = p[1];
    var randX = Math.max(4, acht.l - 16);
    var obenY = Math.max(4, Math.min(p[0].o, zwei.o) - 16);
    var schleife = "M" + acht.l + "," + acht.k + " H" + randX + " V" + obenY + " H" + zwei.m + " V" + zwei.o;
    return { haupt: d, schleife: schleife, schleifeLabel: { x: randX + 10, y: obenY - 6 } };
  }

  function wegeSchmal(p) {
    var schiene = 22;
    var d = [];
    for (var i = 0; i < 7; i++) d.push("M" + schiene + "," + p[i].k + " V" + p[i + 1].k);
    var schleife = "M" + schiene + "," + p[7].k + " H6 V" + p[1].k + " H" + schiene;
    return { haupt: d, schleife: schleife, schleifeLabel: null };
  }

  function zeichnen() {
    var basis = wurzel.getBoundingClientRect();
    if (basis.width === 0) return false;
    svg.setAttribute("viewBox", "0 0 " + basis.width + " " + basis.height);
    svg.setAttribute("width", basis.width);
    svg.setAttribute("height", basis.height);
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    var defs = el("defs", {});
    var glut = el("filter", { id: "org-glut", x: "-200%", y: "-200%", width: "500%", height: "500%" });
    glut.appendChild(el("feGaussianBlur", { stdDeviation: "5" }));
    defs.appendChild(glut);
    svg.appendChild(defs);

    var p = punkte();
    var wege = breit.matches ? wegeBreit(p) : wegeSchmal(p);
    abschnitte = [];
    wege.haupt.forEach(function (d, i) {
      svg.appendChild(el("path", { d: d, class: "org__gleis" }));
      var lit = el("path", { d: d, class: "org__gleis-licht" });
      svg.appendChild(lit);
      abschnitte.push({ pfad: lit, laenge: lit.getTotalLength(), ziel: i + 1 });
    });
    svg.appendChild(el("path", { d: wege.schleife, class: "org__gleis org__gleis--schleife" }));
    var schleifePfad = el("path", { d: wege.schleife, class: "org__gleis-licht org__gleis-licht--schleife" });
    svg.appendChild(schleifePfad);
    if (wege.schleifeLabel && schleifeText) {
      var t = el("text", { x: wege.schleifeLabel.x, y: wege.schleifeLabel.y, class: "org__schleife-label" });
      t.textContent = schleifeText.querySelector("strong").textContent;
      svg.appendChild(t);
    }
    // Route: 1→8, loop back to 2, 2→8 again, then a fresh order starts at 1.
    var route = abschnitte.slice();
    route.push({ pfad: schleifePfad, laenge: schleifePfad.getTotalLength(), ziel: 1, schleife: true });
    route = route.concat(abschnitte.slice(1));
    abschnitte = route;

    // Nodes sit where the signal arrives: left edge in row one, top centre of station 5, right edge after it.
    stationen.forEach(function (s, i) {
      var c = { x: 22, y: p[i].k };
      if (breit.matches) c = i === 4 ? { x: p[i].m, y: p[i].o } : { x: i < 4 ? p[i].l : p[i].r, y: p[i].k };
      svg.appendChild(el("circle", { cx: c.x, cy: c.y, r: 4.5, class: "org__knoten" }));
    });

    punkt = el("g", { class: "org__signal" });
    punkt.appendChild(el("circle", { r: 11, class: "org__signal-glut", filter: "url(#org-glut)" }));
    punkt.appendChild(el("circle", { r: 4.5, class: "org__signal-kern" }));
    svg.appendChild(punkt);
    setzePunkt(abschnitte[0].pfad.getPointAtLength(0));
    return true;
  }

  function setzePunkt(pt) {
    if (punkt) punkt.setAttribute("transform", "translate(" + pt.x.toFixed(1) + " " + pt.y.toFixed(1) + ")");
  }

  function markiere(index) {
    stationen.forEach(function (s, i) { s.classList.toggle("ist-dran", i === index); });
    if (schleifeText) schleifeText.classList.toggle("ist-dran", index === -2);
  }

  function takt(jetzt) {
    if (!laeuft) return;
    var a = abschnitte[schritt];
    var dauer;
    if (phase === "ruht") {
      if (jetzt - phaseStart >= VERWEILEN) { phase = "faehrt"; phaseStart = jetzt; markiere(a.schleife ? -2 : -1); }
    } else {
      dauer = Math.max(450, a.laenge / TEMPO);
      var t = Math.min(1, (jetzt - phaseStart) / dauer);
      var e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      setzePunkt(a.pfad.getPointAtLength(e * a.laenge));
      if (t >= 1) {
        markiere(a.schleife ? 1 : a.ziel);
        schritt += 1;
        if (schritt >= abschnitte.length) {
          schritt = 0;
          setzePunkt(abschnitte[0].pfad.getPointAtLength(0));
          markiere(0);
        }
        phase = "ruht";
        phaseStart = jetzt;
      }
    }
    rafId = requestAnimationFrame(takt);
  }

  function starten() {
    if (!zeichnen()) return;
    if (reduziert.matches) {
      if (punkt) punkt.style.display = "none";
      markiere(-1);
      return;
    }
    if (laeuft) return;
    laeuft = true;
    schritt = 0;
    phase = "ruht";
    phaseStart = performance.now();
    markiere(0);
    rafId = requestAnimationFrame(takt);
  }

  function anhalten() {
    laeuft = false;
    cancelAnimationFrame(rafId);
  }

  function offen() {
    return document.documentElement.dataset.reiter === "organigramm" || !document.documentElement.dataset.reiter;
  }

  document.addEventListener("reiter:wechsel", function (e) {
    if (e.detail.id === "organigramm") requestAnimationFrame(starten);
    else anhalten();
  });
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) anhalten();
    else if (offen()) starten();
  });

  var warten = 0;
  function neuZeichnen() {
    clearTimeout(warten);
    warten = setTimeout(function () {
      if (!offen()) return;
      var lief = laeuft;
      anhalten();
      if (lief || reduziert.matches) starten(); else zeichnen();
    }, 120);
  }
  if ("ResizeObserver" in window) new ResizeObserver(neuZeichnen).observe(wurzel);
  else window.addEventListener("resize", neuZeichnen);

  if (offen()) requestAnimationFrame(starten);
})();
