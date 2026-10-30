// Der Projekt-Ring: 104 Prüfkarten stehen senkrecht auf einem Kreis, Außenseite zum Betrachter,
// nach dem Vorbild des Projektrings von unseen.co/projects/clou. Scrollen und Ziehen drehen ihn
// um die Y-Achse; die Karte vorn wächst leicht, Karten hinten versinken im Nebel.
//
// Mathematik der Anordnung (alles im Vertex-Shader, eine einzige Instanz-Zeichnung):
//   Winkel der Karte i:   θ_i = i · 2π / N + uRotation
//   Platz auf dem Ring:   P_i = (R · sin θ_i, 0, R · cos θ_i)
//   Blickrichtung:        Karte um θ_i um die Y-Achse gedreht → Normale zeigt radial nach außen
//   Radius:               R = N · Platz je Karte / (2π); Karten sind breiter als ihr Platz und fächern sich
//   Fokus:                f = ½ + ½ · cos θ_i  (1 = vorn, 0 = hinten); Skala = 1 + 0,18 · f⁸
//   Nebel:                Deckkraft fällt linear mit der Tiefe im Kameraraum zwischen uNah und uFern
import * as THREE from "../vendor/three.module.min.js";
import { bereiche, karten, texte } from "./katalog.js";

const TAU = Math.PI * 2;
// Karten überlappen wie ein aufgefächertes Deck: Breite 2,4 bei einem Platz von 1,22 je Karte.
const KARTE = { breite: 2.1, hoehe: 3.4, platz: 1.22 };
const ATLAS = { spalten: 10, zeilen: 11, px: 240, py: 300 };

const vertex = /* glsl */ `
  uniform float uRotation;
  uniform float uRadius;
  uniform float uAnzahl;
  uniform float uGeschwindigkeit;   // normierte Drehgeschwindigkeit, -1 … 1
  uniform float uZeit;
  uniform float uHervor;            // Index der Karte unter dem Zeiger, -1 = keine
  uniform float uGewaehlt;          // Index der angeklickten Karte, -1 = keine
  attribute float aIndex;
  attribute vec2 aKachel;
  varying vec2 vUv;
  varying vec2 vKachel;
  varying float vTiefe;
  varying float vFokus;
  varying float vHervor;

  void main() {
    float winkel = aIndex / uAnzahl * 6.28318530718 + uRotation;
    float fokus = 0.5 + 0.5 * cos(winkel);
    float gewaehlt = step(abs(aIndex - uGewaehlt), 0.5);
    float hervor = max(step(abs(aIndex - uHervor), 0.5), gewaehlt);
    float skala = 1.0 + 0.18 * pow(fokus, 8.0) + 0.1 * hervor;
    vHervor = hervor;
    vec3 p = position * skala;

    // Wellen-Verzerrung: beim schnellen Drehen biegt sich die Karte entlang ihrer Höhe
    // und schert in Drehrichtung; bei Stillstand ist uGeschwindigkeit 0 und die Karte plan.
    float welle = sin(uv.y * 3.14159 + uZeit * 4.0 + aIndex * 0.35) * uGeschwindigkeit * 0.22;
    p.z += welle * (1.0 - fokus * 0.5);
    p.x += (uv.y - 0.5) * uGeschwindigkeit * 0.45;
    // Hervorgehobene Karte tritt radial aus dem Fächer heraus, die gewählte ganz nach vorn
    p.z += 0.3 * hervor + 0.5 * gewaehlt;

    float s = sin(winkel);
    float c = cos(winkel);
    vec3 welt = vec3(p.x * c + p.z * s, p.y, -p.x * s + p.z * c);
    welt += vec3(uRadius * s, 0.0, uRadius * c);

    vec4 mv = modelViewMatrix * vec4(welt, 1.0);
    vTiefe = -mv.z;
    vFokus = fokus;
    vUv = uv;
    vKachel = aKachel;
    gl_Position = projectionMatrix * mv;
  }
`;

