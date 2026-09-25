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

  // Fragment components stay encoded until parsed here: a named destination
  // can contain '&', '+', '%' or '=', including in a Remote GoTo PDF action.
  async function navigate(application, fragment) {
    if (typeof fragment !== 'string' || !fragment || !application.pdfDocument) return false;
    const raw = fragment.replace(/^#/, '');
    const params = new URLSearchParams(raw);
    const pdf = application.pdfDocument;
    try {
      if (params.has('nameddest')) {
        if (params.getAll('nameddest').length !== 1) return false;
        const destination = await pdf.getDestination(params.get('nameddest'));
        if (!destination) return false;
        await application.pdfLinkService.goToDestination(destination);
        return true;
      }
      if (['page', 'zoom', 'pagemode', 'search'].some(key => params.has(key))) {
        if (params.has('page')) {
          const page = params.get('page');
          if (params.getAll('page').length !== 1 || !/^[1-9][0-9]*$/.test(page) || Number(page) > pdf.numPages) return false;
        }
        application.pdfLinkService.setHash(raw);
        return true;
      }
      const name = decodeURIComponent(raw);
      // An actual destination name takes precedence over JSON-looking names.
      let destination = await pdf.getDestination(name);
      if (!destination && name.startsWith('[')) {
        const value = JSON.parse(name);
        const sizes = { XYZ: 5, Fit: 2, FitB: 2, FitH: 3, FitBH: 3, FitV: 3, FitBV: 3, FitR: 6 };
        if (!Array.isArray(value) || !Number.isInteger(value[0]) || value[0] < 0 || value[0] >= pdf.numPages ||
            !value[1] || sizes[value[1].name] !== value.length ||
            !value.slice(2).every(item => (item === null && value[1].name !== 'FitR') || (typeof item === 'number' && Number.isFinite(item)))) return false;
        destination = value;
      }
      if (!destination) return false;
      await application.pdfLinkService.goToDestination(destination);
      return true;
    } catch (_) {
      return false;
    }
  }

  const api = { isLocalLink, enableLocalAnnotations, install, navigate };
  if (typeof module !== 'undefined') module.exports = api;
  else root.PdfLocalLinks = api;
})(globalThis);
