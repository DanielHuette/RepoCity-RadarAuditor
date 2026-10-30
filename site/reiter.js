// Tab bar of the project page: four views (Projekt, Organigramm, Benchmarks, Präsentation) of one
// document. The hash names the open tab, so a link like /#benchmarks opens that tab directly and
// back/forward walk through the tabs. Loaded in <head> without defer: the first line marks the
// requested tab on <html> before the body is painted, the stylesheet then hides the other panels.
// Without JavaScript nothing is hidden and the tab links jump to the stacked sections.
(function () {
  "use strict";

  var html = document.documentElement;
  var bekannte = ["projekt", "organigramm", "benchmarks", "praesentation"];

  function reiterAusHash() {
    var name = decodeURIComponent(location.hash.slice(1));
    if (bekannte.indexOf(name) >= 0) return { id: name, ziel: null };
    var el = name ? document.getElementById(name) : null;
    var tafel = el && el.closest ? el.closest("[data-reiter-tafel]") : null;
    if (tafel) return { id: tafel.id, ziel: el };
    return { id: "projekt", ziel: null };
  }

  // Before the body exists, only the hash can decide; anchors inside panels are resolved later.
  var frueh = decodeURIComponent(location.hash.slice(1));
  html.dataset.reiter = bekannte.indexOf(frueh) >= 0 ? frueh : "projekt";

  function verdrahten() {
    var reiter = Array.prototype.slice.call(document.querySelectorAll('[role="tab"][data-reiter]'));
    if (reiter.length === 0) return;
    var reduziert = window.matchMedia("(prefers-reduced-motion: reduce)");

    function zeigen(id, optionen) {
      var aktuell = html.dataset.reiter;
      html.dataset.reiter = id;
      reiter.forEach(function (r) {
        var an = r.dataset.reiter === id;
        r.setAttribute("aria-selected", an ? "true" : "false");
        r.tabIndex = an ? 0 : -1;
      });
      if (optionen.oben && aktuell !== id) nachOben();
      document.dispatchEvent(new CustomEvent("reiter:wechsel", { detail: { id: id, vorher: aktuell } }));
    }

    // Smooth scrolling belongs to the 3D layer when it runs (Lenis); it cancels this event and scrolls itself.
    function nachOben() {
      var ev = new CustomEvent("seite:ziel", { cancelable: true, detail: { ziel: null } });
      if (document.dispatchEvent(ev)) window.scrollTo(0, 0);
    }

    function hinScrollen(el, sofort) {
      var ev = new CustomEvent("seite:ziel", { cancelable: true, detail: { ziel: el, sofort: sofort } });
      if (document.dispatchEvent(ev)) {
        el.scrollIntoView({ behavior: sofort || reduziert.matches ? "auto" : "smooth", block: "start" });
      }
    }

    // A tab link opens its tab at the top; only anchors inside a panel scroll to their target.
    function ausAdresse(oben) {
      var stand = reiterAusHash();
      zeigen(stand.id, { oben: oben && !stand.ziel });
      if (!oben && !stand.ziel && location.hash) requestAnimationFrame(nachOben);
      if (stand.ziel) {
        // On page load jump straight to the target: a smooth scroll would be cut off when the 3D layer starts.
        requestAnimationFrame(function () { hinScrollen(stand.ziel, !oben); });
      }
    }

    reiter.forEach(function (r, i) {
      r.addEventListener("click", function (e) {
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        var id = r.dataset.reiter;
        if (html.dataset.reiter !== id || location.hash !== "#" + id) {
          history.pushState(null, "", "#" + id);
        }
        zeigen(id, { oben: true });
      });
      r.addEventListener("keydown", function (e) {
        var naechster = null;
        if (e.key === "ArrowRight" || e.key === "ArrowDown") naechster = reiter[(i + 1) % reiter.length];
        if (e.key === "ArrowLeft" || e.key === "ArrowUp") naechster = reiter[(i - 1 + reiter.length) % reiter.length];
        if (e.key === "Home") naechster = reiter[0];
        if (e.key === "End") naechster = reiter[reiter.length - 1];
        if (!naechster) return;
        e.preventDefault();
        naechster.focus();
        naechster.click();
      });
    });

    // In-page links such as "So läuft ein Audit" (#ablauf): open the panel that holds the target, then scroll.
    document.addEventListener("click", function (e) {
      var a = e.target.closest ? e.target.closest('a[href^="#"]') : null;
      if (!a || a.getAttribute("role") === "tab" || e.ctrlKey || e.metaKey || e.shiftKey) return;
      var name = decodeURIComponent(a.getAttribute("href").slice(1));
      var el = name ? document.getElementById(name) : null;
      var tafel = el && el.closest("[data-reiter-tafel]");
      if (!tafel) return;
      e.preventDefault();
      history.pushState(null, "", "#" + name);
      zeigen(tafel.id, { oben: false });
      requestAnimationFrame(function () { hinScrollen(el); });
    });

    window.addEventListener("popstate", function () { ausAdresse(true); });
    // The browser scrolls to the element named by the hash once the page has loaded; for a tab
    // (whose panel is that element) the tab should open at the top instead.
    window.addEventListener("load", function () {
      if (bekannte.indexOf(decodeURIComponent(location.hash.slice(1))) >= 0 && window.scrollY > 0) nachOben();
    });
    ausAdresse(false);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", verdrahten);
  } else {
    verdrahten();
  }
})();
