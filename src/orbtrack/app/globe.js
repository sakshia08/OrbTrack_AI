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

const TAIL_VERTEX = `
  varying float vAlong;
  void main() {
    vAlong = uv.x;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }`;

const TAIL_FRAGMENT = `
  uniform vec3 color;
  varying float vAlong;
  void main() {
    float a = pow(vAlong, 1.6);
    gl_FragColor = vec4(mix(color, vec3(1.0), a * a * 0.6) * a, a);
  }`;

const SAT_VERTEX = `
  attribute vec3 posA;
  attribute vec3 posB;
  attribute vec3 tint;
  attribute float size;
  attribute float cls;
  uniform float uT;
  uniform float uPixelRatio;
  uniform float uVisible[5];
  varying vec3 vTint;
  void main() {
    vec3 p = mix(posA, posB, uT);
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;
    float visible = uVisible[int(cls + 0.5)];
    gl_PointSize = visible * clamp(size * uPixelRatio * 1400.0 / -mv.z, 2.2 * uPixelRatio, 10.0 * uPixelRatio);
    vTint = tint;
  }`;

const SAT_FRAGMENT = `
  varying vec3 vTint;
  void main() {
    float d = length(gl_PointCoord - 0.5) * 2.0;
    if (d > 1.0) discard;
    float core = smoothstep(0.45, 0.0, d);
    float halo = pow(1.0 - d, 2.5) * 0.55;
    gl_FragColor = vec4(vTint * (core + halo) + core * 0.35, core + halo);
  }`;

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

