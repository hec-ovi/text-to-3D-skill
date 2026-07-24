// The WebGL half: one turntable that loads a GLB, frames it, and spins it.
// Nothing in here touches the page's controls; it exposes a small API that
// ui.js drives.

import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js'

export function createViewer(container) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
  renderer.setPixelRatio(Math.min(globalThis.devicePixelRatio || 1, 2))
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.05
  // A model floating with no contact point reads as a render, not an object.
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFSoftShadowMap
  container.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  scene.background = new THREE.Color(0x0c0d11)

  // TRELLIS writes PBR materials with real metallic and roughness, and metal
  // renders black without something to reflect. RoomEnvironment is generated in
  // code, so the page still needs no downloaded asset.
  const pmrem = new THREE.PMREMGenerator(renderer)
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture

  const key = new THREE.DirectionalLight(0xffffff, 1.7)
  key.position.set(3, 5, 4)
  key.castShadow = true
  key.shadow.mapSize.set(1024, 1024)
  key.shadow.bias = -0.0015
  scene.add(key)
  scene.add(new THREE.HemisphereLight(0xbfd4ff, 0x30302f, 0.5))

  // The floor takes the shadow and nothing else: no albedo, no lighting, so the
  // model keeps the whole frame and still sits on something.
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(24, 24),
    new THREE.ShadowMaterial({ opacity: 0.42 }),
  )
  floor.rotation.x = -Math.PI / 2
  floor.receiveShadow = true
  scene.add(floor)

  const grid = new THREE.GridHelper(4, 20, 0x343a46, 0x20232a)
  grid.material.transparent = true
  grid.material.opacity = 0.42
  scene.add(grid)

  const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 100)
  camera.position.set(1.6, 1.1, 1.9)

  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.06
  controls.autoRotate = true
  controls.autoRotateSpeed = 1.5
  controls.minDistance = 0.2
  controls.maxDistance = 20

  const loader = new GLTFLoader()
  const pivot = new THREE.Group()
  scene.add(pivot)
  const clock = new THREE.Clock()

  let current = null
  let home = { position: camera.position.clone(), target: controls.target.clone() }
  let wireframe = false
  let disposed = false
  let mixer = null
  let clips = []
  let action = null
  let playing = true

  function resize() {
    const { clientWidth: w, clientHeight: h } = container
    if (!w || !h) return
    renderer.setSize(w, h, false)
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

  // Scale into a unit-ish box and sit it on the floor, so a 4 cm bolt and a
  // 3 m statue both arrive framed the same way.
  function frame(object) {
    const box = new THREE.Box3().setFromObject(object)
    const size = box.getSize(new THREE.Vector3())
    const centre = box.getCenter(new THREE.Vector3())
    const longest = Math.max(size.x, size.y, size.z) || 1
    const scale = 1.4 / longest

    object.scale.setScalar(scale)
    object.position.copy(centre).multiplyScalar(-scale)
    object.position.y += (size.y * scale) / 2

    const radius = (longest * scale) / 2
    const distance = radius / Math.sin((camera.fov * Math.PI) / 360) * 1.6
    const target = new THREE.Vector3(0, (size.y * scale) / 2, 0)
    camera.position.set(distance * 0.62, target.y + distance * 0.42, distance * 0.72)
    controls.target.copy(target)
    controls.update()
    home = { position: camera.position.clone(), target: controls.target.clone() }

    // The shadow camera is orthographic and fixed by default, which either
    // clips a tall model's shadow or wastes the map on empty floor.
    const extent = Math.max(radius * 2.2, 1)
    const shadow = key.shadow.camera
    shadow.left = -extent
    shadow.right = extent
    shadow.top = extent
    shadow.bottom = -extent
    shadow.near = 0.1
    shadow.far = extent * 8
    shadow.updateProjectionMatrix()
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
          applyWireframe(current)
          pivot.add(current)
          frame(current)

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
    const delta = clock.getDelta()
    if (!container.clientWidth || !container.clientHeight) return
    if (mixer) mixer.update(delta)
    controls.update()
    renderer.render(scene, camera)
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
      hasModel: Boolean(current),
      clips: clips.map((c) => c.name),
      clip: action ? action.getClip().name : null,
      playing,
    }
  }

  return {
    load,
    play,
    setPlaying,
    setRotation,
    setWireframe,
    resetView,
    resize,
    getState,
    dispose() {
      disposed = true
      observer?.disconnect()
      disposeCurrent()
      floor.geometry.dispose()
      floor.material.dispose()
      pmrem.dispose()
      controls.dispose()
      renderer.dispose()
      renderer.domElement.remove()
    },
  }
}
