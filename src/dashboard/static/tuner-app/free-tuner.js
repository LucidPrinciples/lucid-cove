/* Habit-floor doors on the house shell. No second player. */

function _ltFieldCountdown() {
  var el = document.getElementById("fieldDesc");
  if (!el) return;
  if (typeof _tfCountdownStr === "function") {
    el.textContent = "Next in " + _tfCountdownStr();
  }
}

document.getElementById("dailyBtn")?.addEventListener("click", () => {
  document.getElementById("freq-badge")?.click();
});

document.getElementById("tuneBtn")?.addEventListener("click", () => {
  var count = (typeof tuneFlow !== "undefined" && tuneFlow && tuneFlow.todayCount) || 0;
  if (typeof _tfCanTuneAgain === "function" && !_tfCanTuneAgain(count)) {
    if (typeof showUpgradeModal === "function") showUpgradeModal();
    return;
  }
  if (typeof lchGoto === "function") lchGoto("/tune");
});

document.getElementById("playlistsBtn")?.addEventListener("click", () => {
  if (typeof lchGoto === "function") lchGoto("/playlists");
});

document.getElementById("btn-go-deeper")?.addEventListener("click", () => {
  if (typeof lchGoto === "function") lchGoto("/deeper");
});

_ltFieldCountdown();
setInterval(_ltFieldCountdown, 30000);
