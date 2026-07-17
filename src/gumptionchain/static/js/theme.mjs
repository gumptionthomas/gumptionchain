// Base-only dark-mode logic. Lives here (not in any shared page template) so
// the hub — which ships its own base.html — never loads it. Drives Bootstrap
// 5.3's native data-bs-theme attribute.

export const STORAGE_KEY = 'gc-theme';

// A stored 'light'/'dark' override always wins; otherwise follow the OS.
export function resolveTheme(stored, osPrefersDark) {
  if (stored === 'light' || stored === 'dark') {
    return stored;
  }
  return osPrefersDark ? 'dark' : 'light';
}

export function nextTheme(current) {
  return current === 'dark' ? 'light' : 'dark';
}

export function applyTheme(theme, root) {
  root.setAttribute('data-bs-theme', theme);
}

// Wire the navbar toggle button (#theme-toggle) and the OS-change listener.
// window/document are injectable for testing.
export function initThemeToggle({ window: win = window, document: doc = document } = {}) {
  const stored = () => win.localStorage.getItem(STORAGE_KEY);
  const current = () => resolveTheme(stored(), win.matchMedia('(prefers-color-scheme: dark)').matches);
  const button = doc.getElementById('theme-toggle');
  const mql = win.matchMedia('(prefers-color-scheme: dark)');

  const paint = () => {
    const theme = current();
    applyTheme(theme, doc.documentElement);
    if (button) {
      const icon = button.querySelector('i');
      if (icon) {
        icon.className = theme === 'dark' ? 'bi bi-sun' : 'bi bi-moon-stars';
      }
      button.setAttribute(
        'aria-label',
        theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme',
      );
    }
  };

  if (button) {
    button.addEventListener('click', () => {
      win.localStorage.setItem(STORAGE_KEY, nextTheme(current()));
      paint();
    });
  }

  // Follow the OS live, but only while the operator hasn't chosen explicitly.
  mql.addEventListener('change', () => {
    if (!stored()) {
      paint();
    }
  });

  paint();
  return { paint };
}
