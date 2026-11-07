// The library's table: rows filtered as the visitor types (every word must
// appear in a row's title, composer, opus or tags), and sorted by the column
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
  rows.forEach(function (row, i) {
    row.dataset.folded = fold(row.dataset.search);
    row.dataset.default = i;
  });

  function filter() {
    var words = fold(input.value).split(/\s+/).filter(Boolean);
    var shown = 0;
    rows.forEach(function (row) {
      var match = words.every(function (w) { return row.dataset.folded.indexOf(w) >= 0; });
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

  input.addEventListener("input", function () { filter(); save(); });

  headings.forEach(function (th) {
    th.querySelector("button").addEventListener("click", function () {
      if (th.dataset.sort === sort) desc = !desc;
      else { sort = th.dataset.sort; desc = false; }
      apply();
      save();
    });
  });
})();
