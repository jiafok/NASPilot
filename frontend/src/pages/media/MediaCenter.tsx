import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  ApiOutlined,
  CloudDownloadOutlined,
  DeleteOutlined,
  EditOutlined,
  FolderOpenOutlined,
  GlobalOutlined,
  HistoryOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  RedoOutlined,
  ReloadOutlined,
  StopOutlined,
  SyncOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import api from '../../utils/api';

const { Title, Text } = Typography;
const { Search } = Input;

// ── types ──────────────────────────────────────────────────────────────
type MediaMode = 'video' | 'audio' | 'both';
type NetworkMode = 'system' | 'direct' | 'http' | 'socks5' | 'docker_vpn';
type ScheduleType = 'manual' | 'hourly' | 'every_6h' | 'every_12h' | 'daily' | 'cron';

interface Subscription {
  id: number;
  name: string;
  provider: string;
  url: string;
  type: string;
  enabled: boolean;
  media_mode: MediaMode;
  video_height: number;
  audio_format: string;
  download_path: string | null;
  number_prefix: boolean;
  schedule_type: ScheduleType;
  cron_expr: string | null;
  max_items_per_sync: number;
  rate_limit: string | null;
  network_mode: NetworkMode;
  proxy_url: string | null;
  proxy_username: string | null;
  vpn_container: string | null;
  last_check_time: string | null;
  last_error: string | null;
  download_count: number;
  status: 'idle' | 'syncing' | 'disabled' | 'error';
}

interface Task {
  id: number;
  subscription_id: number | null;
  subscription_name: string | null;
  provider: string;
  media_id: string;
  title: string | null;
  status: string;
  progress: number;
  speed: string | null;
  downloaded_bytes: number;
  total_bytes: number;
  eta: string | null;
  error_message: string | null;
  attempts: number;
  updated_time: string;
}

interface HistoryRow {
  id: number;
  provider: string;
  media_id: string;
  title: string | null;
  channel_name: string | null;
  playlist_name: string | null;
  file_path: string | null;
  file_size: number | null;
  download_time: string | null;
  status: string;
  media_type: string | null;
  duration: number | null;
  created_time: string;
}

interface ProviderOption {
  value: string;
  label: string;
  implemented: boolean;
  types: string[];
}

interface HealthPayload {
  ytdlp_installed: boolean;
  ytdlp_version: string | null;
  ffmpeg_installed: boolean;
  ffmpeg_version: string | null;
  status: string;
  detail: string | null;
  network: NetworkReport;
  directories: { video: string; audio: string };
  active_downloads: number;
  queued_tasks: number;
}

interface NetCheck {
  label?: string;
  url: string;
  http_status: number | null;
  reachable: boolean;
}

interface NetworkReport {
  ok: boolean;
  proxy: string | null;
  detail: string | null;
  checks: Record<string, NetCheck>;
  provider?: string;
  display_name?: string;
  providers?: Record<string, NetworkReport>;
}

interface MediaServer {
  kind: string;
  configured: boolean;
  base_url: string | null;
  library_paths: string[];
  sample_links: { label: string; url: string }[];
}

interface FormValues {
  name: string;
  provider: string;
  url: string;
  type: string;
  enabled: boolean;
  media_mode: MediaMode;
  video_height: number;
  audio_format: string;
  download_path?: string;
  number_prefix: boolean;
  schedule_type: ScheduleType;
  cron_expr?: string;
  max_items_per_sync: number;
  rate_limit?: string;
  network_mode: NetworkMode;
  proxy_url?: string;
  proxy_username?: string;
  proxy_password?: string;
  vpn_container?: string;
}

// ── constants ──────────────────────────────────────────────────────────
const PROVIDER_COLOR: Record<string, string> = {
  youtube: 'red',
  bilibili: 'pink',
  podcast: 'gold',
  soundcloud: 'orange',
  vimeo: 'cyan',
  twitch: 'purple',
  x: 'default',
  custom: 'blue',
  ximalaya: 'volcano',
  xiaoyuzhou: 'geekblue',
};

const MODE_LABEL: Record<MediaMode, string> = {
  video: '仅视频',
  audio: '仅音频',
  both: '视频+音频',
};

const MODE_COLOR: Record<MediaMode, string> = { video: 'blue', audio: 'purple', both: 'green' };

const TYPE_LABEL: Record<string, string> = {
  video: '单个视频',
  playlist: '播放列表',
  channel: '频道订阅',
};

const HEIGHT_OPTIONS = [
  { value: 480, label: '480P' },
  { value: 720, label: '720P' },
  { value: 1080, label: '1080P' },
  { value: 0, label: '原画（最佳）' },
];

const AUDIO_OPTIONS = [
  { value: 'mp3', label: 'MP3（有损，兼容最好）' },
  { value: 'best', label: '原流（零转码，音质最高）' },
  { value: 'm4a', label: 'M4A / AAC' },
  { value: 'opus', label: 'Opus' },
  { value: 'flac', label: 'FLAC（转码）' },
];

