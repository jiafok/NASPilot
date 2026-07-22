import { useEffect, useRef, useState, useCallback } from 'react';
import { Tag, Button, Space, Typography, Switch, Spin } from 'antd';
import { PauseCircleOutlined, PlayCircleOutlined, ClearOutlined, ReloadOutlined, WifiOutlined } from '@ant-design/icons';
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
  /** Filter logs by source, e.g. "plugin:pt_rss". Omit for all sources. */
  source?: string;
  /** Max height of the log container. */
  maxHeight?: number;
  /** Number of log lines to keep in memory. */
  maxLines?: number;
  /** Auto-scroll to bottom on new log lines. */
  autoScroll?: boolean;
  /** Show when there are no logs yet */
  placeholder?: string;
}

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: 'default',
  INFO: 'blue',
  WARNING: 'orange',
  ERROR: 'red',
  CRITICAL: 'magenta',
};

export default function LogViewer({ source, maxHeight = 350, maxLines = 1000, autoScroll = true, placeholder = '暂无日志' }: Props) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [paused, setPaused] = useState(false);
  const [connected, setConnected] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pausedBufferRef = useRef<LogLine[]>([]);

  const scrollToBottom = useCallback(() => {
    if (containerRef.current && autoScroll) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [autoScroll]);

  const appendLine = useCallback((line: LogLine) => {
    setLines((prev) => {
      const next = [...prev, line];
      if (next.length > maxLines) return next.slice(-maxLines);
      return next;
    });
  }, [maxLines]);

  const connect = useCallback(() => {
    const token = getToken();
    if (!token) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    let wsUrl = `${protocol}//${window.location.host}/ws/logs?token=${encodeURIComponent(token)}`;
    if (source) wsUrl += `&source=${encodeURIComponent(source)}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      // Flush buffered lines from paused period
      if (pausedBufferRef.current.length > 0) {
        setLines((prev) => {
          const next = [...prev, ...pausedBufferRef.current];
          pausedBufferRef.current = [];
          if (next.length > maxLines) return next.slice(-maxLines);
          return next;
        });
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
          appendLine(line);
          setTimeout(scrollToBottom, 50);
        }
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      setConnected(false);
      // Reconnect after 3s
      setTimeout(connect, 3000);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [source, paused, appendLine, scrollToBottom, maxLines]);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
  }, [connect]);

  const levelTag = (level: string) => (
    <Tag color={LEVEL_COLORS[level] || 'default'} style={{ fontSize: 11, lineHeight: '16px', marginRight: 4 }}>
      {level}
    </Tag>
  );

  return (
    <div style={{ border: '1px solid #d9d9d9', borderRadius: 8, overflow: 'hidden' }}>
      {/* Toolbar */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '6px 12px', background: '#fafafa', borderBottom: '1px solid #d9d9d9',
        fontSize: 12,
      }}>
        <Space size={4}>
          <WifiOutlined style={{ color: connected ? '#52c41a' : '#ff4d4f' }} />
          <Text type="secondary" style={{ fontSize: 12 }}>
            {connected ? '已连接' : '断开'} · {lines.length} 行
          </Text>
        </Space>
        <Space size={4}>
          <Switch
            size="small"
            checked={!paused}
            onChange={(v) => setPaused(!v)}
            checkedChildren={<PlayCircleOutlined />}
            unCheckedChildren={<PauseCircleOutlined />}
          />
          <Button size="small" icon={<ClearOutlined />} onClick={() => setLines([])}>清空</Button>
        </Space>
      </div>

      {/* Log lines */}
      <div
        ref={containerRef}
        style={{
          maxHeight,
          overflowY: 'auto',
          background: '#1e1e1e',
          padding: '8px 0',
          fontFamily: "'Cascadia Code', 'Fira Code', 'Consolas', monospace",
          fontSize: 12,
          lineHeight: '20px',
        }}
      >
        {lines.length === 0 ? (
          <div style={{ color: '#666', textAlign: 'center', padding: '40px 0' }}>
            {connected ? placeholder : <Spin size="small" />}
          </div>
        ) : (
          lines.map((line, i) => (
            <div
              key={i}
              style={{
                padding: '1px 12px',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
                color: line.level === 'ERROR' || line.level === 'CRITICAL' ? '#ff6b6b'
                     : line.level === 'WARNING' ? '#ffd93d'
                     : line.level === 'DEBUG' ? '#888'
                     : '#e0e0e0',
              }}
            >
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
}
