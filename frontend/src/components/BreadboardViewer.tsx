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

function colorForRef(ref: string): number {
  const r = ref.toUpperCase();
  if (r.startsWith('U') || r.startsWith('IC')) return 0x2d5a27;
  if (r.startsWith('R')) return 0x1f4e79;
  if (r.startsWith('C')) return 0xb45309;
  if (r.startsWith('L')) return 0x6b21a8;
  if (r.startsWith('Q') || r.startsWith('T')) return 0x9f1239;
  if (r.startsWith('D')) return 0x7c2d12;
  if (r.startsWith('J') || r.startsWith('X')) return 0x374151;
  return 0x4b5563;
}

function makeLabelSprite(text: string): THREE.Sprite {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 64;
  const ctx = canvas.getContext('2d')!;
  ctx.fillStyle = 'rgba(0,0,0,0.7)';
  ctx.fillRect(0, 0, 256, 64);
  ctx.font = 'bold 28px monospace';
  ctx.fillStyle = '#ffffff';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, 128, 32);
  const tex = new THREE.CanvasTexture(canvas);
  const mat = new THREE.SpriteMaterial({ map: tex, depthTest: false });
  const sprite = new THREE.Sprite(mat);
  sprite.scale.set(2.4, 0.6, 1);
  return sprite;
}

/**
 * Понятная 3D-визуализация макетной платы (breadboard/fretboard) с
 * анимацией появления компонентов. Отдельный артефакт — PCB не трогает.
 */
