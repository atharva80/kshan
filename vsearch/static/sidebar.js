// Loaded into Immich's own web pages (see web.py: immich_web; named sidebar.js because blockers tend to filter names like inject.js/upload.js). Adds "Find in videos" and
// "Pendrives" to Immich's sidebar, right under Explore, styled exactly like the items around
// them (they're clones). If Immich's markup ever changes so the sidebar can't be found,
// this does nothing at all.
(() => {
  const ITEMS = [
    {
      key: 'videos', href: '/vsearch/', label: 'Find in videos',
      // film strip + magnifier
      icon: 'M4 4h12a2 2 0 0 1 2 2v3.1a6.5 6.5 0 0 0-2-.6V8h-2V6H6v2H4V6m0 4h2v2H4v-2m0 4h2v2H4v-2m0 4h2v2H4.4A2 2 0 0 1 4 18m12.5-7.5a4.5 4.5 0 0 1 3.75 7l2.7 2.7-1.4 1.4-2.7-2.7a4.5 4.5 0 1 1-2.35-8.4m0 2a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5M8 18h3.1a6.5 6.5 0 0 0 .9 2H8v-2Z',
    },
    {
      key: 'pendrives', href: '/vsearch/#pendrives', label: 'Pendrives',
      // USB pendrive
      icon: 'M9 2h6v5h1a1 1 0 0 1 1 1v11a3 3 0 0 1-3 3h-4a3 3 0 0 1-3-3V8a1 1 0 0 1 1-1h1V2m2 2v3h2V4h-2m-2 5v10a1 1 0 0 0 1 1h4a1 1 0 0 0 1-1V9H9Z',
    },
  ];

  const isCurrent = (a) => a.getAttribute('aria-current') || a.matches('[class*="active" i]');

  function template() {
    // Immich's sidebar links: /photos, /explore, /map, /sharing ... Clone a plain one.
    const links = [...document.querySelectorAll('a[href]')].filter((a) => /^\/(explore|map|sharing)$/.test(a.getAttribute('href')));
    const explore = links.find((a) => a.getAttribute('href') === '/explore');
    const source = links.find((a) => !isCurrent(a)) || explore;
    return explore && source ? { explore, source } : null;
  }

  function relabel(node, text) {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    let done = false;
    for (let t = walker.nextNode(); t; t = walker.nextNode()) {
      if (t.nodeValue.trim()) { t.nodeValue = done ? '' : text; done = true; }
    }
    node.setAttribute('title', text);
    node.setAttribute('aria-label', text);
  }

  function add() {
    if (document.querySelector('[data-vsearch-link]')) return;
    const found = template();
    if (!found) return;
    // Insert after Explore's own wrapper (a list item or the link itself).
    const wrap = (a) => (a.parentElement && a.parentElement.children.length === 1 ? a.parentElement : a);
    let after = wrap(found.explore);
    for (const item of ITEMS) {
      const clone = wrap(found.source).cloneNode(true);
      const link = clone.matches('a') ? clone : clone.querySelector('a');
      link.href = item.href;
      link.removeAttribute('aria-current');
      link.dataset.vsearchLink = item.key;
      link.setAttribute('target', '_self');
      relabel(link, item.label);
      const path = link.querySelector('svg path');
      if (path) path.setAttribute('d', item.icon);
      // A real page load, not Immich's (SvelteKit) client-side router.
      link.setAttribute('data-sveltekit-reload', '');
      link.setAttribute('rel', 'external');
      after.after(clone);
      after = clone;
    }
  }

  // Immich re-renders the sidebar on navigation and when the phone menu opens; re-add then.
  let queued = false;
  const schedule = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; add(); });
  };
  new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true });
  add();
})();
