/* Built-in defaults for the TV. On the Pi, Settings (/admin) override these. */
window.DASHBOARD_CONFIG = {
  mode: "auto",          // "auto" uses the Pi server when it answers; ?mode=demo or ?mode=live to force
  perf: "auto",          // "lite" drops the glass blur for small boards (1 GB or less); ?perf=lite or ?perf=full to force
  timeFormat: "12h",
  units: { temp: "F", wind: "mph", precip: "in" },
  refresh: { weather: 120, calendar: 300, drive: 300 },   // seconds
  nightDim: { enabled: true, start: "22:30", end: "06:00", level: 0.6 },
  burnInShift: true,
  background: "sky",     // "sky" or "photos" (photos come from the Pi; set in Settings)
  photoMinutes: 10,
  dailyReload: "03:30",
};
