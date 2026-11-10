import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

interface BreadboardViewerProps {
  layout: {
    board: { cols: number; rows: number; rail_top: number; rail_bottom: number };
    components: { ref: string; pins: number; is_ic: boolean; col: number; positions: { pin: number; row: number; col: number }[] }[];
    wires: { from: number[]; to: number[] }[];
  };
  height?: number;
}

/**
 * 3D-визуализация макетной платы (breadboard/fretboard) с анимацией.
 * Отдельный артефакт — не ломает PCB.
 */
export function BreadboardViewer({ layout, height = 320 }: BreadboardViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0d1117);

    const camera = new THREE.PerspectiveCamera(45, container.clientWidth / height, 0.1, 1000);
    camera.position.set(30, 25, 30);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(container.clientWidth, height);
    renderer.setPixelRatio(window.devicePixelRatio);
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    const amb = new THREE.AmbientLight(0xffffff, 0.5);
    scene.add(amb);
    const dir = new THREE.DirectionalLight(0xffffff, 0.9);
    dir.position.set(20, 30, 20);
    scene.add(dir);

    const { cols, rows } = layout.board;
    const cell = 1.0;
    const gap = 0.6; // центральный разрыв

    // основание макетки
    const baseGeo = new THREE.BoxGeometry(cols * cell + 2, 0.4, (rows + 2) * cell + 2);
    const baseMat = new THREE.MeshStandardMaterial({ color: 0xf5f5dc, roughness: 0.8 });
    const base = new THREE.Mesh(baseGeo, baseMat);
    base.position.set(cols / 2, -0.2, rows / 2);
    scene.add(base);

    // отверстия (точки контакта)
    const holeGeo = new THREE.CylinderGeometry(0.15, 0.15, 0.5, 8);
    const holeMat = new THREE.MeshStandardMaterial({ color: 0x333333 });
    for (let c = 1; c <= cols; c++) {
      for (let r = 0; r < rows; r++) {
        const rr = r >= rows / 2 ? r + 1 : r; // разрыв по центру
        const hole = new THREE.Mesh(holeGeo, holeMat);
        hole.position.set(c, 0.1, rr);
        scene.add(hole);
      }
    }

    // рельсы питания (верх/низ)
    const railGeo = new THREE.BoxGeometry(cols * cell, 0.15, 0.6);
    const railMat = new THREE.MeshStandardMaterial({ color: 0xcc3333 });
    const railTop = new THREE.Mesh(railGeo, railMat);
    railTop.position.set(cols / 2, 0.1, -1);
    scene.add(railTop);
    const railBot = new THREE.Mesh(railGeo, railMat);
    railBot.position.set(cols / 2, 0.1, rows + 1);
    scene.add(railBot);

    // компоненты (боксы с анимацией появления)
    const compMeshes: THREE.Mesh[] = [];
    layout.components.forEach((comp, idx) => {
      const w = (comp.is_ic ? comp.pins / 2 : 1) * cell + 0.3;
      const d = comp.is_ic ? 1.2 : 0.6;
      const geo = new THREE.BoxGeometry(w, 0.8, d);
      const mat = new THREE.MeshStandardMaterial({
        color: comp.is_ic ? 0x2d5a27 : 0x1f4e79,
        roughness: 0.6,
      });
      const mesh = new THREE.Mesh(geo, mat);
      const pos = comp.positions[0];
      const rr = pos.row >= rows / 2 ? pos.row + 1 : pos.row;
      mesh.position.set(pos.col, 0.6, rr);
      mesh.scale.setScalar(0);
      scene.add(mesh);
      compMeshes.push(mesh);
      // анимация появления с задержкой
      const start = performance.now() + idx * 120;
      const dur = 600;
      const tick = () => {
        const t = Math.min(1, (performance.now() - start) / dur);
        if (t < 0) { requestAnimationFrame(tick); return; }
        const e = 1 - Math.pow(1 - t, 3);
        mesh.scale.setScalar(e);
        if (t < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });

    // провода (линии)
    const wireMat = new THREE.LineBasicMaterial({ color: 0xffcc00 });
    layout.wires.forEach((w) => {
      const [r1, c1] = w.from;
      const [r2, c2] = w.to;
      const rr1 = r1 >= rows / 2 ? r1 + 1 : r1;
      const rr2 = r2 >= rows / 2 ? r2 + 1 : r2;
      const pts = [
        new THREE.Vector3(c1, 0.3, rr1),
        new THREE.Vector3((c1 + c2) / 2, 1.2, (rr1 + rr2) / 2),
        new THREE.Vector3(c2, 0.3, rr2),
      ];
      const curve = new THREE.CatmullRomCurve3(pts);
      const geo = new THREE.TubeGeometry(curve, 12, 0.08, 6, false);
      const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0xffcc00 }));
      scene.add(mesh);
    });

    let raf = 0;
    const animate = () => {
      raf = requestAnimationFrame(animate);
      controls.update();
      scene.rotation.y += 0.002;
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
      if (container.contains(renderer.domElement)) container.removeChild(renderer.domElement);
    };
  }, [layout, height]);

  return <div ref={containerRef} style={{ width: '100%', height }} className="rounded overflow-hidden" />;
}