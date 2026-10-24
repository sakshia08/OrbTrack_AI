// Time machine bar: the simulated clock, speed controls and a ±48 h scrub track that shows
// day and night over your city, upcoming passes and detected events. The clock animates locally from the
// line the server sends (sim = sim0 + (real - real0) * rate); scrubbing emits new times.

const WINDOW_MS = 48 * 3600 * 1000;
const DAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

const ICONS = {
  rewind: '<svg viewBox="0 0 24 24"><path d="M11 6v12l-8.5-6zM21 6v12l-8.5-6z"/></svg>',
  play: '<svg viewBox="0 0 24 24"><path d="M7 5v14l12-7z"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>',
  forward: '<svg viewBox="0 0 24 24"><path d="M13 6v12l8.5-6zM3 6v12l8.5-6z"/></svg>',
};

const pad = (n) => String(n).padStart(2, "0");

function offsetText(ms) {
  const sign = ms < 0 ? "−" : "+";
  let m = Math.round(Math.abs(ms) / 60000);
  const d = Math.floor(m / 1440);
  m -= d * 1440;
  const h = Math.floor(m / 60);
  m -= h * 60;
  if (d) return `${sign}${d} d ${h} h`;
  if (h) return `${sign}${h} h ${pad(m)} m`;
  return `${sign}${m} m`;
}

function rateText(rate) {
  if (rate === 0) return "PAUSED";
  return `${rate < 0 ? "−" : ""}${Math.abs(rate)}×`;
}

