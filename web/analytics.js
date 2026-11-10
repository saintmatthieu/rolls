// Counts in Plausible, with the score's id, the clicks on "Download MusicXML"
// and on "Play it in Orchestrion". A download stays on the page; a plain click
// on Play waits for the event to be sent before following the link (leaving
// the page would cancel it), but never more than a second: with Plausible
// blocked, the event is never sent.
document.addEventListener("click", (event) => {
  const download = event.target.closest("a[data-musicxml]");
  if (download) {
    window.plausible("Download MusicXML", { props: { score: download.dataset.musicxml } });
    return;
  }
  const link = event.target.closest("a[data-play]");
  if (!link) return;
  const elsewhere = event.button !== 0 || event.ctrlKey || event.metaKey ||
    event.shiftKey || event.altKey || (link.target && link.target !== "_self");
  if (elsewhere) {
    window.plausible("Play click", { props: { score: link.dataset.play } });
    return;
  }
  event.preventDefault();
  let gone = false;
  const go = () => {
    if (!gone) {
      gone = true;
      window.location.href = link.href;
    }
  };
  window.plausible("Play click", { props: { score: link.dataset.play }, callback: go });
  setTimeout(go, 1000);
});
