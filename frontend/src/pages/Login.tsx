import { useState } from 'react';
import { Form, Input, Button, Card, message, Typography } from 'antd';
import { UserOutlined, LockOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../hooks/useAuth';
import { useNavigate } from 'react-router-dom';

const { Title, Text } = Typography;

export default function LoginPage() {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      await login(values.username, values.password);
      message.success(t('auth.loginSuccess'));
      navigate('/', { replace: true });
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      if (typeof detail === 'string') message.error(detail);
      else if (Array.isArray(detail)) message.error(detail.map((d: any) => d.msg).join('; '));
      else message.error(t('auth.loginFailed'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', justifyContent: 'center', alignItems: 'center', background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', padding: 16 }}>
      <Card style={{ width: '100%', maxWidth: 400, borderRadius: 12, boxShadow: '0 8px 32px rgba(0,0,0,0.18)' }}>
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <Title level={3} style={{ marginBottom: 4 }}>🚀 {t('app.title')}</Title>
          <Text type="secondary">{t('app.subtitle')}</Text>
        </div>
        <Form name="login" onFinish={onFinish} size="large" initialValues={{ username: 'admin' }}>
          <Form.Item name="username" rules={[{ required: true, message: t('common.pleaseInput') }]}>
            <Input prefix={<UserOutlined />} placeholder={t('auth.username')} />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: t('common.pleaseInput') }]}>
            <Input.Password prefix={<LockOutlined />} placeholder={t('auth.password')} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={loading} block>{t('auth.login')}</Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
