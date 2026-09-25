"use strict";

(function () {
  function loadConfig() {
    const elem = document.getElementById('pdf-preview-config')
    if (elem) {
      return JSON.parse(elem.getAttribute('data-config'))
    }
    throw new Error('Could not load configuration.')
  }
  function cursorTools(name) {
    if (name === 'hand') {
      return 1
    }
    return 0
  }
  function scrollMode(name) {
    switch (name) {
      case 'vertical':
        return 0
      case 'horizontal':
        return 1
      case 'wrapped':
        return 2
      default:
        return -1
    }
  }
  function spreadMode(name) {
    switch (name) {
      case 'none':
        return 0
      case 'odd':
        return 1
      case 'even':
        return 2
      default:
        return -1
    }
  }
  window.addEventListener('load', async function () {
    const config = loadConfig()
    const vscode = acquireVsCodeApi()
    const linkState = { resolvedByConverter: false }
    let restoreView = null
    PdfLocalLinks.install(pdfjsLib, vscode, document, linkState)
    PDFViewerApplicationOptions.set('enableScripting', false)
    PDFViewerApplicationOptions.set('isEvalSupported', false)
    PDFViewerApplicationOptions.set('cMapUrl', config.cMapUrl)
    PDFViewerApplicationOptions.set('standardFontDataUrl', config.standardFontDataUrl)
    const loadOpts = {
      url:config.path,
      useWorkerFetch: false,
      cMapUrl: config.cMapUrl,
      cMapPacked: true,
      standardFontDataUrl: config.standardFontDataUrl,
      docBaseUrl: config.documentUri,
      isEvalSupported: false
    }
    PDFViewerApplication.initializedPromise.then(() => {
      const originalLoad = PDFViewerApplication.load
      PDFViewerApplication.load = function (doc) {
        doc._pdfInfo.fingerprints = [config.path]
        return originalLoad.call(this, doc)
      }
      PDFViewerApplication.eventBus.on('pagesloaded', async () => {
        if (restoreView) {
          PDFViewerApplication.pdfViewer.currentScaleValue = restoreView.scale
          PDFViewerApplication.page = Math.min(restoreView.page, PDFViewerApplication.pagesCount)
          PDFViewerApplication.pdfViewer.container.scrollLeft = restoreView.left
          PDFViewerApplication.pdfViewer.container.scrollTop = restoreView.top
          restoreView = null
        }
        const metadata = await PDFViewerApplication.pdfDocument.getMetadata()
        linkState.resolvedByConverter = (metadata.info.Keywords || '').includes('mcp-tools-links:resolved')
        vscode.postMessage({ type: 'ready' })
      })
      const defaults = config.defaults
      const optsOnLoad = () => {
        PDFViewerApplication.pdfCursorTools.switchTool(cursorTools(defaults.cursor))
        PDFViewerApplication.pdfViewer.currentScaleValue = defaults.scale
        PDFViewerApplication.pdfViewer.scrollMode = scrollMode(defaults.scrollMode)
        PDFViewerApplication.pdfViewer.spreadMode = spreadMode(defaults.spreadMode)
        if (defaults.sidebar) {
          PDFViewerApplication.pdfSidebar.open()
        } else {
          PDFViewerApplication.pdfSidebar.close()
        }
        PDFViewerApplication.eventBus.off('documentloaded', optsOnLoad)
      }
      PDFViewerApplication.eventBus.on('documentloaded', optsOnLoad)
      
      // One loading task owns the document and is destroyed by open() on reload.
      PDFViewerApplication.open(config.path, loadOpts)
    })

    window.addEventListener('message', async function (event) {
      const message = event.data
      if (message.type === 'navigate') {
        const fragment = message.fragment
        if (typeof fragment !== 'string' || !PDFViewerApplication.pdfDocument) return
        if (!await PdfLocalLinks.navigate(PDFViewerApplication, fragment)) {
          vscode.postMessage({ type: 'missing-destination' })
        }
        return
      }
      if (message.type !== 'reload') return
      restoreView = {
        page: PDFViewerApplication.page,
        scale: PDFViewerApplication.pdfViewer.currentScaleValue,
        left: PDFViewerApplication.pdfViewer.container.scrollLeft,
        top: PDFViewerApplication.pdfViewer.container.scrollTop,
      }
      // Prevents flickering of page when PDF is reloaded
      const oldResetView = PDFViewerApplication.pdfViewer._resetView
      PDFViewerApplication.pdfViewer._resetView = function () {
        this._firstPageCapability = (0, pdfjsLib.createPromiseCapability)()
        this._onePageRenderedCapability = (0, pdfjsLib.createPromiseCapability)()
        this._pagesCapability = (0, pdfjsLib.createPromiseCapability)()

        this.viewer.textContent = ""
      }

      try {
        await PDFViewerApplication.open(config.path, loadOpts)
      } finally {
        PDFViewerApplication.pdfViewer._resetView = oldResetView
      }
    });
  }, { once: true });

  window.onerror = function () {
    const msg = document.createElement('body')
    msg.innerText = 'An error occurred while loading the file. Please open it again.'
    document.body = msg
  }
}());
