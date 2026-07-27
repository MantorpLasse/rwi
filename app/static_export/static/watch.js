/* Client-side "watch a signal" - localStorage only, no server/DB involved.
   Shared by signals_list.html (sorting/filtering on top of this) and
   signal_detail.html (a single star, no sorting needed there). */
(function (global) {
  var STORAGE_KEY = "rwi_watched_signals";

  function getWatched() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      var ids = raw ? JSON.parse(raw) : [];
      return Array.isArray(ids) ? ids.map(String) : [];
    } catch (e) {
      return [];
    }
  }

  function isWatched(id, watchedSet) {
    return (watchedSet || getWatched()).indexOf(String(id)) !== -1;
  }

  function setWatched(id, watched) {
    var ids = getWatched();
    var idStr = String(id);
    var pos = ids.indexOf(idStr);
    if (watched && pos === -1) {
      ids.push(idStr);
    } else if (!watched && pos !== -1) {
      ids.splice(pos, 1);
    }
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
    } catch (e) {
      /* localStorage unavailable (private mode, quota, etc.) - watching
         just won't persist; the star still reflects state for this load. */
    }
    return ids;
  }

  function paintStar(el, watched) {
    el.textContent = watched ? "★" : "☆";
    el.classList.toggle("watched", watched);
    el.setAttribute("aria-pressed", watched ? "true" : "false");
    el.setAttribute("aria-label", watched ? "Sluta bevaka signal" : "Bevaka signal");
  }

  /* Paints and wires every .star-toggle under `root` (default: whole
     document). Two stars can share the same data-signal-id (a group's
     collapsed headline and its expanded strip both represent the group's
     top signal - see signals_list.html) - clicking either repaints all
     stars for that id, so they never fall out of sync. `onChange(id,
     watched)` fires after each toggle, for callers that need to re-sort
     or re-filter. */
  function initStars(root, onChange) {
    var scope = root || document;
    var watched = getWatched();
    var stars = scope.querySelectorAll(".star-toggle");
    Array.prototype.forEach.call(stars, function (el) {
      var id = el.getAttribute("data-signal-id");
      paintStar(el, isWatched(id, watched));
      el.addEventListener("click", function (e) {
        e.stopPropagation();
        var next = !el.classList.contains("watched");
        setWatched(id, next);
        var siblings = scope.querySelectorAll('.star-toggle[data-signal-id="' + id + '"]');
        Array.prototype.forEach.call(siblings, function (sib) {
          paintStar(sib, next);
        });
        if (onChange) onChange(id, next);
      });
    });
  }

  global.RWIWatch = {
    STORAGE_KEY: STORAGE_KEY,
    getWatched: getWatched,
    isWatched: isWatched,
    setWatched: setWatched,
    paintStar: paintStar,
    initStars: initStars,
  };
})(window);
