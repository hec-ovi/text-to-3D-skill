// Everything the preview does that is not WebGL: fetch the model list, build
// the controls, keep the URL in sync, report status. Kept free of any three.js
// import so it runs, and is tested, in a plain DOM.

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

// The list arrives newest first, so element 0 is the model someone most likely
// just generated. An explicit ?model= wins, but only if it is really there.
export function pickInitial(models, requested) {
  if (!models.length) return null
  if (requested) {
    const match = models.find((m) => m.name === requested)
    if (match) return match
  }
  return models[0]
}

const CONTROLS = `
<header class="bar">
  <span class="brand">text-to-3D <span class="dim">preview</span></span>
  <label class="field" for="model-select">Model</label>
  <select id="model-select" aria-label="Model"></select>
  <button id="refresh" type="button">Refresh</button>
  <span class="spacer"></span>
  <label class="toggle"><input id="autorotate" type="checkbox" checked> Auto rotate</label>
  <label class="field" for="speed">Speed</label>
  <input id="speed" type="range" min="0" max="6" step="0.5" value="1.5" aria-label="Rotation speed">
  <label class="toggle"><input id="wireframe" type="checkbox"> Wireframe</label>
  <label class="toggle"><input id="show-source" type="checkbox" checked> Source image</label>
  <button id="reset-view" type="button">Reset view</button>
</header>
<div class="stage-wrap">
  <div class="stage" id="stage"></div>
  <aside class="source" id="source-panel">
    <figure>
      <img id="source-image" alt="" hidden>
      <figcaption id="source-note">No source image next to this model.</figcaption>
    </figure>
  </aside>
</div>
<footer class="bar stats">
  <output id="status" role="status">Loading…</output>
  <span class="spacer"></span>
  <dl id="stats"></dl>
</footer>
`

/**
 * Build the controls into `root` and wire them to the callbacks.
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

  root.innerHTML = CONTROLS
  const el = {
    select: root.querySelector('#model-select'),
    refresh: root.querySelector('#refresh'),
    autorotate: root.querySelector('#autorotate'),
    speed: root.querySelector('#speed'),
    wireframe: root.querySelector('#wireframe'),
    reset: root.querySelector('#reset-view'),
    status: root.querySelector('#status'),
    stats: root.querySelector('#stats'),
    stage: root.querySelector('#stage'),
    showSource: root.querySelector('#show-source'),
    sourcePanel: root.querySelector('#source-panel'),
    sourceImage: root.querySelector('#source-image'),
    sourceNote: root.querySelector('#source-note'),
  }

  let models = []

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
    entries.push(['modified', model.modifiedAt.replace('T', ' ').replace('+00:00', 'Z')])
    return entries
  }

  // The picture the mesh was reconstructed from, side by side with it. Seeing
  // both is the only way to tell a bad reconstruction from a bad prompt.
  function showSource(model) {
    const source = model && model.source
    if (source) {
      el.sourceImage.src = source.uri
      el.sourceImage.alt = `Source image for ${model.name}`
      el.sourceImage.hidden = false
      const dims = source.width && source.height ? `${source.width}x${source.height}, ` : ''
      el.sourceNote.textContent = `${source.name} (${dims}${formatBytes(source.byteSize)})`
    } else {
      el.sourceImage.removeAttribute('src')
      el.sourceImage.alt = ''
      el.sourceImage.hidden = true
      el.sourceNote.textContent = 'No source image next to this model.'
    }
    el.sourcePanel.hidden = !el.showSource.checked
  }

  function rememberInUrl(name) {
    if (!history || typeof history.replaceState !== 'function') return
    const url = new URL(globalThis.location?.href ?? 'http://localhost/')
    url.searchParams.set('model', name)
    history.replaceState(null, '', `${url.pathname}${url.search}`)
  }

  function select(name, { fromUser = true } = {}) {
    const model = models.find((m) => m.name === name)
    if (!model) return
    el.select.value = name
    setStats(statsFor(model))
    showSource(model)
    if (model.readable === false) {
      setStatus(`${model.name} is not a readable GLB`, 'error')
      return
    }
    setStatus(`Loading ${model.name}…`)
    rememberInUrl(name)
    onSelect(model, { fromUser })
  }

  async function refresh({ keepSelection = true } = {}) {
    const previous = el.select.value
    let payload
    try {
      payload = await fetchModels(fetchImpl)
    } catch (error) {
      setStatus(`Cannot reach the preview server: ${error.message}`, 'error')
      return []
    }

    models = payload.models
    el.select.innerHTML = ''
    for (const model of models) {
      const option = document.createElement('option')
      option.value = model.name
      const tris = typeof model.triangles === 'number' ? ` (${formatCount(model.triangles)} tris)` : ''
      option.textContent = `${model.name}${tris}`
      el.select.append(option)
    }

    if (!models.length) {
      el.select.disabled = true
      setStats([])
      showSource(null)
      setStatus(`No .glb files in ${payload.dir}. Generate one with the pipeline, then hit Refresh.`,
                'empty')
      return models
    }

    el.select.disabled = false
    const requested = new URLSearchParams(search).get('model')
    const wanted = keepSelection && previous ? previous : requested
    const initial = pickInitial(models, wanted)
    if (wanted && !models.some((m) => m.name === wanted)) {
      setStatus(`No model named ${wanted}; showing ${initial.name} instead.`, 'warn')
      onSelect(initial, { fromUser: false })
      el.select.value = initial.name
      setStats(statsFor(initial))
      showSource(initial)
      rememberInUrl(initial.name)
      return models
    }
    select(initial.name, { fromUser: false })
    return models
  }

  el.select.addEventListener('change', (event) => select(event.target.value))
  el.refresh.addEventListener('click', () => refresh({ keepSelection: false }))
  el.autorotate.addEventListener('change', () =>
    onRotationChange({ enabled: el.autorotate.checked, speed: Number(el.speed.value) }))
  el.speed.addEventListener('input', () =>
    onRotationChange({ enabled: el.autorotate.checked, speed: Number(el.speed.value) }))
  el.wireframe.addEventListener('change', () => onWireframeChange(el.wireframe.checked))
  el.reset.addEventListener('click', () => onResetView())
  el.showSource.addEventListener('change', () => {
    el.sourcePanel.hidden = !el.showSource.checked
    // The 3D viewport just changed width; the renderer has to be told.
    onLayoutChange()
  })

  return {
    elements: el,
    refresh,
    select,
    setStatus,
    setStats,
    get models() {
      return models
    },
    get rotation() {
      return { enabled: el.autorotate.checked, speed: Number(el.speed.value) }
    },
  }
}
