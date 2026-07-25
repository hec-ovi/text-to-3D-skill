// Wiring only: the controls from ui.js, the turntable from scene.js, the card
// art from thumbs.js.

import { mountUi, formatCount } from './ui.js'
import { createViewer } from './scene.js'
import { createThumbnailer } from './thumbs.js'

const app = document.getElementById('app')
const thumbnailer = createThumbnailer()

const ui = mountUi(app, {
  search: window.location.search,
  onSelect: (model) => show(model),
  onRotationChange: (rotation) => viewer.setRotation(rotation),
  onWireframeChange: (on) => viewer.setWireframe(on),
  onGridChange: (on) => viewer.setGrid(on),
  onQualityChange: (on) => viewer.setQuality(on),
  onResetView: () => viewer.resetView(),
  onLayoutChange: () => viewer.resize(),
  onClipChange: (name) => viewer.play(name),
  onPlayingChange: (on) => viewer.setPlaying(on),
  // Keyed on the modification time as well as the path, so regenerating an
  // asset under the same name draws a new card instead of serving the old one.
  onThumbnail: (model) => thumbnailer.get(`${model.uri}#${model.modifiedAt}`, model.uri),
})

const viewer = createViewer(ui.elements.stage, { quality: ui.quality })
viewer.setRotation(ui.rotation)

async function show(model) {
  try {
    const { triangles, clips } = await viewer.load(model.uri)
    // The clips the renderer parsed, not the ones the server listed: the file
    // is the authority on what can actually be played.
    ui.showClips(clips.map((c) => c.name), ui.clip)
    if (ui.clip) viewer.play(ui.clip)
    const motion = clips.length ? ` · ${clips.length} clip${clips.length === 1 ? '' : 's'}` : ''
    // The count the renderer actually built, not the one the server predicted.
    ui.setStatus(`${model.name} · ${formatCount(triangles)} triangles${motion}`, 'ok')
  } catch (error) {
    ui.setStatus(`Could not load ${model.name}: ${error.message}`, 'error')
  }
}

// Debug handle: lets the console, and the screenshot check in the README, ask
// the turntable where it is without reading the framebuffer back.
window.__t2m = { ui, viewer, thumbnailer }

ui.refresh({ keepSelection: false })

// Regenerating an asset while the tab is open is the normal loop, so pick up
// new files when the tab comes back to the front.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') ui.refresh()
})
