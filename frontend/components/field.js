import { el } from '../core/dom.js';

/**
 * Field({ label, type, value, help, placeholder, required, id }) -> { wrap, get, set, input }
 * type: text | password | number | email | textarea | select
 * For type 'select', pass `options: [{ value, label }]`.
 */
export function Field(opts = {}) {
  const {
    label, type = 'text', value, help, placeholder, required = false, options, min, max, step, disabled = false,
  } = opts;
  let input;
  if (type === 'textarea') {
    input = el('textarea', { class: 'input', placeholder, disabled }, value == null ? '' : String(value));
  } else if (type === 'select') {
    input = el('select', { class: 'input', disabled },
      ...(options || []).map(o => el('option', { value: o.value, selected: o.value === value }, o.label ?? o.value)));
  } else {
    input = el('input', {
      class: 'input', type, placeholder, disabled,
      value: value == null ? '' : String(value),
      min: min == null ? null : String(min), max: max == null ? null : String(max), step: step == null ? null : String(step),
    });
  }
  const labelRow = label
    ? el('span', { class: 'field-label' }, label, required ? el('span', { class: 'error-text' }, ' *') : null)
    : null;
  const wrap = el('label', { class: 'field' },
    labelRow,
    input,
    help ? el('span', { class: 'field-help' }, help) : null);

  return {
    wrap,
    input,
    get: () => input.value,
    set: (v) => { input.value = v == null ? '' : String(v); },
  };
}

/** A checkbox-driven switch — see Toggle() in toggle.js for the styled variant. */
export function Checkbox({ label, checked = false } = {}) {
  const input = el('input', { type: 'checkbox', checked });
  const wrap = el('label', { class: 'row' }, input, el('span', { class: 'text-sm' }, label));
  return { wrap, get: () => input.checked, input };
}