const fragment = /* glsl */ `
  precision highp float;
  uniform sampler2D tAtlas;
  uniform vec2 uRaster;
  uniform vec2 uAtlasPx;
  uniform float uNah;
  uniform float uFern;
  uniform float uGeschwindigkeit;
  uniform vec3 uNebelFarbe;
  uniform vec2 uClip;               // visible rows of the ring stage in device pixels, from the bottom
  varying vec2 vUv;
  varying vec2 vKachel;
  varying float vTiefe;
  varying float vFokus;
  varying float vHervor;

  void main() {
    // The canvas is fixed behind the whole page; outside its own stage the ring must not show
    // through the next sections' headings and lists.
    if (gl_FragCoord.y < uClip.x || gl_FragCoord.y > uClip.y) discard;
    vec2 zelle = 1.0 / uRaster;
    vec2 rand = 1.0 / uAtlasPx;
    vec2 lo = vKachel * zelle + rand;
    vec2 hi = (vKachel + 1.0) * zelle - rand;
    vec2 uv0 = (vKachel + vUv) * zelle;
    // Farbspaltung in Drehrichtung, auf die eigene Kachel begrenzt
    float spalt = uGeschwindigkeit * 0.012;
    float r = texture2D(tAtlas, clamp(uv0 + vec2(spalt, 0.0), lo, hi)).r;
    float g = texture2D(tAtlas, clamp(uv0, lo, hi)).g;
    float b = texture2D(tAtlas, clamp(uv0 - vec2(spalt, 0.0), lo, hi)).b;
    vec3 farbe = vec3(r, g, b);

    float nah = smoothstep(uFern, uNah, vTiefe);   // 1 vorn, 0 hinten
    float deckkraft = mix(0.45, 1.0, pow(nah, 1.2));
    farbe = mix(uNebelFarbe, farbe, mix(0.6, 1.0, nah));
    farbe += vec3(0.08, 0.06, 0.02) * pow(vFokus, 12.0);   // warmes Glimmen auf der Karte vorn
    // Karte unter dem Zeiger oder gewählt: heller und ein goldener Rand
    float randAbstand = min(min(vUv.x, 1.0 - vUv.x) * 1.25, min(vUv.y, 1.0 - vUv.y));
    farbe += vHervor * (vec3(0.06, 0.05, 0.02) + vec3(0.9, 0.7, 0.35) * (1.0 - smoothstep(0.0, 0.03, randAbstand)));
    deckkraft = max(deckkraft, vHervor * 0.9);
    gl_FragColor = vec4(farbe * deckkraft, deckkraft);
  }
`;

