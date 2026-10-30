// Die Bühne: eine bildschirmfüllende WebGL-Leinwand hinter dem Seiteninhalt.
// Alle Schichten (Radar, Bilder, Ring) zeichnen in ein gemeinsames Zwischenbild;
// ein letzter Durchgang legt Farbsaum an den Rändern und feines Korn darüber.
import * as THREE from "../vendor/three.module.min.js";

const nachbearbeitungVertex = /* glsl */ `
  out vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = vec4(position.xy, 0.0, 1.0);
  }
`;

// uAberration: Stärke des Farbsaums (0 = aus). uKorn: Amplitude des Rauschens.
// Der Farbsaum wächst quadratisch mit dem Abstand zur Bildmitte, die Mitte bleibt scharf.
const nachbearbeitungFragment = /* glsl */ `
  precision highp float;
  uniform sampler2D tBild;
  uniform float uZeit;
  uniform float uAberration;
  uniform float uKorn;
  in vec2 vUv;
  out vec4 fragColor;

  // Ganzzahl-Hash (PCG): liefert echtes weißes Rauschen ohne Streifen, anders als sin-Hashes.
  float rauschen(uvec2 p, uint t) {
    uint h = p.x * 1664525u + p.y * 1013904223u + t * 2654435761u;
    h = (h ^ (h >> 16u)) * 2246822519u;
    h = (h ^ (h >> 13u)) * 3266489917u;
    h = h ^ (h >> 16u);
    return float(h) / 4294967295.0;
  }

  void main() {
    vec2 d = vUv - 0.5;
    float r2 = dot(d, d);
    vec2 versatz = d * r2 * uAberration;
    vec4 mitte = texture(tBild, vUv);
    vec4 plus = texture(tBild, vUv + versatz);
    vec4 minus = texture(tBild, vUv - versatz);
    vec3 farbe = vec3(plus.r, mitte.g, minus.b);
    float a = max(mitte.a, max(plus.a, minus.a));
    float korn = (rauschen(uvec2(gl_FragCoord.xy), uint(mod(uZeit * 24.0, 1024.0))) - 0.5) * uKorn;
    // Korn liegt vor allem auf dem leeren Raum; über Bildern und Karten bleibt nur ein Hauch, damit Text lesbar bleibt.
    fragColor = vec4(farbe + korn * (1.0 - 0.7 * a), clamp(a + abs(korn) * 0.5, 0.0, 1.0));
    fragColor = linearToOutputTexel(fragColor);   // linear → sRGB, wie der Bildschirm es erwartet
  }
`;

export function buehne() {
  const leinwand = document.createElement("canvas");
  leinwand.className = "buehne";
  leinwand.setAttribute("aria-hidden", "true");
  document.body.prepend(leinwand);

  const renderer = new THREE.WebGLRenderer({
    canvas: leinwand,
    alpha: true,
    antialias: false,
    powerPreference: "high-performance"
  });
  renderer.setClearColor(0x000000, 0);
  renderer.autoClear = false;

  // Geht der Grafikkontext verloren (Treiber, Speicher), fällt die Seite auf das HTML zurück.
  leinwand.addEventListener("webglcontextlost", (e) => {
    e.preventDefault();
    document.documentElement.classList.remove("fx");
    leinwand.remove();
  });

  const dpr = Math.min(window.devicePixelRatio || 1, 1.75);
  renderer.setPixelRatio(dpr);

  // Zwischenbild mit Mehrfachabtastung (WebGL2), damit Kanten der Karten ruhig bleiben.
  const ziel = new THREE.WebGLRenderTarget(1, 1, {
    samples: 4,
    depthBuffer: true,
    stencilBuffer: false
  });

  const nachKamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  const nachSzene = new THREE.Scene();
  const nachMaterial = new THREE.ShaderMaterial({
    uniforms: {
      tBild: { value: ziel.texture },
      uZeit: { value: 0 },
      uAberration: { value: 0.012 },
      uKorn: { value: 0.028 }
    },
    vertexShader: nachbearbeitungVertex,
    fragmentShader: nachbearbeitungFragment,
    glslVersion: THREE.GLSL3,
    transparent: true,
    blending: THREE.NoBlending,
    depthTest: false,
    depthWrite: false
  });
  nachSzene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), nachMaterial));

  const schichten = [];
  const groesse = { breite: 1, hoehe: 1, dpr };

  function anpassen() {
    groesse.breite = window.innerWidth;
    groesse.hoehe = window.innerHeight;
    renderer.setSize(groesse.breite, groesse.hoehe, false);
    ziel.setSize(Math.round(groesse.breite * dpr), Math.round(groesse.hoehe * dpr));
    schichten.forEach((s) => s.anpassen && s.anpassen(groesse));
  }
  window.addEventListener("resize", anpassen);
  anpassen();

  // zustand: { zeit, dt, scrollY, geschwindigkeit, maus }
  function bild(zustand) {
    renderer.setRenderTarget(ziel);
    renderer.clear(true, true, false);
    for (const schicht of schichten) {
      if (!schicht.sichtbar(zustand)) continue;
      schicht.zeichne(renderer, zustand);
    }
    renderer.setRenderTarget(null);
    renderer.clear(true, false, false);
    nachMaterial.uniforms.uZeit.value = zustand.zeit;
    // Der Farbsaum zieht mit der Scrollgeschwindigkeit an: ruhig fast unsichtbar, im Zug deutlich.
    const tempo = Math.min(1, Math.abs(zustand.geschwindigkeit) * 0.6);
    nachMaterial.uniforms.uAberration.value = 0.008 + tempo * 0.03;
    renderer.render(nachSzene, nachKamera);
  }

  return {
    renderer,
    groesse,
    schichtHinzufuegen(schicht) {
      schichten.push(schicht);
      schicht.anpassen && schicht.anpassen(groesse);
    },
    bild
  };
}