const SCHEDULE_OPTIONS = [
  { value: 'manual', label: '仅手动' },
  { value: 'hourly', label: '每小时' },
  { value: 'every_6h', label: '每 6 小时' },
  { value: 'every_12h', label: '每 12 小时' },
  { value: 'daily', label: '每天' },
  { value: 'cron', label: '自定义 Cron' },
];

const SCHEDULE_LABEL: Record<string, string> = Object.fromEntries(
  SCHEDULE_OPTIONS.map((o) => [o.value, o.label]),
);

const NETWORK_OPTIONS = [
  { value: 'system', label: '使用系统默认' },
  { value: 'direct', label: '直连（不走代理）' },
  { value: 'http', label: 'HTTP 代理' },
  { value: 'socks5', label: 'SOCKS5 代理' },
  { value: 'docker_vpn', label: 'Docker VPN 容器' },
];

const NETWORK_LABEL: Record<string, string> = Object.fromEntries(
  NETWORK_OPTIONS.map((o) => [o.value, o.label]),
);

const TASK_STATUS: Record<string, { color: string; text: string }> = {
  pending: { color: 'default', text: '等待中' },
  downloading: { color: 'processing', text: '下载中' },
  pausing: { color: 'warning', text: '暂停中' },
  paused: { color: 'warning', text: '已暂停' },
  processing: { color: 'cyan', text: '处理中' },
  cancelling: { color: 'warning', text: '取消中' },
  completed: { color: 'success', text: '已完成' },
  failed: { color: 'error', text: '失败' },
  cancelled: { color: 'default', text: '已取消' },
};

