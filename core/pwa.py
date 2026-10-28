"""Home-screen install: adds the app manifest, icons and phone-app tags to the page, so 'Add to Home Screen'
(iPhone) or 'Install app' (Android/Chrome) gives a proper icon and a full-screen app. Files live in ./static and are
served by Streamlit at app/static/ (server.enableStaticServing in .streamlit/config.toml)."""
import streamlit as st

_JS = """
<script>
(function () {
  function add(doc, base) {
    if (!doc || !doc.head || doc.getElementById('vf-manifest')) return;
    var tags = [
      ['link', {id: 'vf-manifest', rel: 'manifest', href: base + 'manifest.json'}],
      ['link', {rel: 'apple-touch-icon', href: base + 'apple-touch-icon.png'}],
      ['link', {rel: 'icon', type: 'image/png', sizes: '192x192', href: base + 'icon-192.png'}],
      ['meta', {name: 'theme-color', content: '#1F6B4F'}],
      ['meta', {name: 'mobile-web-app-capable', content: 'yes'}],
      ['meta', {name: 'apple-mobile-web-app-capable', content: 'yes'}],
      ['meta', {name: 'apple-mobile-web-app-status-bar-style', content: 'default'}],
      ['meta', {name: 'apple-mobile-web-app-title', content: 'Verdant'}]
    ];
    tags.forEach(function (t) {
      var el = doc.createElement(t[0]);
      Object.keys(t[1]).forEach(function (k) { el.setAttribute(k, t[1][k]); });
      doc.head.appendChild(el);
    });
  }
  try {
    var base = new URL('app/static/', window.location.href.split('?')[0].replace(/[^/]*$/, '')).href;
    add(document, base);
    try { if (window.parent && window.parent !== window) add(window.parent.document, base); } catch (e) {}
  } catch (e) {}
})();
</script>
"""


def inject() -> None:
    """Call once per page run (cheap: the script does nothing if the tags are already there)."""
    if st.session_state.get("_pwa_done"):
        return
    try:
        st.html(_JS, unsafe_allow_javascript=True)
        st.session_state["_pwa_done"] = True
    except Exception:
        pass
