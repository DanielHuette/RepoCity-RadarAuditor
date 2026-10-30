// The radar of the RadarAuditor website (site/effekte/radar.js) as a backdrop of the Prüfen tab:
// a tilted disc of 2600 points with four rings; a gold beam sweeps it and points glow after it.
// Same shaders, point count and geometry as the site, in the app's gold. Loaded on demand only,
// so three.js stays in its own chunk.
import * as THREE from "three";

const TAU = Math.PI * 2;
const POINTS = 2600;
const RADIUS = 3.2;
const RING_COUNT = 4;
const RING_SEGMENTS = 96;
const RING_SHARE = 0.35;
const RING_JITTER = 0.015;
const SWEEP_PER_SECOND = 0.55;
const MAX_PIXEL_RATIO = 2;
const MAX_FRAME_SECONDS = 0.1;
/** Matches --gold in theme/tokens.css; used only where the token cannot be read. */
export const FALLBACK_GOLD = "#ecc46a";
/** Seen from above at a slant, like a radar screen lying on a table. */
const BASE_TILT = { x: 0.98, z: -0.12 } as const;
/** The pointer tilts the disc gently, never more than about four degrees. */
const POINTER_TILT = { x: -0.07, z: 0.09 } as const;
const TILT_FOLLOW_PER_SECOND = 3;
/** Height of the disc centre above the middle of the content area, as a share of half its height:
 * the centre sits at 46 %, between the address field (36 %) and the button (56 %). */
const CENTRE_RAISE = 0.08;
/** Beam position of the still image shown with reduced motion. */
const STILL_ANGLE = 2.2;
const WIDE_LAYOUT_PX = 900;

const vertexShader = /* glsl */ `
  uniform float uBeamAngle;
  uniform float uPixel;
  uniform float uOpacity;
  attribute float aAngle;
  attribute float aSize;
  varying float vGlow;

  void main() {
    float behindBeam = mod(uBeamAngle - aAngle, 6.28318530718);
    float afterglow = exp(-behindBeam * 1.9);
    vGlow = (0.10 + 0.9 * afterglow) * uOpacity;
    vec4 viewPosition = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = aSize * uPixel * (1.0 + afterglow * 1.6) * (9.0 / -viewPosition.z);
    gl_Position = projectionMatrix * viewPosition;
  }
`;

const fragmentShader = /* glsl */ `
  precision mediump float;
  uniform vec3 uColor;
  varying float vGlow;
  void main() {
    float centreDistance = length(gl_PointCoord - 0.5);
    float disc = smoothstep(0.5, 0.18, centreDistance);
    gl_FragColor = vec4(uColor * vGlow * disc, vGlow * disc);
  }
`;

export interface RadarOptions {
  /** Draw one still image and never animate (prefers-reduced-motion). */
  still: boolean;
}

export interface PointAttributes {
  position: Float32Array;
  angle: Float32Array;
  size: Float32Array;
}

/** Beam angle, current tilt and the pointer position the tilt follows, in -1..1. */
export interface Motion {
  angle: number;
  tiltX: number;
  tiltZ: number;
  pointerX: number;
  pointerY: number;
}

interface RadarParts {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  group: THREE.Group;
  beam: THREE.Line;
  beamAngle: { value: number };
  disposables: { dispose: () => void }[];
}

/** The gold of the theme tokens, so the radar follows tokens.css instead of a copy of its value. */
export function themeGold(element: Element): string {
  const value = getComputedStyle(element).getPropertyValue("--gold").trim();
  return value === "" ? FALLBACK_GOLD : value;
}

function pointDistance(random: () => number): number {
  const distance = Math.sqrt(random());
  // About a third of the points sit on one of the four rings, so the rings glow along.
  if (random() >= RING_SHARE) return distance;
  return Math.round(distance * RING_COUNT) / RING_COUNT + (random() - 0.5) * RING_JITTER;
}

export function scatterPoints(random: () => number = Math.random): PointAttributes {
  const position = new Float32Array(POINTS * 3);
  const angle = new Float32Array(POINTS);
  const size = new Float32Array(POINTS);
  for (let index = 0; index < POINTS; index++) {
    const pointAngle = random() * TAU;
    const distance = pointDistance(random) * RADIUS;
    position[index * 3] = Math.cos(pointAngle) * distance;
    position[index * 3 + 2] = Math.sin(pointAngle) * distance;
    angle[index] = pointAngle;
    size[index] = 1.2 + random() * 1.8;
  }
  return { position, angle, size };
}

function pointCloud(pixelRatio: number, color: string) {
  const points = scatterPoints();
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(points.position, 3));
  geometry.setAttribute("aAngle", new THREE.BufferAttribute(points.angle, 1));
  geometry.setAttribute("aSize", new THREE.BufferAttribute(points.size, 1));
  const uniforms = {
    uBeamAngle: { value: 0 },
    uPixel: { value: pixelRatio },
    uOpacity: { value: 1 },
    uColor: { value: new THREE.Color(color) },
  };
  const material = new THREE.ShaderMaterial({
    uniforms,
    vertexShader,
    fragmentShader,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  return { points: new THREE.Points(geometry, material), geometry, material, beamAngle: uniforms.uBeamAngle };
}

function ringGeometry(radius: number): THREE.BufferGeometry {
  const points: THREE.Vector3[] = [];
  for (let segment = 0; segment <= RING_SEGMENTS; segment++) {
    const angle = (segment / RING_SEGMENTS) * TAU;
    points.push(new THREE.Vector3(Math.cos(angle) * radius, 0, Math.sin(angle) * radius));
  }
  return new THREE.BufferGeometry().setFromPoints(points);
}

function createRenderer(host: HTMLElement, pixelRatio: number): THREE.WebGLRenderer {
  const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: false, powerPreference: "low-power" });
  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(pixelRatio);
  renderer.domElement.setAttribute("aria-hidden", "true");
  host.appendChild(renderer.domElement);
  return renderer;
}

