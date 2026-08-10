import { el } from '../core/dom.js';
import { icon } from '../core/icons.js';
import { toast } from './toast.js';

/**
 * Button({ variant, size, icon: iconName, onClick, disabled }, label)
 * variant: primary | secondary | outline | ghost | link | destructive | destructive-outline
 *
 * `onClick` may be async. It is always awaited: while pending the button is
 * disabled (no double-submits on slow devices), and a rejection is caught
 * and surfaced as an error toast instead of silently vanishing as an
 * unhandled promise rejection (every mutating action in the app goes through
 * this component, so this is the one place that needs to handle it).
 */
export function Button(opts = {}, label) {
  const {
    variant = 'primary', size = 'md', icon: iconName, onClick, disabled = false, type = 'button', title,
  } = opts;
  const cls = ['btn', `btn-${variant}`];
  if (size === 'sm') cls.push('btn-sm');
  if (!label && iconName) cls.push('btn-icon');
  const children = [];
  if (iconName) children.push(icon(iconName, { size: size === 'sm' ? 14 : 15 }));
  if (label) children.push(el('span', {}, label));

  // Icon-only buttons have no visible text for assistive tech to read, so
  // fall back to `title` as the accessible name.
  const ariaLabel = !label && iconName ? title : null;
  const btn = el('button', { type, class: cls.join(' '), disabled, title, 'aria-label': ariaLabel }, ...children);
  if (onClick) {
    btn.run = (...args) => onClick(...args);
    btn.addEventListener('click', async (e) => {
      if (btn.disabled) return;
      btn.disabled = true;
      try {
        await btn.run(e);
      } catch (err) {
        toast(err && err.message ? err.message : 'Something went wrong', 'error');
      } finally {
        btn.disabled = disabled;
      }
    });
  }
  return btn;
}
