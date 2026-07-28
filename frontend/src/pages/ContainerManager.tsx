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

interface ExecResult {
  exit_code: number | null;
  running: boolean;
  output: string;
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
  const [execResult, setExecResult] = useState<ExecResult | null>(null);
  const [execForm] = Form.useForm();

  const [terminalOutput, setTerminalOutput] = useState('');
  const [terminalConnected, setTerminalConnected] = useState(false);
  const [terminalConnecting, setTerminalConnecting] = useState(false);
  const [terminalInput, setTerminalInput] = useState('');
  const terminalWsRef = useRef<WebSocket | null>(null);

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
    setExecResult(null);
    execForm.setFieldsValue({ user: '', workdir: '' });
    setTerminalOutput('');
    setTerminalInput('');
    setExecOpen(true);
  };

  const closeTerminal = () => {
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
    setTerminalOutput('');

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

    ws.onopen = () => {
      setTerminalConnecting(false);
      setTerminalConnected(true);
      setTerminalOutput((prev) => prev + '$ connected\n');
    };

    ws.onmessage = (ev) => {
      try {
        const payload = JSON.parse(String(ev.data || '{}'));
        if (payload.type === 'stdout') {
          setTerminalOutput((prev) => prev + String(payload.data || ''));
        } else if (payload.type === 'error') {
          setTerminalOutput((prev) => prev + `\n[error] ${String(payload.message || '')}\n`);
        } else if (payload.type === 'status') {
          setTerminalOutput((prev) => prev + `\n[${payload.status}]\n`);
        }
      } catch {
        setTerminalOutput((prev) => prev + String(ev.data || ''));
      }
      requestAnimationFrame(() => {
        const el = document.getElementById('container-terminal-panel');
        if (el) el.scrollTop = el.scrollHeight;
      });
    };

    ws.onerror = () => {
      setTerminalOutput((prev) => prev + '\n[error] websocket connection failed\n');
    };

    ws.onclose = () => {
      setTerminalConnected(false);
      setTerminalConnecting(false);
      terminalWsRef.current = null;
      setTerminalOutput((prev) => prev + '\n$ disconnected\n');
    };
  };

  const sendTerminalInput = (value?: string) => {
    const ws = terminalWsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      message.warning('终端未连接');
      return;
    }
    const toSend = typeof value === 'string' ? value : terminalInput;
    if (!toSend) return;
    ws.send(JSON.stringify({ type: 'stdin', data: `${toSend}\n` }));
    setTerminalInput('');
  };

  const sendCtrlC = () => {
    const ws = terminalWsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: 'stdin', data: '\u0003' }));
  };

  useEffect(() => {
    if (!execOpen || !selected?.id) return;
    connectTerminal();
    return () => {
      closeTerminal();
    };
  }, [execOpen, selected?.id]);

  useEffect(() => {
    return () => {
      closeTerminal();
    };
  }, []);

  const runExec = async () => {
    if (!selected) return;
    const values = await execForm.validateFields();
    setExecResult(null);
    try {
      const res = await api.post(`/system/docker/containers/${selected.id}/exec`, {
        command: values.command,
        user: values.user || null,
        workdir: values.workdir || null,
      });
      setExecResult(res.data);
      setTerminalOutput((prev) => `${prev}\n$ one-shot command done (exit ${res.data?.exit_code ?? '-'})\n`);
      if (logsOpen) {
        await fetchLogs(selected.id, true);
      }
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '执行命令失败');
    }
  };

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
        onOk={() => {
          if (!terminalConnected) {
            connectTerminal();
            return;
          }
          sendTerminalInput();
        }}
        okText={terminalConnected ? '发送' : '重连'}
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
            <Button size="small" onClick={() => setTerminalOutput('')}>清空输出</Button>
            <Button size="small" onClick={() => connectTerminal()}>重连</Button>
          </Space>
        </Space>

        <pre
          id="container-terminal-panel"
          style={{
            margin: 0,
            maxHeight: 420,
            minHeight: 300,
            overflow: 'auto',
            background: '#111827',
            color: '#e5e7eb',
            padding: 12,
            borderRadius: 8,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            fontSize: 12,
            lineHeight: 1.45,
          }}
        >
          {terminalOutput || '(terminal output)'}
        </pre>

        <Input
          style={{ marginTop: 10 }}
          value={terminalInput}
          onChange={(e) => setTerminalInput(e.target.value)}
          onPressEnter={() => terminalConnected ? sendTerminalInput() : connectTerminal()}
          placeholder={terminalConnected ? '输入命令后回车发送' : '先点击“连接终端”'}
          disabled={terminalConnecting}
        />

        <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
          <Button size="small" onClick={() => setTerminalInput('ls -lah')}>ls -lah</Button>
          <Button size="small" onClick={() => setTerminalInput('pwd')}>pwd</Button>
          <Button size="small" onClick={() => setTerminalInput('env | head')}>env | head</Button>
        </div>

        <div style={{ marginTop: 12 }}>
          <Card size="small" title="单次命令执行（可选）">
            <Form form={execForm} layout="vertical">
              <Form.Item name="command" label="Command" rules={[{ required: true, message: '请输入命令' }]}
              >
                <Input.TextArea rows={3} placeholder="例如: ls -lah /" />
              </Form.Item>
            </Form>
            <Button type="primary" onClick={runExec}>执行单次命令</Button>
          </Card>
        </div>

        {execResult && (
          <Card size="small" style={{ marginTop: 10 }} title={`Exit Code: ${execResult.exit_code ?? '-'} | Running: ${String(execResult.running)}`}>
            <pre
              style={{
                margin: 0,
                maxHeight: 220,
                overflow: 'auto',
                background: '#111827',
                color: '#e5e7eb',
                padding: 12,
                borderRadius: 8,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {execResult.output || '(no output)'}
            </pre>
          </Card>
        )}
      </Modal>
    </div>
  );
}
