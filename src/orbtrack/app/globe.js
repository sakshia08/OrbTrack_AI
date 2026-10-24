// Live globe: globe.gl for the Earth, plus one GPU point cloud for the whole catalog.
//
// The server pushes two keyframes of positions (now and a few seconds ahead); the
// vertex shader interpolates between them every frame, so tens of thousands of
// satellites move smoothly without any per-satellite JavaScript.

const STATIC = "/orbtrack-static";
const R = 100; // globe.gl Earth radius in scene units

// Same order as ORBIT_CLASSES in frames.py: LEO, STARLINK, MEO, GEO, HEO
const CLASS_COLORS = ["#38e1ff", "#9b8cff", "#ffc24b", "#ff5fa2", "#9dff6a"];
const CLASS_SIZES = [1.0, 0.75, 1.25, 1.5, 1.25];

const EARTH_VERTEX = `
  varying vec2 vUv;
  varying vec3 vNormalW;
  varying vec3 vPosW;
  void main() {
    vUv = uv;
    vNormalW = normalize(mat3(modelMatrix) * normal);
    vec4 world = modelMatrix * vec4(position, 1.0);
    vPosW = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }`;

const EARTH_FRAGMENT = `
  uniform sampler2D dayMap;
  uniform sampler2D nightMap;
  uniform sampler2D waterMap;
  uniform vec3 sunDir;
  varying vec2 vUv;
  varying vec3 vNormalW;
  varying vec3 vPosW;
  void main() {
    // Lighting is done in linear colour space, so effect strengths are small numbers.
    vec3 n = normalize(vNormalW);
    vec3 viewDir = normalize(cameraPosition - vPosW);
    float sun = dot(n, sunDir);
    float dayMix = smoothstep(-0.06, 0.10, sun);

    vec3 day = texture2D(dayMap, vUv).rgb;
    day *= 0.18 + 1.0 * clamp(sun, 0.0, 1.0);                     // Lambert, with a little skylight
    float water = texture2D(waterMap, vUv).r;
    float glint = pow(max(dot(reflect(-sunDir, n), viewDir), 0.0), 80.0) * water;
    day += vec3(1.0, 0.92, 0.8) * glint * 0.18;
    float rim = pow(1.0 - max(dot(n, viewDir), 0.0), 3.0);
    day += vec3(0.25, 0.55, 1.0) * rim * 0.22 * clamp(sun + 0.2, 0.0, 1.0); // atmospheric haze at the limb

    vec3 nightTex = texture2D(nightMap, vUv).rgb;
    float lum = dot(nightTex, vec3(0.299, 0.587, 0.114));
    vec3 cities = vec3(1.0, 0.62, 0.28) * smoothstep(0.06, 0.45, lum) * 1.3;
    vec3 night = nightTex * vec3(0.10, 0.12, 0.18) + cities;

    vec3 color = mix(night, day, dayMix);
    float twilight = exp(-pow(sun / 0.05, 2.0));                    // thin warm line on the terminator
    color += vec3(1.0, 0.4, 0.12) * twilight * 0.015;
    gl_FragColor = vec4(color, 1.0);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }`;

// Comet tail: a tube around one orbit, faded by how long ago the satellite was at each point.
// Its width scales with distance to the camera, so it stays a few pixels wide at any zoom.
const TAIL_VERTEX = `
  attribute vec3 center;
  uniform float uDuration;
  uniform float uWidth;
  varying float vT;
  void main() {
    vT = uv.x * uDuration;
    vec4 wc = modelMatrix * vec4(center, 1.0);
    float d = length(cameraPosition - wc.xyz);
    vec3 p = center + (position - center) * d * uWidth;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
  }`;

const TAIL_FRAGMENT = `
  uniform vec3 color;
  uniform float uNow;
  uniform float uPeriod;
  uniform float uTail;
  varying float vT;
  void main() {
    float age = mod(uNow - vT, uPeriod);
    float a = age < uTail ? pow(1.0 - age / uTail, 1.6) : 0.0;
    if (a < 0.003) discard;
    gl_FragColor = vec4(mix(color, vec3(1.0), a * a * 0.6) * a, a);
  }`;

// The orbit ahead: dashes along the rest of the ring, brightest just in front of the satellite.
const AHEAD_VERTEX = `
  attribute float aT;
  varying float vT;
  void main() {
    vT = aT;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }`;

const AHEAD_FRAGMENT = `
  uniform vec3 color;
  uniform float uNow;
  uniform float uPeriod;
  uniform float uTail;
  varying float vT;
  void main() {
    float age = mod(uNow - vT, uPeriod);
    float ahead = uPeriod - age;
    float dash = step(0.45, fract(vT / (uPeriod / 140.0)));
    float a = dash * 0.5 * (1.0 - 0.65 * ahead / uPeriod) * smoothstep(0.6 * uTail, uTail, age);
    if (a < 0.01) discard;
    gl_FragColor = vec4(color, a);
  }`;

// Satellites: slerp between two inertial keyframes, then the parent group turns them with
// the Earth. Points in the Earth's shadow are dimmed.
const SAT_VERTEX = `
  attribute vec3 posA;
  attribute vec3 posB;
  attribute vec3 tint;
  attribute float size;
  attribute float cls;
  uniform float uT;
  uniform float uPixelRatio;
  uniform float uSizeScale;
  uniform float uVisible[5];
  uniform float uFade;
  uniform vec3 uSun;
  varying vec3 vTint;
  void main() {
    float ra = length(posA), rb = length(posB);
    if (ra < 1.0) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); gl_PointSize = 0.0; return; }
    vec3 ua = posA / ra, ub = posB / rb;
    float w = atan(length(cross(ua, ub)), dot(ua, ub));
    vec3 u = w < 1e-5 ? mix(ua, ub, uT) : (sin((1.0 - uT) * w) * ua + sin(uT * w) * ub) / sin(w);
    vec3 p = normalize(u) * mix(ra, rb, uT);
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;

    float along = dot(p, uSun);
    float lit = along > 0.0 ? 1.0 : smoothstep(99.0, 101.5, length(p - along * uSun));
    float visible = uVisible[int(cls + 0.5)];
    float px = size * uSizeScale * uPixelRatio * 1400.0 / -mv.z;
    gl_PointSize = visible * mix(0.8, 1.0, lit) * clamp(px, 2.2 * uPixelRatio, 10.0 * uPixelRatio);
    vTint = tint * mix(0.3, 1.0, lit) * uFade;
  }`;

