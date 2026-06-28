// ── ANIMATED STAT COUNTERS ─────────────────────────────────────────────────────
(function statCounters() {
  var els = document.querySelectorAll('.stat-counter');
  if (!els.length || !('IntersectionObserver' in window)) {
    // Fallback: just set the target value immediately
    els.forEach(function(el) { el.textContent = parseInt(el.dataset.count||0).toLocaleString() + (el.dataset.suffix||''); });
    return;
  }
  var obs = new IntersectionObserver(function(entries) {
    entries.forEach(function(entry) {
      if (!entry.isIntersecting) return;
      obs.unobserve(entry.target);
      var el = entry.target;
      var target = parseInt(el.dataset.count || 0, 10);
      var duration = 1600;
      var start = performance.now();
      var isPct = el.classList.contains('stat-pct');
      function tick(now) {
        var t = Math.min((now - start) / duration, 1);
        var eased = 1 - Math.pow(1 - t, 3);
        var val = Math.round(eased * target);
        el.textContent = val.toLocaleString();
        if (t < 1) {
          requestAnimationFrame(tick);
        } else if (el.dataset.suffix) {
          setTimeout(function() { el.textContent = target.toLocaleString() + el.dataset.suffix; }, 220);
        }
      }
      requestAnimationFrame(tick);
    });
  }, { threshold: 0.6 });
  els.forEach(function(el) { obs.observe(el); });
})();

// ── TOC SCROLL TRACKER ─────────────────────────────────────────────────────────
(function tocTracker() {
  var toc  = document.getElementById('article-toc');
  var prog = document.getElementById('toc-progress');
  if (!toc) return;

  var tocLinks = toc.querySelectorAll('a');
  var headings = document.querySelectorAll('.post-content h2[id], .post-content h3[id]');
  if (!headings.length || !tocLinks.length) return;

  // Highlight active heading
  var headingObs = new IntersectionObserver(function(entries) {
    var topmost = null;
    entries.forEach(function(e) { if (e.isIntersecting) topmost = e.target; });
    if (!topmost) return;
    var id = topmost.id;
    tocLinks.forEach(function(a) {
      a.classList.toggle('toc-active', a.getAttribute('href') === '#' + id);
    });
  }, { rootMargin: '-5% 0px -75% 0px' });
  headings.forEach(function(h) { headingObs.observe(h); });

  // Reading progress bar
  if (prog) {
    var content = document.querySelector('.post-content');
    if (content) {
      window.addEventListener('scroll', function() {
        var rect = content.getBoundingClientRect();
        var total = content.offsetHeight - window.innerHeight;
        var scrolled = Math.max(0, -rect.top);
        var pct = total > 0 ? Math.min(100, (scrolled / total) * 100) : 0;
        prog.style.width = pct.toFixed(1) + '%';
      }, { passive: true });
    }
  }
})();

// ── COOKIE CONSENT BANNER ──────────────────────────────────────────────────────
(function cookieBanner() {
  var CONSENT_KEY = 'cookie_consent';
  var banner = document.getElementById('cookie-banner');
  if (!banner) return;
  var stored = localStorage.getItem(CONSENT_KEY);
  if (!stored) {
    // Show after a short delay so it doesn't flash on first paint
    setTimeout(function() { banner.style.display = 'block'; }, 800);
  }
  document.getElementById('cookie-accept').addEventListener('click', function() {
    localStorage.setItem(CONSENT_KEY, 'accepted');
    banner.classList.add('cookie-banner-hide');
    setTimeout(function() { banner.style.display = 'none'; }, 350);
  });
  document.getElementById('cookie-decline').addEventListener('click', function() {
    localStorage.setItem(CONSENT_KEY, 'declined');
    banner.classList.add('cookie-banner-hide');
    setTimeout(function() { banner.style.display = 'none'; }, 350);
  });
})();