function kartenAtlas(sprache) {
  const liste = karten(sprache);
  const canvas = document.createElement("canvas");
  canvas.width = ATLAS.spalten * ATLAS.px;
  canvas.height = ATLAS.zeilen * ATLAS.py;
  const ctx = canvas.getContext("2d");
  const font = getComputedStyle(document.body).fontFamily;

  // Zeilenumbruch nach Wörtern; die letzte erlaubte Zeile wird mit … gekürzt, wenn Text übrig bleibt.
  function zeile(text, x, y, maxBreite, zeilenHoehe, maxZeilen) {
    const woerter = text.split(" ");
    const zeilen = [];
    let aktuell = "";
    for (const wort of woerter) {
      const probe = aktuell ? aktuell + " " + wort : wort;
      if (ctx.measureText(probe).width > maxBreite && aktuell) {
        zeilen.push(aktuell);
        aktuell = wort;
      } else {
        aktuell = probe;
      }
    }
    if (aktuell) zeilen.push(aktuell);
    if (zeilen.length > maxZeilen) {
      let letzte = zeilen.slice(maxZeilen - 1).join(" ");
      while (ctx.measureText(letzte + "…").width > maxBreite && letzte.length > 1) letzte = letzte.slice(0, -1);
      zeilen.length = maxZeilen - 1;
      zeilen.push(letzte.trimEnd() + "…");
    }
    zeilen.forEach((t, n) => ctx.fillText(t, x, y + n * zeilenHoehe));
  }

  liste.forEach((karte, i) => {
    const x0 = (i % ATLAS.spalten) * ATLAS.px;
    const y0 = Math.floor(i / ATLAS.spalten) * ATLAS.py;
    const b = bereiche[karte.bereich];
    const verlauf = ctx.createLinearGradient(x0, y0, x0, y0 + ATLAS.py);
    verlauf.addColorStop(0, "#1b2745");
    verlauf.addColorStop(1, "#0c1326");
    ctx.fillStyle = verlauf;
    ctx.fillRect(x0, y0, ATLAS.px, ATLAS.py);
    // Goldband am Kopf: seine Höhe folgt dem Gewicht des Bereichs (2 → schmal, 14 → breit),
    // so unterscheiden sich die Sektoren des Rings schon aus der Ferne.
    const band = Math.round(ATLAS.py * (0.12 + (b.gewicht / 14) * 0.3));
    const gold = ctx.createLinearGradient(x0, y0, x0, y0 + band);
    gold.addColorStop(0, "rgba(240, 212, 146, 0.95)");
    gold.addColorStop(1, "rgba(169, 128, 58, 0.75)");
    ctx.fillStyle = gold;
    ctx.fillRect(x0, y0, ATLAS.px, band);
    ctx.fillStyle = "#120d04";
    ctx.font = `700 ${Math.min(44, band - 10)}px ${font}`;
    ctx.textBaseline = "middle";
    ctx.fillText(String(karte.kennung.slice(1)), x0 + 14, y0 + band / 2 + 1);
    ctx.textBaseline = "alphabetic";
    ctx.strokeStyle = "rgba(220, 178, 92, 0.55)";
    ctx.lineWidth = 2;
    ctx.strokeRect(x0 + 1, y0 + 1, ATLAS.px - 2, ATLAS.py - 2);

    ctx.fillStyle = "#dcb25c";
    ctx.font = `600 16px ${font}`;
    ctx.fillText(karte.kennung.toUpperCase(), x0 + 16, y0 + band + 28);
    ctx.textAlign = "right";
    ctx.fillStyle = "#a9a79f";
    ctx.font = `500 15px ${font}`;
    ctx.fillText(`${texte[sprache].gewicht} ${b.gewicht}`, x0 + ATLAS.px - 16, y0 + band + 28);
    ctx.textAlign = "left";

    ctx.fillStyle = "#f3f1ea";
    ctx.font = `600 21px ${font}`;
    zeile(karte.name, x0 + 16, y0 + band + 64, ATLAS.px - 32, 27, Math.max(2, Math.floor((ATLAS.py - band - 120) / 27)));

    ctx.fillStyle = "rgba(220, 178, 92, 0.25)";
    ctx.fillRect(x0 + 16, y0 + ATLAS.py - 52, ATLAS.px - 32, 1);
    ctx.fillStyle = "#a9a79f";
    ctx.font = `500 14px ${font}`;
    zeile(sprache === "en" ? b.en : b.de, x0 + 16, y0 + ATLAS.py - 30, ATLAS.px - 32, 17, 1);
  });

  const textur = new THREE.CanvasTexture(canvas);
  textur.colorSpace = THREE.SRGBColorSpace;
  textur.anisotropy = 4;
  textur.minFilter = THREE.LinearMipmapLinearFilter;
  textur.generateMipmaps = true;
  return { textur, liste };
}

