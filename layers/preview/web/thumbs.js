// Card art for the gallery: each GLB rendered once, off to the side, into a
// data URL the card shows as an image.
//
// The alternative was a live canvas per card. Twenty live turntables is twenty
// WebGL contexts, and the browser starts dropping the oldest at sixteen. One
// hidden renderer that draws each model once costs one context and one frame
// per model, and the card keeps showing its picture while the tab is busy.

import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js'
import { GTAOPass } from 'three/addons/postprocessing/GTAOPass.js'
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js'
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js'

import { buildStudio, frameObject, placeCamera, upgradeTextures, TONE_MAPPING, EXPOSURE } from './studio.js'

const SIZE = 512

export function createThumbnailer({ size = SIZE } = {}) {
  const renderer = new THREE.WebGLRenderer({
    antialias: true,
    alpha: false,
    // toDataURL reads the drawing buffer, which the browser is free to clear
    // the moment the frame is presented unless this is asked for.
    preserveDrawingBuffer: true,
  })
  renderer.setPixelRatio(1)
  renderer.setSize(size, size, false)
  renderer.toneMapping = TONE_MAPPING
  renderer.toneMappingExposure = EXPOSURE
  renderer.shadowMap.enabled = true
  // The same filter the turntable uses. A card drawn with a harder shadow than
  // the viewer gives is the mismatch this module exists to avoid.
  renderer.shadowMap.type = THREE.PCFSoftShadowMap

  const scene = new THREE.Scene()
  const studio = buildStudio(scene, renderer)
  const camera = new THREE.PerspectiveCamera(35, 1, 0.05, 80)

  const composer = new EffectComposer(
    renderer,
    new THREE.WebGLRenderTarget(size, size, { type: THREE.HalfFloatType, samples: 4 }),
  )
  composer.setSize(size, size)
  composer.addPass(new RenderPass(scene, camera))
  const gtao = new GTAOPass(scene, camera, size, size)
  gtao.output = GTAOPass.OUTPUT.Default
  gtao.blendIntensity = 0.9
  gtao.updateGtaoMaterial({ radius: 0.12, distanceExponent: 1.0, thickness: 0.4, scale: 1.0, samples: 16 })
  composer.addPass(gtao)
  composer.addPass(new UnrealBloomPass(new THREE.Vector2(size, size), 0.12, 0.45, 1.25))
  composer.addPass(new OutputPass())

  const loader = new GLTFLoader()
  const cache = new Map()
  const pending = new Map()
  let queue = Promise.resolve()
  let disposed = false

  function clear(root) {
    scene.remove(root)
    root.traverse((node) => {
      if (!node.isMesh) return
      node.geometry?.dispose()
      for (const material of [].concat(node.material || [])) {
        for (const key of Object.keys(material)) {
          const value = material[key]
          if (value && value.isTexture) value.dispose()
        }
        material.dispose()
      }
    })
  }

  async function draw(uri) {
    const gltf = await loader.loadAsync(uri)
    const root = gltf.scene
    root.traverse((node) => {
      if (node.isMesh) { node.castShadow = true; node.receiveShadow = true }
      if (node.isSkinnedMesh) node.frustumCulled = false
    })
    upgradeTextures(root, renderer)
    scene.add(root)

    const framed = frameObject(root)
    camera.aspect = 1
    camera.updateProjectionMatrix()
    // Pulled back a little further than the turntable's opening shot: a card is
    // read at 300px, and a tight crop there loses the silhouette, which is the
    // only thing legible at that size.
    placeCamera(camera, framed, 1.12)
    camera.lookAt(framed.target)
    studio.fitShadow(framed.radius)

    // A rigged GLB is stored in its bind pose and its first clip has not been
    // applied, so the card shows exactly the A-pose the mesh was built in.
    // That is the honest picture of the asset on disk.
    composer.render()
    const url = renderer.domElement.toDataURL('image/webp', 0.9)
    clear(root)
    return url
  }

  /**
   * The data URL for one model, rendered on first ask and remembered after.
   * Renders are serialised: one scene is reused, and two at once would draw
   * both models into the same frame.
   */
  function get(key, uri) {
    if (cache.has(key)) return Promise.resolve(cache.get(key))
    if (pending.has(key)) return pending.get(key)

    const job = queue
      .then(() => (disposed ? null : draw(uri)))
      .then((url) => {
        if (url) cache.set(key, url)
        pending.delete(key)
        return url
      })
      .catch((error) => {
        pending.delete(key)
        // A card with no picture is a card; a card that throws takes the list
        // with it. The name and the numbers are still worth showing.
        console.warn(`thumbnail failed for ${uri}:`, error.message)
        return null
      })

    pending.set(key, job)
    queue = job
    return job
  }

  return {
    get,
    has: (key) => cache.has(key),
    /** Drop everything, so a Refresh re-renders a model that was regenerated. */
    invalidate() {
      cache.clear()
    },
    dispose() {
      disposed = true
      cache.clear()
      studio.dispose()
      composer.dispose()
      renderer.dispose()
    },
  }
}
