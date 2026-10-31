// Präsentation tab: nine scenes that play by themselves like a short promo film.
// Timing lives in CSS: the active chapter's progress bar fills over --szene-dauer and its
// animationend moves the film on; pausing freezes every animation inside the film.
// The film starts when its tab opens and stops when the tab or the browser window is left; once
// the last scene has played out it opens the Projekt tab. With reduced motion nothing plays or
// switches tabs by itself; the chapters still switch scenes.
(function () {
  "use strict";

  var film = document.querySelector("[data-film]");
  if (!film) return;
  var szenen = Array.prototype.slice.call(film.querySelectorAll(".szene"));
  var kapitel = Array.prototype.slice.call(film.querySelectorAll("[data-kapitel]"));
  var knopf = film.querySelector(".film__knopf");
  var knopfText = knopf.querySelector(".film__knopf-text");
  var reduziert = window.matchMedia("(prefers-reduced-motion: reduce)");
  var texte = { abspielen: knopf.dataset.abspielen, anhalten: knopf.dataset.anhalten };
  var aktuell = 0;
  var spielt = false;

  function zeige(index) {
    aktuell = (index + szenen.length) % szenen.length;
    szenen.forEach(function (s, i) {
      var an = i === aktuell;
      s.classList.remove("ist-aktiv");
      s.hidden = !an;
      s.setAttribute("aria-hidden", an ? "false" : "true");
    });
    kapitel.forEach(function (k, i) {
      k.classList.remove("ist-aktiv");
      k.classList.toggle("ist-gesehen", i < aktuell);
      if (i === aktuell) k.setAttribute("aria-current", "step");
      else k.removeAttribute("aria-current");
    });
    // Re-adding the class after a reflow restarts the scene's CSS animations from the beginning.
    void film.offsetWidth;
    szenen[aktuell].classList.add("ist-aktiv");
    kapitel[aktuell].classList.add("ist-aktiv");
  }

  function setzeSpielt(an) {
    spielt = an && !reduziert.matches;
    film.classList.toggle("ist-pausiert", !spielt);
    knopf.setAttribute("aria-pressed", spielt ? "true" : "false");
    knopfText.textContent = spielt ? texte.anhalten : texte.abspielen;
  }

  kapitel.forEach(function (k, i) {
    k.addEventListener("click", function () {
      zeige(i);
      if (!reduziert.matches) setzeSpielt(true);
    });
    var balken = k.querySelector(".kapitel__balken");
    balken.addEventListener("animationend", function () {
      if (!spielt || i !== aktuell) return;
      var letzte = aktuell === szenen.length - 1;
      zeige(aktuell + 1);
      if (letzte) zumProjekt();
    });
  });

  // A film that has played to the end hands over to the project tab through the tab itself, so
  // address, history and tab bar follow; the tab change stops the film on its first scene.
  function zumProjekt() {
    var reiter = document.querySelector('[role="tab"][data-reiter="projekt"]');
    if (reiter) reiter.click();
  }

  knopf.addEventListener("click", function () {
    setzeSpielt(!spielt);
  });

  function offen() {
    return document.documentElement.dataset.reiter === "praesentation";
  }

  document.addEventListener("reiter:wechsel", function (e) {
    if (e.detail.id === "praesentation") {
      zeige(e.detail.vorher === "praesentation" ? aktuell : 0);
      setzeSpielt(true);
    } else {
      setzeSpielt(false);
    }
  });
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) setzeSpielt(false);
  });

  film.classList.add("ist-bereit");
  zeige(0);
  setzeSpielt(offen());
})();
