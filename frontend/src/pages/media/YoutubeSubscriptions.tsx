import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
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
  CloudDownloadOutlined,
  DeleteOutlined,
  EditOutlined,
  FolderOpenOutlined,
  HistoryOutlined,
  PlayCircleOutlined,
  ApiOutlined,
  PlusOutlined,
  RedoOutlined,
  ReloadOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import api from '../../utils/api';

const { Title, Text } = Typography;
const { Search } = Input;

interface Subscription {
  id: number;
  name: string;
  url: string;
  type: string;
  enabled: boolean;
  media_mode: MediaMode;
  video_height: number;
  audio_format: string;
  audio_only: boolean;
  download_path: string | null;
  last_check_time: string | null;
  created_time: string;
  updated_time: string;
  download_count: number;
  status: 'idle' | 'syncing' | 'disabled';
}

interface DownloadTask {
  id: number;
  subscription_id: number | null;
  subscription_name: string | null;
  video_id: string;
  title: string | null;
  status: 'pending' | 'downloading' | 'processing' | 'completed' | 'failed';
  progress: number;
  speed: string | null;
  downloaded_bytes: number;
  total_bytes: number;
  eta: string | null;
  error_message: string | null;
  created_time: string;
  updated_time: string;
}

interface HistoryItem {
  id: number;
  video_id: string;
  title: string | null;
  channel: string | null;
  playlist: string | null;
  publish_date: string | null;
  download_time: string | null;
  file_path: string | null;
  file_size: number | null;
  status: string;
  media_type: string | null;
  created_time: string;
  subscription_id: number | null;
}

interface Health {
  ytdlp_installed: boolean;
  ytdlp_version: string | null;
  ytdlp_path: string | null;
  ffmpeg_installed: boolean;
  ffmpeg_version: string | null;
  status: 'ok' | 'degraded' | 'error';
  detail: string | null;
  proxy: string | null;
}

interface NetTest {
  proxy: string | null;
  ok: boolean;
  detail: string | null;
  checks: Record<string, { url: string; http_status: number | null; reachable: boolean }>;
}

interface SubscriptionFormValues {
  name: string;
  url: string;
  type: string;
  media_mode: MediaMode;
  video_height: number;
  audio_format: string;
  enabled: boolean;
  download_path?: string;
}

type MediaMode = 'video' | 'audio' | 'both';

const MEDIA_MODE_LABEL: Record<MediaMode, string> = {
  video: '仅 MP4',
  audio: '仅 MP3',
  both: 'MP4 + MP3',
};

const MEDIA_MODE_OPTIONS = (Object.keys(MEDIA_MODE_LABEL) as MediaMode[]).map((value) => ({
  value,
  label: MEDIA_MODE_LABEL[value],
}));

const MEDIA_MODE_COLOR: Record<MediaMode, string> = {
  video: 'blue',
  audio: 'purple',
  both: 'green',
};

// 0 = 跟随全局默认（后端 YOUTUBE_MAX_HEIGHT，默认 480p）
const HEIGHT_OPTIONS = [
  { value: 0, label: '默认 (480p)' },
  { value: 480, label: '480p' },
  { value: 720, label: '720p HD' },
  { value: 1080, label: '1080p Full HD' },
  { value: 1440, label: '1440p' },
  { value: 2160, label: '2160p 4K' },
];

const AUDIO_FORMAT_OPTIONS = [
  { value: 'mp3', label: 'MP3 (有损, 兼容最好)' },
  { value: 'best', label: '原流 (零转码, 音质最高)' },
  { value: 'm4a', label: 'M4A / AAC' },
  { value: 'opus', label: 'Opus' },
  { value: 'flac', label: 'FLAC (转码, 非真无损)' },
];

const AUDIO_FORMAT_COLOR: Record<string, string> = {
  mp3: 'default',
  best: 'gold',
  m4a: 'cyan',
  opus: 'geekblue',
  flac: 'volcano',
};

function audioFormatLabel(fmt: string): string {
  const found = AUDIO_FORMAT_OPTIONS.find((o) => o.value === fmt);
  return found ? found.label.split(' ')[0] : fmt || 'mp3';
}

const TYPE_LABEL: Record<string, string> = {
  channel: 'YouTube 频道',
  playlist: 'YouTube 播放列表',
  bilibili: 'Bilibili 合集',
  podcast: 'Podcast RSS',
  ximalaya: '喜马拉雅专辑',
  rss: '通用 RSS',
};

