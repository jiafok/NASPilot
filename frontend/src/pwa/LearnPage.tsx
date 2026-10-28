import { useCallback, useEffect, useState } from 'react';
import { apiGet, errText, fmtTime, withToken, type HomeData } from './api';
import { EmptyState, ErrorState, IconRoute, PageHeader, SectionHead, SkeletonList } from './components';
import { ArrowRightOutlined } from '@ant-design/icons';

interface Props {
  onOpenLesson: (lessonId: number) => void;
  onOpenCourse: (courseId: number) => void;
  onGoDiscover: () => void;
}

/**
 * Learning path.
 *
 * The backend has no "path" concept, so this is built on the client from what
 * it does have: lessons in progress (continue) plus the level distribution.
 * It is presented as three honest stages rather than pretending to be a
 * curated curriculum.
 */
export default function LearnPage({ onOpenLesson, onOpenCourse, onGoDiscover }: Props) {
  const [home, setHome] = useState<HomeData | null>(null);
  const [levels, setLevels] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    Promise.all([apiGet.home(), apiGet.stats(90).catch(() => null)])
      .then(([homeData, stats]) => {
        setHome(homeData);
        setLevels(stats?.levels || {});
      })
      .catch((err) => setError(errText(err, '网络不可用')))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  if (loading) {
    return (
      <div className="ef-page">
        <SkeletonList rows={1} />
        <div style={{ height: 24 }} />
        <SkeletonList rows={4} />
      </div>
    );
  }

  if (error || !home) {
    return (
      <div className="ef-page">
        <ErrorState message={error || '没有数据'} onRetry={load} />
      </div>
    );
  }

  const inProgress = home.continue_learning;
  const stages = [
    { level: 'A2', title: '打基础', hint: '短句、慢速、日常场景' },
    { level: 'B1', title: '能跟上', hint: '真实对话、常见口音' },
    { level: 'B2', title: '听得懂', hint: '长段落、观点表达' },
  ];

  return (
    <div className="ef-page ef-learn-page">
      <PageHeader title="学习空间" eyebrow="A LITTLE, EVERY DAY" symbol={<IconRoute />} detail={`今日 ${home.stats.minutes_today} 分钟 · 已完成 ${home.stats.completed} 课时`} />
      <section className="ef-section">
        <SectionHead title="我的学习" />
        {inProgress.length === 0 ? (
          <EmptyState
            title="还没有开始"
            description="挑一节课开始，进度会自动保存。"
            action="去发现"
            onAction={onGoDiscover}
          />
        ) : (
          <div className="ef-list">
            {inProgress.map((item) => (
              <button
                key={item.lesson_id}
                type="button"
                className="ef-row"
                onClick={() => onOpenLesson(item.lesson_id)}
              >
                <span className="ef-cover" style={{ width: 64, height: 44, fontSize: 14 }}>
                  {item.cover ? <img src={withToken(item.cover)} alt="" /> : null}
                </span>
                <span className="ef-row-body">
                  <span className="ef-title-2" style={{ margin: 0 }}>
                    {item.lesson_title}
                  </span>
                  <span className="ef-row-meta">
                    {item.course_title} · {fmtTime(item.position)} / {fmtTime(item.duration)}
                  </span>
                  <span className="ef-bar">
                    <i style={{ width: `${Math.round((item.progress || 0) * 100)}%` }} />
                  </span>
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="ef-section">
        <SectionHead title="学习阶段" />
        <div className="ef-stage-path">
          {stages.map((stage) => {
            const done = levels[stage.level] || 0;
            return (
              <button key={stage.level} className="ef-path-step" type="button" onClick={onGoDiscover}>
                <span className="ef-path-level">{stage.level}</span>
                <span className="ef-path-body"><strong>{stage.title}</strong><span>{stage.hint}</span><small>已完成 {done} 课</small></span>
                <ArrowRightOutlined />
              </button>
            );
          })}
        </div>
      </section>

      <section className="ef-section">
        <SectionHead title="课程体系" action="全部" onAction={onGoDiscover} />
        <div className="ef-list">
          {home.recommended.slice(0, 6).map((course) => (
            <button
              key={course.id}
              type="button"
              className="ef-row"
              onClick={() => onOpenCourse(course.id)}
            >
              <span className="ef-cover" style={{ width: 64, height: 44, fontSize: 14 }}>
                {course.cover ? <img src={withToken(course.cover)} alt="" /> : null}
              </span>
              <span className="ef-row-body">
                <span className="ef-title-2" style={{ margin: 0 }}>
                  {course.title}
                </span>
                <span className="ef-row-meta">
                  <span className="ef-tag">{course.level}</span>
                  <span>{course.lesson_count} 课</span>
                  <span>· {fmtTime(course.total_seconds)}</span>
                </span>
              </span>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
