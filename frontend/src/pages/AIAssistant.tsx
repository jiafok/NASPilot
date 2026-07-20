import { useRef, useState } from 'react';
import {
  Alert, Button, Card, Checkbox, Input, List, Space, Typography, Spin,
} from 'antd';
import { RobotOutlined, SendOutlined, UserOutlined } from '@ant-design/icons';
import api from '../utils/api';

const { Title, Paragraph, Text } = Typography;
const { TextArea } = Input;

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

const SUGGESTIONS = [
  '为什么磁盘空间不足？',
  '当前系统 CPU 和内存占用如何？',
  'PT RSS 下载任务一直不动，可能是什么原因？',
  '如何优化 qBittorrent 的下载策略？',
  'Docker 容器频繁重启怎么排查？',
];

export default function AIAssistant() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [includeStats, setIncludeStats] = useState(true);
  const [configError, setConfigError] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const send = async (question?: string) => {
    const q = (question ?? input).trim();
    if (!q) return;
    setInput('');
    const userMsg: Message = { role: 'user', content: q };
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);
    setConfigError(false);
    try {
      const res = await api.post('/ai/ask', { question: q, include_system_stats: includeStats });
      setMessages((prev) => [...prev, { role: 'assistant', content: res.data.answer }]);
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 50);
    } catch (err: any) {
      const detail = err?.response?.data?.detail || '请求失败';
      if (detail.includes('OPENAI_API_KEY')) setConfigError(true);
      setMessages((prev) => [...prev, { role: 'assistant', content: `❌ ${detail}` }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <Card style={{ marginBottom: 16 }}>
        <Space>
          <RobotOutlined style={{ fontSize: 28, color: '#667eea' }} />
          <div>
            <Title level={3} style={{ margin: 0 }}>AI 运维助手</Title>
            <Text type="secondary">基于大模型，结合实时系统状态，帮你诊断和优化 NAS 运维问题</Text>
          </div>
        </Space>
      </Card>

      {configError && (
        <Alert
          style={{ marginBottom: 16 }}
          type="warning"
          showIcon
          message="AI 功能未配置"
          description="请前往「系统设置」填写 OPENAI_API_KEY 和 OPENAI_BASE_URL（支持 OpenAI 兼容接口，如 DeepSeek、Qwen 等）。"
        />
      )}

      {messages.length === 0 && (
        <Card title="快速提问" style={{ marginBottom: 16 }}>
          <Space wrap>
            {SUGGESTIONS.map((s) => (
              <Button key={s} size="small" onClick={() => send(s)}>{s}</Button>
            ))}
          </Space>
        </Card>
      )}

      <Card
        style={{ marginBottom: 16, minHeight: 300, maxHeight: 520, overflowY: 'auto' }}
        bodyStyle={{ padding: '12px 16px' }}
      >
        {messages.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '60px 0', color: '#888' }}>
            <RobotOutlined style={{ fontSize: 48 }} />
            <Paragraph style={{ marginTop: 16 }}>向 AI 助手提问，获取运维建议</Paragraph>
          </div>
        ) : (
          <List
            dataSource={messages}
            renderItem={(msg) => (
              <List.Item style={{ alignItems: 'flex-start', border: 'none', padding: '6px 0' }}>
                <Space align="start" style={{ width: '100%' }}>
                  <div style={{ flexShrink: 0, marginTop: 2 }}>
                    {msg.role === 'user'
                      ? <UserOutlined style={{ fontSize: 20, color: '#667eea' }} />
                      : <RobotOutlined style={{ fontSize: 20, color: '#34d399' }} />}
                  </div>
                  <div
                    style={{
                      background: msg.role === 'user' ? '#f0f4ff' : '#f6ffed',
                      borderRadius: 8,
                      padding: '8px 14px',
                      flex: 1,
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                    }}
                  >
                    {msg.content}
                  </div>
                </Space>
              </List.Item>
            )}
          />
        )}
        {loading && (
          <div style={{ textAlign: 'center', padding: 16 }}>
            <Spin tip="AI 思考中..." />
          </div>
        )}
        <div ref={bottomRef} />
      </Card>

      <Card>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Checkbox checked={includeStats} onChange={(e) => setIncludeStats(e.target.checked)}>
            附带当前系统状态（CPU / 内存 / 磁盘）
          </Checkbox>
          <Space.Compact style={{ width: '100%' }}>
            <TextArea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="输入你的运维问题，例如：为什么磁盘空间不足？"
              autoSize={{ minRows: 2, maxRows: 5 }}
              onPressEnter={(e) => { if (!e.shiftKey) { e.preventDefault(); send(); } }}
              disabled={loading}
            />
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={() => send()}
              loading={loading}
              style={{ height: 'auto', alignSelf: 'flex-end' }}
            >
              发送
            </Button>
          </Space.Compact>
          <Text type="secondary" style={{ fontSize: 12 }}>按 Enter 发送，Shift+Enter 换行</Text>
        </Space>
      </Card>
    </div>
  );
}
