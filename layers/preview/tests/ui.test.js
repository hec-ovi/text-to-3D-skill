/**
 * DOM tests for the preview controls.
 *
 * The real ui.js runs against a real DOM, driven by real user interaction, with
 * HTTP intercepted by MSW. Queries go by role and label. No WebGL here: that
 * lives in scene.js, which this module never imports.
 */

import { describe, expect, test, vi, beforeEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/dom'
import userEvent from '@testing-library/user-event'

import { mountUi, pickInitial, formatBytes, formatCount } from '../web/ui.js'
import { model, serveModels, serveError, serveNetworkFailure } from './msw-server.js'

function mount(options = {}) {
  document.body.innerHTML = '<div id="app"></div>'
  return mountUi(document.getElementById('app'), options)
}

const BELL = model('bell-r512.glb', { triangles: 140654, byteSize: 4528192 })
const HELMET = model('helmet-r512.glb', {
  triangles: 138520,
  byteSize: 5182056,
  modifiedAt: '2026-07-22T09:00:00+00:00',
})

beforeEach(() => {
  window.history.replaceState(null, '', '/')
})

describe('model list', () => {
  test('fills the dropdown from the server, newest first', async () => {
    serveModels([BELL, HELMET])
    const ui = mount()
    await ui.refresh()

    const select = screen.getByRole('combobox', { name: /model/i })
    const options = within(select).getAllByRole('option')
    expect(options.map((o) => o.value)).toEqual(['bell-r512.glb', 'helmet-r512.glb'])
    expect(options[0]).toHaveProperty('textContent', 'bell-r512.glb (140,654 tris)')
  })

  test('selects the newest model and reports its stats', async () => {
    serveModels([BELL, HELMET])
    const onSelect = vi.fn()
    const ui = mount({ onSelect })
    await ui.refresh()

    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect.mock.calls[0][0].name).toBe('bell-r512.glb')
    expect(screen.getByRole('status')).toHaveProperty('textContent', 'Loading bell-r512.glb…')
    expect(document.querySelector('#stats').textContent).toContain('140,654')
    expect(document.querySelector('#stats').textContent).toContain('4.3 MB')
  })

  test('honours ?model= when that file exists', async () => {
    serveModels([BELL, HELMET])
    const onSelect = vi.fn()
    const ui = mount({ onSelect, search: '?model=helmet-r512.glb' })
    await ui.refresh({ keepSelection: false })

    expect(onSelect.mock.calls[0][0].name).toBe('helmet-r512.glb')
    expect(screen.getByRole('combobox', { name: /model/i })).toHaveProperty(
      'value', 'helmet-r512.glb')
  })

  test('falls back to the newest model and says so when ?model= is unknown', async () => {
    serveModels([BELL, HELMET])
    const onSelect = vi.fn()
    const ui = mount({ onSelect, search: '?model=nope.glb' })
    await ui.refresh({ keepSelection: false })

    const status = screen.getByRole('status')
    expect(status.textContent).toMatch(/No model named nope\.glb/)
    expect(status.dataset.kind).toBe('warn')
    expect(onSelect.mock.calls[0][0].name).toBe('bell-r512.glb')
  })
})

describe('empty and error states', () => {
  test('an empty directory explains what to do next and disables the dropdown', async () => {
    serveModels([], '/home/hec/workspace/text-to-3D-skill/out')
    const onSelect = vi.fn()
    const ui = mount({ onSelect })
    await ui.refresh()

    const status = screen.getByRole('status')
    expect(status.textContent).toMatch(/No \.glb files in/)
    expect(status.textContent).toMatch(/pipeline/)
    expect(status.dataset.kind).toBe('empty')
    expect(screen.getByRole('combobox', { name: /model/i })).toHaveProperty('disabled', true)
    expect(onSelect).not.toHaveBeenCalled()
  })

  test('an error envelope from the server is shown, not swallowed', async () => {
    serveError(404, { contractVersion: '1.0', code: 'DIR_MISSING', message: 'no directory at /nope' })
    const ui = mount()
    await ui.refresh()

    const status = screen.getByRole('status')
    expect(status.textContent).toMatch(/no directory at \/nope/)
    expect(status.dataset.kind).toBe('error')
  })

  test('a dead server is reported instead of leaving the page on Loading', async () => {
    serveNetworkFailure()
    const ui = mount()
    await ui.refresh()

    expect(screen.getByRole('status').dataset.kind).toBe('error')
    expect(screen.getByRole('status').textContent).toMatch(/Cannot reach the preview server/)
  })

  test('a model the server could not parse is flagged and not handed to the viewer', async () => {
    serveModels([model('broken.glb', { readable: false, triangles: undefined })])
    const onSelect = vi.fn()
    const ui = mount({ onSelect })
    await ui.refresh()

    expect(screen.getByRole('status').textContent).toMatch(/not a readable GLB/)
    expect(onSelect).not.toHaveBeenCalled()
  })
})

