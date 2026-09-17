const params = new URLSearchParams(location.search);
const tableId = params.get("table_id");
const root = document.querySelector<HTMLDivElement>("#root")!;
root.innerHTML = `<main><h1>The Lantern</h1><p>${tableId ? `Table ${tableId} provisioned` : "This device is not provisioned. Add ?table_id=T4."}</p><button ${tableId ? "" : "disabled"}>Start voice service</button><section id="status">Waiting for kitchen…</section></main>`;
if (tableId) {
  const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/realtime?table_id=${encodeURIComponent(tableId)}`);
  socket.onmessage = (event) => { const data = JSON.parse(event.data); document.querySelector("#status")!.textContent = data.type === "session_ready" ? "Connected — speak your order." : JSON.stringify(data); };
}
