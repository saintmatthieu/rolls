// The library's table: rows filtered as the visitor types (every word must
// match in a row's title, composer, opus or tags, allowing for typos: see
// matches), and sorted by the column
// whose heading is clicked (again to reverse). The page lists the rows in the
// default order (composer, opus, title); the other orders come from the rows'
// data attributes, ties falling back to the default. The search and the order
// are kept in the address (?q=schumann&sort=difficulty&order=desc), to share them.
(function () {
  var input = document.getElementById("search");
  var table = document.getElementById("scores");
  var body = table.tBodies[0];
  var rows = Array.prototype.slice.call(body.rows);
  var headings = Array.prototype.slice.call(table.querySelectorAll("th[data-sort]"));
  var empty = document.getElementById("empty");

  function fold(text) {
    return text.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  }
  var texts = rows.map(function (row, i) {
    row.dataset.default = i;
    // Also as German umlauts are written without them: "fuer elise".
    var umlauts = row.dataset.search.toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe")
      .replace(/ü/g, "ue");
    var text = fold(row.dataset.search) + " " + fold(umlauts);
    // Tags only match exactly: shared by most scores ("teaching"), they would
    // match most abbreviations and typos.
    return { text: text + " " + fold(row.dataset.tags || ""),
             words: text.split(/[^a-z0-9]+/).filter(Boolean) };
  });

  // How many typos the start of a word may have to match w (a substitution,
  // an insertion, a deletion or two letters swapped each count as one): the
  // edit distance from w to the closest prefix of word.
  function prefixDistance(w, word) {
    var m = w.length, n = word.length, d = [], i, j;
    for (i = 0; i <= m; i++) { d.push([i]); }
    for (j = 1; j <= n; j++) { d[0][j] = j; }
    for (i = 1; i <= m; i++) {
      for (j = 1; j <= n; j++) {
        var cost = w[i - 1] === word[j - 1] ? 0 : 1;
        d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost);
        if (i > 1 && j > 1 && w[i - 1] === word[j - 2] && w[i - 2] === word[j - 1]) {
          d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
        }
      }
    }
    return Math.min.apply(null, d[m]);
  }

  // Whether w's letters are in word, in order, from its first: "shu" in
  // "schumann", "bthvn" in "beethoven".
  function abbreviates(w, word) {
    if (w[0] !== word[0]) return false;
    for (var i = 1, j = 1; i < w.length; i++, j++) {
      j = word.indexOf(w[i], j);
      if (j < 0) return false;
    }
    return true;
  }

  // A search word matches a row if the row contains it, or, from 3 letters,
  // abbreviates one of its words; or, from 5 letters, if the start of one of its words is
  // within a typo of it (two from 8 letters): "shuman" finds Schumann.
  // Numbers must be exact: "68" doesn't find 66.
  function matches(w, row) {
    if (row.text.indexOf(w) >= 0) return true;
    if (/\d/.test(w)) return false;
    var allowed = w.length < 5 ? 0 : w.length < 8 ? 1 : 2;
    return row.words.some(function (word) {
      return (w.length >= 3 && abbreviates(w, word)) ||
        (allowed > 0 && word.length >= w.length - allowed && prefixDistance(w, word) <= allowed);
    });
  }

  function filter() {
    var words = fold(input.value).split(/\s+/).filter(Boolean);
    var shown = 0;
    rows.forEach(function (row, i) {
      var match = words.every(function (w) { return matches(w, texts[i]); });
      row.hidden = !match;
      if (match) shown++;
    });
    empty.hidden = shown > 0;
  }

  // What each order compares, most significant first.
  function num(row, name) { return Number(row.dataset[name]); }
  var keys = {
    composer: function () { return []; },
    title: function (r) { return [num(r, "title")]; },
    opus: function (r) { return [num(r, "opus")]; },
    difficulty: function (r) {
      var a = num(r, "rhythm"), b = num(r, "notes");
      return [Math.max(a, b), Math.min(a, b)];
    },
    rhythm: function (r) { return [num(r, "rhythm"), num(r, "notes")]; },
    notes: function (r) { return [num(r, "notes"), num(r, "rhythm")]; },
    length: function (r) { return [num(r, "length")]; }
  };
  var sort = "composer", desc = false;

  function apply() {
    var key = keys[sort], sign = desc ? -1 : 1;
    rows.slice().sort(function (a, b) {
      var ka = key(a), kb = key(b);
      for (var i = 0; i < ka.length; i++) {
        if (ka[i] !== kb[i]) return sign * (ka[i] - kb[i]);
      }
      return sign * (num(a, "default") - num(b, "default"));
    }).forEach(function (row) { body.appendChild(row); });
    headings.forEach(function (th) {
      if (th.dataset.sort === sort) th.setAttribute("aria-sort", desc ? "descending" : "ascending");
      else th.removeAttribute("aria-sort");
    });
  }

  function save() {
    var params = new URLSearchParams(location.search);
    var q = input.value.trim();
    if (q) params.set("q", q); else params.delete("q");
    if (sort === "composer") params.delete("sort"); else params.set("sort", sort);
    if (desc) params.set("order", "desc"); else params.delete("order");
    var query = params.toString();
    history.replaceState(null, "", location.pathname + (query ? "?" + query : "") + location.hash);
  }

  var params = new URLSearchParams(location.search);
  if (keys[params.get("sort")]) sort = params.get("sort");
  desc = params.get("order") === "desc";
  apply();
  if (params.get("q")) {
    input.value = params.get("q");
    filter();
  }

  // The search box stays on screen as the table scrolls under it: searching
  // from down the table brings the table's start back under the box, or the
  // rows found could be out of sight above.
  input.addEventListener("input", function () {
    filter();
    save();
    var under = table.getBoundingClientRect().top - input.getBoundingClientRect().bottom;
    if (under < 0) window.scrollBy(0, under);
  });

  // Back to the top: shown once the visitor is a screen down the page.
  var toTop = document.getElementById("to-top");
  function showToTop() { toTop.hidden = window.scrollY < window.innerHeight; }
  window.addEventListener("scroll", showToTop, { passive: true });
  showToTop();
  toTop.addEventListener("click", function () {
    var still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: 0, behavior: still ? "auto" : "smooth" });
  });

  headings.forEach(function (th) {
    th.querySelector("button").addEventListener("click", function () {
      if (th.dataset.sort === sort) desc = !desc;
      else { sort = th.dataset.sort; desc = false; }
      apply();
      save();
    });
  });
})();
