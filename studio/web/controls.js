import { html } from './vendor/preact-htm.js';

// One set of style controls, used for the library (Style page), a whole title
// and a single poster.  Every control is a /poster parameter.
const FADE = [['off', 'Off'], ['low', 'Light'], ['medium', 'Medium'], ['high', 'Strong']];
export const GROUPS = [
  ['Logo', [
    { k: 'logo_max_w_ratio', t: 'range', label: 'Width', min: 0.4, max: 0.95, step: 0.01, pct: true },
    { k: 'logo_max_h_ratio', t: 'range', label: 'Max height', min: 0.1, max: 0.45, step: 0.01, pct: true },
    { k: 'logo_bottom_ratio', t: 'range', label: 'From bottom', min: 0, max: 0.3, step: 0.005, pct: true },
    { k: 'logo_color', t: 'color', label: 'Colour' },
  ]],
  ['Fades', [
    { k: 'top_gradient', t: 'select', label: 'Top', options: FADE },
    { k: 'bottom_gradient', t: 'select', label: 'Bottom', options: FADE },
    { k: 'fade_color', t: 'color', label: 'Colour' },
  ]],
  ['Notch', [
    { k: 'show_award_sash', t: 'bool', label: 'Show' },
    { k: 'notch_label', t: 'text', label: 'Text', placeholder: 'Automatic' },
    { k: 'sash_badge_style', t: 'select', label: 'Look', options: [['frosted', 'Frosted'], ['black', 'Black'], ['silver', 'Silver'], ['gold', 'Gold']] },
    { k: 'tint_color', t: 'color', label: 'Colour' },
    { k: 'notch_text_color', t: 'color', label: 'Text colour' },
  ]],
];

const fmt = (c, v) => (c.pct ? `${Math.round(+v * 100)}%` : v);

/**
 * values:   this level's own settings {param: value}
 * inherited: what applies when this level doesn't set it {param: value}
 * from:     label for where inherited values come from ("library", "title")
 * onSet(k, v | null)
 */
export function StyleControls({ values, inherited, from, onSet, pickColor, groups = GROUPS }) {
  return html`<div class="ctl-groups">${groups.map(([title, ctls]) => html`<section class="ctl-group">
    <h3>${title}</h3>
    ${ctls.map(c => {
      const own = values[c.k] !== undefined && values[c.k] !== '';
      const v = own ? values[c.k] : (inherited[c.k] ?? '');
      const reset = own ? html`<button class="dot" title=${`Back to the ${from}'s`} onClick=${() => onSet(c.k, null)}>↺</button>`
        : html`<span class="dot-pad"></span>`;
      let input;
      if (c.t === 'range') {
        input = html`<input type="range" min=${c.min} max=${c.max} step=${c.step} value=${v === '' ? c.min : v}
          onInput=${e => onSet(c.k, e.target.value)} /><span class="val">${v === '' ? '–' : fmt(c, v)}</span>`;
      } else if (c.t === 'select') {
        input = html`<select value=${v} onChange=${e => onSet(c.k, e.target.value)}>
          ${c.options.map(([o, l]) => html`<option value=${o}>${l}</option>`)}</select>`;
      } else if (c.t === 'bool') {
        input = html`<input type="checkbox" class="toggle" checked=${v !== 'false'} onChange=${e => onSet(c.k, e.target.checked ? 'true' : 'false')} />`;
      } else if (c.t === 'text') {
        input = html`<input type="text" maxlength="40" value=${own ? values[c.k] : ''} placeholder=${inherited[c.k] || c.placeholder}
          onInput=${e => onSet(c.k, e.target.value.trim() ? e.target.value : null)} />`;
      } else {
        input = html`<span class="color-pick">
          <input type="color" class=${v ? '' : 'unset'} value=${v ? '#' + String(v).replace('#', '') : '#888888'}
            onInput=${e => onSet(c.k, e.target.value.slice(1))} />
          ${pickColor && html`<button class="mini" title="Pick from the preview" onClick=${() => pickColor(c.k, c.label)}>⌖</button>`}
          <span class="val">${v ? '' : 'auto'}</span></span>`;
      }
      return html`<div class="ctl ${own ? 'own' : ''}"><label>${c.label}</label><div class="ctl-in">${input}</div>${reset}</div>`;
    })}
  </section>`)}</div>`;
}
