const meta = JSON.parse(document.getElementById('rights-meta').textContent);
const errorBox = document.getElementById('rights-error');
const counterBox = document.getElementById('ingest-counters');
let busy = false;

async function call(url, payload) {
  if (busy) return;
  busy = true;
  errorBox.textContent = '处理中…';
  document.querySelectorAll('button').forEach(b => b.disabled = b.id === 'print' ? false : true);
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const result = await resp.json();
    if (!resp.ok) throw Error(result.message || '请求失败');
    return result;
  } finally {
    busy = false;
  }
}

function reload(asOf) {
  const q = new URLSearchParams({ offering_id: meta.offering_id });
  if (asOf) q.set('as_of', asOf);
  location.href = '/rights?' + q.toString();
}

function currentAsOf() {
  const v = document.getElementById('as-of').value.trim();
  return v || null;
}

async function ingest() {
  const asOf = currentAsOf();
  const result = await call('/api/rights/ingest', {
    offering_id: meta.offering_id,
    declarations_csv: document.getElementById('declarations-csv').value,
    payments_csv: document.getElementById('payments-csv').value,
    as_of: asOf,
  }).catch(e => { errorBox.textContent = e.message; });
  if (!result) return;
  counterBox.innerHTML = '<span>入库计数</span><b><small>' +
    Object.entries(result.counters).map(([k, v]) => `${k}=${v}`).join(' · ') + '</small></b>';
  reload(asOf);
}

document.getElementById('submit-csv').addEventListener('click', ingest);
document.getElementById('load-sample').addEventListener('click', ingest);

document.getElementById('publish').addEventListener('click', async () => {
  const result = await call('/api/rights/publish', {
    offering_id: meta.offering_id,
    revision: Number(document.getElementById('publish').dataset.revision),
    as_of: currentAsOf(),
  }).catch(e => { errorBox.textContent = e.message; });
  if (result) reload(currentAsOf());
});

document.querySelectorAll('[data-review]').forEach(btn => btn.addEventListener('click', async () => {
  const result = await call('/api/rights/review', {
    offering_id: meta.offering_id,
    revision: Number(btn.dataset.revision),
    decision: btn.dataset.review,
  }).catch(e => { errorBox.textContent = e.message; });
  if (result) reload(currentAsOf());
}));

document.getElementById('print').addEventListener('click', () => print());
