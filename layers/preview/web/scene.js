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
  container.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  scene.background = new THREE.Color(0x15171c)

  // TRELLIS writes PBR materials with real metallic and roughness, and metal
  // renders black without something to reflect. RoomEnvironment is generated in
  // code, so the page still needs no downloaded asset.
  const pmrem = new THREE.PMREMGenerator(renderer)
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture

  const key = new THREE.DirectionalLight(0xffffff, 1.6)
  key.position.set(3, 5, 4)
  scene.add(key)
  scene.add(new THREE.HemisphereLight(0xbfd4ff, 0x30302f, 0.5))

  const grid = new THREE.GridHelper(4, 20, 0x3a3f4b, 0x24272e)
  grid.material.transparent = true
  grid.material.opacity = 0.5
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

  let current = null
  let home = { position: camera.position.clone(), target: controls.target.clone() }
  let wireframe = false
  let disposed = false

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

  // Scale into a unit-ish box and sit it on the grid, so a 4 cm bolt and a
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
    grid.position.y = 0
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
          applyWireframe(current)
          pivot.add(current)
          frame(current)
          let triangles = 0
          current.traverse((node) => {
            if (node.isMesh && node.geometry) {
              const index = node.geometry.index
              triangles += index ? index.count / 3 : node.geometry.attributes.position.count / 3
            }
          })
          resolve({ triangles: Math.round(triangles) })
        },
        undefined,
        (error) => reject(error instanceof Error ? error : new Error(String(error))),
      )
    })
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
    controls.update()
    renderer.render(scene, camera)
    globalThis.requestAnimationFrame(tick)
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
    }
  }

  return {
    load,
    setRotation,
    setWireframe,
    resetView,
    resize,
    getState,
    dispose() {
      disposed = true
      observer?.disconnect()
      disposeCurrent()
      pmrem.dispose()
      controls.dispose()
      renderer.dispose()
      renderer.domElement.remove()
    },
  }
}
