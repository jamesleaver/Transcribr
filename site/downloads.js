// Point the download buttons at the newest release's installers.
//
// The installers are named Transcribr-<version>-arm64.pkg, -x86_64.pkg
// and -Setup.exe, so the version changes the file names; asking GitHub
// for the latest release keeps the page right without editing it. If
// the request fails (offline, rate-limited), every button still goes to
// the latest release's page.

(function () {
  var API = "https://api.github.com/repos/jamesleaver/Transcribr/releases/latest";
  var SLOTS = [
    { id: "dl-arm64", match: /-arm64\.pkg$/ },
    { id: "dl-x86_64", match: /-x86_64\.pkg$/ },
    { id: "dl-win", match: /-Setup\.exe$/ },
  ];

  function mb(bytes) {
    return Math.round(bytes / 1048576) + " MB";
  }

  function guessSlot() {
    var ua = navigator.userAgent || "";
    if (/Windows/.test(ua)) return "dl-win";
    // Browsers report Intel on every Mac, so Apple Silicon - the Macs
    // sold since 2020 - is the better guess; the page shows both.
    if (/Macintosh|Mac OS X/.test(ua)) return "dl-arm64";
    return null;
  }

  fetch(API, { headers: { Accept: "application/vnd.github+json" } })
    .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(function (rel) {
      var version = (rel.tag_name || "").replace(/^v/, "");
      var date = rel.published_at
        ? new Date(rel.published_at).toLocaleDateString("en-AU",
            { day: "numeric", month: "long", year: "numeric" })
        : "";
      var line = document.getElementById("release-line");
      if (line && version) {
        line.textContent = "Version " + version + (date ? ", released " + date : "") +
          ". Free of charge.";
      }
      var hero = document.getElementById("hero-version");
      if (hero && version) hero.textContent = "Version " + version + " · macOS and Windows";

      SLOTS.forEach(function (slot) {
        var el = document.getElementById(slot.id);
        var asset = (rel.assets || []).find(function (a) { return slot.match.test(a.name); });
        if (!el || !asset) return;
        el.href = asset.browser_download_url;
        var meta = el.querySelector("[data-meta]");
        if (meta) meta.textContent = asset.name + " · " + mb(asset.size);
      });

      var guess = guessSlot();
      var heroBtn = document.getElementById("hero-download");
      var target = guess && document.getElementById(guess);
      if (heroBtn && target && target.href.indexOf("/download/") !== -1) {
        heroBtn.href = target.href;
        heroBtn.textContent = guess === "dl-win" ? "Download for Windows" : "Download for Mac";
        target.classList.add("dl-suggested");
      }
    })
    .catch(function () { /* the buttons already go to the releases page */ });
})();