export function BreadboardViewer({ layout, height = 360 }: BreadboardViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x1a1f2e);

    const camera = new THREE.PerspectiveCamera(40, container.clientWidth / height, 0.1, 1000);
    camera.position.set(32, 28, 32);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(container.clientWidth, height);
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.shadowMap.enabled = true;
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.target.set(layout.board.cols / 2, 0, layout.board.rows / 2);

    const amb = new THREE.AmbientLight(0xffffff, 0.55);
    scene.add(amb);
    const dir = new THREE.DirectionalLight(0xffffff, 0.85);
    dir.position.set(20, 40, 20);
    dir.castShadow = true;
    scene.add(dir);
    const fill = new THREE.DirectionalLight(0x88aaff, 0.3);
    fill.position.set(-20, 10, -10);
    scene.add(fill);

    const { cols, rows } = layout.board;
    const cell = 1.0;
    const centerGap = 1; // разрыв между рядами e/f

    // основание
    const baseGeo = new THREE.BoxGeometry(cols * cell + 2, 0.5, rows * cell + centerGap + 2);
    const baseMat = new THREE.MeshStandardMaterial({ color: 0xf2f2e8, roughness: 0.7 });
    const base = new THREE.Mesh(baseGeo, baseMat);
    base.position.set(cols / 2, -0.25, (rows + centerGap) / 2);
    base.receiveShadow = true;
    scene.add(base);

    // отверстия (точки контакта) — мелкие тёмные цилиндры
    const holeGeo = new THREE.CylinderGeometry(0.12, 0.12, 0.55, 8);
    const holeMat = new THREE.MeshStandardMaterial({ color: 0x222222 });
    for (let c = 1; c <= cols; c++) {
      for (let r = 0; r < rows; r++) {
        const rr = r >= rows / 2 ? r + centerGap : r;
        const hole = new THREE.Mesh(holeGeo, holeMat);
        hole.position.set(c, 0.1, rr);
        scene.add(hole);
      }
    }

    // рельсы питания (красный + синий)
    const railGeo = new THREE.BoxGeometry(cols * cell, 0.12, 0.5);
    const railRed = new THREE.Mesh(railGeo, new THREE.MeshStandardMaterial({ color: 0xcc2222 }));
    railRed.position.set(cols / 2, 0.12, -0.8);
    scene.add(railRed);
    const railBlue = new THREE.Mesh(railGeo, new THREE.MeshStandardMaterial({ color: 0x2244cc }));
    railBlue.position.set(cols / 2, 0.12, rows + centerGap + 0.8);
    scene.add(railBlue);

    // метки колонок (каждые 5)
    for (let c = 5; c <= cols; c += 5) {
      const sp = makeLabelSprite(String(c));
      sp.position.set(c, 0.6, -1.6);
      sp.scale.set(1.2, 0.3, 1);
      scene.add(sp);
    }
    // метки рядов
    const rowLabels = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j'];
    for (let r = 0; r < rows; r++) {
      const rr = r >= rows / 2 ? r + centerGap : r;
      const sp = makeLabelSprite(rowLabels[r]);
      sp.position.set(0, 0.6, rr);
      sp.scale.set(0.5, 0.3, 1);
      scene.add(sp);
    }

    // компоненты с анимацией появления и подписями
    const compMeshes: { mesh: THREE.Mesh; label: THREE.Sprite }[] = [];
    layout.components.forEach((comp, idx) => {
      const w = (comp.is_ic ? Math.max(2, comp.pins / 2) : 1.2) * cell;
      const d = comp.is_ic ? 1.4 : 0.7;
      const geo = new THREE.BoxGeometry(w, 0.9, d);
      const mat = new THREE.MeshStandardMaterial({
        color: colorForRef(comp.ref),
        roughness: 0.5,
        metalness: 0.1,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.castShadow = true;
      const pos = comp.positions[0];
      const rr = pos.row >= rows / 2 ? pos.row + centerGap : pos.row;
      mesh.position.set(pos.col, 0.6, rr);
      mesh.scale.setScalar(0);
      scene.add(mesh);

      // подпись refdes
      const label = makeLabelSprite(comp.ref);
      label.position.set(pos.col, 1.4, rr);
      label.scale.set(1.6, 0.4, 1);
      label.visible = false;
      scene.add(label);
      compMeshes.push({ mesh, label });

      const start = performance.now() + idx * 80;
      const dur = 500;
      const tick = () => {
        const t = Math.min(1, (performance.now() - start) / dur);
        if (t < 0) { requestAnimationFrame(tick); return; }
        const e = 1 - Math.pow(1 - t, 3);
        mesh.scale.setScalar(e);
        if (t >= 1) label.visible = true;
        if (t < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });

    // провода (трубки)
    layout.wires.forEach((w) => {
      const [r1, c1] = w.from;
      const [r2, c2] = w.to;
      const rr1 = r1 >= rows / 2 ? r1 + centerGap : r1;
      const rr2 = r2 >= rows / 2 ? r2 + centerGap : r2;
      const pts = [
        new THREE.Vector3(c1, 0.35, rr1),
        new THREE.Vector3((c1 + c2) / 2, 1.5, (rr1 + rr2) / 2),
        new THREE.Vector3(c2, 0.35, rr2),
      ];
      const curve = new THREE.CatmullRomCurve3(pts);
      const geo = new THREE.TubeGeometry(curve, 16, 0.1, 6, false);
      const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0xffcc00, emissive: 0x332200 }));
      scene.add(mesh);
    });

    // легенда (HTML-оверлей через DOM)
    const legend = document.createElement('div');
    legend.style.cssText = 'position:absolute;bottom:8px;left:8px;background:rgba(13,17,23,0.8);color:#e6edf3;font:10px monospace;padding:6px 8px;border-radius:6px;pointer-events:none;';
    legend.innerHTML = '<div style="margin-bottom:4px;font-weight:bold">Легенда</div>' +
      '<div><span style="display:inline-block;width:10px;height:10px;background:#2d5a27;margin-right:4px"></span>IC</div>' +
      '<div><span style="display:inline-block;width:10px;height:10px;background:#1f4e79;margin-right:4px"></span>R</div>' +
      '<div><span style="display:inline-block;width:10px;height:10px;background:#b45309;margin-right:4px"></span>C</div>' +
      '<div><span style="display:inline-block;width:10px;height:10px;background:#ffcc00;margin-right:4px"></span>провод</div>';
    container.style.position = 'relative';
    container.appendChild(legend);

    let raf = 0;
    const animate = () => {
      raf = requestAnimationFrame(animate);
      controls.update();
      scene.rotation.y += 0.0015;
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
      if (container.contains(legend)) container.removeChild(legend);
    };
  }, [layout, height]);

  return <div ref={containerRef} style={{ width: '100%', height }} className="rounded overflow-hidden" />;
}