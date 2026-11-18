// Applies the saved theme before first paint (kept external for a strict CSP).
try {
  var t = localStorage.getItem('gbp-theme');
  if (t === 'dark' || (!t && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
    document.documentElement.classList.add('dark');
  }
} catch (e) {
  /* storage unavailable */
}
