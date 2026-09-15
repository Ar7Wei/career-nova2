/**
 * 不签名（noop）：开源免费工具，无代码签名证书。
 *
 * electron-builder 即便不配证书（CSC_LINK 空）也会下载 winCodeSign 去重签
 * extraResources 里裸放的 backend.exe；而 winCodeSign 解压要在缓存里建 darwin
 * 符号链接，需 SeCreateSymbolicLinkPrivilege（普通 shell 没有）→ 7-Zip 报错。
 * 把 win.signtoolOptions.sign 指到本 noop 脚本后，electron-builder 直接跳过签名，
 * 不再下载/解压 winCodeSign，也无需管理员或开发者模式。首发即不签名。
 */
module.exports = async function sign() {
  // 刻意为空：返回 undefined = 该文件不签名。
};
