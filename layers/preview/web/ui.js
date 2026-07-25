// Everything the preview does that is not WebGL: fetch the model list, build
// the browser and the controls, keep the URL in sync, report status. Kept free
// of any three.js import so it runs, and is tested, in a plain DOM.

export function formatBytes(n) {
  if (!Number.isFinite(n) || n < 0) return '?'
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB']
  let value = n / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit++
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`
}

export function formatCount(n) {
  return Number.isFinite(n) ? n.toLocaleString('en-US') : '?'
}

/** Triangle counts get long fast, and a card line has room for three characters. */
export function formatShortCount(n) {
  if (!Number.isFinite(n)) return '?'
  if (n < 1000) return String(n)
  if (n < 1000000) return `${(n / 1000).toFixed(n < 10000 ? 1 : 0)}k`
  return `${(n / 1000000).toFixed(1)}M`
}

/** "4 min ago" beats an ISO string when the point is which one is newest. */
export function formatAge(iso, now = Date.now()) {
  const then = Date.parse(iso)
  if (!Number.isFinite(then)) return '?'
  const seconds = Math.max(0, Math.round((now - then) / 1000))
  if (seconds < 60) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.round(hours / 24)
  return days === 1 ? 'yesterday' : `${days} days ago`
}

export async function fetchModels(fetchImpl = globalThis.fetch) {
  const response = await fetchImpl('/api/models', { headers: { Accept: 'application/json' } })
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const message = body && body.message ? body.message : `HTTP ${response.status}`
    const error = new Error(message)
    error.code = body && body.code ? body.code : 'HTTP_ERROR'
    throw error
  }
  if (!body || !Array.isArray(body.models)) {
    const error = new Error('the server returned no model list')
    error.code = 'BAD_RESPONSE'
    throw error
  }
  return body
}

/** A model's stable handle: its id when the server gave one, else its file name. */
export function keyOf(model) {
  return model ? (model.id || model.name) : null
}

// The list arrives newest first, so element 0 is the model someone most likely
// just generated. An explicit id or name wins, but only if it is really there.
export function pickInitial(models, requested) {
  if (!models.length) return null
  if (requested) {
    const match = models.find((m) => m.id === requested || m.name === requested)
    if (match) return match
  }
  return models[0]
}

/** Case-insensitive substring match on the name. An empty query keeps everything. */
export function filterModels(models, query) {
  const needle = (query || '').trim().toLowerCase()
  if (!needle) return models
  return models.filter((m) => m.name.toLowerCase().includes(needle))
}

// One card list, two layouts. The gallery is the same `#model-list` widened
// into a grid rather than a second copy of every card: two lists would mean two
// selection states to keep in step, and the one that is off screen is always
// the one that goes stale.
const LAYOUT = `
<header class="topbar">
  <span class="brand">text-to-3D<span class="dim"> preview</span></span>
  <div class="views" role="group" aria-label="Layout">
    <button id="view-gallery" type="button" aria-pressed="false">Gallery</button>
    <button id="view-single" type="button" aria-pressed="true">Single</button>
  </div>
  <input id="filter" type="search" placeholder="Filter by name" aria-label="Filter models">
  <span class="count" id="model-count"></span>
  <button id="refresh" type="button">Refresh</button>
  <span class="spacer"></span>
  <output id="status" role="status">Loading…</output>
</header>

<div class="body">
  <aside class="sidebar">
    <div class="sidebar-head"><h2>Models</h2></div>
    <div class="model-list" id="model-list" role="listbox" aria-label="Models"></div>
    <p class="sidebar-note" id="sidebar-note" hidden></p>

    <section class="motion" id="motion" hidden>
      <div class="motion-head">
        <h2>Motion</h2>
        <button id="play-pause" type="button" aria-pressed="true">Pause</button>
      </div>
      <div class="clip-list" id="clip-list" role="radiogroup" aria-label="Clips"></div>
    </section>
  </aside>

  <main class="viewer">
    <div class="tabs" role="tablist" aria-label="Preview mode">
      <button id="tab-model" type="button" role="tab" aria-selected="true">Model</button>
      <button id="tab-image" type="button" role="tab" aria-selected="false">Image</button>
    </div>

    <div class="panel" id="panel-model" role="tabpanel" aria-label="Model">
      <div class="stage" id="stage"></div>
      <div class="stage-controls">
        <label class="toggle"><input id="autorotate" type="checkbox" checked> Auto rotate</label>
        <input id="speed" type="range" min="0" max="6" step="0.5" value="1.5" aria-label="Rotation speed">
        <label class="toggle"><input id="wireframe" type="checkbox"> Wireframe</label>
        <label class="toggle"><input id="grid" type="checkbox"> Grid</label>
        <label class="toggle"><input id="quality" type="checkbox" checked> Quality</label>
        <button id="reset-view" type="button">Reset view</button>
      </div>
    </div>

    <div class="panel image-panel" id="panel-image" role="tabpanel" aria-label="Source image" hidden>
      <img id="source-image" alt="" hidden>
      <p class="note" id="source-note">No source image next to this model.</p>
    </div>

    <footer class="statusbar">
      <span class="current" id="current-name"></span>
      <span class="spacer"></span>
      <dl id="stats"></dl>
    </footer>
  </main>
</div>
`

/**
 * Build the interface into `root` and wire it to the callbacks.
 * Every callback is optional so the UI can be driven on its own in a test.
 */
export function mountUi(root, options = {}) {
  const {
    onSelect = () => {},
    onRotationChange = () => {},
    onWireframeChange = () => {},
    onGridChange = () => {},
    onQualityChange = () => {},
    onResetView = () => {},
    onLayoutChange = () => {},
    onClipChange = () => {},
    onPlayingChange = () => {},
    // Given a model, resolves to a data URL of that GLB rendered, or null. Left
    // out when there is no renderer to ask, which is what the DOM tests do.
    onThumbnail = null,
    fetchImpl = globalThis.fetch,
    search = '',
    view = 'single',
    history = globalThis.history,
  } = options

  root.innerHTML = LAYOUT
  const el = {
    status: root.querySelector('#status'),
    autorotate: root.querySelector('#autorotate'),
    speed: root.querySelector('#speed'),
    wireframe: root.querySelector('#wireframe'),
    grid: root.querySelector('#grid'),
    quality: root.querySelector('#quality'),
    viewGallery: root.querySelector('#view-gallery'),
    viewSingle: root.querySelector('#view-single'),
    reset: root.querySelector('#reset-view'),
    refresh: root.querySelector('#refresh'),
    filter: root.querySelector('#filter'),
    list: root.querySelector('#model-list'),
    note: root.querySelector('#sidebar-note'),
    count: root.querySelector('#model-count'),
    tabModel: root.querySelector('#tab-model'),
    tabImage: root.querySelector('#tab-image'),
    panelModel: root.querySelector('#panel-model'),
    panelImage: root.querySelector('#panel-image'),
    stage: root.querySelector('#stage'),
    sourceImage: root.querySelector('#source-image'),
    sourceNote: root.querySelector('#source-note'),
    currentName: root.querySelector('#current-name'),
    stats: root.querySelector('#stats'),
    motion: root.querySelector('#motion'),
    clipList: root.querySelector('#clip-list'),
    playPause: root.querySelector('#play-pause'),
  }

  let models = []
  let selected = null
  let mode = 'model'
  let layout = view
  let clip = null
  let playing = true
  root.dataset.view = layout

  function setStatus(text, kind = 'info') {
    el.status.textContent = text
    el.status.dataset.kind = kind
  }

  function setStats(entries) {
    el.stats.innerHTML = ''
    for (const [label, value] of entries) {
      const dt = document.createElement('dt')
      dt.textContent = label
      const dd = document.createElement('dd')
      dd.textContent = value
      el.stats.append(dt, dd)
    }
  }

  function statsFor(model) {
    const entries = [['size', formatBytes(model.byteSize)]]
    if (typeof model.triangles === 'number') entries.push(['triangles', formatCount(model.triangles)])
    if (typeof model.materials === 'number') entries.push(['materials', formatCount(model.materials)])
    if (typeof model.joints === 'number') entries.push(['joints', formatCount(model.joints)])
    entries.push(['modified', formatAge(model.modifiedAt)])
    return entries
  }

  /**
   * The clip list for the selected model. Names come from the server's read of
   * the file, and are replaced by the viewer's own list once it has parsed the
   * GLB, because the renderer is the one that will actually play them.
   */
  function showClips(names, active = null) {
    el.clipList.innerHTML = ''
    el.motion.hidden = !names.length
    if (!names.length) {
      clip = null
      return
    }
    clip = active && names.includes(active) ? active : names[0]
    for (const name of names) {
      const button = document.createElement('button')
      button.type = 'button'
      button.className = 'clip'
      button.setAttribute('role', 'radio')
      button.setAttribute('aria-checked', String(name === clip))
      button.textContent = name
      button.addEventListener('click', () => selectClip(name))
      el.clipList.append(button)
    }
    markClip()
  }

  function markClip() {
    for (const button of el.clipList.querySelectorAll('.clip')) {
      const on = button.textContent === clip
      button.setAttribute('aria-checked', String(on))
      button.classList.toggle('selected', on)
    }
  }

  function selectClip(name) {
    clip = name
    markClip()
    onClipChange(name)
  }

  function setPlaying(on) {
    playing = on
    el.playPause.textContent = on ? 'Pause' : 'Play'
    el.playPause.setAttribute('aria-pressed', String(on))
    onPlayingChange(on)
  }

  // The picture the mesh was reconstructed from. Seeing both is the only way to
  // tell a bad reconstruction from a bad prompt, so it gets its own full-size
  // panel rather than a thumbnail in a corner.
  function showSource(model) {
    const source = model && model.source
    if (source) {
      el.sourceImage.src = source.uri
      el.sourceImage.alt = `Source image for ${model.name}`
      el.sourceImage.hidden = false
      const dims = source.width && source.height ? `${source.width}x${source.height}, ` : ''
      el.sourceNote.textContent = `${source.name} (${dims}${formatBytes(source.byteSize)})`
      el.tabImage.disabled = false
    } else {
      el.sourceImage.removeAttribute('src')
      el.sourceImage.alt = ''
      el.sourceImage.hidden = true
      el.sourceNote.textContent = 'No source image next to this model.'
      el.tabImage.disabled = true
      setMode('model')
    }
  }

  function setMode(next) {
    if (next === mode) return
    mode = next
    const showingModel = mode === 'model'
    el.tabModel.setAttribute('aria-selected', String(showingModel))
    el.tabImage.setAttribute('aria-selected', String(!showingModel))
    el.panelModel.hidden = !showingModel
    el.panelImage.hidden = showingModel
    // The 3D viewport was display:none while hidden and comes back at whatever
    // size the window is now; the renderer has to be told or it keeps the old
    // aspect ratio and the model arrives stretched.
    if (showingModel) onLayoutChange()
  }

  /**
   * Fill a card's picture. The GLB rendered by the page is the honest one: the
   * source PNG is what FLUX drew, and a grid of those flatters a reconstruction
   * that may have lost half of it. The source is the fallback, not the default,
   * and it is all there is when no renderer was handed in.
   */
  function fillThumb(model, thumb, img) {
    const fallback = () => {
      thumb.classList.remove('thumb-pending')
      if (model.source) img.src = model.source.uri
      else thumb.classList.add('thumb-empty')
    }

    if (!onThumbnail || model.readable === false) {
      fallback()
      return
    }

    thumb.classList.add('thumb-pending')
    const run = () => Promise.resolve()
      .then(() => onThumbnail(model))
      .then((url) => {
        if (!url) return fallback()
        img.src = url
        thumb.classList.remove('thumb-pending')
        thumb.classList.add('thumb-render')
      })
      .catch(fallback)

    // Rendering every model in a folder of fifty costs fifty GLB downloads and
    // fifty frames, most of them for cards nobody scrolled to. Where the
    // browser can say what is on screen, only those get drawn.
    if (typeof IntersectionObserver !== 'function') {
      run()
      return
    }
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue
        observer.disconnect()
        run()
      }
    }, { rootMargin: '300px' })
    observer.observe(thumb)
  }

  function card(model) {
    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'card'
    button.setAttribute('role', 'option')
    button.setAttribute('aria-selected', 'false')
    button.dataset.key = keyOf(model)

    const thumb = document.createElement('span')
    thumb.className = 'thumb'
    const img = document.createElement('img')
    img.alt = ''
    // A directory of a dozen 1024x1024 sources is 20 MB of PNG; off-screen
    // cards never fetch theirs, and the server answers a revisit with a 304.
    img.setAttribute('loading', 'lazy')
    img.setAttribute('decoding', 'async')
    thumb.append(img)
    fillThumb(model, thumb, img)

    const meta = document.createElement('span')
    meta.className = 'meta'
    const name = document.createElement('span')
    name.className = 'name'
    name.textContent = model.name
    const sub = document.createElement('span')
    sub.className = 'sub'
    const parts = []
    if (model.readable === false) parts.push('unreadable')
    else if (typeof model.triangles === 'number') parts.push(`${formatShortCount(model.triangles)} tris`)
    parts.push(formatBytes(model.byteSize), formatAge(model.modifiedAt))
    sub.textContent = parts.join(' · ')
    meta.append(name, sub)

    button.append(thumb, meta)
    button.addEventListener('click', () => {
      select(button.dataset.key)
      // A click in the gallery means "show me this one", so it opens the
      // turntable. The Gallery button goes back.
      if (layout === 'gallery') setView('single')
    })
    button.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown') { event.preventDefault(); moveSelection(1) }
      else if (event.key === 'ArrowUp') { event.preventDefault(); moveSelection(-1) }
    })
    return button
  }

  function setView(next) {
    if (next === layout) return
    layout = next
    root.dataset.view = layout
    el.viewGallery.setAttribute('aria-pressed', String(layout === 'gallery'))
    el.viewSingle.setAttribute('aria-pressed', String(layout === 'single'))
    // Same reason the tabs call this: the canvas was display:none and comes
    // back at a size the renderer has not been told about.
    onLayoutChange()
  }

  function renderList() {
    const visible = filterModels(models, el.filter.value)
    el.list.innerHTML = ''
    for (const model of visible) el.list.append(card(model))
    el.count.textContent = visible.length === models.length
      ? `(${models.length})`
      : `(${visible.length}/${models.length})`

    if (!models.length) {
      el.note.hidden = false
      el.note.textContent = 'No models yet.'
    } else if (!visible.length) {
      el.note.hidden = false
      el.note.textContent = `Nothing matches "${el.filter.value.trim()}".`
    } else {
      el.note.hidden = true
      el.note.textContent = ''
    }
    markSelected()
  }

  function markSelected() {
    for (const button of el.list.querySelectorAll('.card')) {
      const on = Boolean(selected) && button.dataset.key === keyOf(selected)
      button.setAttribute('aria-selected', String(on))
      button.classList.toggle('selected', on)
    }
  }

  function rememberInUrl(model) {
    if (!history || typeof history.replaceState !== 'function') return
    const url = new URL(globalThis.location?.href ?? 'http://localhost/')
    url.searchParams.delete('model')
    url.searchParams.set('id', keyOf(model))
    history.replaceState(null, '', `${url.pathname}${url.search}`)
  }

  function select(key, { fromUser = true } = {}) {
    const model = models.find((m) => m.id === key || m.name === key)
    if (!model) return
    selected = model
    markSelected()
    el.currentName.textContent = model.name
    setStats(statsFor(model))
    showSource(model)
    showClips(model.animations || [])
    rememberInUrl(model)
    if (model.readable === false) {
      setStatus(`${model.name} is not a readable GLB`, 'error')
      return
    }
    setStatus(`Loading ${model.name}…`)
    onSelect(model, { fromUser })
  }

  function moveSelection(delta) {
    const visible = filterModels(models, el.filter.value)
    if (!visible.length) return
    const at = visible.findIndex((m) => keyOf(m) === keyOf(selected))
    const next = visible[Math.min(visible.length - 1, Math.max(0, at + delta))]
    if (!next || keyOf(next) === keyOf(selected)) return
    select(keyOf(next))
    el.list.querySelector('.card.selected')?.focus()
  }

  async function refresh({ keepSelection = true } = {}) {
    const previous = selected
    let payload
    try {
      payload = await fetchModels(fetchImpl)
    } catch (error) {
      setStatus(`Cannot reach the preview server: ${error.message}`, 'error')
      return []
    }

    models = payload.models

    if (!models.length) {
      selected = null
      renderList()
      setStats([])
      el.currentName.textContent = ''
      showSource(null)
      showClips([])
      setStatus(`No .glb files in ${payload.dir}. Generate one with the pipeline, then hit Refresh.`,
                'empty')
      return models
    }

    const params = new URLSearchParams(search)
    const requested = params.get('id') || params.get('model')
    const wanted = keepSelection && previous ? keyOf(previous) : requested
    const initial = pickInitial(models, wanted)
    renderList()

    if (wanted && !models.some((m) => m.id === wanted || m.name === wanted)) {
      selected = initial
      markSelected()
      el.currentName.textContent = initial.name
      setStats(statsFor(initial))
      showSource(initial)
      rememberInUrl(initial)
      setStatus(`No model named ${wanted}; showing ${initial.name} instead.`, 'warn')
      onSelect(initial, { fromUser: false })
      return models
    }

    select(keyOf(initial), { fromUser: false })
    return models
  }

  el.refresh.addEventListener('click', () => refresh({ keepSelection: false }))
  el.filter.addEventListener('input', () => renderList())
  el.autorotate.addEventListener('change', () =>
    onRotationChange({ enabled: el.autorotate.checked, speed: Number(el.speed.value) }))
  el.speed.addEventListener('input', () =>
    onRotationChange({ enabled: el.autorotate.checked, speed: Number(el.speed.value) }))
  el.wireframe.addEventListener('change', () => onWireframeChange(el.wireframe.checked))
  el.grid.addEventListener('change', () => onGridChange(el.grid.checked))
  el.quality.addEventListener('change', () => onQualityChange(el.quality.checked))
  el.viewGallery.addEventListener('click', () => setView('gallery'))
  el.viewSingle.addEventListener('click', () => setView('single'))
  el.reset.addEventListener('click', () => onResetView())
  el.tabModel.addEventListener('click', () => setMode('model'))
  el.tabImage.addEventListener('click', () => setMode('image'))
  el.playPause.addEventListener('click', () => setPlaying(!playing))

  return {
    elements: el,
    refresh,
    select,
    setView,
    setStatus,
    setStats,
    showClips,
    get view() {
      return layout
    },
    get clip() {
      return clip
    },
    get playing() {
      return playing
    },
    get models() {
      return models
    },
    get selected() {
      return selected
    },
    get mode() {
      return mode
    },
    get rotation() {
      return { enabled: el.autorotate.checked, speed: Number(el.speed.value) }
    },
    get quality() {
      return el.quality.checked
    },
  }
}
