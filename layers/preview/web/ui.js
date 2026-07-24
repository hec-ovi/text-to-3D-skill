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

const LAYOUT = `
<header class="topbar">
  <span class="brand">text-to-3D<span class="dim"> preview</span></span>
  <output id="status" role="status">Loading…</output>
  <span class="spacer"></span>
  <label class="toggle"><input id="autorotate" type="checkbox" checked> Auto rotate</label>
  <input id="speed" type="range" min="0" max="6" step="0.5" value="1.5" aria-label="Rotation speed">
  <label class="toggle"><input id="wireframe" type="checkbox"> Wireframe</label>
  <button id="reset-view" type="button">Reset view</button>
</header>

<div class="body">
  <aside class="sidebar">
    <div class="sidebar-head">
      <h2>Models <span class="count" id="model-count"></span></h2>
      <button id="refresh" type="button">Refresh</button>
    </div>
    <input id="filter" type="search" placeholder="Filter by name" aria-label="Filter models">
    <div class="model-list" id="model-list" role="listbox" aria-label="Models"></div>
    <p class="sidebar-note" id="sidebar-note" hidden></p>
  </aside>

  <main class="viewer">
    <div class="tabs" role="tablist" aria-label="Preview mode">
      <button id="tab-model" type="button" role="tab" aria-selected="true">Model</button>
      <button id="tab-image" type="button" role="tab" aria-selected="false">Image</button>
    </div>

    <div class="panel" id="panel-model" role="tabpanel" aria-label="Model">
      <div class="stage" id="stage"></div>
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
    onResetView = () => {},
    onLayoutChange = () => {},
    fetchImpl = globalThis.fetch,
    search = '',
    history = globalThis.history,
  } = options

  root.innerHTML = LAYOUT
  const el = {
    status: root.querySelector('#status'),
    autorotate: root.querySelector('#autorotate'),
    speed: root.querySelector('#speed'),
    wireframe: root.querySelector('#wireframe'),
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
  }

  let models = []
  let selected = null
  let mode = 'model'

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
    entries.push(['modified', formatAge(model.modifiedAt)])
    return entries
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

  function card(model) {
    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'card'
    button.setAttribute('role', 'option')
    button.setAttribute('aria-selected', 'false')
    button.dataset.key = keyOf(model)

    const thumb = document.createElement('span')
    thumb.className = 'thumb'
    if (model.source) {
      const img = document.createElement('img')
      img.src = model.source.uri
      img.alt = ''
      // A directory of a dozen 1024x1024 sources is 20 MB of PNG; off-screen
      // cards never fetch theirs, and the server answers a revisit with a 304.
      img.setAttribute('loading', 'lazy')
      img.setAttribute('decoding', 'async')
      thumb.append(img)
    } else {
      thumb.classList.add('thumb-empty')
      thumb.textContent = 'GLB'
    }

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
    button.addEventListener('click', () => select(button.dataset.key))
    button.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown') { event.preventDefault(); moveSelection(1) }
      else if (event.key === 'ArrowUp') { event.preventDefault(); moveSelection(-1) }
    })
    return button
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
  el.reset.addEventListener('click', () => onResetView())
  el.tabModel.addEventListener('click', () => setMode('model'))
  el.tabImage.addEventListener('click', () => setMode('image'))

  return {
    elements: el,
    refresh,
    select,
    setStatus,
    setStats,
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
  }
}