const TASK_STATUS: Record<string, { color: string; text: string }> = {
  pending: { color: 'default', text: '等待中' },
  downloading: { color: 'processing', text: '下载中' },
  processing: { color: 'cyan', text: '处理中' },
  completed: { color: 'success', text: '已完成' },
  failed: { color: 'error', text: '失败' },
};

function formatBytes(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return '-';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(i === 0 ? 0 : 2)} ${units[i]}`;
}

function formatTime(value: string | null | undefined): string {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '从未';
}

function apiErrorMessage(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
}

export default function YoutubeSubscriptions() {
  const navigate = useNavigate();
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [subLoading, setSubLoading] = useState(false);
  const [tasks, setTasks] = useState<DownloadTask[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyPageSize, setHistoryPageSize] = useState(20);
  const [historySearch, setHistorySearch] = useState('');
  const [health, setHealth] = useState<Health | null>(null);
  const [netTest, setNetTest] = useState<NetTest | null>(null);
  const [netTesting, setNetTesting] = useState(false);
  const [activeTab, setActiveTab] = useState('subs');

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Subscription | null>(null);
  const [saving, setSaving] = useState(false);
  const [syncingIds, setSyncingIds] = useState<number[]>([]);
  const [form] = Form.useForm<SubscriptionFormValues>();

  // ── data loading ────────────────────────────────────────────────────
  const loadSubscriptions = useCallback(async () => {
    setSubLoading(true);
    try {
      const res = await api.get<Subscription[]>('/youtube/subscriptions');
      setSubscriptions(res.data);
    } catch {
      message.error('加载订阅列表失败');
    } finally {
      setSubLoading(false);
    }
  }, []);

  const loadTasks = useCallback(async () => {
    try {
      const res = await api.get<DownloadTask[]>('/youtube/tasks', { params: { limit: 100 } });
      setTasks(res.data);
    } catch {
      /* polling failures are non-fatal */
    }
  }, []);

  const loadHistory = useCallback(async (page = historyPage, pageSize = historyPageSize, search = historySearch) => {
    try {
      const res = await api.get<{ items: HistoryItem[]; total: number }>('/youtube/history', {
        params: { page, page_size: pageSize, search: search || undefined },
      });
      setHistory(res.data.items);
      setHistoryTotal(res.data.total);
    } catch {
      message.error('加载下载历史失败');
    }
  }, [historyPage, historyPageSize, historySearch]);

  const runNetTest = useCallback(async () => {
    setNetTesting(true);
    try {
      const res = await api.get<NetTest>('/youtube/net-test', { timeout: 60000 });
      setNetTest(res.data);
    } catch {
      message.error('网络检测失败');
    } finally {
      setNetTesting(false);
    }
  }, []);

  const loadHealth = useCallback(async () => {
    try {
      const res = await api.get<Health>('/youtube/health');
      setHealth(res.data);
    } catch {
      setHealth(null);
    }
  }, []);

  useEffect(() => {
    loadSubscriptions();
    loadTasks();
    loadHistory(1, historyPageSize, '');
    loadHealth();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Live progress polling (task page + syncing badge)
  useEffect(() => {
    const timer = window.setInterval(() => {
      loadTasks();
      if (subscriptions.some((s) => s.status === 'syncing')) {
        loadSubscriptions();
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [loadTasks, loadSubscriptions, subscriptions]);

  // Keep the history list fresh after downloads finish
  useEffect(() => {
    if (activeTab === 'history') {
      loadHistory(historyPage, historyPageSize, historySearch);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, historyPage, historyPageSize]);

  // ── subscription actions ────────────────────────────────────────────
  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ type: 'channel', enabled: true, media_mode: 'video', video_height: 0, audio_format: 'mp3' });
    setModalOpen(true);
  };

  const openEdit = (record: Subscription) => {
    setEditing(record);
    form.setFieldsValue({
      name: record.name,
      url: record.url,
      type: record.type,
      media_mode: record.media_mode,
      video_height: record.video_height ?? 0,
      audio_format: record.audio_format || 'mp3',
      enabled: record.enabled,
      download_path: record.download_path || '',
    });
    setModalOpen(true);
  };

  const submit = async () => {
    const values = await form.validateFields();
    const payload = {
      ...values,
      download_path: values.download_path && values.download_path.trim() ? values.download_path.trim() : null,
    };
    setSaving(true);
    try {
      if (editing) {
        await api.put(`/youtube/subscriptions/${editing.id}`, payload);
        message.success('订阅已更新');
      } else {
        await api.post('/youtube/subscriptions', payload);
        message.success('订阅已创建，首次同步可在列表中点击“立即同步”');
      }
      setModalOpen(false);
      await loadSubscriptions();
    } catch (err: unknown) {
      message.error(apiErrorMessage(err, '保存失败'));
    } finally {
      setSaving(false);
    }
  };

  const toggle = async (record: Subscription) => {
    try {
      await api.post(`/youtube/subscriptions/${record.id}/toggle`);
      await loadSubscriptions();
    } catch {
      message.error('操作失败');
    }
  };

  const remove = async (record: Subscription) => {
    try {
      await api.delete(`/youtube/subscriptions/${record.id}`);
      message.success('订阅已删除（下载历史保留）');
      await loadSubscriptions();
    } catch {
      message.error('删除失败');
    }
  };

  const syncNow = async (record: Subscription) => {
    setSyncingIds((prev) => [...prev, record.id]);
    try {
      const res = await api.post<{ status: string; message?: string }>(`/youtube/sync/${record.id}`);
      message.info(res.data.message || (res.data.status === 'queued' ? '已加入同步队列' : '正在同步中'));
      await loadSubscriptions();
      await loadTasks();
    } catch {
      message.error('同步失败');
    } finally {
      setSyncingIds((prev) => prev.filter((id) => id !== record.id));
    }
  };

  const syncAll = async () => {
    try {
      const res = await api.post<{ status: string }[]>('/youtube/sync-all');
      message.info(`已加入 ${res.data.filter((r) => r.status === 'queued').length} 个同步任务`);
      await loadSubscriptions();
    } catch {
      message.error('批量同步失败');
    }
  };

  // ── history actions ────────────────────────────────────────────────
  const removeHistory = async (record: HistoryItem, deleteFile: boolean) => {
    try {
      await api.delete(`/youtube/history/${record.id}`, { params: { delete_file: deleteFile } });
      message.success('记录已删除');
      await loadHistory(historyPage, historyPageSize, historySearch);
    } catch {
      message.error('删除失败');
    }
  };

  const redownload = async (record: HistoryItem) => {
    try {
      await api.post(`/youtube/history/${record.id}/redownload`);
      message.info('已加入重新下载队列');
      setActiveTab('tasks');
      await loadTasks();
    } catch (err: unknown) {
      message.error(apiErrorMessage(err, '重新下载失败'));
    }
  };

  const openDirectory = (record: HistoryItem) => {
    if (!record.file_path) return;
    const dir = record.file_path.replace(/[\\/][^\\/]+$/, '');
    navigate(`/files?path=${encodeURIComponent(dir)}`);
  };

  // ── derived ────────────────────────────────────────────────────────
  const stats = useMemo(() => {
    const enabled = subscriptions.filter((s) => s.enabled).length;
    const active = tasks.filter((t) => ['pending', 'downloading', 'processing'].includes(t.status)).length;
    const completed = tasks.filter((t) => t.status === 'completed').length;
    const failed = tasks.filter((t) => t.status === 'failed').length;
    const totalDownloads = subscriptions.reduce((sum, s) => sum + (s.download_count || 0), 0);
    return { enabled, active, completed, failed, totalDownloads };
  }, [subscriptions, tasks]);

  // ── columns ────────────────────────────────────────────────────────
  const subColumns = [
    {
      title: '名称',
      dataIndex: 'name',
      ellipsis: true,
      render: (name: string, record: Subscription) => (
        <Space direction="vertical" size={0}>
          <Text strong>{name}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{record.download_path || '默认目录'}</Text>
        </Space>
      ),
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
      title: '类型',
      dataIndex: 'type',
      width: 150,
      render: (type: string, record: Subscription) => (
        <Space direction="vertical" size={0}>
          <Tag color="blue">{TYPE_LABEL[type] || type}</Tag>
          <Tag color={MEDIA_MODE_COLOR[record.media_mode] || 'blue'}>
            {MEDIA_MODE_LABEL[record.media_mode] || record.media_mode}
          </Tag>
          {record.media_mode !== 'audio' && (
            <Tag>{record.video_height ? `${record.video_height}p` : '480p'}</Tag>
          )}
          {record.media_mode !== 'video' && (
            <Tag color={AUDIO_FORMAT_COLOR[record.audio_format] || 'default'}>
              {audioFormatLabel(record.audio_format)}
            </Tag>
          )}
        </Space>
      ),
    },
    {
      title: '最新检查时间',
      dataIndex: 'last_check_time',
      width: 170,
      render: (value: string | null) => <Text style={{ fontSize: 12 }}>{formatTime(value)}</Text>,
    },
    {
      title: '下载总数',
      dataIndex: 'download_count',
      width: 90,
      align: 'center' as const,
      render: (count: number) => <Text strong>{count}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (status: string) => {
        if (status === 'syncing') return <Tag color="processing" icon={<SyncOutlined spin />}>同步中</Tag>;
        if (status === 'disabled') return <Tag>已禁用</Tag>;
        return <Tag color="success">空闲</Tag>;
      },
    },
    {
      title: '启用',
      dataIndex: 'enabled',
      width: 70,
      render: (enabled: boolean, record: Subscription) => (
        <Switch size="small" checked={enabled} onChange={() => toggle(record)} />
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 210,
      render: (_: unknown, record: Subscription) => (
        <Space size={4} wrap>
          <Button
            size="small"
            type="primary"
            icon={<SyncOutlined />}
            loading={syncingIds.includes(record.id) || record.status === 'syncing'}
            disabled={record.status === 'syncing'}
            onClick={() => syncNow(record)}
          >
            立即同步
          </Button>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(record)} />
          <Popconfirm title="删除该订阅？" description="下载历史会保留" onConfirm={() => remove(record)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const taskColumns = [
    {
      title: '标题',
      dataIndex: 'title',
      ellipsis: true,
      render: (title: string | null, record: DownloadTask) => (
        <Space direction="vertical" size={0}>
          <Text>{title || record.video_id}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {record.subscription_name || '-'} · {record.video_id}
          </Text>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (status: string) => {
        const meta = TASK_STATUS[status] || { color: 'default', text: status };
        return <Tag color={meta.color}>{meta.text}</Tag>;
      },
    },
    {
      title: '进度',
      dataIndex: 'progress',
      width: 220,
      render: (_progress: number, record: DownloadTask) => (
        <Space direction="vertical" size={0} style={{ width: '100%' }}>
          <Progress
            percent={Math.round(record.progress || 0)}
            size="small"
            status={record.status === 'failed' ? 'exception' : record.status === 'completed' ? 'success' : 'active'}
          />
          <Text type="secondary" style={{ fontSize: 12 }}>
            {formatBytes(record.downloaded_bytes)} / {formatBytes(record.total_bytes)}
          </Text>
        </Space>
      ),
    },
    {
      title: '网速',
      dataIndex: 'speed',
      width: 110,
      render: (speed: string | null) => <Text style={{ fontSize: 12 }}>{speed || '-'}</Text>,
    },
    {
      title: '剩余时间',
      dataIndex: 'eta',
      width: 100,
      render: (eta: string | null) => <Text style={{ fontSize: 12 }}>{eta || '-'}</Text>,
    },
    {
      title: '错误信息',
      dataIndex: 'error_message',
      ellipsis: true,
      render: (error: string | null) =>
        error ? (
          <Tooltip title={error}>
            <Text type="danger" style={{ fontSize: 12 }}>{error}</Text>
          </Tooltip>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_time',
      width: 170,
      render: (value: string) => <Text style={{ fontSize: 12 }}>{formatTime(value)}</Text>,
    },
  ];

  const historyColumns = [
    {
      title: '下载时间',
      dataIndex: 'download_time',
      width: 170,
      render: (value: string | null) => <Text style={{ fontSize: 12 }}>{formatTime(value)}</Text>,
    },
    {
      title: '标题',
      dataIndex: 'title',
      ellipsis: true,
      render: (title: string | null, record: HistoryItem) => (
        <Space direction="vertical" size={0}>
          <Text>{title || record.video_id}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {record.playlist || record.channel || '-'} · {record.video_id}
          </Text>
        </Space>
      ),
    },
    {
      title: '大小',
      dataIndex: 'file_size',
      width: 100,
      render: (size: number | null) => <Text style={{ fontSize: 12 }}>{formatBytes(size)}</Text>,
    },
    {
      title: '类型',
      dataIndex: 'media_type',
      width: 80,
      render: (type: string | null) => <Tag color={type === 'audio' ? 'purple' : 'blue'}>{type === 'audio' ? 'MP3' : 'MP4'}</Tag>,
    },
    {
      title: '路径',
      dataIndex: 'file_path',
      ellipsis: true,
      render: (path: string | null) => (
        <Text type="secondary" style={{ fontSize: 12 }}>{path || '-'}</Text>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 240,
      render: (_: unknown, record: HistoryItem) => (
        <Space size={4} wrap>
          <Button size="small" icon={<FolderOpenOutlined />} disabled={!record.file_path} onClick={() => openDirectory(record)}>
            打开目录
          </Button>
          <Button size="small" icon={<RedoOutlined />} onClick={() => redownload(record)}>重新下载</Button>
          <Popconfirm title="删除该记录？" onConfirm={() => removeHistory(record, false)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12, marginBottom: 16 }}>
        <div>
          <Title level={4} style={{ margin: 0 }}>媒体订阅中心</Title>
          <Text type="secondary">
            YouTube 频道 / 播放列表自动订阅下载 · 每 6 小时增量同步
            {health?.ytdlp_version && ` · yt-dlp ${health.ytdlp_version}`}
            {health?.ffmpeg_version && ` · ffmpeg ${health.ffmpeg_version}`}
          </Text>
        </div>
        <Space wrap>
          <Button icon={<ReloadOutlined />} onClick={() => { loadSubscriptions(); loadTasks(); loadHistory(1, historyPageSize, historySearch); loadHealth(); }}>
            刷新
          </Button>
          <Button
            icon={<ApiOutlined />}
            loading={netTesting}
            onClick={runNetTest}
          >
            网络检测
          </Button>
          <Button icon={<CloudDownloadOutlined />} onClick={syncAll} disabled={!subscriptions.some((s) => s.enabled)}>
            全部同步
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增订阅</Button>
        </Space>
      </div>

      {netTest && (
        <Alert
          type={netTest.ok ? 'success' : 'error'}
          showIcon
          closable
          onClose={() => setNetTest(null)}
          style={{ marginBottom: 16 }}
          message={netTest.ok ? '网络可直连 YouTube（API + 视频 CDN）' : '网络无法下载 YouTube 视频'}
          description={
            <div style={{ fontSize: 12 }}>
              <div>{netTest.detail}</div>
              <div style={{ marginTop: 4 }}>
                API: {netTest.checks.youtube_api?.reachable ? `✅ ${netTest.checks.youtube_api.http_status}` : '❌ 不可达'}
                {'　'}CDN: {netTest.checks.youtube_cdn?.reachable ? `✅ ${netTest.checks.youtube_cdn.http_status}` : '❌ 不可达'}
                {'　'}代理: {netTest.proxy || '未配置'}
              </div>
              {!netTest.ok && (
                <div style={{ marginTop: 4 }}>
                  需要在 <code>docker-compose.dev.yml</code> 的 environment 中加入
                  {' '}<code>- YOUTUBE_PROXY=http://host.docker.internal:7890</code>
                  {' '}（端口换成你自己的代理端口），然后重启容器。
                </div>
              )}
            </div>
          }
        />
      )}

      {health && health.status !== 'ok' && (
        <Alert
          type={health.status === 'error' ? 'error' : 'warning'}
          showIcon
          style={{ marginBottom: 16 }}
          message={health.status === 'error' ? '下载依赖缺失' : '下载依赖不完整'}
          description={health.detail || undefined}
        />
      )}

      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={6}><Card size="small"><Statistic title="已启用订阅" value={stats.enabled} prefix={<PlayCircleOutlined />} /></Card></Col>
        <Col xs={12} sm={6}><Card size="small"><Statistic title="活动任务" value={stats.active} prefix={<SyncOutlined spin={stats.active > 0} />} /></Card></Col>
        <Col xs={12} sm={6}><Card size="small"><Statistic title="累计下载" value={stats.totalDownloads} prefix={<HistoryOutlined />} /></Card></Col>
        <Col xs={12} sm={6}><Card size="small"><Statistic title="失败任务" value={stats.failed} valueStyle={{ color: stats.failed ? '#cf1322' : undefined }} /></Card></Col>
      </Row>

      <Card size="small">
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: 'subs',
              label: `订阅列表 (${subscriptions.length})`,
              children: (
                <Table
                  rowKey="id"
                  size="small"
                  loading={subLoading}
                  columns={subColumns}
                  dataSource={subscriptions}
                  scroll={{ x: 1100 }}
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
                    <Text type="secondary">每 2 秒自动刷新</Text>
                    <Button size="small" onClick={loadTasks} icon={<ReloadOutlined />}>刷新</Button>
                    <Popconfirm title="清除已完成 / 失败的任务记录？" onConfirm={async () => { await api.delete('/youtube/tasks'); await loadTasks(); }}>
                      <Button size="small" danger>清除已结束任务</Button>
                    </Popconfirm>
                  </Space>
                  <Table
                    rowKey="id"
                    size="small"
                    columns={taskColumns}
                    dataSource={tasks}
                    scroll={{ x: 1100 }}
                    pagination={{ pageSize: 10, hideOnSinglePage: true }}
                  />
                </Space>
              ),
            },
            {
              key: 'history',
              label: `下载历史 (${historyTotal})`,
              children: (
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <Space wrap>
                    <Search
                      allowClear
                      placeholder="搜索标题 / 频道 / 播放列表 / 视频 ID"
                      style={{ width: 360, maxWidth: '100%' }}
                      defaultValue={historySearch}
                      onSearch={(value) => {
                        setHistorySearch(value);
                        setHistoryPage(1);
                        loadHistory(1, historyPageSize, value);
                      }}
                    />
                    <Button icon={<ReloadOutlined />} onClick={() => loadHistory(historyPage, historyPageSize, historySearch)}>
                      刷新
                    </Button>
                  </Space>
                  <Table
                    rowKey="id"
                    size="small"
                    columns={historyColumns}
                    dataSource={history}
                    scroll={{ x: 1100 }}
                    pagination={{
                      current: historyPage,
                      pageSize: historyPageSize,
                      total: historyTotal,
                      showSizeChanger: true,
                      onChange: (page, size) => {
                        setHistoryPage(page);
                        setHistoryPageSize(size);
                      },
                    }}
                  />
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        title={editing ? '编辑订阅' : '新增 YouTube 订阅'}
        open={modalOpen}
        onOk={submit}
        confirmLoading={saving}
        onCancel={() => setModalOpen(false)}
        width={620}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ type: 'channel', enabled: true, media_mode: 'video', video_height: 0, audio_format: 'mp3' }}
        >
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="例如：Mr English Channel" maxLength={255} />
          </Form.Item>
          <Form.Item
            name="url"
            label="频道 / 播放列表 URL"
            rules={[{ required: true, message: '请输入 URL' }]}
            extra="支持 https://www.youtube.com/@handle、/playlists、/channel/xxx 或 watch?v=…&list=…"
          >
            <Input placeholder="https://www.youtube.com/@Mr.EnglishChannel" />
          </Form.Item>
          <Space size={12} style={{ display: 'flex' }} align="start">
            <Form.Item name="type" label="类型" rules={[{ required: true }]} style={{ flex: 1, minWidth: 220 }}>
              <Select
                options={Object.entries(TYPE_LABEL).map(([value, label]) => ({ value, label }))}
              />
            </Form.Item>
            <Form.Item name="media_mode" label="下载格式" rules={[{ required: true }]}>
              <Select
                options={MEDIA_MODE_OPTIONS}
                style={{ width: 148 }}
                popupMatchSelectWidth={false}
              />
            </Form.Item>
            <Form.Item name="enabled" label="启用" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
          <Space size={12} style={{ display: 'flex' }} align="start">
            <Form.Item
              name="video_height"
              label="视频清晰度"
              style={{ flex: 1, minWidth: 200 }}
              extra="YouTube 多数视频最高 1080p；4K 仅部分视频有，文件会明显变大"
            >
              <Select options={HEIGHT_OPTIONS} />
            </Form.Item>
            <Form.Item
              name="audio_format"
              label="音频格式"
              style={{ flex: 1, minWidth: 200 }}
              extra="「原流」直接重封装 YouTube 原始音轨（Opus/AAC），零转码，音质最高"
            >
              <Select options={AUDIO_FORMAT_OPTIONS} />
            </Form.Item>
          </Space>
          <Form.Item
            name="download_path"
            label="下载目录（可选）"
            extra="留空则使用默认目录：仅 MP3 → /media/Audio，MP4 与 MP4+MP3 → /media/Youtube"
          >
            <Input placeholder="/media/Youtube" maxLength={1024} />
          </Form.Item>
          <Alert
            type="info"
            showIcon
            message="目录结构"
            description="下载目录 / 播放列表名称 / 原始视频标题.mp4|mp3；字幕与封面沿用同名：原标题.en.srt、原标题.jpg。MP4+MP3 会先下载 MP4，再用 ffmpeg 就地转出同名 MP3（不重复下载）。"
          />
        </Form>
      </Modal>
    </div>
  );
}
