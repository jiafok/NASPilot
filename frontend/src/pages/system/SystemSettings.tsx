import { useState, useEffect, useCallback } from 'react';
import { Form, Input, Button, Switch, message, Typography, Card, Spin, Space } from 'antd';
import api from '../../utils/api';
import { SaveOutlined, ReloadOutlined } from '@ant-design/icons';

const { Title } = Typography;

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

  const renderField = (setting: Setting) => {
    const commonProps = { key: setting.key, name: setting.key, label: setting.key, help: setting.description };

    if (setting.value_type === 'bool') {
      return (
        <Form.Item {...commonProps} valuePropName="checked">
          <Switch />
        </Form.Item>
      );
    }

    if (setting.key.includes('secret') || setting.key.includes('password') || setting.key.includes('apikey')) {
      return (
        <Form.Item {...commonProps}>
          <Input.Password />
        </Form.Item>
      );
    }

    if (setting.value_type === 'int') {
      return (
        <Form.Item {...commonProps}>
          <Input type="number" />
        </Form.Item>
      );
    }

    return (
      <Form.Item {...commonProps}>
        <Input />
      </Form.Item>
    );
  };

  // 按类别分组
  const categories = [...new Set(settings.map((s) => s.category))];

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>系统设置</Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={fetchSettings}>刷新</Button>
          <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>保存</Button>
        </Space>
      </div>

      <Form form={form} layout="vertical">
        {categories.map((cat) => (
          <Card key={cat} title={cat || '未分类'} size="small" style={{ marginBottom: 16 }}>
            {settings.filter((s) => s.category === cat).map((s) => renderField(s))}
          </Card>
        ))}
      </Form>
    </div>
  );
}
