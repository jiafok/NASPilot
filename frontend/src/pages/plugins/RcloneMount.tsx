import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Card, Descriptions, Space, Tag, Tooltip, Typography, message } from 'antd';
import {
  CloudDownloadOutlined,
  DisconnectOutlined,
  ReloadOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import PluginConfigForm from '../../components/PluginConfigForm';
import LogViewer from '../../components/LogViewer';
import type { PluginField } from '../../components/PluginConfigForm';
import api from '../../utils/api';

const { Text, Paragraph } = Typography;

const FIELDS: PluginField[] = [
  {
    key: 'alist_url',
    label: 'AList 地址',
    type: 'string',
    placeholder: 'https://alist.example.com',
    help: '含协议，不要带 /dav（插件会自动补上 WebDAV 路径）',
  },
  { key: 'username', label: '用户名', type: 'string', placeholder: 'admin' },
  {
    key: 'password',
    label: '密码',
    type: 'password',
    help: '保存后由 rclone obscure 加密写入 rclone.conf，不会明文落盘',
  },
  { key: 'remote_name', label: 'Remote 名称', type: 'string', default: 'alist', help: 'rclone.conf 里的段落名，保持默认即可' },
  { key: 'remote_path', label: '远程子路径', type: 'string', default: '/', help: '只挂载远程的某个子目录时填写，例如 /media' },

  {
    key: 'mount_point',
    label: '挂载点（容器内路径）',
    type: 'string',
    default: '/app/data/alist',
    required: true,
    help: '必须位于 /app/data 之下，这样宿主机才能通过 data 卷看到挂载内容',
  },
  {
    key: 'vfs_cache_mode',
    label: 'VFS 缓存模式',
    type: 'select',
    default: 'full',
    options: [
      { label: 'off — 不缓存（随机读性能最差）', value: 'off' },
      { label: 'minimal — 只缓存写前读取', value: 'minimal' },
      { label: 'writes — 只缓存写入', value: 'writes' },
      { label: 'full — 读写全缓存（推荐）', value: 'full' },
    ],
    help: '播放视频必须用 full，否则拖动进度条会反复回源',
  },
  { key: 'cache_size', label: '缓存大小上限', type: 'string', default: '10G', help: '例如 10G / 500G' },
  { key: 'cache_age_m', label: '缓存保留（分钟）', type: 'number', default: 15 },
  { key: 'dir_cache_time', label: '目录缓存时间', type: 'string', default: '5m', help: '越长越省请求，但远程目录变化发现得慢' },

  { key: 'read_only', label: '只读挂载', type: 'boolean', default: false, help: '开启后容器内与宿主机都无法写入 AList' },
  {
    key: 'allow_other',
    label: '允许其他用户访问',
    type: 'boolean',
    default: true,
    help: '必须开启，否则以非 root 身份运行的进程（含宿主机侧）看不到挂载内容',
  },
  { key: 'uid', label: '文件属主 UID', type: 'number', default: 0, help: '群晖常见为 0；如需其它用户读写可改成 1000' },
  { key: 'gid', label: '文件属主 GID', type: 'number', default: 0 },
  { key: 'umask', label: '权限掩码', type: 'string', default: '002' },
  { key: 'extra_args', label: '额外 rclone 参数', type: 'string', placeholder: '--transfers 8 --checkers 16', help: '空格分隔，原样追加到 rclone mount 命令' },

  {
    key: 'autostart',
    label: '容器启动时自动挂载',
    type: 'boolean',
    default: true,
    help: '开启后重建容器会自动恢复挂载，换机器部署只需带走过 data 卷',
  },
  {
    key: 'autoheal_seconds',
    label: '自愈检查间隔（秒）',
    type: 'number',
    default: 60,
    help: '重启后自动重建挂载的检查周期，0 = 关闭。挂载失效（Transport endpoint is not connected）时会在下个周期自动清理并重新挂载',
  },
  {
    key: 'manage_config',
    label: '由插件生成 rclone.conf',
    type: 'boolean',
    default: true,
    help: '关闭则使用已有的 rclone.conf（此时需自行填写下面的路径）',
  },
  {
    key: 'config_file',
    label: 'rclone.conf 路径',
    type: 'string',
    placeholder: '/app/data/rclone/rclone.conf',
    help: '留空则使用 /app/data/rclone/rclone.conf，随数据卷一起迁移',
  },
];

type Action = 'status' | 'mount' | 'unmount' | 'restart';

const ACTION_META: Record<Action, { label: string; icon: React.ReactNode; danger?: boolean }> = {
  status: { label: '检查状态', icon: <SyncOutlined /> },
  mount: { label: '挂载', icon: <CloudDownloadOutlined /> },
  unmount: { label: '卸载', icon: <DisconnectOutlined />, danger: true },
  restart: { label: '重新挂载', icon: <ReloadOutlined /> },
};

export default function RcloneMount() {
  const [pluginId, setPluginId] = useState<number | null>(null);
  const [pending, setPending] = useState<Action | null>(null);
  const [runResult, setRunResult] = useState<any>(null);
  const [status, setStatus] = useState<any>(null);

  const loadStatus = useCallback(async (id: number) => {
    try {
      const res = await api.post(`/plugins/${id}/run`, { action: 'status' }, { timeout: 60000 });
      const result = res.data?.result;
      setStatus(result);
      return result;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const pluginsRes = await api.get('/plugins');
        const p = (pluginsRes.data as any[]).find((x: any) => x.slug === 'rclone_mount');
        if (!p || cancelled) return;
        setPluginId(p.id);
        await loadStatus(p.id);
      } catch {
        /* the config form reports its own errors */
      }
    })();
    return () => { cancelled = true; };
  }, [loadStatus]);

  const runAction = async (action: Action) => {
    if (!pluginId) return;
    setPending(action);
    setRunResult(null);
    try {
      const res = await api.post(`/plugins/${pluginId}/run`, { action }, { timeout: 120000 });
      const result = res.data?.result;
      setRunResult({ action, ...result });
      if (action !== 'unmount' && action !== 'status') {
        // The mount is created by a detached process — re-read the real state
        // instead of trusting the action's own answer.
        const fresh = await loadStatus(pluginId);
        if (fresh) setRunResult({ action, ...result, verified: fresh });
      } else {
        await loadStatus(pluginId);
      }
      if (result?.status === 'ok') {
        message.success(`${ACTION_META[action].label}成功`);
      } else {
        message.error(result?.error || `${ACTION_META[action].label}失败`);
      }
    } catch (err: any) {
      message.error(err?.response?.data?.detail || `${ACTION_META[action].label}请求失败`);
    } finally {
      setPending(null);
    }
  };

  const mounted: boolean = !!status?.mounted;
  const stale: boolean = !!status?.stale_mount;
  const problems: string[] = Array.isArray(status?.problems) ? status.problems : [];

  return (
    <>
      <PluginConfigForm
        slug="rclone_mount"
        title="Rclone 挂载"
        description="在容器内把 AList / WebDAV 挂载为本地目录。配置、凭据与缓存全部存放在 /app/data 下，换一台 NAS 重建容器即可自动恢复。"
        fields={FIELDS}
        runResult={runResult}
        topContent={
          <Card
            title="挂载控制"
            style={{ marginBottom: 16 }}
            extra={
              <Tag color={stale ? 'red' : mounted ? 'green' : 'default'}>
                {stale ? '挂载已失效' : mounted ? '已挂载' : '未挂载'}
              </Tag>
            }
          >
            <Space wrap style={{ marginBottom: 16 }}>
              {(['status', 'mount', 'unmount', 'restart'] as Action[]).map((a) => (
                <Button
                  key={a}
                  type={a === 'mount' ? 'primary' : 'default'}
                  danger={ACTION_META[a].danger}
                  icon={ACTION_META[a].icon}
                  loading={pending === a}
                  disabled={pending !== null && pending !== a}
                  onClick={() => runAction(a)}
                >
                  {ACTION_META[a].label}
                </Button>
              ))}
            </Space>

            {stale && (
              <Alert
                type="error"
                showIcon
                style={{ marginBottom: 16 }}
                message="挂载点已失效（Transport endpoint is not connected）"
                description={
                  <span>
                    rclone 进程已经退出，但内核仍保留着挂载项，所以目录不可读写。
                    自愈守护会在下一个检查周期（{status?.self_heal_seconds || 60} 秒内）自动清理并重新挂载；
                    也可以立即点「重新挂载」。若持续失败，请在 NAS 宿主机执行{' '}
                    <Text code>sudo umount -l &lt;data目录&gt;/alist</Text>。
                  </span>
                }
              />
            )}

            {problems.length > 0 && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 16 }}
                message="环境检查未通过"
                description={
                  <ul style={{ margin: 0, paddingLeft: 20 }}>
                    {problems.map((p) => <li key={p}><Text code>{p}</Text></li>)}
                  </ul>
                }
              />
            )}

            <Descriptions size="small" column={{ xs: 1, sm: 2, md: 3 }} bordered>
              <Descriptions.Item label="挂载点">
                <Text code copyable>{status?.mount_point || '-'}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                {stale ? (
                  <Tag color="red">失效（需重新挂载）</Tag>
                ) : mounted ? (
                  <Tag color="green">正常</Tag>
                ) : (
                  <Tag>未挂载</Tag>
                )}
              </Descriptions.Item>
              <Descriptions.Item label="远程">{status?.remote || '-'}</Descriptions.Item>
              <Descriptions.Item label="挂载源">{status?.mount_source || '-'}</Descriptions.Item>
              <Descriptions.Item label="顶层条目数">
                {status?.entry_count ?? '-'}
              </Descriptions.Item>
              <Descriptions.Item label="缓存占用">{status?.cache_size || '-'}</Descriptions.Item>
              <Descriptions.Item label="rclone">
                <Tooltip title={status?.rclone_version || '未检测到'}>
                  {status?.rclone_version ? '已安装' : '未安装'}
                </Tooltip>
              </Descriptions.Item>
              <Descriptions.Item label="rclone.conf">
                {status?.config_exists ? <Tag color="green">存在</Tag> : <Tag>未生成</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="/dev/fuse">
                {status?.fuse_device ? <Tag color="green">可用</Tag> : <Tag color="red">缺失</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="自愈守护">
                {status?.self_heal_seconds > 0 ? (
                  <Tag color={status?.watchdog_running ? 'green' : 'orange'}>
                    {status?.watchdog_running
                      ? `运行中 · 每 ${status.self_heal_seconds}s`
                      : '未运行'}
                  </Tag>
                ) : (
                  <Tag>已关闭</Tag>
                )}
              </Descriptions.Item>
            </Descriptions>

            <Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0, fontSize: 12 }}>
              宿主机要看到挂载内容，data 卷必须以 <Text code>:rshared</Text> 挂载
              （Docker 默认 <Text code>rprivate</Text> 不会把容器内新建的挂载传播到宿主机）。
              compose 里还需要 <Text code>devices: /dev/fuse</Text> 与
              <Text code>cap_add: SYS_ADMIN</Text>。
            </Paragraph>
          </Card>
        }
        resultRenderer={(r: any) => <pre style={{ fontSize: 12 }}>{JSON.stringify(r, null, 2)}</pre>}
      />
      <div style={{ marginTop: 16 }}>
        <LogViewer source="rclone_mount" maxHeight={300} placeholder="等待运行..." collapsible defaultOpen={false} label="运行日志" />
      </div>
    </>
  );
}
