import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  resolveTheme,
  nextTheme,
  applyTheme,
  initThemeToggle,
  STORAGE_KEY,
} from './theme.mjs';

test('resolveTheme: a stored value always wins', () => {
  assert.equal(resolveTheme('dark', false), 'dark');
  assert.equal(resolveTheme('light', true), 'light');
});

test('resolveTheme: absent/invalid falls back to the OS preference', () => {
  assert.equal(resolveTheme(null, true), 'dark');
  assert.equal(resolveTheme(null, false), 'light');
  assert.equal(resolveTheme('nonsense', true), 'dark');
  assert.equal(resolveTheme('', false), 'light');
});

test('nextTheme flips', () => {
  assert.equal(nextTheme('dark'), 'light');
  assert.equal(nextTheme('light'), 'dark');
});

test('applyTheme sets data-bs-theme on the given root', () => {
  const calls = [];
  const root = { setAttribute: (k, v) => calls.push([k, v]) };
  applyTheme('dark', root);
  assert.deepEqual(calls, [['data-bs-theme', 'dark']]);
});

// --- fakes for initThemeToggle -----------------------------------------
function fakeEnv({ storedTheme = null, osDark = false } = {}) {
  const store = new Map();
  if (storedTheme !== null) store.set(STORAGE_KEY, storedTheme);
  let osChangeHandler = null;
  let clickHandler = null;
  const root = { attr: null, setAttribute: (_k, v) => { root.attr = v; } };
  const icon = { className: '' };
  const button = {
    _label: null,
    querySelector: () => icon,
    setAttribute: (_k, v) => { button._label = v; },
    addEventListener: (_evt, fn) => { clickHandler = fn; },
  };
  const win = {
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, v),
    },
    matchMedia: () => ({
      matches: osDark,
      addEventListener: (_evt, fn) => { osChangeHandler = fn; },
    }),
  };
  const doc = {
    documentElement: root,
    getElementById: () => button,
  };
  return {
    win, doc, root, icon, button, store,
    fireClick: () => clickHandler && clickHandler(),
    fireOsChange: (nowDark) => {
      win.matchMedia = () => ({ matches: nowDark, addEventListener() {} });
      osChangeHandler && osChangeHandler();
    },
  };
}

test('initThemeToggle: paints the OS theme when unstored (auto)', () => {
  const env = fakeEnv({ osDark: true });
  initThemeToggle({ window: env.win, document: env.doc });
  assert.equal(env.root.attr, 'dark');
});

test('initThemeToggle: click flips, persists, and repaints', () => {
  const env = fakeEnv({ osDark: false }); // shows light
  initThemeToggle({ window: env.win, document: env.doc });
  assert.equal(env.root.attr, 'light');
  env.fireClick();
  assert.equal(env.store.get(STORAGE_KEY), 'dark');
  assert.equal(env.root.attr, 'dark');
});

test('initThemeToggle: OS change is followed ONLY while unstored', () => {
  const env = fakeEnv({ osDark: false });
  initThemeToggle({ window: env.win, document: env.doc });
  env.fireOsChange(true);           // still auto -> follows
  assert.equal(env.root.attr, 'dark');
  env.fireClick();                  // store 'light' (opposite of dark)
  assert.equal(env.store.get(STORAGE_KEY), 'light');
  env.fireOsChange(false);          // stored now -> ignored, stays light
  assert.equal(env.root.attr, 'light');
});