describe('user interaction', () => {
  test('choosing another model loads it and records it in the URL', async () => {
    const user = userEvent.setup()
    serveModels([BELL, HELMET])
    const onSelect = vi.fn()
    const ui = mount({ onSelect })
    await ui.refresh()
    onSelect.mockClear()

    await user.selectOptions(screen.getByRole('combobox', { name: /model/i }), 'helmet-r512.glb')

    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect.mock.calls[0][0].name).toBe('helmet-r512.glb')
    expect(onSelect.mock.calls[0][1]).toEqual({ fromUser: true })
    expect(window.location.search).toBe('?model=helmet-r512.glb')
  })

  test('the auto rotate checkbox starts on and turns the turntable off', async () => {
    const user = userEvent.setup()
    serveModels([BELL])
    const onRotationChange = vi.fn()
    const ui = mount({ onRotationChange })
    await ui.refresh()

    const toggle = screen.getByRole('checkbox', { name: /auto rotate/i })
    expect(toggle).toHaveProperty('checked', true)

    await user.click(toggle)
    expect(onRotationChange).toHaveBeenLastCalledWith({ enabled: false, speed: 1.5 })
    expect(ui.rotation.enabled).toBe(false)

    await user.click(toggle)
    expect(onRotationChange).toHaveBeenLastCalledWith({ enabled: true, speed: 1.5 })
  })

  test('the speed slider reports a new rotation speed', async () => {
    serveModels([BELL])
    const onRotationChange = vi.fn()
    mount({ onRotationChange })

    const slider = screen.getByRole('slider', { name: /rotation speed/i })
    slider.value = '4'
    slider.dispatchEvent(new Event('input', { bubbles: true }))

    expect(onRotationChange).toHaveBeenLastCalledWith({ enabled: true, speed: 4 })
  })

  test('wireframe toggles both ways', async () => {
    const user = userEvent.setup()
    const onWireframeChange = vi.fn()
    mount({ onWireframeChange })

    const toggle = screen.getByRole('checkbox', { name: /wireframe/i })
    expect(toggle).toHaveProperty('checked', false)

    await user.click(toggle)
    expect(onWireframeChange).toHaveBeenLastCalledWith(true)
    await user.click(toggle)
    expect(onWireframeChange).toHaveBeenLastCalledWith(false)
  })

  test('reset view asks the viewer to go home', async () => {
    const user = userEvent.setup()
    const onResetView = vi.fn()
    mount({ onResetView })

    await user.click(screen.getByRole('button', { name: /reset view/i }))
    expect(onResetView).toHaveBeenCalledTimes(1)
  })

  test('refresh picks up a model generated after the page loaded', async () => {
    const user = userEvent.setup()
    serveModels([HELMET])
    const onSelect = vi.fn()
    const ui = mount({ onSelect })
    await ui.refresh()
    expect(ui.models).toHaveLength(1)

    serveModels([BELL, HELMET])
    await user.click(screen.getByRole('button', { name: /refresh/i }))

    await waitFor(() => expect(ui.models).toHaveLength(2))
    expect(screen.getByRole('combobox', { name: /model/i })).toHaveProperty(
      'value', 'bell-r512.glb')
  })
})

describe('source image', () => {
  const WITH_SOURCE = model('bell-r512.glb', {
    source: {
      name: 'bell.png',
      uri: '/images/bell.png',
      byteSize: 1181070,
      mediaType: 'image/png',
      width: 1024,
      height: 1024,
    },
  })

  test('shows the image the mesh was reconstructed from', async () => {
    serveModels([WITH_SOURCE])
    const ui = mount()
    await ui.refresh()

    const img = screen.getByRole('img', { name: /source image for bell-r512\.glb/i })
    expect(img.getAttribute('src')).toBe('/images/bell.png')
    expect(img.hidden).toBe(false)
    expect(document.querySelector('#source-note').textContent).toBe('bell.png (1024x1024, 1.1 MB)')
  })

  test('says so when a model has no source next to it', async () => {
    serveModels([HELMET])
    const ui = mount()
    await ui.refresh()

    expect(screen.queryByRole('img')).toBe(null)
    expect(document.querySelector('#source-note').textContent)
      .toMatch(/No source image next to this model/)
  })

  test('the toggle hides and shows the panel, and tells the renderer to resize', async () => {
    const user = userEvent.setup()
    serveModels([WITH_SOURCE])
    const onLayoutChange = vi.fn()
    const ui = mount({ onLayoutChange })
    await ui.refresh()

    const panel = document.querySelector('#source-panel')
    const toggle = screen.getByRole('checkbox', { name: /source image/i })
    expect(toggle).toHaveProperty('checked', true)
    expect(panel.hidden).toBe(false)

    await user.click(toggle)
    expect(panel.hidden).toBe(true)
    expect(onLayoutChange).toHaveBeenCalledTimes(1)

    await user.click(toggle)
    expect(panel.hidden).toBe(false)
    expect(onLayoutChange).toHaveBeenCalledTimes(2)
  })

  test('switching models swaps the image, and clears it when the next has none', async () => {
    const user = userEvent.setup()
    serveModels([WITH_SOURCE, HELMET])
    const ui = mount()
    await ui.refresh()
    expect(screen.getByRole('img').getAttribute('src')).toBe('/images/bell.png')

    await user.selectOptions(screen.getByRole('combobox', { name: /model/i }), 'helmet-r512.glb')

    expect(screen.queryByRole('img')).toBe(null)
    expect(document.querySelector('#source-image').hasAttribute('src')).toBe(false)
  })

  test('an empty directory leaves no stale image behind', async () => {
    serveModels([])
    const ui = mount()
    await ui.refresh()
    expect(screen.queryByRole('img')).toBe(null)
  })
})

describe('pure helpers', () => {
  test('pickInitial prefers the requested name, then the newest', () => {
    expect(pickInitial([BELL, HELMET], 'helmet-r512.glb')).toBe(HELMET)
    expect(pickInitial([BELL, HELMET], 'gone.glb')).toBe(BELL)
    expect(pickInitial([BELL, HELMET], null)).toBe(BELL)
    expect(pickInitial([], 'anything')).toBe(null)
  })

  test('formatBytes and formatCount stay readable at every scale', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(5182056)).toBe('4.9 MB')
    expect(formatBytes(-1)).toBe('?')
    expect(formatCount(140654)).toBe('140,654')
    expect(formatCount(undefined)).toBe('?')
  })
})
