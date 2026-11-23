/* bkk-nav.js: shared navigation bar + light/dark switch for the Bangkok weather pages.
 * <script src="https://thallendan.github.io/BKK-Rain/bkk-nav.js" defer></script>
 * Put <div id="bkk-nav"></div> where the bar should go (add data-inline for a bare row of links).
 * Pages that use bkk.css add data-bkk-themed to <html>; only those get the theme button.
 * Status dots come from status.json; if that fails the links still work. */
(function () {
  var BASE = 'https://thallendan.github.io/BKK-Rain/';
  var CANAL = 'https://thallendan.github.io/BKK-Canal/';
  var PAGES = [
    { id: 'hub',     label: 'Hub',         href: BASE,                           file: ['', 'index.html'] },
    { id: 'macro',   label: 'Macro',       href: BASE + 'macro.html',            file: ['macro.html'] },
    { id: 'monsoon', label: 'Monsoon',     href: BASE + 'BKKmonsoon.html',       file: ['BKKmonsoon.html'] },
    { id: 'gauges',  label: 'Rain gauges', href: BASE + 'BKKraingaugegrid.html', file: ['BKKraingaugegrid.html'] },
    { id: 'hydro',   label: 'HydroView',   href: CANAL + 'HydroView.html',       file: ['HydroView.html'] },
    { id: 'klongs',  label: 'Klongs',      href: CANAL + 'localKlongsV.html',    file: ['localKlongsV.html'] }
  ];
  var COLORS = { normal: '#2fbf8a', watch: '#e8b400', warning: '#f0852f', critical: '#e5484d', unknown: '#8794a6' };
  var KEY = 'bkk-theme', MODES = ['auto', 'light', 'dark'];
  var ICON = { auto: '\u25D0', light: '\u2600', dark: '\u263E' }, LABEL = { auto: 'Auto', light: 'Light', dark: 'Dark' };
  var root = document.documentElement;

  /* ---------- theme ---------- */
  function getMode() { try { var m = localStorage.getItem(KEY); return m === 'light' || m === 'dark' ? m : 'auto'; } catch (e) { return 'auto'; } }
  function resolved(mode) { return mode !== 'auto' ? mode : (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'); }
  function applyMode(mode, announce) {
    if (mode === 'auto') root.removeAttribute('data-theme'); else root.setAttribute('data-theme', mode);
    if (announce) window.dispatchEvent(new CustomEvent('bkk-theme', { detail: { mode: mode, resolved: resolved(mode) } }));
  }
  function saveMode(mode) { try { if (mode === 'auto') localStorage.removeItem(KEY); else localStorage.setItem(KEY, mode); } catch (e) {} }
  var themed = root.hasAttribute('data-bkk-themed');
  if (themed) applyMode(getMode(), false);

  /* ---------- helpers ---------- */
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
  function bodyIsDark() {
    var c = getComputedStyle(document.body).backgroundColor.match(/\d+(\.\d+)?/g);
    if (!c || (c.length > 3 && +c[3] === 0)) return true;
    return (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) < 140;
  }
  function css() {
    if (document.getElementById('bkk-nav-css')) return;
    var s = document.createElement('style'); s.id = 'bkk-nav-css';
    s.textContent =
      '.bkk-nav{display:flex;flex-wrap:wrap;align-items:center;gap:2px 4px;font:12px/1.2 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding:6px 10px;position:relative;z-index:900;' +
      '--n-bg:#0b1120;--n-bd:#1e293b;--n-tx:#94a3b8;--n-hv:#1e293b;--n-hvt:#f1f5f9;--n-cur:#1e3a5f;--n-curt:#fff;background:var(--n-bg);border-bottom:1px solid var(--n-bd);color:var(--n-tx)}' +
      '.bkk-nav.light{--n-bg:#ffffff;--n-bd:#dde3ea;--n-tx:#4b5666;--n-hv:#eef2f7;--n-hvt:#1b2430;--n-cur:#dbe9fb;--n-curt:#0f4c91}' +
      '.bkk-nav.themed{--n-bg:var(--card);--n-bd:var(--bd);--n-tx:var(--mu);--n-hv:var(--card2);--n-hvt:var(--tx);--n-cur:var(--accent);--n-curt:var(--on-accent);position:sticky;top:0}' +
      '.bkk-nav[data-inline]{background:none;border:0;padding:0;display:inline-flex;position:static}' +
      '.bkk-nav a,.bkk-nav button{display:inline-flex;align-items:center;gap:6px;color:var(--n-tx);text-decoration:none;padding:5px 9px;border-radius:999px;white-space:nowrap;font:inherit;background:none;border:0;cursor:pointer}' +
      '.bkk-nav a:hover,.bkk-nav button:hover{background:var(--n-hv);color:var(--n-hvt)}' +
      '.bkk-nav a[aria-current=page]{background:var(--n-cur);color:var(--n-curt);font-weight:600}' +
      '.bkk-nav a:focus-visible,.bkk-nav button:focus-visible{outline:2px solid #4c9bf0;outline-offset:1px}' +
      '.bkk-nav i{width:8px;height:8px;border-radius:50%;display:inline-block;background:#8794a6;flex:none}' +
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

  /* ---------- build ---------- */
  function init() {
    css();
    var host = document.getElementById('bkk-nav');
    var bar = document.createElement('nav');
    bar.className = 'bkk-nav' + (themed ? ' themed' : (bodyIsDark() ? '' : ' light'));
    bar.setAttribute('aria-label', 'Bangkok weather sites');
    if (host && host.hasAttribute('data-inline')) bar.setAttribute('data-inline', '');
    var cur = currentId();
    bar.innerHTML = PAGES.map(function (p) {
      var dot = p.id === 'hub' ? '' : '<i data-dot="' + p.id + '"></i>';
      return '<a href="' + p.href + '"' + (p.id === cur ? ' aria-current="page"' : '') + '>' + dot + p.label + '</a>';
    }).join('');

    if (themed) {
      var sp = document.createElement('span'); sp.className = 'bkk-sp'; bar.appendChild(sp);
      var btn = document.createElement('button'); btn.type = 'button';
      var show = function () {
        var m = getMode();
        btn.textContent = ICON[m] + ' ' + LABEL[m];
        btn.title = 'Colour theme: ' + LABEL[m] + '. Click to change.';
        btn.setAttribute('aria-label', 'Colour theme: ' + LABEL[m] + '. Click to change.');
      };
      btn.addEventListener('click', function () {
        var next = MODES[(MODES.indexOf(getMode()) + 1) % MODES.length];
        saveMode(next); applyMode(next, true); show();
      });
      if (window.matchMedia) {
        var mq = matchMedia('(prefers-color-scheme: dark)');
        var onSys = function () { if (getMode() === 'auto') window.dispatchEvent(new CustomEvent('bkk-theme', { detail: { mode: 'auto', resolved: resolved('auto') } })); };
        if (mq.addEventListener) mq.addEventListener('change', onSys); else if (mq.addListener) mq.addListener(onSys);
      }
      show(); bar.appendChild(btn);
    }

    if (host) { host.innerHTML = ''; host.appendChild(bar); }
    else document.body.insertBefore(bar, document.body.firstChild);

    fetch(BASE + 'status.json?t=' + Date.now(), { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (j) { paint(bar, j); })
      .catch(function () { /* links still work without dots */ });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
