import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Button,
  Card,
  Drawer,
  Form,
  Input,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
  Switch,
  Row,
  Col,
  Grid,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  ReloadOutlined,
  CodeOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import api from '../utils/api';

const { Title, Text } = Typography;
const { useBreakpoint } = Grid;

interface ContainerItem {
  id: string;
  short_id: string;
  name: string;
  image: string;
  status: string;
  state: string;
  running: boolean;
  created_at?: string;
  stack: string;
  ownership: string;
  ip_addresses: string[];
  ports: string[];
}

interface ContainerStat {
  id: string;
  short_id: string;
  name: string;
  cpu_percent: number;
  memory_usage: number;
  memory_limit: number;
  memory_percent: number;
  net_rx: number;
  net_tx: number;
  blk_read: number;
  blk_write: number;
  pids: number;
}

export default function ContainerManager() {
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const [containers, setContainers] = useState<ContainerItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);

  const [searchText, setSearchText] = useState('');
  const [stateFilter, setStateFilter] = useState<'all' | 'running' | 'stopped'>('all');

  const [statsMap, setStatsMap] = useState<Record<string, ContainerStat>>({});
  const [statsAutoRefresh, setStatsAutoRefresh] = useState(true);

  const [selected, setSelected] = useState<ContainerItem | null>(null);

  const [logsOpen, setLogsOpen] = useState(false);
  const [logsText, setLogsText] = useState('');
  const [tail, setTail] = useState(1000);
  const [logsAutoRefresh, setLogsAutoRefresh] = useState(true);
  const [logsLoading, setLogsLoading] = useState(false);

  const [execOpen, setExecOpen] = useState(false);
  const [execForm] = Form.useForm();

  const [terminalConnected, setTerminalConnected] = useState(false);
  const [terminalConnecting, setTerminalConnecting] = useState(false);
  const terminalWsRef = useRef<WebSocket | null>(null);
  const terminalPanelRef = useRef<HTMLDivElement | null>(null);
  const xtermRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);  // Track flush timer for cleanup

  const fetchContainers = async () => {
    setLoading(true);
    try {
      const res = await api.get('/system/docker/containers', { params: { all: true } });
      setContainers(res.data || []);
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '加载容器失败');
    } finally {
      setLoading(false);
    }
  };

  const fetchLogs = async (containerId: string, keepScrollBottom = false) => {
    setLogsLoading(true);
    try {
      const res = await api.get(`/system/docker/containers/${containerId}/logs`, {
        params: { tail },
        responseType: 'text',
      });
      setLogsText(typeof res.data === 'string' ? res.data : String(res.data || ''));
      if (keepScrollBottom) {
        requestAnimationFrame(() => {
          const el = document.getElementById('container-log-panel');
          if (el) el.scrollTop = el.scrollHeight;
        });
      }
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '获取日志失败');
    } finally {
      setLogsLoading(false);
    }
  };

  useEffect(() => {
    fetchContainers();
  }, []);

  const fetchStats = async () => {
    try {
      const res = await api.get('/system/docker/stats', { params: { running_only: true } });
      const nextMap: Record<string, ContainerStat> = {};
      (res.data || []).forEach((s: ContainerStat) => {
        nextMap[s.id] = s;
      });
      setStatsMap(nextMap);
    } catch {
      // keep previous stats to avoid UI flicker
    }
  };

  useEffect(() => {
    fetchStats();
  }, []);

  useEffect(() => {
    if (!statsAutoRefresh) return;
    const timer = window.setInterval(fetchStats, 3000);
    return () => window.clearInterval(timer);
  }, [statsAutoRefresh]);

  useEffect(() => {
    if (!logsOpen || !selected?.id || !logsAutoRefresh) return;
    const timer = window.setInterval(() => {
      fetchLogs(selected.id, true);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [logsOpen, logsAutoRefresh, selected?.id, tail]);

  const handleBulkAction = async (action: string) => {
    if (selectedRowKeys.length === 0) {
      message.warning('请先选择容器');
      return;
    }
    try {
      const res = await api.post('/system/docker/containers/bulk-action', {
        action,
        container_ids: selectedRowKeys,
      });
      const data = res.data || {};
      const okCount = (data.success || []).length;
      const failCount = (data.failed || []).length;
      if (failCount > 0) {
        message.warning(`${action} 完成：成功 ${okCount}，失败 ${failCount}`);
      } else {
        message.success(`${action} 成功：${okCount} 个容器`);
      }
      await fetchContainers();
      await fetchStats();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || `${action} 批量执行失败`);
    }
  };

  const openLogs = async (row: ContainerItem) => {
    setSelected(row);
    setLogsOpen(true);
    setLogsText('');
    await fetchLogs(row.id, true);
  };

  const openExec = (row: ContainerItem) => {
    setSelected(row);
    execForm.setFieldsValue({ user: '', workdir: '' });
    setExecOpen(true);
  };

  const ensureXterm = () => {
    if (xtermRef.current || !terminalPanelRef.current) return;
    const term = new Terminal({
      cursorBlink: true,
      fontFamily: 'Consolas, Menlo, Monaco, "Courier New", monospace',
      fontSize: 13,
      convertEol: true,
      allowProposedApi: true,
      scrollback: 8000,
      theme: {
        background: '#111827',
        foreground: '#e5e7eb',
        cursor: '#60a5fa',
        selectionBackground: '#334155',
      },
    });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.open(terminalPanelRef.current);
    fitAddon.fit();
    term.focus();
    xtermRef.current = term;
    fitAddonRef.current = fitAddon;

    term.onData((data) => {
      const ws = terminalWsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify({ type: 'raw', data }));
    });
  };

  const disposeXterm = () => {
    fitAddonRef.current = null;
    if (xtermRef.current) {
      xtermRef.current.dispose();
      xtermRef.current = null;
    }
  };

  const writeTerminal = (text: string) => {
    xtermRef.current?.write(text);
  };

  const closeTerminal = () => {
    // Clean up any pending flush timer before closing WS
    if (flushTimerRef.current) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    terminalWsRef.current?.close();
    terminalWsRef.current = null;
    setTerminalConnected(false);
    setTerminalConnecting(false);
  };

  const connectTerminal = async () => {
    if (!selected) return;
    const values = execForm.getFieldsValue(['user', 'workdir']);
    const token = localStorage.getItem('token') || '';
    if (!token) {
      message.error('未登录或 token 已失效');
      return;
    }
    closeTerminal();
    setTerminalConnecting(true);
    if (xtermRef.current) {
      xtermRef.current.clear();
      xtermRef.current.writeln('$ connecting...');
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const params = new URLSearchParams({
      token,
      container_id: selected.id,
      user: values.user || '',
      workdir: values.workdir || '',
    });
    const wsUrl = `${protocol}//${window.location.host}/api/v1/ws/docker/exec?${params.toString()}`;

    const ws = new WebSocket(wsUrl);
    terminalWsRef.current = ws;

    // Batch accumulated stdout messages to prevent frontend freeze
    // Instead of writing each message immediately, accumulate and flush every 10ms
    let outputBuffer = '';

    const flushOutput = () => {
      if (outputBuffer) {
        writeTerminal(outputBuffer);
        outputBuffer = '';
      }
      flushTimerRef.current = null;
    };

    const scheduleFlush = () => {
      if (flushTimerRef.current) return;  // Already scheduled
      flushTimerRef.current = setTimeout(flushOutput, 10);  // Batch window: 10ms
    };

    // Set connection timeout: if no onopen within 15 seconds, abort
    const connectionTimeoutRef = { current: setTimeout(() => {
      if (ws.readyState === WebSocket.CONNECTING) {
        writeTerminal('\r\n[error] websocket connection timeout (backend may not be responding)\r\n');
        ws.close();
      }
    }, 15000) };

    ws.onopen = () => {
      clearTimeout(connectionTimeoutRef.current);
      setTerminalConnecting(false);
      setTerminalConnected(true);
      writeTerminal('\r\n$ connected\r\n');
      requestAnimationFrame(() => {
        fitAddonRef.current?.fit();
        xtermRef.current?.focus();
      });
    };

    ws.onmessage = (ev) => {
      try {
        const payload = JSON.parse(String(ev.data || '{}'));
        if (payload.type === 'stdout') {
          // Accumulate stdout; flush in batches to reduce xterm write overhead
          outputBuffer += String(payload.data || '');
          scheduleFlush();
        } else if (payload.type === 'error') {
          // Flush any pending output before error
          if (flushTimerRef.current) {
            clearTimeout(flushTimerRef.current);
            flushOutput();
          }
          writeTerminal(`\r\n[error] ${String(payload.message || '')}\r\n`);
        } else if (payload.type === 'status') {
          // Flush any pending output before status
          if (flushTimerRef.current) {
            clearTimeout(flushTimerRef.current);
            flushOutput();
          }
          writeTerminal(`\r\n[${payload.status}]\r\n`);
        }
      } catch {
        // Flush pending on parse error
        if (flushTimerRef.current) {
          clearTimeout(flushTimerRef.current);
          flushOutput();
        }
        writeTerminal(String(ev.data || ''));
      }
    };

    ws.onerror = () => {
      if (flushTimerRef.current) clearTimeout(flushTimerRef.current);
      flushOutput();
      writeTerminal('\r\n[error] websocket connection failed\r\n');
    };

    ws.onclose = () => {
      setTerminalConnected(false);
      setTerminalConnecting(false);
      terminalWsRef.current = null;
      writeTerminal('\r\n$ disconnected\r\n');
    };
  };

  const sendRaw = (value: string) => {
    const ws = terminalWsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return;
    }
    ws.send(JSON.stringify({ type: 'raw', data: value }));
  };

  const sendCtrlC = () => {
    sendRaw('\u0003');
  };

  const clearTerminal = () => {
    if (xtermRef.current) {
      xtermRef.current.clear();
      xtermRef.current.focus();
    }
  };

  useEffect(() => {
    if (!execOpen || !selected?.id) return;
    ensureXterm();
    connectTerminal();
    requestAnimationFrame(() => {
      fitAddonRef.current?.fit();
      xtermRef.current?.focus();
    });
    const onResize = () => fitAddonRef.current?.fit();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      closeTerminal();
    };
  }, [execOpen, selected?.id]);

  useEffect(() => {
    return () => {
      closeTerminal();
      disposeXterm();
    };
  }, []);

  const filteredData = useMemo(() => {
    return containers.filter((row) => {
      if (stateFilter === 'running' && !row.running) return false;
      if (stateFilter === 'stopped' && row.running) return false;
      if (!searchText.trim()) return true;
      const kw = searchText.trim().toLowerCase();
      return (
        row.name.toLowerCase().includes(kw)
        || row.image.toLowerCase().includes(kw)
        || row.short_id.toLowerCase().includes(kw)
        || (row.stack || '').toLowerCase().includes(kw)
      );
    });
  }, [containers, searchText, stateFilter]);

  const runningCount = useMemo(() => containers.filter((x) => x.running).length, [containers]);
  const stoppedCount = containers.length - runningCount;
  const avgCpu = useMemo(() => {
    const values = Object.values(statsMap).map((x) => x.cpu_percent);
    if (!values.length) return 0;
    return values.reduce((a, b) => a + b, 0) / values.length;
  }, [statsMap]);
  const avgMem = useMemo(() => {
    const values = Object.values(statsMap).map((x) => x.memory_percent);
    if (!values.length) return 0;
    return values.reduce((a, b) => a + b, 0) / values.length;
  }, [statsMap]);

  const columns: ColumnsType<ContainerItem> = useMemo(
    () => [
      {
        title: 'Name',
        dataIndex: 'name',
        key: 'name',
        width: 170,
        render: (_, row) => (
          <div>
            <div style={{ fontWeight: 600 }}>{row.name}</div>
            <Text type="secondary" style={{ fontSize: 12 }}>{row.short_id}</Text>
          </div>
        ),
      },
      {
        title: 'State',
        dataIndex: 'state',
        key: 'state',
        width: 110,
        render: (_, row) => (
          <Tag color={row.running ? 'green' : 'default'}>{row.running ? 'running' : row.state}</Tag>
        ),
      },
      { title: 'Stack', dataIndex: 'stack', key: 'stack', width: 120 },
      { title: 'Image', dataIndex: 'image', key: 'image', width: 240, ellipsis: true },
      {
        title: 'Created',
        dataIndex: 'created_at',
        key: 'created_at',
        width: 170,
        render: (v?: string) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-'),
      },
      {
        title: 'IP Address',
        dataIndex: 'ip_addresses',
        key: 'ip_addresses',
        width: 180,
        render: (v: string[]) => (v?.length ? v.join(', ') : '-'),
      },
      {
        title: 'Published Ports',
        dataIndex: 'ports',
        key: 'ports',
        width: 220,
        render: (v: string[]) => (v?.length ? v.join(' | ') : '-'),
      },
      {
        title: 'CPU',
        key: 'cpu',
        width: 140,
        render: (_, row) => {
          const stat = statsMap[row.id];
          const v = stat?.cpu_percent ?? 0;
          return <Progress percent={Math.min(100, Number(v.toFixed(2)))} size="small" strokeColor="#1677ff" />;
        },
      },
      {
        title: 'Memory',
        key: 'memory',
        width: 150,
        render: (_, row) => {
          const stat = statsMap[row.id];
          const v = stat?.memory_percent ?? 0;
          return <Progress percent={Math.min(100, Number(v.toFixed(2)))} size="small" strokeColor="#52c41a" />;
        },
      },
      {
        title: 'Actions',
        key: 'actions',
        width: 170,
        render: (_, row) => (
          <Space wrap>
            <Button size="small" icon={<FileTextOutlined />} onClick={() => openLogs(row)}>Logs</Button>
            <Button size="small" icon={<CodeOutlined />} onClick={() => openExec(row)}>Terminal</Button>
          </Space>
        ),
      },
    ],
    [statsMap],
  );

  return (
    <div>
      <Card style={{ marginBottom: 16 }}>
        <Space style={{ width: '100%', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <Title level={4} style={{ margin: 0 }}>Docker 容器管理</Title>
            <Text type="secondary">类似 Portainer 的容器列表、日志、实时终端、批量操作与资源监控。</Text>
          </div>
          <Space wrap>
            <Space>
              <Text>资源自动刷新</Text>
              <Switch checked={statsAutoRefresh} onChange={setStatsAutoRefresh} />
            </Space>
            <Button icon={<ReloadOutlined />} onClick={async () => { await fetchContainers(); await fetchStats(); }} loading={loading}>刷新</Button>
          </Space>
        </Space>

        <Row gutter={[12, 12]} style={{ marginTop: 14 }}>
          <Col xs={12} sm={6}><Statistic title="总容器" value={containers.length} /></Col>
          <Col xs={12} sm={6}><Statistic title="运行中" value={runningCount} /></Col>
          <Col xs={12} sm={6}><Statistic title="已停止" value={stoppedCount} /></Col>
          <Col xs={12} sm={6}><Statistic title="平均 CPU / MEM" value={`${avgCpu.toFixed(1)}% / ${avgMem.toFixed(1)}%`} /></Col>
        </Row>

        <Space style={{ marginTop: 14, width: '100%', justifyContent: 'space-between', flexWrap: 'wrap' }}>
          <Space wrap>
            <Input.Search
              allowClear
              placeholder="搜索名称/镜像/ID/Stack"
              style={{ width: 280 }}
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
            />
            <Select
              style={{ width: 140 }}
              value={stateFilter}
              onChange={(v) => setStateFilter(v)}
              options={[
                { value: 'all', label: '全部状态' },
                { value: 'running', label: '仅运行中' },
                { value: 'stopped', label: '仅已停止' },
              ]}
            />
          </Space>
          <Space wrap>
            <Text type="secondary">已选 {selectedRowKeys.length} 项</Text>
            <Button onClick={() => handleBulkAction('start')}>Start</Button>
            <Button onClick={() => handleBulkAction('stop')}>Stop</Button>
            <Button onClick={() => handleBulkAction('restart')}>Restart</Button>
            <Button onClick={() => handleBulkAction('pause')}>Pause</Button>
            <Button onClick={() => handleBulkAction('unpause')}>Resume</Button>
            <Popconfirm title="确认批量 Kill 选中容器？" onConfirm={() => handleBulkAction('kill')}>
              <Button danger>Kill</Button>
            </Popconfirm>
            <Popconfirm title="确认批量删除选中容器？" onConfirm={() => handleBulkAction('remove')}>
              <Button danger>Remove</Button>
            </Popconfirm>
          </Space>
        </Space>
      </Card>

      <div style={{ overflowX: 'auto' }}>
        <Table
          rowKey="id"
          dataSource={filteredData}
          columns={columns}
          loading={loading}
          size="small"
          scroll={{ x: 'max-content' }}
          rowSelection={{
            selectedRowKeys,
            onChange: (keys) => setSelectedRowKeys(keys),
          }}
          pagination={{ defaultPageSize: 20, showSizeChanger: true, pageSizeOptions: [20, 50, 100] }}
        />
      </div>

      <Drawer
        title={selected ? `Container Logs - ${selected.name}` : 'Container Logs'}
        open={logsOpen}
        onClose={() => setLogsOpen(false)}
        width={980}
        extra={(
          <Space>
            <Input
              style={{ width: 120 }}
              value={tail}
              onChange={(e) => setTail(Math.max(10, Number(e.target.value || 1000)))}
              placeholder="tail"
            />
            <Space>
              <Text>自动刷新</Text>
              <Switch checked={logsAutoRefresh} onChange={setLogsAutoRefresh} />
            </Space>
            <Button
              icon={<ReloadOutlined />}
              loading={logsLoading}
              onClick={() => selected?.id && fetchLogs(selected.id, true)}
            >
              刷新
            </Button>
          </Space>
        )}
      >
        <pre
          id="container-log-panel"
          style={{
            background: '#0f172a',
            color: '#e2e8f0',
            borderRadius: 8,
            padding: 12,
            height: 'calc(100vh - 180px)',
            overflow: 'auto',
            margin: 0,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            fontSize: 12,
            lineHeight: 1.45,
          }}
        >
          {logsText || '暂无日志'}
        </pre>
      </Drawer>

      <Modal
        title={selected ? `Interactive Terminal - ${selected.name}` : 'Interactive Terminal'}
        open={execOpen}
        onCancel={() => { closeTerminal(); setExecOpen(false); }}
        onOk={() => connectTerminal()}
        okText="重连"
        cancelText="取消"
        confirmLoading={terminalConnecting}
        width={isMobile ? '100%' : 980}
      >
        <Form form={execForm} layout="vertical" style={{ marginTop: 12 }}>
          <Space wrap style={{ width: '100%' }}>
            <Form.Item name="user" label="User" style={{ minWidth: 220 }}>
              <Input placeholder="留空使用容器默认用户" />
            </Form.Item>
            <Form.Item name="workdir" label="Workdir" style={{ minWidth: 280 }}>
              <Input placeholder="例如: /app" />
            </Form.Item>
          </Space>
        </Form>

        <Space style={{ width: '100%', marginBottom: 8, justifyContent: 'space-between' }}>
          <Tag color={terminalConnected ? 'green' : 'default'}>{terminalConnected ? 'connected' : 'disconnected'}</Tag>
          <Space>
            {terminalConnected && (
              <Button size="small" onClick={sendCtrlC}>Ctrl+C</Button>
            )}
            <Button size="small" onClick={clearTerminal}>清空输出</Button>
            <Button size="small" onClick={() => connectTerminal()}>重连</Button>
          </Space>
        </Space>

        <div
          ref={terminalPanelRef}
          id="container-terminal-panel"
          style={{
            margin: 0,
            maxHeight: 420,
            minHeight: 300,
            height: 420,
            background: '#111827',
            padding: 0,
            borderRadius: 8,
            overflow: 'hidden',
          }}
        />

        <Text type="secondary" style={{ display: 'block', marginTop: 10 }}>
          终端已切换为 xterm 模式：可直接输入，支持方向键、Tab、历史、粘贴、Ctrl+C。
        </Text>

        <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
          <Button size="small" onClick={() => sendRaw('ls -lah\r')}>ls -lah</Button>
          <Button size="small" onClick={() => sendRaw('pwd\r')}>pwd</Button>
          <Button size="small" onClick={() => sendRaw('env | head\r')}>env | head</Button>
        </div>
      </Modal>
    </div>
  );
}
