// The WebGL half: one turntable that loads a GLB, frames it, and spins it.
// Nothing in here touches the page's controls; it exposes a small API that
// ui.js drives.
//
// The look lives in studio.js, shared with the gallery thumbnails. What is here
// is the render graph and the model's lifecycle.

import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js'
import { GTAOPass } from 'three/addons/postprocessing/GTAOPass.js'
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js'
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js'

import { buildStudio, frameObject, placeCamera, upgradeTextures, TONE_MAPPING, EXPOSURE } from './studio.js'

export function createViewer(container, { quality = true } = {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
  renderer.setPixelRatio(Math.min(globalThis.devicePixelRatio || 1, 2))
  renderer.toneMapping = TONE_MAPPING
  renderer.toneMappingExposure = EXPOSURE
  // A model floating with no contact point reads as a render, not an object.
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFShadowMap
  container.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  const studio = buildStudio(scene, renderer)

  // near/far are tight on purpose. The ambient-occlusion pass reads the depth
  // buffer, and a 0.01-to-100 range spends its precision on empty space, which
  // shows up as AO crawling over flat surfaces as the camera moves.
  const camera = new THREE.PerspectiveCamera(35, 1, 0.05, 80)
  camera.position.set(1.6, 1.1, 1.9)

  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.06
  controls.autoRotate = true
  controls.autoRotateSpeed = 1.5
  controls.minDistance = 0.2
  controls.maxDistance = 20
  // Orbiting under the floor shows the model from below through the shadow
  // plane, which is never what anyone wants from a turntable.
  controls.maxPolarAngle = Math.PI * 0.495

  const grid = new THREE.GridHelper(4, 20, 0x343a46, 0x20232a)
  grid.material.transparent = true
  grid.material.opacity = 0.35
  grid.visible = false
  grid.position.y = 0.001
  scene.add(grid)

  // Multisampling has to be asked for explicitly once the composer owns the
  // frame: `antialias: true` only applies to the default framebuffer, and
  // dropping it turns every silhouette into a staircase.
  const composer = new EffectComposer(
    renderer,
    new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: 4 }),
  )
  composer.addPass(new RenderPass(scene, camera))

  // The single biggest readability win on a reconstructed mesh. TRELLIS bakes
  // no occlusion into the atlas, so every crevice, eye socket and panel gap is
  // lit exactly as brightly as the surface beside it and the form goes flat.
  const gtao = new GTAOPass(scene, camera, 1, 1)
  gtao.output = GTAOPass.OUTPUT.Default
  gtao.blendIntensity = 0.9
  // Radius is in world units and the model is framed into a 1.4-unit box, so
  // 0.12 is roughly a finger's width on a human figure: it catches the join
  // between arm and torso without darkening the whole chest.
  gtao.updateGtaoMaterial({
    radius: 0.12,
    distanceExponent: 1.0,
    thickness: 0.4,
    scale: 1.0,
    samples: 16,
    distanceFallOff: 1.0,
    screenSpaceRadius: false,
  })
  gtao.updatePdMaterial({ lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 4, radiusExponent: 1, rings: 2, samples: 16 })
  composer.addPass(gtao)

  // Threshold above white in the linear pass, so only a genuine specular hit
  // blooms. At 1.0 and strength 0.28 the whole figure wore a halo, because
  // polished armour lit by a 6.5 soft box is over 1.0 across most of its area.
  const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.12, 0.45, 1.25)
  composer.addPass(bloom)

  // Tone mapping and the sRGB conversion happen here rather than in the
  // material, because the composer's targets are linear and the renderer only
  // applies them when it draws to the canvas.
  composer.addPass(new OutputPass())

  const loader = new GLTFLoader()
  const pivot = new THREE.Group()
  scene.add(pivot)
  const timer = new THREE.Timer()

  let current = null
  let home = { position: camera.position.clone(), target: controls.target.clone() }
  let wireframe = false
  let highQuality = quality
  let disposed = false
  let mixer = null
  let clips = []
  let action = null
  let playing = true

  function resize() {
    const { clientWidth: w, clientHeight: h } = container
    if (!w || !h) return
    renderer.setSize(w, h, false)
    composer.setSize(w, h)
    const ratio = renderer.getPixelRatio()
    gtao.setSize(w * ratio, h * ratio)
    bloom.setSize(w * ratio, h * ratio)
    camera.aspect = w / h
    camera.updateProjectionMatrix()
  }

  const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(resize) : null
  if (observer) observer.observe(container)
  else globalThis.addEventListener?.('resize', resize)
  resize()

  function disposeCurrent() {
    if (!current) return
    if (mixer) {
      mixer.stopAllAction()
      mixer.uncacheRoot(current)
    }
    mixer = null
    action = null
    clips = []
    pivot.remove(current)
    current.traverse((node) => {
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
    current = null
  }

  function applyWireframe(object) {
    object.traverse((node) => {
      if (!node.isMesh) return
      for (const material of [].concat(node.material || [])) material.wireframe = wireframe
    })
  }

  function load(url) {
    return new Promise((resolve, reject) => {
      loader.load(
        url,
        (gltf) => {
          disposeCurrent()
          current = gltf.scene
          current.traverse((node) => {
            if (node.isMesh) { node.castShadow = true; node.receiveShadow = true }
            // A skinned mesh's bounding box is its bind pose and does not follow
            // the animation, so it culls out of frame mid-clip without this.
            if (node.isSkinnedMesh) node.frustumCulled = false
          })
          upgradeTextures(current, renderer)
          applyWireframe(current)
          pivot.add(current)

          const framed = frameObject(current)
          placeCamera(camera, framed)
          controls.target.copy(framed.target)
          controls.update()
          home = { position: camera.position.clone(), target: controls.target.clone() }
          studio.fitShadow(framed.radius)

          clips = gltf.animations || []
          if (clips.length) {
            mixer = new THREE.AnimationMixer(current)
            play(clips[0].name)
          }

          let triangles = 0
          current.traverse((node) => {
            if (node.isMesh && node.geometry) {
              const index = node.geometry.index
              triangles += index ? index.count / 3 : node.geometry.attributes.position.count / 3
            }
          })
          resolve({
            triangles: Math.round(triangles),
            clips: clips.map((c) => ({ name: c.name, duration: Number(c.duration.toFixed(3)) })),
          })
        },
        undefined,
        (error) => reject(error instanceof Error ? error : new Error(String(error))),
      )
    })
  }

  /** Cross-fade to the named clip. Unknown names are ignored, not thrown. */
  function play(name) {
    if (!mixer) return null
    const clip = clips.find((c) => c.name === name)
    if (!clip) return null
    const next = mixer.clipAction(clip)
    next.reset()
    next.play()
    if (action && action !== next) {
      // A hard cut between a run and an idle reads as a glitch; a short fade
      // reads as the character changing its mind.
      action.crossFadeTo(next, 0.25, false)
    }
    action = next
    action.paused = !playing
    return clip.name
  }

  function setPlaying(on) {
    playing = on
    if (action) action.paused = !on
  }

  function setRotation({ enabled, speed }) {
    controls.autoRotate = enabled
    if (Number.isFinite(speed)) controls.autoRotateSpeed = speed
  }

  function setWireframe(on) {
    wireframe = on
    if (current) applyWireframe(current)
  }

  /**
   * Off drops the ambient occlusion and the bloom and draws straight to the
   * canvas. The generating box is usually mid-inference on the same iGPU, and
   * a stuttering turntable is worse than a plainer one.
   */
  function setQuality(on) {
    highQuality = on
  }

  function setGrid(on) {
    grid.visible = on
    if (studio.ground) studio.ground.visible = !on
  }

  function resetView() {
    camera.position.copy(home.position)
    controls.target.copy(home.target)
    controls.update()
  }

  function tick() {
    if (disposed) return
    globalThis.requestAnimationFrame(tick)
    // The image tab hides the stage; a hidden element has no size, and drawing
    // into it is pure waste on a laptop iGPU that is usually busy generating.
    timer.update()
    const delta = timer.getDelta()
    if (!container.clientWidth || !container.clientHeight) return
    if (mixer) mixer.update(delta)
    controls.update()
    if (highQuality) composer.render(delta)
    else renderer.render(scene, camera)
  }
  globalThis.requestAnimationFrame(tick)

  // Camera state as numbers. The canvas cannot be read back after a frame is
  // presented without preserveDrawingBuffer, which costs performance on every
  // frame to serve debugging, so the turntable reports where it is instead.
  function getState() {
    return {
      autoRotate: controls.autoRotate,
      autoRotateSpeed: controls.autoRotateSpeed,
      azimuth: controls.getAzimuthalAngle(),
      polar: controls.getPolarAngle(),
      distance: controls.getDistance(),
      wireframe,
      quality: highQuality,
      grid: grid.visible,
      hasModel: Boolean(current),
      clips: clips.map((c) => c.name),
      clip: action ? action.getClip().name : null,
      playing,
    }
  }

  return {
    // The graph itself, for the console and for the screenshot checks. Nothing
    // in the page reads these; they are here so a render bug can be measured
    // rather than argued about.
    debug: { scene, camera, renderer, studio, composer },
    load,
    play,
    setPlaying,
    setRotation,
    setWireframe,
    setQuality,
    setGrid,
    resetView,
    resize,
    getState,
    dispose() {
      disposed = true
      observer?.disconnect()
      disposeCurrent()
      grid.geometry.dispose()
      grid.material.dispose()
      studio.dispose()
      composer.dispose()
      controls.dispose()
      renderer.dispose()
      renderer.domElement.remove()
    },
  }
}