// Mobile nav toggle
const toggle = document.querySelector('.nav-toggle');
const nav    = document.querySelector('.site-nav');
if (toggle && nav) {
  toggle.addEventListener('click', () => {
    const open = nav.classList.toggle('nav-open');
    if (open) {
      nav.style.display        = 'flex';
      nav.style.flexDirection  = 'column';
      nav.style.position       = 'absolute';
      nav.style.top            = '64px';
      nav.style.left           = '0';
      nav.style.right          = '0';
      nav.style.background     = 'var(--primary)';
      nav.style.padding        = '16px 24px';
      nav.style.gap            = '16px';
      nav.style.zIndex         = '99';
    } else {
      nav.style.display = '';
      nav.style.position = '';
    }
  });
}

// Collapse unfilled ad slots
// AdSense sets data-ad-status="unfilled" when no ad is available.
// Before AdSense is approved, slots remain empty — we collapse them after a delay.
(function collapseEmptyAds() {
  function check() {
    document.querySelectorAll('.ad-container').forEach(function(container) {
      const ins = container.querySelector('ins.adsbygoogle');
      if (!ins) return;
      const status = ins.getAttribute('data-ad-status');
      // Collapse if explicitly unfilled OR if AdSense never ran (status never set)
      if (status === 'unfilled' || status === null) {
        container.classList.add('ad-empty');
      } else {
        container.classList.remove('ad-empty');
      }
    });
  }
  // Give AdSense up to 2.5s to load and fill slots
  setTimeout(check, 2500);
  // Watch for future attribute changes (slot filled/unfilled dynamically)
  document.querySelectorAll('ins.adsbygoogle').forEach(function(ins) {
    new MutationObserver(check).observe(ins, { attributes: true, attributeFilter: ['data-ad-status'] });
  });
})();

// Lazy load images
if ('IntersectionObserver' in window) {
  const imgs = document.querySelectorAll('img[loading="lazy"]');
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(e => { if (e.isIntersecting) { observer.unobserve(e.target); } });
  });
  imgs.forEach(img => observer.observe(img));
}
// -- Scroll-to-top --
(function(){var b=document.createElement('button');b.id='scroll-top';b.setAttribute('aria-label','Back to top');b.innerHTML='&#8679;';document.body.appendChild(b);window.addEventListener('scroll',function(){b.classList.toggle('visible',window.scrollY>400);},{passive:true});b.addEventListener('click',function(){window.scrollTo({top:0,behavior:'smooth'});});})();

// -- Wave 5 reading progress --
(function(){
  var bar=document.createElement('div');
  bar.id='reading-progress';
  bar.setAttribute('aria-hidden','true');
  document.body.insertBefore(bar,document.body.firstChild);
  if(!CSS.supports('animation-timeline','scroll()')){
    window.addEventListener('scroll',function(){
      var s=document.documentElement;
      var pct=s.scrollTop/(s.scrollHeight-s.clientHeight);
      bar.style.transform='scaleX('+Math.min(1,Math.max(0,pct||0))+')';
    },{passive:true});
  }
})();

// -- Browse Topics truncation --
(function(){
  document.querySelectorAll('.widget-title').forEach(function(t){
    if(t.textContent.trim()!=='Browse Topics')return;
    var ul=t.closest('.widget').querySelector('.widget-list');
    if(!ul)return;
    var items=[].slice.call(ul.children);
    if(items.length<=10)return;
    items.slice(10).forEach(function(li){li.hidden=true;});
    var btn=document.createElement('button');
    btn.textContent='Show all '+items.length+' topics ▾';
    btn.style.cssText='margin-top:8px;font-size:12px;color:var(--accent,#c2410c);background:none;border:none;cursor:pointer;padding:2px 0;font-family:inherit;display:block';
    btn.addEventListener('click',function(){
      items.slice(10).forEach(function(li){li.hidden=false;});
      btn.remove();
    });
    ul.after(btn);
  });
})();

// -- Ticker speed proportional --
(function(){
  document.addEventListener('DOMContentLoaded',function(){
    var track=document.querySelector('.ticker-track');
    if(!track)return;
    // track is doubled for seamless loop; -50% = one content-width traveled
    var halfW=track.scrollWidth/2;
    if(!halfW)return;
    // target 25px/sec — comfortable reading speed for a category ticker
    var dur=Math.round(Math.max(60,halfW/25));
    track.style.animationDuration=dur+'s';
  });
})();
