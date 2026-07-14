const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];

/* ---------- tabs ---------- */
function showTab(name) {
  const btn = $(`nav button[data-tab="${name}"]`);
  const panel = $('#' + name);
  if (!btn || !panel) return;
  $$('nav button').forEach(x => x.classList.remove('on'));
  $$('.tab').forEach(x => x.classList.remove('on'));
  btn.classList.add('on');
  panel.classList.add('on');
}

$$('nav button').forEach(b => b.onclick = () => {
  showTab(b.dataset.tab);
  history.replaceState(null, '', '?tab=' + b.dataset.tab);  // so a reload keeps your place
});

// Deep-link: /?tab=statcast opens that tab directly, and survives the pull auto-reload.
// A query param, NOT a #hash: a hash makes the browser jump to the element with that id,
// which scrolls the nav and filters off the top of the page.
const wanted = new URLSearchParams(location.search).get('tab');
if (wanted) showTab(wanted);

/* ---------- segmented sub-toggles (SP/Hitters, Hitters/Pitchers) ---------- */
$$('.seg button').forEach(b => b.onclick = () => {
  const seg = b.closest('.seg');
  seg.querySelectorAll('button').forEach(x => x.classList.remove('on'));
  b.classList.add('on');
  // Only touch subs that belong to this segment's own tab.
  const scope = b.closest('.tab');
  scope.querySelectorAll('.sub').forEach(x => x.classList.remove('on'));
  scope.querySelector('#' + b.dataset.target).classList.add('on');
});

/* ---------- status filter ----------
   Scoped to the tab the bar sits in, so it covers every table there -- Statcast has
   two (hitters/pitchers) and Streamers has one per start date. "Rostered" includes IL:
   an injured player is still on your team. */
function applyFilter(tab, want) {
  let shown = 0, total = 0;
  tab.querySelectorAll('tr[data-status]').forEach(tr => {
    const status = tr.dataset.status;
    const keep = want === 'ALL'
      || status === want
      || (want === 'MINE' && status === 'IL');
    tr.hidden = !keep;
    total++;
    if (keep) shown++;
  });

  const label = tab.querySelector('.filters .count');
  if (label) label.textContent = want === 'ALL' ? `${total} players`
                                                : `${shown} of ${total}`;
}

$$('.filters .filter button').forEach(b => b.onclick = () => {
  const bar = b.closest('.filter');
  bar.querySelectorAll('button').forEach(x => x.classList.remove('on'));
  b.classList.add('on');
  applyFilter(b.closest('.tab'), b.dataset.filter);
});

// Seed the counts on load.
$$('.filters').forEach(f => applyFilter(f.closest('.tab'), 'ALL'));

/* ---------- sortable tables ----------
   data-sort on a cell overrides its text, so a percentile bar (which renders no
   number of its own in the DOM order we want) still sorts by its real value. */
$$('table[data-sortable] th.sortable').forEach((th, i) => {
  th.onclick = () => {
    const table = th.closest('table');
    const idx = [...th.parentNode.children].indexOf(th);
    const numeric = th.dataset.type === 'num';
    const desc = !th.classList.contains('desc');

    table.querySelectorAll('th').forEach(x => x.classList.remove('asc', 'desc'));
    th.classList.add(desc ? 'desc' : 'asc');

    const body = table.tBodies[0];
    const rows = [...body.rows];
    const val = row => {
      const cell = row.cells[idx];
      const raw = cell.dataset.sort !== undefined ? cell.dataset.sort : cell.textContent.trim();
      return numeric ? (parseFloat(raw) || 0) : raw.toLowerCase();
    };
    rows.sort((a, b) => {
      const x = val(a), y = val(b);
      if (x < y) return desc ? 1 : -1;
      if (x > y) return desc ? -1 : 1;
      return 0;
    });
    rows.forEach(r => body.appendChild(r));
  };
});

/* ---------- chopping block ---------- */
$$('.chop').forEach(cb => cb.onchange = async () => {
  const body = new URLSearchParams({player_key: cb.dataset.key, name: cb.dataset.name});
  await fetch('/chopping-block/toggle', {method: 'POST', body});
  location.reload();  // cut order and the paired drop both change
});

$$('#order .up, #order .down').forEach(b => b.onclick = async () => {
  const row = b.closest('tr');
  const sib = b.classList.contains('up') ? row.previousElementSibling : row.nextElementSibling;
  if (!sib) return;
  b.classList.contains('up') ? row.parentNode.insertBefore(row, sib)
                             : row.parentNode.insertBefore(sib, row);
  const keys = $$('#order tr').map(r => r.dataset.key);
  await fetch('/chopping-block/reorder', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({player_keys: keys}),
  });
  location.reload();
});

/* ---------- transactions: the only click that writes to Yahoo ---------- */
$$('.approve').forEach(btn => btn.onclick = async () => {
  const {add, addName, drop, dropName} = btn.dataset;
  const what = drop ? `Add ${addName} and drop ${dropName}?`
                    : `Add ${addName} into an open roster spot? Nobody is dropped.`;
  if (!confirm(`${what}\n\nThis is a real transaction in your league.`)) return;

  btn.disabled = true;
  btn.textContent = 'Filing...';
  const body = new URLSearchParams({
    add_key: add, add_name: addName, drop_key: drop, drop_name: dropName,
  });
  const data = await (await fetch('/transaction', {method: 'POST', body})).json();

  if (!data.ok) {
    btn.textContent = 'Failed';
    btn.disabled = false;
    alert(data.error);
    return;
  }
  btn.textContent = data.pending ? 'Claim filed' : 'Done';
  btn.classList.remove('primary');
});

/* ---------- data pulls ---------- */
let watching = false;

function watchPull() {
  if (watching) return;
  watching = true;

  const log = $('#log');
  const btn = $('#refresh');
  if (btn) btn.disabled = true;
  log.style.display = 'block';

  const poll = setInterval(async () => {
    const s = await (await fetch('/run/status')).json();
    log.textContent = s.log.join('\n');
    log.scrollTop = log.scrollHeight;
    if (s.active) return;

    clearInterval(poll);
    watching = false;
    if (s.error) {
      log.textContent += '\n\nERROR: ' + s.error;
      if (btn) btn.disabled = false;
    } else {
      location.reload();
    }
  }, 1000);
}

if ($('#refresh')) {
  $('#refresh').onclick = async () => {
    await fetch('/run', {method: 'POST'});
    watchPull();
  };
}

// The hourly refresh fires on its own; pick it up without the user clicking anything.
setInterval(async () => {
  const s = await (await fetch('/run/status')).json();
  if (s.active) watchPull();
}, 15000);

/* ---------- quit ---------- */
if ($('#quit')) {
  $('#quit').onclick = async () => {
    if (!confirm('Stop the Fantasy Baseball server?\n\nData already pulled stays on disk.')) return;
    await fetch('/shutdown', {method: 'POST'}).catch(() => {});  // the server dies mid-response
    document.body.innerHTML =
      '<main><div class="banner">Server stopped. You can close this tab.</div></main>';
  };
}
