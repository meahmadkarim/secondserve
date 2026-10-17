const toggle = document.querySelector('.nav-toggle');
if (toggle) toggle.addEventListener('click', () => {
  const expanded = toggle.getAttribute('aria-expanded') === 'true';
  toggle.setAttribute('aria-expanded', String(!expanded));
  document.getElementById('navigation').classList.toggle('expanded', !expanded);
});
