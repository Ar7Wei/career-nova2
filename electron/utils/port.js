const net = require("net");

/** 检查某 TCP 端口能否在指定地址上绑定。 */
function canBind(port, host) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.once("listening", () => {
      server.close();
      resolve(true);
    });
    server.listen(port, host);
  });
}

/** 端口在 IPv4 与 IPv6 localhost 上是否都空闲。 */
async function isPortFree(port) {
  const [ipv4Free, ipv6Free] = await Promise.all([canBind(port, "127.0.0.1"), canBind(port, "::1")]);
  return ipv4Free && ipv6Free;
}

/** 能否连上本地某端口。 */
function canConnect(port, host = "127.0.0.1") {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    socket.setTimeout(1000);
    const done = (ok) => {
      socket.destroy();
      resolve(ok);
    };
    socket.once("connect", () => done(true));
    socket.once("timeout", () => done(false));
    socket.once("error", () => done(false));
    socket.connect(port, host);
  });
}

/** 从 preferred 起找第一个空闲端口。 */
async function findFreePort(preferred, maxOffset = 100) {
  for (let offset = 0; offset < maxOffset; offset++) {
    const port = preferred + offset;
    if (await isPortFree(port)) return port;
  }
  throw new Error(`从 ${preferred} 起 ${maxOffset} 个端口内无空闲端口`);
}

/** 等服务在某端口开始监听。 */
async function waitForPort(port, timeoutMs = 30000, intervalMs = 500) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if ((await canConnect(port, "127.0.0.1")) || (await canConnect(port, "::1"))) return true;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  return false;
}

module.exports = { isPortFree, findFreePort, waitForPort };