// ── helpers ────────────────────────────────────────────────────────────
function fmtBytes(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return '-';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 2)} ${units[i]}`;
}

function fmtTime(value: string | null | undefined): string {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '从未';
}

function apiError(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { msg?: string };
    return first?.msg ? String(first.msg) : fallback;
  }
  return fallback;
}

export type MediaView = 'subscriptions' | 'tasks' | 'history';

export default function MediaCenter({ view = 'subscriptions' }: { view?: MediaView }) {
  const navigate = useNavigate();

  const [subs, setSubs] = useState<Subscription[]>([]);
  const [subLoading, setSubLoading] = useState(false);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [search, setSearch] = useState('');
  const [providers, setProviders] = useState<ProviderOption[]>([]);
  const [health, setHealth] = useState<HealthPayload | null>(null);
  const [servers, setServers] = useState<MediaServer[]>([]);
  const [netTesting, setNetTesting] = useState(false);
  const [netReports, setNetReports] = useState<Record<string, NetworkReport>>({});
  const [netBusyIds, setNetBusyIds] = useState<number[]>([]);
  const [queueStats, setQueueStats] = useState<{ active: number; counts: Record<string, number>; concurrency: number } | null>(null);

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Subscription | null>(null);
  const [saving, setSaving] = useState(false);
  const [busyIds, setBusyIds] = useState<number[]>([]);
  const [form] = Form.useForm<FormValues>();
  const networkMode = Form.useWatch('network_mode', form);
  const scheduleType = Form.useWatch('schedule_type', form);
  const mediaMode = Form.useWatch('media_mode', form);

  // ── loaders ─────────────────────────────────────────────────────────
  const loadSubs = useCallback(async () => {
    setSubLoading(true);
    try {
      const res = await api.get<Subscription[]>('/media/subscriptions');
      setSubs(res.data);
    } catch {
      message.error('加载订阅失败');
    } finally {
      setSubLoading(false);
    }
  }, []);

  const loadTasks = useCallback(async () => {
    try {
      const [t, s] = await Promise.all([
        api.get<Task[]>('/media/tasks', { params: { limit: 100 } }),
        api.get<typeof queueStats>('/media/tasks/stats'),
      ]);
      setTasks(t.data);
      setQueueStats(s.data);
    } catch {
      /* polling errors are non-fatal */
    }
  }, []);

  const loadHistory = useCallback(
    async (p = page, ps = pageSize, kw = search) => {
      try {
        const res = await api.get<{ items: HistoryRow[]; total: number }>('/media/history', {
          params: { page: p, page_size: ps, search: kw || undefined },
        });
        setHistory(res.data.items);
        setHistoryTotal(res.data.total);
      } catch {
        message.error('加载历史失败');
      }
    },
    [page, pageSize, search],
  );

  const loadMeta = useCallback(async () => {
    try {
      const [p, h, s] = await Promise.all([
        api.get<ProviderOption[]>('/media/providers/simple'),
        api.get<HealthPayload>('/media/health'),
        api.get<MediaServer[]>('/media/mediaservers'),
      ]);
      setProviders(p.data);
      setHealth(h.data);
      if (h.data.network?.providers) setNetReports(h.data.network.providers);
      setServers(s.data);
    } catch {
      /* meta is best-effort */
    }
  }, []);

  useEffect(() => {
    loadSubs();
    loadTasks();
    loadMeta();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadHistory(page, pageSize, search);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize]);

  // live progress polling
  useEffect(() => {
    const timer = window.setInterval(() => {
      loadTasks();
      if (subs.some((s) => s.status === 'syncing')) loadSubs();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [loadTasks, loadSubs, subs]);

  // ── derived ─────────────────────────────────────────────────────────
  const stats = useMemo(() => {
    const enabled = subs.filter((s) => s.enabled).length;
    const active = tasks.filter((t) => ['pending', 'downloading', 'processing', 'paused'].includes(t.status)).length;
    const failed = tasks.filter((t) => t.status === 'failed').length;
    const total = subs.reduce((sum, s) => sum + (s.download_count || 0), 0);
    return { enabled, active, failed, total };
  }, [subs, tasks]);

  const providerLabel = useCallback(
    (value: string) => providers.find((p) => p.value === value)?.label || value,
    [providers],
  );

  // ── actions ─────────────────────────────────────────────────────────
  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({
      provider: 'youtube',
      type: 'channel',
      enabled: true,
      media_mode: 'video',
      video_height: 480,
      audio_format: 'mp3',
      number_prefix: false,
      schedule_type: 'every_6h',
      max_items_per_sync: 20,
      network_mode: 'system',
    });
    setModalOpen(true);
  };

  const openEdit = (record: Subscription) => {
    setEditing(record);
    form.setFieldsValue({
      name: record.name,
      provider: record.provider,
      url: record.url,
      type: record.type,
      enabled: record.enabled,
      media_mode: record.media_mode,
      video_height: record.video_height,
      audio_format: record.audio_format,
      download_path: record.download_path || '',
      number_prefix: record.number_prefix,
      schedule_type: record.schedule_type,
      cron_expr: record.cron_expr || '',
      max_items_per_sync: record.max_items_per_sync,
      rate_limit: record.rate_limit || '',
      network_mode: record.network_mode,
      proxy_url: record.proxy_url || '',
      proxy_username: record.proxy_username || '',
      proxy_password: '',
      vpn_container: record.vpn_container || '',
    });
    setModalOpen(true);
  };

  const onUrlBlur = async () => {
    const url = (form.getFieldValue('url') || '').trim();
    if (!url) return;
    try {
      const res = await api.get<{ provider: string; type: string; valid: boolean; message?: string }>(
        '/media/detect',
        { params: { url } },
      );
      if (res.data.provider) form.setFieldValue('provider', res.data.provider);
      if (res.data.type) form.setFieldValue('type', res.data.type);
      if (!res.data.valid && res.data.message) message.warning(res.data.message);
    } catch {
      /* detection is a convenience only */
    }
  };

  const submit = async () => {
    const values = await form.validateFields();
    const payload = {
      ...values,
      download_path: values.download_path?.trim() || null,
      rate_limit: values.rate_limit?.trim() || null,
      cron_expr: values.schedule_type === 'cron' ? values.cron_expr?.trim() || null : null,
      proxy_url: values.proxy_url?.trim() || null,
      proxy_username: values.proxy_username?.trim() || null,
      proxy_password: values.proxy_password || null,
      vpn_container: values.vpn_container?.trim() || null,
    };
    setSaving(true);
    try {
      if (editing) {
        await api.put(`/media/subscriptions/${editing.id}`, payload);
        message.success('订阅已更新');
      } else {
        await api.post('/media/subscriptions', payload);
        message.success('订阅已创建');
      }
      setModalOpen(false);
      await loadSubs();
    } catch (err: unknown) {
      message.error(apiError(err, '保存失败'));
    } finally {
      setSaving(false);
    }
  };

  const toggle = async (record: Subscription) => {
    try {
      await api.post(`/media/subscriptions/${record.id}/toggle`);
      await loadSubs();
    } catch (err: unknown) {
      message.error(apiError(err, '操作失败'));
    }
  };

  const remove = async (record: Subscription) => {
    try {
      await api.delete(`/media/subscriptions/${record.id}`);
      message.success('订阅已删除（历史保留）');
      await loadSubs();
    } catch (err: unknown) {
      message.error(apiError(err, '删除失败'));
    }
  };

  const sync = async (record: Subscription) => {
    setBusyIds((prev) => [...prev, record.id]);
    try {
      const res = await api.post<{ status: string; message?: string }>(`/media/sync/${record.id}`);
      message.info(res.data.message || '已开始同步');
      await loadSubs();
      await loadTasks();
    } catch (err: unknown) {
      message.error(apiError(err, '同步失败'));
    } finally {
      setBusyIds((prev) => prev.filter((id) => id !== record.id));
    }
  };

  const syncAll = async () => {
    try {
      const res = await api.post<{ status: string }[]>('/media/sync-all');
      message.info(`已开始 ${res.data.filter((r) => r.status === 'queued').length} 个同步任务`);
      await loadSubs();
    } catch (err: unknown) {
      message.error(apiError(err, '批量同步失败'));
    }
  };

  const netTestFor = useCallback(async (record: Subscription) => {
    setNetBusyIds((prev) => [...prev, record.id]);
    try {
      const res = await api.get<NetworkReport>('/media/network-test', {
        params: { subscription_id: record.id },
        timeout: 60000,
      });
      setNetReports((prev) => ({ ...prev, [record.provider]: res.data }));
      message[res.data.ok ? 'success' : 'error'](res.data.detail || '检测完成');
    } catch (err: unknown) {
      message.error(apiError(err, '网络检测失败'));
    } finally {
      setNetBusyIds((prev) => prev.filter((id) => id !== record.id));
    }
  }, []);

  const netTest = async () => {
    setNetTesting(true);
    try {
      await loadMeta();
      const res = await api.get<HealthPayload['network']>('/media/network-test', { timeout: 60000 });
      message[res.data.ok ? 'success' : 'error'](res.data.detail || '检测完成');
    } catch {
      message.error('网络检测失败');
    } finally {
      setNetTesting(false);
    }
  };

  const taskAction = async (taskId: number, action: 'pause' | 'resume' | 'cancel' | 'retry') => {
    try {
      const res = await api.post<{ status: string; message?: string }>(`/media/tasks/${taskId}/${action}`);
      if (res.data.status === 'not_running' || res.data.status === 'not_resumable') {
        message.warning(res.data.message || '当前状态不支持该操作');
      } else {
        message.success('已提交');
      }
      await loadTasks();
    } catch (err: unknown) {
      message.error(apiError(err, '操作失败'));
    }
  };

  const clearTasks = async () => {
    try {
      const res = await api.delete<{ deleted: number }>('/media/tasks');
      message.success(`已清除 ${res.data.deleted} 条已结束任务`);
      await loadTasks();
    } catch (err: unknown) {
      message.error(apiError(err, '清理失败'));
    }
  };

  const historyAction = async (row: HistoryRow, action: 'redl' | 'del') => {
    try {
      if (action === 'redl') {
        await api.post(`/media/history/${row.id}/redownload`);
        message.info('已加入下载队列');
        navigate('/media/tasks');
      } else {
        await api.delete(`/media/history/${row.id}`);
        message.success('记录已删除');
        await loadHistory(page, pageSize, search);
      }
    } catch (err: unknown) {
      message.error(apiError(err, action === 'redl' ? '重新下载失败' : '删除失败'));
    }
  };

  const openDirectory = (row: HistoryRow) => {
    if (!row.file_path) return;
    const dir = row.file_path.replace(/[\\/][^\\/]+$/, '');
    navigate(`/files?path=${encodeURIComponent(dir)}`);
  };

  // ── columns: subscriptions ──────────────────────────────────────────
  const subColumns = [
    {
      title: '名称',
      dataIndex: 'name',
      ellipsis: true,
      render: (name: string, r: Subscription) => (
        <Space direction="vertical" size={0}>
          <Text strong>{name}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{r.download_path || '默认目录'}</Text>
        </Space>
      ),
    },
    {
      title: '来源',
      dataIndex: 'provider',
      width: 130,
      render: (p: string) => <Tag color={PROVIDER_COLOR[p] || 'blue'}>{providerLabel(p)}</Tag>,
    },
    {
      title: 'URL',
      dataIndex: 'url',
      ellipsis: true,
      render: (url: string) => (
        <Tooltip title={url}>
          <a href={url} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>{url}</a>
        </Tooltip>
      ),
    },
    {
      title: '类型 / 规格',
      width: 170,
      render: (_: unknown, r: Subscription) => (
        <Space size={4} wrap>
          <Tag>{TYPE_LABEL[r.type] || r.type}</Tag>
          <Tag color={MODE_COLOR[r.media_mode]}>{MODE_LABEL[r.media_mode]}</Tag>
          {r.media_mode !== 'audio' && <Tag>{r.video_height ? `${r.video_height}P` : '原画'}</Tag>}
          {r.media_mode !== 'video' && <Tag color="geekblue">{r.audio_format}</Tag>}
        </Space>
      ),
    },
    {
      title: '调度',
      dataIndex: 'schedule_type',
      width: 110,
      render: (s: string, r: Subscription) => (
        <Text style={{ fontSize: 12 }}>
          {s === 'cron' ? `cron: ${r.cron_expr || '-'}` : SCHEDULE_LABEL[s] || s}
        </Text>
      ),
    },
    {
      title: '网络',
      dataIndex: 'network_mode',
      width: 130,
      render: (m: string, r: Subscription) => (
        <Tooltip title={r.proxy_url || r.vpn_container || '无'}>
          <Text style={{ fontSize: 12 }}>{NETWORK_LABEL[m] || m}</Text>
        </Tooltip>
      ),
    },
    {
      title: '已下载',
      dataIndex: 'download_count',
      width: 80,
      align: 'center' as const,
      render: (n: number) => <Text strong>{n}</Text>,
    },
    {
      title: '最后更新',
      dataIndex: 'last_check_time',
      width: 160,
      render: (v: string | null) => <Text style={{ fontSize: 12 }}>{fmtTime(v)}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (s: string, r: Subscription) => {
        if (s === 'syncing') return <Tag color="processing" icon={<SyncOutlined spin />}>同步中</Tag>;
        if (s === 'disabled') return <Tag>已禁用</Tag>;
        if (s === 'error') {
          return (
            <Tooltip title={r.last_error || ''}>
              <Tag color="error">异常</Tag>
            </Tooltip>
          );
        }
        return <Tag color="success">空闲</Tag>;
      },
    },
    {
      title: '启用',
      dataIndex: 'enabled',
      width: 70,
      render: (v: boolean, r: Subscription) => <Switch size="small" checked={v} onChange={() => toggle(r)} />,
    },
    {
      title: '操作',
      width: 250,
      render: (_: unknown, r: Subscription) => (
        <Space size={4} wrap>
          <Button
            size="small"
            type="primary"
            icon={<ThunderboltOutlined />}
            loading={busyIds.includes(r.id) || r.status === 'syncing'}
            onClick={() => sync(r)}
          >
            立即同步
          </Button>
          <Button
            size="small"
            icon={<GlobalOutlined />}
            loading={netBusyIds.includes(r.id)}
            onClick={() => netTestFor(r)}
          />
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)} />
          <Popconfirm title="删除该订阅？" description="下载历史会保留" onConfirm={() => remove(r)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // ── columns: tasks ──────────────────────────────────────────────────
  const taskColumns = [
    {
      title: '标题',
      dataIndex: 'title',
      ellipsis: true,
      render: (title: string | null, r: Task) => (
        <Space direction="vertical" size={0}>
          <Text>{title || r.media_id}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {r.subscription_name || '-'} · {r.media_id}
          </Text>
        </Space>
      ),
    },
    {
      title: '来源',
      dataIndex: 'provider',
      width: 110,
      render: (p: string) => <Tag color={PROVIDER_COLOR[p] || 'blue'}>{providerLabel(p)}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (s: string) => {
        const meta = TASK_STATUS[s] || { color: 'default', text: s };
        return <Tag color={meta.color}>{meta.text}</Tag>;
      },
    },
    {
      title: '进度',
      dataIndex: 'progress',
      width: 210,
      render: (_p: number, r: Task) => (
        <Space direction="vertical" size={0} style={{ width: '100%' }}>
          <Progress
            percent={Math.round(r.progress || 0)}
            size="small"
            status={
              r.status === 'failed' ? 'exception' : r.status === 'completed' ? 'success' : 'active'
            }
          />
          <Text type="secondary" style={{ fontSize: 12 }}>
            {fmtBytes(r.downloaded_bytes)} / {fmtBytes(r.total_bytes)}
          </Text>
        </Space>
      ),
    },
    {
      title: '速度',
      dataIndex: 'speed',
      width: 100,
      render: (v: string | null) => <Text style={{ fontSize: 12 }}>{v || '-'}</Text>,
    },
    {
      title: '剩余',
      dataIndex: 'eta',
      width: 80,
      render: (v: string | null) => <Text style={{ fontSize: 12 }}>{v || '-'}</Text>,
    },
    {
      title: '信息',
      dataIndex: 'error_message',
      ellipsis: true,
      render: (v: string | null) =>
        v ? (
          <Tooltip title={v}>
            <Text type="danger" style={{ fontSize: 12 }}>{v}</Text>
          </Tooltip>
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>-</Text>
        ),
    },
    {
      title: '操作',
      width: 250,
      render: (_: unknown, r: Task) => {
        const active = ['pending', 'downloading', 'processing'].includes(r.status);
        return (
          <Space size={4} wrap>
            {active && (
              <Button size="small" icon={<PauseCircleOutlined />} onClick={() => taskAction(r.id, 'pause')}>
                暂停
              </Button>
            )}
            {(r.status === 'paused' || r.status === 'failed' || r.status === 'cancelled') && (
              <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={() => taskAction(r.id, 'resume')}>
                继续
              </Button>
            )}
            {active && (
              <Popconfirm title="取消该任务？" onConfirm={() => taskAction(r.id, 'cancel')}>
                <Button size="small" danger icon={<StopOutlined />} />
              </Popconfirm>
            )}
            {(r.status === 'failed' || r.status === 'cancelled') && (
              <Button size="small" icon={<RedoOutlined />} onClick={() => taskAction(r.id, 'retry')}>
                重试
              </Button>
            )}
          </Space>
        );
      },
    },
  ];

  // ── columns: history ────────────────────────────────────────────────
  const historyColumns = [
    {
      title: '标题',
      dataIndex: 'title',
      ellipsis: true,
      render: (title: string | null, r: HistoryRow) => (
        <Space direction="vertical" size={0}>
          <Text>{title || r.media_id}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {[r.channel_name, r.playlist_name].filter(Boolean).join(' / ') || '-'} · {r.media_id}
          </Text>
        </Space>
      ),
    },
    {
      title: '来源',
      dataIndex: 'provider',
      width: 120,
      render: (p: string, r: HistoryRow) => (
        <Space size={4}>
          <Tag color={PROVIDER_COLOR[p] || 'blue'}>{providerLabel(p)}</Tag>
          {r.media_type && <Tag>{r.media_type === 'audio' ? 'MP3' : r.media_type === 'both' ? 'MP4+音频' : 'MP4'}</Tag>}
        </Space>
      ),
    },
    {
      title: '文件大小',
      dataIndex: 'file_size',
      width: 110,
      render: (v: number | null) => <Text style={{ fontSize: 12 }}>{fmtBytes(v)}</Text>,
    },
    {
      title: '下载时间',
      dataIndex: 'download_time',
      width: 170,
      render: (v: string | null) => <Text style={{ fontSize: 12 }}>{fmtTime(v)}</Text>,
    },
    {
      title: '文件路径',
      dataIndex: 'file_path',
      ellipsis: true,
      render: (v: string | null) => <Text type="secondary" style={{ fontSize: 12 }}>{v || '-'}</Text>,
    },
    {
      title: '操作',
      width: 260,
      render: (_: unknown, r: HistoryRow) => (
        <Space size={4} wrap>
          <Button size="small" icon={<FolderOpenOutlined />} disabled={!r.file_path} onClick={() => openDirectory(r)}>
            打开目录
          </Button>
          <Button size="small" icon={<RedoOutlined />} onClick={() => historyAction(r, 'redl')}>
            重新下载
          </Button>
          <Popconfirm title="删除该记录？" onConfirm={() => historyAction(r, 'del')}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // ── render ──────────────────────────────────────────────────────────
  const healthAlert = health && health.status !== 'ok' && (
    <Alert
      type={health.status === 'error' ? 'error' : 'warning'}
      showIcon
      style={{ marginBottom: 16 }}
      message={health.status === 'error' ? '下载依赖缺失' : '下载依赖不完整'}
      description={health.detail || undefined}
    />
  );

  const networkEntries = Object.entries(netReports);

  const networkAlert = networkEntries.length > 0 &&
    networkEntries.some(([, report]) => !report.ok) && (
    <Alert
      type="warning"
      showIcon
      style={{ marginBottom: 16 }}
      message="部分来源当前网络不可达"
      description={
        <div style={{ fontSize: 12 }}>
          <Space direction="vertical" size={2} style={{ width: '100%' }}>
            {networkEntries.map(([key, report]) => (
              <div key={key}>
                <Tag color={report.ok ? 'success' : 'error'}>{report.display_name || key}</Tag>
                <Text style={{ fontSize: 12 }}>
                  {Object.values(report.checks || {})
                    .map((c) => `${c.label || c.url}: ${c.reachable ? `✅ ${c.http_status}` : '❌'}`)
                    .join('　')}
                </Text>
              </div>
            ))}
          </Space>
          <div style={{ marginTop: 6 }}>
            受影响的来源请单独设置代理（编辑订阅 → 出网方式 → HTTP/SOCKS5），
            或设置环境变量 <code>MEDIA_PROXY_URL</code>；点订阅行的 🌐 按钮可用该订阅自己的代理策略复测。
          </div>
        </div>
      }
    />
  );

  const toolbar = (
    <Space wrap>
      <Button icon={<ReloadOutlined />} onClick={() => { loadSubs(); loadTasks(); loadHistory(1, pageSize, search); loadMeta(); }}>
        刷新
      </Button>
      <Button icon={<ApiOutlined />} loading={netTesting} onClick={netTest}>
        网络检测
      </Button>
      <Button icon={<CloudDownloadOutlined />} onClick={syncAll} disabled={!subs.some((s) => s.enabled)}>
        全部同步
      </Button>
      <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
        新增订阅
      </Button>
    </Space>
  );

  const statsRow = (
    <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
      <Col xs={12} sm={6}><Card size="small"><Statistic title="已启用订阅" value={stats.enabled} prefix={<PlayCircleOutlined />} /></Card></Col>
      <Col xs={12} sm={6}><Card size="small"><Statistic title="活动任务" value={stats.active} prefix={<SyncOutlined spin={stats.active > 0} />} /></Card></Col>
      <Col xs={12} sm={6}><Card size="small"><Statistic title="累计下载" value={stats.total} prefix={<HistoryOutlined />} /></Card></Col>
      <Col xs={12} sm={6}><Card size="small"><Statistic title="失败任务" value={stats.failed} valueStyle={{ color: stats.failed ? '#cf1322' : undefined }} /></Card></Col>
    </Row>
  );

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12, marginBottom: 16 }}>
        <div>
          <Title level={4} style={{ margin: 0 }}>媒体下载中心</Title>
          <Text type="secondary">
            统一订阅 / 下载 / 管理 · 支持 YouTube、Bilibili、Podcast RSS、SoundCloud、Vimeo 等
            {health?.ytdlp_version && ` · yt-dlp ${health.ytdlp_version}`}
            {health?.ffmpeg_version && ` · ffmpeg ${health.ffmpeg_version}`}
          </Text>
        </div>
        {toolbar}
      </div>

      {healthAlert}
      {networkAlert}
      {statsRow}

      <Card size="small">
        <Tabs
          activeKey={view}
          onChange={(key) => navigate(`/media/${key}`)}
          items={[
            {
              key: 'subscriptions',
              label: `媒体订阅 (${subs.length})`,
              children: (
                <Table
                  rowKey="id"
                  size="small"
                  loading={subLoading}
                  columns={subColumns}
                  dataSource={subs}
                  scroll={{ x: 1400 }}
                  pagination={{ pageSize: 10, hideOnSinglePage: true }}
                />
              ),
            },
            {
              key: 'tasks',
              label: `下载任务 (${tasks.length})`,
              children: (
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <Space wrap>
                    <Text type="secondary">
                      每 2 秒自动刷新
                      {queueStats && ` · 并发上限 ${queueStats.concurrency} · 运行中 ${queueStats.active}`}
                    </Text>
                    <Button size="small" icon={<ReloadOutlined />} onClick={loadTasks}>刷新</Button>
                    <Popconfirm title="清除已完成 / 已取消的任务？" onConfirm={clearTasks}>
                      <Button size="small" danger>清除已结束</Button>
                    </Popconfirm>
                  </Space>
                  <Table
                    rowKey="id"
                    size="small"
                    columns={taskColumns}
                    dataSource={tasks}
                    scroll={{ x: 1200 }}
                    pagination={{ pageSize: 10, hideOnSinglePage: true }}
                  />
                </Space>
              ),
            },
            {
              key: 'history',
              label: `历史记录 (${historyTotal})`,
              children: (
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <Space wrap>
                    <Search
                      allowClear
                      placeholder="搜索标题 / 频道 / 播放列表 / media id"
                      style={{ width: 340, maxWidth: '100%' }}
                      defaultValue={search}
                      onSearch={(v) => {
                        setSearch(v);
                        setPage(1);
                        loadHistory(1, pageSize, v);
                      }}
                    />
                    <Button icon={<ReloadOutlined />} onClick={() => loadHistory(page, pageSize, search)}>
                      刷新
                    </Button>
                  </Space>
                  <Table
                    rowKey="id"
                    size="small"
                    columns={historyColumns}
                    dataSource={history}
                    scroll={{ x: 1200 }}
                    pagination={{
                      current: page,
                      pageSize,
                      total: historyTotal,
                      showSizeChanger: true,
                      onChange: (p, ps) => {
                        setPage(p);
                        setPageSize(ps);
                      },
                    }}
                  />
                </Space>
              ),
            },
          ]}
        />
      </Card>

      {servers.length > 0 && (
        <Card size="small" style={{ marginTop: 16 }} title="媒体服务器集成">
          <Row gutter={[16, 16]}>
            {servers.map((s) => (
              <Col xs={24} md={8} key={s.kind}>
                <Card size="small" type="inner" title={<Space><GlobalOutlined />{s.kind.toUpperCase()}
                  <Badge status={s.configured ? 'success' : 'default'} text={s.configured ? '已配置' : '未配置'} />
                </Space>}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    建议媒体库目录：{s.library_paths.join('、')}
                  </Text>
                  <Divider style={{ margin: '8px 0' }} />
                  {s.configured ? (
                    <Space direction="vertical" size={4}>
                      {s.sample_links.map((l) => (
                        <a key={l.url} href={l.url} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>
                          {l.label}
                        </a>
                      ))}
                    </Space>
                  ) : (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      设置环境变量 {s.kind.toUpperCase()}_URL 后即可一键跳转
                    </Text>
                  )}
                </Card>
              </Col>
            ))}
          </Row>
          {health?.directories && (
            <Descriptions size="small" column={2} style={{ marginTop: 12 }}>
              <Descriptions.Item label="视频根目录">{health.directories.video}</Descriptions.Item>
              <Descriptions.Item label="音频根目录">{health.directories.audio}</Descriptions.Item>
            </Descriptions>
          )}
        </Card>
      )}

      {/* ── create / edit modal ───────────────────────────────────── */}
      <Modal
        title={editing ? '编辑订阅' : '新增媒体订阅'}
        open={modalOpen}
        onOk={submit}
        confirmLoading={saving}
        onCancel={() => setModalOpen(false)}
        width={760}
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Space size={12} style={{ display: 'flex' }} align="start">
            <Form.Item name="provider" label="来源" rules={[{ required: true }]} style={{ flex: 1, minWidth: 160 }}>
              <Select
                options={providers.map((p) => ({
                  value: p.value,
                  label: p.implemented ? p.label : `${p.label}（预留）`,
                  disabled: !p.implemented,
                }))}
              />
            </Form.Item>
            <Form.Item name="type" label="订阅类型" style={{ flex: 1, minWidth: 140 }}>
              <Select
                options={Object.entries(TYPE_LABEL).map(([value, label]) => ({ value, label }))}
              />
            </Form.Item>
            <Form.Item name="enabled" label="启用" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>

          <Form.Item
            name="url"
            label="URL"
            rules={[{ required: true, message: '请输入 URL' }]}
            extra="粘贴链接后会自动识别来源与类型：视频 / 播放列表 / 频道 / RSS 订阅地址"
          >
            <Input placeholder="https://www.youtube.com/@Mr.EnglishChannel" onBlur={onUrlBlur} />
          </Form.Item>

          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="例如：Mr English Channel / BBC Learning English" maxLength={255} />
          </Form.Item>

          <Space size={12} style={{ display: 'flex' }} align="start">
            <Form.Item name="media_mode" label="下载格式" style={{ flex: 1, minWidth: 150 }}>
              <Select
                options={(['video', 'audio', 'both'] as MediaMode[]).map((v) => ({ value: v, label: MODE_LABEL[v] }))}
              />
            </Form.Item>
            {mediaMode !== 'audio' && (
              <Form.Item name="video_height" label="视频清晰度" style={{ flex: 1, minWidth: 140 }}>
                <Select options={HEIGHT_OPTIONS} />
              </Form.Item>
            )}
            {mediaMode !== 'video' && (
              <Form.Item name="audio_format" label="音频格式" style={{ flex: 1, minWidth: 200 }}>
                <Select options={AUDIO_OPTIONS} />
              </Form.Item>
            )}
          </Space>

          <Space size={12} style={{ display: 'flex' }} align="start">
            <Form.Item name="schedule_type" label="调度" style={{ flex: 1, minWidth: 160 }}>
              <Select options={SCHEDULE_OPTIONS} />
            </Form.Item>
            {scheduleType === 'cron' && (
              <Form.Item
                name="cron_expr"
                label="Cron 表达式"
                rules={[{ required: true, message: '例如 0 */6 * * *' }]}
                style={{ flex: 2, minWidth: 220 }}
              >
                <Input placeholder="0 */6 * * *" />
              </Form.Item>
            )}
          </Space>

          <Space size={12} style={{ display: 'flex' }} align="start">
            <Form.Item name="max_items_per_sync" label="每次最多下载" style={{ flex: 1, minWidth: 150 }}>
              <InputNumber min={1} max={500} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="rate_limit" label="限速（可选）" extra="如 5M / 500K，留空不限" style={{ flex: 1, minWidth: 150 }}>
              <Input placeholder="5M" />
            </Form.Item>
            <Form.Item name="number_prefix" label="文件名加序号" valuePropName="checked" extra="001-标题">
              <Switch />
            </Form.Item>
          </Space>

          <Form.Item
            name="download_path"
            label="下载目录（可选）"
            extra="留空使用默认：视频 /Media/Video/<来源>/<频道>/<播放列表>，音频 /Media/Audio/..."
          >
            <Input placeholder="/Media/Video" maxLength={1024} />
          </Form.Item>

          <Divider plain>网络策略</Divider>
          <Space size={12} style={{ display: 'flex' }} align="start">
            <Form.Item name="network_mode" label="出网方式" style={{ flex: 1, minWidth: 180 }}>
              <Select options={NETWORK_OPTIONS} />
            </Form.Item>
            {networkMode === 'docker_vpn' && (
              <Form.Item
                name="vpn_container"
                label="VPN 容器名"
                extra="需在 compose 中用 network_mode: container:<名称>"
                style={{ flex: 2, minWidth: 220 }}
              >
                <Input placeholder="gluetun / v2raya / clash" />
              </Form.Item>
            )}
          </Space>
          {(networkMode === 'http' || networkMode === 'socks5') && (
            <>
              <Form.Item
                name="proxy_url"
                label="代理地址"
                rules={[{ required: true, message: '请填写代理地址' }]}
                extra={
                  networkMode === 'socks5'
                    ? 'socks5://host.docker.internal:1080'
                    : 'http://host.docker.internal:7890'
                }
              >
                <Input placeholder={networkMode === 'socks5' ? 'socks5://host:port' : 'http://host:port'} />
              </Form.Item>
              <Space size={12} style={{ display: 'flex' }} align="start">
                <Form.Item name="proxy_username" label="代理用户名" style={{ flex: 1 }}>
                  <Input autoComplete="off" />
                </Form.Item>
                <Form.Item name="proxy_password" label="代理密码" style={{ flex: 1 }}>
                  <Input.Password autoComplete="new-password" placeholder={editing?.proxy_username ? '留空表示不修改' : ''} />
                </Form.Item>
              </Space>
            </>
          )}

          <Alert
            type="info"
            showIcon
            message="目录与文件命名"
            description="文件默认使用原始标题；字幕（.srt）与封面（.jpg）沿用同名。目录： 根目录 / 来源 / 频道 / 播放列表。勾选「文件名加序号」则输出 001-标题.ext。"
          />
        </Form>
      </Modal>
    </div>
  );
}
