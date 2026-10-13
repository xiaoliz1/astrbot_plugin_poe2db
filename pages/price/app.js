const bridge = window.AstrBotPluginPage || window.AstrBotPluginView;
const button = document.getElementById("refresh");
const statusNode = document.getElementById("status");

function showStatus(data) {
  statusNode.textContent = data.message || "暂无采集状态。";
  statusNode.dataset.state = data.state || "idle";
  button.disabled = data.state === "running";

  const tables = data.tables || {};
  document.getElementById("international-time").textContent =
    tables.international?.ready
      ? `图片已生成：${tables.international.updated_at || "时间未知"}`
      : "尚无已生成图片";
  document.getElementById("national-time").textContent =
    tables.national?.ready
      ? `图片已生成：${tables.national.updated_at || "时间未知"}`
      : "尚无已生成图片";
}

async function loadStatus() {
  const status = await bridge.apiGet("price/status");
  showStatus(status);
  return status;
}

const delay = (milliseconds) =>
  new Promise((resolve) => window.setTimeout(resolve, milliseconds));

async function waitForCompletion() {
  for (let attempt = 0; attempt < 240; attempt += 1) {
    await delay(1500);
    const status = await loadStatus();
    if (status.state !== "running") return;
  }
  statusNode.textContent = "采集仍在后台运行，可稍后刷新页面查看结果。";
  button.disabled = false;
}

button.addEventListener("click", async () => {
  button.disabled = true;
  statusNode.textContent = "正在启动手动采集……";
  statusNode.dataset.state = "running";
  try {
    const status = await bridge.apiPost("price/refresh", {});
    showStatus(status);
    if (status.state === "running") await waitForCompletion();
  } catch (error) {
    statusNode.textContent = `启动采集失败：${error?.message || "请检查 AstrBot 插件日志。"}`;
    statusNode.dataset.state = "failed";
    button.disabled = false;
  }
});

await bridge.ready();
try {
  const status = await loadStatus();
  if (status.state === "running") await waitForCompletion();
} catch (error) {
  statusNode.textContent = `无法读取采集状态：${error?.message || "请稍后刷新页面。"}`;
  statusNode.dataset.state = "failed";
}
