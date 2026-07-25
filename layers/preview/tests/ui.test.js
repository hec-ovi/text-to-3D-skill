/**
 * DOM tests for the preview interface.
 *
 * The real ui.js runs against a real DOM, driven by real user interaction, with
 * HTTP intercepted by MSW. Queries go by role and label. No WebGL here: that
 * lives in scene.js, which this module never imports.
 */

import { describe, expect, test, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/dom'
import userEvent from '@testing-library/user-event'

import {
  mountUi, pickInitial, filterModels, formatBytes, formatCount, formatShortCount, formatAge,
} from '../web/ui.js'
import { model, serveModels, serveError, serveNetworkFailure } from './msw-server.js'

function mount(options = {}) {
  document.body.innerHTML = '<div id="app"></div>'
  return mountUi(document.getElementById('app'), options)
}

const cards = () => screen.getAllByRole('option')
const cardNames = () => cards().map((c) => c.querySelector('.name').textContent)
const selectedCard = () => cards().find((c) => c.getAttribute('aria-selected') === 'true')

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
  test('one card per model, in the order the server sent them', async () => {
    serveModels([BELL, HELMET])
    const ui = mount()
    await ui.refresh()

    expect(cardNames()).toEqual(['bell-r512.glb', 'helmet-r512.glb'])
    expect(cards()[0].textContent).toContain('141k tris')
    expect(cards()[0].textContent).toContain('4.3 MB')
    expect(screen.getByRole('listbox', { name: /models/i })).toBeTruthy()
    expect(document.querySelector('#model-count').textContent).toBe('(2)')
  })

  test('a card shows the source image as its thumbnail, lazily', async () => {
    serveModels([model('bell-r512.glb', {
      source: { name: 'bell.png', uri: '/images/bell.png', byteSize: 1181070 },
    })])
    const ui = mount()
    await ui.refresh()

    const thumb = cards()[0].querySelector('.thumb img')
    expect(thumb.getAttribute('src')).toBe('/images/bell.png')
    expect(thumb.getAttribute('loading')).toBe('lazy')
  })

  test('selects the newest model, marks its card and reports its stats', async () => {
    serveModels([BELL, HELMET])
    const onSelect = vi.fn()
    const ui = mount({ onSelect })
    await ui.refresh()

    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect.mock.calls[0][0].name).toBe('bell-r512.glb')
    expect(selectedCard().querySelector('.name').textContent).toBe('bell-r512.glb')
    expect(screen.getByRole('status')).toHaveProperty('textContent', 'Loading bell-r512.glb…')
    expect(document.querySelector('#stats').textContent).toContain('140,654')
    expect(document.querySelector('#current-name').textContent).toBe('bell-r512.glb')
  })

  test('honours ?id= when that model exists', async () => {
    serveModels([BELL, HELMET])
    const onSelect = vi.fn()
    const ui = mount({ onSelect, search: '?id=helmet-r512' })
    await ui.refresh({ keepSelection: false })

    expect(onSelect.mock.calls[0][0].name).toBe('helmet-r512.glb')
    expect(selectedCard().querySelector('.name').textContent).toBe('helmet-r512.glb')
  })

  test('still honours the older ?model= link', async () => {
    serveModels([BELL, HELMET])
    const onSelect = vi.fn()
    const ui = mount({ onSelect, search: '?model=helmet-r512.glb' })
    await ui.refresh({ keepSelection: false })

    expect(onSelect.mock.calls[0][0].name).toBe('helmet-r512.glb')
  })

  test('falls back to the newest model and says so when the id is unknown', async () => {
    serveModels([BELL, HELMET])
    const onSelect = vi.fn()
    const ui = mount({ onSelect, search: '?id=nope' })
    await ui.refresh({ keepSelection: false })

    const status = screen.getByRole('status')
    expect(status.textContent).toMatch(/No model named nope/)
    expect(status.dataset.kind).toBe('warn')
    expect(onSelect.mock.calls[0][0].name).toBe('bell-r512.glb')
  })
})

