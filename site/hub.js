/* ==========================================================
   ACowork Agent Hub — client-side browse
   ==========================================================
   No backend, no build step, no npm dependency. The whole
   registry ships as a generated static JSON (data/index.json)
   and every interaction happens here.

   Every manifest-derived string is inserted via textContent — a
   community pull request must not be able to inject markup through
   a description field.

   The page also embeds a snapshot of the index (see the
   agent-hub:data markers in index.html) so the page still renders
   when opened straight off disk, where fetch() is blocked.
   ========================================================== */

(function () {
  'use strict';

  var DATA_URL = 'data/index.json';

  var doc = null;
  var state = { q: '', cat: 'all', label: 'all', sig: 'all', sort: 'name' };

  var $ = function (id) { return document.getElementById(id); };

  var grid = $('ah-grid');
  var skeleton = $('ah-skeleton');
  var emptyBox = $('ah-empty');
  var errorBox = $('ah-error');
  var statusEl = $('ah-status');
  var qInput = $('ah-q');
  var clearBtn = $('ah-clear');
  var sortSel = $('ah-sort');
  var sigSel = $('ah-sig');
  var resetBtn = $('ah-reset');
  var catsEl = $('ah-cats');
  var labelsEl = $('ah-labels');
  var countEl = $('ah-count-total');

  /* ---------- tiny DOM helpers ---------- */

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  function add(parent, tag, cls, text) {
    var n = el(tag, cls, text);
    parent.appendChild(n);
    return n;
  }

  function formatBytes(n) {
    if (!n && n !== 0) return '';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + ' KB';
    return (n / 1048576).toFixed(1) + ' MB';
  }

  function prettyKey(s) {
    return String(s).replace(/[_-]+/g, ' ').replace(/\b\w/g, function (c) {
      return c.toUpperCase();
    });
  }

  /* ---------- search ---------- */

  function haystack(a) {
    var parts = [
      a.agent_id, a.name, a.display_name, a.role, a.description, a.author,
      a.category_label
    ];
    if (a.publisher) parts.push(a.publisher.display_name, a.publisher.id);
    (a.labels || []).forEach(function (l) { parts.push(l, prettyKey(l)); });
    (a.capabilities || []).forEach(function (c) {
      parts.push(c.key, prettyKey(c.key), c.description);
    });
    (a.skills || []).forEach(function (s) { parts.push(s, prettyKey(s)); });
    (a.permissions || []).forEach(function (p) { parts.push(p); });
    return parts.join(' \u0001 ').toLowerCase();
  }

  function matches(a, tokens) {
    if (!tokens.length) return true;
    var hay = a._hay || (a._hay = haystack(a));
    return tokens.every(function (t) { return hay.indexOf(t) !== -1; });
  }

  function sigStatus(a) {
    return (a.signature && a.signature.status) || 'unsigned';
  }

  function filtered() {
    var tokens = state.q.toLowerCase().split(/\s+/).filter(Boolean);
    var list = (doc.agents || []).filter(function (a) {
      if (state.cat !== 'all' && a.category !== state.cat) return false;
      if (state.label !== 'all' && (a.labels || []).indexOf(state.label) === -1) return false;
      if (state.sig !== 'all' && sigStatus(a) !== state.sig) return false;
      return matches(a, tokens);
    });

    var byName = function (x, y) {
      return (x.display_name || x.name || '').toLowerCase()
        .localeCompare((y.display_name || y.name || '').toLowerCase());
    };

    if (state.sort === 'category') {
      list.sort(function (x, y) {
        return (x.category_label || '').localeCompare(y.category_label || '') || byName(x, y);
      });
    } else if (state.sort === 'size') {
      list.sort(function (x, y) {
        return ((x.package && x.package.size_bytes) || 0) - ((y.package && y.package.size_bytes) || 0);
      });
    } else {
      list.sort(byName);
    }
    return list;
  }

  /* ---------- rendering ---------- */

  function avatarInto(host, a) {
    if (a.avatar) {
      var img = el('img', 'ah-card__avatar');
      img.src = a.avatar;
      img.alt = '';
      img.width = 44;
      img.height = 44;
      img.setAttribute('loading', 'lazy');
      img.setAttribute('decoding', 'async');
      img.onerror = function () {
        img.remove();
        add(host, 'div', 'ah-card__avatar ah-card__avatar--initials', (a.display_name || a.name || '?').charAt(0));
      };
      host.appendChild(img);
    } else {
      add(host, 'div', 'ah-card__avatar ah-card__avatar--initials', (a.display_name || a.name || '?').charAt(0));
    }
  }

  function signatureBadge(a) {
    var status = sigStatus(a);
    if (status === 'official-channel') {
      return el('span', 'ah-chip ah-chip--ok', 'official channel');
    }
    if (status === 'third-party') {
      return el('span', 'ah-chip', 'third-party');
    }
    return el('span', 'ah-chip ah-chip--warn', 'unsigned');
  }

  function renderCard(a) {
    var card = el('article', 'ah-card');

    var head = add(card, 'div', 'ah-card__head');
    avatarInto(head, a);

    var title = add(head, 'div', 'ah-card__title');
    add(title, 'h3', 'ah-card__name', a.display_name || a.name);
    add(title, 'p', 'ah-card__role', (a.role || '') + ' · ' + (a.category_label || ''));

    if (a.deprecated) {
      var dep = add(card, 'div', 'ah-card__deprecated');
      add(dep, 'strong', null, 'Deprecated: ');
      add(dep, 'span', null, a.deprecated.reason || 'no longer recommended');
    }

    add(card, 'p', 'ah-card__desc', a.description || '');

    var meta = add(card, 'div', 'ah-card__meta');
    var pub = (a.publisher && a.publisher.display_name) || a.author || 'unknown';
    add(meta, 'span', 'ah-chip ah-chip--pub', 'by ' + pub);
    var sig = signatureBadge(a);
    meta.appendChild(sig);
    if (a.license) add(meta, 'span', 'ah-chip', a.license);

    var perms = a.permissions || [];
    if (perms.length) {
      var pl = add(card, 'div', 'ah-card__perms');
      add(pl, 'span', 'ah-card__perms-label', 'Permissions');
      perms.forEach(function (p) {
        var cls = 'ah-chip ah-chip--perm';
        if ((a.high_risk_permissions || []).indexOf(p) !== -1) cls += ' ah-chip--risk';
        add(pl, 'span', cls, p);
      });
    }

    var caps = a.capabilities || [];
    if (caps.length) {
      var cl = add(card, 'div', 'ah-card__caps');
      add(cl, 'span', 'ah-card__perms-label', 'Capabilities');
      var list = add(cl, 'ul');
      caps.slice(0, 5).forEach(function (c) {
        add(list, 'li', null, prettyKey(c.key));
      });
      if (caps.length > 5) add(list, 'li', 'ah-card__more', '+' + (caps.length - 5) + ' more');
    }

    var foot = add(card, 'div', 'ah-card__foot');
    var pkg = a.package || {};
    add(foot, 'span', 'ah-card__size', formatBytes(pkg.size_bytes));
    if (pkg.sha256) {
      var sha = add(foot, 'span', 'ah-card__sha', pkg.sha256.slice(0, 12));
      sha.title = 'sha256 ' + pkg.sha256;
    }
    var dl = add(foot, 'a', 'btn btn--ghost btn--sm', 'Download');
    dl.href = pkg.file || '#';
    dl.setAttribute('download', '');

    return card;
  }

  /* ---------- facets ---------- */

  // The index already carries the facet counts (facets.category with
  // labels, facets.label with the author's own tags). The page consumes
  // them rather than recounting — one source of truth, and the counts in
  // the UI are exactly the counts in the published index.
  function buildFacets() {
    var agents = doc.agents || [];
    var facets = doc.facets || {};
    var cats = facets.category || [];
    var labels = facets.label || [];

    catsEl.innerHTML = '';
    var allBtn = el('button', 'ah-chip' + (state.cat === 'all' ? ' is-active' : ''),
      'All (' + agents.length + ')');
    allBtn.type = 'button';
    allBtn.addEventListener('click', function () { state.cat = 'all'; render(); });
    catsEl.appendChild(allBtn);

    cats.forEach(function (c) {
      var key = c.key;
      var label = c.label || prettyKey(key);
      var n = c.count || 0;
      var btn = el('button', 'ah-chip' + (state.cat === key ? ' is-active' : ''),
        label + ' (' + n + ')');
      btn.type = 'button';
      btn.title = key;
      btn.disabled = n === 0 && state.cat !== key;
      btn.addEventListener('click', function () {
        state.cat = state.cat === key ? 'all' : key;
        render();
      });
      catsEl.appendChild(btn);
    });

    // Labels are the secondary facet — the author's own retrieval tags.
    labelsEl.innerHTML = '';
    var labelAll = el('button', 'ah-chip ah-chip--label' + (state.label === 'all' ? ' is-active' : ''),
      'Any label');
    labelAll.type = 'button';
    labelAll.addEventListener('click', function () { state.label = 'all'; render(); });
    labelsEl.appendChild(labelAll);

    labels.forEach(function (l) {
      var btn = el('button', 'ah-chip ah-chip--label' + (state.label === l.key ? ' is-active' : ''),
        l.key + ' (' + (l.count || 0) + ')');
      btn.type = 'button';
      btn.addEventListener('click', function () {
        state.label = state.label === l.key ? 'all' : l.key;
        render();
      });
      labelsEl.appendChild(btn);
    });
  }

  /* ---------- render ---------- */

  function render() {
    if (!doc) return;

    var list = filtered();

    grid.innerHTML = '';
    list.forEach(function (a) {
      grid.appendChild(renderCard(a));
    });

    skeleton.hidden = true;
    emptyBox.hidden = list.length !== 0;
    grid.hidden = list.length === 0;

    var total = (doc.agents || []).length;
    if (countEl) countEl.textContent = String(total);

    var bits = [];
    bits.push(list.length === total
      ? total + ' agent' + (total === 1 ? '' : 's')
      : list.length + ' of ' + total + ' agents');
    if (state.cat !== 'all') bits.push('category: ' + state.cat);
    if (state.label !== 'all') bits.push('label: ' + state.label);
    if (state.sig !== 'all') bits.push('channel: ' + state.sig);
    statusEl.textContent = bits.join(' · ');

    clearBtn.hidden = !state.q;
  }

  /* ---------- data sources ---------- */

  function fromSnapshot() {
    var node = document.getElementById('ah-data');
    if (!node) return null;
    try {
      return JSON.parse(node.textContent);
    } catch (e) {
      return null;
    }
  }

  function showError(msg) {
    skeleton.hidden = true;
    grid.hidden = true;
    emptyBox.hidden = true;
    errorBox.hidden = false;
    $('ah-error-msg').textContent = msg;
  }

  function start(data) {
    doc = data;
    if (!doc || !doc.agents) {
      showError('The registry index is malformed.');
      return;
    }
    buildFacets();
    render();
  }

  function load() {
    fetch(DATA_URL, { cache: 'no-cache' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(start)
      .catch(function (err) {
        // file:// blocks fetch. The embedded snapshot is the fallback there.
        var snap = fromSnapshot();
        if (snap) { start(snap); return; }
        showError('Could not load ' + DATA_URL + ' — ' + err.message);
      });
  }

  /* ---------- events ---------- */

  qInput.addEventListener('input', function () {
    state.q = qInput.value.trim();
    render();
  });
  clearBtn.addEventListener('click', function () {
    qInput.value = '';
    state.q = '';
    render();
    qInput.focus();
  });
  sortSel.addEventListener('change', function () { state.sort = sortSel.value; render(); });
  sigSel.addEventListener('change', function () { state.sig = sigSel.value; render(); });
  resetBtn.addEventListener('click', function () {
    state.q = ''; state.cat = 'all'; state.label = 'all'; state.sig = 'all'; state.sort = 'name';
    qInput.value = '';
    sortSel.value = 'name';
    sigSel.value = 'all';
    render();
  });

  load();
})();
