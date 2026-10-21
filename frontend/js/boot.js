/* Runs synchronously in <head>: apply saved theme and sidebar state before first paint. */
(function () {
  var root = document.documentElement;
  try {
    var t = localStorage.getItem("vigil.theme");
    if (t === "light" || t === "dark") root.setAttribute("data-theme", t);
    if (localStorage.getItem("vigil.sidebar") === "collapsed") root.setAttribute("data-sidebar", "collapsed");
  } catch (e) { /* storage blocked: fall back to system preference */ }
})();