describe('filtering', () => {
  test('typing narrows the list without touching the selection', async () => {
    const user = userEvent.setup()
    serveModels([BELL, HELMET])
    const onSelect = vi.fn()
    const ui = mount({ onSelect })
    await ui.refresh()
    onSelect.mockClear()

    await user.type(screen.getByRole('searchbox', { name: /filter models/i }), 'helm')

    expect(cardNames()).toEqual(['helmet-r512.glb'])
    expect(document.querySelector('#model-count').textContent).toBe('(1/2)')
    expect(onSelect).not.toHaveBeenCalled()
    expect(ui.selected.name).toBe('bell-r512.glb')
  })

  test('a filter that matches nothing says so', async () => {
    const user = userEvent.setup()
    serveModels([BELL, HELMET])
    const ui = mount()
    await ui.refresh()

    await user.type(screen.getByRole('searchbox', { name: /filter models/i }), 'zzz')

    expect(screen.queryAllByRole('option')).toHaveLength(0)
    expect(document.querySelector('#sidebar-note').textContent).toMatch(/Nothing matches "zzz"/)
  })
})

describe('empty and error states', () => {
  test('an empty directory explains what to do next', async () => {
    serveModels([], '/home/hec/workspace/text-to-3D-skill/out')
    const onSelect = vi.fn()
    const ui = mount({ onSelect })
    await ui.refresh()

    const status = screen.getByRole('status')
    expect(status.textContent).toMatch(/No \.glb files in/)
    expect(status.textContent).toMatch(/pipeline/)
    expect(status.dataset.kind).toBe('empty')
    expect(screen.queryAllByRole('option')).toHaveLength(0)
    expect(document.querySelector('#sidebar-note').textContent).toBe('No models yet.')
    expect(onSelect).not.toHaveBeenCalled()
  })

  test('an error envelope from the server is shown, not swallowed', async () => {
    serveError(404, { contractVersion: '1.1', code: 'DIR_MISSING', message: 'no directory at /nope' })
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
    expect(cards()[0].textContent).toContain('unreadable')
    expect(onSelect).not.toHaveBeenCalled()
  })
})

describe('user interaction', () => {
  test('clicking a card loads that model and records its id in the URL', async () => {
    const user = userEvent.setup()
    serveModels([BELL, HELMET])
    const onSelect = vi.fn()
    const ui = mount({ onSelect })
    await ui.refresh()
    onSelect.mockClear()

    await user.click(screen.getByRole('option', { name: /helmet-r512\.glb/ }))

    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect.mock.calls[0][0].name).toBe('helmet-r512.glb')
    expect(onSelect.mock.calls[0][1]).toEqual({ fromUser: true })
    expect(window.location.search).toBe('?id=helmet-r512')
  })

  test('arrow keys walk down and back up the list', async () => {
    const user = userEvent.setup()
    serveModels([BELL, HELMET])
    const onSelect = vi.fn()
    const ui = mount({ onSelect })
    await ui.refresh()

    cards()[0].focus()
    await user.keyboard('{ArrowDown}')
    expect(ui.selected.name).toBe('helmet-r512.glb')

    await user.keyboard('{ArrowUp}')
    expect(ui.selected.name).toBe('bell-r512.glb')

    // Already at the top: staying put beats wrapping around to the oldest file.
    onSelect.mockClear()
    await user.keyboard('{ArrowUp}')
    expect(onSelect).not.toHaveBeenCalled()
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
    const ui = mount()
    await ui.refresh()
    expect(ui.models).toHaveLength(1)

    serveModels([BELL, HELMET])
    await user.click(screen.getByRole('button', { name: /refresh/i }))

    await waitFor(() => expect(ui.models).toHaveLength(2))
    expect(ui.selected.name).toBe('bell-r512.glb')
  })
})