const SAT_FRAGMENT = `
  varying vec3 vTint;
  void main() {
    float d = length(gl_PointCoord - 0.5) * 2.0;
    if (d > 1.0) discard;
    float core = smoothstep(0.45, 0.0, d);
    float halo = pow(1.0 - d, 2.5) * 0.55;
    gl_FragColor = vec4(vTint * (core + halo) + vTint * core * 0.35, core + halo);
  }`;

// Your sky: a dome around the observer, blue by day, warm at twilight, clear at night.
const SKY_VERTEX = `
  varying vec3 vDir;
  void main() {
    vDir = normalize((modelMatrix * vec4(position, 0.0)).xyz);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }`;

const SKY_FRAGMENT = `
  uniform vec3 uUp;
  uniform vec3 uSun;
  varying vec3 vDir;
  void main() {
    vec3 d = normalize(vDir);
    float el = dot(d, uUp);
    float sunAlt = dot(uSun, uUp);
    float day = smoothstep(-0.1, 0.12, sunAlt);
    float twilight = exp(-pow((sunAlt + 0.04) / 0.1, 2.0));
    float toSun = max(dot(d, uSun), 0.0);
    float h = pow(1.0 - clamp(el, 0.0, 1.0), 4.0);

    vec3 sky = mix(vec3(0.06, 0.22, 0.62), vec3(0.42, 0.62, 0.92), h) * day;
    sky += vec3(1.0, 0.42, 0.16) * twilight * h * (0.25 + 0.75 * pow(toSun, 4.0));
    sky += vec3(1.0, 0.9, 0.7) * pow(toSun, 600.0) * day * 3.0;   // the Sun itself
    sky += vec3(0.03, 0.08, 0.2) * h * (1.0 - day);                // faint airglow at night
    float a = clamp(day * 0.5 + twilight * h * 0.45 + h * 0.2, 0.0, 0.75);
    gl_FragColor = vec4(sky / max(a, 0.001), a);
    #include <colorspace_fragment>
  }`;

const OMEGA_EARTH = 7.2921158553e-5; // rad/s, sidereal
const DEG = Math.PI / 180;
const SKY_SIZE_SCALE = 0.06; // satellites are much closer to the camera when looking up from the ground
const REPLAY_CYCLE_MS = 9000; // one loop of a replay: half an orbit before the burn, half after
const REPLAY_FADE = 0.3; // the rest of the swarm steps back while a replay runs

function glowTexture(THREE) {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d");
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, "rgba(255, 255, 255, 1)");
  grad.addColorStop(0.25, "rgba(255, 255, 255, 0.85)");
  grad.addColorStop(1, "rgba(255, 255, 255, 0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 64, 64);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

const BEACON_PERIOD_MS = 2200;

function decode(b64, Type) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Type(bytes.buffer);
}

function ringTexture(THREE) {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d");
  g.strokeStyle = "rgba(120, 240, 255, 0.95)";
  g.lineWidth = 6;
  g.beginPath();
  g.arc(64, 64, 52, 0, Math.PI * 2);
  g.stroke();
  g.globalAlpha = 0.35;
  g.lineWidth = 14;
  g.beginPath();
  g.arc(64, 64, 52, 0, Math.PI * 2);
  g.stroke();
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

// A text label that keeps the same size on screen.
// `size` is the font size in CSS pixels; the canvas is drawn at 3x so it stays crisp.
// `fov` is the camera's: on-screen size also scales with 1 / tan(fov / 2). Sizes were tuned
// under the 80° sky camera, so that one is the reference.
function textSprite(THREE, text, { color = "#cfe6ff", size = 13, weight = 600, opacity = 1, fov = 80 } = {}) {
  const k = 3;
  const c = document.createElement("canvas");
  const g = c.getContext("2d");
  const font = `${weight} ${size * k}px "JetBrains Mono", ui-monospace, monospace`;
  g.font = font;
  c.width = Math.ceil(g.measureText(text).width) + 8 * k;
  c.height = (size + 8) * k;
  g.font = font;
  g.textBaseline = "middle";
  g.shadowColor = "rgba(0, 0, 0, 0.85)";
  g.shadowBlur = 3 * k;
  g.fillStyle = color;
  g.fillText(text, 4 * k, c.height / 2);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: t, transparent: true, depthWrite: false, sizeAttenuation: false, opacity })
  );
  // With sizeAttenuation off, a scale of 1 spans half the viewport height; keep CSS pixels instead.
  const h = ((2 * c.height) / k / window.innerHeight) * (Math.tan((fov / 2) * DEG) / Math.tan(40 * DEG));
  sprite.scale.set((h * c.width) / c.height, h, 1);
  sprite.renderOrder = 6;
  return sprite;
}

// The simulated clock is a line: sim = sim0 + (real - real0) * rate. The server sends the
// three numbers; the browser estimates how far its own clock is from the server's.
class SimClock {
  constructor() {
    this.state = { sim0_ms: Date.now(), real0_ms: Date.now(), rate: 1 };
    this.samples = [];
    this.skew = 0;
  }
  set(state, sentMs) {
    this.state = state;
    if (sentMs !== undefined) {
      this.samples.push(sentMs - Date.now()); // = skew - latency, so the max is the best guess
      if (this.samples.length > 20) this.samples.shift();
      this.skew = Math.max(...this.samples);
    }
  }
  real() {
    return Date.now() + this.skew;
  }
  now() {
    const s = this.state;
    return s.sim0_ms + (this.real() - s.real0_ms) * s.rate;
  }
}

