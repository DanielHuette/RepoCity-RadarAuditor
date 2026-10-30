// Einstieg. Entscheidet, ob die Seite die 3D-Schicht bekommt, und verdrahtet dann
// weiches Scrollen, Bühne, Radar, Bildflächen, Ring und Typografie zu einem Takt.
// Ohne WebGL 2, bei „Bewegung reduzieren“ oder Datensparmodus bleibt alles beim HTML.

const reduziert = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const sparsam = navigator.connection && navigator.connection.saveData;
const probe = document.createElement("canvas").getContext("webgl2");

if (!reduziert && !sparsam && probe) {
  probe.getExtension("WEBGL_lose_context")?.loseContext();
  Promise.all([
    import("./scroll.js"),
    import("./buehne.js"),
    import("./radar.js"),
    import("./bilder.js"),
    import("./ring.js"),
    import("./typo.js"),
    document.fonts ? document.fonts.load("600 17px Archivo") : Promise.resolve()
  ]).then(starten).catch((fehler) => {
    console.warn("3D-Schicht nicht gestartet:", fehler);
    document.documentElement.classList.remove("fx");
  });
}

function starten([scrollModul, buehneModul, radarModul, bilderModul, ringModul, typoModul]) {
  const html = document.documentElement;
  const sprache = html.lang === "en" ? "en" : "de";
  html.classList.add("fx");

  const scroll = scrollModul.scrollSystem();
  const b = buehneModul.buehne();

  // The 3D layer belongs to the tab "Projekt". On the other tabs it rests, smooth scrolling keeps running.
  const projektOffen = () => !html.dataset.reiter || html.dataset.reiter === "projekt";
  document.addEventListener("seite:ziel", (e) => {
    e.preventDefault();
    // A tab switch has just changed the page height; Lenis would clamp to the old one.
    scroll.lenis.resize();
    const ziel = e.detail.ziel;
    if (ziel) scroll.lenis.scrollTo(ziel, { offset: -12, immediate: Boolean(e.detail.sofort) });
    else scroll.lenis.scrollTo(0, { immediate: true, force: true });
  });

  const hero = document.querySelector(".hero");
  if (hero) b.schichtHinzufuegen(radarModul.radar());

  // Touch scrolling runs on the compositor ahead of WebGL, so on phones the image planes trailed the
  // page and slid over headings. The planes only run with a mouse on wide screens.
  const feinZeiger = window.matchMedia("(min-width: 721px) and (hover: hover) and (pointer: fine)").matches;
  const shots = Array.from(document.querySelectorAll("#projekt .output__shot img"));
  if (shots.length && feinZeiger) b.schichtHinzufuegen(bilderModul.bilder(shots, scroll));

  const ringEl = document.querySelector("[data-ring]");
  if (ringEl) {
    ringEl.hidden = false;
    b.schichtHinzufuegen(ringModul.ring(ringEl, sprache, scroll));
  }

  const ueberschriften = Array.from(document.querySelectorAll("#projekt h2"));
  const typo = typoModul.typo(ueberschriften);

  // Mausposition als -1 … 1, träge nachgeführt
  const maus = { x: 0, y: 0 };
  const mausZiel = { x: 0, y: 0 };
  window.addEventListener("pointermove", (e) => {
    if (e.pointerType !== "mouse") return;
    mausZiel.x = (e.clientX / window.innerWidth) * 2 - 1;
    mausZiel.y = (e.clientY / window.innerHeight) * 2 - 1;
  }, { passive: true });

  // The 3D layer grows the screenshot frames and opens the ring, which moves everything below.
  // A link straight to an anchor such as #ablauf is therefore re-aimed once the layout has settled.
  const anker = decodeURIComponent(location.hash.slice(1));
  const ankerZiel = anker && document.getElementById(anker);
  if (ankerZiel && !ankerZiel.matches("[data-reiter-tafel]") && ankerZiel.closest("#projekt")) {
    requestAnimationFrame(() => requestAnimationFrame(() => scroll.lenis.scrollTo(ankerZiel, { offset: -12, immediate: true, force: true })));
  }

  const zustand = { zeit: 0, dt: 0.016, scrollY: 0, geschwindigkeit: 0, maus };
  let zuletzt = performance.now();

  function bild(jetzt) {
    const dt = Math.min(0.05, (jetzt - zuletzt) / 1000);
    zuletzt = jetzt;
    scroll.takt(jetzt);
    if (!projektOffen()) {
      requestAnimationFrame(bild);
      return;
    }
    maus.x += (mausZiel.x - maus.x) * Math.min(1, dt * 4);
    maus.y += (mausZiel.y - maus.y) * Math.min(1, dt * 4);
    zustand.zeit = jetzt / 1000;
    zustand.dt = dt;
    zustand.scrollY = scroll.y;
    zustand.geschwindigkeit = scroll.geschwindigkeit;
    typo.takt(b.groesse.hoehe);
    b.bild(zustand);
    requestAnimationFrame(bild);
  }
  requestAnimationFrame(bild);
}
