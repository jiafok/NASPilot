import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Empty,
  Input,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  ArrowLeftOutlined,
  BookOutlined,
  CustomerServiceOutlined,
  FileTextOutlined,
  FundOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RobotOutlined,
  SoundOutlined,
  StarFilled,
  StarOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import api from '../../utils/api';

const { Title, Text, Paragraph } = Typography;

// ── types ────────────────────────────────────────────────────────────────

interface Course {
  id: number;
  title: string;
  channel: string;
  category: string;
  difficulty: string;
  description?: string | null;
  tags: string[];
  lesson_count: number;
  total_seconds: number;
  cover?: string | null;
  is_favorite: boolean;
}

interface LessonBrief {
  id: number;
  title: string;
  position: number;
  duration: number;
  media_type: string;
  media_ext: string;
  has_subtitle: boolean;
  progress: number;
  completed: boolean;
}

interface CourseDetail extends Course {
  lessons: LessonBrief[];
  vocabulary_count: number;
}

interface Segment {
  i: number;
  start: number;
  end: number;
  text: string;
  /** Speaker turn label ("A"/"B") when the source marks dialogue. */
  spk?: string | null;
}

interface VocabItem {
  id: number;
  word: string;
  kind: string;
  frequency: number;
  difficulty: string;
  meaning?: string | null;
}

interface Bookmark {
  id: number;
  position: number;
  at_seconds: number;
  note?: string | null;
  text?: string | null;
}

interface LessonDetail {
  id: number;
  course_id: number;
  title: string;
  media_type: string;
  duration: number;
  stream_url: string;
  subtitle_url?: string | null;
  has_subtitle: boolean;
  vocabulary: VocabItem[];
  bookmarks: Bookmark[];
  progress: { position: number; completed: boolean; listen_seconds: number };
  prev_lesson_id?: number | null;
  next_lesson_id?: number | null;
}

interface HomeStats {
  courses: number;
  lessons: number;
  completed: number;
  minutes_today: number;
  total_time: string;
}

interface HomeData {
  greeting: string;
  stats: HomeStats;
  continue_learning: Array<{
    lesson_id: number;
    lesson_title: string;
    course_id: number;
    course_title: string;
    progress: number;
    position: number;
    duration: number;
  }>;
  recent: Course[];
  recommended: Course[];
}

interface ScanReport {
  root: string;
  exists: boolean;
  courses_created: number;
  courses_updated: number;
  lessons_created: number;
  lessons_updated: number;
  lessons_missing: number;
  transcripts_parsed: number;
  vocabulary_terms: number;
  errors: string[];
}

interface LibStats {
  root: string;
  exists: boolean;
  courses: number;
  lessons: number;
  lessons_missing: number;
  transcripts: number;
  vocabulary: number;
  total_seconds: number;
}

const DIFFICULTY_COLOR: Record<string, string> = {
  beginner: 'green',
  intermediate: 'blue',
  advanced: 'purple',
  mixed: 'default',
};

const DIFFICULTY_LABEL: Record<string, string> = {
  beginner: '入门',
  intermediate: '中级',
  advanced: '进阶',
  mixed: '混合',
};

