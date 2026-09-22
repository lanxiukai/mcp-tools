/* Local link bridge for the vendored PDF.js viewer. MIT, see ../LICENSE. */
(function (root) {
  'use strict';
  function isLocalLink(value) {
    if (typeof value !== 'string' || !value.trim() || /[\x00-\x1f]/.test(value)) return false;
    const raw = value.trim();
    if (raw.startsWith('#')) return false;
    const scheme = /^([a-z][a-z\d+.-]*):/i.exec(raw);
    return !scheme || /^(file|vscode-remote)$/i.test(scheme[1]);
  }
  function enableLocalAnnotations(annotations) {
    for (const annotation of annotations || []) {
      if (annotation.subtype === 'Link' && !annotation.url && isLocalLink(annotation.unsafeUrl)) {
        annotation.url = annotation.unsafeUrl;
      }
    }
  }
  function install(pdfjs, vscode, document, state) {
    const render = pdfjs.AnnotationLayer.render;
    pdfjs.AnnotationLayer.render = function (parameters) {
      enableLocalAnnotations(parameters.annotations);
      return render.call(this, parameters);
    };
    document.addEventListener('click', function (event) {
      const anchor = event.target.closest && event.target.closest('a');
      if (!anchor) return;
      const href = anchor.getAttribute('href');
      if (isLocalLink(href) || /^https?:/i.test(href || '')) {
        event.preventDefault();
        event.stopImmediatePropagation();
        vscode.postMessage({ type: 'open-document-link', href, resolvedByConverter: state.resolvedByConverter });
      }
    }, true);
  }
  const api = { isLocalLink, enableLocalAnnotations, install };
  if (typeof module !== 'undefined') module.exports = api;
  else root.PdfLocalLinks = api;
})(globalThis);