export default {
  template: `
    <div class="orbtrack-globe-root">
      <div ref="canvas" class="orbtrack-globe-canvas"></div>
      <div ref="tip" class="orbtrack-tip"></div>
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
    globe.controls().autoRotate = false;
    globe.controls().enableDamping = true;

    new ResizeObserver(() => globe.width(el.clientWidth).height(el.clientHeight)).observe(el);

    this.marker = new THREE.Sprite(
      new THREE.SpriteMaterial({ map: ringTexture(THREE), depthTest: false, transparent: true, sizeAttenuation: false })
    );
    this.marker.scale.set(0.045, 0.045, 1);
    this.marker.visible = false;
    this.marker.renderOrder = 10;
    globe.scene().add(this.marker);

    this.selected = -1;
    this.following = false;
    this.offsetMs = 0;
    this.firstFrameSent = false;
    this.bindPointer();
    this.markReady();
    this.loop();
  },

  methods: {
    async initCatalog(payload) {
      await this.ready;
      const THREE = this.THREE;
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
          uVisible: { value: [1, 1, 1, 1, 1] },
        },
        vertexShader: SAT_VERTEX,
        fragmentShader: SAT_FRAGMENT,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      });
      this.points = new THREE.Points(geometry, this.satMaterial);
      this.points.frustumCulled = false;
      this.globe.scene().add(this.points);
    },

    async pushFrame(frame) {
      await this.ready;
      if (!this.points) return;
      this.posA.set(decode(frame.a, Float32Array));
      this.posB.set(decode(frame.b, Float32Array));
      this.attrA.needsUpdate = true;
      this.attrB.needsUpdate = true;
      this.frameA = frame.a_ms;
      this.frameB = frame.b_ms;
      this.offsetMs = frame.a_ms - Date.now();
      if (!this.firstFrameSent) {
        this.firstFrameSent = true;
        requestAnimationFrame(() => this.$emit("first_frame", { ms: Math.round(performance.now()) }));
      }
    },

    async setSun(lat, lng) {
      await this.ready;
      const p = this.globe.getCoords(lat, lng, 0);
      this.earthMaterial.uniforms.sunDir.value.set(p.x, p.y, p.z).normalize();
    },

    setClassVisible(index, visible) {
      if (this.satMaterial) this.satMaterial.uniforms.uVisible.value[index] = visible ? 1 : 0;
    },

    async select(noradId, trail, follow) {
      await this.ready;
      const index = this.ids ? this.ids.indexOf(noradId) : -1;
      this.selected = index;
      this.marker.visible = index >= 0;
      this.setTrail(trail);
      if (follow && index >= 0) this.startFollow();
    },

    setTrail(trail) {
      const THREE = this.THREE;
      const scene = this.globe.scene();
      for (const obj of this.trailObjects || []) {
        scene.remove(obj);
        obj.geometry.dispose();
        obj.material.dispose();
      }
      this.trailObjects = [];
      if (!trail) return;
      const toVec = ([lat, lng, alt]) => {
        const c = this.globe.getCoords(lat, lng, alt);
        return new THREE.Vector3(c.x, c.y, c.z);
      };

      // Comet tail: a thin tube that fades in towards the satellite.
      const past = trail.past.map(toVec);
      if (past.length > 1) {
        const curve = new THREE.CatmullRomCurve3(past);
        const radius = 0.0016 * this.globe.camera().position.length(); // a few pixels at any zoom
        const tail = new THREE.Mesh(
          new THREE.TubeGeometry(curve, past.length * 4, radius, 8, false),
          new THREE.ShaderMaterial({
            uniforms: { color: { value: new THREE.Color("#7ef3ff") } },
            vertexShader: TAIL_VERTEX,
            fragmentShader: TAIL_FRAGMENT,
            transparent: true,
            depthWrite: false,
            blending: THREE.AdditiveBlending,
          })
        );
        this.trailObjects.push(tail);
      }

      // The orbit ahead: a faint dashed line.
      const ahead = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(trail.future.map(toVec)),
        new THREE.LineDashedMaterial({ color: "#7ecbff", dashSize: 0.9, gapSize: 0.7, transparent: true, opacity: 0.45, depthWrite: false })
      );
      ahead.computeLineDistances();
      this.trailObjects.push(ahead);
      for (const obj of this.trailObjects) {
        obj.frustumCulled = false;
        scene.add(obj);
      }
    },

    release() {
      this.selected = -1;
      this.following = false;
      this.marker.visible = false;
      this.setTrail(null);
      const controls = this.globe.controls();
      controls.enableRotate = true;
      controls.enableDamping = true;
      clearTimeout(this.followTimer);
      const pov = this.globe.pointOfView();
      this.globe.pointOfView({ lat: pov.lat, lng: pov.lng, altitude: 2.6 }, 1200);
    },

    startFollow() {
      const p = this.currentPosition(this.selected);
      const geo = this.globe.toGeoCoords(p);
      const altitude = Math.max(geo.altitude + 0.9, 1.1);
      const controls = this.globe.controls();
      controls.enableRotate = false;
      controls.enableDamping = false;
      this.following = false;
      this.globe.pointOfView({ lat: geo.lat, lng: geo.lng, altitude }, 1400);
      clearTimeout(this.followTimer);
      this.followTimer = setTimeout(() => (this.following = this.selected >= 0), 1400);
    },

    fraction() {
      if (this.frameA === undefined) return 0;
      const now = Date.now() + this.offsetMs;
      return Math.min(Math.max((now - this.frameA) / (this.frameB - this.frameA), 0), 1.5);
    },

    currentPosition(i) {
      const t = this.fraction();
      const a = this.posA, b = this.posB, k = i * 3;
      return {
        x: a[k] + (b[k] - a[k]) * t,
        y: a[k + 1] + (b[k + 1] - a[k + 1]) * t,
        z: a[k + 2] + (b[k + 2] - a[k + 2]) * t,
      };
    },

    loop() {
      requestAnimationFrame(() => this.loop());
      if (!this.points) return;
      this.satMaterial.uniforms.uT.value = this.fraction();
      if (this.selected >= 0) {
        const p = this.currentPosition(this.selected);
        this.marker.position.set(p.x, p.y, p.z);
        if (this.following) {
          const geo = this.globe.toGeoCoords(p);
          const cam = this.globe.camera().position;
          const altitude = cam.length() / R - 1; // keep the user's zoom
          this.globe.pointOfView({ lat: geo.lat, lng: geo.lng, altitude }, 0);
        }
      }
    },

    // Picking: project candidates to screen space and take the nearest one in front of the Earth.
    pick(clientX, clientY, radiusPx) {
      if (!this.points) return -1;
      const THREE = this.THREE;
      const rect = this.$refs.canvas.getBoundingClientRect();
      const camera = this.globe.camera();
      const camPos = camera.position;
      const v = new THREE.Vector3();
      const t = this.fraction();
      const visible = this.satMaterial.uniforms.uVisible.value;
      let best = -1, bestD = radiusPx * radiusPx;
      for (let i = 0; i < this.count; i++) {
        if (!visible[this.classes[i]]) continue;
        const k = i * 3;
        const x = this.posA[k] + (this.posB[k] - this.posA[k]) * t;
        const y = this.posA[k + 1] + (this.posB[k + 1] - this.posA[k + 1]) * t;
        const z = this.posA[k + 2] + (this.posB[k + 2] - this.posA[k + 2]) * t;
        if (x * x + y * y + z * z < 1) continue; // failed propagation, parked at the centre
        v.set(x, y, z).project(camera);
        if (v.z > 1) continue;
        const dx = ((v.x + 1) / 2) * rect.width - (clientX - rect.left);
        const dy = ((1 - v.y) / 2) * rect.height - (clientY - rect.top);
        const d = dx * dx + dy * dy;
        if (d < bestD && !this.hiddenByEarth(camPos, { x, y, z })) {
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
      let down = null, lastMove = 0;
      el.addEventListener("pointerdown", (e) => (down = { x: e.clientX, y: e.clientY }));
      el.addEventListener("pointerup", (e) => {
        if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 5) return;
        const i = this.pick(e.clientX, e.clientY, 14);
        if (i >= 0) this.$emit("select", { norad_id: this.ids[i] });
      });
      el.addEventListener("pointermove", (e) => {
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
      window.addEventListener("keydown", (e) => {
        if (e.key === "Escape") this.$emit("release");
      });
    },
  },
};