function buildScene(host: HTMLElement, pixelRatio: number): RadarParts {
  const color = themeGold(host);
  const renderer = createRenderer(host, pixelRatio);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 50);
  camera.position.set(0, 0, 9);
  const group = new THREE.Group();
  scene.add(group);

  const cloud = pointCloud(pixelRatio, color);
  group.add(cloud.points);
  const ringMaterial = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.16 });
  const rings = Array.from({ length: RING_COUNT }, (_, ring) => ringGeometry(((ring + 1) / RING_COUNT) * RADIUS));
  rings.forEach((geometry) => group.add(new THREE.Line(geometry, ringMaterial)));
  const beamMaterial = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.55 });
  const beamGeometry = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(RADIUS, 0, 0)]);
  const beam = new THREE.Line(beamGeometry, beamMaterial);
  group.add(beam);

  const disposables = [cloud.geometry, cloud.material, ...rings, ringMaterial, beamGeometry, beamMaterial];
  return { renderer, scene, camera, group, beam, beamAngle: cloud.beamAngle, disposables };
}

function draw(parts: RadarParts, motion: Motion): void {
  parts.beamAngle.value = motion.angle;
  parts.beam.rotation.y = -motion.angle;
  parts.group.rotation.x = BASE_TILT.x + motion.tiltX;
  parts.group.rotation.z = BASE_TILT.z + motion.tiltZ;
  parts.renderer.render(parts.scene, parts.camera);
}

function fitToHost(host: HTMLElement, parts: RadarParts): void {
  const width = Math.max(1, host.clientWidth);
  const height = Math.max(1, host.clientHeight);
  const { renderer, camera, group } = parts;
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  group.scale.setScalar(width > WIDE_LAYOUT_PX ? 1 : 0.8);
  group.position.y = Math.tan((camera.fov * Math.PI) / 360) * camera.position.z * CENTRE_RAISE;
}

/** Moves the beam on by `seconds` and eases the tilt towards the pointer. */
export function advanceMotion(motion: Motion, seconds: number): void {
  const follow = Math.min(1, seconds * TILT_FOLLOW_PER_SECOND);
  motion.angle = (motion.angle + seconds * SWEEP_PER_SECOND) % TAU;
  motion.tiltX += (motion.pointerY * POINTER_TILT.x - motion.tiltX) * follow;
  motion.tiltZ += (motion.pointerX * POINTER_TILT.z - motion.tiltZ) * follow;
}

function trackPointer(motion: Motion): () => void {
  const onPointer = (event: PointerEvent) => {
    motion.pointerX = (event.clientX / window.innerWidth) * 2 - 1;
    motion.pointerY = -((event.clientY / window.innerHeight) * 2 - 1);
  };
  window.addEventListener("pointermove", onPointer, { passive: true });
  return () => {
    window.removeEventListener("pointermove", onPointer);
  };
}

/** Draws one frame per display refresh while running; the first frame after a pause does not jump. */
function frameLoop(parts: RadarParts, motion: Motion): { run: () => void; pause: () => void } {
  let frame = 0;
  let lastFrame = 0;
  const tick = (now: number) => {
    advanceMotion(motion, lastFrame === 0 ? 0 : Math.min(MAX_FRAME_SECONDS, (now - lastFrame) / 1000));
    lastFrame = now;
    draw(parts, motion);
    frame = requestAnimationFrame(tick);
  };
  const run = () => {
    if (frame !== 0 || document.hidden) return;
    lastFrame = 0;
    frame = requestAnimationFrame(tick);
  };
  const pause = () => {
    cancelAnimationFrame(frame);
    frame = 0;
  };
  return { run, pause };
}

/** Runs the sweep while the page is visible; returns the function that stops it for good. */
function animate(parts: RadarParts, motion: Motion): () => void {
  const { run, pause } = frameLoop(parts, motion);
  const onVisibility = () => {
    if (document.hidden) pause();
    else run();
  };
  document.addEventListener("visibilitychange", onVisibility);
  const stopTracking = trackPointer(motion);
  run();
  return () => {
    pause();
    document.removeEventListener("visibilitychange", onVisibility);
    stopTracking();
  };
}

function disposeScene(parts: RadarParts): void {
  parts.disposables.forEach((resource) => {
    resource.dispose();
  });
  parts.renderer.dispose();
  // dispose() keeps the WebGL context; release it, or every visit of Prüfen leaves one behind.
  parts.renderer.forceContextLoss();
  parts.renderer.domElement.remove();
}

/** Starts the radar in `host` and returns the function that stops it and frees the GPU resources. */
export function startRadar(host: HTMLElement, options: RadarOptions): () => void {
  const parts = buildScene(host, Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO));
  const motion: Motion = { angle: options.still ? STILL_ANGLE : 0, tiltX: 0, tiltZ: 0, pointerX: 0, pointerY: 0 };
  const observer = new ResizeObserver(() => {
    fitToHost(host, parts);
    draw(parts, motion);
  });
  observer.observe(host);
  fitToHost(host, parts);
  draw(parts, motion);
  const stopAnimation = options.still ? null : animate(parts, motion);
  return () => {
    stopAnimation?.();
    observer.disconnect();
    disposeScene(parts);
  };
}