function fmtDuration(seconds: number | null | undefined): string {
  const total = Math.max(0, Math.round(seconds || 0));
  if (!total) return '—';
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

//: <video>/<audio>/<img> cannot send an Authorization header, so the JWT is
//: passed as a query param for stream URLs (matches the backend fallback).
function withToken(path: string | null | undefined): string | undefined {
  if (!path) return undefined;
  const token = localStorage.getItem('token') || '';
  if (!token) return path;
  return `${path}${path.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;
}

function errText(err: unknown, fallback: string): string {
  const anyErr = err as { response?: { data?: { detail?: unknown } } };
  const detail = anyErr?.response?.data?.detail;
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail) && detail.length) return String((detail[0] as { msg?: string }).msg || fallback);
  return fallback;
}

// ── component ────────────────────────────────────────────────────────────

export default function EnglishCenter() {
  const [home, setHome] = useState<HomeData | null>(null);
  const [stats, setStats] = useState<LibStats | null>(null);
  const [courses, setCourses] = useState<Course[]>([]);
  const [filters, setFilters] = useState<{ channels: string[]; categories: string[]; difficulties: string[] }>({
    channels: [],
    categories: [],
    difficulties: [],
  });
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [q, setQ] = useState('');
  const [channel, setChannel] = useState<string | undefined>();
  const [category, setCategory] = useState<string | undefined>();
  const [difficulty, setDifficulty] = useState<string | undefined>();
  const [onlyFavorite, setOnlyFavorite] = useState(false);

  const [course, setCourse] = useState<CourseDetail | null>(null);
  const [lesson, setLesson] = useState<LessonDetail | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [activeSegment, setActiveSegment] = useState<number>(-1);
  const [aiConfigured, setAiConfigured] = useState(false);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiOutput, setAiOutput] = useState<string>('');
  const [aiTitle, setAiTitle] = useState<string>('');
  const [question, setQuestion] = useState('');

  const mediaRef = useRef<HTMLMediaElement | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const lastReportRef = useRef<number>(0);

  // ── data loading ──────────────────────────────────────────────────────

  const loadOverview = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, unknown> = { page_size: 60 };
      if (q.trim()) params.q = q.trim();
      if (channel) params.channel = channel;
      if (category) params.category = category;
      if (difficulty) params.difficulty = difficulty;
      if (onlyFavorite) params.favorite = true;

      const [homeRes, dirRes, listRes, filterRes, aiRes] = await Promise.all([
        api.get<HomeData>('/english/home'),
        api.get<{ root: string; stats: LibStats }>('/english/directories'),
        api.get<{ items: Course[] }>('/english/courses', { params }),
        api.get<{ channels: string[]; categories: string[]; difficulties: string[] }>('/english/filters'),
        api.get<{ configured: boolean }>('/english/ai/status').catch(() => ({ data: { configured: false } })),
      ]);
      setHome(homeRes.data);
      setStats(dirRes.data.stats);
      setCourses(listRes.data.items);
      setFilters(filterRes.data);
      setAiConfigured(Boolean(aiRes.data.configured));
    } catch (err) {
      message.error(errText(err, '加载英语学习中心失败'));
    } finally {
      setLoading(false);
    }
  }, [q, channel, category, difficulty, onlyFavorite]);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  const runScan = async () => {
    setScanning(true);
    try {
      const res = await api.post<ScanReport>('/english/admin/scan', null, { timeout: 300000 });
      const r = res.data;
      if (!r.exists) {
        message.warning(`目录不存在：${r.root}`);
      } else {
        message.success(
          `扫描完成：新增 ${r.lessons_created} 课时 / 更新 ${r.lessons_updated} / 缺失 ${r.lessons_missing}，` +
            `字幕 ${r.transcripts_parsed}，生词 ${r.vocabulary_terms}`,
        );
      }
      if (r.errors?.length) message.warning(`有 ${r.errors.length} 个文件处理失败`);
      await loadOverview();
    } catch (err) {
      message.error(errText(err, '扫描失败'));
    } finally {
      setScanning(false);
    }
  };

  const openCourse = async (courseId: number) => {
    try {
      const res = await api.get<CourseDetail>(`/english/course/${courseId}`);
      setCourse(res.data);
      setLesson(null);
      setSegments([]);
      setAiOutput('');
    } catch (err) {
      message.error(errText(err, '加载课程失败'));
    }
  };

  const openLesson = useCallback(async (lessonId: number) => {
    try {
      const res = await api.get<LessonDetail>(`/english/lesson/${lessonId}`);
      setLesson(res.data);
      setActiveSegment(-1);
      setAiOutput('');
      if (res.data.has_subtitle) {
        const tr = await api.get<{ segments: Segment[] }>(`/english/transcript/${lessonId}`);
        setSegments(tr.data.segments || []);
      } else {
        setSegments([]);
      }
    } catch (err) {
      message.error(errText(err, '加载课时失败'));
    }
  }, []);

  // ── progress reporting ────────────────────────────────────────────────

  const reportProgress = useCallback(
    async (position: number, listenDelta: number) => {
      if (!lesson) return;
      try {
        await api.post('/english/progress', {
          lesson_id: lesson.id,
          position,
          listen_seconds: listenDelta,
        });
      } catch {
        /* progress reporting must never disturb playback */
      }
    },
    [lesson],
  );

  const onTimeUpdate = () => {
    const el = mediaRef.current;
    if (!el || !lesson) return;
    const ms = el.currentTime * 1000;
    const index = segments.findIndex((s) => ms >= s.start && ms < s.end);
    if (index !== -1 && index !== activeSegment) {
      setActiveSegment(index);
      const row = transcriptRef.current?.querySelector<HTMLElement>(`[data-seg="${index}"]`);
      row?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
    const now = Date.now();
    if (now - lastReportRef.current > 10000) {
      lastReportRef.current = now;
      void reportProgress(el.currentTime, 10);
    }
  };

  const seekTo = (segment: Segment) => {
    const el = mediaRef.current;
    if (!el) return;
    el.currentTime = segment.start / 1000;
    void el.play().catch(() => undefined);
    setActiveSegment(segment.i);
  };

  const toggleFavorite = async () => {
    if (!course) return;
    const next = !course.is_favorite;
    try {
      await api.post('/english/favorite', { course_id: course.id, favorite: next });
      setCourse({ ...course, is_favorite: next });
      setCourses((prev) => prev.map((c) => (c.id === course.id ? { ...c, is_favorite: next } : c)));
    } catch (err) {
      message.error(errText(err, '操作失败'));
    }
  };

  const markCompleted = async () => {
    if (!lesson) return;
    await reportProgress(lesson.duration, 0);
    try {
      await api.post('/english/progress', {
        lesson_id: lesson.id,
        position: lesson.duration,
        listen_seconds: 0,
        completed: true,
      });
      setLesson({ ...lesson, progress: { ...lesson.progress, completed: true } });
      message.success('已标记为完成');
      if (course) await openCourse(course.id);
    } catch (err) {
      message.error(errText(err, '操作失败'));
    }
  };

  const addBookmark = async () => {
    if (!lesson || activeSegment < 0) {
      message.info('请先播放到某个句子');
      return;
    }
    const seg = segments[activeSegment];
    try {
      const res = await api.post<Bookmark>('/english/bookmark', {
        lesson_id: lesson.id,
        position: seg.i,
        at_seconds: seg.start / 1000,
        text: seg.text,
        note: '标签',
      });
      setLesson({ ...lesson, bookmarks: [...lesson.bookmarks.filter((b) => b.position !== seg.i), res.data] });
      message.success('已添加书签');
    } catch (err) {
      message.error(errText(err, '添加书签失败'));
    }
  };

  const runAi = async (kind: 'summary' | 'explain' | 'chat') => {
    if (!lesson) return;
    setAiBusy(true);
    setAiOutput('');
    try {
      if (kind === 'summary') {
        setAiTitle('AI 总结');
        const res = await api.post<{ summary: string }>('/english/ai/summary', {
          lesson_id: lesson.id,
          language: 'zh',
        }, { timeout: 120000 });
        setAiOutput(res.data.summary);
      } else if (kind === 'explain') {
        if (activeSegment < 0) {
          message.info('请先播放到某个句子');
          return;
        }
        setAiTitle('AI 逐句讲解');
        const res = await api.post<{ answer: string }>('/english/ai/explain', {
          lesson_id: lesson.id,
          position: segments[activeSegment].i,
        }, { timeout: 120000 });
        setAiOutput(res.data.answer);
      } else {
        if (!question.trim()) {
          message.info('请输入问题');
          return;
        }
        setAiTitle('AI 老师');
        const res = await api.post<{ answer: string }>('/english/ai/chat', {
          lesson_id: lesson.id,
          question: question.trim(),
        }, { timeout: 120000 });
        setAiOutput(res.data.answer);
      }
    } catch (err) {
      message.error(errText(err, 'AI 调用失败'));
    } finally {
      setAiBusy(false);
    }
  };

  // ── render helpers ────────────────────────────────────────────────────

  const coverStyle = useMemo(
    () => ({
      height: 120,
      borderRadius: 12,
      background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      color: '#fff',
      fontSize: 34,
      fontWeight: 700,
      overflow: 'hidden',
    }),
    [],
  );

  const renderCover = (item: Course) =>
    item.cover ? (
      <div style={{ ...coverStyle, backgroundImage: `url(${withToken(item.cover)})`, backgroundSize: 'cover' }} />
    ) : (
      <div style={coverStyle}>{item.title.slice(0, 1).toUpperCase()}</div>
    );

  const renderCourseGrid = (items: Course[]) => (
    <Row gutter={[16, 16]}>
      {items.map((item) => (
        <Col xs={24} sm={12} lg={8} xl={6} key={item.id}>
          <Card hoverable onClick={() => void openCourse(item.id)} styles={{ body: { padding: 16 } }}>
            {renderCover(item)}
            <div style={{ marginTop: 12 }}>
              <Text strong ellipsis style={{ display: 'block' }}>
                {item.title}
              </Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {item.channel} · {item.lesson_count} 课时 · {fmtDuration(item.total_seconds)}
              </Text>
            </div>
            <Space size={4} style={{ marginTop: 8 }} wrap>
              <Tag color={DIFFICULTY_COLOR[item.difficulty] || 'default'}>
                {DIFFICULTY_LABEL[item.difficulty] || item.difficulty}
              </Tag>
              {item.category && <Tag>{item.category}</Tag>}
              {item.is_favorite && <Tag color="gold">收藏</Tag>}
            </Space>
          </Card>
        </Col>
      ))}
    </Row>
  );

  // ── views ─────────────────────────────────────────────────────────────

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '120px auto' }} />;

  // 播放器视图
  if (lesson) {
    return (
      <div>
        <Space style={{ marginBottom: 16 }} wrap>
          <Button icon={<ArrowLeftOutlined />} onClick={() => setLesson(null)}>
            返回课程
          </Button>
          <Button
            icon={<PlayCircleOutlined />}
            disabled={!lesson.prev_lesson_id}
            onClick={() => lesson.prev_lesson_id && void openLesson(lesson.prev_lesson_id)}
          >
            上一课
          </Button>
          <Button
            icon={<PlayCircleOutlined />}
            disabled={!lesson.next_lesson_id}
            onClick={() => lesson.next_lesson_id && void openLesson(lesson.next_lesson_id)}
          >
            下一课
          </Button>
          {lesson.progress.completed ? (
            <Tag color="green">已完成</Tag>
          ) : (
            <Button onClick={() => void markCompleted()}>标记完成</Button>
          )}
          <Button icon={<StarOutlined />} onClick={() => void addBookmark()}>
            收藏当前句
          </Button>
        </Space>

        <Row gutter={[16, 16]}>
          <Col xs={24} lg={14}>
            <Card styles={{ body: { padding: 0 } }}>
              {lesson.media_type === 'video' ? (
                <video
                  ref={mediaRef as unknown as React.RefObject<HTMLVideoElement>}
                  src={withToken(lesson.stream_url)}
                  controls
                  onTimeUpdate={onTimeUpdate}
                  style={{ width: '100%', borderRadius: 8, background: '#000', maxHeight: 460 }}
                />
              ) : (
                <div style={{ padding: 32, textAlign: 'center' }}>
                  <CustomerServiceOutlined style={{ fontSize: 72, color: '#667eea' }} />
                  <audio
                    ref={mediaRef as unknown as React.RefObject<HTMLAudioElement>}
                    src={withToken(lesson.stream_url)}
                    controls
                    onTimeUpdate={onTimeUpdate}
                    style={{ width: '100%', marginTop: 24 }}
                  />
                </div>
              )}
            </Card>

            <Card style={{ marginTop: 16 }} size="small" title={<Space><RobotOutlined />AI 老师</Space>}>
              {!aiConfigured && (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginBottom: 12 }}
                  message="AI 未配置"
                  description="请先在「系统设置 → AI」填写 API Key 后再使用总结 / 讲解 / 问答。"
                />
              )}
              <Space wrap style={{ marginBottom: 12 }}>
                <Button
                  icon={<FundOutlined />}
                  loading={aiBusy && aiTitle === 'AI 总结'}
                  disabled={!aiConfigured || !lesson.has_subtitle}
                  onClick={() => void runAi('summary')}
                >
                  AI 总结
                </Button>
                <Button
                  icon={<FileTextOutlined />}
                  loading={aiBusy && aiTitle === 'AI 逐句讲解'}
                  disabled={!aiConfigured || !lesson.has_subtitle}
                  onClick={() => void runAi('explain')}
                >
                  讲解当前句
                </Button>
              </Space>
              <Space.Compact style={{ width: '100%' }}>
                <Input
                  placeholder="问 AI 老师，例如：这句话为什么用现在完成时？"
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  onPressEnter={() => void runAi('chat')}
                  disabled={!aiConfigured}
                />
                <Button type="primary" loading={aiBusy && aiTitle === 'AI 老师'} disabled={!aiConfigured} onClick={() => void runAi('chat')}>
                  提问
                </Button>
              </Space.Compact>
              {aiOutput && (
                <Card size="small" title={aiTitle} style={{ marginTop: 12 }}>
                  <Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>{aiOutput}</Paragraph>
                </Card>
              )}
            </Card>
          </Col>

          <Col xs={24} lg={10}>
            <Card
              size="small"
              title={
                <Space>
                  <FileTextOutlined />
                  字幕（{segments.length} 句）
                </Space>
              }
              extra={<Text type="secondary">点击句子跳转</Text>}
              styles={{ body: { maxHeight: 520, overflowY: 'auto', padding: 8 } }}
            >
              <div ref={transcriptRef}>
                {segments.length === 0 ? (
                  <Empty description="该课时没有字幕" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                ) : (
                  segments.map((seg, index) => {
                    const prev = index > 0 ? segments[index - 1] : null;
                    const turnStart = !prev || prev.spk !== seg.spk;
                    const showSpeaker = Boolean(seg.spk) && turnStart;
                    return (
                      <div
                        key={seg.i}
                        data-seg={index}
                        onClick={() => seekTo(seg)}
                        style={{
                          padding: '8px 10px',
                          marginTop: showSpeaker && index > 0 ? 10 : 0,
                          marginBottom: 4,
                          borderRadius: 8,
                          cursor: 'pointer',
                          background:
                            index === activeSegment ? 'rgba(102,126,234,0.16)' : 'transparent',
                          borderLeft:
                            index === activeSegment
                              ? '3px solid #667eea'
                              : '3px solid transparent',
                        }}
                      >
                        <Space size={6}>
                          {showSpeaker && (
                            <Tag color={seg.spk === 'A' ? 'geekblue' : 'magenta'}>
                              {seg.spk === 'A' ? '说话人 A' : '说话人 B'}
                            </Tag>
                          )}
                          <Text type="secondary" style={{ fontSize: 11 }}>
                            {fmtDuration(seg.start / 1000)}
                          </Text>
                        </Space>
                        <div style={{ marginTop: showSpeaker && index > 0 ? 4 : 0 }}>
                          <Text strong={index === activeSegment}>{seg.text}</Text>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </Card>

            <Card size="small" style={{ marginTop: 12 }} title={<Space><SoundOutlined />生词本</Space>}>
              <Space wrap size={[6, 6]}>
                {lesson.vocabulary.length === 0 ? (
                  <Text type="secondary">暂无生词（需要字幕）</Text>
                ) : (
                  lesson.vocabulary.map((v) => (
                    <Tag key={v.id} color={v.kind === 'phrase' ? 'purple' : 'blue'}>
                      {v.word} ×{v.frequency}
                    </Tag>
                  ))
                )}
              </Space>
            </Card>
          </Col>
        </Row>
      </div>
    );
  }

  // 课程详情视图
  if (course) {
    return (
      <div>
        <Space style={{ marginBottom: 16 }} wrap>
          <Button icon={<ArrowLeftOutlined />} onClick={() => setCourse(null)}>
            返回课程库
          </Button>
          <Button
            icon={course.is_favorite ? <StarFilled style={{ color: '#faad14' }} /> : <StarOutlined />}
            onClick={() => void toggleFavorite()}
          >
            {course.is_favorite ? '已收藏' : '收藏'}
          </Button>
        </Space>

        <Card style={{ marginBottom: 16 }}>
          <Row gutter={[16, 16]} align="middle">
            <Col xs={24} sm={6}>{renderCover(course)}</Col>
            <Col xs={24} sm={18}>
              <Title level={4} style={{ marginTop: 0 }}>{course.title}</Title>
              <Space wrap size={[8, 8]}>
                <Tag>{course.channel}</Tag>
                <Tag color={DIFFICULTY_COLOR[course.difficulty] || 'default'}>
                  {DIFFICULTY_LABEL[course.difficulty] || course.difficulty}
                </Tag>
                <Tag>{course.category}</Tag>
                {course.tags.map((t) => (
                  <Tag key={t} color="cyan">{t}</Tag>
                ))}
              </Space>
              <Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
                {course.description || `${course.lesson_count} 课时 · 共 ${fmtDuration(course.total_seconds)} · 生词 ${course.vocabulary_count} 个`}
              </Paragraph>
            </Col>
          </Row>
        </Card>

        <Card title={<Space><BookOutlined />课时列表</Space>} size="small">
          {course.lessons.length === 0 ? (
            <Empty description="该课程还没有课时" />
          ) : (
            course.lessons.map((ls) => (
              <div
                key={ls.id}
                onClick={() => void openLesson(ls.id)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  padding: '10px 8px',
                  borderBottom: '1px solid rgba(0,0,0,0.06)',
                  cursor: 'pointer',
                }}
              >
                <Text type="secondary" style={{ width: 32 }}>{ls.position}</Text>
                {ls.media_type === 'video' ? <VideoCameraOutlined /> : <CustomerServiceOutlined />}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Text strong ellipsis style={{ display: 'block' }}>{ls.title}</Text>
                  <Space size={6}>
                    <Text type="secondary" style={{ fontSize: 12 }}>{fmtDuration(ls.duration)}</Text>
                    {ls.has_subtitle && <Tag color="blue">字幕</Tag>}
                    {ls.completed && <Tag color="green">已完成</Tag>}
                  </Space>
                </div>
                <div style={{ width: 110 }}>
                  <Progress percent={Math.round((ls.progress || 0) * 100)} size="small" />
                </div>
              </div>
            ))
          )}
        </Card>
      </div>
    );
  }

  // 课程库视图（默认）
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 8 }}>
        <div>
          <Title level={4} style={{ margin: 0 }}>📚 英语学习中心</Title>
          <Text type="secondary">
            资源目录：{stats?.root || '—'}
            {stats && !stats.exists && ' （目录不存在，请把音视频放入该目录）'}
          </Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void loadOverview()}>刷新</Button>
          <Button type="primary" icon={<ReloadOutlined />} loading={scanning} onClick={() => void runScan()}>
            扫描媒体库
          </Button>
        </Space>
      </div>

      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={6}><Card size="small"><Statistic title="课程" value={stats?.courses ?? 0} /></Card></Col>
        <Col xs={12} sm={6}><Card size="small"><Statistic title="课时" value={stats?.lessons ?? 0} /></Card></Col>
        <Col xs={12} sm={6}><Card size="small"><Statistic title="已完成" value={home?.stats.completed ?? 0} /></Card></Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic title="今日学习" value={home?.stats.minutes_today ?? 0} suffix="分钟" />
          </Card>
        </Col>
      </Row>

      {stats && !stats.exists && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message={`资源目录 ${stats.root} 不存在`}
          description="把英语音视频放进该目录后点「扫描媒体库」。层级：<频道>/<课程>/<课时>.mp4|.mp3 + 同名 .srt/.vtt。"
        />
      )}

      {home && home.continue_learning.length > 0 && (
        <Card size="small" title="继续学习" style={{ marginBottom: 16 }}>
          <Row gutter={[12, 12]}>
            {home.continue_learning.map((item) => (
              <Col xs={24} sm={12} lg={8} key={item.lesson_id}>
                <Card size="small" hoverable onClick={() => void openLesson(item.lesson_id)}>
                  <Text strong ellipsis style={{ display: 'block' }}>{item.lesson_title}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>{item.course_title}</Text>
                  <Progress percent={Math.round(item.progress * 100)} size="small" style={{ marginTop: 8 }} />
                </Card>
              </Col>
            ))}
          </Row>
        </Card>
      )}

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input.Search
            placeholder="搜索课程 / 频道"
            allowClear
            style={{ width: 240 }}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onSearch={() => void loadOverview()}
          />
          <Select
            allowClear
            placeholder="频道"
            style={{ width: 160 }}
            value={channel}
            onChange={setChannel}
            options={filters.channels.map((c) => ({ value: c, label: c }))}
          />
          <Select
            allowClear
            placeholder="分类"
            style={{ width: 140 }}
            value={category}
            onChange={setCategory}
            options={filters.categories.map((c) => ({ value: c, label: c }))}
          />
          <Select
            allowClear
            placeholder="难度"
            style={{ width: 130 }}
            value={difficulty}
            onChange={setDifficulty}
            options={filters.difficulties.map((d) => ({ value: d, label: DIFFICULTY_LABEL[d] || d }))}
          />
          <Tooltip title="只看收藏">
            <Button
              icon={onlyFavorite ? <StarFilled style={{ color: '#faad14' }} /> : <StarOutlined />}
              type={onlyFavorite ? 'primary' : 'default'}
              onClick={() => setOnlyFavorite((v) => !v)}
            >
              收藏
            </Button>
          </Tooltip>
        </Space>
      </Card>

      {courses.length === 0 ? (
        <Card>
          <Empty
            description={
              <div>
                <Paragraph>还没有课程。</Paragraph>
                <Text type="secondary">
                  媒体中心下载的音视频会自动出现在这里；也可以手动放进{' '}
                  <Text code>{stats?.root || '/Media'}</Text>，
                  层级为 <Text code>&lt;频道&gt;/&lt;课程&gt;/&lt;课时&gt;</Text>，然后点「扫描媒体库」。
                </Text>
              </div>
            }
          />
        </Card>
      ) : (
        <Badge.Ribbon text={`${courses.length} 门课程`} color="#667eea">
          <div>{renderCourseGrid(courses)}</div>
        </Badge.Ribbon>
      )}
    </div>
  );
}