export default {
  template: `
    <div class="orbtrack-globe-root">
      <div ref="canvas" class="orbtrack-globe-canvas"></div>
      <div ref="tip" class="orbtrack-tip"></div>
      <div ref="flash" class="orbtrack-flash"></div>
    </div>`,

  created() {
    this.ready = new Promise((resolve) => (this.markReady = resolve));
  },

  async mounted() {
    const THREE = await import(`${STATIC}/vendor/three.module.min.js`);
    window.THREE = THREE; // globe.gl builds its objects from this copy when present
    await import(`${STATIC}/vendor/globe.gl.min.js`);
    this.THREE = THREE;

    const el = this.$refs.canvas;
    const loader = new THREE.TextureLoader();
    const tex = (name) => {
      const t = loader.load(`${STATIC}/textures/${name}`);
      t.colorSpace = THREE.SRGBColorSpace;
      t.anisotropy = 8;
      return t;
    };
    this.earthMaterial = new THREE.ShaderMaterial({
      uniforms: {
        dayMap: { value: tex("earth-blue-marble.jpg") },
        nightMap: { value: tex("earth-night.jpg") },
        waterMap: { value: loader.load(`${STATIC}/textures/earth-water.png`) },
        sunDir: { value: new THREE.Vector3(1, 0, 0) },
      },
      vertexShader: EARTH_VERTEX,
      fragmentShader: EARTH_FRAGMENT,
    });

    const globe = new window.Globe(el, {
      animateIn: false,
      rendererConfig: { antialias: true, powerPreference: "high-performance" },
    })
      .width(el.clientWidth)
      .height(el.clientHeight)
      .backgroundColor("#02040a")
      .backgroundImageUrl(`${STATIC}/textures/night-sky.png`)
      .globeMaterial(this.earthMaterial)
      .showAtmosphere(true)
      .atmosphereColor("#3d9bff")
      .atmosphereAltitude(0.16)
      .pointOfView({ lat: 18, lng: 40, altitude: 2.6 });
    this.globe = globe;
    this.rings = [];
    // Beacons where events happened: they turn with the Earth, so they hang off the scene itself.
    this.beacons = new THREE.Group();
    globe.scene().add(this.beacons);
    globe.controls().autoRotate = false;
    globe.controls().enableDamping = true;

    new ResizeObserver(() => globe.width(el.clientWidth).height(el.clientHeight)).observe(el);

    // Everything that lives in inertial space hangs off this group, turned by the sidereal angle.
    this.orbitFrame = new THREE.Group();
    globe.scene().add(this.orbitFrame);

    this.marker = new THREE.Sprite(
      new THREE.SpriteMaterial({ map: ringTexture(THREE), depthTest: false, transparent: true, sizeAttenuation: false })
    );
    this.marker.scale.set(0.045, 0.045, 1);
    this.marker.visible = false;
    this.marker.renderOrder = 10;
    this.orbitFrame.add(this.marker);

    this.clock = new SimClock();
    this.sunTeme = new THREE.Vector3(1, 0, 0);
    this.sunWorld = new THREE.Vector3(1, 0, 0);
    this.theta = 0;
    this.selected = -1;
    this.following = false;
    this.sky = null;
    this.firstFrameSent = false;
    this.tmp = { x: 0, y: 0, z: 0 };
    this.bindPointer();
    this.markReady();
    this.$emit("ready");
    this.loop();
  },

  methods: {
    // "ready" is also sent from mounted(); this covers a socket that connected after that.
    async hello() {
      await this.ready;
      this.$emit("ready");
    },

    // Called again whenever the server refreshes the catalog (every 2 h); the old swarm is replaced.
    async initCatalog(payload) {
      await this.ready;
      const THREE = this.THREE;
      const keepVisible = this.satMaterial ? [...this.satMaterial.uniforms.uVisible.value] : [1, 1, 1, 1, 1];
      if (this.points) {
        this.orbitFrame.remove(this.points);
        this.points.geometry.dispose();
        this.satMaterial.dispose();
        this.points = null;
        this.frame = null; // wait for a frame that matches the new catalog
      }
      const n = payload.count;
      this.count = n;
      this.ids = decode(payload.ids, Int32Array);
      this.classes = decode(payload.classes, Uint8Array);
      this.names = payload.names.split("\n");

      const tint = new Float32Array(n * 3);
      const size = new Float32Array(n);
      const color = new THREE.Color();
      for (let i = 0; i < n; i++) {
        color.set(CLASS_COLORS[this.classes[i]]);
        tint.set([color.r, color.g, color.b], i * 3);
        size[i] = CLASS_SIZES[this.classes[i]];
      }
      this.posA = new Float32Array(n * 3);
      this.posB = new Float32Array(n * 3);

      const geometry = new THREE.BufferGeometry();
      this.attrA = new THREE.BufferAttribute(this.posA, 3);
      this.attrB = new THREE.BufferAttribute(this.posB, 3);
      geometry.setAttribute("position", this.attrA);
      geometry.setAttribute("posA", this.attrA);
      geometry.setAttribute("posB", this.attrB);
      geometry.setAttribute("tint", new THREE.BufferAttribute(tint, 3));
      geometry.setAttribute("size", new THREE.BufferAttribute(size, 1));
      geometry.setAttribute("cls", new THREE.BufferAttribute(new Float32Array(this.classes), 1));

      this.satMaterial = new THREE.ShaderMaterial({
        uniforms: {
          uT: { value: 0 },
          uPixelRatio: { value: Math.min(window.devicePixelRatio, 2) },
          uSizeScale: { value: this.sky ? SKY_SIZE_SCALE : 1 },
          uVisible: { value: keepVisible },
          uFade: { value: this.replayState ? REPLAY_FADE : 1 },
          uSun: { value: this.sunTeme },
        },
        vertexShader: SAT_VERTEX,
        fragmentShader: SAT_FRAGMENT,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      });
      this.points = new THREE.Points(geometry, this.satMaterial);
      this.points.frustumCulled = false;
      this.points.renderOrder = 2;
      this.orbitFrame.add(this.points);
    },

    async pushFrame(frame) {
      await this.ready;
      if (!this.points) return;
      this.posA.set(decode(frame.a, Float32Array));
      this.posB.set(decode(frame.b, Float32Array));
      this.attrA.needsUpdate = true;
      this.attrB.needsUpdate = true;
      this.frame = { a: frame.a_ms, b: frame.b_ms, theta: frame.theta };
      this.sunTeme.set(...frame.sun);
      this.clock.set(frame.clock, frame.sent_ms);
      if (!this.firstFrameSent) {
        this.firstFrameSent = true;
        requestAnimationFrame(() => this.$emit("first_frame", { ms: Math.round(performance.now()) }));
      }
    },

    setClassVisible(index, visible) {
      if (this.satMaterial) this.satMaterial.uniforms.uVisible.value[index] = visible ? 1 : 0;
    },

    // --- Events --------------------------------------------------------------

    async setEvents(rings) {
      await this.ready;
      this.rings = rings;
      this.showRings();
    },

    // A glowing dot and two rings that spread and fade, flat on the ground at each event.
    showRings() {
      const THREE = this.THREE;
      for (const obj of [...this.beacons.children]) {
        this.beacons.remove(obj);
        obj.traverse((o) => {
          o.geometry?.dispose();
          o.material?.dispose();
        });
      }
      this.beacons.visible = !this.sky;
      const active = this.replayState?.key;
      this.glowTex = this.glowTex || glowTexture(THREE);
      this.rings.forEach((r, k) => {
        const big = r.key === active ? 1.8 : 1;
        const color = new THREE.Color(r.color);
        const beacon = new THREE.Group();
        const pos = this.globe.getCoords(r.lat, r.lon, 0.004);
        beacon.position.set(pos.x, pos.y, pos.z);
        beacon.lookAt(pos.x * 2, pos.y * 2, pos.z * 2);
        const dot = new THREE.Sprite(
          new THREE.SpriteMaterial({ map: this.glowTex, color, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending })
        );
        dot.scale.set(2.2 * big, 2.2 * big, 1);
        beacon.add(dot);
        beacon.userData = { rings: [], phase: (k * 0.37) % 1, big };
        for (let j = 0; j < 2; j++) {
          const ring = new THREE.Mesh(
            new THREE.RingGeometry(0.86, 1, 48),
            new THREE.MeshBasicMaterial({ color, transparent: true, depthWrite: false, side: THREE.DoubleSide, blending: THREE.AdditiveBlending })
          );
          ring.renderOrder = 5;
          beacon.add(ring);
          beacon.userData.rings.push(ring);
        }
        this.beacons.add(beacon);
      });
    },

    updateBeacons() {
      const now = performance.now() / BEACON_PERIOD_MS;
      for (const b of this.beacons.children) {
        const { rings, phase, big } = b.userData;
        rings.forEach((ring, j) => {
          const t = (now + phase + j / rings.length) % 1;
          const r = (0.6 + 5.4 * t) * big;
          ring.scale.set(r, r, 1);
          ring.material.opacity = Math.pow(1 - t, 1.6) * 0.95;
        });
      }
    },

    // Both orbits, frozen against the Earth at the moment of the event, and a ghost that flies
    // the old one into the burn and carries on along the new one.
    async replay(p) {
      await this.ready;
      const THREE = this.THREE;
      this.clearReplay();
      if (this.selected >= 0) this.release(false);
      const group = new THREE.Group();
      const v = (a) => new THREE.Vector3(a[0], a[1], a[2]);
      const before = p.before.map(v), after = p.after.map(v);
      const color = new THREE.Color(p.color);
      const tube = (pts, radius, mat) => {
        const curve = new THREE.CatmullRomCurve3(pts, true);
        const mesh = new THREE.Mesh(new THREE.TubeGeometry(curve, pts.length * 2, radius, 6, true), mat);
        mesh.renderOrder = 7;
        group.add(mesh);
        return mesh;
      };
      const w = Math.max(p.radius / 106, 1);
      const oldLine = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(before),
        new THREE.LineDashedMaterial({ color: "#a9bddc", dashSize: 1.2 * w, gapSize: 0.9 * w, transparent: true, opacity: 0.7, depthWrite: false })
      );
      oldLine.computeLineDistances();
      oldLine.renderOrder = 7;
      group.add(oldLine);
      const glow = (opacity) =>
        new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthWrite: false, blending: THREE.AdditiveBlending });
      const newCore = tube(after, 0.22 * w, glow(0.95));
      const newHalo = tube(after, 0.8 * w, glow(0.18));

      const tex = glowTexture(THREE);
      const sprite = (scale, opacity, tint = color) => {
        const s = new THREE.Sprite(
          new THREE.SpriteMaterial({ map: tex, color: tint, transparent: true, opacity, depthWrite: false, sizeAttenuation: false, blending: THREE.AdditiveBlending })
        );
        s.scale.set(scale, scale, 1);
        s.renderOrder = 9;
        group.add(s);
        return s;
      };
      const fovK = Math.tan((this.globe.camera().fov / 2) * DEG); // keep sprites a steady size on screen
      const ghost = sprite(0.022 * fovK, 1, new THREE.Color("#ffffff"));
      const ghostHalo = sprite(0.06 * fovK, 0.55);
      const burst = sprite(0.05 * fovK, 0);
      burst.position.copy(before[0]);
      const burnDot = sprite(0.02 * fovK, 0.9);
      burnDot.position.copy(before[0]);

      // `cx`, `cy`: which point of the label sits on the spot (0.5, -0.6 = centred above it).
      const label = (text, at, color, size, cx, cy) => {
        const s = textSprite(THREE, text, { color, size, fov: this.globe.camera().fov });
        s.position.copy(at);
        s.center.set(cx, cy);
        s.renderOrder = 10;
        s.material.depthTest = false; // text stays readable where it crosses the Earth's edge
        group.add(s);
      };
      label(p.burn_label, before[0], "#ffffff", 12, 0.5, -0.7);
      if (p.kind !== "decay") {
        label("BEFORE", before[p.far], "#a9bddc", 11, 1.1, 0.5); // left of the spot
        label("AFTER", after[p.far], p.color, 11, -0.1, 0.5); // right of it
      }

      this.scene().add(group);
      this.replayState = {
        key: p.key, group, before, after, ghost, ghostHalo, burst, newCore, newHalo, fovK,
        start: performance.now(), lastPhase: 0, decay: p.kind === "decay",
      };
      if (this.satMaterial) this.satMaterial.uniforms.uFade.value = REPLAY_FADE;
      this.showRings();

      // High orbits: a three-quarter view from above the plane, towards where the orbits part most.
      // Low ones hug the Earth and would hide behind its edge from above, so look from the side:
      // both orbits cross the Earth's face and spread apart from the burn towards the far side.
      const n = v(p.normal).normalize();
      const far = before[p.far].clone().normalize();
      let dir, altitude;
      if (p.radius > 2 * R) {
        dir = n.multiplyScalar(0.72).add(far.multiplyScalar(0.7)).normalize();
        altitude = (p.radius * 2.9) / R - 1;
      } else {
        const side = new THREE.Vector3().crossVectors(n, far).normalize();
        dir = n.multiplyScalar(0.36).add(side.multiplyScalar(0.93)).normalize();
        altitude = 2.9;
      }
      const geo = this.globe.toGeoCoords(dir.multiplyScalar(R * 2));
      const controls = this.globe.controls();
      controls.enableRotate = true;
      controls.enableDamping = true;
      this.globe.pointOfView({ lat: geo.lat, lng: geo.lng, altitude }, 1800);
    },

    clearReplay() {
      const r = this.replayState;
      if (!r) return;
      this.scene().remove(r.group);
      r.group.traverse((o) => {
        o.geometry?.dispose();
        o.material?.map?.dispose();
        o.material?.dispose();
      });
      this.replayState = null;
    },

    endReplay() {
      if (!this.replayState) return;
      this.clearReplay();
      if (this.satMaterial) this.satMaterial.uniforms.uFade.value = 1;
      this.showRings();
      const pov = this.globe.pointOfView();
      this.globe.pointOfView({ lat: pov.lat, lng: pov.lng, altitude: 2.6 }, 1400);
    },

    scene() {
      return this.globe.scene();
    },

    updateReplay() {
      const r = this.replayState;
      const s = ((performance.now() - r.start) % REPLAY_CYCLE_MS) / REPLAY_CYCLE_MS;
      const along = (pts, f) => {
        const x = f * (pts.length - 1), i = Math.min(Math.floor(x), pts.length - 2);
        return pts[i].clone().lerp(pts[i + 1], x - i);
      };
      const onNew = s >= 0.5 && !r.decay;
      const p = onNew ? along(r.after, s - 0.5) : along(r.before, r.decay ? s : 0.5 + s);
      r.ghost.position.copy(p);
      r.ghostHalo.position.copy(p);
      // The new orbit lights up once the ghost is on it.
      const lit = onNew ? 1 : 0.45;
      r.newCore.material.opacity += (0.9 * lit - r.newCore.material.opacity) * 0.08;
      r.newHalo.material.opacity += (0.16 * lit - r.newHalo.material.opacity) * 0.08;
      if (s >= 0.5 && r.lastPhase < 0.5) r.burstAt = performance.now();
      r.lastPhase = s;
      const age = r.burstAt ? (performance.now() - r.burstAt) / 1100 : 1;
      r.burst.material.opacity = age < 1 ? Math.pow(1 - age, 1.5) : 0;
      const k = (0.04 + 0.22 * Math.min(age, 1)) * r.fovK;
      r.burst.scale.set(k, k, 1);
    },

    async select(noradId, trail) {
      await this.ready;
      const index = this.ids ? this.ids.indexOf(noradId) : -1;
      this.selected = index;
      this.marker.visible = index >= 0;
      this.setTrail(trail);
      if (index < 0) return;
      if (this.sky) this.sky.track = true;
      else this.startFollow();
    },

    setTrail(trail) {
      const THREE = this.THREE;
      for (const obj of this.trailObjects || []) {
        this.orbitFrame.remove(obj);
        obj.geometry.dispose();
        obj.material.dispose();
      }
      this.trailObjects = [];
      this.trail = trail;
      if (!trail) return;

      const pts = trail.points.map(([x, y, z]) => new THREE.Vector3(x, y, z));
      const duration = trail.step_s * (pts.length - 1);
      const shared = {
        uNow: { value: 0 },
        uPeriod: { value: trail.period_s },
        uTail: { value: trail.tail_s },
      };
      this.trailUniforms = shared;

      // Points are evenly spaced in time, so walk the curve by its own parameter, not arc length.
      const curve = new THREE.CatmullRomCurve3(pts);
      curve.getPointAt = (u, target) => curve.getPoint(u, target);
      curve.getTangentAt = (u, target) => curve.getTangent(u, target);
      const segments = (pts.length - 1) * 2, radial = 6;
      const geometry = new THREE.TubeGeometry(curve, segments, 1, radial, false);
      const center = new Float32Array((segments + 1) * (radial + 1) * 3);
      const c = new THREE.Vector3();
      for (let i = 0; i <= segments; i++) {
        curve.getPoint(i / segments, c);
        for (let j = 0; j <= radial; j++) c.toArray(center, (i * (radial + 1) + j) * 3);
      }
      geometry.setAttribute("center", new THREE.BufferAttribute(center, 3));
      const tail = new THREE.Mesh(
        geometry,
        new THREE.ShaderMaterial({
          uniforms: {
            ...shared,
            color: { value: new THREE.Color("#7ef3ff") },
            uDuration: { value: duration },
            uWidth: { value: 0.0024 },
          },
          vertexShader: TAIL_VERTEX,
          fragmentShader: TAIL_FRAGMENT,
          transparent: true,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
        })
      );

      const lineGeometry = new THREE.BufferGeometry().setFromPoints(pts);
      lineGeometry.setAttribute("aT", new THREE.BufferAttribute(Float32Array.from(pts, (_, i) => i * trail.step_s), 1));
      const ahead = new THREE.Line(
        lineGeometry,
        new THREE.ShaderMaterial({
          uniforms: { ...shared, color: { value: new THREE.Color("#7ecbff") } },
          vertexShader: AHEAD_VERTEX,
          fragmentShader: AHEAD_FRAGMENT,
          transparent: true,
          depthWrite: false,
        })
      );
      for (const obj of [tail, ahead]) {
        obj.frustumCulled = false;
        obj.renderOrder = 3;
        this.orbitFrame.add(obj);
        this.trailObjects.push(obj);
      }
    },

    release(moveCamera = true) {
      this.selected = -1;
      this.following = false;
      this.marker.visible = false;
      this.setTrail(null);
      clearTimeout(this.followTimer);
      if (this.sky) {
        this.sky.track = false;
        return;
      }
      const controls = this.globe.controls();
      controls.enableRotate = true;
      controls.enableDamping = true;
      if (!moveCamera) return;
      const pov = this.globe.pointOfView();
      this.globe.pointOfView({ lat: pov.lat, lng: pov.lng, altitude: 2.6 }, 1200);
    },

    startFollow() {
      const geo = this.globe.toGeoCoords(this.worldPosition(this.selected));
      const altitude = Math.max(geo.altitude + 0.9, 1.1);
      const controls = this.globe.controls();
      controls.enableRotate = false;
      controls.enableDamping = false;
      this.following = false;
      this.globe.pointOfView({ lat: geo.lat, lng: geo.lng, altitude }, 1400);
      clearTimeout(this.followTimer);
      this.followTimer = setTimeout(() => (this.following = this.selected >= 0 && !this.sky), 1400);
    },

    // --- Time --------------------------------------------------------------

    fraction() {
      if (!this.frame) return 0;
      const f = (this.clock.now() - this.frame.a) / (this.frame.b - this.frame.a);
      return Math.min(Math.max(f, 0), 1.6);
    },

    // Satellite i in the inertial frame, slerped like the vertex shader does it.
    localPosition(i, t, out) {
      const A = this.posA, B = this.posB, k = i * 3;
      const ax = A[k], ay = A[k + 1], az = A[k + 2], bx = B[k], by = B[k + 1], bz = B[k + 2];
      const ra = Math.hypot(ax, ay, az), rb = Math.hypot(bx, by, bz);
      if (ra < 1) return null;
      const cx = ay * bz - az * by, cy = az * bx - ax * bz, cz = ax * by - ay * bx;
      const w = Math.atan2(Math.hypot(cx, cy, cz), ax * bx + ay * by + az * bz);
      let wa = 1 - t, wb = t;
      if (w > 1e-6) {
        const s = Math.sin(w);
        wa = Math.sin((1 - t) * w) / s;
        wb = Math.sin(t * w) / s;
      }
      const x = (wa * ax) / ra + (wb * bx) / rb;
      const y = (wa * ay) / ra + (wb * by) / rb;
      const z = (wa * az) / ra + (wb * bz) / rb;
      const r = (ra + (rb - ra) * t) / Math.hypot(x, y, z);
      out.x = x * r;
      out.y = y * r;
      out.z = z * r;
      return out;
    },

    // Inertial -> Earth-fixed, the same turn as orbitFrame.rotation.y = -theta.
    toWorld(p, out) {
      const c = Math.cos(this.theta), s = Math.sin(this.theta);
      const x = p.x, z = p.z;
      out.x = c * x - s * z;
      out.y = p.y;
      out.z = s * x + c * z;
      return out;
    },

    worldPosition(i) {
      const p = this.localPosition(i, this.fraction(), { x: 0, y: 0, z: 0 });
      return p ? this.toWorld(p, p) : { x: 0, y: 0, z: 0 };
    },

    loop() {
      requestAnimationFrame(() => this.loop());
      if (!this.points || !this.frame) return;
      const simNow = this.clock.now();
      const t = this.fraction();
      this.theta = this.frame.theta + (OMEGA_EARTH * (simNow - this.frame.a)) / 1000;
      this.orbitFrame.rotation.y = -this.theta;
      this.satMaterial.uniforms.uT.value = t;
      this.toWorld(this.sunTeme, this.sunWorld);
      this.earthMaterial.uniforms.sunDir.value.copy(this.sunWorld);
      if (this.trail) this.trailUniforms.uNow.value = (simNow - this.trail.t0_ms) / 1000;

      if (this.selected >= 0) {
        const p = this.localPosition(this.selected, t, this.tmp);
        if (p) this.marker.position.set(p.x, p.y, p.z);
        if (this.following) {
          const geo = this.globe.toGeoCoords(this.worldPosition(this.selected));
          const altitude = this.globe.camera().position.length() / R - 1; // keep the user's zoom
          this.globe.pointOfView({ lat: geo.lat, lng: geo.lng, altitude }, 0);
        }
      }
      if (this.sky) this.updateSky();
      if (this.replayState) this.updateReplay();
      if (this.beacons.visible && this.beacons.children.length) this.updateBeacons();
    },

    // --- Your sky ----------------------------------------------------------

    skyDir(altDeg, azDeg) {
      const f = this.obs, alt = altDeg * DEG, az = azDeg * DEG;
      const h = Math.cos(alt), s = Math.sin(alt);
      return new this.THREE.Vector3(
        h * (Math.sin(az) * f.east[0] + Math.cos(az) * f.north[0]) + s * f.up[0],
        h * (Math.sin(az) * f.east[1] + Math.cos(az) * f.north[1]) + s * f.up[1],
        h * (Math.sin(az) * f.east[2] + Math.cos(az) * f.north[2]) + s * f.up[2]
      );
    },

    buildDome() {
      const THREE = this.THREE;
      const D = 4; // dome radius in scene units; only directions matter from its centre
      const dome = new THREE.Group();
      const sky = new THREE.Mesh(
        new THREE.SphereGeometry(D * 0.9, 48, 24),
        new THREE.ShaderMaterial({
          uniforms: { uUp: { value: new THREE.Vector3(...this.obs.up) }, uSun: { value: this.sunWorld } },
          vertexShader: SKY_VERTEX,
          fragmentShader: SKY_FRAGMENT,
          side: THREE.BackSide,
          transparent: true,
          depthWrite: false,
        })
      );
      sky.renderOrder = -2;
      dome.add(sky);

      const line = (points, opacity) => {
        const obj = new THREE.Line(
          new THREE.BufferGeometry().setFromPoints(points),
          new THREE.LineBasicMaterial({ color: "#8fd3ff", transparent: true, opacity, depthWrite: false })
        );
        obj.renderOrder = 1;
        dome.add(obj);
      };
      for (const [alt, opacity] of [[0, 0.5], [30, 0.16], [60, 0.16]]) {
        const ring = [];
        for (let az = 0; az <= 360; az += 3) ring.push(this.skyDir(alt, az).multiplyScalar(D));
        line(ring, opacity);
      }
      for (let az = 0; az < 360; az += 30) {
        const spoke = [];
        for (let alt = 0; alt <= 80; alt += 4) spoke.push(this.skyDir(alt, az).multiplyScalar(D));
        line(spoke, az % 90 === 0 ? 0.2 : 0.09);
      }
      const labels = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
      labels.forEach((name, k) => {
        const big = name.length === 1;
        const sprite = textSprite(THREE, name, { color: big ? "#e9f4ff" : "#8fb4d9", size: big ? 16 : 11 });
        sprite.position.copy(this.skyDir(3, k * 45).multiplyScalar(D));
        dome.add(sprite);
      });
      for (const alt of [30, 60]) {
        const sprite = textSprite(THREE, `${alt}°`, { color: "#6f8fb3", size: 10 });
        sprite.position.copy(this.skyDir(alt + 1.5, 10).multiplyScalar(D));
        dome.add(sprite);
      }
      this.passGroup = new THREE.Group();
      dome.add(this.passGroup);
      dome.position.copy(this.skyEye());
      this.dome = dome;
      this.domeRadius = D;
      this.globe.scene().add(dome);
      this.setPasses(this.passes || []);
    },

    skyEye() {
      const p = new this.THREE.Vector3(...this.obs.position);
      return p.setLength(Math.max(p.length(), R + 0.03)); // stay above globe.gl's sphere
    },

    setPasses(passes) {
      this.passes = passes;
      if (!this.passGroup) return;
      const THREE = this.THREE;
      for (const obj of [...this.passGroup.children]) {
        this.passGroup.remove(obj);
        obj.geometry?.dispose();
        obj.material.map?.dispose();
        obj.material.dispose();
      }
      const D = this.domeRadius * 0.98;
      passes.forEach((p, k) => {
        const color = p.visible ? "#ffd36b" : "#7ecbff";
        const pts = p.path.map(([x, y, z]) => new THREE.Vector3(x, y, z).multiplyScalar(D));
        const arc = new THREE.Line(
          new THREE.BufferGeometry().setFromPoints(pts),
          new THREE.LineBasicMaterial({ color, transparent: true, opacity: p.visible ? 0.85 : 0.45, depthWrite: false })
        );
        arc.renderOrder = 4;
        arc.userData.index = k;
        this.passGroup.add(arc);
        const peak = pts[pts.length >> 1];
        const time = new Date(p.culminate_ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
        const label = textSprite(THREE, `${p.label}  ${time}  ${Math.round(p.max_el)}°`, {
          color: p.visible ? "#ffe2a0" : "#a9dcff",
          size: 11,
        });
        label.position.copy(peak);
        label.center.set(0.5, -0.3);
        this.passGroup.add(label);
        const end = new THREE.Mesh(
          new THREE.SphereGeometry(0.025, 8, 8),
          new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.9, depthWrite: false })
        );
        end.position.copy(pts[pts.length - 1]);
        this.passGroup.add(end);
      });
    },

    async enterSky(frame) {
      await this.ready;
      if (this.sky) return;
      this.obs = frame;
      this.following = false;
      clearTimeout(this.followTimer);
      this.savedPov = this.globe.pointOfView();
      this.globe.pointOfView({ lat: frame.lat, lng: frame.lon, altitude: 0.06 }, 1300);
      this.flash(1300);
      clearTimeout(this.skyTimer);
      this.skyTimer = setTimeout(() => this.startSky(), 1300);
    },

    startSky() {
      const camera = this.globe.camera();
      const controls = this.globe.controls();
      controls.enabled = false;
      this.saved = { fov: camera.fov, near: camera.near, up: camera.up.clone() };
      camera.fov = 80;
      camera.near = 0.005;
      camera.up.set(...this.obs.up);
      camera.updateProjectionMatrix();
      this.globe.showAtmosphere(false);
      this.satMaterial.uniforms.uSizeScale.value = SKY_SIZE_SCALE;
      if (!this.dome) this.buildDome();
      this.dome.visible = true;
      this.showRings();
      const southward = this.obs.lat >= 0 ? 180 : 0;
      this.sky = { az: southward, el: 35, dir: this.skyDir(35, southward), track: this.selected >= 0 };
      this.updateSky();
    },

    exitSky() {
      clearTimeout(this.skyTimer);
      if (!this.sky) return;
      const camera = this.globe.camera();
      const controls = this.globe.controls();
      this.sky = null;
      this.dome.visible = false;
      this.showRings();
      camera.fov = this.saved.fov;
      camera.near = this.saved.near;
      camera.up.copy(this.saved.up);
      camera.updateProjectionMatrix();
      controls.target.set(0, 0, 0);
      controls.enabled = true;
      this.globe.showAtmosphere(true);
      this.satMaterial.uniforms.uSizeScale.value = 1;
      this.globe.pointOfView({ lat: this.obs.lat, lng: this.obs.lon, altitude: 0.06 }, 0);
      this.flash(500);
      if (this.selected >= 0) this.startFollow();
      else this.globe.pointOfView({ ...this.savedPov, altitude: Math.max(this.savedPov.altitude, 1.6) }, 1500);
    },

    updateSky() {
      const THREE = this.THREE;
      const camera = this.globe.camera();
      const eye = this.skyEye();
      const sky = this.sky;
      if (sky.track && this.selected >= 0) {
        const p = this.worldPosition(this.selected);
        const want = new THREE.Vector3(p.x, p.y, p.z).sub(eye).normalize();
        sky.dir.lerp(want, 0.08).normalize();
        const up = new THREE.Vector3(...this.obs.up);
        sky.el = Math.min(Math.max(Math.asin(sky.dir.dot(up)) / DEG, -2), 89);
        const east = sky.dir.dot(new THREE.Vector3(...this.obs.east));
        const north = sky.dir.dot(new THREE.Vector3(...this.obs.north));
        sky.az = Math.atan2(east, north) / DEG;
      }
      sky.dir.copy(this.skyDir(sky.el, sky.az));
      camera.position.copy(eye);
      camera.lookAt(eye.clone().add(sky.dir));
    },

    lookAt(azDeg, elDeg) {
      if (!this.sky) return;
      this.sky.track = false;
      this.sky.az = azDeg;
      this.sky.el = elDeg;
    },

    flash(ms) {
      const f = this.$refs.flash;
      f.style.transition = "none";
      f.style.opacity = 0;
      requestAnimationFrame(() => {
        f.style.transition = `opacity ${ms * 0.6}ms ease-in`;
        f.style.opacity = 1;
        setTimeout(() => {
          f.style.transition = `opacity ${ms}ms ease-out`;
          f.style.opacity = 0;
        }, ms * 0.7);
      });
    },

    // --- Picking and input ---------------------------------------------------

    // Project candidates to screen space and take the nearest one in front of the Earth.
    pick(clientX, clientY, radiusPx) {
      if (!this.points || !this.frame) return -1;
      const THREE = this.THREE;
      const rect = this.$refs.canvas.getBoundingClientRect();
      const camera = this.globe.camera();
      const camPos = camera.position;
      const v = new THREE.Vector3();
      const t = this.fraction();
      const visible = this.satMaterial.uniforms.uVisible.value;
      const p = { x: 0, y: 0, z: 0 };
      let best = -1, bestD = radiusPx * radiusPx;
      for (let i = 0; i < this.count; i++) {
        if (!visible[this.classes[i]] || !this.localPosition(i, t, p)) continue;
        this.toWorld(p, p);
        v.set(p.x, p.y, p.z).project(camera);
        if (v.z > 1 || v.z < -1) continue;
        const dx = ((v.x + 1) / 2) * rect.width - (clientX - rect.left);
        const dy = ((1 - v.y) / 2) * rect.height - (clientY - rect.top);
        const d = dx * dx + dy * dy;
        if (d < bestD && !this.hiddenByEarth(camPos, p)) {
          best = i;
          bestD = d;
        }
      }
      return best;
    },

    hiddenByEarth(cam, p) {
      // Does the segment camera -> p pass through the sphere of radius R?
      const dx = p.x - cam.x, dy = p.y - cam.y, dz = p.z - cam.z;
      const len = Math.hypot(dx, dy, dz);
      const ux = dx / len, uy = dy / len, uz = dz / len;
      const b = cam.x * ux + cam.y * uy + cam.z * uz;
      const c = cam.x * cam.x + cam.y * cam.y + cam.z * cam.z - R * R;
      const disc = b * b - c;
      if (disc < 0) return false;
      const hit = -b - Math.sqrt(disc);
      return hit > 0 && hit < len - 0.5;
    },

    bindPointer() {
      const el = this.$refs.canvas;
      const tip = this.$refs.tip;
      let down = null, last = null, lastMove = 0;
      el.addEventListener("pointerdown", (e) => {
        down = last = { x: e.clientX, y: e.clientY };
        if (this.sky) el.setPointerCapture(e.pointerId);
      });
      el.addEventListener("pointerup", (e) => {
        const click = down && Math.hypot(e.clientX - down.x, e.clientY - down.y) <= 5;
        down = null;
        if (!click) return;
        const i = this.pick(e.clientX, e.clientY, 14);
        if (i >= 0) this.$emit("select", { norad_id: this.ids[i] });
      });
      el.addEventListener("pointermove", (e) => {
        if (this.sky && down) {
          // Drag to look around: the sky moves with the pointer.
          const degPerPx = this.globe.camera().fov / el.clientHeight;
          this.sky.track = false;
          this.sky.az -= (e.clientX - last.x) * degPerPx;
          this.sky.el = Math.min(Math.max(this.sky.el + (e.clientY - last.y) * degPerPx, -5), 89);
          last = { x: e.clientX, y: e.clientY };
          tip.style.opacity = 0;
          return;
        }
        const now = performance.now();
        if (now - lastMove < 50) return;
        lastMove = now;
        const i = this.pick(e.clientX, e.clientY, 10);
        if (i < 0) {
          tip.style.opacity = 0;
          el.style.cursor = "";
          return;
        }
        const rect = el.getBoundingClientRect();
        tip.textContent = this.names[i];
        tip.style.borderColor = CLASS_COLORS[this.classes[i]];
        tip.style.left = `${e.clientX - rect.left + 14}px`;
        tip.style.top = `${e.clientY - rect.top + 14}px`;
        tip.style.opacity = 1;
        el.style.cursor = "pointer";
      });
      el.addEventListener(
        "wheel",
        (e) => {
          if (!this.sky) return;
          e.preventDefault();
          const camera = this.globe.camera();
          camera.fov = Math.min(Math.max(camera.fov * (1 + e.deltaY * 0.001), 20), 100);
          camera.updateProjectionMatrix();
        },
        { passive: false }
      );
      window.addEventListener("keydown", (e) => {
        const typing = e.target.closest?.("input, textarea, [contenteditable]");
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
          e.preventDefault();
          this.$emit("hotkey", { key: "palette" });
          return;
        }
        if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
        const keys = {
          "/": "palette",
          " ": "toggle",
          ArrowRight: "faster",
          ArrowLeft: "slower",
          l: "live",
          v: "view",
          Escape: "escape",
        };
        const key = keys[e.key] || keys[e.key.toLowerCase()];
        if (!key) return;
        e.preventDefault();
        this.$emit("hotkey", { key });
      });
    },
  },
};
