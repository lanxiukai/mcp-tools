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
    const application = PDFViewerApplication
    const linkState = { resolvedByConverter: false }
    let initialized = false
    let loading = false
    let reloadPending = false
    let restoreView = null
    let pendingFragment = ''
    let revision = 0

    PdfLocalLinks.install(pdfjsLib, vscode, document, linkState)
    PDFViewerApplicationOptions.set('enableScripting', false)
    PDFViewerApplicationOptions.set('isEvalSupported', false)
    PDFViewerApplicationOptions.set('cMapUrl', config.cMapUrl)
    PDFViewerApplicationOptions.set('standardFontDataUrl', config.standardFontDataUrl)
    const loadOpts = {
      useWorkerFetch: false,
      cMapUrl: config.cMapUrl,
      cMapPacked: true,
      standardFontDataUrl: config.standardFontDataUrl,
      docBaseUrl: config.documentUri,
      isEvalSupported: false
    }

    function captureView() {
      const viewer = application.pdfViewer
      const page = viewer.getPageView(application.page - 1)
      const rect = page.div.getBoundingClientRect()
      const container = viewer.container.getBoundingClientRect()
      return {
        page: application.page,
        point: page.getPagePoint(container.left - rect.left - page.div.clientLeft,
          container.top - rect.top - page.div.clientTop),
        scale: viewer.currentScaleValue,
        rotation: viewer.pagesRotation,
        scroll: viewer.scrollMode,
        spread: viewer.spreadMode,
        sidebar: application.pdfSidebar.isOpen,
      }
    }

    function restorePosition(saved) {
      const viewer = application.pdfViewer
      viewer.pagesRotation = saved.rotation
      viewer.scrollMode = saved.scroll
      viewer.spreadMode = saved.spread
      viewer.currentScaleValue = saved.scale
      if (saved.sidebar) application.pdfSidebar.open()
      else application.pdfSidebar.close()
      const pageNumber = Math.min(saved.page, application.pagesCount)
      // Page-relative PDF coordinates survive different page sizes before the
      // current page. If pages were removed, show the new last page instead.
      viewer.scrollPageIntoView({
        pageNumber,
        destArray: pageNumber === saved.page ? [null, { name: 'XYZ' }, ...saved.point, null] : undefined,
        allowNegativeOffset: true,
      })
    }

    async function navigate(fragment) {
      if (!await PdfLocalLinks.navigate(application, fragment)) {
        vscode.postMessage({ type: 'missing-destination' })
      }
    }

    async function openLatest() {
      const url = new URL(config.path)
      // The same filename can have been replaced without a usable cache
      // validator. Give every load its own webview resource URL.
      url.searchParams.set('_pdf_reload', Date.now() + '-' + (++revision))
      let onInitialized
      const viewInitialized = new Promise(resolve => {
        onInitialized = resolve
        application.eventBus.on('documentinit', onInitialized)
      })
      try {
        await application.open(url.href, loadOpts)
        await Promise.all([viewInitialized, application.pdfViewer.pagesPromise])
        // Let PDF.js finish its initial history/layout restoration first.
        await new Promise(resolve => requestAnimationFrame(resolve))
        if (restoreView) {
          restorePosition(restoreView)
          restoreView = null
        } else {
          const defaults = config.defaults
          application.pdfCursorTools.switchTool(cursorTools(defaults.cursor))
          application.pdfViewer.currentScaleValue = defaults.scale
          application.pdfViewer.scrollMode = scrollMode(defaults.scrollMode)
          application.pdfViewer.spreadMode = spreadMode(defaults.spreadMode)
          if (defaults.sidebar) application.pdfSidebar.open()
          else application.pdfSidebar.close()
        }
        const metadata = await application.pdfDocument.getMetadata()
        linkState.resolvedByConverter = (metadata.info.Keywords || '').includes('mcp-tools-links:resolved')
      } finally {
        application.eventBus.off('documentinit', onInitialized)
      }
    }

    async function reload() {
      reloadPending = true
      if (!initialized || loading) return
      loading = true
      let succeeded = false
      try {
        do {
          reloadPending = false
          if (!restoreView && application.pdfDocument) restoreView = captureView()
          await openLatest()
        } while (reloadPending)
        succeeded = true
      } catch (_) {
        // A writer may still be saving. Keep the view snapshot for the host's
        // bounded retry and allow later file changes to recover this editor.
        vscode.postMessage({ type: 'reload-failed' })
      } finally {
        loading = false
      }
      if (succeeded) {
        if (pendingFragment) {
          const fragment = pendingFragment
          pendingFragment = ''
          await navigate(fragment)
        }
        vscode.postMessage({ type: 'ready' })
      }
    }

    window.addEventListener('message', async function (event) {
      const message = event.data
      if (message.type === 'navigate' && typeof message.fragment === 'string') {
        if (loading || !application.pdfDocument) pendingFragment = message.fragment
        else await navigate(message.fragment)
      } else if (message.type === 'reload') {
        void reload()
      }
    })

    await application.initializedPromise
    const originalLoad = application.load
    application.load = function (doc) {
      // Fresh resource URLs must still share the document's reading history.
      doc._pdfInfo.fingerprints = [config.path]
      return originalLoad.call(this, doc)
    }
    initialized = true
    void reload()
  }, { once: true })

  window.onerror = function () {
    const msg = document.createElement('body')
    msg.innerText = 'An error occurred while loading the file. Please open it again.'
    document.body = msg
  }
}());
