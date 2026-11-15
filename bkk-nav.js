/* bkk-nav.js: shared navigation bar for the five Bangkok weather pages + hub.
 * Load with:  <script src="https://thallendan.github.io/BKK-Rain/bkk-nav.js" defer></script>
 * Put <div id="bkk-nav"></div> where the bar should go (add data-inline for a bare row of links).
 * Without the div, the bar is inserted at the top of <body>.
 * Status dots come from status.json; if that fails the links still work. */
(function () {
  var BASE = 'https://thallendan.github.io/BKK-Rain/';
  var CANAL = 'https://thallendan.github.io/BKK-Canal/';
  var PAGES = [
    { id: 'hub',     label: 'Hub',         href: BASE,                           file: ['', 'index.html'], inRain: true },
    { id: 'macro',   label: 'Macro',       href: BASE + 'macro.html',            file: ['macro.html'] },
    { id: 'monsoon', label: 'Monsoon',     href: BASE + 'BKKmonsoon.html',       file: ['BKKmonsoon.html'] },
    { id: 'gauges',  label: 'Rain gauges', href: BASE + 'BKKraingaugegrid.html', file: ['BKKraingaugegrid.html'] },
    { id: 'hydro',   label: 'HydroView',   href: CANAL + 'HydroView.html',       file: ['HydroView.html'] },
    { id: 'klongs',  label: 'Klongs',      href: CANAL + 'localKlongsV.html',    file: ['localKlongsV.html'] }
  ];
  var COLORS = { normal: '#34d399', watch: '#facc15', warning: '#fb923c', critical: '#ef4444', unknown: '#64748b' };

  function currentId() {
    var path = location.pathname, file = path.split('/').pop();
    for (var i = 0; i < PAGES.length; i++) {
      var p = PAGES[i];
      if (p.file.indexOf(file) !== -1) {
        if (p.id === 'hub') return /\/BKK-Rain\/(index\.html)?$/.test(path) || path === '/' ? 'hub' : null;
        return p.id;
      }
    }
    return null;
  }

  function css() {
    if (document.getElementById('bkk-nav-css')) return;
    var s = document.createElement('style');
    s.id = 'bkk-nav-css';
    s.textContent =
      '.bkk-nav{display:flex;flex-wrap:wrap;align-items:center;gap:2px 4px;font:12px/1.2 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;' +
      'background:#0b1120;color:#cbd5e1;padding:6px 10px;border-bottom:1px solid #1e293b;position:relative;z-index:60}' +
      '.bkk-nav[data-inline]{background:none;border:0;padding:0;display:inline-flex}' +
      '.bkk-nav a{display:inline-flex;align-items:center;gap:6px;color:#94a3b8;text-decoration:none;padding:4px 9px;border-radius:999px;white-space:nowrap}' +
      '.bkk-nav a:hover{background:#1e293b;color:#f1f5f9}' +
      '.bkk-nav a[aria-current=page]{background:#1e3a5f;color:#fff;font-weight:600}' +
      '.bkk-nav i{width:8px;height:8px;border-radius:50%;display:inline-block;background:#64748b;flex:none}' +
      '.bkk-nav .bkk-sp{flex:1}';
    document.head.appendChild(s);
  }

  function ageMin(iso) { var t = Date.parse(iso); return isNaN(t) ? null : (Date.now() - t) / 60000; }

  function paint(bar, status) {
    PAGES.forEach(function (p) {
      var dot = bar.querySelector('[data-dot="' + p.id + '"]');
      if (!dot || !status || !status.sites || !status.sites[p.id]) return;
      var s = status.sites[p.id], a = ageMin(s.updated_utc);
      var stale = !s.ok || a === null || a > (s.max_age_min || 60);
      var lv = stale ? 'unknown' : (s.level || 'unknown');
      dot.style.background = COLORS[lv] || COLORS.unknown;
      dot.parentNode.title = p.label + ': ' + (stale ? 'no current data. ' : '') + (s.headline || '');
    });
  }

  function init() {
    css();
    var host = document.getElementById('bkk-nav');
    var bar = document.createElement('nav');
    bar.className = 'bkk-nav';
    bar.setAttribute('aria-label', 'Bangkok weather sites');
    if (host && host.hasAttribute('data-inline')) bar.setAttribute('data-inline', '');
    var cur = currentId();
    bar.innerHTML = PAGES.map(function (p) {
      var dot = p.id === 'hub' ? '' : '<i data-dot="' + p.id + '"></i>';
      return '<a href="' + p.href + '"' + (p.id === cur ? ' aria-current="page"' : '') + '>' + dot + p.label + '</a>';
    }).join('');
    if (host) { host.innerHTML = ''; host.appendChild(bar); }
    else document.body.insertBefore(bar, document.body.firstChild);

    fetch(BASE + 'status.json?t=' + Date.now(), { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (j) { paint(bar, j); })
      .catch(function () { /* links still work without dots */ });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
