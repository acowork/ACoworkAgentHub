/* ==========================================================
   ACowork Agent Hub — client-side browse
   ==========================================================
   No backend, no build step, no npm dependency.

   Loading model (three tiers, each sized by what the user is doing,
   never by how big the registry is):

     data/meta.json          ~1 KiB, fetched uncached; declares the build id
     data/pages/p###.json    48 cards per shard; page 0 is the first paint
     data/details/<id>.json  one full entry, fetched when a card is opened

   The browser never downloads the whole registry to draw the page. It
   downloads the whole *card* set only when the user searches, filters or
   sorts — operations that genuinely need every record — and even then it
   renders a window of the results, not all of them.

   Every manifest-derived string is inserted via textContent: a community
   pull request must not be able to inject markup through a description.
   ========================================================== */

(function () {
  'use strict';

  var META_URL = 'data/meta.json';

  /* How many cards may exist in the DOM at once. Paging controls the
     network; this controls the render cost. Without it, a search matching
     thousands of agents would still freeze the tab. */
  var RENDER_WINDOW = 96;

  var meta = null;
  var pages = [];        // pages[i] = array of cards, or null when not fetched
  var details = {};      // agent_id -> full entry, once opened
  var pending = {};      // url -> in-flight promise, so a shard is fetched once
  var visiblePages = 0;  // pages currently drawn when unfiltered
  var windowSize = RENDER_WINDOW; // result cap when filtered

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
  var moreBtn = $('ah-more');
  var detailEl = $('ah-detail');

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

  /* ---------- data access ---------- */

  /* Pages and details are immutable for a given build id, so they are cached
     hard and versioned by that id. Only meta.json is revalidated — it is tiny,
     and it is what points at the next build. */
  function versioned(url) {
    return url + (url.indexOf('?') === -1 ? '?' : '&') + 'v=' + meta.build;
  }

  function getJson(url, cacheable) {
    if (pending[url]) return pending[url];
    var p = fetch(url, { cache: cacheable ? 'force-cache' : 'no-store' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status + ' for ' + url);
        return r.json();
      })
      .catch(function (err) {
        delete pending[url];
        throw err;
      });
    pending[url] = p;
    return p;
  }

  function loadPage(i) {
    if (pages[i]) return Promise.resolve(pages[i]);
    var spec = meta.pages[i];
    if (!spec) return Promise.resolve([]);
    return getJson(versioned(spec.file), true).then(function (doc) {
      pages[i] = doc.agents || [];
      return pages[i];
    });
  }

  function loadAllPages() {
    var missing = [];
    for (var i = 0; i < meta.page_count; i++) {
      if (!pages[i]) missing.push(i);
    }
    if (!missing.length) return Promise.resolve();
    setStatus('Loading ' + missing.length + ' more shard(s)\u2026');
    return Promise.all(missing.map(loadPage));
  }

  function loadDetail(agentId) {
    if (details[agentId]) return Promise.resolve(details[agentId]);
    return getJson(versioned(meta.detail_prefix + encodeURIComponent(agentId) + '.json'), true)
      .then(function (doc) {
        details[agentId] = doc.agent;
        return doc.agent;
      });
  }

  function loadedCards() {
    var out = [];
    for (var i = 0; i < pages.length; i++) {
      if (pages[i]) out = out.concat(pages[i]);
    }
    return out;
  }

  /* ---------- search ---------- */

  /* The haystack is built from the card only. Capability and skill *names*
     are searchable; their descriptions live in the detail file and are not
     part of registry-wide search — that would put the heavy fields back into
     every page load, which is the whole thing this layout exists to avoid. */
  function haystack(a) {
    var parts = [
      a.agent_id, a.name, a.display_name, a.role, a.description, a.author,
      a.category_label, a.license
    ];
    if (a.publisher) parts.push(a.publisher.display_name, a.publisher.id);
    (a.labels || []).forEach(function (l) { parts.push(l, prettyKey(l)); });
    (a.capability_keys || []).forEach(function (c) { parts.push(c, prettyKey(c)); });
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
    return a.signature_status || (a.signature && a.signature.status) || 'unsigned';
  }

  function filtered(list) {
    var tokens = state.q.toLowerCase().split(/\s+/).filter(Boolean);
    var out = list.filter(function (a) {
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
      out.sort(function (x, y) {
        return (x.category_label || '').localeCompare(y.category_label || '') || byName(x, y);
      });
    } else if (state.sort === 'size') {
      out.sort(function (x, y) {
        return (x.size_bytes || 0) - (y.size_bytes || 0);
      });
    } else {
      out.sort(byName);
    }
    return out;
  }

  /* An unfiltered, name-sorted view is exactly the page order the build
     wrote, so it can be drawn from the loaded shards. Anything else needs
     every card, because a partial sort is not a sort. */
  function viewIsPageOrder() {
    return !state.q && state.cat === 'all' && state.label === 'all' &&
      state.sig === 'all' && state.sort === 'name';
  }

  /* ---------- rendering ---------- */

  function avatarInto(host, a) {
    if (a.avatar) {
      var img = el('img', 'ah-card__avatar');
      img.src = versioned(a.avatar);
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
    if (status === 'official-channel') return el('span', 'ah-chip ah-chip--ok', 'official channel');
    if (status === 'third-party') return el('span', 'ah-chip', 'third-party');
    return el('span', 'ah-chip ah-chip--warn', 'unsigned');
  }

  function renderCard(a) {
    var card = el('article', 'ah-card');
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.setAttribute('aria-haspopup', 'dialog');

    var head = add(card, 'div', 'ah-card__head');
    avatarInto(head, a);

    var title = add(head, 'div', 'ah-card__title');
    add(title, 'h3', 'ah-card__name', a.display_name || a.name);
    add(title, 'p', 'ah-card__role', (a.role || '') + ' \u00b7 ' + (a.category_label || ''));

    if (a.deprecated) {
      var dep = add(card, 'div', 'ah-card__deprecated');
      add(dep, 'strong', null, 'Deprecated: ');
      add(dep, 'span', null, a.deprecated.reason || 'no longer recommended');
    }

    add(card, 'p', 'ah-card__desc', a.description || '');

    var meta2 = add(card, 'div', 'ah-card__meta');
    var pub = (a.publisher && a.publisher.display_name) || a.author || 'unknown';
    add(meta2, 'span', 'ah-chip ah-chip--pub', 'by ' + pub);
    meta2.appendChild(signatureBadge(a));
    if (a.license) add(meta2, 'span', 'ah-chip', a.license);

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

    var caps = a.capability_keys || [];
    if (caps.length) {
      var cl = add(card, 'div', 'ah-card__caps');
      add(cl, 'span', 'ah-card__perms-label', 'Capabilities');
      var list = add(cl, 'ul');
      caps.slice(0, 5).forEach(function (c) { add(list, 'li', null, prettyKey(c)); });
      if (caps.length > 5) add(list, 'li', 'ah-card__more', '+' + (caps.length - 5) + ' more');
    }

    var foot = add(card, 'div', 'ah-card__foot');
    add(foot, 'span', 'ah-card__size', formatBytes(a.size_bytes));
    var dl = add(foot, 'a', 'btn btn--ghost btn--sm', 'Download');
    dl.href = a.file || '#';
    dl.setAttribute('download', '');
    // The download link is inside the card, which is itself a button: a
    // click there must open the file, not the detail panel.
    dl.addEventListener('click', function (ev) { ev.stopPropagation(); });

    card.addEventListener('click', function () { openDetail(a.agent_id); });
    card.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openDetail(a.agent_id); }
    });

    return card;
  }

  function setStatus(text) {
    statusEl.textContent = text;
  }

  function drawCards(cards) {
    grid.innerHTML = '';
    var shown = cards.slice(0, windowSize);
    var frag = document.createDocumentFragment();
    shown.forEach(function (a) { frag.appendChild(renderCard(a)); });
    grid.appendChild(frag);
    grid.hidden = shown.length === 0;
    emptyBox.hidden = shown.length !== 0;
    return shown.length;
  }

  function render() {
    if (!meta) return;
    skeleton.hidden = true;

    if (viewIsPageOrder()) {
      var cards = loadedCards();
      var shown = drawCards(cards);
      var morePages = visiblePages < meta.page_count;
      var bits = [meta.total + ' agent' + (meta.total === 1 ? '' : 's')];
      if (morePages || meta.total > shown) {
        bits.push('showing ' + shown + ' \u2014 more load as you scroll');
      }
      setStatus(bits.join(' \u00b7 '));
      moreBtn.hidden = !morePages;
      clearBtn.hidden = !state.q;
      if (countEl) countEl.textContent = String(meta.total);
      return;
    }

    // Filtered: the whole card set is required. loadAllPages() is cached, so
    // this costs one extra round trip per shard, once, for the whole session.
    loadAllPages().then(function () {
      var list = filtered(loadedCards());
      var shown = drawCards(list);
      var bits = [list.length === meta.total
        ? meta.total + ' agent' + (meta.total === 1 ? '' : 's')
        : list.length + ' of ' + meta.total + ' agents'];
      if (state.cat !== 'all') bits.push('category: ' + state.cat);
      if (state.label !== 'all') bits.push('label: ' + state.label);
      if (state.sig !== 'all') bits.push('channel: ' + state.sig);
      if (shown < list.length) bits.push('showing ' + shown + ' of ' + list.length);
      setStatus(bits.join(' \u00b7 '));
      moreBtn.hidden = shown >= list.length;
      clearBtn.hidden = !state.q;
      if (countEl) countEl.textContent = String(meta.total);
    }).catch(showError);
  }

  /* ---------- detail panel ---------- */

  function openDetail(agentId) {
    detailEl.hidden = false;
    detailEl.innerHTML = '';
    detailEl.setAttribute('aria-busy', 'true');
    add(detailEl, 'p', 'ah-detail__loading', 'Loading\u2026');

    loadDetail(agentId).then(function (a) {
      if (!a) { add(detailEl, 'p', null, 'This agent has no detail record.'); return; }
      detailEl.innerHTML = '';
      detailEl.setAttribute('aria-busy', 'false');

      var close = el('button', 'ah-detail__close', '\u00d7');
      close.type = 'button';
      close.setAttribute('aria-label', 'Close agent details');
      close.addEventListener('click', closeDetail);
      detailEl.appendChild(close);

      var head = add(detailEl, 'div', 'ah-detail__head');
      avatarInto(head, a);
      var ht = add(head, 'div', 'ah-card__title');
      add(ht, 'h3', 'ah-card__name', a.display_name || a.name);
      add(ht, 'p', 'ah-card__role', (a.role || '') + ' \u00b7 ' + (a.category_label || ''));

      var ids = add(detailEl, 'dl', 'ah-detail__ids');
      [['agent id', a.agent_id], ['version', a.version], ['author', a.author],
       ['publisher', (a.publisher && a.publisher.display_name) || ''],
       ['license', a.license], ['runtime', a.runtime_version],
       ['channel', sigStatus(a)]
      ].forEach(function (row) {
        if (!row[1]) return;
        add(ids, 'dt', null, row[0]);
        add(ids, 'dd', null, row[1]);
      });

      add(detailEl, 'p', 'ah-detail__desc', a.description || '');

      var caps = a.capabilities || [];
      if (caps.length) {
        var cs = add(detailEl, 'section', 'ah-detail__section');
        add(cs, 'h4', null, 'Capabilities');
        var ul = add(cs, 'ul', 'ah-detail__caps');
        caps.forEach(function (c) {
          var li = add(ul, 'li');
          add(li, 'strong', null, prettyKey(c.key || ''));
          if (c.description) add(li, 'span', null, ' \u2014 ' + c.description);
        });
      }

      var skills = a.skills || [];
      if (skills.length) {
        var ss = add(detailEl, 'section', 'ah-detail__section');
        add(ss, 'h4', null, 'Skills');
        var sw = add(ss, 'div', 'ah-detail__chips');
        skills.forEach(function (s) { add(sw, 'span', 'ah-chip', s); });
      }

      var perms = a.permissions || [];
      if (perms.length) {
        var ps = add(detailEl, 'section', 'ah-detail__section');
        add(ps, 'h4', null, 'Permissions');
        var pw = add(ps, 'div', 'ah-detail__chips');
        var risky = a.high_risk_permissions || [];
        perms.forEach(function (p) {
          add(pw, 'span', 'ah-chip ah-chip--perm' + (risky.indexOf(p) !== -1 ? ' ah-chip--risk' : ''), p);
        });
      }

      var pkg = a.package || {};
      if (pkg.file) {
        var bs = add(detailEl, 'section', 'ah-detail__section');
        add(bs, 'h4', null, 'Package');
        var bl = add(bs, 'dl', 'ah-detail__ids');
        add(bl, 'dt', null, 'size');
        add(bl, 'dd', null, formatBytes(pkg.size_bytes));
        if (pkg.sha256) {
          add(bl, 'dt', null, 'sha256');
          add(bl, 'dd', 'ah-detail__sha', pkg.sha256);
        }
        if (a.signature && a.signature.key_fingerprint) {
          add(bl, 'dt', null, 'signer');
          add(bl, 'dd', 'ah-detail__sha', a.signature.key_fingerprint);
        }
        var dl = add(bs, 'a', 'btn btn--primary btn--sm', 'Download .agent');
        dl.href = pkg.file;
        dl.setAttribute('download', '');
      }

      if (a.repository) {
        var ln = add(detailEl, 'div', 'ah-detail__links');
        var repo = add(ln, 'a', 'ah-link', 'Source repository');
        repo.href = a.repository;
        repo.target = '_blank';
        repo.rel = 'noopener';
      }
    }).catch(function (err) {
      detailEl.innerHTML = '';
      add(detailEl, 'p', null, 'Could not load this agent: ' + err.message);
    });
  }

  function closeDetail() {
    detailEl.hidden = true;
    detailEl.innerHTML = '';
  }

  /* ---------- facets ---------- */

  // The published index carries the facet counts (facets.category, facets.label).
  // The page consumes them rather than recounting from loaded shards: a count
  // derived from what happens to be loaded would change as the user scrolls.
  function buildFacets() {
    var facets = meta.facets || {};
    var cats = facets.category || [];
    var labels = facets.label || [];

    catsEl.innerHTML = '';
    var allBtn = el('button', 'ah-chip' + (state.cat === 'all' ? ' is-active' : ''),
      'All (' + meta.total + ')');
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

  /* ---------- paging ---------- */

  function showNextPage() {
    if (visiblePages >= meta.page_count) return Promise.resolve();
    var i = visiblePages;
    return loadPage(i).then(function () {
      visiblePages = i + 1;
      render();
    });
  }

  function wireInfiniteScroll() {
    if (!('IntersectionObserver' in window)) {
      moreBtn.hidden = false;
      return;
    }
    var obs = new IntersectionObserver(function (entries) {
      if (entries[0].isIntersecting && viewIsPageOrder() &&
          visiblePages < meta.page_count) {
        showNextPage().catch(showError);
      }
    }, { rootMargin: '400px 0px' });
    obs.observe(moreBtn);
  }

  /* ---------- boot ---------- */

  function showError(err) {
    skeleton.hidden = true;
    grid.hidden = true;
    emptyBox.hidden = true;
    errorBox.hidden = false;
    var msg = err && err.message ? err.message : String(err);
    $('ah-error-msg').textContent = msg;
  }

  function boot() {
    getJson(META_URL, false).then(function (doc) {
      meta = doc;
      if (!meta || !meta.pages || !meta.pages.length) {
        throw new Error('meta.json declares no page shards');
      }
      buildFacets();
      return loadPage(0);
    }).then(function () {
      visiblePages = 1;
      wireInfiniteScroll();
      render();
    }).catch(function (err) {
      showError(err);
      // file:// blocks fetch entirely; there is no embedded fallback any
      // more, by design — a snapshot in the HTML would be a second copy of
      // the registry that goes stale and is downloaded even when ignored.
      if (location.protocol === 'file:') {
        $('ah-error-msg').textContent =
          'This page must be served over HTTP (python -m http.server). ' +
          'file:// cannot load the registry shards.';
      }
    });
  }

  /* ---------- events ---------- */

  qInput.addEventListener('input', function () {
    state.q = qInput.value.trim();
    windowSize = RENDER_WINDOW;
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
    windowSize = RENDER_WINDOW;
    render();
  });
  moreBtn.addEventListener('click', function () {
    if (viewIsPageOrder()) {
      showNextPage().catch(showError);
    } else {
      windowSize += RENDER_WINDOW;
      render();
    }
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && !detailEl.hidden) closeDetail();
  });

  boot();
})();
