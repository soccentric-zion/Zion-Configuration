// Thin top-of-page progress bar (YouTube/GitHub-style) — gives feedback on
// route changes and slow first-fetches instead of a bare blank moment before
// the loading skeleton appears.

let bar = null;
let hideTimer = null;

function ensure() {
  if (!bar) {
    bar = document.createElement('div');
    bar.className = 'top-progress';
    document.body.appendChild(bar);
  }
  return bar;
}

export function startProgress() {
  const b = ensure();
  clearTimeout(hideTimer);
  b.style.transition = 'none';
  b.style.width = '0%';
  b.classList.add('active');
  // Force reflow so the transition below actually animates from 0%.
  // eslint-disable-next-line no-unused-expressions
  b.offsetHeight;
  b.style.transition = '';
  requestAnimationFrame(() => { b.style.width = '80%'; });
}

export function finishProgress() {
  if (!bar) return;
  bar.style.width = '100%';
  hideTimer = setTimeout(() => {
    bar.classList.remove('active');
    bar.style.width = '0%';
  }, 200);
}
