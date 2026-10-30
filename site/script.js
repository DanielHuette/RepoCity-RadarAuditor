// Runs the audit flow animation only while the diagram is on screen and never when reduced motion is requested.
(function () {
  "use strict";

  var diagrams = Array.prototype.slice.call(document.querySelectorAll("svg.flow"));
  if (diagrams.length === 0) {
    return;
  }

  var reduced = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  var visible = new Map();

  function apply(svg) {
    if (typeof svg.pauseAnimations !== "function") {
      return;
    }
    var still = (reduced && reduced.matches) || visible.get(svg) === false;
    if (still) {
      svg.pauseAnimations();
    } else {
      svg.unpauseAnimations();
    }
  }

  function applyAll() {
    diagrams.forEach(apply);
  }

  if ("IntersectionObserver" in window) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        visible.set(entry.target, entry.isIntersecting);
        apply(entry.target);
      });
    });
    diagrams.forEach(function (svg) {
      observer.observe(svg);
    });
  }

  if (reduced) {
    if (typeof reduced.addEventListener === "function") {
      reduced.addEventListener("change", applyAll);
    } else if (typeof reduced.addListener === "function") {
      reduced.addListener(applyAll);
    }
  }

  applyAll();
})();
