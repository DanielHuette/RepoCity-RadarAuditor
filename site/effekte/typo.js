// Kinetische Typografie: Überschriften stehen zunächst als Kontur und füllen sich von links
// nach rechts, während sie ins Bild scrollen. Die Füllung ist eine Kopie des Texts im
// ::after-Element, beschnitten mit clip-path; der Fortschritt steht in der Variablen --p.
export function typo(elemente) {
  const eintraege = elemente.map((el) => {
    el.dataset.text = el.textContent.trim();
    el.classList.add("kinetik");
    el.style.setProperty("--p", "0");
    return { el, p: 0 };
  });

  function takt(hoehe) {
    const start = hoehe * 0.94;    // Oberkante hier: Füllung beginnt
    const weg = hoehe * 0.42;      // nach so viel Scrollweg ist sie vollständig
    eintraege.forEach((e) => {
      const top = e.el.getBoundingClientRect().top;
      const ziel = Math.min(1, Math.max(0, (start - top) / weg));
      if (Math.abs(ziel - e.p) < 0.002) return;
      e.p = ziel;
      e.el.style.setProperty("--p", ziel.toFixed(3));
    });
  }

  return { takt };
}
