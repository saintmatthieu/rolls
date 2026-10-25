// Filters the library's cards as the visitor types: every word must appear
// in a card's title, composer, opus or tags.
(function () {
  var input = document.getElementById("search");
  var cards = Array.prototype.slice.call(document.querySelectorAll("#cards .card"));
  var empty = document.getElementById("empty");
  function fold(text) {
    return text.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  }
  cards.forEach(function (card) { card.dataset.folded = fold(card.dataset.search); });
  input.addEventListener("input", function () {
    var words = fold(input.value).split(/\s+/).filter(Boolean);
    var shown = 0;
    cards.forEach(function (card) {
      var match = words.every(function (w) { return card.dataset.folded.indexOf(w) >= 0; });
      card.hidden = !match;
      if (match) shown++;
    });
    empty.hidden = shown > 0;
  });
})();
