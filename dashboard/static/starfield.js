/**
 * Aethelark 3D Deep Space Starfield & Cyber-Particle Engine
 * Defensively halts on hidden tabs and respects prefers-reduced-motion.
 */
(function () {
  'use strict';

  const canvas = document.getElementById('starfield');
  if (!canvas) return;

  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let animId = null;
  let width = 0;
  let height = 0;
  let stars = [];
  const STAR_COUNT = 150;

  class Star {
    constructor() {
      this.reset(true);
    }

    reset(initial = false) {
      this.x = Math.random() * width;
      this.y = initial ? Math.random() * height : 0;
      this.z = Math.random() * 2 + 0.5;
      this.size = Math.random() * 1.5 + 0.5;
      this.opacity = Math.random() * 0.7 + 0.3;
      this.speed = prefersReducedMotion ? 0.05 : (this.z * 0.4);
      this.twinkle = Math.random() * Math.PI;
    }

    update() {
      this.y += this.speed;
      this.twinkle += 0.02;
      if (this.y > height) {
        this.reset(false);
      }
    }

    draw() {
      const alpha = this.opacity * (0.7 + 0.3 * Math.sin(this.twinkle));
      ctx.fillStyle = `rgba(186, 230, 253, ${alpha})`;
      ctx.shadowBlur = this.size * 2;
      ctx.shadowColor = 'rgba(56, 189, 248, 0.5)';
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function resize() {
    width = canvas.width = window.innerWidth;
    height = canvas.height = window.innerHeight;
    stars = Array.from({ length: STAR_COUNT }, () => new Star());
  }

  function render() {
    ctx.clearRect(0, 0, width, height);
    for (let i = 0; i < stars.length; i++) {
      stars[i].update();
      stars[i].draw();
    }
    if (!document.hidden) {
      animId = requestAnimationFrame(render);
    }
  }

  function onVisibilityChange() {
    if (document.hidden) {
      if (animId) {
        cancelAnimationFrame(animId);
        animId = null;
      }
    } else {
      if (!animId) {
        animId = requestAnimationFrame(render);
      }
    }
  }

  window.addEventListener('resize', resize, { passive: true });
  document.addEventListener('visibilitychange', onVisibilityChange);

  resize();
  animId = requestAnimationFrame(render);
})();