export function ring(wurzel, sprache, scroll) {
  const stage = wurzel.querySelector(".ring__stage");
  const labelsEl = wurzel.querySelector(".ring__labels");
  const bereichEl = wurzel.querySelector(".ring__area");
  const metaEl = wurzel.querySelector(".ring__meta");
  const { textur, liste } = kartenAtlas(sprache);
  const N = liste.length;
  const R = (N * KARTE.platz) / TAU;

  const szene = new THREE.Scene();
  const kamera = new THREE.PerspectiveCamera(32, 1, 0.1, 400);

  const basis = new THREE.PlaneGeometry(KARTE.breite, KARTE.hoehe);
  const geometrie = new THREE.InstancedBufferGeometry();
  geometrie.index = basis.index;
  geometrie.attributes.position = basis.attributes.position;
  geometrie.attributes.uv = basis.attributes.uv;
  const index = new Float32Array(N);
  const kachel = new Float32Array(N * 2);
  for (let i = 0; i < N; i++) {
    index[i] = i;
    kachel[i * 2] = i % ATLAS.spalten;
    // Die Leinwand zählt Zeilen von oben, die Textur von unten: deshalb gespiegelt.
    kachel[i * 2 + 1] = ATLAS.zeilen - 1 - Math.floor(i / ATLAS.spalten);
  }
  geometrie.setAttribute("aIndex", new THREE.InstancedBufferAttribute(index, 1));
  geometrie.setAttribute("aKachel", new THREE.InstancedBufferAttribute(kachel, 2));
  geometrie.instanceCount = N;

  const material = new THREE.ShaderMaterial({
    uniforms: {
      tAtlas: { value: textur },
      uRaster: { value: new THREE.Vector2(ATLAS.spalten, ATLAS.zeilen) },
      uAtlasPx: { value: new THREE.Vector2(ATLAS.spalten * ATLAS.px, ATLAS.zeilen * ATLAS.py) },
      uRotation: { value: 0 },
      uRadius: { value: R },
      uAnzahl: { value: N },
      uGeschwindigkeit: { value: 0 },
      uZeit: { value: 0 },
      uNah: { value: 1 },
      uFern: { value: 10 },
      uNebelFarbe: { value: new THREE.Color("#0b1222") },
      uHervor: { value: -1 },
      uGewaehlt: { value: -1 },
      uClip: { value: new THREE.Vector2(0, 0) }
    },
    vertexShader: vertex,
    fragmentShader: fragment,
    transparent: true,
    side: THREE.DoubleSide,
    depthWrite: true
  });
  const netz = new THREE.Mesh(geometrie, material);
  netz.frustumCulled = false;
  szene.add(netz);

  // Beschriftungen je Bereich, außen um den Ring, im Dokument statt in WebGL (scharf, auswählbar).
  const labels = bereiche.map((b, bi) => {
    const el = document.createElement("span");
    el.className = "ring__label";
    el.innerHTML = `${sprache === "en" ? b.en : b.de}<sup>${b.gewicht}</sup>`;
    labelsEl.appendChild(el);
    const start = liste.findIndex((k) => k.bereich === bi);
    const mitte = start + (b.pruefungen.length - 1) / 2;
    return { el, winkel: (mitte / N) * TAU, ziel: new THREE.Vector3() };
  });

  // Drehung = Scrollanteil + Zieh-Anteil + Eigendrehung im Stillstand + Ausrichtung auf eine gewählte Karte.
  let scrollDrehung = 0;
  let ziehDrehung = 0;
  let ziehTempo = 0;
  let eigen = 0;
  let letzteDrehung = 0;
  let tempoGlatt = 0;
  let vorn = -1;

  // Zeiger: Ziehen dreht, ein kurzer Klick ohne Bewegung wählt die nächste Karte.
  let zeigerAktiv = false;
  let zeigerX = 0;
  let zeigerStart = { x: 0, y: 0 };
  let zeigerWeg = 0;
  let hervor = -1;

  // Gewählte Karte: der Ring dreht sie nach vorn, die Kamera fährt heran, die Tafel zeigt die Prüfung.
  const wahl = { index: -1, drehZiel: 0, dreh: 0, zoom: 0, zoomZiel: 0, scrollBeiWahl: 0 };
  const tafel = document.createElement("aside");
  tafel.className = "ring__tafel";
  tafel.hidden = true;
  tafel.innerHTML = `<button type="button" class="ring__zu" aria-label="${sprache === "en" ? "Close" : "Schließen"}">×</button>
    <p class="ring__tafel-kopf"></p>
    <h3 class="ring__tafel-name"></h3>
    <p class="ring__tafel-frage"></p>
    <p class="ring__tafel-wirkung"></p>`;
  stage.appendChild(tafel);
  const tafelKopf = tafel.querySelector(".ring__tafel-kopf");
  const tafelName = tafel.querySelector(".ring__tafel-name");
  const tafelFrage = tafel.querySelector(".ring__tafel-frage");
  const tafelWirkung = tafel.querySelector(".ring__tafel-wirkung");

  const hilfsVektor = new THREE.Vector3();
  const hilfsMatrix = new THREE.Matrix4();

  // Karte zum Zeiger: alle Kartenmitten auf den Schirm projizieren und die nächste nehmen, solange
  // der Zeiger innerhalb einer großzügigen Trefferzone liegt (halbe Kartenhöhe plus 28 Pixel).
  function treffer(clientX, clientY, drehung) {
    const s = stage.getBoundingClientRect();
    const px = clientX - s.left;
    const py = clientY - s.top;
    const halbTan = Math.tan((kamera.fov * Math.PI) / 360);
    hilfsMatrix.copy(kamera.matrixWorldInverse);
    let bester = -1;
    let besteWertung = Infinity;
    for (let i = 0; i < N; i++) {
      const w = (i / N) * TAU + drehung;
      const nah = 0.5 + 0.5 * Math.cos(w);
      if (nah < 0.18) continue;                       // Rückseite: nicht wählbar
      hilfsVektor.set(Math.sin(w) * R, 0, Math.cos(w) * R).applyMatrix4(hilfsMatrix);
      const tiefe = -hilfsVektor.z;
      if (tiefe <= 0) continue;
      const halbHoehePx = ((KARTE.hoehe * 0.5) / (tiefe * halbTan)) * (groesse.hoehe * 0.5);
      hilfsVektor.set(Math.sin(w) * R, 0, Math.cos(w) * R).project(kamera);
      const x = (hilfsVektor.x * 0.5 + 0.5) * groesse.breite - s.left;
      const y = (-hilfsVektor.y * 0.5 + 0.5) * groesse.hoehe - s.top;
      const dx = Math.abs(px - x);
      const dy = Math.abs(py - y);
      if (dy > halbHoehePx + 28 || dx > halbHoehePx * 0.5 + 28) continue;
      // Näher am Zeiger und weiter vorn gewinnt
      const wertung = Math.hypot(dx * 1.6, dy) / (0.35 + nah);
      if (wertung < besteWertung) { besteWertung = wertung; bester = i; }
    }
    return bester;
  }

  function normiereWinkel(a) {
    return ((((a + Math.PI) % TAU) + TAU) % TAU) - Math.PI;
  }

  function waehle(i, drehung) {
    const k = liste[i];
    const b = bereiche[k.bereich];
    wahl.index = i;
    // Zielwinkel: Karte i vorn ⇒ i/N·2π + drehung ≡ 0; kürzester Weg dorthin.
    wahl.drehZiel = wahl.dreh + normiereWinkel(-(i / N) * TAU - drehung);
    wahl.zoomZiel = 1;
    wahl.scrollBeiWahl = scrollDrehung + ziehDrehung;
    material.uniforms.uGewaehlt.value = i;
    tafelKopf.textContent = `${k.kennung.toUpperCase()} · ${sprache === "en" ? b.en : b.de} · ${texte[sprache].gewicht} ${b.gewicht}`;
    tafelName.textContent = k.name;
    // Kundenfrage und Folge liegen im Prüfkatalog nur auf Deutsch vor.
    tafelFrage.textContent = sprache === "en" ? "" : k.frage;
    tafelWirkung.textContent = sprache === "en" ? "" : k.wirkung;
    tafel.hidden = false;
    wurzel.classList.add("ist-gewaehlt");
  }

  function abwaehlen() {
    if (wahl.index < 0) return;
    wahl.index = -1;
    wahl.zoomZiel = 0;
    material.uniforms.uGewaehlt.value = -1;
    wurzel.classList.remove("ist-gewaehlt");
  }

  tafel.addEventListener("pointerdown", (e) => e.stopPropagation());
  tafel.querySelector(".ring__zu").addEventListener("click", abwaehlen);
  window.addEventListener("keydown", (e) => { if (e.key === "Escape") abwaehlen(); });

  stage.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    zeigerAktiv = true;
    zeigerX = e.clientX;
    zeigerStart = { x: e.clientX, y: e.clientY };
    zeigerWeg = 0;
    ziehTempo = 0;
    stage.setPointerCapture(e.pointerId);
    stage.classList.add("ist-am-ziehen");
  });
  stage.addEventListener("pointermove", (e) => {
    if (!zeigerAktiv) {
      if (e.pointerType === "mouse") {
        hervor = treffer(e.clientX, e.clientY, letzteDrehung);
        material.uniforms.uHervor.value = hervor;
        stage.classList.toggle("hat-treffer", hervor >= 0);
      }
      return;
    }
    const dx = e.clientX - zeigerX;
    zeigerX = e.clientX;
    zeigerWeg = Math.max(zeigerWeg, Math.hypot(e.clientX - zeigerStart.x, e.clientY - zeigerStart.y));
    const schritt = (dx / stage.clientWidth) * TAU * 0.55;
    ziehDrehung += schritt;
    ziehTempo = schritt;
  });
  function loslassen(e) {
    if (!zeigerAktiv) return;
    zeigerAktiv = false;
    stage.classList.remove("ist-am-ziehen");
    if (e.type === "pointerup" && zeigerWeg < 6) {
      const i = treffer(e.clientX, e.clientY, letzteDrehung);
      if (i >= 0 && i !== wahl.index) waehle(i, letzteDrehung);
      else abwaehlen();
    }
  }
  stage.addEventListener("pointerup", loslassen);
  stage.addEventListener("pointercancel", loslassen);
  stage.addEventListener("pointerleave", () => {
    if (hervor >= 0) { hervor = -1; material.uniforms.uHervor.value = -1; stage.classList.remove("hat-treffer"); }
  });

  let groesse = { breite: 1, hoehe: 1 };
  const basisPos = new THREE.Vector3();
  const basisBlick = new THREE.Vector3();
  const kartenVorn = new THREE.Vector3();   // Mitte der Karte, die vorn steht
  const blick = new THREE.Vector3();
  function anpassen(g) {
    groesse = g;
    kamera.aspect = g.breite / g.hoehe;
    // Kamera schräg von oben (Vorbild: Elevation rund 37°), Abstand so, dass auch die nahe
    // Ringhälfte in die Breite passt: sie liegt um R·cos(Elevation) näher als der Mittelpunkt.
    const schmal = g.breite <= 720;
    // Schmale Schirme sehen den Ring flacher und näher: die vorderen Karten werden lesbar, der Ring ragt seitlich hinaus.
    const sinE = schmal ? 0.42 : 0.6;
    const cosE = Math.sqrt(1 - sinE * sinE);
    const halbTan = Math.tan((kamera.fov * Math.PI) / 360);
    const halbBreite = R + KARTE.breite * 0.8;
    const fuellung = schmal ? 1.35 : 0.74;   // Anteil der Fensterbreite, den der Ring einnimmt
    const abstand = R * cosE + halbBreite / (halbTan * kamera.aspect * fuellung);
    basisPos.set(0, abstand * sinE, abstand * cosE);
    // Blick etwas vor die Mitte, damit die perspektivisch größere nahe Hälfte nicht am Rand klebt
    basisBlick.set(0, 0, R * 0.14);
    kamera.position.copy(basisPos);
    kamera.lookAt(basisBlick);
    kamera.updateProjectionMatrix();
    material.uniforms.uNah.value = abstand - R * cosE * 0.95;
    material.uniforms.uFern.value = abstand + R * cosE * 1.05;
  }

  function sichtbar() {
    const r = wurzel.getBoundingClientRect();
    return r.bottom > 0 && r.top < groesse.hoehe;
  }

  function zeichne(renderer, z) {
    const r = wurzel.getBoundingClientRect();
    const weg = Math.max(1, r.height - groesse.hoehe);
    const fortschritt = Math.min(1, Math.max(0, -r.top / weg));
    scrollDrehung = -fortschritt * TAU * 0.9;

    if (!zeigerAktiv) {
      ziehDrehung += ziehTempo;
      ziehTempo *= 0.94;
      if (Math.abs(ziehTempo) < 0.00005) ziehTempo = 0;
    }
    // Gewählte Karte: Eigendrehung ruht; scrollt oder zieht man merklich weiter, löst sich die Wahl.
    if (wahl.index >= 0) {
      if (Math.abs(scrollDrehung + ziehDrehung - wahl.scrollBeiWahl) > 0.03) abwaehlen();
    } else {
      eigen += z.dt * 0.035;
    }
    wahl.dreh += (wahl.drehZiel - wahl.dreh) * Math.min(1, z.dt * 5);
    wahl.zoom += (wahl.zoomZiel - wahl.zoom) * Math.min(1, z.dt * 4);
    if (wahl.zoom < 0.002 && wahl.zoomZiel === 0 && !tafel.hidden) tafel.hidden = true;

    // Kamera: bei Wahl fährt sie 70 % des Wegs auf die vordere Karte zu und blickt auf sie.
    kartenVorn.set(0, KARTE.hoehe * 0.05, R);
    const ease = wahl.zoom * wahl.zoom * (3 - 2 * wahl.zoom);
    kamera.position.copy(basisPos).lerp(kartenVorn, ease * 0.7);
    // Blickpunkt rechts neben die Karte, damit sie links der Mitte steht und die Tafel rechts Platz hat
    const seitlich = groesse.breite > 720 ? 1.6 : 0;
    blick.copy(basisBlick).lerp(kartenVorn, ease).x += seitlich * ease;
    kamera.lookAt(blick);
    kamera.updateMatrixWorld();

    const drehung = scrollDrehung + ziehDrehung + eigen + wahl.dreh;
    const tempo = (drehung - letzteDrehung) / Math.max(0.001, z.dt);
    letzteDrehung = drehung;
    tempoGlatt += (tempo - tempoGlatt) * Math.min(1, z.dt * 8);
    const normiert = Math.max(-1, Math.min(1, tempoGlatt / 4.5));

    material.uniforms.uRotation.value = drehung;
    material.uniforms.uGeschwindigkeit.value = normiert;
    material.uniforms.uZeit.value = z.zeit;

    // Welche Karte steht vorn? θ_i ≡ 0 (mod 2π)  ⇒  i = −uRotation · N / 2π
    const i = ((Math.round((-drehung / TAU) * N) % N) + N) % N;
    if (i !== vorn) {
      vorn = i;
      const b = bereiche[liste[i].bereich];
      bereichEl.textContent = sprache === "en" ? b.en : b.de;
      metaEl.textContent = `${texte[sprache].gewicht} ${b.gewicht} · ${b.pruefungen.length} ${texte[sprache].pruefungen}`;
      wurzel.style.setProperty("--ring-fokus", String(liste[i].bereich));
    }

    // Beschriftungen auf den Bildschirm projizieren: Weltpunkt außen am Ring → Kamera → Pixel,
    // dann vom Fenster in die Koordinaten der (klebenden) Bühne umgerechnet.
    const s = stage.getBoundingClientRect();
    // Solange die Bühne noch nicht oben klebt, wandert das Bild mit ihr: Blickfenster um s.top verschieben.
    kamera.setViewOffset(groesse.breite, groesse.hoehe, 0, -s.top, groesse.breite, groesse.hoehe);
    const dpr = groesse.dpr || 1;
    material.uniforms.uClip.value.set(
      (groesse.hoehe - Math.min(groesse.hoehe, s.bottom)) * dpr,
      (groesse.hoehe - Math.max(0, s.top)) * dpr
    );
    labels.forEach((l) => {
      const w = l.winkel + drehung;
      l.ziel.set(Math.sin(w) * R * 1.16, KARTE.hoehe * 0.62, Math.cos(w) * R * 1.16).project(kamera);
      const x = (l.ziel.x * 0.5 + 0.5) * groesse.breite - s.left;
      const y = (-l.ziel.y * 0.5 + 0.5) * groesse.hoehe - s.top;
      const nah = 0.5 + 0.5 * Math.cos(w);
      // Links vom Ring endet der Text am Ankerpunkt, rechts beginnt er dort (wie beim Vorbild).
      const links = Math.sin(w) < 0;
      const schmal = groesse.breite <= 720;
      const breite = l.el.offsetWidth;
      const xk = links ? Math.max(12 + breite, x) : Math.min(s.width - 12 - breite, x);
      l.el.style.transform = `translate(${links ? "-100%" : "0"}, -50%) translate(${xk.toFixed(1)}px, ${y.toFixed(1)}px)`;
      l.el.style.opacity = schmal ? (nah > 0.55 ? ((nah - 0.55) / 0.45).toFixed(2) : "0") : (0.4 + nah * 0.6).toFixed(2);
      l.el.style.zIndex = nah > 0.5 ? "2" : "1";
    });

    renderer.render(szene, kamera);
  }

  return { sichtbar, zeichne, anpassen };
}