describe('model and image tabs', () => {
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

  test('the image tab shows the picture the mesh was reconstructed from', async () => {
    const user = userEvent.setup()
    serveModels([WITH_SOURCE])
    const onLayoutChange = vi.fn()
    const ui = mount({ onLayoutChange })
    await ui.refresh()

    expect(ui.mode).toBe('model')
    await user.click(screen.getByRole('tab', { name: 'Image' }))

    expect(ui.mode).toBe('image')
    const img = screen.getByRole('img', { name: /source image for bell-r512\.glb/i })
    expect(img.getAttribute('src')).toBe('/images/bell.png')
    expect(document.querySelector('#source-note').textContent).toBe('bell.png (1024x1024, 1.1 MB)')
    expect(document.querySelector('#panel-model').hidden).toBe(true)

    // Coming back has to tell the renderer, or the canvas keeps the size it had
    // while it was hidden.
    await user.click(screen.getByRole('tab', { name: 'Model' }))
    expect(document.querySelector('#panel-model').hidden).toBe(false)
    expect(onLayoutChange).toHaveBeenCalledTimes(1)
  })

  test('a model with no source disables the image tab and says why', async () => {
    serveModels([HELMET])
    const ui = mount()
    await ui.refresh()

    expect(screen.getByRole('tab', { name: 'Image' })).toHaveProperty('disabled', true)
    expect(document.querySelector('#source-note').textContent)
      .toMatch(/No source image next to this model/)
    expect(document.querySelector('#source-image').hasAttribute('src')).toBe(false)
  })

  test('switching to a model with no source leaves the image tab behind', async () => {
    const user = userEvent.setup()
    serveModels([WITH_SOURCE, HELMET])
    const ui = mount()
    await ui.refresh()
    await user.click(screen.getByRole('tab', { name: 'Image' }))
    expect(ui.mode).toBe('image')

    await user.click(screen.getByRole('option', { name: /helmet-r512\.glb/ }))

    expect(ui.mode).toBe('model')
    expect(screen.getByRole('tab', { name: 'Image' })).toHaveProperty('disabled', true)
  })

  test('an empty directory leaves no stale image behind', async () => {
    serveModels([])
    const ui = mount()
    await ui.refresh()
    expect(document.querySelector('#source-image').hasAttribute('src')).toBe(false)
  })
})

describe('motion', () => {
  const RIGGED = model('hero-rigged.glb', {
    animations: ['idle', 'walk', 'run', 'jump'],
    joints: 19,
  })

  test('a rigged model lists its clips and starts on the first', async () => {
    serveModels([RIGGED])
    const onClipChange = vi.fn()
    const ui = mount({ onClipChange })
    await ui.refresh()

    const clips = screen.getAllByRole('radio')
    expect(clips.map((c) => c.textContent)).toEqual(['idle', 'walk', 'run', 'jump'])
    expect(clips[0].getAttribute('aria-checked')).toBe('true')
    expect(ui.clip).toBe('idle')
    expect(document.querySelector('#stats').textContent).toContain('19')
    expect(onClipChange).not.toHaveBeenCalled()
  })

  test('picking a clip asks the viewer to play it', async () => {
    const user = userEvent.setup()
    serveModels([RIGGED])
    const onClipChange = vi.fn()
    const ui = mount({ onClipChange })
    await ui.refresh()

    await user.click(screen.getByRole('radio', { name: 'run' }))

    expect(onClipChange).toHaveBeenCalledWith('run')
    expect(ui.clip).toBe('run')
    expect(screen.getByRole('radio', { name: 'run' }).getAttribute('aria-checked')).toBe('true')
    expect(screen.getByRole('radio', { name: 'idle' }).getAttribute('aria-checked')).toBe('false')
  })

  test('pause and play report to the viewer both ways', async () => {
    const user = userEvent.setup()
    serveModels([RIGGED])
    const onPlayingChange = vi.fn()
    const ui = mount({ onPlayingChange })
    await ui.refresh()

    const button = screen.getByRole('button', { name: 'Pause' })
    await user.click(button)
    expect(onPlayingChange).toHaveBeenLastCalledWith(false)
    expect(ui.playing).toBe(false)

    await user.click(screen.getByRole('button', { name: 'Play' }))
    expect(onPlayingChange).toHaveBeenLastCalledWith(true)
    expect(ui.playing).toBe(true)
  })

  test('a model with no clips hides the motion panel', async () => {
    serveModels([RIGGED, BELL])
    const user = userEvent.setup()
    const ui = mount()
    await ui.refresh()
    expect(document.querySelector('#motion').hidden).toBe(false)

    await user.click(screen.getByRole('option', { name: /bell-r512\.glb/ }))

    expect(document.querySelector('#motion').hidden).toBe(true)
    expect(screen.queryAllByRole('radio')).toHaveLength(0)
    expect(ui.clip).toBe(null)
  })

  test('the viewer overrides the list when the file disagrees', async () => {
    serveModels([RIGGED])
    const ui = mount()
    await ui.refresh()

    // What the renderer parsed out of the GLB wins: it is the thing that plays.
    ui.showClips(['idle', 'walk'], 'walk')

    expect(screen.getAllByRole('radio').map((c) => c.textContent)).toEqual(['idle', 'walk'])
    expect(ui.clip).toBe('walk')
  })
})

