// Apply the saved theme before first paint to avoid a flash. Kept in its own
// file so the page needs no inline script (see the Content-Security-Policy).
(function () {
  var d = localStorage.getItem('darkMode');
  var dark = d === 'true' || (d === null && window.matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.classList.toggle('dark', dark);
  if (localStorage.getItem('reduceMotion') === 'true') document.documentElement.classList.add('reduce-motion');
})();
