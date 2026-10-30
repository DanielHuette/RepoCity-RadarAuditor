// Screenshots als WebGL-Flächen: jedes Bild bekommt eine Fläche, die deckungsgleich über dem
// Bild im Dokument liegt (Pixelkamera). Beim schnellen Scrollen wölbt und schert die Fläche
// sich mit der Geschwindigkeit, bei Stillstand liegt sie wieder plan wie das Original.
import * as THREE from "../vendor/three.module.min.js";

const vertex = /* glsl */ `
  uniform float uGeschwindigkeit;   // Bildschirmhöhen je Sekunde, geglättet
  uniform float uZeit;
  uniform vec2 uGroesse;            // Breite, Höhe der Fläche in CSS-Pixeln
  varying vec2 vUv;
  void main() {
    vUv = uv;
    vec3 p = position;
    float v = clamp(uGeschwindigkeit, -1.6, 1.6);
    // Wölbung quer zur Scrollrichtung: die Mitte eilt voraus, die Ränder hängen nach
    p.y += sin(uv.x * 3.14159) * v * uGroesse.y * 0.045;
    // Scherung: Oberkante und Unterkante verschieben sich gegeneinander
    p.x += (uv.y - 0.5) * v * uGroesse.x * 0.05;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
  }
`;

const fragment = /* glsl */ `
  precision highp float;
  uniform sampler2D tBild;
  uniform float uGeschwindigkeit;
  uniform vec4 uSchnitt;      // sichtbarer Bereich in Gerätepixeln: links, unten, rechts, oben
  uniform float uRadius;      // Eckenrundung des sichtbaren Bereichs in Gerätepixeln
  uniform float uDeckkraft;
  varying vec2 vUv;

  // Abstand zu einem Rechteck mit runden Ecken; negativ = innen
  float rundesRechteck(vec2 p, vec2 mitte, vec2 halb, float r) {
    vec2 q = abs(p - mitte) - halb + r;
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
  }

  void main() {
    vec2 mitte = 0.5 * (uSchnitt.xy + uSchnitt.zw);
    vec2 halb = 0.5 * (uSchnitt.zw - uSchnitt.xy);
    float d = rundesRechteck(gl_FragCoord.xy, mitte, halb, uRadius);
    float kante = 1.0 - smoothstep(-0.8, 0.8, d);
    if (kante <= 0.001) discard;

    float spalt = clamp(uGeschwindigkeit, -1.6, 1.6) * 0.006;
    float r = texture2D(tBild, vUv + vec2(0.0, spalt)).r;
    float g = texture2D(tBild, vUv).g;
    float b = texture2D(tBild, vUv - vec2(0.0, spalt)).b;
    float a = kante * uDeckkraft;
    gl_FragColor = vec4(vec3(r, g, b) * a, a);
  }
`;

export function bilder(auswahl, scroll) {
  const szene = new THREE.Scene();
  const kamera = new THREE.OrthographicCamera(0, 1, 0, -1, -10, 10);
  const lader = new THREE.TextureLoader();
  const flaechen = [];
  let dpr = 1;
  let g = { breite: 1, hoehe: 1 };

  auswahl.forEach((img) => {
    const quelle = img.currentSrc || img.src;
    const rahmen = img.closest(".output__shot") || img.parentElement;
    const panel = img.closest(".panel") || rahmen;
    const material = new THREE.ShaderMaterial({
      uniforms: {
        tBild: { value: null },
        uGeschwindigkeit: { value: 0 },
        uZeit: { value: 0 },
        uGroesse: { value: new THREE.Vector2(1, 1) },
        uSchnitt: { value: new THREE.Vector4() },
        uRadius: { value: 12 },
        uDeckkraft: { value: 0 }
      },
      vertexShader: vertex,
      fragmentShader: fragment,
      transparent: true,
      depthTest: false,
      depthWrite: false
    });
    // 24 × 24 Unterteilungen, damit die Wölbung weich bleibt
    const netz = new THREE.Mesh(new THREE.PlaneGeometry(1, 1, 24, 24), material);
    netz.frustumCulled = false;
    szene.add(netz);
    const eintrag = { img, rahmen, panel, material, netz, bereit: false };
    flaechen.push(eintrag);
    lader.load(quelle, (textur) => {
      textur.colorSpace = THREE.SRGBColorSpace;
      textur.minFilter = THREE.LinearMipmapLinearFilter;
      textur.generateMipmaps = true;
      textur.anisotropy = 4;
      material.uniforms.tBild.value = textur;
      eintrag.bereit = true;
      img.classList.add("ist-webgl");
    });
  });

  function anpassen(groesse) {
    g = groesse;
    dpr = groesse.dpr;
    kamera.right = g.breite;
    kamera.bottom = -g.hoehe;
    kamera.updateProjectionMatrix();
  }

  function sichtbar() {
    return flaechen.some((f) => {
      if (!f.bereit) return false;
      const r = f.rahmen.getBoundingClientRect();
      return r.bottom > 0 && r.top < g.hoehe;
    });
  }

  function zeichne(renderer, z) {
    flaechen.forEach((f) => {
      if (!f.bereit) { f.netz.visible = false; return; }
      const r = f.img.getBoundingClientRect();
      const s = f.rahmen.getBoundingClientRect();
      const p = f.panel.getBoundingClientRect();
      f.netz.visible = s.bottom > 0 && s.top < g.hoehe;
      if (!f.netz.visible) return;
      f.netz.position.set(r.left + r.width / 2, -(r.top + r.height / 2), 0);
      f.netz.scale.set(r.width, r.height, 1);
      const u = f.material.uniforms;
      u.uGroesse.value.set(r.width, r.height);
      u.uGeschwindigkeit.value = z.geschwindigkeit;
      u.uZeit.value = z.zeit;
      // Schnittfenster = Rahmen ∩ Panel, in Gerätepixeln, y von unten gezählt
      const links = Math.max(s.left, p.left);
      const rechts = Math.min(s.right, p.right);
      const oben = Math.max(s.top, p.top);
      const unten = Math.min(s.bottom, p.bottom);
      u.uSchnitt.value.set(links * dpr, (g.hoehe - unten) * dpr, rechts * dpr, (g.hoehe - oben) * dpr);
      u.uRadius.value = 12 * dpr;
      u.uDeckkraft.value = Math.min(1, u.uDeckkraft.value + z.dt * 3);
    });
    renderer.render(szene, kamera);
  }

  void scroll;
  return { sichtbar, zeichne, anpassen };
}
