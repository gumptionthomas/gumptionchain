// The derived-identity recovery-phrase surface: chunk the 24-word BIP-39 phrase
// for display, render it, and wire copy + "I've saved it" confirm. The phrase is
// the derived identity's only seed-bearing backup (no encrypted artifact). DOM-free
// pure logic up top; DOM wiring in init(). Mirrors the hub's recovery-phrase.mjs.
const COLS = 4;

export function chunkPhrase(mnemonic) {
  const words = String(mnemonic || '').trim().split(/\s+/).filter(Boolean);
  const rows = [];
  for (let i = 0; i < words.length; i += COLS) {
    rows.push(words.slice(i, i + COLS).map((word, j) => ({ n: i + j + 1, word })));
  }
  return rows;
}

export function renderPhrase(doc, container, mnemonic) {
  container.replaceChildren?.();
  for (const row of chunkPhrase(mnemonic)) {
    const rowEl = doc.createElement('div');
    rowEl.className = 'rp-row d-flex flex-wrap gap-2 mb-1';
    for (const { n, word } of row) {
      const cell = doc.createElement('span');
      cell.className = 'rp-cell badge bg-light text-dark border';
      const num = doc.createElement('span');
      num.className = 'rp-num text-muted me-1';
      num.textContent = String(n);
      const w = doc.createElement('span');
      w.className = 'rp-word fw-bold';
      w.textContent = word;
      cell.append(num, w);
      rowEl.append(cell);
    }
    container.append(rowEl);
  }
}

// Wire the partial: render the words, the Copy button, and the confirm that fires
// onConfirm (the host uses it to reveal its continue affordance). root defaults to
// document; ids: rp-words, rp-copy, rp-confirm, rp-status.
export function init({ mnemonic, onConfirm, root = document } = {}) {
  const $ = (id) => root.getElementById(id);
  renderPhrase(root, $('rp-words'), mnemonic);
  const copyBtn = $('rp-copy');
  const confirmBox = $('rp-confirm');
  const status = $('rp-status');
  if (confirmBox) confirmBox.checked = false;
  if (status) { status.textContent = ''; status.dataset.kind = 'info'; }
  const setStatus = (text, ok) => {
    if (!status) return;
    status.textContent = text;
    status.dataset.kind = ok ? 'ok' : 'error';
  };
  if (copyBtn) {
    copyBtn.onclick = async () => {
      try {
        await navigator.clipboard.writeText(mnemonic);
        setStatus('Copied — paste it somewhere safe, then clear your clipboard.', true);
      } catch {
        setStatus("Couldn't copy — write the words down instead.", false);
      }
    };
  }
  if (confirmBox) {
    confirmBox.onchange = () => { if (confirmBox.checked) onConfirm?.(); };
  }
}
