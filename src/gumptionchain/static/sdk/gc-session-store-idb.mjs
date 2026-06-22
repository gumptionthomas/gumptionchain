// Real IndexedDB session store adapter (browser-only). Implements the `store`
// interface for the session-signer: one DB, one object store, a single fixed-key
// record. Uses a DISTINCT dbName from the durable keyring store so the
// auto-locking session lives in its own database. The stored record holds
// structured-cloneable values including non-extractable CryptoKeys — IndexedDB
// structured-clones them natively, so no special handling is needed. Touches
// `indexedDB` only inside functions so it imports cleanly in Node.
const STORE = 'signing_key';
const KEY = 'singleton';

function openDb(dbName) {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(dbName, 1);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) {
        req.result.createObjectStore(STORE);
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tx(db, mode, fn) {
  return new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode);
    const req = fn(t.objectStore(STORE));
    t.oncomplete = () => resolve(req ? req.result : undefined);
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error);
  });
}

export function makeSessionStore({ dbName = 'gc-session-signer' } = {}) {
  return {
    async get() {
      const db = await openDb(dbName);
      try {
        const rec = await tx(db, 'readonly', (s) => s.get(KEY));
        return rec ?? null;
      } finally {
        db.close();
      }
    },
    async put(record) {
      const db = await openDb(dbName);
      try {
        await tx(db, 'readwrite', (s) => s.put(record, KEY));
      } finally {
        db.close();
      }
    },
    async delete() {
      const db = await openDb(dbName);
      try {
        await tx(db, 'readwrite', (s) => s.delete(KEY));
      } finally {
        db.close();
      }
    },
  };
}