describe('render controls', () => {
  test('the grid checkbox reports to the viewer both ways', async () => {
    const user = userEvent.setup()
    const onGridChange = vi.fn()
    mount({ onGridChange })

    const toggle = screen.getByRole('checkbox', { name: /grid/i })
    expect(toggle).toHaveProperty('checked', false)

    await user.click(toggle)
    expect(onGridChange).toHaveBeenLastCalledWith(true)
    await user.click(toggle)
    expect(onGridChange).toHaveBeenLastCalledWith(false)
  })

  test('quality starts on, so the page opens on the good render', async () => {
    const user = userEvent.setup()
    const onQualityChange = vi.fn()
    const ui = mount({ onQualityChange })

    const toggle = screen.getByRole('checkbox', { name: /quality/i })
    expect(toggle).toHaveProperty('checked', true)
    expect(ui.quality).toBe(true)

    await user.click(toggle)
    expect(onQualityChange).toHaveBeenLastCalledWith(false)
    expect(ui.quality).toBe(false)
  })
})

describe('gallery', () => {
  test('the page opens on the turntable and the toggle swaps the layout', async () => {
    const user = userEvent.setup()
    serveModels([BELL, HELMET])
    const onLayoutChange = vi.fn()
    const ui = mount({ onLayoutChange })
    await ui.refresh()

    const app = document.getElementById('app')
    expect(app.dataset.view).toBe('single')
    expect(screen.getByRole('button', { name: 'Single' }).getAttribute('aria-pressed')).toBe('true')

    await user.click(screen.getByRole('button', { name: 'Gallery' }))
    expect(app.dataset.view).toBe('gallery')
    expect(ui.view).toBe('gallery')
    expect(screen.getByRole('button', { name: 'Gallery' }).getAttribute('aria-pressed')).toBe('true')
    // The canvas was display:none and comes back at a new size.
    expect(onLayoutChange).toHaveBeenCalled()

    // One list, two layouts: the cards are the same elements either way.
    expect(cardNames()).toEqual(['bell-r512.glb', 'helmet-r512.glb'])
  })

  test('clicking a card in the gallery opens it on the turntable', async () => {
    const user = userEvent.setup()
    serveModels([BELL, HELMET])
    const onSelect = vi.fn()
    const ui = mount({ onSelect })
    await ui.refresh()
    await user.click(screen.getByRole('button', { name: 'Gallery' }))

    await user.click(screen.getByRole('option', { name: /helmet-r512\.glb/ }))

    expect(ui.view).toBe('single')
    expect(ui.selected.name).toBe('helmet-r512.glb')
    expect(onSelect).toHaveBeenLastCalledWith(
      expect.objectContaining({ name: 'helmet-r512.glb' }), { fromUser: true })
  })

  test('a card shows the GLB rendered, not the picture it was made from', async () => {
    const withSource = model('bell-r512.glb', {
      source: { name: 'bell.png', uri: '/images/bell.png', byteSize: 1181070 },
    })
    serveModels([withSource])
    const onThumbnail = vi.fn(async () => 'data:image/webp;base64,RENDERED')
    const ui = mount({ onThumbnail })
    await ui.refresh()

    const thumb = cards()[0].querySelector('.thumb')
    await waitFor(() =>
      expect(thumb.querySelector('img').getAttribute('src')).toBe('data:image/webp;base64,RENDERED'))
    expect(onThumbnail).toHaveBeenCalledWith(expect.objectContaining({ name: 'bell-r512.glb' }))
    expect(thumb.classList.contains('thumb-render')).toBe(true)
    expect(thumb.classList.contains('thumb-pending')).toBe(false)
  })

  test('a thumbnail that fails to render falls back to the source image', async () => {
    const withSource = model('bell-r512.glb', {
      source: { name: 'bell.png', uri: '/images/bell.png', byteSize: 1181070 },
    })
    serveModels([withSource])
    const ui = mount({ onThumbnail: async () => { throw new Error('context lost') } })
    await ui.refresh()

    const thumb = cards()[0].querySelector('.thumb')
    await waitFor(() =>
      expect(thumb.querySelector('img').getAttribute('src')).toBe('/images/bell.png'))
    expect(thumb.classList.contains('thumb-pending')).toBe(false)
  })

  test('an unreadable GLB is never handed to the renderer for a thumbnail', async () => {
    serveModels([model('broken.glb', { readable: false, triangles: undefined })])
    const onThumbnail = vi.fn(async () => 'data:image/webp;base64,RENDERED')
    const ui = mount({ onThumbnail })
    await ui.refresh()

    expect(onThumbnail).not.toHaveBeenCalled()
    expect(cards()[0].querySelector('.thumb').classList.contains('thumb-empty')).toBe(true)
  })
})

