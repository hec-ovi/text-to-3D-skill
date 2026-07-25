// The lighting and the look, in one place, because the turntable and the
// gallery thumbnails have to agree: a card that renders a model darker than the
// viewer does is worse than no card.
//
// Everything here is generated in code. The page ships no HDRI, no LUT and no
// texture, so it still opens from a file:// path with the network off.

import * as THREE from 'three'

// TRELLIS bakes albedo into the base colour map, and a filmic curve that
// desaturates (ACES) turns a brass helmet into a grey one. Khronos PBR Neutral
// exists for exactly this case: highlight rolloff without touching mid-tone
// hue, which is what an asset viewer wants.
export const TONE_MAPPING = THREE.NeutralToneMapping
export const EXPOSURE = 1.15

// The model is framed into a 1.4-unit box (see frameObject), so every distance
// here is in those units and holds for a bolt and for a statue alike.
export const FRAME_SIZE = 1.4

const SKY = new THREE.Color(0.055, 0.062, 0.082)
const HORIZON = new THREE.Color(0.62, 0.66, 0.78)
const GROUND = new THREE.Color(0.012, 0.013, 0.018)

// How hard the environment pushes. A big bright dome is ambient light by
// another name, and ambient light is the enemy of form: at the first setting
// here the dome supplied 72% of the floor's brightness (measured, by knocking
// scene.environment out and reading the pixel back), which flattened the model
// and left the key light with nothing to carve. The dome is now mostly there so
// metal has a horizon to bend, and the soft boxes carry the speculars.

/**
 * The scene the environment map is baked from: a graded sky dome plus three
 * soft boxes.
 *
 * RoomEnvironment, which this replaces, is a box of grey panels. Metal lit by
 * it reflects flat grey and reads as plastic, because there is no horizon in
 * the reflection and no bright source to catch an edge. A dome with a hot
 * horizon line and one strong key gives curved metal something to bend, which
 * is most of what separates a render from a screenshot.
 */
function environmentScene() {
  const scene = new THREE.Scene()

  const dome = new THREE.Mesh(
    new THREE.SphereGeometry(40, 32, 24),
    new THREE.ShaderMaterial({
      side: THREE.BackSide,
      depthWrite: false,
      uniforms: {
        sky: { value: SKY.clone().multiplyScalar(0.9) },
        horizon: { value: HORIZON.clone().multiplyScalar(1.8) },
        ground: { value: GROUND.clone().multiplyScalar(0.5) },
      },
      vertexShader: `
        varying vec3 vDir;
        void main() {
          vDir = normalize(position);
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }`,
      // Two smoothsteps rather than one mix: the horizon needs to be a band,
      // not a midpoint, or the reflection shows a gradient with no line in it.
      fragmentShader: `
        uniform vec3 sky, horizon, ground;
        varying vec3 vDir;
        void main() {
          float h = vDir.y;
          vec3 above = mix(horizon, sky, smoothstep(0.0, 0.55, h));
          vec3 below = mix(horizon, ground, smoothstep(0.0, -0.35, h));
          gl_FragColor = vec4(h > 0.0 ? above : below, 1.0);
        }`,
    }),
  )
  scene.add(dome)

  // Emissive planes stand in for area lights. Values above 1 are the point:
  // PMREM renders into a half-float target, so a soft box at 8 puts a real
  // specular hit on the model instead of a pale smear. They are kept small as
  // well as bright, because a panel's contribution to diffuse light goes with
  // the solid angle it covers while its specular hit does not: small and hot
  // gives the highlight without the wash.
  const box = (w, h, colour, position) => {
    const mesh = new THREE.Mesh(
      new THREE.PlaneGeometry(w, h),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(...colour) }),
    )
    mesh.position.set(...position)
    mesh.lookAt(0, 0, 0)
    scene.add(mesh)
  }
  box(5, 5, [8.0, 7.9, 7.5], [5.5, 4.3, 0.7])   // key, side-right, 38 degrees up
  box(5, 5, [0.9, 1.0, 1.3], [-5.0, 1.6, 2.5])  // fill, low front-left, cool
  box(5, 3, [4.0, 4.2, 5.2], [-1.2, 3.2, -6.0]) // rim, behind, for the edge

  return scene
}

