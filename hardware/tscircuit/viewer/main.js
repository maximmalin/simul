/**
 * Krystal Radio — интерактивный просмотрщик 3D-модели платы.
 *
 * Модуль собирается rollup'ом в один IIFE (window.KrystalViewer) и
 * встраивается в самодостаточную HTML-страницу out/krystal-radio.html
 * вместе с base64-моделью board.glb. Никаких CDN: страница работает
 * офлайн из одного файла.
 *
 * Зависимости берутся из локального node_modules/three, поэтому версия
 * three.js зафиксирована в проекте, а не «когда-нибудь в интернете».
 */

import * as THREE from "three"
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js"
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js"

const BG = 0x0d1117

/**
 * Создать сцену с моделью GLB внутри контейнера.
 *
 * @param {HTMLElement} container  куда вставляется canvas
 * @param {string} glbUrl          data: URI или обычный URL на .glb
 * @returns {{dispose: Function}|null}
 */
function mount(container, glbUrl) {
  if (!container) throw new Error("KrystalViewer.mount: нет контейнера")

  let renderer
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
  } catch (err) {
    container.innerHTML =
      '<div class="viewer-fallback">WebGL недоступен в этом браузере — ' +
      "3D-модель показана на вкладке «Файлы» (board.glb / board.step).</div>"
    return null
  }

  const width = () => Math.max(1, container.clientWidth)
  const height = () => Math.max(1, container.clientHeight)

  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.setSize(width(), height())
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.05
  renderer.domElement.setAttribute("aria-label", "3D-модель печатной платы")
  container.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  scene.background = new THREE.Color(BG)

  const camera = new THREE.PerspectiveCamera(42, width() / height(), 0.05, 100000)
  camera.position.set(60, -90, 70)

  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.rotateSpeed = 0.8
  controls.zoomSpeed = 0.9
  controls.panSpeed = 0.8

  // Освещение: небо + ключевой + заполняющий. Достаточно, чтобы
  // проявились и шёлкография, и медные полигоны, и корпуса деталей.
  const hemi = new THREE.HemisphereLight(0xffffff, 0x2b3440, 1.35)
  scene.add(hemi)
  const key = new THREE.DirectionalLight(0xffffff, 2.2)
  key.position.set(120, 180, 240)
  scene.add(key)
  const fill = new THREE.DirectionalLight(0x9fc4ff, 0.9)
  fill.position.set(-160, -90, 60)
  scene.add(fill)
  const rim = new THREE.DirectionalLight(0xffd9a0, 0.7)
  rim.position.set(0, -200, -160)
  scene.add(rim)

  const root = new THREE.Group()
  scene.add(root)

  let disposed = false
  const state = { model: null, wire: false }

  const loader = new GLTFLoader()
  loader.load(
    glbUrl,
    (gltf) => {
      if (disposed) return
      state.model = gltf.scene
      root.add(gltf.scene)

      // Центрируем модель по её габариту и подгоняем камеру так, чтобы
      // она целиком попала в кадр при любом размере платы.
      const box = new THREE.Box3().setFromObject(gltf.scene)
      const size = box.getSize(new THREE.Vector3())
      const center = box.getCenter(new THREE.Vector3())
      gltf.scene.position.sub(center)

      const maxDim = Math.max(size.x, size.y, size.z) || 100
      const fitDist = maxDim / (2 * Math.tan((45 * Math.PI) / 180 / 2))
      camera.near = maxDim / 500
      camera.far = maxDim * 60
      camera.updateProjectionMatrix()

      const dir = new THREE.Vector3(0.55, -1.0, 0.75).normalize()
      camera.position.copy(dir.multiplyScalar(fitDist * 1.55))
      controls.target.set(0, 0, 0)
      controls.update()

      container.dispatchEvent(
        new CustomEvent("model-loaded", { detail: { size, center } }),
      )
    },
    undefined,
    (err) => {
      if (disposed) return
      container.innerHTML =
        '<div class="viewer-fallback">Не удалось разобрать board.glb: ' +
        String(err && err.message ? err.message : err) +
        "</div>"
      container.dispatchEvent(new CustomEvent("model-error", { detail: { error: err } }))
    },
  )

  function resize() {
    renderer.setSize(width(), height(), false)
    const cam = camera
    cam.aspect = width() / height()
    cam.updateProjectionMatrix()
  }
  const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null
  if (ro) ro.observe(container)
  window.addEventListener("resize", resize)

  // Непрерывный цикл, пока вкладка видима: OrbitControls с damping
  // нуждается в update() каждый кадр. При скрытой вкладке rAF
  // приостанавливается браузером сам.
  function animate() {
    if (disposed) return
    requestAnimationFrame(animate)
    controls.update()
    renderer.render(scene, camera)
  }
  animate()

  return {
    dispose() {
      disposed = true
      if (ro) ro.disconnect()
      window.removeEventListener("resize", resize)
      controls.dispose()
      scene.traverse((o) => {
        if (o.geometry) o.geometry.dispose()
        if (o.material) {
          const mats = Array.isArray(o.material) ? o.material : [o.material]
          for (const m of mats) m.dispose()
        }
      })
      renderer.dispose()
      if (renderer.domElement.parentNode === container) {
        container.removeChild(renderer.domElement)
      }
    },
    setWireframe(on) {
      state.wire = !!on
      root.traverse((o) => {
        if (o.isMesh) {
          const mats = Array.isArray(o.material) ? o.material : [o.material]
          for (const m of mats) if ("wireframe" in m) m.wireframe = state.wire
        }
      })
    },
    reset() {
      if (!state.model) return
      const box = new THREE.Box3().setFromObject(state.model)
      const size = box.getSize(new THREE.Vector3())
      const maxDim = Math.max(size.x, size.y, size.z) || 100
      const fitDist = maxDim / (2 * Math.tan((45 * Math.PI) / 180 / 2))
      const dir = new THREE.Vector3(0.55, -1.0, 0.75).normalize()
      camera.position.copy(dir.multiplyScalar(fitDist * 1.55))
      controls.target.set(0, 0, 0)
      controls.update()
    },
    setAutoRotate(on) {
      controls.autoRotate = !!on
      controls.autoRotateSpeed = 1.2
    },
  }
}

const api = { mount, THREE }
if (typeof window !== "undefined") window.KrystalViewer = api
export default api