describe('pure helpers', () => {
  test('pickInitial prefers the requested id or name, then the newest', () => {
    expect(pickInitial([BELL, HELMET], 'helmet-r512')).toBe(HELMET)
    expect(pickInitial([BELL, HELMET], 'helmet-r512.glb')).toBe(HELMET)
    expect(pickInitial([BELL, HELMET], 'gone')).toBe(BELL)
    expect(pickInitial([BELL, HELMET], null)).toBe(BELL)
    expect(pickInitial([], 'anything')).toBe(null)
  })

  test('filterModels matches any part of the name, case-insensitively', () => {
    expect(filterModels([BELL, HELMET], 'HELM')).toEqual([HELMET])
    expect(filterModels([BELL, HELMET], 'r512')).toEqual([BELL, HELMET])
    expect(filterModels([BELL, HELMET], '  ')).toEqual([BELL, HELMET])
  })

  test('the number formats stay readable at every scale', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(5182056)).toBe('4.9 MB')
    expect(formatBytes(-1)).toBe('?')
    expect(formatCount(140654)).toBe('140,654')
    expect(formatCount(undefined)).toBe('?')
    expect(formatShortCount(842)).toBe('842')
    expect(formatShortCount(3810)).toBe('3.8k')
    expect(formatShortCount(147330)).toBe('147k')
    expect(formatShortCount(1400000)).toBe('1.4M')
  })

  test('formatAge counts back from now', () => {
    const now = Date.parse('2026-07-24T12:00:00Z')
    expect(formatAge('2026-07-24T11:59:30Z', now)).toBe('just now')
    expect(formatAge('2026-07-24T11:40:00Z', now)).toBe('20 min ago')
    expect(formatAge('2026-07-24T09:00:00Z', now)).toBe('3 h ago')
    expect(formatAge('2026-07-23T11:00:00Z', now)).toBe('yesterday')
    expect(formatAge('2026-07-18T12:00:00Z', now)).toBe('6 days ago')
    expect(formatAge('not a date', now)).toBe('?')
  })
})
