// Radar im Kopfbereich: eine schräg liegende Scheibe aus Punkten, über die ein goldener
// Strahl wandert. Punkte leuchten kurz auf, wenn der Strahl sie passiert, und klingen ab.
// Die Maus kippt die Scheibe um wenige Grad, Scrollen schiebt sie nach oben aus dem Bild.
import * as THREE from "../vendor/three.module.min.js";

const TAU = Math.PI * 2;

const vertex = /* glsl */ `
  uniform float uStrahl;     // aktueller Winkel des Strahls
  uniform float uPixel;      // Gerätepixel je CSS-Pixel
  uniform float uDeckkraft;
  attribute float aWinkel;
  attribute float aGroesse;
  varying float vLicht;

  void main() {
    // Winkelabstand hinter dem Strahl, 0 … 2π; dicht hinter dem Strahl ist es am hellsten
    float hinter = mod(uStrahl - aWinkel, 6.28318530718);
    float nachglut = exp(-hinter * 1.9);
    vLicht = (0.10 + 0.9 * nachglut) * uDeckkraft;
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = aGroesse * uPixel * (1.0 + nachglut * 1.6) * (9.0 / -mv.z);
    gl_Position = projectionMatrix * mv;
  }
`;

const fragment = /* glsl */ `
  precision mediump float;
  uniform vec3 uFarbe;
  varying float vLicht;
  void main() {
    float d = length(gl_PointCoord - 0.5);
    float kreis = smoothstep(0.5, 0.18, d);
    gl_FragColor = vec4(uFarbe * vLicht * kreis, vLicht * kreis);
  }
`;

export function radar() {
  const szene = new THREE.Scene();
  const kamera = new THREE.PerspectiveCamera(38, 1, 0.1, 50);
  kamera.position.set(0, 0, 9);
  const gruppe = new THREE.Group();
  szene.add(gruppe);

  // Punkte: gleichmäßig in der Scheibe verteilt (Wurzel des Zufallsradius), leicht zu den Ringen hin verdichtet.
  const anzahl = 2600;
  const pos = new Float32Array(anzahl * 3);
  const winkel = new Float32Array(anzahl);
  const groesse = new Float32Array(anzahl);
  for (let i = 0; i < anzahl; i++) {
    const w = Math.random() * TAU;
    let r = Math.sqrt(Math.random());
    // Jeder dritte Punkt rückt auf einen der vier Ringe, damit die Ringe als Punktketten mitleuchten
    if (Math.random() < 0.35) r = Math.round(r * 4) / 4 + (Math.random() - 0.5) * 0.015;
    pos[i * 3] = Math.cos(w) * r * 3.2;
    pos[i * 3 + 1] = 0;
    pos[i * 3 + 2] = Math.sin(w) * r * 3.2;
    winkel[i] = w;
    groesse[i] = 1.2 + Math.random() * 1.8;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("aWinkel", new THREE.BufferAttribute(winkel, 1));
  geo.setAttribute("aGroesse", new THREE.BufferAttribute(groesse, 1));
  const material = new THREE.ShaderMaterial({
    uniforms: {
      uStrahl: { value: 0 },
      uPixel: { value: Math.min(window.devicePixelRatio || 1, 1.75) },
      uDeckkraft: { value: 1 },
      uFarbe: { value: new THREE.Color("#dcb25c") }
    },
    vertexShader: vertex,
    fragmentShader: fragment,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending
  });
  const punkte = new THREE.Points(geo, material);
  gruppe.add(punkte);

  // Vier Ringe und der Strahl als feine Linien.
  const linienMaterial = new THREE.LineBasicMaterial({ color: 0xdcb25c, transparent: true, opacity: 0.16 });
  for (let k = 1; k <= 4; k++) {
    const r = (k / 4) * 3.2;
    const pts = [];
    for (let i = 0; i <= 96; i++) {
      const w = (i / 96) * TAU;
      pts.push(new THREE.Vector3(Math.cos(w) * r, 0, Math.sin(w) * r));
    }
    gruppe.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), linienMaterial));
  }
  const strahlMaterial = new THREE.LineBasicMaterial({ color: 0xf0d492, transparent: true, opacity: 0.55 });
  const strahl = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(3.2, 0, 0)]),
    strahlMaterial
  );
  gruppe.add(strahl);

  // Scheibe kippen: von schräg oben betrachtet, wie ein Radarschirm auf dem Tisch.
  gruppe.rotation.x = 0.98;
  gruppe.rotation.z = -0.12;

  let g = { breite: 1, hoehe: 1 };
  let ziel = new THREE.Vector3();
  // On narrow screens the disc lies behind the headline and lead text; it glows less there so the text stays readable.
  let staerke = 1;
  function anpassen(groesseNeu) {
    g = groesseNeu;
    kamera.aspect = g.breite / g.hoehe;
    kamera.updateProjectionMatrix();
    // Mitte der Scheibe: auf breiten Schirmen links der Mitte hinter dem Text, auf schmalen mittig oben.
    const breit = g.breite > 900;
    const ndcX = breit ? -0.22 : 0;
    const ndcY = breit ? 0.05 : 0.35;
    const halbH = Math.tan((kamera.fov * Math.PI) / 360) * 9;
    ziel.set(ndcX * halbH * kamera.aspect, ndcY * halbH, 0);
    const skala = breit ? 1 : 0.8;
    staerke = breit ? 1 : 0.5;
    gruppe.scale.setScalar(skala);
  }

  let winkelStrahl = 0;
  let kippX = 0;
  let kippY = 0;

  function sichtbar(z) {
    return z.scrollY < g.hoehe * 0.7;
  }

  function zeichne(renderer, z) {
    winkelStrahl = (winkelStrahl + z.dt * 0.55) % TAU;
    material.uniforms.uStrahl.value = winkelStrahl;
    strahl.rotation.y = -winkelStrahl;
    // Ausblenden beim Scrollen: die Scheibe hebt sich und verblasst
    const weg = Math.min(1, z.scrollY / (g.hoehe * 0.65));
    const deck = (1 - weg * weg) * staerke;
    material.uniforms.uDeckkraft.value = deck;
    linienMaterial.opacity = 0.16 * deck;
    strahlMaterial.opacity = 0.55 * deck;
    gruppe.position.set(ziel.x, ziel.y + weg * 2.5, 0);
    // Maus: sanftes Nachkippen, nie mehr als etwa 4 Grad
    kippX += ((z.maus.y * -0.07) - kippX) * Math.min(1, z.dt * 3);
    kippY += ((z.maus.x * 0.09) - kippY) * Math.min(1, z.dt * 3);
    gruppe.rotation.x = 0.98 + kippX;
    gruppe.rotation.z = -0.12 + kippY;
    renderer.render(szene, kamera);
  }

  return { sichtbar, zeichne, anpassen };
}
