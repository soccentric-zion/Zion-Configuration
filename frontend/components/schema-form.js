// Generic schema -> form renderer (spec §3: schema-driven rendering
// everywhere). Adding a customer setting must require ZERO UI code changes.
//
// A schema descriptor looks like:
//   { id, title, groups: [ { id, title, fields: [ { id, title, field_type,
//     required, options, validation, help, apply_hook } ] } ], validate? }
//
// field_type is one of: string | int | bool | enum | secret.
// The renderer builds inputs, applies client-side validation rules, and
// exposes `getValue()` / `validate()`. The backend re-validates (spec §2
// boundary).

import { el } from '../core/dom.js';

// Validate one field against its validation rules; returns '' or an error msg.
function validateField(field, value) {
  const v = field.validation || {};
  if (field.required && (value === '' || value == null)) return 'required';
  if (value === '' || value == null) return '';
  switch (field.field_type) {
    case 'int': {
      const n = Number(value);
      if (!Number.isInteger(n)) return 'must be an integer';
      if (v.min != null && n < v.min) return `min ${v.min}`;
      if (v.max != null && n > v.max) return `max ${v.max}`;
      break;
    }
    case 'string':
    case 'secret': {
      const s = String(value);
      if (v.min_len != null && s.length < v.min_len) return `min length ${v.min_len}`;
      if (v.max_len != null && s.length > v.max_len) return `max length ${v.max_len}`;
      if (v.pattern) {
        try { if (!new RegExp(v.pattern).test(s)) return 'pattern mismatch'; } catch (_) { /* bad pattern: skip */ }
      }
      break;
    }
    case 'enum':
      if (field.options && !field.options.some(o => o.value === value)) return 'invalid choice';
      break;
  }
  return '';
}

// Build an input element for a field type.
function buildInput(field, value) {
  const t = field.field_type;
  if (t === 'bool') {
    return el('input', { type: 'checkbox', checked: value === true || value === 'true' });
  }
  if (t === 'enum') {
    const sel = el('select', { class: 'input' });
    sel.appendChild(el('option', { value: '' }, '—'));
    for (const o of field.options || []) {
      sel.appendChild(el('option', { value: o.value, selected: o.value === value }, o.label || o.value));
    }
    return sel;
  }
  const inputType = t === 'secret' ? 'password' : t === 'int' ? 'number' : 'text';
  const attrs = { class: 'input', type: inputType, value: value == null ? '' : String(value) };
  if (t === 'int') attrs.step = '1';
  return el('input', attrs);
}

/** Render a full schema descriptor, bound to `values`. Returns { element, getValue, validate }. */
export function renderSchemaForm(descriptor, values) {
  const values0 = values || {};
  const inputs = {}; // field id -> { input, field, errorEl }
  const root = el('div', { class: 'schema-form stack' });

  if (descriptor.title) root.appendChild(el('h2', {}, descriptor.title));

  const groups = descriptor.groups && descriptor.groups.length ? descriptor.groups : [{
    id: '_default', title: '', fields: descriptor.fields || [],
  }];

  for (const g of groups) {
    const card = el('div', { class: 'card' });
    if (g.title) card.appendChild(el('div', { class: 'card-header' }, el('h3', {}, g.title)));
    const form = el('div', { class: 'card-body form-grid' });
    for (const f of g.fields || []) {
      const input = buildInput(f, values0[f.id]);
      const err = el('span', { class: 'field-error', hidden: true });
      const wrap = f.field_type === 'bool'
        ? el('label', { class: 'row' }, input, el('span', {}, f.title || f.id))
        : el('label', { class: 'field' },
          el('span', { class: 'field-label' }, f.title || f.id, f.required ? el('span', { class: 'error-text' }, ' *') : null),
          input,
          f.help ? el('span', { class: 'field-help' }, f.help) : null,
          err);
      inputs[f.id] = { input, field: f, errorEl: err };
      form.appendChild(wrap);
    }
    card.appendChild(form);
    root.appendChild(card);
  }

  function readOne(input, field) {
    if (field.field_type === 'bool') return input.checked;
    if (field.field_type === 'int') {
      const n = Number(input.value);
      return Number.isInteger(n) ? n : null;
    }
    return input.value;
  }

  function getValue() {
    const out = {};
    for (const [id, { input, field }] of Object.entries(inputs)) out[id] = readOne(input, field);
    return out;
  }

  function validate() {
    let ok = true;
    for (const [, { input, field, errorEl }] of Object.entries(inputs)) {
      const msg = validateField(field, readOne(input, field));
      if (msg) { ok = false; errorEl.textContent = msg; errorEl.hidden = false; }
      else { errorEl.hidden = true; errorEl.textContent = ''; }
    }
    return ok;
  }

  return { element: root, getValue, validate };
}

export { validateField };
