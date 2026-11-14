// Apply the saved theme before first paint to avoid a flash. Kept in its own
// file so the page needs no inline script (see the Content-Security-Policy).
// The app (src/lib/theme/store.ts) writes these keys; keep them in step.
(function () {
  var root = document.documentElement;
  try {
    var d = localStorage.getItem('darkMode');
    var dark = d === 'true' || (d === null && window.matchMedia('(prefers-color-scheme: dark)').matches);
    root.classList.toggle('dark', dark);
    if (localStorage.getItem('reduceMotion') === 'true') root.classList.add('reduce-motion');
    // A custom theme: its CSS variables for this appearance (none = Signal, from app.css).
    var saved = JSON.parse(localStorage.getItem('cognitrix:theme-vars:v1') || 'null');
    var vars = saved && saved[dark ? 'dark' : 'light'];
    if (vars && typeof vars === 'object') {
      for (var name in vars) {
        if (/^--[a-z0-9-]+$/.test(name) && typeof vars[name] === 'string') root.style.setProperty(name, vars[name]);
      }
      if (vars['--bg']) {
        var metas = document.querySelectorAll('meta[name="theme-color"]');
        for (var i = 0; i < metas.length; i++) metas[i].setAttribute('content', vars['--bg']);
      }
    }
  } catch (e) { /* storage blocked or bad JSON: the default theme shows */ }
})();
