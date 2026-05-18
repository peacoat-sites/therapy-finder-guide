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
