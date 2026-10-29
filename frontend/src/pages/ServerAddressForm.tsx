import { useState, type FormEvent } from 'react';
import { isGatewayStatus, normalizeServerAddress, probeServer, saveServerAddress } from '../utils/serverAddress';

interface Props {
  initialAddress?: string;
  onConnected: (address: string) => Promise<void> | void;
  onCancel?: () => void;
}

export default function ServerAddressForm({ initialAddress = '', onConnected, onCancel }: Props) {
  const [address, setAddress] = useState(initialAddress);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // Set when the probe failed: the address may still be usable, so offer to
  // continue instead of dead-ending the user on a flaky tunnel.
  const [canSkip, setCanSkip] = useState(false);

  const finish = async (normalized: string) => {
    const saved = await saveServerAddress(normalized);
    await onConnected(saved);
  };

  const connect = async (event?: FormEvent) => {
    event?.preventDefault();
    setBusy(true);
    setError('');
    setCanSkip(false);
    try {
      const normalized = normalizeServerAddress(address);
      const probe = await probeServer(normalized);

      if (probe.native.state === 'unreachable') {
        setCanSkip(true);
        throw new Error(
          `连不上 ${normalized}（${probe.native.reason === 'timeout' ? '请求超时' : '网络不通'}）。` +
            '请确认 NAS 已开机；用域名访问时还要确认 Cloudflare Tunnel / 内网穿透在线。' +
            '网络较慢时可以直接点「仍然使用此地址」，登录时会再试一次。',
        );
      }
      if (probe.native.state === 'http-error' && isGatewayStatus(probe.native.status)) {
        setCanSkip(true);
        throw new Error(
          `域名可以解析，但反向代理连不上 NAS（HTTP ${probe.native.status}）。` +
            '这通常是隧道断开或 NAS 上的容器没在运行，不是地址填错。' +
            '恢复之后点「仍然使用此地址」即可重试。',
        );
      }
      if (probe.native.state === 'http-error') {
        throw new Error(
          `已连上 ${normalized}，但这里返回 HTTP ${probe.native.status}，不是 NASPilot 服务。` +
            '请检查端口（通常是 8082）和反向代理的路径规则。',
        );
      }
      if (probe.browser.state !== 'ok') {
        // The server answers, but the app's own requests are refused: in
        // practice this is an outdated backend without the Capacitor origin
        // allowlist, not a wrong address.
        throw new Error(
          '服务器可以连接，但当前 NAS 版本还不允许 App 访问（缺少访问授权）。' +
            '请在 NAS 上更新到最新版本并重启容器后再试。',
        );
      }

      await finish(normalized);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法连接 NAS，请检查地址和网络。');
    } finally {
      setBusy(false);
    }
  };

  const skip = async () => {
    setBusy(true);
    setError('');
    try {
      await finish(normalizeServerAddress(address));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '地址格式不正确。');
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="ef-server-setup">
      <section className="ef-server-setup-panel">
        <span className="ef-server-setup-mark">NASPILOT · ENGLISHFLOW</span>
        <h1>{onCancel ? 'NAS 连接地址' : '连接你的 NAS'}</h1>
        <p>输入手机可以访问的 NAS 地址。App 会用它连接课程、账号和媒体。</p>
        <form onSubmit={(event) => void connect(event)}>
          <label htmlFor="nas-server-address">NAS 访问地址</label>
          <input
            id="nas-server-address"
            type="text"
            inputMode="url"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            placeholder="http://192.168.1.100:8082"
            value={address}
            onChange={(event) => setAddress(event.target.value)}
            required
          />
          <small>支持局域网 IP、主机名或 HTTPS 域名；不要填写 localhost。</small>
          <small>HTTP 仅建议在可信局域网使用；外网访问请使用 VPN 或 HTTPS。</small>
          {error && <p className="ef-server-setup-error" role="alert">{error}</p>}
          <div className="ef-server-setup-actions">
            {onCancel && <button type="button" className="ef-server-setup-secondary" onClick={onCancel}>取消</button>}
            {canSkip && !busy && (
              <button type="button" className="ef-server-setup-secondary" onClick={() => void skip()}>
                仍然使用此地址
              </button>
            )}
            <button type="submit" disabled={busy || !address.trim()}>{busy ? '正在测试连接…' : '测试并连接'}</button>
          </div>
        </form>
      </section>
    </main>
  );
}