import { useState, useEffect, useCallback } from 'react';
import { Form, Input, Button, Switch, message, Typography, Card, Spin, Tabs, Modal, Space, Row, Col, Statistic } from 'antd';
import { useNavigate } from 'react-router-dom';
import api from '../../utils/api';
import { SaveOutlined, ReloadOutlined, LockOutlined, SettingOutlined, RobotOutlined, FolderOpenOutlined, CopyOutlined } from '@ant-design/icons';

const { Title, Paragraph } = Typography;

interface Setting {
  key: string;
  value: string;
  value_type: string;
  category: string;
  title?: string;
  description?: string;
}

export default function SystemSettings() {
  const [settings, setSettings] = useState<Setting[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();
  const navigate = useNavigate();

  // Password change
  const [pwdModal, setPwdModal] = useState(false);
  const [pwdForm] = Form.useForm();
  const [pwdChanging, setPwdChanging] = useState(false);

  const fetchSettings = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/system/settings');
      const data = res.data as Setting[];
      setSettings(data);
      const values: Record<string, any> = {};
      data.forEach((s) => {
        if (s.value_type === 'int') values[s.key] = parseInt(s.value, 10);
        else if (s.value_type === 'bool') values[s.key] = s.value === 'true';
        else if (s.value_type === 'json') {
          try { values[s.key] = JSON.parse(s.value); }
          catch { values[s.key] = s.value; }
        } else values[s.key] = s.value;
      });
      form.setFieldsValue(values);
    } catch { message.error('获取配置失败'); }
    finally { setLoading(false); }
  }, [form]);

  useEffect(() => { fetchSettings(); }, [fetchSettings]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const values = form.getFieldsValue();
      const entries = Object.entries(values).map(([key, value]) => {
        const setting = settings.find((s) => s.key === key);
        let strValue = String(value);
        if (setting?.value_type === 'bool') strValue = value ? 'true' : 'false';
        else if (setting?.value_type === 'json') strValue = JSON.stringify(value);
        return { key, value: strValue };
      });
      await api.put('/system/settings', entries);
      message.success('配置已保存');
    } catch (err: any) {
      message.error('保存失败');
    } finally { setSaving(false); }
  };

  const handleChangePassword = async () => {
    try {
      const vals = await pwdForm.validateFields();
      setPwdChanging(true);
      await api.post('/auth/change-password', vals);
      message.success('密码已更改');
      setPwdModal(false);
      pwdForm.resetFields();
    } catch (err: any) {
      if (err?.response) message.error(err.response.data?.detail || '更改失败');
      else if (err?.errorFields) { /* validation error, ignore */ }
      else message.error('更改失败');
    }
    finally { setPwdChanging(false); }
  };

  const generateApiToken = () => {
    const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    const token = Array.from({length: 32}, () => chars[Math.floor(Math.random() * chars.length)]).join('');
    form.setFieldValue('AI_API_KEY', `naspilot-${token}`);
  };

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;

  // Filter settings by category
  const catSettings = (cat: string) => settings.filter(s => s.category === cat);

  const tabItems = [
    {
      key: 'general',
      label: <span><SettingOutlined /> 常规</span>,
      children: (
        <Row gutter={[16, 0]}>
          {catSettings('general').map(s => {
            if (s.value_type === 'bool') {
              return <Col xs={24} sm={12} key={s.key}><Form.Item name={s.key} label={s.key} valuePropName="checked" help={s.description}><Switch /></Form.Item></Col>;
            }
            if (s.key.includes('secret') || s.key.includes('password') || s.key.includes('apikey')) {
              return <Col xs={24} sm={12} key={s.key}><Form.Item name={s.key} label={s.key} help={s.description}><Input.Password /></Form.Item></Col>;
            }
            if (s.value_type === 'int') {
              return <Col xs={24} sm={12} key={s.key}><Form.Item name={s.key} label={s.key} help={s.description}><Input type="number" /></Form.Item></Col>;
            }
            return <Col xs={24} sm={12} key={s.key}><Form.Item name={s.key} label={s.key} help={s.description}><Input /></Form.Item></Col>;
          })}
        </Row>
      ),
    },
    {
      key: 'ai',
      label: <span><RobotOutlined /> AI</span>,
      children: (
        <>
          <Paragraph type="secondary" style={{ marginBottom: 16 }}>
            配置 AI 助手的 API 密钥。支持任何兼容 OpenAI 接口的服务。
          </Paragraph>
          <Row gutter={[16, 0]}>
            <Col xs={24} sm={12}>
              <Form.Item name="AI_API_KEY" label="AI API Key" help="OpenAI / 兼容 API 的密钥">
                <Input.Password />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12}>
              <Form.Item name="AI_BASE_URL" label="API Base URL" help="默认 https://api.openai.com/v1">
                <Input placeholder="https://api.openai.com/v1" />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12}>
              <Form.Item name="AI_MODEL" label="Model" help="默认 gpt-4o">
                <Input placeholder="gpt-4o" />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12} style={{ display: 'flex', alignItems: 'center', paddingTop: 28 }}>
              <Button icon={<CopyOutlined />} onClick={generateApiToken}>生成 API Token</Button>
            </Col>
          </Row>
        </>
      ),
    },
    {
      key: 'security',
      label: <span><LockOutlined /> 安全</span>,
      children: (
        <Card title="修改密码" style={{ maxWidth: 500 }}>
          <Paragraph type="secondary">修改管理员登录密码。建议使用 8 位以上的强密码。</Paragraph>
          <Button type="primary" icon={<LockOutlined />} onClick={() => setPwdModal(true)}>修改密码</Button>
        </Card>
      ),
    },
    {
      key: 'tools',
      label: <span><FolderOpenOutlined /> 工具</span>,
      children: (
        <Row gutter={[16, 16]}>
          <Col xs={24} sm={12} lg={8}>
            <Card hoverable onClick={() => navigate('/files')}>
              <Statistic title="文件浏览" value="📂" prefix={<FolderOpenOutlined />} />
              <Paragraph type="secondary" style={{ marginTop: 8 }}>浏览 NAS 上的文件和日志</Paragraph>
            </Card>
          </Col>
        </Row>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>⚙️ 系统设置</Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={fetchSettings}>刷新</Button>
          <Button type="primary" icon={<SaveOutlined />} onClick={handleSave} loading={saving}>保存</Button>
        </Space>
      </div>

      <Form form={form} layout="vertical">
        <Tabs items={tabItems} />
      </Form>

      <Modal title="修改密码" open={pwdModal} onOk={handleChangePassword} onCancel={() => { setPwdModal(false); pwdForm.resetFields(); }}
        confirmLoading={pwdChanging} okText="确认修改" cancelText="取消">
        <Form form={pwdForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="old_password" label="当前密码" rules={[{ required: true, message: '请输入当前密码' }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="new_password" label="新密码" rules={[{ required: true, min: 6, message: '至少 6 位' }]}>
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
