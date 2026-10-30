// Weiches Scrollen mit Lenis. Lenis legt eine Trägheit über das Mausrad und Touch-Gesten;
// der daraus entstehende Scrollweg pro Bild (das Scroll-Delta) ist der Taktgeber aller 3D-Bewegung.
import Lenis from "../vendor/lenis.mjs";

export function scrollSystem() {
  const lenis = new Lenis({
    lerp: 0.085,          // Anteil des Restwegs, der je Bild aufgeholt wird: kleiner = träger
    wheelMultiplier: 0.95,
    touchMultiplier: 1.4,
    anchors: false         // in-page links go through reiter.js, which opens the right tab first
  });

  // Geglättete Geschwindigkeit in Bildschirmhöhen pro Sekunde, Vorzeichen = Richtung.
  let geschwindigkeit = 0;
  let zuletzt = performance.now();
  let zuletztY = window.scrollY;

  function takt(zeit) {
    lenis.raf(zeit);
    const dt = Math.max(1, zeit - zuletzt) / 1000;
    const y = lenis.scroll;
    const roh = (y - zuletztY) / Math.max(1, window.innerHeight) / dt;
    // Exponentielle Glättung: schnelle Stöße kommen an, Zittern nicht.
    geschwindigkeit += (roh - geschwindigkeit) * Math.min(1, dt * 9);
    zuletzt = zeit;
    zuletztY = y;
  }

  return {
    lenis,
    takt,
    get y() { return lenis.scroll; },
    get geschwindigkeit() { return geschwindigkeit; }
  };
}
