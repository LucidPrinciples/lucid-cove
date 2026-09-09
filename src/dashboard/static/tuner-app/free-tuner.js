/* Habit-floor doors on the house shell. No second player. */

document.getElementById("dailyBtn")?.addEventListener("click", () => {
  document.getElementById("freq-badge")?.click();
});

document.getElementById("tuneBtn")?.addEventListener("click", () => {
  if (typeof lchGoto === "function") lchGoto("/tune");
});

document.getElementById("playlistsBtn")?.addEventListener("click", () => {
  if (typeof lchGoto === "function") lchGoto("/playlists");
});

document.getElementById("btn-go-deeper")?.addEventListener("click", () => {
  if (typeof lchGoto === "function") lchGoto("/deeper");
});
