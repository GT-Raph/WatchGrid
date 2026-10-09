// Runs in <head> before first paint so a stored light/dark choice never flashes.
try {
  const theme = localStorage.getItem("watchgrid-theme");
  if (theme === "dark" || theme === "light") document.documentElement.dataset.theme = theme;
} catch {
  /* Storage may be unavailable; the system theme applies. */
}