/**
 * A disc that fades to nothing at its rim, as vertex colour rather than alpha.
 *
 * A transparent floor was the obvious way to do this and the wrong one. The
 * ambient-occlusion pass reads a depth buffer that transparent surfaces do not
 * write to, so a see-through floor takes no contact shadow from the model
 * standing on it, which is the one thing the floor is there for.
 */
function fadingDisc(radius, segments = 96, rings = 24) {
  const geometry = new THREE.RingGeometry(0, radius, segments, rings)
  const position = geometry.attributes.position
  const colours = new Float32Array(position.count * 3)
  for (let i = 0; i < position.count; i++) {
    const r = Math.hypot(position.getX(i), position.getY(i)) / radius
    // Held flat under the model, then rolled off, so the fade never reads as a
    // circle drawn on the floor.
    const t = 1 - THREE.MathUtils.smoothstep(r, 0.16, 1.0)
    colours[i * 3] = colours[i * 3 + 1] = colours[i * 3 + 2] = t
  }
  geometry.setAttribute('color', new THREE.BufferAttribute(colours, 3))
  return geometry
}

/**
 * Build the environment, the backdrop, the floor and the three lights into
 * `scene`. Returns the handles the caller needs to size shadows and dispose.
 */
export function buildStudio(scene, renderer, { floor = true, backdrop = true } = {}) {
  const pmrem = new THREE.PMREMGenerator(renderer)
  const source = environmentScene()
  const environment = pmrem.fromScene(source, 0.03).texture
  scene.environment = environment
  source.traverse((node) => {
    if (!node.isMesh) return
    node.geometry.dispose()
    node.material.dispose()
  })

  const disposables = []

  if (backdrop) {
    // The visible background is its own dome, darker than the one the
    // reflections see. Showing the lighting dome directly blows the model out
    // against a bright horizon; the model has to be the brightest thing here.
    //
    // These numbers are linear, not the sRGB they look like: the composer
    // renders into a linear target and the output pass encodes at the end. The
    // first version of this used #12141a straight and produced a mid-grey fog
    // bank, because 0.075 linear lands near 0.30 once encoded.
    const material = new THREE.ShaderMaterial({
      side: THREE.BackSide,
      depthWrite: false,
      uniforms: {
        top: { value: new THREE.Color(0.0028, 0.0033, 0.0048) },
        middle: { value: new THREE.Color(0.0125, 0.0140, 0.0185) },
        bottom: { value: new THREE.Color(0.0016, 0.0018, 0.0024) },
      },
      vertexShader: `
        varying vec3 vDir;
        void main() {
          vDir = normalize(position);
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }`,
      fragmentShader: `
        uniform vec3 top, middle, bottom;
        varying vec3 vDir;
        void main() {
          float h = vDir.y;
          vec3 c = h > 0.02
            ? mix(middle, top, smoothstep(0.02, 0.75, h))
            : mix(middle, bottom, smoothstep(0.02, -0.4, h));
          gl_FragColor = vec4(c, 1.0);
        }`,
    })
    const dome = new THREE.Mesh(new THREE.SphereGeometry(30, 32, 24), material)
    dome.name = 'backdrop'
    scene.add(dome)
    disposables.push(dome.geometry, material)
  }

  let ground = null
  if (floor) {
    // Phong, not Standard, and this is the one place in the page that is not
    // physically based on purpose.
    //
    // A standard floor is lit by scene.environment whatever its
    // envMapIntensity says, because that property scales a material's own
    // envMap and does not touch the scene's. So the floor sat at a flat pale
    // grey with the key contributing almost none of it, and a shadow, which is
    // only the absence of the key, was invisible. Phong takes the environment
    // through `reflectivity` instead, which can be set to zero, and `specular`
    // black removes the grazing sheen that a near-horizontal floor otherwise
    // reflects back at the camera. What is left is diffuse from three lights,
    // which is exactly what a shadow can cut into.
    const material = new THREE.MeshPhongMaterial({
      color: 0x363d4c,
      specular: 0x000000,
      shininess: 0,
      reflectivity: 0,
      vertexColors: true,
    })
    ground = new THREE.Mesh(fadingDisc(9), material)
    ground.name = 'ground'
    ground.rotation.x = -Math.PI / 2
    ground.receiveShadow = true
    scene.add(ground)
    disposables.push(ground.geometry, material)
  }

  // Three-point, matching the environment so the specular and the shadow agree
  // on where the light is. Only the key casts: two shadows from one object read
  // as a mistake, and the second map costs more than it buys.
  // Where the key sits is a composition decision, not a taste one. The first
  // version put it at (3, 6, 4): within a few degrees of the turntable's
  // opening camera azimuth and 50 degrees up, which is the one arrangement
  // where the shadow is both short and thrown straight away from the camera,
  // so the model hid its own shadow completely and the floor read as fog.
  //
  // 45 degrees off the camera's azimuth and 38 degrees up is the portrait
  // setup, and it fixes both: the shadow is about 1.8 times the model's height
  // and sweeps out to one side of the silhouette instead of behind it. Side
  // light is also what gives a shape its form; a key next to the lens flattens
  // whatever it hits.
  const key = new THREE.DirectionalLight(0xfff4e8, 3.6)
  key.position.set(4.96, 3.9, 0.61)
  key.castShadow = true
  key.shadow.mapSize.set(2048, 2048)
  // normalBias, not bias: a marching-cubes surface is curved everywhere, and a
  // constant depth bias either leaves acne on the curve or peels the contact
  // shadow off the floor. Offsetting along the normal fixes both.
  key.shadow.normalBias = 0.02
  key.shadow.bias = -0.0004
  key.shadow.radius = 1.5
  scene.add(key)

  const fill = new THREE.DirectionalLight(0xbfd0ff, 0.45)
  fill.position.set(-5.0, 1.6, 2.5)
  scene.add(fill)

  const rim = new THREE.DirectionalLight(0xd8e4ff, 1.9)
  rim.position.set(-1.2, 3.2, -6.0)
  scene.add(rim)

  return {
    key,
    fill,
    rim,
    ground,
    environment,
    /** Fit the key's orthographic shadow frustum to the framed model. */
    fitShadow(radius) {
      const extent = Math.max(radius * 2.4, 1)
      const camera = key.shadow.camera
      camera.left = -extent
      camera.right = extent
      camera.top = extent
      camera.bottom = -extent
      camera.near = 0.1
      camera.far = extent * 10
      camera.updateProjectionMatrix()
    },
    dispose() {
      for (const item of disposables) item.dispose()
      environment.dispose()
      pmrem.dispose()
    },
  }
}

