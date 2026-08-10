import { el } from '../core/dom.js';
import { toast } from './toast.js';

/**
 * Toggle({ label, checked, onChange }) -> { wrap, get, set, input } — styled
 * switch. If `onChange` is given it's treated as an immediate-persist action
 * (e.g. "enable on boot"): the input is disabled while it runs, and a
 * rejection reverts the visual state and surfaces an error toast rather than
 * leaving the UI showing a value that was never actually saved.
 */
export function Toggle(opts = {}) {
  const { label, checked = false, onChange } = opts;
  const input = el('input', { type: 'checkbox', checked });
  if (onChange) {
    input.addEventListener('change', async (e) => {
      const next = e.target.checked;
      input.disabled = true;
      try {
        await onChange(next);
      } catch (err) {
        input.checked = !next;
        toast(err && err.message ? err.message : 'Something went wrong', 'error');
      } finally {
        input.disabled = false;
      }
    });
  }
  const wrap = el('label', { class: 'switch' },
    input,
    el('span', { class: 'switch-track' }, el('span', { class: 'switch-thumb' })),
    label ? el('span', { class: 'switch-label' }, label) : null);
  return { wrap, input, get: () => input.checked, set: (v) => { input.checked = !!v; } };
}
