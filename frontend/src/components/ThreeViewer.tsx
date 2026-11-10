import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

interface ThreeViewerProps {
  url: string;
  height?: number;
}

/**
 * Лёгкий three.js-просмотрщик GLB/GLTF для живого флоу (реальный 3D,
 * а не картинка/ссылка). Использует GLTFLoader + OrbitControls.
 */
export function ThreeViewer({ url, height = 220 }: ThreeViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0d1117);

    const camera = new THREE.PerspectiveCamera(45, container.clientWidth / height, 0.1, 1000);
    camera.position.set(2, 2, 2);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(container.clientWidth, height);
    renderer.setPixelRatio(window.devicePixelRatio);
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    // свет
    const amb = new THREE.AmbientLight(0xffffff, 0.6);
    scene.add(amb);
    const dir = new THREE.DirectionalLight(0xffffff, 0.8);
    dir.position.set(5, 5, 5);
    scene.add(dir);

    const grid = new THREE.GridHelper(10, 10, 0x30363d, 0x1c232d);
    scene.add(grid);

    const loader = new GLTFLoader();
    let model: THREE.Object3D | null = null;
    let entranceT = 0;
    let entranceDone = false;
    loader.load(
      url,
      (gltf) => {
        model = gltf.scene;
        const box = new THREE.Box3().setFromObject(model);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z) || 1;
        const finalScale = 2 / maxDim;
        // стартуем с 0 для анимации появления
        model.scale.setScalar(0);
        model.position.sub(center.multiplyScalar(finalScale));
        scene.add(model);
        // анимация появления: масштаб 0 -> finalScale
        const start = performance.now();
        const dur = 900;
        const tick = () => {
          const t = Math.min(1, (performance.now() - start) / dur);
          const e = 1 - Math.pow(1 - t, 3); // easeOutCubic
          if (model) model.scale.setScalar(finalScale * e);
          if (t < 1) requestAnimationFrame(tick);
          else entranceDone = true;
        };
        requestAnimationFrame(tick);
      },
      undefined,
      (err) => console.error('GLTF load error', err),
    );

    let raf = 0;
    const animate = () => {
      raf = requestAnimationFrame(animate);
      controls.update();
      if (model && entranceDone) {
        // плавный автоповорот + лёгкое покачивание
        model.rotation.y += 0.008;
        model.position.y = Math.sin(performance.now() * 0.001) * 0.05;
      }
      renderer.render(scene, camera);
    };
    animate();

    const onResize = () => {
      if (!container) return;
      camera.aspect = container.clientWidth / height;
      camera.updateProjectionMatrix();
      renderer.setSize(container.clientWidth, height);
    };
    window.addEventListener('resize', onResize);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
      controls.dispose();
      renderer.dispose();
      if (container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement);
      }
    };
  }, [url, height]);

  return (
    <div ref={containerRef} style={{ width: '100%', height }} className="rounded overflow-hidden" />
  );
}