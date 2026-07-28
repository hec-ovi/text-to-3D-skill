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

// What a file name says about the asset besides its subject. Order matters:
// these are shown as written, so `r1024` before `r512` is only cosmetic but
// `lowpoly` before `poly` would not be.
const VARIANT = /^(r\d+|rigged|lowpoly\w*|polyhaven|base)$/
// A content-addressed segment. Eight hex characters or more is a digest, not a
// word: the shortest English word that is also hex is four letters.
const DIGEST = /^[0-9a-f]{8,}$/

function parts(name) {
  return name.replace(/\.(glb|gltf|png|jpe?g|webp)$/i, '').split(/[-_]/).filter(Boolean)
}

/**
 * The name to put on a card.
 *
 * Files are named `<subject>-<digest>-r512.glb`, and only the subject is worth
 * reading. Before the subject was in there at all this returned things like
 * "Cd3cfe84c0486665", which is why it exists.
 */
export function titleOf(name) {
  if (!name) return ''
  const words = parts(name).filter((p) => !DIGEST.test(p) && !VARIANT.test(p))
  // An older asset is all digest and variant. Its file name is all there is.
  if (!words.length) return parts(name).join(' ')
  return words.map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')
}

/**
 * The variant tags in a file name, in the order they appear.
 *
 * Without these two files reduce to the same title: a rigged asset and the mesh
 * it was rigged from are both "Viking Warrior", and a gallery showing that
 * twice is worse than showing the raw file name.
 */
