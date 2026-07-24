// Wiring only: the controls from ui.js, the turntable from scene.js.

import { mountUi, formatCount } from './ui.js'
import { createViewer } from './scene.js'

const app = document.getElementById('app')

const ui = mountUi(app, {
  search: window.location.search,
  onSelect: (model) => show(model),
  onRotationChange: (rotation) => viewer.setRotation(rotation),
  onWireframeChange: (on) => viewer.setWireframe(on),
  onResetView: () => viewer.resetView(),
})

const viewer = createViewer(ui.elements.stage)
viewer.setRotation(ui.rotation)

async function show(model) {
  try {
    const { triangles } = await viewer.load(model.uri)
    // The count the renderer actually built, not the one the server predicted.
    ui.setStatus(`${model.name} · ${formatCount(triangles)} triangles`, 'ok')
  } catch (error) {
    ui.setStatus(`Could not load ${model.name}: ${error.message}`, 'error')
  }
}

// Debug handle: lets the console, and the screenshot check in the README, ask
// the turntable where it is without reading the framebuffer back.
window.__t2m = { ui, viewer }

ui.refresh({ keepSelection: false })

// Regenerating an asset while the tab is open is the normal loop, so pick up
// new files when the tab comes back to the front.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') ui.refresh()
})