export default {
  template: `
    <div class="tb">
      <div class="tb-row">
        <div class="tb-btns">
          <button class="tb-btn" title="Slower / rewind (←)" @click="$emit('step', {dir: -1})" v-html="icons.rewind"></button>
          <button class="tb-btn tb-play" title="Pause / play (space)" @click="$emit('toggle')" v-html="paused ? icons.play : icons.pause"></button>
          <button class="tb-btn" title="Faster (→)" @click="$emit('step', {dir: 1})" v-html="icons.forward"></button>
        </div>
        <div class="tb-time">
          <span ref="date" class="tb-date"></span>
          <span ref="clock" class="tb-clock"></span>
          <span class="tb-utc">UTC</span>
        </div>
        <div class="tb-status">
          <span ref="rate" class="tb-rate"></span>
          <span ref="offset" class="tb-offset"></span>
        </div>
        <button ref="live" class="tb-live" title="Back to live (L)" @click="$emit('live')"><span class="dot"></span>LIVE</button>
      </div>
      <div ref="track" class="tb-track">
        <canvas ref="canvas"></canvas>
        <div ref="head" class="tb-head"></div>
        <div ref="hover" class="tb-hover"></div>
      </div>
    </div>`,

  data() {
    return { icons: ICONS, paused: false };
  },

  mounted() {
    this.clock = { sim0_ms: Date.now(), real0_ms: Date.now(), rate: 1, live: true };
    this.skew = 0;
    this.bands = null;
    this.passes = [];
    this.events = [];
    this.drag = null;
    this.lastDraw = 0;
    this.lastEmit = 0;
    new ResizeObserver(() => this.draw()).observe(this.$refs.track);
    this.bindTrack();
    this.tick();
  },

  methods: {
    setClock(clock, sentMs) {
      if (sentMs !== undefined) this.skew = sentMs - Date.now();
      if (this.drag) {
        this.pending = clock;
        return;
      }
      this.clock = clock;
      this.paused = clock.rate === 0;
      this.$refs.live.classList.toggle("on", !!clock.live);
      this.$refs.rate.textContent = rateText(clock.rate);
      this.$refs.rate.classList.toggle("fast", Math.abs(clock.rate) > 1);
    },

    setBands(bands) {
      this.bands = bands;
      this.draw();
    },

    setPasses(passes) {
      this.passes = passes;
      this.draw();
    },

    setEvents(events) {
      this.events = events;
      this.draw();
    },

    real() {
      return Date.now() + this.skew;
    },

    simNow() {
      if (this.drag) return this.drag.sim;
      const c = this.clock;
      return c.sim0_ms + (this.real() - c.real0_ms) * c.rate;
    },

    xToSim(clientX) {
      const rect = this.$refs.track.getBoundingClientRect();
      const f = Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1);
      return this.real() - WINDOW_MS + f * 2 * WINDOW_MS;
    },

    // Writes to the DOM only when what is shown changes: every repaint of a blurred glass panel is costly.
    show(ref, text) {
      if (this.$refs[ref].textContent !== text) this.$refs[ref].textContent = text;
    },

    tick() {
      requestAnimationFrame(() => this.tick());
      const sim = this.simNow();
      const d = new Date(sim);
      this.show("clock", `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`);
      this.show("date", `${DAYS[d.getUTCDay()]} ${pad(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]}`);
      const offset = sim - this.real();
      const live = !this.drag && this.clock.live;
      this.show("offset", live ? "real time" : Math.abs(offset) < 30000 ? "now" : offsetText(offset));
      const f = Math.min(Math.max((offset + WINDOW_MS) / (2 * WINDOW_MS), 0), 1);
      const left = `${(f * 100).toFixed(2)}%`;
      if (this.$refs.head.style.left !== left) this.$refs.head.style.left = left;
      if (performance.now() - this.lastDraw > 1000) this.draw();
    },

    // Night bands from the Sun's altitude over the observer, hour ticks, passes and "now".
    draw() {
      this.lastDraw = performance.now();
      const canvas = this.$refs.canvas;
      const rect = this.$refs.track.getBoundingClientRect();
      if (!rect.width) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(rect.width * dpr);
      canvas.height = Math.round(rect.height * dpr);
      const g = canvas.getContext("2d");
      g.scale(dpr, dpr);
      const w = rect.width, h = rect.height;
      const start = this.real() - WINDOW_MS;
      const x = (ms) => ((ms - start) / (2 * WINDOW_MS)) * w;

      if (this.bands) {
        const { t0_ms, step_ms, alt } = this.bands;
        for (let px = 0; px < w; px += 2) {
          const ms = start + (px / w) * 2 * WINDOW_MS;
          const k = (ms - t0_ms) / step_ms;
          const i = Math.min(Math.max(Math.floor(k), 0), alt.length - 2);
          const a = alt[i] + (alt[i + 1] - alt[i]) * Math.min(Math.max(k - i, 0), 1);
          const day = Math.min(Math.max((a + 6) / 12, 0), 1); // 0 below -6°, 1 above +6°
          const dusk = Math.exp(-(((a + 4) / 6) ** 2));
          const r = 50 + 60 * day + 190 * dusk, gr = 90 + 100 * day + 20 * dusk, b = 150 + 100 * day - 70 * dusk;
          g.fillStyle = `rgba(${r}, ${gr}, ${b}, ${0.04 + 0.3 * day + 0.22 * dusk})`;
          g.fillRect(px, 0, 2, h);
        }
      }

      // Hour ticks every 6 h, and local midnight with the date.
      g.font = '600 9px "JetBrains Mono", monospace';
      g.textBaseline = "top";
      const first = new Date(start);
      first.setMinutes(0, 0, 0);
      for (let ms = first.getTime(); ms < start + 2 * WINDOW_MS; ms += 3600000) {
        const d = new Date(ms);
        const hour = d.getHours();
        if (hour % 6) continue;
        const px = x(ms);
        g.fillStyle = hour === 0 ? "rgba(200, 225, 255, 0.45)" : "rgba(200, 225, 255, 0.16)";
        g.fillRect(px, hour === 0 ? 0 : h - 6, 1, hour === 0 ? h : 6);
        if (hour === 0) {
          g.fillStyle = "rgba(200, 225, 255, 0.55)";
          g.fillText(`${DAYS[d.getDay()]} ${d.getDate()}`, px + 4, 3);
        }
      }

      for (const p of this.passes) {
        const a = x(p.rise_ms), b = Math.max(x(p.set_ms), a + 2);
        g.fillStyle = p.visible ? "rgba(255, 211, 107, 0.95)" : "rgba(126, 203, 255, 0.55)";
        g.fillRect(a, h - 11, b - a, 4);
      }

      // Events: a small glowing diamond along the top edge.
      for (const ev of this.events) {
        const px = x(ev.ms);
        if (px < -4 || px > w + 4) continue;
        g.save();
        g.shadowColor = ev.color;
        g.shadowBlur = 8;
        g.fillStyle = ev.color;
        g.beginPath();
        g.moveTo(px, 3);
        g.lineTo(px + 4, 7.5);
        g.lineTo(px, 12);
        g.lineTo(px - 4, 7.5);
        g.closePath();
        g.fill();
        g.restore();
        g.fillStyle = ev.color.length === 7 ? `${ev.color}55` : ev.color;
        g.fillRect(px - 0.5, 12, 1, h - 12);
      }

      const now = x(this.real());
      g.fillStyle = "rgba(255, 77, 109, 0.9)";
      g.fillRect(now - 0.5, 0, 1.5, h);
    },

    bindTrack() {
      const track = this.$refs.track;
      const hover = this.$refs.hover;
      const emit = (final) => {
        const now = performance.now();
        if (!final && now - this.lastEmit < 90) return;
        this.lastEmit = now;
        this.$emit("scrub", { sim_ms: this.drag.sim, final });
      };
      track.addEventListener("pointerdown", (e) => {
        track.setPointerCapture(e.pointerId);
        this.drag = { sim: this.xToSim(e.clientX) };
        track.classList.add("dragging");
        emit(false);
      });
      track.addEventListener("pointermove", (e) => {
        const sim = this.xToSim(e.clientX);
        const rect = track.getBoundingClientRect();
        const d = new Date(sim);
        const pass = this.passes.find((p) => sim >= p.rise_ms - 5 * 60000 && sim <= p.set_ms + 5 * 60000);
        const slack = (2 * WINDOW_MS * 6) / rect.width; // within 6 px of a mark
        const ev = this.events.find((e) => Math.abs(sim - e.ms) <= slack);
        hover.textContent =
          `${DAYS[d.getDay()]} ${pad(d.getHours())}:${pad(d.getMinutes())}` +
          (ev ? ` · ${ev.label}` : pass ? ` · ${pass.label} pass${pass.visible ? " ✦ visible" : ""}` : "");
        hover.style.left = `${e.clientX - rect.left}px`;
        hover.style.opacity = 1;
        if (!this.drag) return;
        this.drag.sim = sim;
        emit(false);
      });
      track.addEventListener("pointerleave", () => (hover.style.opacity = 0));
      const end = () => {
        if (!this.drag) return;
        emit(true);
        this.drag = null;
        track.classList.remove("dragging");
        if (this.pending) this.setClock(this.pending);
        this.pending = null;
      };
      track.addEventListener("pointerup", end);
      track.addEventListener("pointercancel", end);
    },
  },
};