/**
 * Scale an object into a FRAME_SIZE box, sit it on y=0, and return the numbers
 * a camera needs to look at it. Pure geometry: no camera is touched here, so
 * the turntable and a 256px thumbnail frame identically.
 */
export function frameObject(object) {
  const box = new THREE.Box3().setFromObject(object)
  const size = box.getSize(new THREE.Vector3())
  const centre = box.getCenter(new THREE.Vector3())
  const longest = Math.max(size.x, size.y, size.z) || 1
  const scale = FRAME_SIZE / longest

  object.scale.setScalar(scale)
  object.position.copy(centre).multiplyScalar(-scale)
  object.position.y += (size.y * scale) / 2

  const height = size.y * scale
  const radius = (longest * scale) / 2
  return { scale, radius, height, target: new THREE.Vector3(0, height / 2, 0) }
}

/**
 * Point `camera` at a framed object from the three-quarter angle a product shot
 * uses. `fov` is read off the camera, so a narrow lens pulls back on its own.
 */
export function placeCamera(camera, { radius, target }, distanceScale = 1.0) {
  const distance = (radius / Math.sin((camera.fov * Math.PI) / 360)) * 1.9 * distanceScale
  camera.position.set(distance * 0.58, target.y + distance * 0.34, distance * 0.74)
  camera.lookAt(target)
  return distance
}

/**
 * Anisotropy defaults to 1, which is why a baked 2048 atlas on a curved surface
 * looks blurred the moment the surface turns away from the camera. This is the
 * cheapest quality win on the page: no extra draw calls, no extra memory.
 */
export function upgradeTextures(root, renderer) {
  const anisotropy = renderer.capabilities.getMaxAnisotropy()
  root.traverse((node) => {
    if (!node.isMesh) return
    for (const material of [].concat(node.material || [])) {
      for (const key of Object.keys(material)) {
        const value = material[key]
        if (value && value.isTexture) {
          value.anisotropy = anisotropy
          value.needsUpdate = true
        }
      }
    }
  })
}
