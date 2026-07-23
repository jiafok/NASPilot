import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { Button, Space, Typography, Switch, Spin, Collapse, Select, Tag } from 'antd';
import { PauseCircleOutlined, PlayCircleOutlined, ClearOutlined, WifiOutlined, CodeOutlined, ExportOutlined } from '@ant-design/icons';
import { getToken } from '../utils/auth';

const { Text } = Typography;

interface LogLine {
  timestamp: string;
  level: string;
  source: string;
  logger: string;
  message: string;
}

interface Props {
  source?: string;
  maxHeight?: number;
  maxLines?: number;
  placeholder?: string;
  collapsible?: boolean;
  defaultOpen?: boolean;
  label?: string;
  /** Show level & source filter toolbar. Default true. */
  showFilters?: boolean;
  /** Show "open in new window" button. Default false. */
  showOpenWindow?: boolean;
}

const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];
const LEVEL_COLORS: Record<string, string> = { DEBUG: 'default', INFO: 'blue', WARNING: 'orange', ERROR: 'red', CRITICAL: 'magenta' };

export default function LogViewer({ source, maxHeight = 400, maxLines = 5000, placeholder = '暂无日志', collapsible = false, defaultOpen = false, label = '📋 日志', showFilters = true, showOpenWindow = false }: Props) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [paused, setPaused] = useState(false);
  const [connected, setConnected] = useState(false);
  const [levelFilter, setLevelFilter] = useState<string[]>([]);
  const containerRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pausedBufferRef = useRef<LogLine[]>([]);
  const userScrolledUpRef = useRef(false);
  // Batch incoming lines to avoid 500+ re-renders during WS buffer replay
  const batchRef = useRef<LogLine[]>([]);
  const batchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const filtered = useMemo(() => {
    if (levelFilter.length === 0) return lines;
    return lines.filter(l => levelFilter.includes(l.level));
  }, [lines, levelFilter]);

  // Smart auto-scroll: only when user is at the bottom
  const isAtBottom = useCallback(() => {
    const el = containerRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  }, []);

  const smartScroll = useCallback(() => {
    if (!userScrolledUpRef.current) {
      const el = containerRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    }
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const handler = () => { userScrolledUpRef.current = !isAtBottom(); };
    el.addEventListener('scroll', handler, { passive: true });
    return () => el.removeEventListener('scroll', handler);
  }, [isAtBottom]);

  const connect = useCallback(() => {
    const token = getToken();
    if (!token) return;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    let wsUrl = `${protocol}//${window.location.host}/api/v1/ws/logs?token=${encodeURIComponent(token)}`;
    if (source) wsUrl += `&source=${encodeURIComponent(source)}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      // Flush buffered lines from paused period in one batch
      if (pausedBufferRef.current.length > 0) {
        batchRef.current.push(...pausedBufferRef.current);
        pausedBufferRef.current = [];
      }
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'ping') return;
        const line: LogLine = {
          timestamp: data.timestamp || '',
          level: data.level || 'INFO',
          source: data.source || 'system',
          logger: data.logger || '',
          message: data.message || '',
        };
        if (paused) {
          pausedBufferRef.current.push(line);
        } else {
          // Batch mode: accumulate lines, flush every 100ms
          batchRef.current.push(line);
          if (batchTimerRef.current === null) {
            batchTimerRef.current = setTimeout(() => {
              const batch = batchRef.current;
              batchRef.current = [];
              batchTimerRef.current = null;
              setLines((prev) => {
                const next = [...prev, ...batch];
                if (next.length > maxLines) return next.slice(-maxLines);
                return next;
              });
              setTimeout(smartScroll, 50);
            }, 100);
          }
        }
      } catch { /* ignore */ }
    };

    ws.onclose = () => {
      setConnected(false);
      setTimeout(connect, 3000);
    };
    ws.onerror = () => { ws.close(); };
  }, [source, paused, smartScroll, maxLines]);

  useEffect(() => {
    connect();
    return () => { wsRef.current?.close(); };
  }, [connect]);

  const body = (
    <div style={{ border: '1px solid #d9d9d9', borderRadius: 8, overflow: 'hidden' }}>
      {/* Toolbar */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 4,
        padding: '6px 10px', background: '#fafafa', borderBottom: '1px solid #d9d9d9', fontSize: 12,
      }}>
        <Space size={4}>
          <WifiOutlined style={{ color: connected ? '#52c41a' : '#ff4d4f' }} />
          <Text type="secondary" style={{ fontSize: 12 }}>
            {connected ? '已连接' : '断开'
            } · {levelFilter.length ? filtered.length + '/' : ''}{lines.length} 行
          </Text>
          {showFilters && (
            <Select
              mode="multiple" size="small" placeholder="级别"
              style={{ minWidth: 90, maxWidth: 180 }}
              value={levelFilter}
              onChange={(v) => setLevelFilter(v)}
              options={LEVELS.map(l => ({ value: l, label: (
                <Tag color={LEVEL_COLORS[l]} style={{ margin: 0, fontSize: 10, lineHeight: '14px' }}>{l}</Tag>
              ) }))}
              maxTagCount={2}
            />
          )}
        </Space>
        <Space size={4}>
          <Switch size="small" checked={!paused} onChange={(v) => setPaused(!v)}
            checkedChildren={<PlayCircleOutlined />} unCheckedChildren={<PauseCircleOutlined />} />
          <Button size="small" icon={<ClearOutlined />} onClick={() => setLines([])}>清空</Button>
          {showOpenWindow && (
            <Button size="small" icon={<ExportOutlined />}
              onClick={() => { const u = source ? `/logs/full?source=${encodeURIComponent(source)}` : '/logs/full'; window.open(u, '_blank', 'width=1100,height=800'); }}>
              新窗口
            </Button>
          )}
        </Space>
      </div>

      {/* Log lines */}
      <div ref={containerRef} style={{
        maxHeight, overflowY: 'auto', background: '#1e1e1e', padding: '6px 0',
        fontFamily: "'Cascadia Code','Fira Code','Consolas',monospace", fontSize: 12, lineHeight: '20px',
      }}>
        {filtered.length === 0 ? (
          <div style={{ color: '#666', textAlign: 'center', padding: '40px 0' }}>
            {connected ? placeholder : <Spin size="small" />}
          </div>
        ) : (
          filtered.map((line, i) => (
            <div key={i} style={{
              padding: '1px 12px', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
              color: line.level === 'ERROR' || line.level === 'CRITICAL' ? '#ff6b6b'
                   : line.level === 'WARNING' ? '#ffd93d'
                   : line.level === 'DEBUG' ? '#888' : '#e0e0e0',
            }}>
              <span style={{ color: '#569cd6', marginRight: 8 }}>
                {line.timestamp ? new Date(line.timestamp).toLocaleTimeString() : '--:--:--'}
              </span>
              <span style={{ color: '#888', marginRight: 4 }}>[{line.level}]</span>
              <span>{line.message}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );

  if (collapsible) {
    return (
      <Collapse defaultActiveKey={defaultOpen ? ['log'] : []} items={[{
        key: 'log',
        label: <span><CodeOutlined /> {label} · {lines.length} 行</span>,
        children: body,
      }]} />
    );
  }
  return body;
}