export function tagsOf(name) {
  return name ? parts(name).filter((p) => VARIANT.test(p)) : []
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

// Drawn here rather than downloaded: the page has to work with no network, and
// an icon font would be a second request and a second thing to keep in sync.
// Every one is decorative, so every one is aria-hidden and the label beside it
// is what a screen reader reads.
const ICON = {
  cube: `<svg class="i" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M8 1.8 13.6 5v6L8 14.2 2.4 11V5z"/><path d="M2.4 5 8 8.2 13.6 5M8 8.2v6"/></svg>`,
  search: `<svg class="i" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><circle cx="7.1" cy="7.1" r="4.1"/><path d="m10.2 10.2 3.2 3.2"/></svg>`,
  refresh: `<svg class="i" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M13.1 8a5.1 5.1 0 1 1-1.5-3.6"/><path d="M13.4 2.7v3h-3"/></svg>`,
  grid: `<svg class="i" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><rect x="2.4" y="2.4" width="4.9" height="4.9"/><rect x="8.7" y="2.4" width="4.9" height="4.9"/><rect x="2.4" y="8.7" width="4.9" height="4.9"/><rect x="8.7" y="8.7" width="4.9" height="4.9"/></svg>`,
  frame: `<svg class="i" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><rect x="2.3" y="2.9" width="11.4" height="10.2"/><circle cx="8" cy="8" r="2.5"/></svg>`,
  recenter: `<svg class="i" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M2.7 5.9V2.7h3.2M13.3 5.9V2.7h-3.2M2.7 10.1v3.2h3.2M13.3 10.1v3.2h-3.2"/><circle cx="8" cy="8" r="2.1"/></svg>`,
  play: `<svg class="i i-play" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M5.6 3.6 12 8l-6.4 4.4z" fill="currentColor" stroke="none"/></svg>`,
  // A figure standing with its arms out: the A-pose the character framing asks
  // for, which is also what the clips play on.
  character: `<svg class="i" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><circle cx="8" cy="3.1" r="1.7"/><path d="M8 4.8v5M8 6.4 4.6 8.6M8 6.4l3.4 2.2M8 9.8l-2.2 3.4M8 9.8l2.2 3.4"/></svg>`,
  sliders: `<svg class="i" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M2.6 4.6h10.8M2.6 11.4h10.8"/><circle cx="6" cy="4.6" r="1.6"/><circle cx="10.4" cy="11.4" r="1.6"/></svg>`,
  info: `<svg class="i" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><circle cx="8" cy="8" r="5.7"/><path d="M8 7.2v4"/><circle cx="8" cy="5.1" r="0.5" fill="currentColor" stroke="none"/></svg>`,
  trash: `<svg class="i" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M3.2 4.6h9.6M6.4 4.6V3.2h3.2v1.4M4.5 4.6l.6 8.2h5.8l.6-8.2M6.7 6.9v3.6M9.3 6.9v3.6"/></svg>`,
  pause: `<svg class="i i-pause" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><rect x="4.7" y="3.7" width="2.3" height="8.6" fill="currentColor" stroke="none"/><rect x="9" y="3.7" width="2.3" height="8.6" fill="currentColor" stroke="none"/></svg>`,
}

// One shell, three regions that never move: a bar of global actions, the model
// list, and a status line. Only the middle pane swaps between the contact sheet
// and the turntable, so the list is on screen whatever you are doing and the
// two layouts read as one page rather than two.
//
// Three scopes, three places, and each one owns a strip of the window.
//
//   the page      top bar: which layout, and what you are filtering for
//   the model     sidebar: every model, and nothing else
//   the asset     viewer: its name above the render, its controls below it
//
// The top bar used to hold all three at once, which is why it read as a row of
// unrelated widgets. The rule that sorts them: the sidebar answers "which
// asset", so anything that acts on the one already loaded belongs in the
// viewer's own footer, under the render it changes. That footer is a strip, not
// a fixed set of controls: an asset with animation puts its clips there too,
// beside the render controls, because clips are a fact about the loaded file
// and not about the list.
//
// The list is the only `listbox` on the page. Sheet tiles are plain buttons
// carrying `aria-current`, because a second listbox over the same models would
// be a second selection for a screen reader to reconcile with the first.
const LAYOUT = `
<header class="topbar">
  <button id="brand" class="brand" type="button" title="Back to the gallery">
    ${ICON.cube}<span class="brand-name">3D</span> <span class="brand-sub">SKILL</span>
  </button>

  <div class="seg" role="group" aria-label="Layout">
    <button id="view-gallery" type="button" aria-pressed="true"
            aria-label="Gallery" title="Gallery">${ICON.grid}</button>
    <button id="view-single" type="button" aria-pressed="false"
            aria-label="Single" title="Single">${ICON.frame}</button>
  </div>

  <span class="spacer"></span>

  <div class="field">
    ${ICON.search}
    <input id="filter" type="search" placeholder="Filter models" aria-label="Filter models">
  </div>
  <button id="refresh" class="btn" type="button">${ICON.refresh}Refresh</button>
</header>

<div class="body">
  <aside class="sidebar">
    <div class="pane-head">
      <h2>Models</h2>
      <span class="count" id="model-count"></span>
    </div>
    <p class="note" id="sidebar-note" hidden></p>
    <div class="model-list" id="model-list" role="listbox" aria-label="Models"></div>
  </aside>

  <main class="main">
    <section class="sheet" aria-label="Model gallery">
      <div class="sheet-grid" id="sheet-grid"></div>
      <p class="note sheet-note" id="sheet-note" hidden></p>
    </section>

    <section class="viewer">
      <div class="viewer-bar">
        <div class="tabs" role="tablist" aria-label="Preview mode">
          <button id="tab-model" type="button" role="tab" aria-selected="true">Model</button>
          <button id="tab-image" type="button" role="tab" aria-selected="false">Image</button>
        </div>
        <span class="current" id="current-name"></span>
      </div>

      <div class="panel" id="panel-model" role="tabpanel" aria-label="Model">
        <div class="stage" id="stage"></div>
      </div>

      <div class="panel image-panel" id="panel-image" role="tabpanel" aria-label="Source image" hidden>
        <img id="source-image" alt="" hidden>
        <p class="note" id="source-note">No source image next to this model.</p>
      </div>

      <div class="viewer-foot">
        <div class="toolbar" id="render-controls" role="group" aria-label="Render controls">
          <span class="foot-cap" aria-hidden="true">${ICON.sliders}View</span>
          <label class="toggle"><input id="autorotate" type="checkbox" checked><span>Auto rotate</span></label>
          <span class="slider">
            <span class="cap">Speed</span>
            <input id="speed" type="range" min="0" max="6" step="0.2" value="0.6" aria-label="Rotation speed">
          </span>
          <span class="rule"></span>
          <label class="toggle"><input id="wireframe" type="checkbox"><span>Wireframe</span></label>
          <label class="toggle"><input id="grid" type="checkbox"><span>Grid</span></label>
          <label class="toggle"><input id="quality" type="checkbox" checked><span>Quality</span></label>
          <button id="reset-view" class="btn" type="button">${ICON.recenter}Reset view</button>
          <span class="spacer"></span>
          <button id="remove" class="btn btn-danger" type="button">${ICON.trash}Remove</button>
          <span class="confirm" id="remove-confirm" hidden>
            <span class="confirm-text">Delete this model and its files?</span>
            <button id="remove-yes" class="btn btn-danger" type="button">Delete</button>
            <button id="remove-no" class="btn" type="button">Cancel</button>
          </span>
        </div>

        <section class="motion" id="motion" hidden>
          <span class="foot-cap" aria-hidden="true">${ICON.character}Motion</span>
          <button id="play-pause" class="btn" type="button" aria-pressed="true">
            ${ICON.pause}${ICON.play}<span class="play-label">Pause</span>
          </button>
          <div class="clip-list" id="clip-list" role="radiogroup" aria-label="Clips"></div>
        </section>
      </div>
    </section>
  </main>
</div>

<footer class="statusbar">
  <button id="about" class="btn btn-quiet" type="button"
          aria-expanded="false" aria-controls="stats">
    ${ICON.info}About this model
  </button>
  <dl id="stats" hidden></dl>
  <span class="spacer"></span>
  <output id="status" role="status">Loading…</output>
</footer>
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
    // The page opens on the contact sheet: the usual question after a batch is
    // "what came out", not "show me this one file".
    view = 'gallery',
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
    brand: root.querySelector('#brand'),
    viewGallery: root.querySelector('#view-gallery'),
    viewSingle: root.querySelector('#view-single'),
    reset: root.querySelector('#reset-view'),
    refresh: root.querySelector('#refresh'),
    filter: root.querySelector('#filter'),
    list: root.querySelector('#model-list'),
    note: root.querySelector('#sidebar-note'),
    sheet: root.querySelector('#sheet-grid'),
    sheetNote: root.querySelector('#sheet-note'),
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
    about: root.querySelector('#about'),
    remove: root.querySelector('#remove'),
    removeConfirm: root.querySelector('#remove-confirm'),
    removeYes: root.querySelector('#remove-yes'),
    removeNo: root.querySelector('#remove-no'),
    motion: root.querySelector('#motion'),
    clipList: root.querySelector('#clip-list'),
    playPause: root.querySelector('#play-pause'),
    playLabel: root.querySelector('#play-pause .play-label'),
  }

  let models = []
  let selected = null
  let mode = 'model'
  let layout = view
  let clip = null
  let playing = true
  root.dataset.view = layout
  root.dataset.mode = mode
  root.dataset.loaded = 'false'
  el.viewGallery.setAttribute('aria-pressed', String(layout === 'gallery'))
  el.viewSingle.setAttribute('aria-pressed', String(layout === 'single'))

  /**
   * Whether there is a render for the footer controls to control.
   *
   * The stylesheet keys off this, plus the view and the tab: auto rotate on the
   * contact sheet, or over the source PNG, or with an unreadable file selected,
   * is a control wired to nothing. Disabling them would leave five dead widgets
   * on screen saying the page is broken; they are simply not there until they
   * do something. The attribute is the assertable part, because jsdom loads no
   * stylesheet and so cannot see the rule that acts on it.
   */
  function setLoaded(model) {
    root.dataset.loaded = String(Boolean(model) && model.readable !== false)
  }

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

  /**
   * Everything about the loaded file, for the About panel.
   *
   * This is where the file name, the resolution tag and the timestamp went.
   * They are real and occasionally needed; they were just never the thing
   * being asked while scanning a grid of models, and they crowded out the
   * three flags that were.
   */
  function statsFor(model) {
    const entries = [['file', model.name], ['id', keyOf(model)],
                     ['size', formatBytes(model.byteSize)]]
    if (typeof model.triangles === 'number') entries.push(['triangles', formatCount(model.triangles)])
    if (typeof model.materials === 'number') entries.push(['materials', formatCount(model.materials)])
    if (typeof model.joints === 'number') entries.push(['joints', formatCount(model.joints)])
    if (model.rigged) entries.push(['skeleton', model.humanoid ? 'humanoid' : 'other'])
    const tags = tagsOf(model.name)
    if (tags.length) entries.push(['variant', tags.join(' · ')])
    if (model.supersedes) entries.push(['replaces', model.supersedes.join(', ')])
    if (model.source) entries.push(['from', model.source.name])
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
      // Nothing but the clip name: this is the element a screen reader reads
      // out, and an icon or a duration in here would be read with it.
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
    // Only the label, so the two glyphs survive; CSS shows whichever one the
    // pressed state calls for.
    el.playLabel.textContent = on ? 'Pause' : 'Play'
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
    root.dataset.mode = mode
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

  /** The picture element both the list rows and the sheet tiles hang off. */
  function thumbFor(model) {
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
    return thumb
  }

  /**
   * The flags under a name.
   *
   * This line used to read `r1024 · rigged · 11.8k tris · 1.7 MB · 3 days ago`,
   * which is five facts in the order they were easiest to produce and answers
   * no question anybody has while looking at a grid of characters. The
   * questions are: how heavy is it, is it a person, can it move. So it is three
   * flags, each one a yes or a number, and everything else moved to About.
   */
  function flagsFor(model) {
    if (model.readable === false) return [{ label: 'unreadable', kind: 'bad' }]
    const flags = [{ label: formatBytes(model.byteSize), kind: 'size' }]
    // What it is, then what it can do. A prop stops at the first: saying
    // "not rigged, not animated" about a treasure chest is two answers to
    // questions nobody asked about a treasure chest.
    flags.push(model.humanoid
      ? { label: 'Humanoid', kind: 'humanoid' }
      : { label: 'Model', kind: 'model' })
    if (model.rigged) flags.push({ label: 'Rigged', kind: 'rigged' })
    if (model.animations && model.animations.length) {
      flags.push({ label: 'Animated', kind: 'animated' })
    }
    return flags
  }

  function meta(model) {
    const wrap = document.createElement('span')
    wrap.className = 'meta'
    const name = document.createElement('span')
    name.className = 'name'
    name.textContent = titleOf(model.name)
    const sub = document.createElement('span')
    sub.className = 'flags'
    for (const flag of flagsFor(model)) {
      const chip = document.createElement('span')
      chip.className = 'flag'
      chip.dataset.kind = flag.kind
      chip.textContent = flag.label
      sub.append(chip)
    }
    wrap.append(name, sub)
    return wrap
  }

  /** A row in the sidebar list. This is the page's one selectable set. */
  function card(model) {
    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'card'
    button.setAttribute('role', 'option')
    button.setAttribute('aria-selected', 'false')
    button.dataset.key = keyOf(model)
    // The card shows the subject; the file name is one hover away and is still
    // spelled out in full in the status bar under the turntable.
    button.title = model.name

    button.append(thumbFor(model), meta(model))
    button.addEventListener('click', () => open(button.dataset.key))
    button.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown') { event.preventDefault(); moveSelection(1) }
      else if (event.key === 'ArrowUp') { event.preventDefault(); moveSelection(-1) }
    })
    return button
  }

  /** A tile in the contact sheet. Not an option: the sidebar owns selection. */
  function tile(model) {
    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'tile'
    button.dataset.key = keyOf(model)
    button.title = model.name
    button.setAttribute('aria-current', 'false')
    button.append(thumbFor(model), meta(model))
    button.addEventListener('click', () => open(button.dataset.key))
    return button
  }

  /** Picking a model anywhere means "show me this one", so it opens the turntable. */
  function open(key) {
    select(key)
    setView('single')
  }

  function setView(next) {
    if (next === layout) return
    layout = next
    root.dataset.view = layout
    el.viewGallery.setAttribute('aria-pressed', String(layout === 'gallery'))
    el.viewSingle.setAttribute('aria-pressed', String(layout === 'single'))
    renderSheet()
    // Same reason the tabs call this: the canvas was display:none and comes
    // back at a size the renderer has not been told about.
    onLayoutChange()
  }

  function visibleModels() {
    return filterModels(models, el.filter.value)
  }

  function renderList() {
    const visible = visibleModels()
    el.list.innerHTML = ''
    for (const model of visible) el.list.append(card(model))
    el.count.textContent = visible.length === models.length
      ? `(${models.length})`
      : `(${visible.length}/${models.length})`

    const message = !models.length
      ? 'No models yet.'
      : (!visible.length ? `Nothing matches "${el.filter.value.trim()}".` : '')
    for (const note of [el.note, el.sheetNote]) {
      note.hidden = !message
      note.textContent = message
    }
    renderSheet()
    markSelected()
  }

  // Tiles are built only for the layout that shows them. In single view the
  // sheet is empty rather than hidden, so a hidden grid cannot go stale and
  // cannot hold a second copy of every thumbnail image alive.
  function renderSheet() {
    el.sheet.innerHTML = ''
    if (layout !== 'gallery') return
    for (const model of visibleModels()) el.sheet.append(tile(model))
    markSelected()
  }

  function markSelected() {
    const key = keyOf(selected)
    for (const button of el.list.querySelectorAll('.card')) {
      const on = Boolean(selected) && button.dataset.key === key
      button.setAttribute('aria-selected', String(on))
      button.classList.toggle('selected', on)
    }
    // A tile says which model is loaded to a screen reader and to the URL, and
    // says nothing about it visually: see the note on `.tile` in style.css.
    for (const button of el.sheet.querySelectorAll('.tile')) {
      button.setAttribute('aria-current', String(Boolean(selected) && button.dataset.key === key))
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
    setLoaded(model)
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
    const visible = visibleModels()
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
      setLoaded(null)
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
      setLoaded(initial)
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
  // The wordmark works the way a logo does everywhere else: it goes home, and
  // home is the sheet of everything that has been generated.
  el.brand.addEventListener('click', () => setView('gallery'))
  el.viewGallery.addEventListener('click', () => setView('gallery'))
  el.viewSingle.addEventListener('click', () => setView('single'))
  el.reset.addEventListener('click', () => onResetView())
  el.tabModel.addEventListener('click', () => setMode('model'))
  el.tabImage.addEventListener('click', () => setMode('image'))
  el.playPause.addEventListener('click', () => setPlaying(!playing))
  // Folded away by default. The file name and the timestamp are real and
  // occasionally needed; they were never what was being asked while looking at
  // the model itself.
  el.about.addEventListener('click', () => {
    const open = el.about.getAttribute('aria-expanded') === 'true'
    el.about.setAttribute('aria-expanded', String(!open))
    el.stats.hidden = open
  })

  // Two steps, and the second one is not in the same place as the first, so a
  // double click on Remove cannot delete anything. Deletion is the one action
  // here that a person cannot undo from the page.
  function askToRemove(on) {
    el.removeConfirm.hidden = !on
    el.remove.hidden = on
    if (on) el.removeNo.focus()
  }

  el.remove.addEventListener('click', () => askToRemove(true))
  el.removeNo.addEventListener('click', () => {
    askToRemove(false)
    el.remove.focus()
  })
  el.removeYes.addEventListener('click', async () => {
    const model = selected
    if (!model) return
    askToRemove(false)
    setStatus(`Removing ${model.name}…`)
    try {
      const response = await fetchImpl(`/api/models/${encodeURIComponent(keyOf(model))}`,
                                       { method: 'DELETE' })
      const body = await response.json().catch(() => null)
      if (!response.ok) {
        throw new Error(body && body.message ? body.message : `HTTP ${response.status}`)
      }
      const files = (body && body.removed) || []
      await refresh({ keepSelection: false })
      setStatus(`Removed ${model.name}${files.length > 1 ? ` and ${files.length - 1} more file${files.length > 2 ? 's' : ''}` : ''}.`, 'ok')
    } catch (error) {
      setStatus(`Could not remove ${model.name}: ${error.message}`, 'error')
    }
  })

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
